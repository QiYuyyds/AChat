"""Tests for provider error classification (app.adapters.errors).

构造 openai SDK 异常族实例（本地 httpx Request/Response，无网络），断言
classify_provider_error 输出中文、含可操作指引的用户消息，以及 fallback
分支保留原始错误文本（截断 ≤300 字符）。
"""

import httpx
import openai
import pytest

from app.adapters.errors import ProviderError, classify_provider_error

_URL = "https://api.example.com/v1/chat/completions"


def _status_error(cls: type[openai.APIStatusError], code: int, text: str):
    request = httpx.Request("POST", _URL)
    response = httpx.Response(code, request=request, text=text)
    return cls(f"Error code: {code} - {text}", response=response, body=None)


def _connection_error() -> openai.APIConnectionError:
    request = httpx.Request("POST", _URL)
    return openai.APIConnectionError(request=request)


def _timeout_error() -> openai.APITimeoutError:
    request = httpx.Request("POST", _URL)
    return openai.APITimeoutError(request=request)


# ─── 已知异常族 ──────────────────────────────────────────────────────────────


def test_401_authentication_error():
    msg = classify_provider_error(
        _status_error(openai.AuthenticationError, 401, "invalid api key")
    )
    assert msg is not None
    assert "API Key" in msg
    assert "模型配置" in msg


def test_429_rate_limit_error():
    msg = classify_provider_error(
        _status_error(openai.RateLimitError, 429, "rate limit exceeded")
    )
    assert msg is not None
    assert "限流" in msg
    assert "稍后重试" in msg


def test_404_not_found_error():
    msg = classify_provider_error(
        _status_error(openai.NotFoundError, 404, "model not found")
    )
    assert msg is not None
    assert "不存在" in msg
    assert "模型配置" in msg


def test_403_permission_denied_error():
    msg = classify_provider_error(
        _status_error(openai.PermissionDeniedError, 403, "permission denied")
    )
    assert msg is not None
    assert "无权" in msg
    assert "权限" in msg


def test_timeout_error_takes_precedence_over_connection():
    # APITimeoutError 是 APIConnectionError 的子类，必须命中超时分支
    msg = classify_provider_error(_timeout_error())
    assert msg is not None
    assert "超时" in msg
    assert "稍后重试" in msg


def test_connection_error():
    msg = classify_provider_error(_connection_error())
    assert msg is not None
    assert "连接" in msg
    assert "Base URL" in msg


def test_5xx_internal_server_error():
    msg = classify_provider_error(
        _status_error(openai.InternalServerError, 503, "service unavailable")
    )
    assert msg is not None
    assert "暂时不可用" in msg
    assert "稍后重试" in msg


# ─── fallback 分支 ───────────────────────────────────────────────────────────


def test_unknown_exception_fallback_keeps_original_text():
    msg = classify_provider_error(RuntimeError("something unexpected blew up"))
    assert msg is not None
    assert msg.startswith("模型调用失败：")
    assert "something unexpected blew up" in msg


def test_fallback_truncates_over_300_chars():
    long_text = "x" * 1000
    msg = classify_provider_error(RuntimeError(long_text))
    assert msg is not None
    original_part = msg.removeprefix("模型调用失败：")
    assert len(original_part) <= 300
    assert original_part.startswith("x" * 300)


def test_fallback_with_empty_message_uses_type_name():
    msg = classify_provider_error(ValueError())
    assert msg is not None
    assert "ValueError" in msg


def test_fallback_without_openai_environment(monkeypatch):
    """无 openai 环境（lazy import 失败）下未识别异常仍走 fallback。"""
    from app.adapters import errors

    errors._openai_error_families.cache_clear()
    monkeypatch.setattr(errors, "_openai_error_families", lambda: None)
    msg = errors.classify_provider_error(RuntimeError("boom"))
    assert msg == "模型调用失败：boom"


# ─── ProviderError ───────────────────────────────────────────────────────────


def test_provider_error_is_runtime_error_with_user_message():
    err = ProviderError("API Key 无效或未配置")
    assert isinstance(err, RuntimeError)
    assert str(err) == "API Key 无效或未配置"


def test_provider_error_chains_original_exception():
    cause = _status_error(openai.AuthenticationError, 401, "invalid api key")
    err = ProviderError("classified message")
    err.__cause__ = cause
    assert err.__cause__ is cause


# ─── 消息质量：中文 + 可操作指引 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc",
    [
        _status_error(openai.AuthenticationError, 401, "invalid api key"),
        _status_error(openai.RateLimitError, 429, "rate limit exceeded"),
        _status_error(openai.NotFoundError, 404, "model not found"),
        _status_error(openai.PermissionDeniedError, 403, "forbidden"),
        _timeout_error(),
        _connection_error(),
        _status_error(openai.InternalServerError, 500, "internal error"),
    ],
    ids=["401", "429", "404", "403", "timeout", "connection", "5xx"],
)
def test_known_families_message_is_actionable(exc):
    msg = classify_provider_error(exc)
    assert msg is not None
    assert any("\u4e00" <= ch <= "\u9fff" for ch in msg), f"消息应为中文: {msg}"
    # 可操作指引：出现「请」引导的修复建议
    assert "请" in msg
