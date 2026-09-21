"""Runtime execution coordinator for Inferway ComfyUI nodes."""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import io
import logging
import os
import re
import shutil
import ssl
import stat
import tempfile
import time
import urllib.parse
import weakref
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import av
import httpx
import numpy as np
import torch
from comfy_execution.utils import get_executing_context
from PIL import Image

from .client import create_client
from .contracts import (
    ContractError,
    DownloadSpec,
    read_models,
    read_result,
    validate_create,
    validate_media_slots,
)
from .credentials import ClientError, ClientSettings, load_settings
from .execution import (
    ExecutionConflict,
    ExecutionRecord,
    ExecutionRegistry,
    ExecutionSlot,
    WaitTimeoutError,
    wait_for_result,
)
from .media import MediaCache, download_result, upload_image

_logger = logging.getLogger(__name__)

VERIFIED_PRODUCTION_RESULT_ORIGIN = (
    "https://60a726db174bce17c89497b7184eb752.r2.cloudflarestorage.com"
)

INTERACTION_ID_RE = re.compile(r"^int_[0-9a-f]{32}$")


def validate_interaction_id(interaction_id: object) -> str:
    if not isinstance(interaction_id, str):
        raise ContractError("invalid_interaction_id")
    if not INTERACTION_ID_RE.fullmatch(interaction_id):
        raise ContractError("invalid_interaction_id")
    return interaction_id


_registry = ExecutionRegistry()
_cache: MediaCache | None = None
_cache_dir: Path | None = None
_slot_fingerprints: dict[ExecutionSlot, str] = {}


def get_registry() -> ExecutionRegistry:
    return _registry


def clear_prompt_fingerprints(prompt_id: str) -> None:
    slots_to_delete = [s for s in _slot_fingerprints if s.prompt_id == prompt_id]
    for s in slots_to_delete:
        del _slot_fingerprints[s]


def reset_runtime_state() -> None:
    """Reset registry, media cache, and slot fingerprints deterministically."""
    global _registry
    _slot_fingerprints.clear()
    _registry = ExecutionRegistry()
    _cleanup_cache_dir()


def _cleanup_cache_dir() -> None:
    global _cache_dir, _cache
    _cache = None
    d = _cache_dir
    _cache_dir = None
    if d is not None and d.exists():
        st = d.stat()
        if st.st_uid == os.getuid():
            shutil.rmtree(d)


def get_media_cache() -> MediaCache:
    global _cache, _cache_dir
    if _cache is None:
        try:
            import folder_paths

            base_tmp = Path(folder_paths.get_temp_directory())
            base_tmp.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise RuntimeError("comfy_temp_directory_unavailable") from e

        if not base_tmp.is_dir():
            raise RuntimeError("comfy_temp_directory_unavailable")

        target_dir = Path(
            tempfile.mkdtemp(prefix=f"inferway_proc_{os.getpid()}_", dir=base_tmp)
        )
        st = target_dir.lstat()
        if not stat.S_ISDIR(st.st_mode):
            raise RuntimeError("invalid_cache_directory")
        if st.st_uid != os.getuid():
            raise RuntimeError("unowned_cache_directory")
        # mkdtemp creates an owner-only directory. Verify that boundary instead
        # of broadening permissions if a restrictive umask or filesystem differs.
        if stat.S_IMODE(st.st_mode) != 0o700:
            # This newly created directory is empty; rmdir needs no read access.
            target_dir.rmdir()
            raise RuntimeError("invalid_cache_directory_permissions")
        try:
            cache = MediaCache(target_dir)
        except Exception:
            # Only remove the exact directory we created, never a replacement.
            try:
                current = target_dir.lstat()
                if (current.st_dev, current.st_ino) == (st.st_dev, st.st_ino):
                    shutil.rmtree(target_dir)
            except FileNotFoundError:
                pass
            except OSError:
                _logger.warning("Failed to remove rejected private cache directory")
            raise
        _cache_dir = target_dir
        _cache = cache
        atexit.register(_cleanup_cache_dir)
    return _cache


def get_allowed_download_origins(development_mode: bool) -> set[str]:
    origins = {VERIFIED_PRODUCTION_RESULT_ORIGIN}
    custom = os.environ.get("INFERWAY_COMFY_DOWNLOAD_ORIGINS")
    if custom:
        if not development_mode:
            raise ContractError("invalid_download_origin")
        for orig in custom.split(","):
            orig = orig.strip()
            if not orig:
                continue
            if any(ord(c) < 32 or ord(c) == 127 for c in orig):
                raise ContractError("invalid_download_origin")
            try:
                parsed = urllib.parse.urlsplit(orig)
                _ = parsed.port
            except (ValueError, Exception):  # noqa: BLE001
                raise ContractError("invalid_download_origin") from None
            if (
                parsed.scheme == "https"
                and parsed.hostname in ("127.0.0.1", "localhost", "::1")
                and not (
                    parsed.username
                    or parsed.password
                    or (parsed.path and parsed.path != "/")
                    or parsed.query
                    or parsed.fragment
                )
            ):
                origins.add(f"{parsed.scheme}://{parsed.netloc}")
            else:
                raise ContractError("invalid_download_origin")
    return origins


def create_download_client(development_mode: bool) -> httpx.AsyncClient:
    verify: ssl.SSLContext | bool = True
    if development_mode:
        ca_file = os.environ.get("INFERWAY_COMFY_CA_FILE")
        if ca_file:
            try:
                ctx = ssl.create_default_context(cafile=ca_file)
                verify = ctx
            except Exception:  # noqa: BLE001
                raise ContractError("ca_load_failed") from None
    return httpx.AsyncClient(
        verify=verify,
        follow_redirects=False,
        trust_env=False,
    )


def check_processing_interrupted() -> None:
    try:
        from comfy import model_management

        if hasattr(model_management, "throw_exception_if_processing_interrupted"):
            model_management.throw_exception_if_processing_interrupted()
    except (ImportError, Exception):  # noqa: BLE001, S110
        pass


async def interruptible_sleep(seconds: float) -> None:
    interval = 0.05
    remaining = seconds
    while remaining > 0:
        check_processing_interrupted()
        step = min(interval, remaining)
        await asyncio.sleep(step)
        remaining -= step
        check_processing_interrupted()


async def run_interruptible[T](coro: Awaitable[T]) -> T:
    task = asyncio.create_task(coro)
    try:
        while not task.done():
            check_processing_interrupted()
            done, _ = await asyncio.wait([task], timeout=0.05)
            if done:
                return await task
            check_processing_interrupted()
        return await task
    except BaseException:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                pass
        raise


def compute_content_fingerprint(
    model: str,
    prompt: str,
    duration_seconds: int,
    seed: int | None,
    width: int | None,
    height: int | None,
    image_bytes: dict[str, bytes],
) -> str:
    hasher = hashlib.sha256()
    parts = [
        model,
        prompt,
        str(duration_seconds),
        str(seed) if seed is not None else "",
        str(width) if width is not None else "",
        str(height) if height is not None else "",
    ]
    for p in parts:
        hasher.update(p.encode("utf-8"))
        hasher.update(b"\x00")
    for slot in sorted(image_bytes.keys()):
        hasher.update(slot.encode("utf-8"))
        hasher.update(hashlib.sha256(image_bytes[slot]).digest())
    return hasher.hexdigest()


async def download_and_wrap_video(
    spec: DownloadSpec,
    settings: ClientSettings,
    record: ExecutionRecord | None,
) -> Any:
    cache = get_media_cache()
    lease = cache.allocate(spec.byte_count)
    download_success = False
    try:
        dl_client = create_download_client(settings.development_mode)
        try:
            allowed_origins = get_allowed_download_origins(settings.development_mode)
            await run_interruptible(
                download_result(
                    dl_client,
                    spec=spec,
                    allowed_origins=allowed_origins,
                    destination=lease.path,
                    development_mode=settings.development_mode,
                )
            )
        finally:
            await dl_client.aclose()

        lease.mark_complete()

        try:
            with av.open(str(lease.path)) as container:
                if not container.streams.video:
                    raise ContractError("invalid_video_stream")
                v_stream = container.streams.video[0]
                if (v_stream.width or 0) <= 0 or (v_stream.height or 0) <= 0:
                    raise ContractError("invalid_video_dimensions")
                frame_found = False
                for frame in container.decode(v_stream):
                    if frame.width <= 0 or frame.height <= 0:
                        raise ContractError("invalid_video_dimensions")
                    frame_found = True
                    break
                if not frame_found:
                    raise ContractError("empty_video_stream")
        except ContractError:
            raise
        except Exception:  # noqa: BLE001
            raise ContractError("video_decode_failed") from None

        from comfy_api.latest import InputImpl

        video = InputImpl.VideoFromFile(str(lease.path))
        weakref.finalize(video, lease.release)
        if record is not None:
            record.result = video
            record.cache_token = str(lease.path)
        download_success = True
        return video
    finally:
        if not download_success:
            try:
                lease.release()
            except Exception as cleanup_err:  # noqa: BLE001
                _logger.warning(
                    "Failed to release lease during cleanup: %s", cleanup_err
                )


async def execute_generate(
    model: str = "inferway/minimax-h3-768p",
    prompt: str = "",
    duration_seconds: int = 5,
    resolution: str = "default",
    seed: str = "",
    first_frame: torch.Tensor | None = None,
    last_frame: torch.Tensor | None = None,
    ref_image_1: torch.Tensor | None = None,
    ref_image_2: torch.Tensor | None = None,
    ref_image_3: torch.Tensor | None = None,
    profile: str = "default",
    wait_timeout_seconds: int = 600,
    **extra: Any,
) -> tuple[Any, str, str]:
    if profile != "default":
        raise ContractError("invalid_profile")

    if (
        type(wait_timeout_seconds) is not int
        or isinstance(wait_timeout_seconds, bool)
        or not (1 <= wait_timeout_seconds <= 3600)
    ):
        raise ContractError("invalid_wait_timeout")

    ctx = get_executing_context()
    if ctx is None or not ctx.prompt_id or not ctx.node_id:
        raise ContractError("missing_execution_context")

    # Parse and validate seed
    seed_val: int | None = None
    if seed is not None and str(seed).strip() != "":
        s = str(seed).strip()
        if len(s) > 20 or not s.isdigit():
            raise ContractError("invalid_seed")
        try:
            seed_val = int(s)
        except (ValueError, OverflowError):
            raise ContractError("invalid_seed") from None
        if not (0 <= seed_val < 2**64):
            raise ContractError("invalid_seed")

    # Parse and validate resolution
    width_val: int | None = None
    height_val: int | None = None
    if resolution is None or not isinstance(resolution, str):
        raise ContractError("invalid_resolution")
    if resolution != "default":
        parts = resolution.split("x")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ContractError("invalid_resolution")
        width_val = int(parts[0])
        height_val = int(parts[1])
        if width_val <= 0 or height_val <= 0:
            raise ContractError("invalid_resolution")

    settings = load_settings(os.environ)
    slot = ExecutionSlot(
        origin=settings.api_origin,
        profile=settings.profile,
        prompt_id=ctx.prompt_id,
        node_id=str(ctx.node_id),
        list_index=ctx.list_index,
    )
    record, lock = get_registry().get_or_create(slot)

    async with lock:
        # Validate and serialize IMAGE tensors
        images: dict[str, torch.Tensor | None] = {
            "first_frame": first_frame,
            "last_frame": last_frame,
            "ref_image_1": ref_image_1,
            "ref_image_2": ref_image_2,
            "ref_image_3": ref_image_3,
        }
        image_bytes: dict[str, bytes] = {}
        for sname, img_tensor in images.items():
            if img_tensor is not None:
                if (
                    not isinstance(img_tensor, torch.Tensor)
                    or img_tensor.ndim != 4
                    or img_tensor.shape[0] != 1
                ):
                    raise ContractError("invalid_image_batch")
                if not torch.isfinite(img_tensor).all():
                    raise ContractError("invalid_image_values")
                if (img_tensor < 0.0).any() or (img_tensor > 1.0).any():
                    raise ContractError("invalid_image_values")
                if img_tensor.shape[3] not in (1, 3, 4):
                    raise ContractError("invalid_image_channels")

                t = img_tensor[0]
                arr = (t.cpu().numpy() * 255.0).round().astype(np.uint8)
                if arr.shape[2] == 1:
                    pil_img = Image.fromarray(arr.squeeze(2), mode="L")
                elif arr.shape[2] == 3:
                    pil_img = Image.fromarray(arr, mode="RGB")
                elif arr.shape[2] == 4:
                    pil_img = Image.fromarray(arr, mode="RGBA")
                else:
                    raise ContractError("invalid_image_channels")

                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                png_data = buf.getvalue()
                if len(png_data) > 10 * 1024 * 1024:
                    raise ContractError("image_oversized")
                image_bytes[sname] = png_data

        # Slot relationship validation before upload
        attached_slots = set(image_bytes.keys())
        frames = [s for s in attached_slots if s in ("first_frame", "last_frame")]
        refs = [s for s in attached_slots if s.startswith("ref_image")]
        if frames and refs:
            raise ContractError("mode_media_conflict")
        if "last_frame" in frames and "first_frame" not in frames:
            raise ContractError("first_frame_required")
        if "ref_image_2" in refs and "ref_image_1" not in refs:
            raise ContractError("ref_image_gap")
        if "ref_image_3" in refs and (
            "ref_image_1" not in refs or "ref_image_2" not in refs
        ):
            raise ContractError("ref_image_gap")

        fingerprint = compute_content_fingerprint(
            model=model,
            prompt=prompt,
            duration_seconds=duration_seconds,
            seed=seed_val,
            width=width_val,
            height=height_val,
            image_bytes=image_bytes,
        )

        existing_fp = _slot_fingerprints.get(slot)
        if existing_fp is not None and existing_fp != fingerprint:
            raise ExecutionConflict("changed_intent_same_slot")
        if record.fingerprint is not None and record.fingerprint != fingerprint:
            raise ExecutionConflict("changed_intent_same_slot")

        # Pin fingerprint in runtime map BEFORE first upload
        _slot_fingerprints[slot] = fingerprint

        client = create_client(settings)
        try:
            # Model capability preflight
            models_payload = await run_interruptible(client.models())
            caps = read_models(models_payload)
            cap = next((c for c in caps if c.model == model), None)
            if cap is None:
                raise ContractError("unknown_model")

            # Validate media slots against capability before upload
            validate_media_slots(attached_slots, cap)

            # Preflight validation with placeholder references
            preflight_body: dict[str, Any] = {
                "op": "create",
                "mode": "background",
                "model": model,
                "prompt": prompt,
                "duration_seconds": duration_seconds,
            }
            if seed_val is not None:
                preflight_body["seed"] = seed_val
            if width_val is not None and height_val is not None:
                preflight_body["width"] = width_val
                preflight_body["height"] = height_val
            for sname in image_bytes:
                preflight_body[sname] = {"upload_id": "iup_placeholder.sig"}

            validate_create(preflight_body, cap)

            # Upload images wrapped in run_interruptible
            for sname, raw_bytes in sorted(image_bytes.items()):
                if sname in record.uploaded_ids:
                    continue
                purpose = "ref_image" if sname.startswith("ref_image") else sname
                ticket = await run_interruptible(
                    client.prepare_upload(
                        purpose=purpose,
                        content_type="image/png",
                        content_length=len(raw_bytes),
                    )
                )
                upload_id = ticket.get("upload_id")
                upload_url = ticket.get("upload_url", "")
                if not upload_id or not upload_url.endswith(upload_id):
                    raise ContractError("invalid_upload_ticket")

                await run_interruptible(
                    upload_image(
                        client._http,
                        api_origin=settings.api_origin,
                        upload_path=upload_url,
                        content=raw_bytes,
                        content_type="image/png",
                        declared_content_length=len(raw_bytes),
                        development_mode=settings.development_mode,
                    )
                )
                record.set_uploaded_id(sname, upload_id)

            # Build and freeze final validated create body
            final_body: dict[str, Any] = {
                "op": "create",
                "mode": "background",
                "model": model,
                "prompt": prompt,
                "duration_seconds": duration_seconds,
            }
            if seed_val is not None:
                final_body["seed"] = seed_val
            if width_val is not None and height_val is not None:
                final_body["width"] = width_val
                final_body["height"] = height_val
            for sname in sorted(image_bytes.keys()):
                final_body[sname] = {"upload_id": record.uploaded_ids[sname]}

            validated_final = validate_create(final_body, cap)
            record.bind(fingerprint, validated_final)

            # Create if needed
            if record.interaction_id is None:
                create_resp = await run_interruptible(
                    client.create(record.body, idempotency_key=record.key)
                )
                raw_id = create_resp.get("id")
                record.interaction_id = validate_interaction_id(raw_id)
                _logger.info(
                    "Inferway node %s interaction %s state %s",
                    slot.node_id,
                    record.interaction_id,
                    create_resp.get("state"),
                )

            # Poll until completion or deadline
            deadline = time.monotonic() + wait_timeout_seconds
            try:
                interaction = await run_interruptible(
                    wait_for_result(
                        client,
                        record.interaction_id,
                        deadline=deadline,
                        clock=time.monotonic,
                        sleep=interruptible_sleep,
                    )
                )
            except WaitTimeoutError:
                raise ContractError(f"wait_timeout_{record.interaction_id}") from None
            except ClientError as e:
                raise ContractError(f"{e.code}_{record.interaction_id}") from None

            resp_id = interaction.get("id")
            if resp_id is not None and resp_id != record.interaction_id:
                raise ContractError(f"interaction_mismatch_{record.interaction_id}")

            state = interaction.get("state")
            if state in ("failed", "cancelled"):
                raise ContractError(f"interaction_{state}_{record.interaction_id}")

            if state != "succeeded":
                raise ContractError(f"interaction_unknown_{record.interaction_id}")

            try:
                spec = read_result(interaction)
            except ContractError as e:
                raise ContractError(f"{e}_{record.interaction_id}") from None

            if spec is None:
                raise ContractError(f"delivery_unavailable_{record.interaction_id}")

            try:
                video = await download_and_wrap_video(spec, settings, record)
                return video, record.interaction_id, "succeeded"
            except ContractError as e:
                err_str = str(e)
                if record.interaction_id not in err_str:
                    err_str = f"{err_str}_{record.interaction_id}"
                raise ContractError(err_str) from None
        finally:
            await client._http.aclose()


async def execute_resume(
    interaction_id: str = "",
    profile: str = "default",
    wait_timeout_seconds: int = 600,
    **extra: Any,
) -> tuple[Any, str, str]:
    if profile != "default":
        raise ContractError("invalid_profile")

    if (
        type(wait_timeout_seconds) is not int
        or isinstance(wait_timeout_seconds, bool)
        or not (1 <= wait_timeout_seconds <= 3600)
    ):
        raise ContractError("invalid_wait_timeout")

    interaction_id = validate_interaction_id(interaction_id)

    settings = load_settings(os.environ)
    client = create_client(settings)
    try:
        deadline = time.monotonic() + wait_timeout_seconds
        try:
            interaction = await run_interruptible(
                wait_for_result(
                    client,
                    interaction_id,
                    deadline=deadline,
                    clock=time.monotonic,
                    sleep=interruptible_sleep,
                )
            )
        except WaitTimeoutError:
            raise ContractError(f"wait_timeout_{interaction_id}") from None
        except ClientError as e:
            raise ContractError(f"{e.code}_{interaction_id}") from None

        resp_id = interaction.get("id")
        if resp_id is not None and resp_id != interaction_id:
            raise ContractError(f"interaction_mismatch_{interaction_id}")

        state = interaction.get("state")
        if state in ("failed", "cancelled"):
            raise ContractError(f"interaction_{state}_{interaction_id}")
        if state != "succeeded":
            raise ContractError(f"interaction_unknown_{interaction_id}")

        try:
            spec = read_result(interaction)
        except ContractError as e:
            raise ContractError(f"{e}_{interaction_id}") from None

        if spec is None:
            raise ContractError(f"delivery_unavailable_{interaction_id}")

        try:
            video = await download_and_wrap_video(spec, settings, None)
            return video, interaction_id, "succeeded"
        except ContractError as e:
            err_str = str(e)
            if interaction_id not in err_str:
                err_str = f"{err_str}_{interaction_id}"
            raise ContractError(err_str) from None
    finally:
        await client._http.aclose()


async def execute_cancel(
    interaction_id: str = "",
    profile: str = "default",
    **extra: Any,
) -> tuple[str, str]:
    if profile != "default":
        raise ContractError("invalid_profile")

    interaction_id = validate_interaction_id(interaction_id)

    settings = load_settings(os.environ)
    client = create_client(settings)
    try:
        resp = await run_interruptible(client.cancel(interaction_id))
        resp_id = resp.get("id")
        if resp_id is not None and resp_id != interaction_id:
            raise ContractError(f"interaction_mismatch_{interaction_id}")
        cancel_info = resp.get("cancel", {})
        outcome = cancel_info.get("outcome", "unknown")
        charged = cancel_info.get("charged", False)
        status = f"outcome={outcome}, charged={charged}"
        return interaction_id, status
    finally:
        await client._http.aclose()
