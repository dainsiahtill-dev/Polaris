from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.audit.evidence.internal.task_audit_llm_binding import (
    AuditLLMBindingConfig,
    bind_audit_llm_to_task_service,
    make_audit_llm_caller,
)
from polaris.cells.llm.provider_runtime.internal.runtime_invoke import RuntimeProviderInvokeResult


def _settings(**overrides):
    base = {
        "workspace": ".",
        "ramdisk_root": "",
        "model": "glm-4.7-flash:latest",
        "audit_llm_enabled": True,
        "audit_llm_role": "qa",
        "audit_llm_timeout": 180,
        "audit_llm_prefer_local_ollama": True,
        "audit_llm_allow_remote_fallback": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_make_audit_llm_caller_prefers_local_ollama(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def _fake_invoke(**kwargs):
        calls.append(kwargs)
        return RuntimeProviderInvokeResult(
            attempted=True,
            ok=True,
            output='{"acceptance":"PASS","summary":"ok","findings":[]}',
            provider_id="ollama",
            provider_type="ollama",
            model="glm-4.7-flash:latest",
        )

    monkeypatch.setattr(
        "polaris.cells.audit.evidence.internal.task_audit_llm_binding.invoke_role_runtime_provider", _fake_invoke
    )
    monkeypatch.setattr(
        "polaris.cells.audit.evidence.internal.task_audit_llm_binding._resolve_non_local_provider_types",
        lambda _workspace, _settings: {"openai_compat"},
    )

    caller = make_audit_llm_caller(
        workspace=str(tmp_path),
        settings=_settings(),
        config=AuditLLMBindingConfig(
            enabled=True,
            role_id="qa",
            timeout_seconds=120,
            prefer_local_ollama=True,
            allow_remote_fallback=True,
            fallback_model="glm-4.7-flash:latest",
        ),
    )
    output, provider_info = caller("qa", "audit prompt")

    assert output.startswith("{")
    assert len(calls) == 1
    assert calls[0]["blocked_provider_types"] == ("openai_compat",)
    assert provider_info["llm_strategy"] == "local_ollama"
    assert provider_info["court_role_name"] == "门下侍中"


def test_make_audit_llm_caller_falls_back_to_role_runtime(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def _fake_invoke(**kwargs):
        calls.append(kwargs)
        if kwargs.get("blocked_provider_types"):
            return RuntimeProviderInvokeResult(
                attempted=False,
                ok=False,
                output="",
                provider_id="openai_compat",
                provider_type="openai_compat",
                model="gpt-4o",
            )
        return RuntimeProviderInvokeResult(
            attempted=True,
            ok=True,
            output='{"acceptance":"PASS","summary":"fallback","findings":[]}',
            provider_id="openai_compat",
            provider_type="openai_compat",
            model="gpt-4o",
        )

    monkeypatch.setattr(
        "polaris.cells.audit.evidence.internal.task_audit_llm_binding.invoke_role_runtime_provider", _fake_invoke
    )
    monkeypatch.setattr(
        "polaris.cells.audit.evidence.internal.task_audit_llm_binding._resolve_non_local_provider_types",
        lambda _workspace, _settings: {"openai_compat"},
    )

    caller = make_audit_llm_caller(
        workspace=str(tmp_path),
        settings=_settings(),
        config=AuditLLMBindingConfig(
            enabled=True,
            role_id="qa",
            timeout_seconds=120,
            prefer_local_ollama=True,
            allow_remote_fallback=True,
            fallback_model="glm-4.7-flash:latest",
        ),
    )
    output, provider_info = caller("qa", "audit prompt")

    assert output.startswith("{")
    assert len(calls) == 2
    assert provider_info["llm_strategy"] == "role_runtime_fallback"
    assert provider_info["llm_provider_type"] == "openai_compat"


def test_make_audit_llm_caller_local_only_returns_inconclusive_payload(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []

    def _fake_invoke(**kwargs):
        calls.append(kwargs)
        return RuntimeProviderInvokeResult(
            attempted=False,
            ok=False,
            output="",
            provider_id="openai_compat",
            provider_type="openai_compat",
            model="gpt-4o",
        )

    monkeypatch.setattr(
        "polaris.cells.audit.evidence.internal.task_audit_llm_binding.invoke_role_runtime_provider", _fake_invoke
    )
    monkeypatch.setattr(
        "polaris.cells.audit.evidence.internal.task_audit_llm_binding._resolve_non_local_provider_types",
        lambda _workspace, _settings: {"openai_compat"},
    )

    caller = make_audit_llm_caller(
        workspace=str(tmp_path),
        settings=_settings(audit_llm_allow_remote_fallback=False),
        config=AuditLLMBindingConfig(
            enabled=True,
            role_id="qa",
            timeout_seconds=120,
            prefer_local_ollama=True,
            allow_remote_fallback=False,
            fallback_model="glm-4.7-flash:latest",
        ),
    )
    output, provider_info = caller("qa", "audit prompt")

    assert output == ""
    assert len(calls) == 1
    assert provider_info["llm_strategy"] == "local_ollama_only"


def test_bind_audit_llm_to_task_service(monkeypatch, tmp_path) -> None:
    class _TaskService:
        def __init__(self) -> None:
            self.caller = None

        def set_audit_llm_caller(self, llm_caller):
            self.caller = llm_caller

    disabled_service = _TaskService()
    disabled = bind_audit_llm_to_task_service(
        task_service=disabled_service,
        settings=_settings(audit_llm_enabled=False),
        workspace=str(tmp_path),
    )
    assert disabled is False
    assert disabled_service.caller is None

    enabled_service = _TaskService()
    sentinel = object()
    monkeypatch.setattr(
        "polaris.cells.audit.evidence.internal.task_audit_llm_binding.make_audit_llm_caller",
        lambda **_kwargs: sentinel,
    )
    enabled = bind_audit_llm_to_task_service(
        task_service=enabled_service,
        settings=_settings(audit_llm_enabled=True),
        workspace=str(tmp_path),
    )
    assert enabled is True
    assert enabled_service.caller is sentinel
