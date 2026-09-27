"""Provider 错误分类 — 把 openai SDK 异常族映射为可操作的中文用户消息。

供 L3（agent_runner 的 ReAct 循环 catch-all）消费：分类结果作为
``ProviderError`` 的消息向上抛，走既有 failed 链路（finalize failed +
``_emit_error_visualisation``），在会话中渲染为「[失败] {消息}」。

openai 采用模块内延迟 import（与 parser_registry 的 lazy import 惯例一致），
无 openai 环境下本模块可正常导入，未识别异常走 fallback 分支。
"""

from __future__ import annotations

from functools import lru_cache

# fallback 分支保留原始错误文本的上限（design D2：≤300 字符）
_FALLBACK_MAX_CHARS = 300

# 未识别异常的统一前缀；消息内导航用概念名（「模型配置」），
# 不硬编码侧栏 tab 名（入口命名统一是另一个独立 change）。
_FALLBACK_PREFIX = "模型调用失败："

# 各异常族的用户消息：说清出了什么问题 + 去哪里修。
_MSG_AUTH = "API Key 无效或未配置，请检查模型配置中的 API Key 后重试。"
_MSG_RATE_LIMIT = "请求触发限流或账户额度不足，请稍后重试，或检查服务账户的余额与配额。"
_MSG_NOT_FOUND = "模型或接口地址不存在，请检查模型配置中的模型名称与 Base URL。"
_MSG_PERMISSION = "当前账户无权访问该模型，请检查账户权限或确认模型已开通。"
_MSG_INTERNAL = "模型服务暂时不可用（服务端错误），请稍后重试。"
_MSG_TIMEOUT = "模型请求超时，请稍后重试。"
_MSG_CONNECTION = "无法连接模型服务，请检查网络连接与模型配置中的 Base URL。"


class ProviderError(RuntimeError):
    """Provider/SDK 调用失败。``str(exc)`` 即面向用户的中文消息。"""


@lru_cache(maxsize=1)
def _openai_error_families() -> tuple[tuple[type[BaseException], str], ...] | None:
    """Return (exception class, message) pairs for the openai SDK, or None.

    Order matters: specific subclasses before their bases — in particular
    ``APITimeoutError`` before ``APIConnectionError``.
    """
    try:
        import openai
    except ImportError:
        return None
    return (
        (openai.AuthenticationError, _MSG_AUTH),
        (openai.RateLimitError, _MSG_RATE_LIMIT),
        (openai.NotFoundError, _MSG_NOT_FOUND),
        (openai.PermissionDeniedError, _MSG_PERMISSION),
        (openai.InternalServerError, _MSG_INTERNAL),
        (openai.APITimeoutError, _MSG_TIMEOUT),
        (openai.APIConnectionError, _MSG_CONNECTION),
    )


def _truncate(text: str) -> str:
    text = text.strip()
    if len(text) > _FALLBACK_MAX_CHARS:
        return text[:_FALLBACK_MAX_CHARS]
    return text


def classify_provider_error(exc: BaseException) -> str | None:
    """Map a provider-call exception to a user-facing Chinese message.

    Known openai exception families get specific actionable messages;
    anything else falls back to the original error text (truncated) so
    diagnosis is possible. Returns ``None`` only when no usable message
    can be produced (callers fall back to ``str(exc)``).
    """
    families = _openai_error_families()
    if families is not None:
        for exc_type, message in families:
            if isinstance(exc, exc_type):
                return message

    original = _truncate(str(exc)) or type(exc).__name__
    message = f"{_FALLBACK_PREFIX}{original}"
    return message
