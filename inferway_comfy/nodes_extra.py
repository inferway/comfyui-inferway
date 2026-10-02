"""Extra Inferway ComfyUI nodes: recent-interaction history and MiMo prompt expansion.

Both nodes are thin wrappers in the style of ``nodes.py``: the schema lives in
``define_schema()``, the transport work is delegated to ``client.py``, and the
machine-local profile store stays the single source of API keys.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from comfy_api.latest import io

from .client import create_client
from .contracts import ContractError
from .credential_store import is_valid_profile_name, profile_choices
from .credentials import load_settings
from .messages import bilingual_errors

_logger = logging.getLogger(__name__)

#: ``all`` means "no state filter"; every other value is forwarded to op=list.
HISTORY_STATES: tuple[str, ...] = (
    "all",
    "succeeded",
    "failed",
    "queued",
    "running",
    "cancelled",
)
NO_HISTORY_LINE = "No interactions found."

#: MiMo is Inferway's own chat model; expansion is billed per token.
PROMPT_EXPAND_MODEL = "inferway/mimo-v2.6-flash"
PROMPT_EXPAND_MAX_TOKENS = 600
PROMPT_EXPAND_TEMPERATURE = 0.7
PROMPT_EXPAND_TRUNCATED_TEXT = (
    "提示词被截断：MiMo 的回复到了长度上限，结尾可能不完整。"
    "送去生成视频前先看一眼，或者把想法写短一点再扩写。 / "
    "Prompt truncated: MiMo hit its length limit, so the ending may be cut off. "
    "Check it before it reaches Generate, or shorten the idea and expand again."
)

PROMPT_EXPAND_SYSTEM_PROMPT = (
    "You rewrite a short idea into one paragraph of prompt text for AI video "
    "generation.\n"
    "- Output exactly one paragraph of prompt text that can be used as-is for "
    "video generation.\n"
    "- Describe the subject, the action, the scene, the camera movement, the "
    "lighting and the atmosphere.\n"
    "- Keep it under 120 words in English, or under 200 Chinese characters.\n"
    "- Match the language of the user's idea unless the user explicitly asks "
    "for another language.\n"
    "- No preamble, no explanation, no Markdown, no quotation marks around the "
    "prompt.\n"
    "- Never invent people's names or brands that the input does not mention."
)

#: Per-language framing for the user turn; ``auto`` adds no language demand.
PROMPT_EXPAND_LANGUAGES: tuple[str, ...] = ("auto", "zh", "en")
_USER_FRAMING = {
    "auto": "Expand this idea into a video prompt:\n\n{idea}",
    "zh": "把下面的想法扩写成中文视频提示词：\n\n{idea}",
    "en": "Expand the idea below into an English video prompt:\n\n{idea}",
}

# "YYYY-MM-DDTHH:MM[:SS[.fff]]Z" -- anything else is shown as it arrived.
_UTC_TO_THE_MINUTE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?Z$"
)


def build_prompt_expand_messages(idea: str, language: str) -> list[dict]:
    """Assemble the system + user turns for one expansion request."""
    if language not in _USER_FRAMING:
        raise ContractError("invalid_language")
    return [
        {"role": "system", "content": PROMPT_EXPAND_SYSTEM_PROMPT},
        {"role": "user", "content": _USER_FRAMING[language].format(idea=idea)},
    ]


def _as_text(value: object) -> str:
    """One display-safe cell: whitespace collapsed, control characters escaped."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return "-"
    text = " ".join(str(value).split())
    if not text:
        return "-"
    return "".join(
        char if ord(char) >= 32 and ord(char) != 127 else f"\\u{ord(char):04x}"
        for char in text
    )


def _created_at_text(value: object) -> str:
    """UTC timestamp truncated to the minute, as the history line shows it."""
    if not isinstance(value, str):
        return "-"
    text = value.strip()
    if not text:
        return "-"
    if _UTC_TO_THE_MINUTE.match(text):
        return text[:16] + "Z"
    return _as_text(text)


def _history_line(item: dict) -> str:
    request = item.get("request")
    request = request if isinstance(request, dict) else {}
    billing = item.get("billing")
    billing = billing if isinstance(billing, dict) else {}

    duration = request.get("duration_seconds")
    duration_text = (
        f"{duration}s"
        if isinstance(duration, (int, float)) and not isinstance(duration, bool)
        else "-"
    )

    amount = _as_text(billing.get("charged_amount"))
    currency = _as_text(billing.get("currency"))
    if amount == "-" or currency == "-":
        charge = amount
    else:
        charge = f"{amount} {currency}"

    available = item.get("result_available")
    if available is True:
        availability = "downloadable"
    elif available is False:
        availability = "expired"
    else:
        availability = "-"

    return " | ".join(
        (
            _created_at_text(item.get("created_at")),
            _as_text(item.get("state")),
            _as_text(request.get("mode")),
            duration_text,
            charge,
            availability,
            _as_text(item.get("id")),
            _as_text(request.get("prompt_excerpt")),
        )
    )


def format_history(items: object) -> tuple[str, str]:
    """Render one line per interaction plus the newest resumable success id.

    The second element is the first ``succeeded`` item whose result can still
    be downloaded; a succeeded row whose ``result_available`` is not true is
    skipped because Resume would have nothing to fetch.
    """
    lines: list[str] = []
    latest_succeeded_id = ""
    if isinstance(items, (list, tuple)):
        for entry in items:
            if not isinstance(entry, dict):
                continue
            lines.append(_history_line(entry))
            if (
                not latest_succeeded_id
                and entry.get("state") == "succeeded"
                and entry.get("result_available") is True
            ):
                candidate = entry.get("id")
                if isinstance(candidate, str) and candidate.strip():
                    latest_succeeded_id = candidate
    if not lines:
        return NO_HISTORY_LINE, ""
    return "\n".join(lines), latest_succeeded_id


class InferwayH3History(io.ComfyNode):
    """List this account's most recent Inferway interactions."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="InferwayH3History",
            display_name="Inferway H3 History",
            category="Inferway",
            description=(
                "List recent Inferway interactions for this account. Read-only "
                "op=list: it never issues a download link and never creates a task."
            ),
            inputs=[
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
                    "limit",
                    default=10,
                    min=1,
                    max=50,
                    tooltip="How many recent interactions to list (1-50)",
                ),
                io.Combo.Input(
                    "state",
                    options=list(HISTORY_STATES),
                    default="all",
                    tooltip="Filter by state; 'all' sends no state filter",
                ),
            ],
            outputs=[
                io.String.Output(
                    "history",
                    tooltip=(
                        "One line per interaction: created_at (UTC, to the "
                        "minute), state, mode, duration, charge, availability, "
                        "id, prompt excerpt"
                    ),
                ),
                io.String.Output(
                    "latest_succeeded_id",
                    tooltip=(
                        "Newest succeeded interaction with a downloadable "
                        "result; empty when there is none. Wire into Resume."
                    ),
                ),
            ],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs: Any) -> Any:
        # History is live data: a cached page would hide a task that finished
        # after the first queue, so every queue must refetch (NaN != NaN).
        return float("nan")

    @classmethod
    @bilingual_errors
    async def execute(
        cls,
        profile: str = "default",
        limit: int = 10,
        state: str = "all",
        **kwargs: Any,
    ) -> io.NodeOutput:
        if not is_valid_profile_name(profile):
            raise ContractError("invalid_profile")
        if type(limit) is not int or isinstance(limit, bool) or not (1 <= limit <= 50):
            raise ContractError("invalid_limit")
        if state not in HISTORY_STATES:
            raise ContractError("invalid_history_state")

        settings = load_settings(os.environ, profile=profile)
        client = create_client(settings)
        try:
            payload = await client.list(
                limit=limit, state=None if state == "all" else state
            )
            history, latest_succeeded_id = format_history(payload.get("items"))
            return io.NodeOutput(history, latest_succeeded_id)
        finally:
            await client._http.aclose()


def _node_id(cls: Any) -> str | None:
    """The executing node's id from the v3 hidden inputs, else None."""
    hidden = getattr(cls, "hidden", None)
    unique_id = getattr(hidden, "unique_id", None)
    return str(unique_id) if unique_id is not None else None


def _show_on_node(text: str, node_id: str | None) -> None:
    """Show one status line on the node; never raises.

    Kept local instead of importing runtime, which pulls in torch and av.
    """
    if node_id is None:
        return
    try:
        from server import PromptServer

        instance = getattr(PromptServer, "instance", None)
        if instance is not None:
            instance.send_progress_text(text, node_id)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("Inferway node text failed: %s", type(exc).__name__)


class InferwayPromptExpand(io.ComfyNode):
    """Expand a short idea into a video prompt with Inferway's MiMo model."""

    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="InferwayPromptExpand",
            display_name="Inferway Prompt Expand (MiMo)",
            category="Inferway",
            description=(
                "Expand a short idea into a ready-to-use video prompt with "
                f"{PROMPT_EXPAND_MODEL}. Billed per token from this profile's "
                "balance. Re-queuing identical inputs usually reuses ComfyUI's "
                "cache; a restart, --cache-none or cache eviction bills again."
            ),
            inputs=[
                io.Combo.Input(
                    "profile",
                    options=profile_choices(),
                    default="default",
                    tooltip=(
                        "API key profile whose balance pays for the MiMo tokens. "
                        "Add or switch keys: Settings → Inferway, or the "
                        "Inferway menu."
                    ),
                ),
                io.String.Input(
                    "idea",
                    multiline=True,
                    default="",
                    tooltip="Short idea to expand into a full video prompt",
                ),
                io.Combo.Input(
                    "language",
                    options=list(PROMPT_EXPAND_LANGUAGES),
                    default="auto",
                    tooltip="Prompt language; 'auto' answers in the idea's language",
                ),
            ],
            outputs=[
                io.String.Output(
                    "prompt", tooltip="Video prompt text ready for Generate"
                ),
            ],
            hidden=[io.Hidden.unique_id],
        )

    # No fingerprint_inputs override on purpose: with ComfyUI's default caching
    # the same idea expands once, so re-queuing does not bill the same tokens.

    @classmethod
    @bilingual_errors
    async def execute(
        cls,
        profile: str = "default",
        idea: str = "",
        language: str = "auto",
        **kwargs: Any,
    ) -> io.NodeOutput:
        if not isinstance(idea, str) or not idea.strip():
            raise ContractError("empty_idea")
        if not is_valid_profile_name(profile):
            raise ContractError("invalid_profile")

        messages = build_prompt_expand_messages(idea.strip(), language)

        settings = load_settings(os.environ, profile=profile)
        client = create_client(settings)
        try:
            prompt = await client.chat_completion(
                model=PROMPT_EXPAND_MODEL,
                messages=messages,
                max_tokens=PROMPT_EXPAND_MAX_TOKENS,
                temperature=PROMPT_EXPAND_TEMPERATURE,
            )
        finally:
            await client._http.aclose()

        if client.last_chat_finish_reason == "length":
            # The single output stays clean prompt text; the warning goes on
            # the node itself, where the user sees it before paying for video.
            _logger.warning(
                "prompt_expand_output_truncated model=%s", PROMPT_EXPAND_MODEL
            )
            _show_on_node(PROMPT_EXPAND_TRUNCATED_TEXT, _node_id(cls))
        return io.NodeOutput(prompt)
