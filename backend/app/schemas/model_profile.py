"""Pydantic schemas for ModelProfile CRUD."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ModelProvider = Literal["anthropic", "openai", "deepseek", "volcano-ark", "openai-compatible"]
TestStatus = Literal["untested", "ok", "fail"]


def _mask_key(key: str | None) -> str:
    """Return a masked representation of an API key (last 4 chars)."""
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return f"****{key[-4:]}"


class CreateModelProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    provider: ModelProvider
    model_id: str = Field(alias="modelId")
    api_key: str | None = Field(default=None, alias="apiKey")
    api_base_url: str | None = Field(default=None, alias="apiBaseUrl")
    is_default: bool | None = Field(default=None, alias="isDefault")
    supports_vision: bool | None = Field(default=False, alias="supportsVision")

    model_config = {"populate_by_name": True, "protected_namespaces": ()}


class UpdateModelProfileRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    provider: ModelProvider | None = None
    model_id: str | None = Field(default=None, alias="modelId")
    api_key: str | None = Field(default=None, alias="apiKey")
    api_base_url: str | None = Field(default=None, alias="apiBaseUrl")
    is_default: bool | None = Field(default=None, alias="isDefault")
    supports_vision: bool | None = Field(default=None, alias="supportsVision")

    model_config = {"populate_by_name": True, "protected_namespaces": ()}


class ModelProfileOut(BaseModel):
    """Wire shape for ModelProfile — api_key is masked."""

    id: str
    name: str
    provider: str
    model_id: str = Field(alias="modelId")
    api_key_masked: str = Field(alias="apiKeyMasked")
    api_base_url: str | None = Field(default=None, alias="apiBaseUrl")
    is_default: bool = Field(alias="isDefault")
    supports_vision: bool = Field(alias="supportsVision")
    last_test_status: str = Field(alias="lastTestStatus")
    last_tested_at: int | None = Field(default=None, alias="lastTestedAt")
    created_at: int = Field(alias="createdAt")
    updated_at: int = Field(alias="updatedAt")

    model_config = {"populate_by_name": True, "protected_namespaces": ()}


class ModelProfileTestResult(BaseModel):
    status: Literal["ok", "fail"]
    latency_ms: int = Field(alias="latencyMs")
    error: str | None = None

    model_config = {"populate_by_name": True}
