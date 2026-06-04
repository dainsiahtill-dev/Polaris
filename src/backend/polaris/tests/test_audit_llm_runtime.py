from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from polaris.cells.audit.evidence.internal.task_audit_llm_binding import (
    AuditLLMBindingConfig,
    bind_audit_llm_to_task_service,
    make_audit_llm_caller,
)
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1


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


def _patch_role_runtime(monkeypatch, results: list[RoleExecutionResultV1]) -> list[Any]:
    calls: list[Any] = []

    class _FakeRoleRuntimeService:
        async def execute_role_session(self, command):
            calls.append(command)
            return results.pop(0)

    monkeypatch.setattr(
        "polaris.cells.audit.evidence.internal.task_audit_llm_binding._create_role_runtime_service",
        lambda: _FakeRoleRuntimeService(),
    )
    return calls


def _runtime_result(
    *,
    ok: bool,
    output: str = "",
    provider_id: str = "",
    provider_type: str = "",
    model: str = "",
    error_message: str | None = None,
) -> RoleExecutionResultV1:
    return RoleExecutionResultV1(
        ok=ok,
        status="ok" if ok else "failed",
        role="qa",
        workspace=".",
        output=output,
        metadata={
            "provider": provider_id,
            "provider_type": provider_type,
            "model": model,
            "elapsed_ms": 25,
        },
        error_message=error_message,
    )


def test_make_audit_llm_caller_prefers_local_ollama(monkeypatch, tmp_path) -> None:
    calls = _patch_role_runtime(
        monkeypatch,
        [
            _runtime_result(
                ok=True,
                output='{"acceptance":"PASS","summary":"ok","findings":[]}',
                provider_id="ollama",
                provider_type="ollama",
                model="glm-4.7-flash:latest",
            )
        ],
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
    command = calls[0]
    assert command.metadata["blocked_provider_types"] == ("openai_compat",)
    assert command.metadata["role_runtime_required"] is True
    assert command.metadata["cognitive_runtime_required"] is True
    assert command.metadata["context_os_expected"] is True
    assert provider_info["llm_strategy"] == "local_ollama"
    assert provider_info["court_role_name"] == "QA"


def test_make_audit_llm_caller_falls_back_to_role_runtime(monkeypatch, tmp_path) -> None:
    calls = _patch_role_runtime(
        monkeypatch,
        [
            _runtime_result(
                ok=False,
                provider_id="remote",
                provider_type="openai_compat",
                model="gpt-4o",
                error_message="provider_type_blocked:openai_compat",
            ),
            _runtime_result(
                ok=True,
                output='{"acceptance":"PASS","summary":"fallback","findings":[]}',
                provider_id="remote",
                provider_type="openai_compat",
                model="gpt-4o",
            ),
        ],
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
    assert calls[0].metadata["blocked_provider_types"] == ("openai_compat",)
    assert "blocked_provider_types" not in calls[1].metadata
    assert provider_info["llm_strategy"] == "role_runtime_fallback"
    assert provider_info["llm_provider_type"] == "openai_compat"


def test_make_audit_llm_caller_local_only_returns_inconclusive_payload(monkeypatch, tmp_path) -> None:
    calls = _patch_role_runtime(
        monkeypatch,
        [
            _runtime_result(
                ok=False,
                provider_id="remote",
                provider_type="openai_compat",
                model="gpt-4o",
                error_message="provider_type_blocked:openai_compat",
            )
        ],
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
