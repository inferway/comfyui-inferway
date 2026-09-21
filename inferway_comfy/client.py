"""Client transport and API surface for Inferway ComfyUI."""

from __future__ import annotations

import json
import math
import urllib.parse
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from .credentials import (
    DEFAULT_API_ORIGIN,
    LOOPBACK_HOSTS,
    ClientError,
    ClientSettings,
    create_http_client,
)

KNOWN_STATES: frozenset[str] = frozenset(
    {"queued", "running", "succeeded", "failed", "cancelled"}
)
KNOWN_CANCEL_OUTCOMES: frozenset[str] = frozenset(
    {"pending", "cancelled", "delivering", "delivered", "refused"}
)
KNOWN_SERVER_ERROR_CODES: frozenset[str] = frozenset(
    {
        "activation_disabled",
        "mode_unavailable",
        "mode_media_conflict",
        "first_frame_required",
        "unsupported_interaction_mode",
        "unsupported_model",
        "invalid_prompt",
        "invalid_seed",
        "invalid_duration",
        "missing_duration",
        "invalid_size",
        "inline_media_unsupported",
        "missing_idempotency_key",
        "invalid_idempotency_key",
        "payment_required",
        "account_restricted",
        "rate_limited",
        "service_unavailable",
        "conflict",
        "not_found",
        "unauthorized",
        "forbidden",
        "bad_request",
        "invalid_request",
    }
)


def _parse_retry_after(header_val: str | None) -> float | None:
    if not header_val:
        return None
    header_val = header_val.strip()
    try:
        val = float(header_val)
        if not math.isfinite(val) or val < 0.0:
            return None
        return val
    except (ValueError, TypeError):
        pass
    try:
        import time
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(header_val)
        diff = dt.timestamp() - time.time()
        if not math.isfinite(diff) or diff < 0.0:
            return None
        return diff
    except (ValueError, TypeError, OverflowError):
        return None


def _derive_delivered_cancel(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Derive a normalized cancel block for an already-terminal succeeded interaction.

    Fails closed: requires state == "succeeded", result_available is True,
    and a structurally valid server billing block with currency == "USD"
    and a finite non-negative string Decimal charged_amount.
    """
    if payload.get("state") != "succeeded":
        return None
    if payload.get("result_available") is not True:
        return None

    billing = payload.get("billing")
    if not isinstance(billing, Mapping):
        return None
    if billing.get("currency") != "USD":
        return None

    charged_amount_raw = billing.get("charged_amount")
    if type(charged_amount_raw) is not str:
        return None

    try:
        amt = Decimal(charged_amount_raw)
    except (InvalidOperation, TypeError, ValueError):
        return None

    if not amt.is_finite() or amt < Decimal(0):
        return None

    return {
        "outcome": "delivered",
        "charged": amt > Decimal(0),
    }


class InferwayClient:
    """Injectable client for Inferway models and interactions API."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        settings: ClientSettings | None = None,
        base_origin: str | None = None,
    ) -> None:
        self._http = http
        self._settings = settings
        self._base_origin = base_origin

        if base_origin is not None:
            # base_origin string must not authorize external arbitrary origin
            try:
                parsed = urllib.parse.urlsplit(base_origin.strip())
            except (ValueError, AttributeError):
                raise ClientError("invalid_origin") from None

            if parsed.scheme not in ("http", "https"):
                raise ClientError("invalid_origin")
            if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
                raise ClientError("invalid_origin")
            if (parsed.path and parsed.path != "/") or parsed.query or parsed.fragment:
                raise ClientError("invalid_origin")

            norm_origin = f"{parsed.scheme}://{parsed.netloc}"
            dev_mode = settings.development_mode if settings else False
            if norm_origin != DEFAULT_API_ORIGIN:
                if not dev_mode:
                    raise ClientError("unauthorized_origin")
                hostname = parsed.hostname
                if not hostname or hostname not in LOOPBACK_HOSTS:
                    raise ClientError("unauthorized_origin")

    def _verify_origin(self) -> None:
        """Verify the client base origin before sending any authenticated request."""
        base_url_str = str(self._http.base_url).rstrip("/")
        if not base_url_str:
            raise ClientError("invalid_origin")

        try:
            parsed = urllib.parse.urlsplit(base_url_str)
        except (ValueError, AttributeError):
            raise ClientError("invalid_origin") from None

        if parsed.scheme not in ("http", "https"):
            raise ClientError("invalid_origin")
        if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
            raise ClientError("invalid_origin")
        if (parsed.path and parsed.path != "/") or parsed.query or parsed.fragment:
            raise ClientError("invalid_origin")

        origin = f"{parsed.scheme}://{parsed.netloc}"

        dev_mode = False
        if self._settings is not None:
            dev_mode = self._settings.development_mode
            if origin != self._settings.api_origin:
                raise ClientError("unauthorized_origin")

        if self._base_origin is not None and origin != self._base_origin:
            raise ClientError("unauthorized_origin")

        # External arbitrary origins are NEVER authorized
        if origin != DEFAULT_API_ORIGIN:
            if not dev_mode:
                raise ClientError("unauthorized_origin")
            hostname = parsed.hostname
            if not hostname or hostname not in LOOPBACK_HOSTS:
                raise ClientError("unauthorized_origin")

    async def _send_request(
        self,
        method: str,
        path: str,
        *,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self._verify_origin()

        try:
            # Force follow_redirects=False on every request
            resp = await self._http.request(
                method,
                path,
                content=content,
                headers=headers,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise ClientError("timeout") from None
        except httpx.RequestError:
            raise ClientError("network_error") from None
        except Exception:  # noqa: BLE001
            raise ClientError("network_error") from None

        if 300 <= resp.status_code < 400:
            raise ClientError("redirect_refused", status_code=resp.status_code)

        return resp

    def _handle_response(self, resp: httpx.Response) -> dict:
        retry_after = _parse_retry_after(resp.headers.get("retry-after"))

        if 200 <= resp.status_code < 300:
            try:
                payload = resp.json()
            except (ValueError, TypeError):
                raise ClientError(
                    "invalid_json",
                    status_code=resp.status_code,
                    retry_after_seconds=retry_after,
                ) from None
            if not isinstance(payload, dict):
                raise ClientError(
                    "invalid_response_shape",
                    status_code=resp.status_code,
                    retry_after_seconds=retry_after,
                )
            return payload

        # Extract closed allowlist server error code if available
        code = None
        try:
            data = resp.json()
            if isinstance(data, dict):
                err = data.get("error")
                if isinstance(err, dict):
                    for k in ("code", "type"):
                        val = err.get(k)
                        if isinstance(val, str) and val in KNOWN_SERVER_ERROR_CODES:
                            code = val
                            break
                elif isinstance(err, str) and err in KNOWN_SERVER_ERROR_CODES:
                    code = err
                if code is None:
                    for k in ("code", "detail"):
                        val = data.get(k)
                        if isinstance(val, str) and val in KNOWN_SERVER_ERROR_CODES:
                            code = val
                            break
        except (ValueError, TypeError):
            pass

        if code is None:
            if resp.status_code == 401:
                code = "unauthorized"
            elif resp.status_code == 403:
                code = "forbidden"
            elif resp.status_code == 404:
                code = "not_found"
            elif resp.status_code == 409:
                code = "conflict"
            elif resp.status_code == 429:
                code = "rate_limited"
            elif resp.status_code >= 500:
                code = "server_error"
            else:
                code = "client_error"

        raise ClientError(
            code,
            status_code=resp.status_code,
            retry_after_seconds=retry_after,
        )

    async def models(self) -> dict:
        """GET /v1/models."""
        resp = await self._send_request("GET", "/v1/models")
        return self._handle_response(resp)

    async def prepare_upload(
        self,
        *,
        purpose: str,
        content_type: str,
        content_length: int,
    ) -> dict:
        """POST /v1/interactions with op=prepare_upload."""
        body = {
            "op": "prepare_upload",
            "purpose": purpose,
            "content_type": content_type,
            "content_length": content_length,
        }
        content = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        resp = await self._send_request(
            "POST",
            "/v1/interactions",
            content=content,
            headers=headers,
        )
        return self._handle_response(resp)

    async def create(
        self,
        body: dict,
        *,
        idempotency_key: str,
        sleep: Any = None,
    ) -> dict:
        """POST /v1/interactions with caller-validated body and Idempotency-Key.

        Freezes serialized request bytes before transmission. Retries at most
        three attempts total for transport/5xx ambiguity. 4xx is never retried.
        """
        if not isinstance(idempotency_key, str):
            raise ClientError("invalid_idempotency_key")

        if not (1 <= len(idempotency_key) <= 191):
            raise ClientError("invalid_idempotency_key")

        if not all(32 <= ord(c) <= 126 for c in idempotency_key):
            raise ClientError("invalid_idempotency_key")

        if not isinstance(body, dict):
            raise ClientError("invalid_body")

        # Freeze serialized request bytes before transmission
        serialized_bytes = json.dumps(
            body, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }

        attempts = 0
        last_error: ClientError | None = None

        while attempts < 3:
            attempts += 1
            try:
                resp = await self._send_request(
                    "POST",
                    "/v1/interactions",
                    content=serialized_bytes,
                    headers=headers,
                )
                retry_after = _parse_retry_after(resp.headers.get("retry-after"))

                if 200 <= resp.status_code < 300:
                    try:
                        payload = resp.json()
                    except (ValueError, TypeError):
                        last_error = ClientError(
                            "malformed_response",
                            status_code=resp.status_code,
                            retry_after_seconds=retry_after,
                        )
                        continue

                    if not isinstance(payload, dict):
                        last_error = ClientError(
                            "malformed_response",
                            status_code=resp.status_code,
                            retry_after_seconds=retry_after,
                        )
                        continue

                    interaction_id = payload.get("id")
                    state = payload.get("state")
                    if (
                        not isinstance(interaction_id, str)
                        or not interaction_id.strip()
                        or not isinstance(state, str)
                        or state not in KNOWN_STATES
                    ):
                        last_error = ClientError(
                            "malformed_response",
                            status_code=resp.status_code,
                            retry_after_seconds=retry_after,
                        )
                        continue

                    return payload

                # Non-2xx response
                if 400 <= resp.status_code < 500:
                    # 4xx (including 409) is never converted to a new key and never retried
                    self._handle_response(resp)

                # 5xx error: record ambiguity and allow retry
                last_error = ClientError(
                    "server_error",
                    status_code=resp.status_code,
                    retry_after_seconds=retry_after,
                )

            except ClientError as e:
                # Do not retry 4xx errors, redirect_refused, unauthorized_origin, etc.
                if e.status_code is not None and 400 <= e.status_code < 500:
                    raise
                if e.code in (
                    "redirect_refused",
                    "unauthorized_origin",
                    "invalid_idempotency_key",
                ):
                    raise
                last_error = e

            if attempts < 3 and sleep is not None:
                delay = last_error.retry_after_seconds if last_error else None
                if delay is not None and delay > 0:
                    await sleep(delay)

        if last_error is not None:
            raise last_error

        raise ClientError("create_ambiguous")

    async def get(self, interaction_id: str) -> dict:
        """POST /v1/interactions with op=get."""
        if not isinstance(interaction_id, str) or not interaction_id.strip():
            raise ClientError("invalid_interaction_id")

        body = {"op": "get", "interaction_id": interaction_id}
        content = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        resp = await self._send_request(
            "POST",
            "/v1/interactions",
            content=content,
            headers=headers,
        )
        payload = self._handle_response(resp)

        state = payload.get("state")
        if not isinstance(state, str) or state not in KNOWN_STATES:
            raise ClientError("unknown_state")

        return payload

    async def list(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        state: str | list[str] | None = None,
        since: str | None = None,
    ) -> dict:
        """POST /v1/interactions with op=list."""
        if type(limit) is not int or isinstance(limit, bool) or not (1 <= limit <= 50):
            raise ClientError("invalid_limit")

        body: dict[str, Any] = {"op": "list", "limit": limit}
        if cursor is not None:
            body["cursor"] = cursor
        if state is not None:
            states = [state] if isinstance(state, str) else state
            if (
                type(states) is not list
                or not 1 <= len(states) <= len(KNOWN_STATES)
                or any(
                    type(value) is not str or value not in KNOWN_STATES
                    for value in states
                )
            ):
                raise ClientError("invalid_state")
            body["state"] = list(states)
        if since is not None:
            body["since"] = since

        content = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        resp = await self._send_request(
            "POST",
            "/v1/interactions",
            content=content,
            headers=headers,
        )
        payload = self._handle_response(resp)

        items = payload.get("items")
        if not isinstance(items, (list, tuple)):
            raise ClientError("invalid_list_response")

        for item in items:
            if not isinstance(item, dict):
                raise ClientError("invalid_list_response")
            if "state" in item:
                s = item["state"]
                if not isinstance(s, str) or s not in KNOWN_STATES:
                    raise ClientError("invalid_list_response")
            if "id" in item:
                iid = item["id"]
                if not isinstance(iid, str) or not iid.strip():
                    raise ClientError("invalid_list_response")

        next_cursor = payload.get("next_cursor")
        if next_cursor is not None and (
            type(next_cursor) is not str or not next_cursor.strip()
        ):
            raise ClientError("invalid_list_response")

        return payload

    async def cancel(self, interaction_id: str) -> dict:
        """POST /v1/interactions with op=cancel."""
        if not isinstance(interaction_id, str) or not interaction_id.strip():
            raise ClientError("invalid_interaction_id")

        body = {"op": "cancel", "interaction_id": interaction_id}
        content = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        resp = await self._send_request(
            "POST",
            "/v1/interactions",
            content=content,
            headers=headers,
        )
        payload = self._handle_response(resp)

        cancel_info = payload.get("cancel")
        if not isinstance(cancel_info, dict):
            if cancel_info is None:
                derived = _derive_delivered_cancel(payload)
                if derived is not None:
                    cancel_info = derived
                    payload["cancel"] = cancel_info
                else:
                    raise ClientError("missing_cancel_outcome")
            else:
                raise ClientError("missing_cancel_outcome")

        outcome = cancel_info.get("outcome")
        if not isinstance(outcome, str) or outcome not in KNOWN_CANCEL_OUTCOMES:
            raise ClientError("unknown_cancel_outcome")
        if type(cancel_info.get("charged")) is not bool:
            raise ClientError("invalid_cancel_charged")

        return payload


def create_client(settings: ClientSettings) -> InferwayClient:
    """Create an InferwayClient with settings and http client."""
    http = create_http_client(settings)
    return InferwayClient(http, settings=settings)
