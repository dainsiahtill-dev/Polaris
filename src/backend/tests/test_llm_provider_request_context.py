from __future__ import annotations

import pytest
from polaris.cells.llm.provider_config.internal.provider_context import (
    resolve_provider_request_context,
)
from polaris.cells.llm.provider_config.public.contracts import ProviderNotFoundError


def test_resolve_provider_request_context_merges_headers_and_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polaris.cells.llm.provider_config.internal.provider_context.load_llm_config_port",
        lambda *args, **kwargs: {
            "providers": {
                "openai_compat": {
                    "type": "openai_compat",
                    "base_url": "https://example.com",
                    "api_key": "from_config",
                    "headers": {"x-existing": "1"},
                }
            }
        },
    )

    context = resolve_provider_request_context(
        workspace=str(tmp_path),
        cache_root=str(tmp_path / ".cache"),
        provider_id="openai_compat",
        api_key="from_payload",
        headers={"x-request": "2"},
    )

    assert context.provider_type == "openai_compat"
    assert context.api_key == "from_payload"
    assert context.provider_cfg["api_key"] == "from_payload"
    assert context.provider_cfg["headers"] == {
        "x-existing": "1",
        "x-request": "2",
    }


def test_resolve_provider_request_context_uses_config_api_key_when_payload_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polaris.cells.llm.provider_config.internal.provider_context.load_llm_config_port",
        lambda *args, **kwargs: {
            "providers": {
                "anthropic_compat": {
                    "type": "anthropic_compat",
                    "api_key": "from_config",
                }
            }
        },
    )

    context = resolve_provider_request_context(
        workspace=str(tmp_path),
        cache_root=str(tmp_path / ".cache"),
        provider_id="anthropic_compat",
        api_key=None,
        headers=None,
    )

    assert context.provider_type == "anthropic_compat"
    assert context.api_key == "from_config"
    assert context.provider_cfg["api_key"] == "from_config"


def test_resolve_provider_request_context_raises_for_missing_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "polaris.cells.llm.provider_config.internal.provider_context.load_llm_config_port",
        lambda *args, **kwargs: {"providers": {}},
    )

    # Refactored: raises ProviderNotFoundError (contract exception) directly
    # instead of HTTPException. Tests verify the contract exception type.
    with pytest.raises(ProviderNotFoundError) as exc_info:
        resolve_provider_request_context(
            workspace=str(tmp_path),
            cache_root=str(tmp_path / ".cache"),
            provider_id="missing",
            api_key=None,
            headers=None,
        )

    assert exc_info.value.code == "provider_not_found"
    assert "missing" in str(exc_info.value)
