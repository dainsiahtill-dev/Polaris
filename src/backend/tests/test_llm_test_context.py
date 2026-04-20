from __future__ import annotations

import pytest
from polaris.cells.llm.provider_config.internal.test_context import (
    resolve_llm_test_execution_context,
)
from polaris.cells.llm.provider_config.public.contracts import (
    ProviderConfigValidationError,
    RoleNotConfiguredError,
)


def test_resolve_context_uses_direct_config_when_base_url_present(tmp_path):
    payload = {
        "role": "connectivity",
        "provider_type": "openai_compat",
        "base_url": "https://example.com",
        "model": "gpt-4.1",
    }

    # When base_url is present, no workspace/cache_root needed (direct config path)
    context = resolve_llm_test_execution_context(
        workspace=str(tmp_path),
        cache_root=str(tmp_path / ".cache"),
        payload=payload,
    )

    assert context.use_direct_config is True
    assert context.effective_provider_id == "direct_openai_compat"
    assert context.model == "gpt-4.1"
    assert context.suites == ["connectivity"]
    assert context.provider_cfg == {
        "type": "openai_compat",
        "base_url": "https://example.com",
        "api_path": "/v1/chat/completions",
        "timeout": 30,
    }


def test_resolve_context_connectivity_requires_provider_and_model_without_base_url(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polaris.cells.llm.control_plane.load_llm_config_port",
        lambda *args, **kwargs: {},
    )

    # Refactored: raises ProviderConfigValidationError (contract exception)
    # instead of HTTPException. No workspace/cache_root needed when using config path.
    with pytest.raises(ProviderConfigValidationError) as exc_info:
        resolve_llm_test_execution_context(
            workspace=str(tmp_path),
            cache_root=str(tmp_path / ".cache"),
            payload={"role": "connectivity", "provider_id": "openai_compat"},
        )

    assert exc_info.value.code == "provider_config_validation_error"
    assert "连通性测试需要提供" in str(exc_info.value)


def test_resolve_context_connectivity_ignores_requested_suites(tmp_path):
    payload = {
        "role": "connectivity",
        "provider_type": "openai_compat",
        "base_url": "https://example.com",
        "model": "gpt-4.1",
        "suites": ["connectivity", "response", "qualification"],
    }

    context = resolve_llm_test_execution_context(
        workspace=str(tmp_path),
        cache_root=str(tmp_path / ".cache"),
        payload=payload,
    )

    assert context.suites == ["connectivity"]


def test_resolve_context_uses_role_defaults_for_non_connectivity(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polaris.cells.llm.control_plane.load_llm_config_port",
        lambda *args, **kwargs: {
            "roles": {"pm": {"provider_id": "codex_cli", "model": "gpt-5"}},
        },
    )

    context = resolve_llm_test_execution_context(
        workspace=str(tmp_path),
        cache_root=str(tmp_path / ".cache"),
        payload={"role": "pm", "suites": ["response"]},
    )

    assert context.use_direct_config is False
    assert context.provider_cfg is None
    assert context.role == "pm"
    assert context.effective_provider_id == "codex_cli"
    assert context.model == "gpt-5"
    assert context.suites == ["response"]


def test_resolve_context_raises_when_role_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polaris.cells.llm.control_plane.load_llm_config_port",
        lambda *args, **kwargs: {"roles": {}},
    )

    # Refactored: raises RoleNotConfiguredError (contract exception) directly
    # instead of HTTPException. Tests verify the contract exception type.
    with pytest.raises(RoleNotConfiguredError) as exc_info:
        resolve_llm_test_execution_context(
            workspace=str(tmp_path),
            cache_root=str(tmp_path / ".cache"),
            payload={"role": "pm"},
        )

    assert exc_info.value.code == "role_not_configured"
    assert "pm" in str(exc_info.value)
