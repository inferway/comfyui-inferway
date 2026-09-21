"""Inferway client contracts for model discovery, create validation, and result delivery."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

MAX_PROMPT_BYTES = 16384
MAX_DOWNLOAD_RESULT_BYTES = 1024 * 1024 * 1024  # 1 GiB
KNOWN_MODES: tuple[str, ...] = ("t2v", "fl2va", "ref2va")
KNOWN_OUTPUT_MODALITIES: tuple[str, ...] = ("video",)
FRAME_SLOTS: tuple[str, ...] = ("first_frame", "last_frame")
REF_IMAGE_SLOTS: tuple[str, ...] = ("ref_image_1", "ref_image_2", "ref_image_3")
KNOWN_MEDIA_SLOTS: frozenset[str] = frozenset(FRAME_SLOTS + REF_IMAGE_SLOTS)
SUPPORTED_ENVELOPE_FIELDS: frozenset[str] = frozenset({"op", "mode", "model"})
SUPPORTED_CLIENT_FIELDS: frozenset[str] = frozenset(
    {
        "op",
        "mode",
        "model",
        "prompt",
        "duration_seconds",
        "seed",
        "width",
        "height",
        "first_frame",
        "last_frame",
        "ref_image_1",
        "ref_image_2",
        "ref_image_3",
    }
)
MANDATORY_USER_FIELDS: frozenset[str] = frozenset({"prompt", "duration_seconds"})
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ModelCapability:
    model: str
    available_modes: tuple[str, ...]
    durations: tuple[int, ...]
    sizes: tuple[tuple[int, int], ...]
    user_fields: tuple[str, ...]
    contract_version: str = "v1"


@dataclass(frozen=True)
class DownloadSpec:
    url: str
    byte_count: int
    sha256: str
    mime_type: str


class ContractError(ValueError):
    """Bounded local error; never interpolate arbitrary body or signed URLs into exceptions."""


def validate_media_slots(slots: set[str], capability: ModelCapability) -> str:
    """Validate media slot names before upload/create.

    Returns the derived mode ('t2v', 'fl2va', 'ref2va').
    Rejects unknown slots, tail-only, mixed groups, and unavailable modes.
    """
    if not isinstance(slots, (set, frozenset)):
        raise ContractError("invalid_slots")
    for s in slots:
        if not isinstance(s, str) or s not in KNOWN_MEDIA_SLOTS:
            raise ContractError("unknown_media_slot")
        if s not in capability.user_fields:
            raise ContractError("unknown_media_slot")

    frames = [s for s in slots if s in FRAME_SLOTS]
    refs = [s for s in slots if s in REF_IMAGE_SLOTS]

    if frames and refs:
        raise ContractError("mode_media_conflict")

    if "last_frame" in frames and "first_frame" not in frames:
        raise ContractError("first_frame_required")

    if refs:
        derived_mode = "ref2va"
    elif frames:
        derived_mode = "fl2va"
    else:
        derived_mode = "t2v"

    if derived_mode not in capability.available_modes:
        raise ContractError("mode_unavailable")

    return derived_mode


def validate_create(body: object, capability: ModelCapability) -> dict:
    """Accept and validate public create request body against capability.

    Returns an independent validated dictionary without mutating caller data.
    """
    if not isinstance(body, dict):
        raise ContractError("invalid_body")

    allowed_keys = (
        SUPPORTED_ENVELOPE_FIELDS | set(capability.user_fields)
    ) & SUPPORTED_CLIENT_FIELDS
    for k in body:
        if not isinstance(k, str) or k not in allowed_keys:
            raise ContractError("unexpected_field")

    if body.get("op") != "create":
        raise ContractError("invalid_op")

    if body.get("mode") != "background":
        raise ContractError("unsupported_interaction_mode")

    if body.get("model") != capability.model:
        raise ContractError("unsupported_model")

    raw_prompt = body.get("prompt")
    if type(raw_prompt) is not str or not raw_prompt.strip():
        raise ContractError("invalid_prompt")
    try:
        encoded_prompt = raw_prompt.encode("utf-8")
    except UnicodeError:
        raise ContractError("invalid_prompt") from None
    if len(encoded_prompt) > MAX_PROMPT_BYTES:
        raise ContractError("invalid_prompt")

    raw_duration = body.get("duration_seconds")
    if raw_duration is None:
        raise ContractError("missing_duration")
    if (
        type(raw_duration) is not int
        or isinstance(raw_duration, bool)
        or raw_duration <= 0
    ):
        raise ContractError("invalid_duration")
    if raw_duration not in capability.durations:
        raise ContractError("invalid_duration")

    if "seed" in body:
        raw_seed = body["seed"]
        if (
            type(raw_seed) is not int
            or isinstance(raw_seed, bool)
            or not (0 <= raw_seed < 2**64)
        ):
            raise ContractError("invalid_seed")

    raw_width = body.get("width")
    raw_height = body.get("height")
    if (raw_width is None) != (raw_height is None):
        raise ContractError("invalid_size")
    if raw_width is not None and raw_height is not None:
        if (
            type(raw_width) is not int
            or isinstance(raw_width, bool)
            or raw_width <= 0
            or type(raw_height) is not int
            or isinstance(raw_height, bool)
            or raw_height <= 0
        ):
            raise ContractError("invalid_size")
        if (raw_width, raw_height) not in capability.sizes:
            raise ContractError("invalid_size")

    attached_slots: set[str] = set()
    for slot in KNOWN_MEDIA_SLOTS:
        if slot in body and body[slot] is not None:
            slot_val = body[slot]
            if (
                type(slot_val) is not dict
                or set(slot_val.keys()) != {"upload_id"}
                or type(slot_val.get("upload_id")) is not str
                or not slot_val["upload_id"].strip()
            ):
                raise ContractError("inline_media_unsupported")
            attached_slots.add(slot)

    validate_media_slots(attached_slots, capability)

    validated: dict[str, Any] = {
        "op": "create",
        "mode": "background",
        "model": capability.model,
        "prompt": raw_prompt,
        "duration_seconds": raw_duration,
    }
    if "seed" in body:
        validated["seed"] = body["seed"]
    if raw_width is not None and raw_height is not None:
        validated["width"] = raw_width
        validated["height"] = raw_height
    for slot in sorted(attached_slots):
        validated[slot] = {"upload_id": body[slot]["upload_id"]}

    return validated


def read_models(payload: object) -> tuple[ModelCapability, ...]:
    """Read authenticated /v1/models response and extract available ModelCapability rows."""
    if not isinstance(payload, dict):
        return ()

    raw_rows = payload.get("async_interactions")
    if not isinstance(raw_rows, (list, tuple)):
        return ()

    results: list[ModelCapability] = []

    for row in raw_rows:
        if not isinstance(row, dict):
            continue

        try:
            contract_version = row.get("interaction_contract_version")
            if contract_version != "v1":
                continue
            schema_id = row.get("schema_id")
            if (
                schema_id is not None
                and schema_id != "AsyncInteractionClientProjectionV1"
            ):
                continue

            if row.get("available") is not True:
                continue

            if row.get("execution_family") != "async_interaction":
                continue
            if row.get("modality") != "video":
                continue
            output_modalities = row.get("output_modalities")
            if (
                not isinstance(output_modalities, (list, tuple))
                or not output_modalities
                or any(
                    type(om) is not str or om not in KNOWN_OUTPUT_MODALITIES
                    for om in output_modalities
                )
                or len(output_modalities) != len(set(output_modalities))
            ):
                continue

            model_id = row.get("product_sku_id")
            if not isinstance(model_id, str) or not model_id.strip():
                continue

            profile = row.get("capability_profile")
            if not isinstance(profile, dict):
                continue
            prof_schema = profile.get("schema_id")
            if (
                prof_schema is not None
                and prof_schema != "AsyncInteractionCapabilityProfileV1"
            ):
                continue
            if profile.get("modality") != "video":
                continue

            raw_durations = profile.get("duration_seconds")
            if not isinstance(raw_durations, (list, tuple)) or not raw_durations:
                continue
            durations: list[int] = []
            valid_durations = True
            for d in raw_durations:
                if type(d) is not int or isinstance(d, bool) or d <= 0:
                    valid_durations = False
                    break
                durations.append(d)
            if (
                not valid_durations
                or not durations
                or len(durations) != len(set(durations))
            ):
                continue

            raw_sizes = profile.get("sizes")
            if not isinstance(raw_sizes, (list, tuple)) or not raw_sizes:
                continue
            sizes: list[tuple[int, int]] = []
            valid_sizes = True
            for s in raw_sizes:
                if not isinstance(s, (list, tuple)) or len(s) != 2:
                    valid_sizes = False
                    break
                w, h = s
                if (
                    type(w) is not int
                    or isinstance(w, bool)
                    or w <= 0
                    or type(h) is not int
                    or isinstance(h, bool)
                    or h <= 0
                ):
                    valid_sizes = False
                    break
                sizes.append((w, h))
            if not valid_sizes or not sizes or len(sizes) != len(set(sizes)):
                continue

            raw_user_fields = profile.get("user_fields")
            if not isinstance(raw_user_fields, (list, tuple)) or not raw_user_fields:
                continue
            user_fields: list[str] = []
            valid_user_fields = True
            for uf in raw_user_fields:
                if not isinstance(uf, str) or not uf.strip():
                    valid_user_fields = False
                    break
                user_fields.append(uf)
            if (
                not valid_user_fields
                or not user_fields
                or len(user_fields) != len(set(user_fields))
            ):
                continue

            user_field_set = set(user_fields)
            if not MANDATORY_USER_FIELDS <= user_field_set:
                continue

            raw_profile_modes = profile.get("modes")
            if (
                not isinstance(raw_profile_modes, (list, tuple))
                or not raw_profile_modes
            ):
                continue
            if any(
                type(pm) is not str or pm not in KNOWN_MODES for pm in raw_profile_modes
            ):
                continue
            if len(raw_profile_modes) != len(set(raw_profile_modes)):
                continue
            profile_modes = set(raw_profile_modes)
            if "t2v" not in profile_modes:
                continue

            raw_avail_modes = row.get("available_modes")
            if raw_avail_modes is None:
                # Explicit missing available_modes fallback to t2v per current website/spec
                candidate_modes = ("t2v",)
            elif isinstance(raw_avail_modes, (list, tuple)):
                if not raw_avail_modes:
                    continue
                if any(type(m) is not str for m in raw_avail_modes):
                    continue
                if raw_avail_modes[0] != "t2v":
                    continue
                if list(raw_avail_modes) != [
                    m for m in KNOWN_MODES if m in raw_avail_modes
                ]:
                    continue
                if not set(raw_avail_modes) <= profile_modes:
                    continue
                # Validate whole published list rather than filtering/reordering invalid entries
                if "fl2va" in raw_avail_modes and "first_frame" not in user_field_set:
                    continue
                if "ref2va" in raw_avail_modes and not any(
                    r in user_field_set for r in REF_IMAGE_SLOTS
                ):
                    continue
                candidate_modes = tuple(raw_avail_modes)
            else:
                continue

            results.append(
                ModelCapability(
                    model=model_id,
                    available_modes=candidate_modes,
                    durations=tuple(durations),
                    sizes=tuple(sizes),
                    user_fields=tuple(user_fields),
                    contract_version=contract_version,
                )
            )
        except (TypeError, ValueError, AttributeError, KeyError, IndexError):
            continue

    return tuple(results)


def read_result(payload: object) -> DownloadSpec | None:
    """Read deliverable DownloadSpec from an interaction payload, or None if not ready."""
    if not isinstance(payload, dict):
        raise ContractError("invalid_payload")

    state = payload.get("state")
    if state != "succeeded":
        return None

    if "result" not in payload or payload["result"] is None:
        return None

    res = payload["result"]
    if not isinstance(res, dict):
        raise ContractError("invalid_result_shape")

    if res.get("kind") != "video":
        raise ContractError("invalid_result_kind")

    if "download_url" not in res:
        raise ContractError("invalid_result_url")
    raw_url = res["download_url"]
    if type(raw_url) is not str or not raw_url.strip():
        raise ContractError("invalid_result_url")

    # Reject ASCII control characters (0..31), space (32), and DEL (127)
    if any(ord(c) <= 32 or ord(c) == 127 for c in raw_url):
        raise ContractError("invalid_result_url")

    try:
        parsed = urllib.parse.urlsplit(raw_url)
        _ = parsed.port
    except (ValueError, AttributeError):
        raise ContractError("invalid_result_url") from None

    if parsed.scheme != "https":
        raise ContractError("invalid_result_url")
    if not parsed.netloc or not parsed.hostname:
        raise ContractError("invalid_result_url")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise ContractError("invalid_result_url")

    raw_byte_count = res.get("byte_count")
    if (
        type(raw_byte_count) is not int
        or isinstance(raw_byte_count, bool)
        or raw_byte_count <= 0
        or raw_byte_count > MAX_DOWNLOAD_RESULT_BYTES
    ):
        raise ContractError("invalid_result_byte_count")

    raw_sha256 = res.get("sha256")
    if type(raw_sha256) is not str or SHA256_HEX_RE.fullmatch(raw_sha256) is None:
        raise ContractError("invalid_result_sha256")

    raw_mime = res.get("mime_type")
    if type(raw_mime) is not str or raw_mime != "video/mp4":
        raise ContractError("invalid_result_mime_type")

    return DownloadSpec(
        url=raw_url,
        byte_count=raw_byte_count,
        sha256=raw_sha256,
        mime_type=raw_mime,
    )
