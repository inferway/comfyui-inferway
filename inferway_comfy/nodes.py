"""Inferway native ComfyUI nodes."""

from __future__ import annotations

from typing import Any

import torch
from comfy_api.latest import io

from .credential_store import profile_choices
from .runtime import execute_cancel, execute_generate, execute_resume


def _node_id(cls: Any) -> str | None:
    """The executing node's id from the v3 hidden inputs, else None.

    ComfyUI fills ``cls.hidden`` on the per-execution class clone; asking for
    ``Hidden.unique_id`` in the schema is what makes it non-None.
    """
    hidden = getattr(cls, "hidden", None)
    unique_id = getattr(hidden, "unique_id", None)
    return str(unique_id) if unique_id is not None else None


class InferwayH3Generate(io.ComfyNode):
    """Generate video using Inferway API (legacy string seed widget)."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="InferwayH3Generate",
            display_name="Inferway H3 Generate (legacy)",
            category="Inferway",
            description="Generate video using Inferway models",
            is_deprecated=True,
            hidden=[io.Hidden.unique_id],
            inputs=[
                io.Combo.Input(
                    "model",
                    options=["inferway/minimax-h3-768p"],
                    default="inferway/minimax-h3-768p",
                    remote=io.RemoteOptions(
                        route="/inferway/models",
                        refresh_button=True,
                        control_after_refresh="first",
                        timeout=10000,
                        max_retries=2,
                    ),
                    tooltip="Model identifier",
                ),
                io.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="Generation prompt text",
                ),
                io.Int.Input(
                    "duration_seconds",
                    default=5,
                    min=5,
                    max=10,
                    tooltip=(
                        "Video length, 5-10 seconds. Billed per second; "
                        "prices: https://inferway.ai/pricing"
                    ),
                ),
                io.Combo.Input(
                    "resolution",
                    options=["default", "1344x768", "768x1344"],
                    default="default",
                    tooltip=(
                        "default is landscape 1344x768; or pick 1344x768 "
                        "(landscape) or 768x1344 (portrait)"
                    ),
                ),
                io.String.Input(
                    "seed",
                    default="",
                    optional=True,
                    tooltip="Optional decimal unsigned 64-bit integer seed",
                ),
                io.Image.Input(
                    "first_frame", optional=True, tooltip="First frame image"
                ),
                io.Image.Input("last_frame", optional=True, tooltip="Last frame image"),
                io.Image.Input(
                    "ref_image_1", optional=True, tooltip="Reference image 1"
                ),
                io.Image.Input(
                    "ref_image_2", optional=True, tooltip="Reference image 2"
                ),
                io.Image.Input(
                    "ref_image_3", optional=True, tooltip="Reference image 3"
                ),
                io.Combo.Input(
                    "profile",
                    options=profile_choices(),
                    default="default",
                    tooltip=(
                        "API key profile. Add or switch keys: "
                        "Settings → Inferway, or the Inferway menu."
                    ),
                ),
                io.Int.Input(
                    "wait_timeout_seconds",
                    default=0,
                    min=0,
                    max=7200,
                    tooltip=(
                        "Local wait limit in seconds. 0 waits automatically (up "
                        "to 7200s); 60-7200 sets an explicit limit. Reaching it "
                        "never cancels the remote task."
                    ),
                ),
            ],
            outputs=[
                io.Video.Output("video", tooltip="Generated video stream"),
                io.String.Output("interaction_id", tooltip="Inferway interaction ID"),
                io.String.Output("status", tooltip="Safe status text"),
            ],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs: Any) -> Any:
        return float("nan")

    @classmethod
    async def execute(
        cls,
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
        wait_timeout_seconds: int = 0,
        **kwargs: Any,
    ) -> io.NodeOutput:
        video, interaction_id, status = await execute_generate(
            model=model,
            prompt=prompt,
            duration_seconds=duration_seconds,
            resolution=resolution,
            seed=seed,
            first_frame=first_frame,
            last_frame=last_frame,
            ref_image_1=ref_image_1,
            ref_image_2=ref_image_2,
            ref_image_3=ref_image_3,
            profile=profile,
            wait_timeout_seconds=wait_timeout_seconds,
            node_id=_node_id(cls),
            # 0.1.3 and earlier saved 1-59 s here: clamp instead of failing them.
            legacy=True,
            **kwargs,
        )
        return io.NodeOutput(video, interaction_id, status)


class InferwayH3GenerateV2(io.ComfyNode):
    """Generate video using Inferway API with the native integer seed widget."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="InferwayH3GenerateV2",
            display_name="Inferway H3 Generate",
            category="Inferway",
            description="Generate video using Inferway models",
            hidden=[io.Hidden.unique_id],
            inputs=[
                io.Combo.Input(
                    "model",
                    options=["inferway/minimax-h3-768p"],
                    default="inferway/minimax-h3-768p",
                    remote=io.RemoteOptions(
                        route="/inferway/models",
                        refresh_button=True,
                        control_after_refresh="first",
                        timeout=10000,
                        max_retries=2,
                    ),
                    tooltip="Model identifier",
                ),
                io.String.Input(
                    "prompt",
                    multiline=True,
                    default="",
                    tooltip="Generation prompt text",
                ),
                io.Int.Input(
                    "duration_seconds",
                    default=5,
                    min=5,
                    max=10,
                    tooltip=(
                        "Video length, 5-10 seconds. Billed per second; "
                        "prices: https://inferway.ai/pricing"
                    ),
                ),
                io.Combo.Input(
                    "resolution",
                    options=["default", "1344x768", "768x1344"],
                    default="default",
                    tooltip=(
                        "default is landscape 1344x768; or pick 1344x768 "
                        "(landscape) or 768x1344 (portrait)"
                    ),
                ),
                io.Int.Input(
                    "seed",
                    default=0,
                    min=0,
                    max=18446744073709551615,
                    optional=True,
                    control_after_generate=io.ControlAfterGenerate.randomize,
                    tooltip=(
                        "Seed sent with the order (0 to 18446744073709551615). "
                        "Every run of this node places a new paid order whatever "
                        "the seed is; randomize gives each paid run a different "
                        "video. 每次运行都会新下一单并扣费，与 seed 无关。"
                    ),
                ),
                io.Image.Input(
                    "first_frame", optional=True, tooltip="First frame image"
                ),
                io.Image.Input("last_frame", optional=True, tooltip="Last frame image"),
                io.Image.Input(
                    "ref_image_1", optional=True, tooltip="Reference image 1"
                ),
                io.Image.Input(
                    "ref_image_2", optional=True, tooltip="Reference image 2"
                ),
                io.Image.Input(
                    "ref_image_3", optional=True, tooltip="Reference image 3"
                ),
                io.Combo.Input(
                    "profile",
                    options=profile_choices(),
                    default="default",
                    tooltip=(
                        "API key profile. Add or switch keys: "
                        "Settings → Inferway, or the Inferway menu."
                    ),
                ),
                io.Int.Input(
                    "wait_timeout_seconds",
                    default=0,
                    min=0,
                    max=7200,
                    tooltip=(
                        "Local wait limit in seconds. 0 waits automatically (up "
                        "to 7200s); 60-7200 sets an explicit limit. Reaching it "
                        "never cancels the remote task."
                    ),
                ),
            ],
            outputs=[
                io.Video.Output("video", tooltip="Generated video stream"),
                io.String.Output("interaction_id", tooltip="Inferway interaction ID"),
                io.String.Output("status", tooltip="Safe status text"),
            ],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs: Any) -> Any:
        return float("nan")

    @classmethod
    async def execute(
        cls,
        model: str = "inferway/minimax-h3-768p",
        prompt: str = "",
        duration_seconds: int = 5,
        resolution: str = "default",
        seed: int = 0,
        first_frame: torch.Tensor | None = None,
        last_frame: torch.Tensor | None = None,
        ref_image_1: torch.Tensor | None = None,
        ref_image_2: torch.Tensor | None = None,
        ref_image_3: torch.Tensor | None = None,
        profile: str = "default",
        wait_timeout_seconds: int = 0,
        **kwargs: Any,
    ) -> io.NodeOutput:
        # The INT widget always carries a value, so V2 has no "blank means no
        # seed" mode: every V2 order pins a seed.
        video, interaction_id, status = await execute_generate(
            model=model,
            prompt=prompt,
            duration_seconds=duration_seconds,
            resolution=resolution,
            seed=str(seed),
            first_frame=first_frame,
            last_frame=last_frame,
            ref_image_1=ref_image_1,
            ref_image_2=ref_image_2,
            ref_image_3=ref_image_3,
            profile=profile,
            wait_timeout_seconds=wait_timeout_seconds,
            node_id=_node_id(cls),
            # V2 never shipped with 1-59 s waits, so the strict rule stands.
            legacy=False,
            **kwargs,
        )
        return io.NodeOutput(video, interaction_id, status)


class InferwayH3Resume(io.ComfyNode):
    """Resume waiting or downloading for an existing Inferway interaction."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="InferwayH3Resume",
            display_name="Inferway H3 Resume",
            category="Inferway",
            description="Resume waiting or downloading for an interaction",
            hidden=[io.Hidden.unique_id],
            inputs=[
                io.String.Input(
                    "interaction_id",
                    default="",
                    tooltip="Inferway interaction ID to resume",
                ),
                io.Combo.Input(
                    "profile",
                    options=profile_choices(),
                    default="default",
                    tooltip=(
                        "API key profile. Add or switch keys: "
                        "Settings → Inferway, or the Inferway menu."
                    ),
                ),
                io.Int.Input(
                    "wait_timeout_seconds",
                    default=0,
                    min=0,
                    max=7200,
                    tooltip=(
                        "Local wait limit in seconds. 0 waits automatically (up "
                        "to 7200s); 60-7200 sets an explicit limit. Reaching it "
                        "never cancels the remote task."
                    ),
                ),
            ],
            outputs=[
                io.Video.Output("video", tooltip="Resumed video stream"),
                io.String.Output("interaction_id", tooltip="Inferway interaction ID"),
                io.String.Output("status", tooltip="Safe status text"),
            ],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs: Any) -> Any:
        return float("nan")

    @classmethod
    async def execute(
        cls,
        interaction_id: str = "",
        profile: str = "default",
        wait_timeout_seconds: int = 0,
        **kwargs: Any,
    ) -> io.NodeOutput:
        video, interaction_id, status = await execute_resume(
            interaction_id=interaction_id,
            profile=profile,
            wait_timeout_seconds=wait_timeout_seconds,
            node_id=_node_id(cls),
            # Resume predates 0.1.4 and accepts the same 1-59 s legacy values.
            legacy=True,
            **kwargs,
        )
        return io.NodeOutput(video, interaction_id, status)


class InferwayH3Cancel(io.ComfyNode):
    """Cancel a remote Inferway interaction."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="InferwayH3Cancel",
            display_name="Inferway H3 Cancel",
            category="Inferway",
            description="Cancel a remote interaction",
            inputs=[
                io.String.Input(
                    "interaction_id",
                    default="",
                    tooltip="Inferway interaction ID to cancel",
                ),
                io.Combo.Input(
                    "profile",
                    options=profile_choices(),
                    default="default",
                    tooltip=(
                        "API key profile. Add or switch keys: "
                        "Settings → Inferway, or the Inferway menu."
                    ),
                ),
            ],
            outputs=[
                io.String.Output("interaction_id", tooltip="Inferway interaction ID"),
                io.String.Output("status", tooltip="Cancellation status and outcome"),
            ],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs: Any) -> Any:
        return float("nan")

    @classmethod
    async def execute(
        cls,
        interaction_id: str = "",
        profile: str = "default",
        **kwargs: Any,
    ) -> io.NodeOutput:
        interaction_id, status = await execute_cancel(
            interaction_id=interaction_id,
            profile=profile,
            **kwargs,
        )
        return io.NodeOutput(interaction_id, status)
