"""Tests for PM Codex backend environment propagation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from polaris.delivery.cli.pm import backend


def test_build_codex_env_from_role_config(monkeypatch) -> None:
    class _FakeAdapter:
        def load_provider_config(self, *, workspace: str, provider_id: str) -> dict[str, Any]:
            assert workspace == "C:/workspace"
            assert provider_id == "codex_cli"
            return {
                "type": "codex_cli",
                "codex_exec": {
                    "sandbox": "workspace-write",
                    "skip_git_repo_check": True,
                    "color": "never",
                },
            }

    monkeypatch.setattr(backend, "load_pm_model_config", lambda: ("codex_cli", "gpt-5.3-codex"))
    monkeypatch.setattr(
        "polaris.infrastructure.llm.provider_runtime_adapter.AppLLMRuntimeAdapter",
        lambda: _FakeAdapter(),
    )

    env = backend._build_codex_env_from_role_config(SimpleNamespace(workspace_full="C:/workspace"))

    assert env["KERNELONE_CODEX_MODEL"] == "gpt-5.3-codex"
    assert env["KERNELONE_CODEX_SANDBOX"] == "workspace-write"
    assert env["KERNELONE_CODEX_SKIP_GIT_CHECK"] == "1"
    assert env["KERNELONE_CODEX_COLOR"] == "never"


def test_build_codex_env_falls_back_to_global_config(monkeypatch) -> None:
    class _FailingAdapter:
        def load_provider_config(self, *, workspace: str, provider_id: str) -> dict[str, Any]:
            raise RuntimeError("DI settings unavailable")

    monkeypatch.setattr(backend, "load_pm_model_config", lambda: ("codex_cli", "gpt-5.3-codex"))
    monkeypatch.setattr(
        "polaris.infrastructure.llm.provider_runtime_adapter.AppLLMRuntimeAdapter",
        lambda: _FailingAdapter(),
    )
    monkeypatch.setattr(
        "polaris.kernelone.llm.config_store.load_llm_config",
        lambda workspace, cache_root, settings=None: {
            "providers": {
                "codex_cli": {
                    "type": "codex_cli",
                    "codex_exec": {
                        "sandbox": "workspace-write",
                        "skip_git_repo_check": True,
                        "color": "never",
                    },
                }
            }
        },
    )

    env = backend._build_codex_env_from_role_config(
        SimpleNamespace(workspace_full="C:/workspace", cache_root_full="C:/runtime")
    )

    assert env["KERNELONE_CODEX_MODEL"] == "gpt-5.3-codex"
    assert env["KERNELONE_CODEX_SANDBOX"] == "workspace-write"
    assert env["KERNELONE_CODEX_SKIP_GIT_CHECK"] == "1"
    assert env["KERNELONE_CODEX_COLOR"] == "never"
