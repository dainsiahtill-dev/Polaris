from __future__ import annotations

from types import SimpleNamespace

import pytest
from polaris.cells.runtime.state_owner.internal.state import AppState
from polaris.delivery.http.routers import pm_chat, role_chat


def _make_request(workspace: str) -> SimpleNamespace:
    settings = SimpleNamespace(workspace=workspace, ramdisk_root="")
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                app_state=AppState(settings=settings),
            )
        )
    )


def test_pm_chat_status_allows_configured_role_without_test_ready(monkeypatch, tmp_path) -> None:
    request = _make_request(str(tmp_path))
    config_payload = {
        "roles": {
            "pm": {
                "provider_id": "provider-x",
                "model": "model-x",
                "profile": "pm-default",
            }
        },
        "providers": {
            "provider-x": {
                "type": "openai_compat",
            }
        },
    }

    monkeypatch.setattr(pm_chat, "build_cache_root", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(pm_chat, "load_llm_test_index", lambda _settings: {"roles": {}})
    monkeypatch.setattr(pm_chat.llm_config, "load_llm_config", lambda *args, **kwargs: config_payload)

    result = pm_chat.pm_chat_status(request)

    assert result["ready"] is True
    assert result["configured"] is True
    assert result["llm_test_ready"] is False
    assert result["role_config"]["provider_id"] == "provider-x"
    assert result["role_config"]["model"] == "model-x"


@pytest.mark.asyncio
async def test_role_chat_status_allows_configured_role_without_test_ready(monkeypatch, tmp_path) -> None:
    request = _make_request(str(tmp_path))
    config_payload = {
        "roles": {
            "pm": {
                "provider_id": "provider-y",
                "model": "model-y",
                "profile": "pm-default",
            }
        },
        "providers": {
            "provider-y": {
                "type": "openai_compat",
            }
        },
    }

    monkeypatch.setattr(role_chat, "build_cache_root", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(role_chat, "load_llm_test_index", lambda _settings: {"roles": {}})
    monkeypatch.setattr(role_chat.llm_config, "load_llm_config", lambda *args, **kwargs: config_payload)

    result = await role_chat.role_chat_status(request, "pm")

    assert result["ready"] is True
    assert result["configured"] is True
    assert result["llm_test_ready"] is False
    assert result["role"] == "pm"
    assert result["role_config"]["provider_id"] == "provider-y"
    assert result["role_config"]["model"] == "model-y"


def test_pm_chat_status_marks_missing_role_as_unconfigured(monkeypatch, tmp_path) -> None:
    request = _make_request(str(tmp_path))
    config_payload = {
        "roles": {},
        "providers": {},
    }

    monkeypatch.setattr(pm_chat, "build_cache_root", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(pm_chat, "load_llm_test_index", lambda _settings: {"roles": {}})
    monkeypatch.setattr(pm_chat.llm_config, "load_llm_config", lambda *args, **kwargs: config_payload)

    result = pm_chat.pm_chat_status(request)

    assert result["ready"] is False
    assert result["configured"] is False
    assert "not configured" in result["error"]


@pytest.mark.asyncio
async def test_role_chat_status_marks_missing_provider_as_unconfigured(monkeypatch, tmp_path) -> None:
    request = _make_request(str(tmp_path))
    config_payload = {
        "roles": {
            "pm": {
                "provider_id": "provider-z",
                "model": "model-z",
            }
        },
        "providers": {},
    }

    monkeypatch.setattr(role_chat, "build_cache_root", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(role_chat, "load_llm_test_index", lambda _settings: {"roles": {}})
    monkeypatch.setattr(role_chat.llm_config, "load_llm_config", lambda *args, **kwargs: config_payload)

    result = await role_chat.role_chat_status(request, "pm")

    assert result["ready"] is False
    assert result["configured"] is False
    assert "not found" in result["error"]
