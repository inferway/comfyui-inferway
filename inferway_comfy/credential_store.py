"""Private machine-local credential store for Inferway ComfyUI profiles.

Keys live in one JSON file under ComfyUI's *system user* directory
(``folder_paths.get_system_user_directory("inferway")`` -> ``user/__inferway``),
which no HTTP route serves. Nothing here is written into ComfyUI settings,
workflow JSON, or PNG metadata, and no log line ever contains a key.
"""

from __future__ import annotations

import csv
import errno
import json
import logging
import os
import re
import stat
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

# Mirrors the Windows-lane convention so both lanes monkeypatch the same flag.
_IS_WINDOWS = os.name == "nt"

STORE_DIRECTORY_NAME = "inferway"
STORE_FILENAME = "credentials.json"
STORE_VERSION = 1
DEFAULT_PROFILE = "default"

#: Profile names: lowercase alnum first, then lowercase alnum / ``-`` / ``_``,
#: 1 to 32 characters total. Matched with :func:`re.Pattern.fullmatch` so a
#: trailing newline can never slip through ``$``.
PROFILE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

MIN_API_KEY_LENGTH = 8
MAX_API_KEY_LENGTH = 512
#: ``last4`` is only reported for keys long enough that four characters do not
#: give away the whole secret.
LAST4_MIN_KEY_LENGTH = 12

_REPLACE_RETRIES = 5
_REPLACE_RETRY_DELAY_SECONDS = 0.05
_SUBPROCESS_TIMEOUT_SECONDS = 10

# Read outcomes. ``unreadable`` (an OSError while opening the file) is never
# confused with an empty store: writing on top of it would destroy keys.
STORE_OK = "ok"
STORE_MISSING = "missing"
STORE_CORRUPT = "corrupt"
STORE_UNREADABLE = "unreadable"
STORE_VERSION_MISMATCH = "version_mismatch"
STORE_SYMLINK = "symlink"

ERROR_STORE_UNAVAILABLE = "store_unavailable"
ERROR_STORE_VERSION = "store_version"


class StoreError(Exception):
    """Closed store failure. Carries a safe code, never a key."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def is_valid_profile_name(name: object) -> bool:
    """Return True when ``name`` matches the documented profile-name shape."""
    if not isinstance(name, str):
        return False
    return PROFILE_NAME_PATTERN.fullmatch(name) is not None


def is_valid_api_key(value: object) -> bool:
    """Return True for a printable-ASCII key of 8..512 characters.

    Surrounding whitespace is ignored (callers strip first); whitespace inside
    the key is rejected.
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not MIN_API_KEY_LENGTH <= len(candidate) <= MAX_API_KEY_LENGTH:
        return False
    # 33..126 excludes space, control characters and DEL.
    return all(33 <= ord(c) <= 126 for c in candidate)


def normalize_api_key(value: object) -> str | None:
    """Strip surrounding whitespace and return the key, or None when unusable."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if is_valid_api_key(candidate) else None


def _last4(key: str) -> str | None:
    if len(key) < LAST4_MIN_KEY_LENGTH:
        return None
    return key[-4:]


def _default_root() -> Path:
    """Resolve ComfyUI's private ``user/__inferway`` directory (lazy import)."""
    import folder_paths

    return Path(folder_paths.get_system_user_directory(STORE_DIRECTORY_NAME))


def _is_symlink(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode)


def _read_bytes(path: Path) -> bytes:
    """Open without following a symlink, then read a regular file.

    ``O_NOFOLLOW`` plus the post-open ``fstat`` closes the check-then-read race
    on platforms that have it (``getattr`` falls back to 0 on Windows).
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _replace_with_retry(src: Path, dst: Path) -> None:
    """``os.replace`` with Windows-style PermissionError retries."""
    for attempt in range(_REPLACE_RETRIES + 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt >= _REPLACE_RETRIES:
                raise
            _logger.warning(
                "Inferway profile store replace blocked; retrying (%d)", attempt + 1
            )
            time.sleep(_REPLACE_RETRY_DELAY_SECONDS)


def _windows_identity() -> str | None:
    """Current user SID via ``whoami``, falling back to the environment."""
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=False,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Inferway whoami unavailable: %s", type(exc).__name__)
        result = None

    if result is not None and getattr(result, "returncode", 1) == 0:
        stdout = getattr(result, "stdout", "") or ""
        if isinstance(stdout, (bytes, bytearray)):
            stdout = bytes(stdout).decode("utf-8", "replace")
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parts = next(csv.reader([line]))
            except Exception:  # noqa: BLE001 - malformed CSV line
                parts = []
            if len(parts) >= 2 and parts[1].startswith("S-1-"):
                return parts[1]

    domain = os.environ.get("USERDOMAIN")
    user = os.environ.get("USERNAME")
    if domain and user:
        return f"{domain}\\{user}"
    return None


def _restrict_windows_acl(path: Path, *, is_directory: bool = False) -> bool:
    """Best-effort icacls tightening; never raises, never blocks a save."""
    principal = _windows_identity()
    if not principal:
        return False
    # icacls takes a bare "S-1-..." for an account NAME and fails with "No
    # mapping between account names and security IDs"; a SID needs "*".
    if principal.startswith("S-1-"):
        principal = f"*{principal}"
    grant = f"{principal}:(OI)(CI)F" if is_directory else f"{principal}:F"
    try:
        res = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                grant,
            ],
            check=False,
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Inferway icacls unavailable: %s", type(exc).__name__)
        return False
    return getattr(res, "returncode", 1) == 0


@dataclass(frozen=True)
class SaveResult:
    """Outcome of a store mutation. Never carries a key."""

    ok: bool
    profile: str
    last4: str | None = None
    acl_restricted: bool = False
    error: str | None = None


class CredentialStore:
    """Atomic, owner-only JSON store of named API key profiles."""

    def __init__(self, root: Path | str | None = None) -> None:
        self._root: Path | None = Path(root) if root is not None else None

    @property
    def root(self) -> Path:
        if self._root is None:
            self._root = _default_root()
        return self._root

    @property
    def path(self) -> Path:
        return self.root / STORE_FILENAME

    # -- read -------------------------------------------------------------

    def _read_profiles(self) -> tuple[dict[str, str], str]:
        """Return ``(profiles, state)``; a broken store reads as empty."""
        path = self.path
        if not _IS_WINDOWS and _is_symlink(path):
            _logger.warning("Inferway profile store path is a symlink; ignoring")
            return {}, STORE_SYMLINK

        try:
            raw = _read_bytes(path)
        except FileNotFoundError:
            return {}, STORE_MISSING
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ELOOP:
                _logger.warning("Inferway profile store path is a symlink; ignoring")
                return {}, STORE_SYMLINK
            _logger.warning("Inferway profile store unreadable: %s", type(exc).__name__)
            return {}, STORE_UNREADABLE

        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            _logger.warning(
                "Inferway profile store is not valid JSON; treating as empty"
            )
            return {}, STORE_CORRUPT

        if not isinstance(data, dict):
            _logger.warning("Inferway profile store has no object root")
            return {}, STORE_CORRUPT

        if data.get("version") != STORE_VERSION:
            _logger.warning("Inferway profile store version is not %d", STORE_VERSION)
            return {}, STORE_VERSION_MISMATCH

        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            _logger.warning("Inferway profile store has no profiles object")
            return {}, STORE_CORRUPT

        out: dict[str, str] = {}
        for name, entry in profiles.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                continue
            key = entry.get("api_key")
            if isinstance(key, str) and key:
                out[name] = key
        return out, STORE_OK

    def _profile_names(self, profiles: dict[str, str]) -> list[str]:
        names = [
            name
            for name in profiles
            if name != DEFAULT_PROFILE and is_valid_profile_name(name)
        ]
        return [DEFAULT_PROFILE] + sorted(names)

    # -- write ------------------------------------------------------------

    def _prepare_root(self) -> str | None:
        """Create/lock down the store directory. Returns an error code or None."""
        root = self.root
        if not _IS_WINDOWS and _is_symlink(root):
            return "unsafe_path"
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return ERROR_STORE_UNAVAILABLE
        if not _IS_WINDOWS:
            if _is_symlink(root):
                return "unsafe_path"
            try:
                # Owner-only directory: tighter than the rule's 0o644 advice.
                # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
                os.chmod(root, 0o700)
            except OSError:
                return ERROR_STORE_UNAVAILABLE
            if _is_symlink(self.path):
                return "unsafe_path"
        return None

    def _backup_corrupt_store(self) -> None:
        """Move an unreadable-as-JSON store aside before it could be overwritten."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.path.with_name(f"{STORE_FILENAME}.corrupt-{stamp}")
        counter = 0
        while target.exists():
            counter += 1
            target = self.path.with_name(f"{STORE_FILENAME}.corrupt-{stamp}-{counter}")
        _replace_with_retry(self.path, target)
        _logger.warning(
            "Inferway profile store was corrupt; original kept as %s",
            target.name,
        )

    def _persist(self, profiles: dict[str, str]) -> str | None:
        """Atomic temp-file + fsync + rename write. Returns an error code or None."""
        payload = {
            "version": STORE_VERSION,
            "profiles": {
                name: {"api_key": key} for name, key in sorted(profiles.items())
            },
        }
        body = json.dumps(payload, indent=2, sort_keys=True) + "\n"

        tmp_path: Path | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=".credentials-", suffix=".tmp", dir=str(self.root)
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            if not _IS_WINDOWS:
                os.chmod(tmp_path, 0o600)
            _replace_with_retry(tmp_path, self.path)
            tmp_path = None
        except OSError as exc:
            _logger.warning(
                "Inferway profile store write failed: %s", type(exc).__name__
            )
            return ERROR_STORE_UNAVAILABLE
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
        return None

    # -- public API -------------------------------------------------------

    def list_profiles(self) -> list[str]:
        """``default`` first, then the remaining valid names alphabetically."""
        profiles, state = self._read_profiles()
        if state == STORE_UNREADABLE:
            return [DEFAULT_PROFILE]
        return self._profile_names(profiles)

    def get_key(self, name: object) -> str | None:
        if not is_valid_profile_name(name):
            return None
        profiles, state = self._read_profiles()
        if state == STORE_UNREADABLE:
            raise StoreError(ERROR_STORE_UNAVAILABLE)
        return profiles.get(str(name))

    def set_key(self, name: object, key: object) -> SaveResult:
        profile = name if isinstance(name, str) else ""
        if not is_valid_profile_name(name):
            return SaveResult(ok=False, profile=profile, error="invalid_profile")
        normalized = normalize_api_key(key)
        if normalized is None:
            return SaveResult(ok=False, profile=profile, error="invalid_api_key")

        error = self._prepare_root()
        if error is not None:
            return SaveResult(ok=False, profile=profile, error=error)

        acl_restricted = True
        if _IS_WINDOWS:
            # Tighten the directory *before* the first key lands in it.
            acl_restricted = _restrict_windows_acl(self.root, is_directory=True)

        profiles, state = self._read_profiles()
        if state == STORE_UNREADABLE:
            return SaveResult(ok=False, profile=profile, error=ERROR_STORE_UNAVAILABLE)
        if state == STORE_VERSION_MISMATCH:
            return SaveResult(ok=False, profile=profile, error=ERROR_STORE_VERSION)
        if state == STORE_CORRUPT:
            try:
                self._backup_corrupt_store()
            except OSError as exc:
                _logger.warning(
                    "Inferway profile store backup failed: %s",
                    type(exc).__name__,
                )
                return SaveResult(
                    ok=False, profile=profile, error=ERROR_STORE_UNAVAILABLE
                )
            profiles = {}

        profiles[str(name)] = normalized
        error = self._persist(profiles)
        if error is not None:
            return SaveResult(ok=False, profile=profile, error=error)

        if _IS_WINDOWS:
            acl_restricted = acl_restricted and _restrict_windows_acl(self.path) is True
        return SaveResult(
            ok=True,
            profile=profile,
            last4=_last4(normalized),
            acl_restricted=acl_restricted,
        )

    def delete_profile(self, name: object) -> bool:
        """Drop one profile and keep every other entry untouched."""
        if not is_valid_profile_name(name):
            return False
        if not self.root.exists():
            return False
        error = self._prepare_root()
        if error is not None:
            if error == ERROR_STORE_UNAVAILABLE:
                raise StoreError(ERROR_STORE_UNAVAILABLE)
            return False
        profiles, state = self._read_profiles()
        if state == STORE_UNREADABLE:
            raise StoreError(ERROR_STORE_UNAVAILABLE)
        if state != STORE_OK:
            # Corrupt / unknown-version stores are never written by a delete.
            return False
        key = str(name)
        if key not in profiles:
            return False
        del profiles[key]
        return self._persist(profiles) is None

    def status(self, environ: Mapping[str, str] | None = None) -> list[dict]:
        """Per-profile source/last4 view. Never returns a key.

        The environment key is reported exactly the way :func:`load_settings`
        would use it: stripped and validated the same way, and consumed only
        when ``INFERWAY_PROFILE`` does not redirect ``default`` elsewhere.
        """
        env = os.environ if environ is None else environ
        profiles, state = self._read_profiles()
        if state == STORE_UNREADABLE:
            raise StoreError(ERROR_STORE_UNAVAILABLE)

        env_key = env.get("INFERWAY_API_KEY")
        normalized_env = normalize_api_key(env_key)
        env_feeds: str | None = None
        if normalized_env is not None:
            env_profile = env.get("INFERWAY_PROFILE")
            if env_profile is None or env_profile == DEFAULT_PROFILE:
                env_feeds = DEFAULT_PROFILE
            elif not is_valid_profile_name(env_profile):
                # load_settings() raises unsupported_profile for every profile.
                env_feeds = None
            # Otherwise ``default`` is redirected to a named profile, which
            # load_settings() reads from the store only.

        entries: list[dict[str, Any]] = []
        for name in self._profile_names(profiles):
            key: str | None = None
            source: str | None = None
            if name == env_feeds:
                key = normalized_env
                source = "env"
            elif name in profiles:
                key = profiles[name]
                source = "file"
            entries.append(
                {
                    "name": name,
                    "configured": key is not None,
                    "source": source,
                    "last4": _last4(key) if key is not None else None,
                }
            )
        return entries


def _resolve(store: CredentialStore | None) -> CredentialStore:
    return store if store is not None else CredentialStore()


def list_profiles(store: CredentialStore | None = None) -> list[str]:
    return _resolve(store).list_profiles()


def profile_choices(store: CredentialStore | None = None) -> list[str]:
    """Static ``profile`` dropdown options: ``default`` first, then the rest.

    Read-only: never creates the directory, never touches permissions, never
    runs icacls, and never raises. The result holds profile *names* only —
    no key, no ``last4``.
    """
    try:
        names = list_profiles(store=store)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Inferway profile choices unavailable: %s", type(exc).__name__)
        return [DEFAULT_PROFILE]
    out = [DEFAULT_PROFILE]
    for name in names:
        if name == DEFAULT_PROFILE or not is_valid_profile_name(name):
            continue
        out.append(name)
    return out


def get_key(name: object, store: CredentialStore | None = None) -> str | None:
    return _resolve(store).get_key(name)


def set_key(
    name: object, key: object, store: CredentialStore | None = None
) -> SaveResult:
    return _resolve(store).set_key(name, key)


def delete_profile(name: object, store: CredentialStore | None = None) -> bool:
    return _resolve(store).delete_profile(name)


def status(
    environ: Mapping[str, str] | None = None, store: CredentialStore | None = None
) -> list[dict]:
    return _resolve(store).status(environ)
