"""Pydantic request/response models for LLM router endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class LlmTestPayload(BaseModel):
    role: str | None = None
    provider_id: str | None = None
    model: str | None = None
    suites: list[str] | None = None
    test_level: str = "quick"
    evaluation_mode: str | None = None
    api_key: str | None = None
    headers: dict[str, str] | None = None
    env_overrides: dict[str, str] | None = None
    prompt_override: str | None = None
    # Connectivity-only fields (Scheme B): allow bypassing config loading
    provider_type: str | None = None
    base_url: str | None = None
    api_path: str | None = None
    timeout: int | None = None


class ProviderActionPayload(BaseModel):
    api_key: str | None = None
    headers: dict[str, str] | None = None


class InterviewAskPayload(BaseModel):
    role: str
    provider_id: str
    model: str
    question: str
    context: list[dict[str, Any]] | None = None
    expects_thinking: bool | None = None
    criteria: list[str] | None = None
    session_id: str | None = None
    api_key: str | None = None
    # 使用空字典作为默认值，避免 None vs {} 的兼容性问题
    headers: dict[str, str] | None = Field(default_factory=dict)
    env_overrides: dict[str, str] | None = Field(default_factory=dict)
    debug: bool | None = None

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_session_id(cls, v) -> Any:
        """将空字符串或None统一处理为None"""
        if v == "" or v is None:
            return None
        return v

    @field_validator("context", mode="before")
    @classmethod
    def normalize_context(cls, v) -> Any:
        """确保context是列表或None"""
        if v is None or v == []:
            return None
        return v

    @field_validator("criteria", mode="before")
    @classmethod
    def normalize_criteria(cls, v) -> Any:
        """确保criteria是字符串列表或None"""
        if v is None or v == []:
            return None
        # 过滤掉非字符串项
        if isinstance(v, list):
            return [str(item) for item in v if item is not None]
        return v


class InterviewCancelPayload(BaseModel):
    session_id: str


class InterviewSavePayload(BaseModel):
    role: str
    provider_id: str
    model: str
    report: dict[str, Any]
    session_id: str | None = None
