"""Execution slot, idempotency keys, records and polling coordinator for Inferway ComfyUI."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .client import KNOWN_STATES, InferwayClient
from .credentials import ClientError


@dataclass(frozen=True)
class ExecutionSlot:
    """Execution identity slot binding workflow and environment context."""

    origin: str
    profile: str
    prompt_id: str
    node_id: str
    list_index: int | None = None
    contract_version: str = "v1"


def generate_idempotency_key(slot: ExecutionSlot) -> str:
    """Generate a deterministic, printable ASCII Idempotency-Key for an execution slot.

    Uses unambiguous canonical structured JSON with length boundaries for slot-key preimage.
    Never includes API key, raw prompt or media.
    """
    canonical_data = [
        "inferway-comfy",
        slot.contract_version,
        slot.origin,
        slot.profile,
        slot.prompt_id,
        slot.node_id,
        slot.list_index,
    ]
    preimage = json.dumps(canonical_data, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(preimage.encode("utf-8")).hexdigest()
    return f"ik_{slot.contract_version}_{digest}"


class ExecutionConflict(Exception):
    """Raised when an execution slot is bound to differing content or intents."""


class WaitTimeoutError(ClientError):
    """Raised when waiting times out locally. Carries interaction_id and never cancels remote work."""

    def __init__(self, interaction_id: str) -> None:
        self.interaction_id = interaction_id
        super().__init__("wait_timeout")


class ExecutionRecord:
    """Execution record bound to an ExecutionSlot."""

    def __init__(self, slot: ExecutionSlot) -> None:
        self.slot = slot
        self._key = generate_idempotency_key(slot)
        self._fingerprint: str | None = None
        self._body: dict | None = None
        self._uploaded_ids: dict[str, str] = {}
        self._interaction_id: str | None = None
        self._cache_token: str | None = None
        self._result_ref: weakref.ref[Any] | None = None

    @property
    def key(self) -> str:
        return self._key

    @property
    def body(self) -> dict | None:
        if self._body is None:
            return None
        return copy.deepcopy(self._body)

    @property
    def fingerprint(self) -> str | None:
        return self._fingerprint

    @property
    def uploaded_ids(self) -> dict[str, str]:
        return dict(self._uploaded_ids)

    def set_uploaded_id(self, slot_name: str, upload_id: str) -> None:
        self._uploaded_ids[slot_name] = upload_id

    @property
    def interaction_id(self) -> str | None:
        return self._interaction_id

    @interaction_id.setter
    def interaction_id(self, val: str | None) -> None:
        self._interaction_id = val

    @property
    def cache_token(self) -> str | None:
        return self._cache_token

    @cache_token.setter
    def cache_token(self, val: str | None) -> None:
        self._cache_token = val

    @property
    def result(self) -> Any:
        if self._result_ref is not None:
            return self._result_ref()
        return None

    @result.setter
    def result(self, val: Any) -> None:
        if val is None:
            self._result_ref = None
        else:
            try:
                self._result_ref = weakref.ref(val)
            except TypeError:
                self._result_ref = None

    def bind(self, fingerprint: str, body: dict) -> None:
        """Bind fingerprint and body to this execution slot.

        A second identical bind reuses; any changed fingerprint/body raises ExecutionConflict.
        Caller mutation must not change stored bytes.
        """
        if not isinstance(body, dict):
            raise ExecutionConflict("invalid_body")

        if self._fingerprint is None and self._body is None:
            self._fingerprint = fingerprint
            self._body = copy.deepcopy(body)
            return

        if self._fingerprint != fingerprint or self._body != body:
            raise ExecutionConflict("changed_intent_same_slot")


class ExecutionRegistry:
    """Registry mapping ExecutionSlot to an ExecutionRecord and an asyncio.Lock."""

    def __init__(self, capacity: int = 1000) -> None:
        self.capacity = capacity
        self._entries: dict[ExecutionSlot, tuple[ExecutionRecord, asyncio.Lock]] = {}

    def get_or_create(
        self, slot: ExecutionSlot
    ) -> tuple[ExecutionRecord, asyncio.Lock]:
        if slot in self._entries:
            return self._entries[slot]

        if len(self._entries) >= self.capacity:
            raise ExecutionConflict("registry_full")

        record = ExecutionRecord(slot)
        lock = asyncio.Lock()
        self._entries[slot] = (record, lock)
        return record, lock

    def release(self, slot: ExecutionSlot) -> None:
        if slot in self._entries:
            _, lock = self._entries[slot]
            if lock.locked():
                raise ExecutionConflict("cannot_release_locked_record")
            del self._entries[slot]

    def close_prompt(self, prompt_id: str) -> None:
        """Release all records belonging to prompt_id, rejecting still-locked records."""
        matching = [slot for slot in self._entries if slot.prompt_id == prompt_id]
        for slot in matching:
            _, lock = self._entries[slot]
            if lock.locked():
                raise ExecutionConflict("cannot_release_locked_record")
        for slot in matching:
            del self._entries[slot]


async def wait_for_result(
    client: InferwayClient,
    interaction_id: str,
    *,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict:
    """Poll interaction status until completion or deadline.

    Polls get only. Backoff for queued/running states grows from 2 to 10 seconds.
    Temporary network/429/5xx delays are bounded at 30 seconds unless a longer
    Retry-After is specified, always constrained by the remaining local deadline.
    A deadline stops waiting with WaitTimeoutError without calling cancel or create.
    """
    poll_delay = 2.0
    error_delay = 2.0

    while True:
        now = clock()
        remaining = deadline - now
        if remaining <= 0:
            raise WaitTimeoutError(interaction_id)

        step_delay = poll_delay

        try:
            payload = await asyncio.wait_for(
                client.get(interaction_id),
                timeout=remaining,
            )
            if not isinstance(payload, dict):
                raise ClientError("invalid_payload")

            state = payload.get("state")
            if not isinstance(state, str) or state not in KNOWN_STATES:
                raise ClientError("unknown_state")

            if state in ("succeeded", "failed", "cancelled"):
                if state == "succeeded":
                    result = payload.get("result")
                    if result is not None and not isinstance(result, dict):
                        raise ClientError("invalid_payload")
                return payload

            if state in ("queued", "running"):
                error_delay = 2.0
                step_delay = poll_delay
                poll_delay = min(10.0, poll_delay + 2.0)
            else:
                raise ClientError("unknown_state")

        # Python 3.10: asyncio.wait_for raises asyncio.TimeoutError, which only
        # became an alias of the builtin in 3.11.
        except (TimeoutError, asyncio.TimeoutError):
            raise WaitTimeoutError(interaction_id) from None
        except ClientError as e:
            if e.code in (
                "timeout",
                "network_error",
                "server_error",
                "rate_limited",
            ) or (
                e.status_code is not None
                and (e.status_code == 429 or e.status_code >= 500)
            ):
                if e.retry_after_seconds is not None and e.retry_after_seconds > 0:
                    step_delay = e.retry_after_seconds
                else:
                    step_delay = min(30.0, error_delay)
                    error_delay = min(30.0, error_delay * 1.5)
            else:
                raise

        now = clock()
        remaining = deadline - now
        if remaining <= 0:
            raise WaitTimeoutError(interaction_id)

        sleep_duration = min(step_delay, remaining)
        await sleep(sleep_duration)

        if clock() >= deadline:
            raise WaitTimeoutError(interaction_id)
