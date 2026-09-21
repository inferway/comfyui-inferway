"""Bounded media upload and download."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import stat
import urllib.parse
import uuid
from pathlib import Path

import httpx

from .contracts import SHA256_HEX_RE, ContractError, DownloadSpec

# app/interaction_upload_tickets.py: opaque base64url body + signature.
UPLOAD_PATH_RE = re.compile(
    r"^/v1/interactions/uploads/iup_[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"
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
        fd = os.open(
            part_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError:
        raise ContractError("part_exists") from None
    except OSError:
        raise ContractError("io_error") from None

    hasher = hashlib.sha256()
    total_bytes = 0

    try:
        with open(fd, "wb") as f:  # noqa: ASYNC230
            try:
                timeout = httpx.Timeout(total_timeout_seconds)
                async with asyncio.timeout(total_timeout_seconds):
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
                            total_bytes += len(chunk)
                            if total_bytes > spec.byte_count:
                                raise ContractError("oversized_result")
                            hasher.update(chunk)
                            f.write(chunk)
            except httpx.RequestError:
                raise ContractError("download_network_error") from None
            except TimeoutError:
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
            raise ContractError("io_error") from None

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
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
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

        if st.st_uid != os.getuid():
            raise ContractError("not_owner")

        if stat.S_IMODE(st.st_mode) != 0o700:
            raise ContractError("invalid_permissions")

    def _get_budget(self) -> int:
        return sum(self._active_leases.values())

    def allocate(self, size: int) -> CacheLease:
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
                except OSError:
                    # Preserve reservation and allow a later release retry.
                    raise ContractError("cache_cleanup_failed") from None
            else:
                del self._active_leases[path]
