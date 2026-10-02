"""Credentials and client transport settings for Inferway ComfyUI."""

from __future__ import annotations

import logging
import math
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx

from .credential_store import (
    DEFAULT_PROFILE,
    CredentialStore,
    is_valid_profile_name,
    normalize_api_key,
)

_logger = logging.getLogger(__name__)


class ClientError(Exception):
    """Closed safe error for client operations.

    Carries only a closed safe code, optional HTTP status and parsed retry_after_seconds;
    no raw response, URL/query or original exception text.
    """

    def __init__(
        self,
        code: str,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)

    def __repr__(self) -> str:
        parts = [f"code={self.code!r}"]
        if self.status_code is not None:
            parts.append(f"status_code={self.status_code}")
        if self.retry_after_seconds is not None:
            parts.append(f"retry_after_seconds={self.retry_after_seconds}")
        return f"ClientError({', '.join(parts)})"

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True)
class ClientSettings:
    api_origin: str
    api_key: str = field(repr=False)
    profile: str = "default"
    development_mode: bool = False


DEFAULT_API_ORIGIN = "https://api.inferway.ai"
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})


def _resolve_profile(environ: Mapping[str, str], profile: object) -> str:
    """Pick the effective profile name; refuse anything outside the name shape."""
    if not is_valid_profile_name(profile):
        raise ClientError("unsupported_profile")

    env_profile = environ.get("INFERWAY_PROFILE")
    if env_profile is None:
        return str(profile)
    if not is_valid_profile_name(env_profile):
        raise ClientError("unsupported_profile")
    if profile == DEFAULT_PROFILE and env_profile != DEFAULT_PROFILE:
        return str(env_profile)
    return str(profile)


def _stored_key(store: CredentialStore | None, profile: str) -> str | None:
    """Read one profile from the machine-local store; an unreadable store is empty."""
    try:
        active = store if store is not None else CredentialStore()
        return active.get_key(profile)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Inferway profile store unavailable: %s", type(exc).__name__)
        return None


def _require_printable_api_key(key: str) -> str:
    """Strip surrounding whitespace, then demand a printable ASCII key.

    Whitespace inside the stripped key is rejected the same way control
    characters are: header values must be printable ASCII with no blanks.
    """
    normalized = normalize_api_key(key)
    if normalized is None:
        raise ClientError("invalid_api_key")
    return normalized


def _resolve_api_key(
    environ: Mapping[str, str], profile: str, store: CredentialStore | None
) -> str:
    """Resolve the key: env first for ``default``, store for everything else."""
    if profile != DEFAULT_PROFILE:
        stored = _stored_key(store, profile)
        if stored is None:
            raise ClientError("missing_api_key")
        return _require_printable_api_key(stored)

    env_key = environ.get("INFERWAY_API_KEY")
    if isinstance(env_key, str) and env_key.strip():
        return _require_printable_api_key(env_key)

    stored = _stored_key(store, DEFAULT_PROFILE)
    if stored is None:
        raise ClientError("missing_api_key")
    return _require_printable_api_key(stored)


def load_settings(
    environ: Mapping[str, str],
    *,
    profile: str = DEFAULT_PROFILE,
    store: CredentialStore | None = None,
) -> ClientSettings:
    """Load and validate client settings from environment mapping.

    Key source for the ``default`` profile is INFERWAY_API_KEY when it is set,
    otherwise the machine-local credential store; any other profile name is read
    from the store only, and a name that resolves nowhere fails closed with
    ``missing_api_key``. INFERWAY_PROFILE selects a stored profile for headless
    callers that ask for ``default``; a value outside the profile-name shape is
    refused with ``unsupported_profile``. Empty or header-control-character
    containing key values fail with a fixed safe error. Do not read dotenv files
    or browser state.
    Production origin is exactly https://api.inferway.ai. Only an explicit
    INFERWAY_COMFY_DEVELOPMENT_MODE=1 plus INFERWAY_COMFY_API_ORIGIN allows a loopback
    HTTP origin for isolated tests; reject external hosts, userinfo, path/query/fragment,
    ambiguous ports and redirects.
    """
    if not isinstance(environ, Mapping):
        raise ClientError("invalid_environment")

    resolved_profile = _resolve_profile(environ, profile)
    api_key = _resolve_api_key(environ, resolved_profile, store)

    dev_flag = environ.get("INFERWAY_COMFY_DEVELOPMENT_MODE") == "1"
    custom_origin = environ.get("INFERWAY_COMFY_API_ORIGIN")

    if custom_origin is not None:
        if not dev_flag:
            raise ClientError("invalid_origin")

        if not isinstance(custom_origin, str) or not custom_origin.strip():
            raise ClientError("invalid_origin")

        # Reject control characters and whitespace before urlsplit (urlsplit strips newlines/tabs)
        if any(ord(c) <= 32 or ord(c) == 127 for c in custom_origin):
            raise ClientError("invalid_origin")

        try:
            parsed = urllib.parse.urlsplit(custom_origin.strip())
        except (ValueError, AttributeError):
            raise ClientError("invalid_origin") from None

        if parsed.scheme not in ("http", "https"):
            raise ClientError("invalid_origin")

        if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
            raise ClientError("invalid_origin")

        if (parsed.path and parsed.path != "/") or parsed.query or parsed.fragment:
            raise ClientError("invalid_origin")

        # Reject empty or ambiguous ports
        if parsed.netloc.endswith(":"):
            raise ClientError("invalid_origin")

        hostname = parsed.hostname
        if not hostname or hostname not in LOOPBACK_HOSTS:
            raise ClientError("invalid_origin")

        try:
            port = parsed.port
        except ValueError:
            raise ClientError("invalid_origin") from None

        after_host = parsed.netloc.split("]")[-1]
        if ":" in after_host:
            port_str = after_host.split(":")[-1]
            if (
                not port_str.isdigit()
                or port is None
                or not (1 <= port <= 65535)
                or str(port) != port_str
            ):
                raise ClientError("invalid_origin")

        # IPv6 hostname must be reconstructed with RFC 3986 brackets
        if ":" in hostname:
            api_origin = f"{parsed.scheme}://[{hostname}]" + (
                f":{port}" if port else ""
            )
        else:
            api_origin = f"{parsed.scheme}://{hostname}" + (f":{port}" if port else "")
    else:
        api_origin = DEFAULT_API_ORIGIN

    return ClientSettings(
        api_origin=api_origin,
        api_key=api_key,
        profile=resolved_profile,
        development_mode=dev_flag,
    )


def validate_settings(settings: ClientSettings) -> None:
    """Validate ClientSettings integrity before factory instantiation."""
    if not isinstance(settings, ClientSettings):
        raise ClientError("invalid_settings")

    if normalize_api_key(settings.api_key) != settings.api_key:
        raise ClientError("invalid_api_key")

    if not is_valid_profile_name(settings.profile):
        raise ClientError("unsupported_profile")

    if not isinstance(settings.api_origin, str) or not settings.api_origin.strip():
        raise ClientError("invalid_origin")

    try:
        parsed = urllib.parse.urlsplit(settings.api_origin.strip())
    except (ValueError, AttributeError):
        raise ClientError("invalid_origin") from None

    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        raise ClientError("invalid_origin")
    if (parsed.path and parsed.path != "/") or parsed.query or parsed.fragment:
        raise ClientError("invalid_origin")

    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin != DEFAULT_API_ORIGIN:
        if not settings.development_mode:
            raise ClientError("unauthorized_origin")
        hostname = parsed.hostname
        if not hostname or hostname not in LOOPBACK_HOSTS:
            raise ClientError("unauthorized_origin")


def create_http_client(
    settings: ClientSettings, *, timeout: float = 30.0
) -> httpx.AsyncClient:
    """Create a configured httpx AsyncClient for Inferway API calls."""
    validate_settings(settings)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ClientError("invalid_timeout")
    finite_timeout = httpx.Timeout(timeout, connect=10.0)
    headers = {"Authorization": f"Bearer {settings.api_key}"}
    return httpx.AsyncClient(
        base_url=settings.api_origin,
        headers=headers,
        timeout=finite_timeout,
        follow_redirects=False,
        trust_env=False,
    )
