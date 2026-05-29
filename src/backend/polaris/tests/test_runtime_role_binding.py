from __future__ import annotations

import asyncio
import gc
import sys
import types
import warnings
from types import SimpleNamespace

import pytest
from polaris.cells.llm.provider_runtime.internal.runtime_invoke import invoke_role_runtime_provider
from polaris.infrastructure.llm.provider_runtime_adapter import AppLLMRuntimeAdapter
from polaris.kernelone.llm.runtime import invoke_role_runtime_provider as invoke_kernel_role_runtime_provider
from polaris.kernelone.llm.runtime_config import RuntimeConfigManager


def _install_config_settings_stub(monkeypatch: pytest.MonkeyPatch, settings_cls: type[object]) -> None:
    module = types.ModuleType("config")
    module.Settings = settings_cls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "config", module)


def test_runtime_config_strict_mode_requires_explicit_role_assignment(monkeypatch) -> None:
    manager = RuntimeConfigManager()
    manager.clear_cache()
    monkeypatch.setenv("KERNELONE_ROLE_MODEL_BINDING_MODE", "strict")
    monkeypatch.setattr(manager, "get_role_config", lambda _role_id: None)

    provider_id, model = manager.get_role_model("pm")

    assert provider_id == ""
    assert model == ""


def test_runtime_config_warn_mode_rejects_missing_role_model(monkeypatch) -> None:
    manager = RuntimeConfigManager()
    manager.clear_cache()
    monkeypatch.setenv("KERNELONE_ROLE_MODEL_BINDING_MODE", "warn")
    monkeypatch.setattr(manager, "get_role_config", lambda _role_id: None)

    provider_id, model = manager.get_role_model("director")

    assert provider_id == ""
    assert model == ""


def test_runtime_config_manager_accepts_utf8_bom_config(tmp_path) -> None:
    config_path = tmp_path / "llm_config.json"
    config_path.write_text(
        '{"roles":{"pm":{"provider_id":"codex_cli","model":"gpt-5.3-codex"}}}',
        encoding="utf-8-sig",
    )
    manager = RuntimeConfigManager(config_path_resolver=lambda: str(config_path))

    provider_id, model = manager.get_role_model("pm")

    assert provider_id == "codex_cli"
    assert model == "gpt-5.3-codex"


def test_invoke_runtime_provider_strict_mode_rejects_missing_provider_type(
    monkeypatch,
    tmp_path,
) -> None:
    class _Settings:
        def __init__(self, *args, **kwargs) -> None:
            self.ramdisk_root = ""

    monkeypatch.setenv("KERNELONE_ROLE_MODEL_BINDING_MODE", "strict")
    _install_config_settings_stub(monkeypatch, _Settings)
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


def test_kernel_runtime_provider_invocation_forces_workspace_working_dir(tmp_path) -> None:
    class _Provider:
        def __init__(self) -> None:
            self.seen_config: dict[str, object] = {}

        def invoke(self, _prompt: str, _model: str, config: dict[str, object]) -> SimpleNamespace:
            self.seen_config = dict(config)
            return SimpleNamespace(ok=True, output="ok", error="", latency_ms=1, usage={})

    class _Adapter:
        def __init__(self, provider: _Provider) -> None:
            self.provider = provider

        def get_role_model(self, _role: str) -> tuple[str, str]:
            return "codex_cli", "gpt-5.3-codex"

        def load_provider_config(self, *, workspace: str, provider_id: str) -> dict[str, object]:
            del workspace, provider_id
            return {
                "type": "codex_cli",
                "working_dir": "C:/stale-workspace",
                "codex_exec": {"sandbox": "workspace-write"},
            }

        def get_provider_instance(self, provider_type: str) -> _Provider | None:
            return self.provider if provider_type == "codex_cli" else None

        def record_provider_failure(self, _provider_type: str) -> None:
            return None

    provider = _Provider()

    result = invoke_kernel_role_runtime_provider(
        role="director",
        workspace=str(tmp_path),
        prompt="write the task files",
        fallback_model="",
        timeout=3,
        adapter=_Adapter(provider),
        blocked_provider_types=None,
    )

    assert result.ok is True
    assert provider.seen_config["working_dir"] == str(tmp_path)


@pytest.mark.asyncio
async def test_provider_runtime_adapter_requires_settings_or_di_registration(
    monkeypatch,
    tmp_path,
) -> None:
    class _Settings:
        def __init__(self, *args, **kwargs) -> None:
            self.ramdisk_root = ""

    _install_config_settings_stub(monkeypatch, _Settings)

    async def _fake_get_container():
        class _Container:
            def has_registration(self, _interface) -> bool:
                return False

        return _Container()

    monkeypatch.setattr("polaris.infrastructure.di.container.get_container", _fake_get_container)
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
        with pytest.raises(RuntimeError, match=r"Settings resolution via DI failed|Settings is not registered"):
            adapter.load_provider_config(
                workspace=str(tmp_path),
                provider_id="provider-a",
                settings=None,
            )
        await asyncio.sleep(0)
        gc.collect()

    assert not any("was never awaited" in str(item.message) for item in caught)
