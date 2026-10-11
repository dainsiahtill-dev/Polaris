"""Real PM planning cannot be substituted by fixtures, probes or recovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal import factory_stage_executor as executor_module
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.tests._characterization_helpers import _factory_stage_context
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter


@pytest.fixture(autouse=True)
def project_local_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate these fixtures from HTTP app tests that opt into an external cache."""
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", "")
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", "")
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "false")
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="pol62_project_local_runtime_fixture",
        )
    )


def _run(metadata: dict[str, Any] | None = None) -> FactoryRun:
    return FactoryRun(
        id="factory-pol62",
        config=FactoryConfig(name="project-pol62"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-09T00:00:00+00:00",
        metadata={"factory_start_request": {"metadata": metadata or {}}},
    )


@pytest.mark.parametrize("context", [{}, {"metadata": {"factory_bench_project_id": "P1"}}])
def test_bench_metadata_alone_does_not_replace_pm_planning(context: dict[str, Any]) -> None:
    metadata = OrchestrationStageExecutor._pm_deterministic_contract_metadata_for_context(
        _run({"factory_bench_project_id": "P1", "factory_bench_level": 1}), context
    )
    assert metadata.get("deterministic_pm_contracts") is not True
    assert metadata.get("pm_route_audit_probe") is not True


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["", "{bad", '{"tasks":[]}', '{"tasks":[{"title":"Do work"}]}'])
async def test_bad_planning_output_cannot_synthesize_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))

    async def response(_message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"response": output}

    monkeypatch.setattr(adapter, "_call_role_llm", response)
    result = await adapter._run_pm_stage("pm-pol62", "Build a Python input parser with tests and README.", {}, {})
    assert result["success"] is False
    assert result["tasks_created"] == 0
    assert result["quality_gate"]["blocked"] is True
    assert not result.get("real_planning_qualified", False)


@pytest.mark.asyncio
async def test_route_probe_and_explicit_fixture_stay_unqualified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))

    async def response(_message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        assert context is not None and context["mode"] == "pm_task_contract_route_probe"
        return {"response": "PM acknowledged."}

    monkeypatch.setattr(adapter, "_call_role_llm", response)
    result = await adapter._run_pm_stage(
        "pm-probe",
        "Build a Python input parser with tests and README.",
        {"metadata": {"deterministic_pm_contracts": True, "pm_route_audit_probe": True}},
        {},
    )
    assert result["contract_origin"] == "synthesized_fixture"
    assert result["real_planning_qualified"] is False
    plans = list(tmp_path.glob(".polaris/runtime/tasks/plan.json"))
    assert len(plans) == 1, result
    payload = json.loads(plans[0].read_text(encoding="utf-8"))
    signals = payload["quality_gate"]["signals"]
    assert any(
        s.get("contract_origin") == "synthesized_fixture" and s.get("real_planning_qualified") is False for s in signals
    )


@pytest.mark.asyncio
async def test_pm_timeout_never_starts_deterministic_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    recovery_calls: list[str] = []

    class Service:
        async def execute_pm_run(self, **kwargs: Any) -> CommandResult:
            return CommandResult(status="running", run_id="pm-pol62")

    async def timeout(*args: Any, **kwargs: Any) -> CommandResult:
        return CommandResult(status="timeout", run_id="pm-pol62", message="Provider timeout")

    async def recovery(**kwargs: Any) -> CommandResult:
        recovery_calls.append("recovery")
        return CommandResult(status="completed", run_id="pm-synthetic")

    monkeypatch.setattr(executor_module.TaskRuntimeService, "reset_records", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(executor, "_build_orchestration_service", lambda context: Service())
    monkeypatch.setattr(executor, "_wait_run_completion", timeout)
    monkeypatch.setattr(executor, "_run_pm_planning_deterministic_recovery", recovery)
    result = await executor._execute_pm_planning(_run(), _factory_stage_context())
    assert result.status == "failed"
    assert recovery_calls == []
    assert result.metadata["real_planning_qualified"] is False


@pytest.mark.asyncio
async def test_provider_planning_path_persists_qualified_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    contract = {
        "id": "TASK-1",
        "title": "Implement input parser",
        "goal": "Parse records and reject malformed input",
        "description": "Implement and verify a deterministic input parser",
        "scope": "src/parser.py, tests/test_parser.py",
        "scope_paths": ["src/parser.py", "tests/test_parser.py"],
        "target_files": ["src/parser.py", "tests/test_parser.py"],
        "steps": ["Read the format contract", "Implement the parser", "Test valid and invalid records"],
        "acceptance": ["Valid records preserve fields", "Malformed records raise ValueError", "Unit tests pass"],
        "depends_on": [],
        "verification_commands": [
            {"modality": "environment_prep", "argv": ["python3", "-m", "pip", "check"], "cwd": "."},
            {"modality": "test", "argv": ["python3", "-m", "unittest", "discover"], "cwd": "."},
        ],
    }

    async def response(_message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        assert context is not None and context["request_purpose"] == "task_planning"
        return {"response": json.dumps({"tasks": [contract]})}

    monkeypatch.setattr(adapter, "_call_role_llm", response)
    result = await adapter._run_pm_stage("pm-planning", "Implement input parser", {}, {})
    assert result["success"] is True, result
    assert result["contract_origin"] == "provider_planning"
    assert result["real_planning_qualified"] is True
    qualification = OrchestrationStageExecutor(tmp_path)._pm_plan_origin_qualification()
    assert qualification == {"contract_origin": "provider_planning", "real_planning_qualified": True}

    class CompletedPMService:
        async def execute_pm_run(self, **kwargs: Any) -> CommandResult:
            assert kwargs["options"]["run_director"] is False
            assert not kwargs["options"].get("metadata", {}).get("deterministic_pm_contracts")
            return CommandResult(status="completed", run_id="pm-planning")

    async def completed(_service: Any, command_result: CommandResult, **kwargs: Any) -> CommandResult:
        return command_result

    executor = OrchestrationStageExecutor(tmp_path)
    monkeypatch.setattr(executor, "_build_orchestration_service", lambda context: CompletedPMService())
    monkeypatch.setattr(executor, "_wait_run_completion", completed)
    stage_result = await executor._execute_pm_planning(_run(), _factory_stage_context())
    assert stage_result.status == "success", stage_result
    assert stage_result.metadata["contract_origin"] == "provider_planning"
    assert stage_result.metadata["real_planning_qualified"] is True


@pytest.mark.parametrize("payload", [{}, {"quality_gate": {"score": 100, "critical_issue_count": 0, "signals": []}}])
def test_old_or_missing_origin_plan_is_unqualified(tmp_path: Path, payload: dict[str, Any]) -> None:
    path = tmp_path / ".polaris/runtime/tasks/plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    qualification = OrchestrationStageExecutor(tmp_path)._pm_plan_origin_qualification()
    assert qualification["real_planning_qualified"] is False


@pytest.mark.asyncio
async def test_retained_recovery_seam_cannot_execute_or_mint_run(tmp_path: Path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    result = await executor._run_pm_planning_deterministic_recovery(
        service=object(),
        planning_directive="Build parser",
        context={},
        abort_checker=None,
        authority_port=cast(Any, None),
    )
    assert result.status == "failed"
    assert result.run_id == ""
    assert result.message == "pm_deterministic_timeout_recovery_forbidden"


@pytest.mark.parametrize("mismatch", ["", "workspace", "factory_run_id", "project_id", "missing"])
def test_runner_consumes_only_scoped_server_route_qualification(tmp_path: Path, mismatch: str) -> None:
    from polaris.cells.factory.pipeline.internal.bench_gates._gates import build_llm_route_audit

    qualification = {
        "schema_version": "roles.kernel.factory_provider_route_qualification.v1",
        "source": "roles.kernel.canonical_physical_attempt",
        "workspace": str(tmp_path),
        "factory_run_id": "factory-own",
        "project_id": "P1",
        "ok": True,
        "roles": {role: {"ok": True, "all_routes_ok": True} for role in ("pm", "chief_engineer", "director", "qa")},
    }
    if mismatch and mismatch != "missing":
        qualification[mismatch] = "foreign"
    result = build_llm_route_audit(
        [],
        server_qualification=None if mismatch == "missing" else qualification,
        workspace=str(tmp_path),
        factory_run_id="factory-own",
        project_id="P1",
    )
    assert result["ok"] is (not mismatch)


def test_scoped_gate_requires_independent_complete_director_coverage(tmp_path: Path) -> None:
    from polaris.cells.factory.pipeline.internal.bench_gates._gates import build_llm_route_audit

    evidence = {
        "schema_version": "roles.kernel.factory_provider_route_qualification.v1",
        "source": "roles.kernel.canonical_physical_attempt",
        "workspace": str(tmp_path),
        "factory_run_id": "F1",
        "project_id": "P1",
        "ok": True,
        "roles": {"director": {"ok": True, "all_routes_ok": False}},
    }
    result = build_llm_route_audit(
        [],
        server_qualification=evidence,
        workspace=str(tmp_path),
        factory_run_id="F1",
        project_id="P1",
        required_roles=("director",),
        require_all_director_routes=True,
    )
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_actual_fanout_freezes_all_declared_bindings_before_worker_dispatch(tmp_path: Path) -> None:
    import asyncio
    import json

    from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
    from polaris.cells.factory.pipeline.tests._characterization_helpers import _characterization_authority_port
    from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
    from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
        get_factory_provider_route_obligations_json,
    )

    declarations = []

    class Service:
        async def execute_director_run(self, **kwargs):
            declarations.append(json.loads(await asyncio.to_thread(get_factory_provider_route_obligations_json)))
            return CommandResult(run_id="", status="completed", message="local fixture")

    bindings = [{"provider_id": "p1", "model": "m1", "binding_id": f"b{i}"} for i in (1, 2)]
    executor = OrchestrationStageExecutor(tmp_path)
    await executor._execute_director_binding_fanout(
        service=Service(),
        workspace=str(tmp_path),
        tasks=["T1"],
        base_options={"metadata": {"factory_run_id": "F1"}},
        bindings=bindings,
        authority_port=_characterization_authority_port(),
    )
    assert len(declarations) == 1
    assert declarations[0]["expected"] == bindings
    assert declarations[0]["skips"] == [
        {**bindings[1], "reason": "no_assigned_tasks", "source": "factory.director_dispatch"}
    ]
    assert get_factory_provider_route_obligations_json() == ""


@pytest.mark.asyncio
async def test_actual_fanout_caller_final_freeze_lifecycle_and_public_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.control_plane.run_ledger.public import AppendRunLedgerEventCommandV1, append_run_ledger_event
    from polaris.cells.events.fact_stream.public import (
        AppendFactEventCommandV1,
        BootstrapFactStreamWorkspaceCommandV1,
        append_fact_event,
        bootstrap_fact_stream_workspace,
    )
    from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
    from polaris.cells.factory.pipeline.tests._characterization_helpers import _characterization_authority_port
    from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
    from polaris.cells.roles.kernel.public import provider_attempt_lifecycle_replay as replay
    from polaris.cells.roles.kernel.tests.test_pol62_binding_qualification import (
        test_real_invoker_prepare_freeze_retains_existing_route_identity,
    )

    class Service:
        async def execute_director_run(self, **kwargs):
            await test_real_invoker_prepare_freeze_retains_existing_route_identity(
                tmp_path, monkeypatch, "invoke", "fanout"
            )
            return CommandResult(run_id="", status="completed", message="local full path")

    executor = OrchestrationStageExecutor(tmp_path)
    result = await executor._execute_director_binding_fanout(
        service=Service(),
        workspace=str(tmp_path),
        tasks=["T1"],
        base_options={"metadata": {"factory_run_id": "factory-run-1"}},
        bindings=[
            {"provider_id": "provider-a", "model": "kimi-for-coding", "binding_id": "chosen-primary"},
            {"provider_id": "provider-b", "model": "kimi-for-coding", "binding_id": "skipped-worker"},
        ],
        authority_port=_characterization_authority_port(),
    )
    assert result.status == "completed", result
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("task_runtime.execution", "execution.control_plane"),
            maintenance_reason="local_full_path",
        )
    )
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(tmp_path),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            run_id="run-1",
            task_id="T1",
            payload={"run_id": "run-1", "task_id": "T1", "factory_run_id": "factory-run-1", "project_id": "P1"},
            strict_integrity=True,
        )
    )
    for run_id in ("factory-run-1", "run-1"):
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id=run_id,
                event={
                    "event_type": "gate_evaluated",
                    "job_token": {"run_id": run_id, "project_id": "P1"},
                    "gate": {"ok": True},
                },
            )
        )
    audit = replay.query_factory_provider_route_qualification(
        replay.QueryFactoryProviderAttemptLifecycleReplayV1(
            schema_version=replay.QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
            workspace=str(tmp_path),
            factory_run_id="factory-run-1",
        ),
        project_id="P1",
        required_roles=("director",),
    )
    assert audit["ok"] is True, audit
    assert len(audit["roles"]["director"]["configured"]) == 2
    assert audit["roles"]["director"]["observed_count"] == 1
    assert audit["roles"]["director"]["degraded"] is True


def test_http_audit_client_rejects_foreign_instance_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.factory_bench import factory_http_client as client

    foreign = {
        "run_id": "factory-own",
        "role_route_qualification": {
            "schema_version": "roles.kernel.factory_provider_route_qualification.v1",
            "source": "roles.kernel.canonical_physical_attempt",
            "workspace": str(tmp_path / "foreign"),
            "factory_run_id": "factory-own",
            "ok": True,
        },
    }
    monkeypatch.setattr(client, "_http_get_json", lambda *args, **kwargs: foreign)
    result = client.get_audit_bundle("http://fixture.invalid", "factory-own", workspace=str(tmp_path))
    assert result["role_route_qualification"] is None
    assert result["role_route_qualification_error"]["error_code"] == "audit_bundle_target_scope_mismatch"
