from __future__ import annotations

import asyncio
import gc
import warnings

import pytest
from polaris.cells.llm.provider_runtime.internal.runtime_invoke import invoke_role_runtime_provider
from polaris.infrastructure.llm.provider_runtime_adapter import AppLLMRuntimeAdapter
from polaris.kernelone.llm.runtime_config import RuntimeConfigManager


def test_runtime_config_strict_mode_requires_explicit_role_assignment(monkeypatch) -> None:
    manager = RuntimeConfigManager()
    manager.clear_cache()
    monkeypatch.setenv("POLARIS_ROLE_MODEL_BINDING_MODE", "strict")
    monkeypatch.setattr(manager, "get_role_config", lambda _role_id: None)

    provider_id, model = manager.get_role_model("pm")

    assert provider_id == ""
    assert model == ""


def test_runtime_config_warn_mode_rejects_missing_role_model(monkeypatch) -> None:
    manager = RuntimeConfigManager()
    manager.clear_cache()
    monkeypatch.setenv("POLARIS_ROLE_MODEL_BINDING_MODE", "warn")
    monkeypatch.setattr(manager, "get_role_config", lambda _role_id: None)

    provider_id, model = manager.get_role_model("director")

    assert provider_id == ""
    assert model == ""


def test_invoke_runtime_provider_strict_mode_rejects_missing_provider_type(
    monkeypatch,
    tmp_path,
) -> None:
    class _Settings:
        def __init__(self, *args, **kwargs):
            self.ramdisk_root = ""

    monkeypatch.setenv("POLARIS_ROLE_MODEL_BINDING_MODE", "strict")
    monkeypatch.setattr("config.Settings", _Settings)
    monkeypatch.setattr(
        "polaris.kernelone.llm.runtime_config.get_role_model",
        lambda _role: ("provider-a", "model-a"),
    )
    monkeypatch.setattr(
        "polaris.infrastructure.llm.provider_runtime_adapter.llm_config.load_llm_config",
        lambda *args, **kwargs: {"providers": {"provider-a": {}}},
    )
    monkeypatch.setattr("polaris.kernelone.storage.io_paths.build_cache_root", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        "polaris.infrastructure.llm.provider_runtime_adapter._resolve_settings_from_di",
        lambda: _Settings(),
    )

    result = invoke_role_runtime_provider(
        role="pm",
        workspace=str(tmp_path),
        prompt="ping",
        fallback_model="fallback-model",
        timeout=3,
        blocked_provider_types=None,
    )

    assert result.attempted is False
    assert result.ok is False
    assert result.error == "strict_role_model_binding_missing_provider_type"


@pytest.mark.asyncio
async def test_provider_runtime_adapter_requires_settings_or_di_registration(
    monkeypatch,
    tmp_path,
) -> None:
    class _Settings:
        def __init__(self, *args, **kwargs):
            self.ramdisk_root = ""

    async def _fake_get_container():
        class _Container:
            def has_registration(self, _interface) -> bool:
                return False

        return _Container()

    monkeypatch.setattr("polaris.infrastructure.di.container.get_container", _fake_get_container)
    monkeypatch.setattr("config.Settings", _Settings)
    monkeypatch.setattr(
        "polaris.infrastructure.llm.provider_runtime_adapter.llm_config.load_llm_config",
        lambda *args, **kwargs: {"providers": {"provider-a": {"provider_type": "openai"}}},
    )
    monkeypatch.setattr(
        "polaris.kernelone.storage.io_paths.build_cache_root",
        lambda *args, **kwargs: "",
    )

    adapter = AppLLMRuntimeAdapter()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(RuntimeError, match="Settings resolution via DI failed|Settings is not registered"):
            adapter.load_provider_config(
                workspace=str(tmp_path),
                provider_id="provider-a",
                settings=None,
            )
        await asyncio.sleep(0)
        gc.collect()

    assert not any("was never awaited" in str(item.message) for item in caught)
