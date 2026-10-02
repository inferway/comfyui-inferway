"""Bounded media upload and download."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import re
import stat
import urllib.parse
import uuid
from pathlib import Path
from typing import BinaryIO

import httpx

from .contracts import SHA256_HEX_RE, ContractError, DownloadSpec

# app/interaction_upload_tickets.py: opaque base64url body + signature.
UPLOAD_PATH_RE = re.compile(
    r"^/v1/interactions/uploads/iup_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"
)

# Windows has no POSIX uid or permission bits: st_uid is always 0 and a
# directory reports 0o777. Private-cache posture there is the user-profile
# ACL that ComfyUI's temp directory inherits (spec §7.1).
_IS_WINDOWS = os.name == "nt"

_logger = logging.getLogger(__name__)

# Query keys that mark a URL as signed: S3/R2 presigned (X-Amz-*), generic
# Signature/Credential/token. Any one of them redacts the whole query.
_SIGNED_QUERY_KEY_RE = re.compile(
    r"(?i)(?:^|&)(?:x-amz-[a-z-]+|signature|sig|credential|token|access_token)="
)


def _redact_signed_url(value: object) -> object:
    """``value`` with a signed query replaced by ``?<redacted>``; else as is."""
    if not isinstance(value, (httpx.URL, str)):
        return value
    text = str(value)
    base, sep, query = text.partition("?")
    if not sep or not _SIGNED_QUERY_KEY_RE.search(query):
        return value
    return f"{base}?<redacted>"


class _SignedUrlLogFilter(logging.Filter):
    """Keep signed download URLs out of ComfyUI's log.

    httpx logs every request line at INFO with the full URL. A presigned
    download URL in comfyui.log hands the finished video to anyone who reads
    the log until the link expires. Only a query that looks signed is cut, so
    other httpx users in the same process keep their log lines.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and args:
            record.args = tuple(_redact_signed_url(arg) for arg in args)
        return True


def _install_signed_url_log_filter() -> None:
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, _SignedUrlLogFilter) for f in httpx_logger.filters):
        httpx_logger.addFilter(_SignedUrlLogFilter())


_install_signed_url_log_filter()


def _part_open_flags() -> int:
    # O_BINARY: the Windows CRT opens fds in text mode by default and would
    # rewrite every 0x0A in the video as 0x0D 0x0A. CPython's tempfile adds
    # it for the same reason. O_NOFOLLOW does not exist on Windows.
    return (
        os.O_CREAT
        | os.O_EXCL
        | os.O_WRONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
    )


def _validate_origin(origin: str, development_mode: bool) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(origin)
        _ = parsed.port
    except ValueError:
        raise ContractError("invalid_origin") from None

    if (
        origin == "https://api.inferway.ai"
        or development_mode
        and parsed.scheme == "http"
        and parsed.hostname in ("127.0.0.1", "localhost", "::1")
    ):
        pass
    else:
        raise ContractError("invalid_origin")

    if (
        parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ContractError("invalid_origin")

    if any(ord(c) < 32 or ord(c) == 127 for c in origin):
        raise ContractError("invalid_origin")

    return parsed


async def upload_image(
    api_http: httpx.AsyncClient,
    *,
    api_origin: str,
    upload_path: str,
    content: bytes,
    content_type: str,
    declared_content_length: int,
    development_mode: bool = False,
) -> dict:
    _validate_origin(api_origin, development_mode)

    base_url_str = str(api_http.base_url).rstrip("/")
    if base_url_str != api_origin.rstrip("/"):
        raise ContractError("base_url_mismatch")

    auth_header = api_http.headers.get("Authorization") or api_http.headers.get(
        "authorization"
    )
    if (
        not auth_header
        or not auth_header.startswith("Bearer ")
        or len(auth_header.strip()) <= 7
    ):
        raise ContractError("auth_error")

    if (
        not isinstance(upload_path, str)
        or not UPLOAD_PATH_RE.fullmatch(upload_path)
        or len(upload_path.rsplit("/", 1)[-1]) > 1024
    ):
        raise ContractError("invalid_upload_path")

    if (
        type(declared_content_length) is not int
        or isinstance(declared_content_length, bool)
        or declared_content_length <= 0
    ):
        raise ContractError("invalid_content_length")

    if len(content) != declared_content_length:
        raise ContractError("invalid_content_length")

    if len(content) > 10 * 1024 * 1024:
        raise ContractError("invalid_content_length")

    if content_type not in ("image/png", "image/jpeg", "image/webp"):
        raise ContractError("invalid_content_type")

    url = urllib.parse.urljoin(api_origin, upload_path)

    try:
        response = await api_http.put(
            url,
            content=content,
            headers={
                "Content-Length": str(declared_content_length),
                "Content-Type": content_type,
            },
            follow_redirects=False,
        )
    except httpx.RequestError:
        raise ContractError("upload_network_error") from None

    if response.status_code == 401 or response.status_code == 403:
        raise ContractError("auth_error")

    if response.status_code != 200:
        raise ContractError("upload_failed")

    try:
        receipt = response.json()
    except ValueError:
        raise ContractError("upload_failed") from None

    if not isinstance(receipt, dict):
        raise ContractError("upload_failed")

    if receipt.get("upload_id") != upload_path.split("/")[-1]:
        raise ContractError("upload_failed")

    if (
        type(receipt.get("byte_count")) is not int
        or receipt["byte_count"] != declared_content_length
    ):
        raise ContractError("upload_failed")

    if receipt.get("content_type") != content_type:
        raise ContractError("upload_failed")

    expected_sha = hashlib.sha256(content).hexdigest().lower()
    if receipt.get("content_sha256") != expected_sha:
        raise ContractError("upload_failed")

    return receipt


async def download_result(
    download_http: httpx.AsyncClient,
    *,
    spec: DownloadSpec,
    allowed_origins: set[str],
    destination: Path,
    max_bytes: int = 1073741824,
    development_mode: bool = False,
    total_timeout_seconds: float = 60.0,
) -> Path:
    if (
        "Authorization" in download_http.headers
        or "authorization" in download_http.headers
    ):
        raise ContractError("auth_forwarding_forbidden")
    if download_http.cookies:
        raise ContractError("auth_forwarding_forbidden")
    if download_http.auth:
        raise ContractError("auth_forwarding_forbidden")

    try:
        parsed_url = urllib.parse.urlsplit(spec.url)
        _ = parsed_url.port
    except ValueError:
        raise ContractError("invalid_origin") from None

    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"

    if origin not in allowed_origins:
        raise ContractError("invalid_origin")

    if (
        parsed_url.scheme == "https"
        or development_mode
        and parsed_url.scheme == "http"
        and parsed_url.hostname in ("127.0.0.1", "localhost", "::1")
    ):
        pass
    else:
        raise ContractError("invalid_origin")

    if parsed_url.username or parsed_url.password:
        raise ContractError("invalid_origin")

    if any(ord(c) < 32 or ord(c) == 127 for c in spec.url):
        raise ContractError("invalid_origin")

    if spec.mime_type != "video/mp4":
        raise ContractError("invalid_mime_type")

    if (
        type(spec.byte_count) is not int
        or isinstance(spec.byte_count, bool)
        or spec.byte_count <= 0
    ):
        raise ContractError("invalid_byte_count")

    if type(max_bytes) is not int or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ContractError("invalid_byte_count")

    if spec.byte_count > min(max_bytes, 1024 * 1024 * 1024):
        raise ContractError("invalid_byte_count")

    if type(spec.sha256) is not str or SHA256_HEX_RE.fullmatch(spec.sha256) is None:
        raise ContractError("invalid_digest")

    if (
        type(total_timeout_seconds) not in (int, float)
        or not math.isfinite(total_timeout_seconds)
        or total_timeout_seconds <= 0
    ):
        raise ContractError("invalid_timeout")

    # Validate destination absent and owner-controlled nonsymlink parentcomponents BEFORE network
    p = destination
    while True:
        try:
            if p.is_symlink():
                raise ContractError("symlink_destination")
        except OSError:
            pass
        if p == p.parent:
            break
        p = p.parent

    try:
        parent_st = destination.parent.stat()
        if not _IS_WINDOWS:
            if parent_st.st_uid != os.getuid():
                raise ContractError("not_owner")
            if stat.S_IMODE(parent_st.st_mode) & 0o077:
                raise ContractError("invalid_permissions")
    except OSError:
        raise ContractError("io_error") from None

    if destination.exists():
        raise ContractError("destination_exists")

    part_path = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.part")

    try:
        fd = os.open(part_path, _part_open_flags(), 0o600)
    except FileExistsError:
        raise ContractError("part_exists") from None
    except OSError:
        raise ContractError("io_error") from None

    hasher = hashlib.sha256()

    async def _stream_into(f: BinaryIO) -> int:
        received = 0
        timeout = httpx.Timeout(total_timeout_seconds)
        async with download_http.stream(
            "GET", spec.url, follow_redirects=False, timeout=timeout
        ) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                raise ContractError("download_failed")
            if response.status_code != 200:
                raise ContractError("download_failed")

            if response.headers.get("Content-Encoding") not in (
                None,
                "identity",
            ):
                raise ContractError("invalid_encoding")

            if response.headers.get("Content-Type") != "video/mp4":
                raise ContractError("invalid_mime_type")

            cl = response.headers.get("Content-Length")
            if cl is not None:
                try:
                    if int(cl) != spec.byte_count:
                        raise ContractError("mismatched_length")
                except ValueError:
                    raise ContractError("mismatched_length") from None

            async for chunk in response.aiter_bytes(chunk_size=65536):
                received += len(chunk)
                if received > spec.byte_count:
                    raise ContractError("oversized_result")
                hasher.update(chunk)
                f.write(chunk)
        return received

    try:
        with open(fd, "wb") as f:  # noqa: ASYNC230
            try:
                # asyncio.wait_for rather than asyncio.timeout, which needs
                # Python 3.11; ComfyUI itself supports 3.10.
                total_bytes = await asyncio.wait_for(
                    _stream_into(f), total_timeout_seconds
                )
            except httpx.RequestError:
                raise ContractError("download_network_error") from None
            # Python 3.10: asyncio.TimeoutError is not yet the builtin.
            except (TimeoutError, asyncio.TimeoutError):
                raise ContractError("download_timeout") from None

        if total_bytes != spec.byte_count:
            raise ContractError("mismatched_length")

        if hasher.hexdigest().lower() != spec.sha256.lower():
            raise ContractError("digest_mismatch")

        try:
            os.link(part_path, destination)
        except FileExistsError:
            raise ContractError("destination_exists") from None
        except OSError:
            if not _IS_WINDOWS:
                raise ContractError("io_error") from None
            # Windows on FAT32/exFAT (and some network shares) has no hard
            # links. os.rename is exclusive there too: it raises
            # FileExistsError when the destination exists, so the contract
            # stays the same. POSIX keeps os.link only, because POSIX rename
            # would silently overwrite an existing destination.
            try:
                os.rename(part_path, destination)
            except FileExistsError:
                raise ContractError("destination_exists") from None
            except OSError:
                raise ContractError("io_error") from None
        else:
            os.remove(part_path)
    except BaseException:
        try:
            os.remove(part_path)
        except OSError:
            pass
        raise

    return destination


class CacheLease:
    def __init__(self, path: Path, cache: MediaCache):
        self.path = path
        self._cache = cache
        self._released = False
        self._finalized_identity: tuple[int, int] | None = None

    def mark_complete(self):
        if self._released:
            raise ContractError("lease_released")
        try:
            st = self.path.lstat()
        except OSError:
            raise ContractError("cache_file_unavailable") from None
        if not stat.S_ISREG(st.st_mode):
            raise ContractError("invalid_cache_file")
        if not _IS_WINDOWS and st.st_uid != os.getuid():
            raise ContractError("invalid_cache_file")
        identity = (st.st_dev, st.st_ino)
        if (
            self._finalized_identity is not None
            and identity != self._finalized_identity
        ):
            raise ContractError("cache_file_replaced")
        self._finalized_identity = identity

    def release(self):
        if not self._released:
            self._cache._release_lease(self.path, self._finalized_identity)
            self._released = True


class MediaCache:
    def __init__(self, root: Path, *, max_total_bytes: int = 2147483648):
        if (
            type(max_total_bytes) is not int
            or isinstance(max_total_bytes, bool)
            or max_total_bytes <= 0
        ):
            raise ContractError("invalid_max_total_bytes")

        self.root = root.absolute()
        self.max_total_bytes = max_total_bytes
        self._active_leases: dict[Path, int] = {}
        # Windows: files ComfyUI still holds open cannot be unlinked. They are
        # parked here and retried on the next allocate() or cleanup pass.
        self._deferred_unlinks: set[Path] = set()

        p = self.root
        while True:
            try:
                if p.is_symlink():
                    raise ContractError("symlink_root")
            except OSError:
                pass
            if p == p.parent:
                break
            p = p.parent

        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            raise ContractError("io_error") from None

        try:
            st = self.root.stat()
        except OSError:
            raise ContractError("io_error") from None

        if not _IS_WINDOWS:
            if st.st_uid != os.getuid():
                raise ContractError("not_owner")

            if stat.S_IMODE(st.st_mode) != 0o700:
                raise ContractError("invalid_permissions")

    def _get_budget(self) -> int:
        return sum(self._active_leases.values())

    def allocate(self, size: int) -> CacheLease:
        self.retry_deferred_unlinks()
        if type(size) is not int or isinstance(size, bool) or size <= 0:
            raise ContractError("invalid_size")

        if self._get_budget() + size > self.max_total_bytes:
            raise ContractError("cache_budget_exceeded")

        filename = f"{uuid.uuid4().hex}.mp4"
        path = self.root / filename

        self._active_leases[path] = size
        return CacheLease(path, self)

    def _release_lease(self, path: Path, finalized_identity: tuple[int, int] | None):
        if path in self._active_leases:
            if finalized_identity is not None:
                try:
                    st = path.lstat()
                    if (st.st_dev, st.st_ino) == finalized_identity:
                        path.unlink()
                    # A replacement is no longer our file. Never unlink it.
                    del self._active_leases[path]
                except FileNotFoundError:
                    del self._active_leases[path]
                except PermissionError:
                    if not _IS_WINDOWS:
                        # Preserve reservation and allow a later release retry.
                        raise ContractError("cache_cleanup_failed") from None
                    # Windows keeps the file open while ComfyUI previews or
                    # saves it; free the budget now and delete it later.
                    self._deferred_unlinks.add(path)
                    del self._active_leases[path]
                    _logger.debug("Deferred cache unlink, file still open: %s", path)
                except OSError:
                    # Preserve reservation and allow a later release retry.
                    raise ContractError("cache_cleanup_failed") from None
            else:
                del self._active_leases[path]

    def retry_deferred_unlinks(self) -> None:
        """Best-effort removal of files parked because Windows held them open."""
        for path in list(self._deferred_unlinks):
            try:
                path.unlink()
            except FileNotFoundError:
                self._deferred_unlinks.discard(path)
            except PermissionError:
                # Still open by a preview/save node; retried on a later pass.
                continue
            except OSError:
                continue
            else:
                self._deferred_unlinks.discard(path)
