"""Customer-facing text for the Inferway ComfyUI nodes.

Two responsibilities, both pure functions and both free of any ComfyUI import:

* ``human_message(code)`` renders a closed machine code as
  ``中文一句\\nEnglish sentence\\n(code: <原始码>)`` so a customer can read the
  failure and support can still find the code.
* ``describe_status(payload)`` renders the queue/progress lines the node shows
  while it waits, plus the values for the ComfyUI progress bar.

Nothing in this module may ever contain an API key: only closed codes,
interaction ids and hand-written sentences are interpolated.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "BILLING_URL",
    "KEYS_URL",
    "StatusReport",
    "create_uncertain_message",
    "describe_status",
    "failure_message",
    "human_message",
    "interrupt_message",
    "is_closed_code",
    "order_placed_failure",
    "wait_timeout_message",
]

BILLING_URL = "https://inferway.ai/console/billing"
KEYS_URL = "https://inferway.ai/console/keys"

# A rendered message is never mistaken for a code again: codes are snake_case.
_CODE_RE = re.compile(r"^[a-z0-9_]+$")
# ``<code>_int_<32 hex>`` -- the shape runtime.py mints around a live order.
_SUFFIX_RE = re.compile(r"^(?P<head>[a-z0-9_]+)_(?P<iid>int_[0-9a-f]{32})$")

# --- messages that carry a link or an interaction id -------------------------

_KEY_ZH = (
    "还没有设置 API key（或 key 已失效）。在 ComfyUI 设置里的 Inferway 一项填写，"
    f"或到 {KEYS_URL} 新建"
)
_KEY_EN = (
    "No API key is set, or the key no longer works. Add it under Settings -> "
    f"Inferway in ComfyUI, or create one at {KEYS_URL}"
)

_BALANCE_ZH = f"余额不足，请充值后重试：{BILLING_URL}"
_BALANCE_EN = f"Insufficient balance, top up and try again: {BILLING_URL}"


def wait_timeout_message(interaction_id: str) -> str:
    """The wait ran out locally. The order itself is untouched."""
    zh = (
        f"等待超时：这个节点不再等了，但任务没有取消，还会继续排队、生成，"
        f"完成后照常扣费，不管你取不取回。请不要重新运行生成节点，用 Inferway "
        f"H3 Resume 节点填入任务 id {interaction_id} 取回。"
    )
    en = (
        f"Wait timed out: this node stopped waiting, but the task was not "
        f"cancelled. It keeps queuing and generating and is billed when it "
        f"finishes, whether or not you collect it. Do not run Generate again — "
        f"put the task id {interaction_id} into an Inferway H3 Resume node to "
        f"collect it."
    )
    return f"{zh}\n{en}\n(code: wait_timeout_{interaction_id})"


def interrupt_message(interaction_id: str) -> str:
    """ComfyUI's Stop button was pressed. Same promise as a local timeout."""
    zh = (
        f"已停止等待：任务没有取消，还会继续排队、生成，完成后照常扣费，"
        f"不管你取不取回。请不要重新运行生成节点，用 Inferway H3 Resume 节点"
        f"填入任务 id {interaction_id} 取回。"
    )
    en = (
        f"Stopped waiting: the task was not cancelled. It keeps queuing and "
        f"generating and is billed when it finishes, whether or not you collect "
        f"it. Do not run Generate again — put the task id {interaction_id} into "
        f"an Inferway H3 Resume node to collect it."
    )
    return f"{zh}\n{en}\n(code: interrupted_{interaction_id})"


def _joined(zh: str, en: str, zh_tail: str, en_tail: str) -> tuple[str, str]:
    """Append a sentence without doubling a full stop that is already there."""
    if not zh.endswith(("。", "！", "？", "…")):
        zh = f"{zh}。"
    if not en.endswith((".", "!", "?")):
        en = f"{en}."
    return f"{zh}{zh_tail}", f"{en}{en_tail}"


# A code's own "retry" clause (", please retry", "；请稍后重试", "; retry with
# Resume", "，请重新运行" ...). After the order exists that advice contradicts "do not run
# Generate again", so ``order_placed_failure`` keeps only the reason.
_ZH_RETRY_CLAUSE = re.compile(r"[，；][^，；。]*(?:重试|重新运行).*$")
_EN_RETRY_CLAUSE = re.compile(
    r"[;,]\s*[^;,]*\b(?:retry|try again|run the node again)\b.*$", re.IGNORECASE
)


def _without_retry_clause(zh: str, en: str) -> tuple[str, str]:
    return _ZH_RETRY_CLAUSE.sub("", zh) or zh, _EN_RETRY_CLAUSE.sub("", en) or en


def order_placed_failure(code: str, interaction_id: str) -> str:
    """A failure raised *after* the order exists.

    The order keeps running and is billed whatever this error says, so the text
    keeps the code's own sentence and then says exactly that: do not run
    Generate again, collect it with Resume.
    """
    zh, en = _without_retry_clause(*_MESSAGES.get(code, _FALLBACK))
    zh_out, en_out = _joined(
        zh,
        en,
        "订单已经提交，不会因为这个错误被取消，完成后照常扣费。"
        "请不要重新运行生成节点，用 Inferway H3 Resume 节点填入任务 id "
        f"{interaction_id} 取回。",
        " The order was already placed; this error does not cancel it and it "
        "is billed when the video finishes. Do not run Generate again — put the "
        f"task id {interaction_id} into an Inferway H3 Resume node to collect it.",
    )
    return f"{zh_out}\n{en_out}\n(code: {code}_{interaction_id})"


def create_uncertain_message(code: str) -> str:
    """A create that never got an answer: the order may already exist.

    There is no task id yet, so the only honest next step is looking before
    ordering again -- never "just rerun it", which can bill twice.
    """
    zh = (
        "下单结果不确定：请求可能已经到达服务端，订单也许已经建立。"
        "先用 Inferway H3 History 节点（或控制台）看看最近有没有这一单，"
        "再决定要不要重新运行，否则可能被扣两次钱。"
    )
    en = (
        "The order outcome is unknown: the request may have reached the "
        "service and the order may exist. Check with an Inferway H3 History "
        "node (or the console) before running Generate again, or you may be "
        "billed twice."
    )
    return f"{zh}\n{en}\n(code: {code})"


def is_closed_code(value: Any) -> bool:
    """True for a bare machine code, False for already-rendered customer text."""
    return isinstance(value, str) and bool(_CODE_RE.match(value))


def failure_message(
    state: str, interaction_id: str, charged: Any, server_message: Any
) -> str:
    detail = server_message if isinstance(server_message, str) else ""
    detail = detail.strip()
    if state == "cancelled":
        zh_head, en_head = "任务已取消", "The task was cancelled"
    else:
        zh_head, en_head = "任务失败", "The task failed"
    if detail:
        zh_head = f"{zh_head}：{detail}"
        en_head = f"{en_head}: {detail}"
    if charged is False:
        zh_head = f"{zh_head}（未扣费）"
        en_head = f"{en_head} (not charged)"
    code = f"interaction_{state}_{interaction_id}"
    return f"{zh_head}\n{en_head}\n(code: {code})"


# --- the closed code table --------------------------------------------------
# zh sentence, en sentence. Every code the plugin can raise appears here; an
# unlisted code still gets the fallback below rather than a raw machine string.

_MESSAGES: dict[str, tuple[str, str]] = {
    # Key and profile setup
    "missing_api_key": (_KEY_ZH, _KEY_EN),
    "invalid_api_key": (_KEY_ZH, _KEY_EN),
    "unauthorized": (_KEY_ZH, _KEY_EN),
    "account_restricted": (
        "账号已被限制使用，请联系支持",
        "This account is restricted, please contact support",
    ),
    # Money
    "payment_required": (_BALANCE_ZH, _BALANCE_EN),
    # Wait and interruption
    "wait_timeout": (
        "等待超时，本地停止等待",
        "The wait timed out and this node stopped waiting",
    ),
    "interrupted": (
        "已中断等待",
        "Waiting was interrupted",
    ),
    # Remote order states
    "interaction_failed": (
        "任务失败",
        "The task failed",
    ),
    "interaction_cancelled": (
        "任务已取消",
        "The task was cancelled",
    ),
    "interaction_unknown": (
        "任务状态异常，无法取回结果，请稍后用 Resume 重试",
        "The task reported an unexpected state; retry with the Resume node",
    ),
    "interaction_mismatch": (
        "服务端返回了别的任务，请重试",
        "The server answered with a different task, please retry",
    ),
    "delivery_unavailable": (
        "任务已完成但服务端没有可下载的成片，请稍后用 Resume 重试",
        "The task finished but no video is downloadable yet; retry with Resume",
    ),
    # Local request validation
    "invalid_wait_timeout": (
        "等待时长不合法：请填 0（自动等待）或 60 到 7200 之间的秒数",
        "Invalid wait timeout: use 0 (wait automatically) or 60 to 7200 seconds",
    ),
    "invalid_seed": (
        "seed 不合法：需要 0 到 18446744073709551615 之间的整数",
        "Invalid seed: expected an integer from 0 to 18446744073709551615",
    ),
    "invalid_prompt": (
        "提示词不合法：不能为空或过长",
        "Invalid prompt: it must not be empty or oversized",
    ),
    "missing_duration": (
        "缺少生成时长",
        "The generation duration is missing",
    ),
    "invalid_duration": (
        "生成时长不合法",
        "Invalid generation duration",
    ),
    "invalid_resolution": (
        "分辨率不合法：可选 default、1344x768、768x1344",
        "Invalid resolution: use default, 1344x768 or 768x1344",
    ),
    "invalid_profile": (
        "所选 key profile 不存在，请在 ComfyUI 设置里的 Inferway 一项新建",
        "That key profile does not exist; create it under Settings -> Inferway",
    ),
    "unsupported_profile": (
        "所选 key profile 名字不合法",
        "That key profile name is not allowed",
    ),
    "invalid_interaction_id": (
        "任务 id 格式不对，应形如 int_ 加 32 位十六进制",
        "Malformed task id: it looks like int_ followed by 32 hex characters",
    ),
    "missing_execution_context": (
        "没有获取到 ComfyUI 执行上下文，请在正常的工作流里运行本节点",
        "No ComfyUI execution context: run this node inside a workflow",
    ),
    "unknown_model": (
        "该模型不可用，请在下拉里重新选择模型",
        "That model is unavailable; pick another one from the dropdown",
    ),
    "unsupported_model": (
        "该模型不可用",
        "That model is not supported",
    ),
    "mode_unavailable": (
        "当前模型没有开放这种输入组合",
        "This model does not offer that input combination",
    ),
    "unsupported_interaction_mode": (
        "当前模型不支持这种生成方式",
        "That generation mode is not supported",
    ),
    "mode_media_conflict": (
        "首尾帧和参考图不能混用，请二选一",
        "First/last frame and reference images cannot be mixed; pick one",
    ),
    "first_frame_required": (
        "提供了末帧就必须同时提供首帧",
        "A last frame requires a first frame",
    ),
    "ref_image_gap": (
        "参考图必须按 1、2、3 的顺序连续提供",
        "Reference images must be supplied in order 1, 2, 3",
    ),
    # Image inputs
    "invalid_image_batch": (
        "图片输入必须是单张图片",
        "Image inputs must be a single image",
    ),
    "invalid_image_values": (
        "图片数值超出 0 到 1 的范围",
        "Image values are outside the 0..1 range",
    ),
    "invalid_image_channels": (
        "图片通道数不支持，需要 1、3 或 4 通道",
        "Unsupported image channel count: expected 1, 3 or 4",
    ),
    "image_oversized": (
        "图片太大，单张不得超过 10 MB，请缩小后重试",
        "The image is too large: 10 MB per image maximum",
    ),
    "invalid_slots": (
        "请求里的媒体字段不合法",
        "Malformed media fields in the request",
    ),
    "unknown_media_slot": (
        "请求了该模型不支持的图片位",
        "That image slot is not supported by this model",
    ),
    "inline_media_unsupported": (
        "图片必须先上传，不能内联提交",
        "Images must be uploaded first and cannot be inlined",
    ),
    "unexpected_field": (
        "请求里有不该出现的字段",
        "The request carried an unexpected field",
    ),
    "invalid_op": (
        "请求操作类型不合法",
        "Malformed request operation",
    ),
    "invalid_body": (
        "请求体不合法",
        "Malformed request body",
    ),
    # Transport and server errors
    "timeout": (
        "连接 Inferway 服务超时，请检查网络后重试",
        "Timed out talking to the Inferway service; check your network and retry",
    ),
    "network_error": (
        "连不上 Inferway 服务，请检查网络后重试",
        "Could not reach the Inferway service; check your network and retry",
    ),
    "server_error": (
        "Inferway 服务端出错，请稍后重试",
        "The Inferway service failed; try again shortly",
    ),
    "service_unavailable": (
        "Inferway 服务暂时不可用，请稍后重试",
        "The Inferway service is temporarily unavailable; try again shortly",
    ),
    "rate_limited": (
        "请求过于频繁，请稍后重试",
        "Too many requests; try again shortly",
    ),
    "forbidden": (
        "这个 key 没有访问权限",
        "This key is not allowed to do that",
    ),
    "not_found": (
        "资源不存在",
        "Not found",
    ),
    "conflict": (
        "请求冲突，请重试",
        "Conflicting request; please retry",
    ),
    "bad_request": (
        "请求不合法",
        "The request was rejected as invalid",
    ),
    "invalid_request": (
        "请求不合法",
        "The request was rejected as invalid",
    ),
    "client_error": (
        "请求被服务端拒绝",
        "The server rejected the request",
    ),
    "redirect_refused": (
        "服务端返回了跳转，出于安全考虑已拒绝",
        "The server answered with a redirect, which was refused for safety",
    ),
    "unauthorized_origin": (
        "当前 ComfyUI 的访问地址没有被授权使用这个 key",
        "This ComfyUI origin is not authorised to use that key",
    ),
    "invalid_origin": (
        "服务地址配置不正确",
        "The configured API origin is not valid",
    ),
    "invalid_environment": (
        "服务地址配置不正确",
        "The configured API environment is not valid",
    ),
    "invalid_settings": (
        "Inferway 设置读取失败，请重新保存一次 key",
        "The Inferway settings could not be read; save the key again",
    ),
    "invalid_timeout": (
        "超时设置不合法",
        "Invalid timeout setting",
    ),
    "invalid_json": (
        "服务端返回了无法解析的内容",
        "The server returned unreadable content",
    ),
    "invalid_response_shape": (
        "服务端返回的结构不符合预期",
        "The server response had an unexpected shape",
    ),
    "invalid_payload": (
        "服务端返回的数据不符合预期",
        "The server returned unexpected data",
    ),
    "malformed_response": (
        "服务端返回的数据不符合预期",
        "The server returned unexpected data",
    ),
    "unknown_state": (
        "服务端返回了未知的任务状态",
        "The server reported an unknown task state",
    ),
    "invalid_state": (
        "任务状态过滤参数不合法",
        "Invalid state filter",
    ),
    "invalid_limit": (
        "分页数量不合法",
        "Invalid page limit",
    ),
    "invalid_list_response": (
        "任务列表返回的数据不符合预期",
        "The task list response had an unexpected shape",
    ),
    "create_ambiguous": (
        (
            "下单结果不确定：请求可能已经到达服务端，订单也许已经建立。先用 "
            "Inferway H3 History 节点（或控制台）看看最近有没有这一单，再决定"
            "要不要重新运行，否则可能被扣两次钱"
        ),
        (
            "The order outcome is unknown: the request may have reached the service "
            "and the order may exist; check with an Inferway H3 History node (or the "
            "console) before running Generate again"
        ),
    ),
    "missing_idempotency_key": (
        "缺少幂等键",
        "The idempotency key is missing",
    ),
    "invalid_idempotency_key": (
        "幂等键不合法",
        "The idempotency key is malformed",
    ),
    "activation_disabled": (
        "账号尚未开通，请先在控制台开通",
        "This account is not activated yet; activate it in the console first",
    ),
    "missing_cancel_outcome": (
        "取消结果缺失",
        "The cancellation outcome is missing",
    ),
    "unknown_cancel_outcome": (
        "取消结果无法识别",
        "The cancellation outcome is unknown",
    ),
    "invalid_cancel_charged": (
        "取消结果里的计费字段无法识别",
        "The billing flag in the cancellation outcome is unreadable",
    ),
    # Result validation
    "invalid_result_shape": (
        "成片返回结构不符合预期，请用 Resume 重试",
        "The delivered result had an unexpected shape; retry with Resume",
    ),
    "invalid_result_kind": (
        "成片类型不符合预期，请用 Resume 重试",
        "The delivered result was of an unexpected kind; retry with Resume",
    ),
    "invalid_result_url": (
        "成片地址不符合预期，请用 Resume 重试",
        "The delivered URL was unexpected; retry with Resume",
    ),
    "invalid_result_byte_count": (
        "成片大小信息缺失或不合法",
        "The delivered byte count is missing or invalid",
    ),
    "invalid_result_sha256": (
        "成片校验值缺失或不合法",
        "The delivered checksum is missing or invalid",
    ),
    "invalid_result_mime_type": (
        "成片类型标记缺失或不合法",
        "The delivered media type is missing or invalid",
    ),
    # Download, cache and local file handling
    "upload_failed": (
        "图片上传失败，请重试",
        "The image upload failed; please retry",
    ),
    "upload_network_error": (
        "图片上传时网络出错，请重试",
        "A network error interrupted the image upload; please retry",
    ),
    "auth_error": (
        "下载成片时鉴权失败，请确认 key 有效",
        "Authentication failed while downloading the video; check the key",
    ),
    "auth_forwarding_forbidden": (
        "不允许转发鉴权信息",
        "Forwarding credentials is not allowed",
    ),
    "download_failed": (
        "成片下载失败，请用 Resume 重试",
        "The video download failed; retry with the Resume node",
    ),
    "download_network_error": (
        "下载成片时网络出错，请用 Resume 重试",
        "A network error interrupted the download; retry with Resume",
    ),
    "download_timeout": (
        "下载成片超时，请用 Resume 重试",
        "The download timed out; retry with the Resume node",
    ),
    "mismatched_length": (
        "成片字节数与校验值不符，已放弃该文件",
        "The downloaded size did not match the expected byte count; the file was dropped",
    ),
    "digest_mismatch": (
        "成片校验失败，已放弃该文件",
        "The downloaded file failed its checksum and was dropped",
    ),
    "oversized_result": (
        "成片超过本地缓存上限，已放弃",
        "The video exceeds the local cache limit and was dropped",
    ),
    "invalid_byte_count": (
        "字节数信息不合法",
        "The reported byte count is invalid",
    ),
    "invalid_digest": (
        "校验值不合法",
        "The reported checksum is invalid",
    ),
    "invalid_encoding": (
        "响应编码无法解析",
        "The response encoding could not be decoded",
    ),
    "invalid_mime_type": (
        "内容类型不受支持",
        "That content type is not supported",
    ),
    "invalid_content_length": (
        "内容长度不合法",
        "The content length is invalid",
    ),
    "invalid_content_type": (
        "内容类型不受支持",
        "That content type is not supported",
    ),
    "base_url_mismatch": (
        "下载地址不在允许范围内",
        "The download URL is outside the allowed origins",
    ),
    "invalid_download_origin": (
        "下载地址不在允许范围内",
        "The download URL is outside the allowed origins",
    ),
    "invalid_upload_path": (
        "上传路径不合法",
        "The upload path is invalid",
    ),
    "invalid_cache_file": (
        "本地缓存文件不可用，请重新运行",
        "The local cache file is unusable; run the node again",
    ),
    "cache_file_unavailable": (
        "本地缓存文件不可用，请重新运行",
        "The local cache file is unusable; run the node again",
    ),
    "cache_file_replaced": (
        "本地缓存文件已被替换，请重新运行",
        "The local cache file was replaced; run the node again",
    ),
    "lease_released": (
        "本地缓存租约已释放，请重新运行",
        "The local cache lease was released; run the node again",
    ),
    "cache_budget_exceeded": (
        "本地缓存超出上限，请清理 ComfyUI 临时目录",
        "The local cache is over budget; clear ComfyUI's temp directory",
    ),
    "cache_cleanup_failed": (
        "本地缓存清理失败",
        "The local cache could not be cleaned up",
    ),
    "destination_exists": (
        "目标文件已存在",
        "The destination file already exists",
    ),
    "part_exists": (
        "临时分片文件已存在",
        "A partial file already exists",
    ),
    "io_error": (
        "本地文件读写出错",
        "A local file operation failed",
    ),
    "not_owner": (
        "本地缓存文件归属异常，请清理 ComfyUI 临时目录",
        "The local cache file has the wrong owner; clear ComfyUI's temp directory",
    ),
    "invalid_permissions": (
        "本地缓存目录权限异常",
        "The local cache directory permissions are wrong",
    ),
    "symlink_destination": (
        "缓存路径是不安全的符号链接",
        "The cache path is an unsafe symlink",
    ),
    "symlink_root": (
        "缓存根目录是不安全的符号链接",
        "The cache root is an unsafe symlink",
    ),
    "invalid_max_total_bytes": (
        "缓存上限参数不合法",
        "The cache size limit is invalid",
    ),
    "invalid_size": (
        "大小参数不合法",
        "The size value is invalid",
    ),
    "ca_load_failed": (
        "加载系统证书失败，无法安全连接服务端",
        "The system certificate store could not be loaded, so a safe connection is impossible",
    ),
    "invalid_video_stream": (
        "下载到的视频流无法解析",
        "The downloaded video stream could not be parsed",
    ),
    "invalid_video_dimensions": (
        "下载到的视频画面尺寸异常",
        "The downloaded video has invalid dimensions",
    ),
    "empty_video_stream": (
        "下载到的视频是空的",
        "The downloaded video was empty",
    ),
    "video_decode_failed": (
        "视频解码失败",
        "The video could not be decoded",
    ),
    "invalid_upload_ticket": (
        "服务端返回的上传凭证不合法",
        "The upload ticket from the server was invalid",
    ),
}

_FALLBACK = ("出错了", "Something went wrong")


def human_message(code: Any) -> str:
    """Render ``code`` as ``中文\\nEnglish\\n(code: <原始码>)``.

    Idempotent: a string that is already rendered (it is not snake_case) is
    returned unchanged, so callers may convert twice without corrupting text.
    """
    if not isinstance(code, str) or not code or not _CODE_RE.match(code):
        return str(code)

    zh, en = _MESSAGES.get(code, _FALLBACK)

    # wait_timeout_<id>, interaction_failed_<id>, timeout_<id>, interrupted_<id>
    match = _SUFFIX_RE.match(code)
    if match:
        head = match.group("head")
        interaction_id = match.group("iid")
        if head == "wait_timeout":
            return wait_timeout_message(interaction_id)
        if head in ("interaction_failed", "interaction_cancelled"):
            return failure_message(
                head.removeprefix("interaction_"), interaction_id, None, None
            )
        base = _MESSAGES.get(head)
        if base is not None:
            zh, en = base

    return f"{zh}\n{en}\n(code: {code})"


# --- status lines ------------------------------------------------------------


@dataclass(frozen=True)
class StatusReport:
    """One poll worth of UI feedback."""

    text: str
    bar_value: float | None = None
    bar_total: float | None = None


def _positive_int(block: Any, key: str) -> int | None:
    if not isinstance(block, dict):
        return None
    value = block.get(key)
    if type(value) is not int or isinstance(value, bool):
        return None
    return value


def _with_task_id(text: str, interaction_id: str | None) -> str:
    """Append the id Resume needs; terminal states never get one."""
    if not interaction_id:
        return text
    return f"{text}\n任务 id / Task id: {interaction_id}"


def _queue_text(
    payload: dict,
    remaining_seconds: float | None,
    auto_wait: bool,
    interaction_id: str | None = None,
) -> str:
    queue = payload.get("queue")
    position = _positive_int(queue, "position")
    estimate = _positive_int(queue, "estimated_wait_seconds")
    if position is None or estimate is None or position < 1 or estimate < 0:
        return _with_task_id("排队中\nQueued", interaction_id)

    minutes = max(1, math.ceil(estimate / 60))
    # ``position`` counts this order itself: 1 means next to start, so the
    # number of orders *ahead* is one less (queue contract v1).
    ahead = position - 1
    if ahead == 0:
        text = (
            f"排队中：下一个就轮到你，预计 {minutes} 分钟后开始\n"
            f"Queued: next in line, starts in about {minutes} min"
        )
    else:
        text = (
            f"排队中：前面还有 {ahead} 单，预计 {minutes} 分钟后开始\n"
            f"Queued: {ahead} ahead, starts in about {minutes} min"
        )
    if auto_wait and remaining_seconds is not None and estimate > remaining_seconds:
        text += (
            "\n排队较长，可能超过等待上限。到时任务不会取消，完成后照常扣费，"
            "可用 Resume 取回。"
            "\nThe queue may outlast this wait limit. The task will not be "
            "cancelled, is billed when it finishes, and Resume can collect it."
        )
    return _with_task_id(text, interaction_id)


def _running_status(payload: dict, interaction_id: str | None = None) -> StatusReport:
    progress = payload.get("progress")
    elapsed = _positive_int(progress, "elapsed_seconds")
    typical = _positive_int(progress, "typical_seconds")
    if elapsed is None or typical is None or typical <= 0 or elapsed < 0:
        return StatusReport(_with_task_id("生成中\nGenerating", interaction_id))
    # Never paint a finished bar while the order is still running.
    value = min(float(elapsed), typical * 0.99)
    return StatusReport(
        _with_task_id(
            f"生成中：已 {elapsed} 秒（通常 {typical} 秒）\n"
            f"Generating: {elapsed}s (usually {typical}s)",
            interaction_id,
        ),
        bar_value=value,
        bar_total=float(typical),
    )


_TERMINAL_TEXT = {
    "succeeded": "任务完成\nSucceeded",
    "failed": "任务失败\nFailed",
    "cancelled": "任务已取消\nCancelled",
}


def describe_status(
    payload: Any,
    *,
    remaining_seconds: float | None = None,
    auto_wait: bool = False,
    interaction_id: str | None = None,
) -> StatusReport:
    """Build the text (and progress-bar values) for one ``op=get`` payload.

    ``remaining_seconds`` is the local time left before the wait gives up; it
    only feeds the "queue may outlast the limit" hint, and only when
    ``auto_wait`` is set. Missing or malformed blocks degrade to the bare state
    word -- a wrong number is worse than no number.

    ``interaction_id``, when known, is appended to every non-terminal line so
    the id needed for Resume stays on screen for the whole wait.
    """
    if not isinstance(payload, dict):
        return StatusReport("任务状态未知\nUnknown state")

    state = payload.get("state")
    if state == "queued":
        return StatusReport(
            _queue_text(payload, remaining_seconds, auto_wait, interaction_id)
        )
    if state == "running":
        return _running_status(payload, interaction_id)
    text = _TERMINAL_TEXT.get(state)
    if text is None:
        return StatusReport("任务状态未知\nUnknown state")
    return StatusReport(text)
