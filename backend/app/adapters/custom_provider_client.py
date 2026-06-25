"""Resolve OpenAI-compatible client config for CustomAgentAdapter providers.

Port of src/server/adapters/custom-provider-client.ts, with the two validators
from src/shared/openai-compatible.ts inlined.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_VOLCANO_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

OPENAI_COMPATIBLE_BASE_URL_REQUIRED_ERROR = (
    "OpenAI-compatible provider 必须填写 Chat Completions Base URL，"
    "例如 https://dashscope.aliyuncs.com/compatible-mode/v1"
)
OPENAI_COMPATIBLE_BASE_URL_FORMAT_ERROR = (
    "OpenAI-compatible Base URL 必须是完整 URL，"
    "例如 https://dashscope.aliyuncs.com/compatible-mode/v1"
)
OPENAI_COMPATIBLE_API_KEY_REQUIRED_ERROR = (
    "OpenAI-compatible provider 必须为该 Agent 单独填写 API Key"
)


def validate_openai_compatible_base_url(
    provider: str | None, base_url: str | None
) -> str | None:
    if provider != "openai-compatible":
        return None
    trimmed = base_url.strip() if base_url else ""
    if not trimmed:
        return OPENAI_COMPATIBLE_BASE_URL_REQUIRED_ERROR
    parsed = urlparse(trimmed)
    if not parsed.scheme or not parsed.netloc:
        return OPENAI_COMPATIBLE_BASE_URL_FORMAT_ERROR
    return None


def validate_openai_compatible_api_key(
    provider: str | None, api_key: str | None
) -> str | None:
    if provider != "openai-compatible":
        return None
    return None if (api_key and api_key.strip()) else OPENAI_COMPATIBLE_API_KEY_REQUIRED_ERROR


@dataclass
class CustomProviderClientConfig:
    api_key: str
    base_url: str | None = None


def resolve_custom_provider_client_config(
    provider: str,
    override_key: str | None = None,
    api_base_url: str | None = None,
) -> CustomProviderClientConfig:
    if provider == "deepseek":
        api_key = (override_key.strip() if override_key else "") or os.environ.get(
            "DEEPSEEK_API_KEY"
        )
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY not set and agent has no apiKey")
        return CustomProviderClientConfig(api_key=api_key, base_url=DEFAULT_DEEPSEEK_BASE_URL)
    if provider == "volcano-ark":
        api_key = (override_key.strip() if override_key else "") or os.environ.get("ARK_API_KEY")
        if not api_key:
            raise ValueError("ARK_API_KEY not set and agent has no apiKey")
        return CustomProviderClientConfig(api_key=api_key, base_url=DEFAULT_VOLCANO_ARK_BASE_URL)
    if provider == "openai":
        api_key = (override_key.strip() if override_key else "") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set and agent has no apiKey")
        return CustomProviderClientConfig(api_key=api_key)
    if provider == "openai-compatible":
        base_url_error = validate_openai_compatible_base_url(provider, api_base_url)
        if base_url_error:
            raise ValueError(base_url_error)
        api_key_error = validate_openai_compatible_api_key(provider, override_key)
        if api_key_error:
            raise ValueError(api_key_error)
        return CustomProviderClientConfig(
            api_key=override_key.strip() if override_key else "",
            base_url=api_base_url.strip() if api_base_url else None,
        )
    raise ValueError(f'CustomAgentAdapter does not support provider "{provider}" yet')
