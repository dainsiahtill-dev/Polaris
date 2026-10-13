"""Independent QA must observe a fresh owned repair, never replay a stale verdict."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor import (
    OrchestrationStageExecutor,
    _mixin_03 as stage,
)


def _authority(passed: bool, present: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        qa_verdict_present=present,
        qa_verdict_passed=passed,
        quality_stage_authorized=passed,
        task_boundary_completed_verified=True,
        sequence_barrier_satisfied=True,
        evidence_policy_passed=True,
        recovered_runtime_task_ids=(),
        reason_code="completed_verified" if passed else "qa_verdict_failed",
    )


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, dict[str, Any]]:
    executor = OrchestrationStageExecutor(tmp_path)
    run = FactoryRun(
        id="factory-qa",
        config=FactoryConfig(name="QA"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-13T00:00:00+00:00",
    )
    state: dict[str, Any] = {
        "passed": False,
        "present": False,
        "calls": 0,
        "commits": [],
        "repairs": 0,
        "bindings": [],
        "mints": 0,
    }

    def mint(_role: str) -> object:
        state["mints"] += 1
        return object()

    port = SimpleNamespace(mint_authority_binding=mint)
    monkeypatch.setattr(executor, "_factory_role_evidence_cutoff_port", lambda _context: port)

    async def authorized(_port: Any, _role: str, callback: Any, **_kwargs: Any) -> Any:
        state["bindings"].append(_kwargs.get("authority_binding"))
        return await callback()

    monkeypatch.setattr(executor, "_call_with_factory_role_evidence_authority", authorized)
    monkeypatch.setattr(executor, "_canonical_qa_commit_identity", lambda **_kwargs: ("TASK-1", "director-child"))
    monkeypatch.setattr(executor, "_canonical_factory_projection", lambda *_args: {})
    monkeypatch.setattr(
        stage.helpers,
        "evaluate_canonical_factory_authority",
        lambda _projection: _authority(state["passed"], state["present"]),
    )

    async def wait_authority(*_args: Any) -> Any:
        return _authority(state["passed"], state["present"])

    monkeypatch.setattr(executor, "_wait_for_canonical_quality_authority", wait_authority)
    monkeypatch.setattr(
        executor, "_reconcile_verified_runtime_delivery", lambda **_kwargs: {"success": True, "reconciled_task_ids": []}
    )
    monkeypatch.setattr(executor, "_quality_gate_requires_llm_judgement", lambda _context: True)

    async def workspace_checks(*_args: Any) -> tuple[bool, str]:
        executor._write_json_artifact("runtime/qa/workspace-validation.json", {"passed": True})
        return True, "runtime/qa/workspace-validation.json"

    monkeypatch.setattr(executor, "_run_workspace_quality_checks", workspace_checks)

    class Service:
        async def execute_qa_run(self, **_kwargs: Any) -> Any:
            state["calls"] += 1
            return SimpleNamespace(run_id=f"qa-{state['calls']}", status="running", message="started")

    monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: Service())

    async def wait(_service: Any, result: Any, **_kwargs: Any) -> Any:
        payload = {
            "source": "qa_adapter_v3",
            "passed": state["calls"] > 1,
            "verdict": "PASS" if state["calls"] > 1 else "FAIL",
            "critical_issues": [] if state["calls"] > 1 else ["src/a.py: wrong behavior"],
            "major_issues": [],
            "generated_at": f"revision-{state['calls']}",
            "run_id": result.run_id,
            "task_id": "task-0-qa",
            "factory_run_id": run.id,
            "workspace": str(tmp_path),
        }
        executor._write_json_artifact("runtime/qa/report.json", payload)
        return SimpleNamespace(run_id=result.run_id, status="completed", message="reviewed")

    monkeypatch.setattr(executor, "_wait_run_completion", wait)

    async def snapshot(child_run_id: str) -> Any:
        return SimpleNamespace(
            run_id=child_run_id,
            workspace=str(tmp_path),
            tasks={
                "task-0-qa": SimpleNamespace(
                    task_id="task-0-qa", role_id="qa", status="completed" if state["calls"] > 1 else "failed"
                )
            },
        )

    async def service() -> Any:
        return SimpleNamespace(query_run=snapshot)

    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.public.service.get_orchestration_service", service
    )

    async def commit(**kwargs: Any) -> dict[str, Any]:
        payload = kwargs["qa_payload"]
        state["commits"].append(payload["verdict"])
        state["passed"], state["present"] = payload["passed"], True
        return {"success": True}

    monkeypatch.setattr(executor, "_commit_qa_role_report_authority", commit)
    return executor, run, state


@pytest.mark.asyncio
async def test_failed_independent_qa_reverifies_owned_repair_before_fresh_qa(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, run, state = _fixture(tmp_path, monkeypatch)

    async def repair(**kwargs: Any) -> Any:
        from polaris.cells.factory.pipeline.internal.factory_qa_recovery import QaRepairReverification

        state["repairs"] += 1
        assert state["commits"] == ["FAIL"]
        return QaRepairReverification(str(tmp_path), run.id, kwargs["report_hash"], ("TASK-1",), ("real-effect",))

    monkeypatch.setattr(executor, "_repair_qa_role_findings", repair, raising=False)
    result = await executor._execute_quality_gate(run, {})
    assert result.status == "success", result.output
    assert state["calls"] == 2
    assert state["repairs"] == 1
    assert state["commits"] == ["FAIL", "PASS"]
    assert state["mints"] == 1
    assert state["bindings"][0] is state["bindings"][1]


@pytest.mark.asyncio
async def test_old_report_after_repair_cannot_commit_a_new_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, run, state = _fixture(tmp_path, monkeypatch)
    original_wait = executor._wait_run_completion

    async def wait(service: Any, result: Any, **kwargs: Any) -> Any:
        if state["calls"] == 1:
            return await original_wait(service, result, **kwargs)
        return SimpleNamespace(run_id=result.run_id, status="completed", message="no new report")

    async def repair(**kwargs: Any) -> Any:
        from polaris.cells.factory.pipeline.internal.factory_qa_recovery import QaRepairReverification

        state["repairs"] += 1
        return QaRepairReverification(str(tmp_path), run.id, kwargs["report_hash"], ("TASK-1",), ("real-effect",))

    monkeypatch.setattr(executor, "_wait_run_completion", wait)
    monkeypatch.setattr(executor, "_repair_qa_role_findings", repair, raising=False)
    result = await executor._execute_quality_gate(run, {})
    assert result.status == "failed"
    assert "stale" in result.output
    assert state["calls"] == 2
    assert state["commits"] == ["FAIL"]


@pytest.mark.asyncio
async def test_failed_qa_without_owned_effect_stops_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, run, state = _fixture(tmp_path, monkeypatch)

    async def repair(**_kwargs: Any) -> Any:
        state["repairs"] += 1
        return None

    monkeypatch.setattr(executor, "_repair_qa_role_findings", repair, raising=False)
    result = await executor._execute_quality_gate(run, {})
    assert result.status == "failed"
    assert state["calls"] == 1
    assert state["repairs"] == 1
    assert state["commits"] == ["FAIL"]


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["missing_identity", "foreign_task", "foreign_run", "foreign_workspace"])
async def test_fresh_report_identity_must_match_actual_qa_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    executor, run, state = _fixture(tmp_path, monkeypatch)
    original_wait = executor._wait_run_completion

    async def wait(service: Any, result: Any, **kwargs: Any) -> Any:
        final = await original_wait(service, result, **kwargs)
        if state["calls"] == 2:
            path = executor._artifact_path("runtime/qa/report.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if attack == "missing_identity":
                for key in ("task_id", "run_id", "workspace", "factory_run_id"):
                    payload.pop(key)
            else:
                payload[
                    {"foreign_task": "task_id", "foreign_run": "run_id", "foreign_workspace": "workspace"}[attack]
                ] = "foreign"
            executor._write_json_artifact("runtime/qa/report.json", payload)
        return final

    async def repair(**kwargs: Any) -> Any:
        from polaris.cells.factory.pipeline.internal.factory_qa_recovery import QaRepairReverification

        return QaRepairReverification(str(tmp_path), run.id, kwargs["report_hash"], ("TASK-1",), ("real-effect",))

    monkeypatch.setattr(executor, "_wait_run_completion", wait)
    monkeypatch.setattr(executor, "_repair_qa_role_findings", repair)
    result = await executor._execute_quality_gate(run, {})
    assert result.status == "failed"
    assert state["calls"] == 2
    assert state["commits"] == ["FAIL"]


@pytest.mark.asyncio
async def test_provider_failure_report_is_not_an_artifact_repair_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, run, state = _fixture(tmp_path, monkeypatch)
    original_wait = executor._wait_run_completion

    async def wait(service: Any, result: Any, **kwargs: Any) -> Any:
        final = await original_wait(service, result, **kwargs)
        path = executor._artifact_path("runtime/qa/report.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["critical_issues"] = ["qa_runtime_exception"]
        payload["evidence"] = ["qa_runtime_exception=authentication failed"]
        executor._write_json_artifact("runtime/qa/report.json", payload)
        return final

    async def repair(**_kwargs: Any) -> Any:
        pytest.fail("provider failures must not dispatch artifact repair")

    monkeypatch.setattr(executor, "_wait_run_completion", wait)
    monkeypatch.setattr(executor, "_repair_qa_role_findings", repair, raising=False)
    result = await executor._execute_quality_gate(run, {})
    assert result.status == "failed"
    assert state["calls"] == 1


@pytest.mark.asyncio
async def test_preexisting_report_cannot_become_a_new_initial_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor, run, state = _fixture(tmp_path, monkeypatch)
    executor._write_json_artifact(
        "runtime/qa/report.json",
        {
            "source": "qa_adapter_v3",
            "passed": True,
            "verdict": "PASS",
            "run_id": "qa-1",
            "task_id": "task-0-qa",
            "workspace": str(tmp_path),
            "factory_run_id": run.id,
        },
    )

    async def wait(_service: Any, result: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(run_id=result.run_id, status="completed", message="old file left intact")

    monkeypatch.setattr(executor, "_wait_run_completion", wait)
    result = await executor._execute_quality_gate(run, {})
    assert result.status == "failed"
    assert state["commits"] == []
