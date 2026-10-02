"""Local-only HTTP routes for Inferway API key profiles.

These routes live on ComfyUI's ``PromptServer`` and are reachable only from a
browser page that is same-origin with ComfyUI on a loopback IP literal or
``localhost``. ComfyUI's own origin middleware is not a sufficient guard
(``--enable-cors-header`` disables it entirely, ``--listen`` accepts LAN
clients, and DNS rebinding keeps Host and Origin equal), so every route
re-checks the request here.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import urllib.parse
from collections.abc import Mapping
from typing import Any

from .credential_store import (
    CredentialStore,
    StoreError,
    list_profiles,
    normalize_api_key,
)

_logger = logging.getLogger(__name__)

# Reason codes returned by check_local_request and rendered as {"error": ...}.
REASON_CORS_OPEN = "cors_open"
REASON_CROSS_SITE = "cross_site"
REASON_HOST_NOT_ALLOWED = "host_not_allowed"
REASON_ORIGIN_MISMATCH = "origin_mismatch"
REASON_ORIGIN_REQUIRED = "origin_required"
REASON_CONTENT_TYPE = "content_type"
REASON_CONTENT_LENGTH = "content_length"
REASON_REMOTE_NOT_ALLOWED = "remote_not_allowed"

_ALLOWED_SEC_FETCH_SITE = frozenset({"same-origin", "none"})
_JSON_CONTENT_TYPE = "application/json"
_HOST_LOOPBACK_NAME = "localhost"
_DEFAULT_PORTS = frozenset({80, 443})

#: Requests larger than this never reach the JSON parser.
MAX_BODY_BYTES = 4096
#: Opt-in escape hatch for a deliberately LAN-exposed ComfyUI.
REMOTE_KEY_EDIT_ENV = "INFERWAY_ALLOW_REMOTE_KEY_EDIT"

_ALLOWED_SEC_FETCH_SITE_FOR_WRITE = "same-origin"


def _split_authority(raw: object) -> tuple[str, str | None] | None:
    """Split ``host[:port]`` (or ``[v6]:port``) into ``(host, port_text)``."""
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if not value:
        return None
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return None
        name = value[1:end]
        if not name:
            return None
        rest = value[end + 1 :]
        if not rest:
            return name, None
        if not rest.startswith(":"):
            return None
        port = rest[1:]
        if not port:
            return None
        return name, port
    if value.count(":") == 1:
        name, _, port = value.partition(":")
        if not name or not port:
            return None
        return name, port
    # No port, or a bare IPv6 literal (several colons).
    return value, None


def _port_number(port: str | None) -> int | None:
    """1..65535 as a pure decimal, else None."""
    if port is None:
        return None
    if not port.isdigit():
        return None
    number = int(port)
    if not (1 <= number <= 65535):
        return None
    return number


def _authority_key(host: str, port: str | None) -> str:
    """Comparable ``host[:port]`` with explicit default ports removed."""
    name = host.strip("[]")
    number = _port_number(port)
    if port is not None and number is None:
        return f"{name}:!invalid-port"
    if number is None or number in _DEFAULT_PORTS:
        return name
    return f"{name}:{number}"


def _is_loopback(value: object) -> bool:
    """True only for a parseable loopback IP literal (127.0.0.0/8, ::1)."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return address.is_loopback


def _is_ip_literal_or_localhost(name: str) -> bool:
    """Only IP literals and ``localhost`` can be trusted as a Host host part."""
    if name.lower() == _HOST_LOOPBACK_NAME:
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def _origin_netloc(origin: str) -> str | None:
    """Host[:port] of an Origin header, or None when it is not http(s)."""
    try:
        parsed = urllib.parse.urlsplit(origin)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    netloc = (parsed.netloc or "").lower()
    return netloc or None


def check_local_request(
    method: str,
    headers: Mapping[str, Any],
    host: object,
    cors_open: bool,
    *,
    allow_when_cors_open: bool = False,
    remote: object = None,
    allow_remote_key_edit: bool | None = None,
) -> str | None:
    """Return a rejection reason code, or None when the request may proceed.

    ``allow_when_cors_open`` exempts a route from the ``cors_open`` refusal only;
    every other rule still applies to it.

    ``remote`` is the socket peer address. Anything that is not a loopback
    address is refused unless ``INFERWAY_ALLOW_REMOTE_KEY_EDIT=1`` (or an
    explicit ``allow_remote_key_edit``) opts in, so a ComfyUI started with
    ``--listen`` cannot have its key store edited from another LAN machine.
    """
    lowered: dict[str, str] = {}
    try:
        items: list[tuple[Any, Any]] = list(headers.items())
    except AttributeError:  # pragma: no cover - defensive
        items = []
    for key, value in items:
        lowered[str(key).lower()] = str(value)

    if cors_open and not allow_when_cors_open:
        return REASON_CORS_OPEN

    if allow_remote_key_edit is None:
        allow_remote_key_edit = os.environ.get(REMOTE_KEY_EDIT_ENV, "") == "1"
    if not allow_remote_key_edit and not _is_loopback(remote):
        return REASON_REMOTE_NOT_ALLOWED

    site = lowered.get("sec-fetch-site")
    if site is not None and site.lower() not in _ALLOWED_SEC_FETCH_SITE:
        return REASON_CROSS_SITE

    parsed_host = _split_authority(host)
    if parsed_host is None:
        return REASON_HOST_NOT_ALLOWED
    hostname, host_port = parsed_host
    if not _is_ip_literal_or_localhost(hostname):
        return REASON_HOST_NOT_ALLOWED
    if host_port is not None and _port_number(host_port) is None:
        return REASON_HOST_NOT_ALLOWED
    host_key = _authority_key(hostname, host_port)

    origin = lowered.get("origin")
    if origin:
        netloc = _origin_netloc(origin)
        parsed_origin = _split_authority(netloc) if netloc is not None else None
        if parsed_origin is None:
            return REASON_ORIGIN_MISMATCH
        origin_host, origin_port = parsed_origin
        if _authority_key(origin_host, origin_port) != host_key:
            return REASON_ORIGIN_MISMATCH

    if str(method).upper() == "POST":
        content_type = lowered.get("content-type", "")
        media_type = content_type.split(";")[0].strip().lower()
        if media_type != _JSON_CONTENT_TYPE:
            return REASON_CONTENT_TYPE

        length = lowered.get("content-length")
        if length is None or not length.isdigit():
            return REASON_CONTENT_LENGTH
        if not (1 <= int(length) <= MAX_BODY_BYTES):
            return REASON_CONTENT_LENGTH

        # A browser always labels a same-origin POST; a bare curl does not.
        site_lower = site.lower() if site is not None else None
        if not origin and site_lower != _ALLOWED_SEC_FETCH_SITE_FOR_WRITE:
            return REASON_ORIGIN_REQUIRED

    return None


def _cors_open() -> bool:
    """True when CORS is open *or* the answer cannot be determined (fail closed)."""
    try:
        from comfy.cli_args import args
    except Exception as exc:  # noqa: BLE001
        _logger.warning("ComfyUI cli_args unavailable: %s", type(exc).__name__)
        return True
    try:
        return bool(getattr(args, "enable_cors_header", False))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("ComfyUI cli_args unreadable: %s", type(exc).__name__)
        return True


def _profile_names() -> list[str]:
    try:
        return list_profiles(store=CredentialStore())
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Inferway profile store unavailable: %s", type(exc).__name__)
        return ["default"]


def _offload(func: Any) -> Any:
    """Run a blocking store read/write off the event loop."""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(None, func)


def register_local_credential_routes() -> None:
    """Register the profile credential routes once, on the running PromptServer."""
    from aiohttp import web
    from server import PromptServer

    server_instance = getattr(PromptServer, "instance", None)
    if server_instance is None:
        return

    routes = getattr(server_instance, "routes", None)
    if routes is None:
        raise RuntimeError("prompt_server_routes_unavailable")

    existing = {
        (getattr(r, "method", "").upper(), getattr(r, "path", None)) for r in routes
    }

    def guard(
        method: str, request: web.Request, *, allow_when_cors_open: bool = False
    ) -> web.Response | None:
        # Read the Host header directly; request.host can trigger a name lookup.
        host_header = request.headers.get("Host")
        reason = check_local_request(
            method,
            request.headers,
            host_header,
            _cors_open(),
            allow_when_cors_open=allow_when_cors_open,
            remote=request.remote,
        )
        if reason is None:
            return None
        return web.json_response({"error": reason}, status=403)

    async def _read_body(request: web.Request) -> dict | None:
        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Inferway profile request body rejected: %s", type(exc).__name__
            )
            return None
        if not isinstance(body, dict):
            return None
        return body

    if ("GET", "/inferway/profiles") not in existing:

        @routes.get("/inferway/profiles")
        async def handle_get_profiles(request: web.Request) -> web.Response:
            rejected = guard("GET", request, allow_when_cors_open=True)
            if rejected is not None:
                return rejected
            return web.json_response(await _offload(_profile_names))

    if ("GET", "/inferway/credentials") not in existing:

        @routes.get("/inferway/credentials")
        async def handle_get_credentials(request: web.Request) -> web.Response:
            rejected = guard("GET", request)
            if rejected is not None:
                return rejected
            try:
                profiles = await _offload(lambda: CredentialStore().status(os.environ))
            except StoreError as exc:
                return web.json_response({"error": exc.code}, status=500)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "Inferway profile store unavailable: %s", type(exc).__name__
                )
                return web.json_response({"error": "store_unavailable"}, status=500)
            return web.json_response(
                {
                    "profiles": profiles,
                    "env_key_present": normalize_api_key(
                        os.environ.get("INFERWAY_API_KEY")
                    )
                    is not None,
                    "cors_open": _cors_open(),
                }
            )

    if ("POST", "/inferway/credentials") not in existing:

        @routes.post("/inferway/credentials")
        async def handle_post_credentials(request: web.Request) -> web.Response:
            rejected = guard("POST", request)
            if rejected is not None:
                return rejected
            body = await _read_body(request)
            if body is None:
                return web.json_response({"error": "invalid_body"}, status=400)
            try:
                result = await _offload(
                    lambda: CredentialStore().set_key(
                        body.get("profile"), body.get("api_key")
                    )
                )
            except StoreError as exc:
                return web.json_response({"error": exc.code}, status=500)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "Inferway profile store unavailable: %s", type(exc).__name__
                )
                return web.json_response({"error": "store_unavailable"}, status=500)
            if not result.ok:
                return web.json_response(
                    {"error": result.error or "invalid_request"}, status=400
                )
            return web.json_response(
                {
                    "ok": True,
                    "profile": result.profile,
                    "last4": result.last4,
                    "acl_restricted": result.acl_restricted,
                }
            )

    if ("POST", "/inferway/credentials/delete") not in existing:

        @routes.post("/inferway/credentials/delete")
        async def handle_post_delete(request: web.Request) -> web.Response:
            rejected = guard("POST", request)
            if rejected is not None:
                return rejected
            body = await _read_body(request)
            if body is None:
                return web.json_response({"error": "invalid_body"}, status=400)
            name = body.get("profile")
            if not isinstance(name, str):
                return web.json_response({"error": "invalid_profile"}, status=400)
            try:
                removed = await _offload(lambda: CredentialStore().delete_profile(name))
            except StoreError as exc:
                return web.json_response({"error": exc.code}, status=500)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "Inferway profile store unavailable: %s", type(exc).__name__
                )
                return web.json_response({"error": "store_unavailable"}, status=500)
            return web.json_response({"ok": removed, "profile": name})
