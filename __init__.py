"""Inferway ComfyUI extension entrypoint."""

from __future__ import annotations

import logging
import os
from typing import Any

_logger = logging.getLogger(__name__)


async def comfy_entrypoint() -> Any:
    from aiohttp import web
    from comfy_api.latest import ComfyAPI_latest, ComfyExtension, io
    from comfy_execution.cache_provider import (
        CacheContext,
        CacheProvider,
        CacheValue,
    )

    from .inferway_comfy.nodes import (
        InferwayH3Cancel,
        InferwayH3Generate,
        InferwayH3Resume,
    )
    from .inferway_comfy.runtime import clear_prompt_fingerprints, get_registry

    class InferwayCacheProvider(CacheProvider):
        """External cache provider used only for prompt lifecycle cleanup."""

        async def on_lookup(self, context: CacheContext) -> CacheValue | None:
            return None

        async def on_store(self, context: CacheContext, value: CacheValue) -> None:
            pass

        def should_cache(
            self, context: CacheContext, value: CacheValue | None = None
        ) -> bool:
            return False

        def on_prompt_end(self, prompt_id: str) -> None:
            errors: list[Exception] = []
            try:
                get_registry().close_prompt(prompt_id)
            except Exception as e:  # noqa: BLE001
                errors.append(e)
                _logger.warning(
                    "Failed to close prompt %s in registry: %s", prompt_id, e
                )
            finally:
                try:
                    clear_prompt_fingerprints(prompt_id)
                except Exception as e:  # noqa: BLE001
                    errors.append(e)
                    _logger.warning(
                        "Failed to clear fingerprints for prompt %s: %s",
                        prompt_id,
                        e,
                    )

            if errors:
                raise RuntimeError(f"prompt_end_cleanup_failed_{prompt_id}")

    def register_model_discovery_route() -> None:
        """Register local Comfy route for on-demand model discovery."""
        from server import PromptServer

        server_instance = getattr(PromptServer, "instance", None)
        if server_instance is None:
            return

        routes = getattr(server_instance, "routes", None)
        if routes is None:
            raise RuntimeError("prompt_server_routes_unavailable")

        route_path = "/inferway/models"
        for r in routes:
            if (
                getattr(r, "path", None) == route_path
                and getattr(r, "method", "").upper() == "GET"
            ):
                return

        from .inferway_comfy.client import create_client
        from .inferway_comfy.contracts import read_models
        from .inferway_comfy.credentials import ClientError, load_settings

        @routes.get(route_path)
        async def handle_get_models(request: web.Request) -> web.Response:
            try:
                settings = load_settings(os.environ)
            except (ClientError, Exception):  # noqa: BLE001
                return web.json_response([])

            client = create_client(settings)
            try:
                payload = await client.models()
                caps = read_models(payload)
                model_ids = [c.model for c in caps]
                return web.json_response(model_ids)
            except (ClientError, Exception):  # noqa: BLE001
                return web.json_response([])
            finally:
                await client._http.aclose()

    class InferwayExtension(ComfyExtension):
        """Inferway native extension for ComfyUI."""

        async def on_load(self) -> None:
            # Register uniquely named no-op cache provider for prompt lifecycle
            provider = InferwayCacheProvider()
            await ComfyAPI_latest.Caching().register_provider(provider)

            # Register local authenticated route for remote model discovery
            register_model_discovery_route()

        async def get_node_list(self) -> list[type[io.ComfyNode]]:
            return [
                InferwayH3Generate,
                InferwayH3Resume,
                InferwayH3Cancel,
            ]

    return InferwayExtension()
