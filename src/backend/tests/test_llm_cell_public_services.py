from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from polaris.cells.llm.dialogue.public import service as dialogue_public
from polaris.cells.llm.dialogue.public.contracts import (
    InvokeDocsDialogueCommandV1,
    InvokeRoleDialogueCommandV1,
    ValidateRoleOutputQueryV1,
)
from polaris.cells.llm.provider_config.public import service as provider_config_public
from polaris.cells.llm.provider_config.public.contracts import (
    ResolveLlmTestExecutionContextCommandV1,
    ResolveProviderContextCommandV1,
    SyncSettingsFromLlmCommandV1,
)
from polaris.cells.llm.provider_runtime.public import service as provider_runtime_public
from polaris.cells.llm.provider_runtime.public.contracts import (
    InvokeProviderActionCommandV1,
    InvokeRoleProviderCommandV1,
    QueryRoleRuntimeProviderSupportV1,
)


def test_execute_provider_action_maps_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_http(**_: object) -> dict[str, object]:
        raise HTTPException(status_code=404, detail="provider not found")

    monkeypatch.setattr(
        "polaris.cells.llm.provider_runtime.public.service.run_provider_action",
        _raise_http,
    )
    command = InvokeProviderActionCommandV1(
        action="models",
        provider_type="openai_compat",
        provider_cfg={"base_url": "http://localhost"},
        api_key="secret",
    )
    result = provider_runtime_public.execute_provider_action(command)

    assert result.ok is False
    assert result.status == "failed"
    assert result.error_code == "provider_action_error"
    assert "provider not found" in str(result.error_message)


def test_execute_role_provider_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def _invoke(**_: object) -> dict[str, object]:
        return {"provider_kind": "openai_compat", "content": "ok"}

    monkeypatch.setattr(provider_runtime_public, "invoke_role_runtime_provider", _invoke)
    command = InvokeRoleProviderCommandV1(
        workspace="workspace",
        role="pm",
        prompt="plan",
        fallback_model="gpt-5.4",
        timeout=20,
    )
    result = provider_runtime_public.execute_role_provider(command)

    assert result.ok is True
    assert result.provider_kind == "openai_compat"
    assert result.payload["content"] == "ok"


def test_query_role_provider_support(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_runtime_public, "is_role_runtime_supported", lambda *_args, **_kw: True)
    monkeypatch.setattr(provider_runtime_public, "get_role_runtime_provider_kind", lambda *_args, **_kw: "kimi")
    query = QueryRoleRuntimeProviderSupportV1(workspace="workspace", role="director")

    payload = provider_runtime_public.query_role_provider_support(query, provider_id="p1", provider_cfg={"x": 1})

    assert payload["supported"] is True
    assert payload["provider_kind"] == "kimi"
    assert payload["role"] == "director"


@pytest.mark.asyncio
async def test_provider_runtime_service_facade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        provider_runtime_public,
        "execute_provider_action",
        lambda command: SimpleNamespace(ok=True, status="ok", provider_kind=command.provider_type),
    )
    monkeypatch.setattr(
        provider_runtime_public,
        "execute_role_provider",
        lambda command: SimpleNamespace(ok=True, status="ok", provider_kind=command.role),
    )
    service = provider_runtime_public.LlmProviderRuntimeService()

    action_result = await service.invoke_provider_action(
        InvokeProviderActionCommandV1(action="health", provider_type="openai_compat")
    )
    role_result = await service.invoke_role_provider(
        InvokeRoleProviderCommandV1(
            workspace="workspace",
            role="director",
            prompt="run",
            fallback_model="gpt-5.4",
            timeout=10,
        )
    )

    assert action_result.ok is True
    assert role_result.provider_kind == "director"


@dataclass(frozen=True)
class _FakeProviderContext:
    provider_type: str
    provider_cfg: dict[str, object]


def test_resolve_provider_context_contract_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def _resolve(**_: object) -> _FakeProviderContext:
        return _FakeProviderContext(provider_type="openai_compat", provider_cfg={"model": "gpt-5.4"})

    monkeypatch.setattr(
        "polaris.cells.llm.provider_config.public.service.resolve_provider_request_context",
        _resolve,
    )
    command = ResolveProviderContextCommandV1(workspace="workspace", provider_id="p1")
    result = provider_config_public.resolve_provider_context_contract(object(), command)

    assert result.ok is True
    assert result.provider_type == "openai_compat"
    assert result.provider_cfg["model"] == "gpt-5.4"


def test_resolve_provider_context_contract_maps_domain_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contract absorbs domain exception and returns failed result."""
    from polaris.cells.llm.provider_config.public.contracts import ProviderNotFoundError

    def _raise_domain(**_: object) -> object:
        raise ProviderNotFoundError("invalid provider config")

    monkeypatch.setattr(
        "polaris.cells.llm.provider_config.public.service.resolve_provider_request_context",
        _raise_domain,
    )
    command = ResolveProviderContextCommandV1(workspace="workspace", provider_id="p1")
    result = provider_config_public.resolve_provider_context_contract(object(), command)

    assert result.ok is False
    assert result.error_code == "provider_not_found"
    assert "invalid provider config" in str(result.error_message)


@pytest.mark.asyncio
async def test_provider_config_service_facade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        provider_config_public,
        "resolve_provider_context_contract",
        lambda _settings, command: {"provider_id": command.provider_id, "ok": True},
    )
    monkeypatch.setattr(
        provider_config_public,
        "resolve_test_execution_context_contract",
        lambda _settings, _command: {"role": "pm", "model": "gpt-5.4"},
    )
    captured: dict[str, object] = {}

    def _sync(_settings: object, payload: dict[str, object]) -> None:
        captured["payload"] = payload

    monkeypatch.setattr(provider_config_public, "sync_settings_from_llm", _sync)
    service = provider_config_public.LlmProviderConfigService(settings=object())

    provider_result = await service.resolve_provider_context(
        ResolveProviderContextCommandV1(workspace="workspace", provider_id="provider-x")
    )
    test_result = await service.resolve_test_context(
        ResolveLlmTestExecutionContextCommandV1(workspace="workspace", payload={"role": "pm"})
    )
    service.sync_settings(SyncSettingsFromLlmCommandV1(workspace="workspace", llm_config={"providers": {}}))

    assert provider_result["provider_id"] == "provider-x"
    assert test_result["model"] == "gpt-5.4"
    assert captured["payload"] == {"providers": {}}


@pytest.mark.asyncio
async def test_dialogue_service_role_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _role_response(**_: object) -> dict[str, object]:
        return {"response": "hello", "source": "mock"}

    monkeypatch.setattr(dialogue_public, "generate_role_response", _role_response)
    monkeypatch.setattr(dialogue_public, "validate_and_parse_role_output", lambda _role, _output: {"valid": True})

    service = dialogue_public.LlmDialogueService(settings=object())
    result = await service.invoke_role_dialogue(
        InvokeRoleDialogueCommandV1(workspace="workspace", role="pm", message="hi")
    )
    parsed = service.validate_role_output(ValidateRoleOutputQueryV1(role="pm", output='{"task":"ok"}'))

    assert result.ok is True
    assert result.content == "hello"
    assert parsed["valid"] is True


@pytest.mark.asyncio
async def test_dialogue_service_docs_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raise_docs(**_: object) -> dict[str, object]:
        raise RuntimeError("docs failure")

    monkeypatch.setattr(dialogue_public, "generate_dialogue_turn", _raise_docs)
    service = dialogue_public.LlmDialogueService(settings=object())
    result = await service.invoke_docs_dialogue(
        InvokeDocsDialogueCommandV1(workspace="workspace", message="help", fields={}, state={})
    )

    assert result.ok is False
    assert result.error_code == "docs_dialogue_error"
    assert "docs failure" in str(result.error_message)
