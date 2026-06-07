"""Tests for llm.control_plane model capability public contracts."""

from __future__ import annotations

from typing import Any

from polaris.cells.llm.control_plane.public.contracts import (
    CheckLlmModelCapabilityQueryV1,
    LlmModelCapabilityResultV1,
    SaveLlmConfigCommandV1,
)
from polaris.cells.llm.control_plane.public.service import LlmControlPlaneService
from pytest import MonkeyPatch


class FakeConfigStore:
    def __init__(self) -> None:
        self.configs: dict[str, Any] = {}

    def save(self, config: Any) -> None:
        self.configs[config.role] = config

    def get(self, role: str) -> Any | None:
        return self.configs.get(role)

    def get_all(self) -> list[Any]:
        return list(self.configs.values())


def test_check_model_capability_requires_explicit_image_input_support(
    monkeypatch: MonkeyPatch,
) -> None:
    service = LlmControlPlaneService()
    store = FakeConfigStore()
    monkeypatch.setattr(service, "_get_store", lambda workspace: store)
    service.save_config(
        SaveLlmConfigCommandV1(
            workspace="/repo",
            role="qa",
            provider_id="vision-provider",
            model="vision-model",
            config={
                "provider_type": "openai_compat",
                "capabilities": ("text", "image_input"),
                "supports_image_input": True,
            },
        )
    )

    result = service.check_model_capability(
        CheckLlmModelCapabilityQueryV1(
            workspace="/repo",
            role="qa",
            capability="image_input",
        )
    )

    assert isinstance(result, LlmModelCapabilityResultV1)
    assert result.ok is True
    assert result.supported is True
    assert result.provider_id == "vision-provider"
    assert result.model == "vision-model"
    assert result.capability == "image_input"
    assert result.capability_ref.startswith("llm.control_plane:model-capability:")


def test_check_model_capability_rejects_unconfigured_or_text_only_model(monkeypatch: MonkeyPatch) -> None:
    service = LlmControlPlaneService()
    store = FakeConfigStore()
    monkeypatch.setattr(service, "_get_store", lambda workspace: store)

    missing = service.check_model_capability(
        CheckLlmModelCapabilityQueryV1(workspace="/repo", role="qa", capability="image_input")
    )
    assert missing.ok is False
    assert missing.supported is False
    assert missing.reason == "llm role is not configured"

    service.save_config(
        SaveLlmConfigCommandV1(
            workspace="/repo",
            role="qa",
            provider_id="text-provider",
            model="text-model",
            config={"provider_type": "openai_compat", "capabilities": ("text",)},
        )
    )
    text_only = service.check_model_capability(
        CheckLlmModelCapabilityQueryV1(workspace="/repo", role="qa", capability="image_input")
    )
    assert text_only.ok is True
    assert text_only.supported is False
    assert text_only.reason == "model does not declare image_input support"
