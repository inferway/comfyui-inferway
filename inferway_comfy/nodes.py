"""Inferway native ComfyUI nodes."""

from __future__ import annotations

from typing import Any

import torch
from comfy_api.latest import io

from .runtime import execute_cancel, execute_generate, execute_resume


class InferwayH3Generate(io.ComfyNode):
    """Generate video using Inferway API."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="InferwayH3Generate",
            display_name="Inferway H3 Generate",
            category="Inferway",
            description="Generate video using Inferway models",
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
                    tooltip="Video duration in seconds",
                ),
                io.Combo.Input(
                    "resolution",
                    options=["default", "1344x768", "768x1344"],
                    default="default",
                    tooltip="Video resolution",
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
                    options=["default"],
                    default="default",
                    tooltip="Configuration profile",
                ),
                io.Int.Input(
                    "wait_timeout_seconds",
                    default=600,
                    min=1,
                    max=3600,
                    tooltip="Local timeout in seconds",
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
        wait_timeout_seconds: int = 600,
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
            inputs=[
                io.String.Input(
                    "interaction_id",
                    default="",
                    tooltip="Inferway interaction ID to resume",
                ),
                io.Combo.Input(
                    "profile",
                    options=["default"],
                    default="default",
                    tooltip="Configuration profile",
                ),
                io.Int.Input(
                    "wait_timeout_seconds",
                    default=600,
                    min=1,
                    max=3600,
                    tooltip="Local timeout in seconds",
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
        wait_timeout_seconds: int = 600,
        **kwargs: Any,
    ) -> io.NodeOutput:
        video, interaction_id, status = await execute_resume(
            interaction_id=interaction_id,
            profile=profile,
            wait_timeout_seconds=wait_timeout_seconds,
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
                    options=["default"],
                    default="default",
                    tooltip="Configuration profile",
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
