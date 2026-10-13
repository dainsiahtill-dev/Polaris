"""QA host lineage and required artifacts remain independent of model prose."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl as quality
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.internal.native_validation_session import NativeValidationSession
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import (  # noqa: F401
    _projection,
    _real_effect,
    authored,
)
from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
    DirectorQualityRepairCandidateGuard,
)
from polaris.cells.roles.adapters.internal.qa_adapter import QAAdapter


def test_qa_report_identity_comes_from_host_context(tmp_path: Path) -> None:
    adapter = QAAdapter(workspace=str(tmp_path))
    report = adapter._write_qa_report(
        review_type="quality_gate",
        target="Current artifacts",
        raw_output="review",
        task_id="task-0-qa",
        run_id="qa-current",
        context={"metadata": {"factory_run_id": "factory-current"}},
        review_result={
            "verdict": "FAIL",
            "passed": False,
            "critical_issues": ["src/a.py: wrong behavior"],
            "task_id": "foreign",
            "run_id": "foreign",
            "workspace": "foreign",
        },
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert {key: payload[key] for key in ("task_id", "run_id", "factory_run_id", "workspace")} == {
        "task_id": "task-0-qa",
        "run_id": "qa-current",
        "factory_run_id": "factory-current",
        "workspace": str(tmp_path),
    }
    assert payload["critical_issues"] == ["src/a.py: wrong behavior"]


@pytest.mark.parametrize("required", [None, (), ("src/required.py",)])
def test_qa_required_target_closure_does_not_promote_optional_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    required: tuple[str, ...] | None,
) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    task = {
        "id": "TASK-1",
        "schema_version": "pm.task_intent.v1",
        "product_goal": "Deliver library",
        "required_requirements": [],
        "product_acceptance": [],
        "acceptance": [],
        "metadata": {"planning_contract_version": "pm.task_intent.v1", "topology_authority": "chief_engineer"},
    }
    target_files = ("src/required.py", "docs/optional.md")
    monkeypatch.setattr(
        "polaris.cells.chief_engineer.blueprint.public.get_blueprint_status",
        lambda _query: SimpleNamespace(status="generated", target_files=target_files, required_write_targets=required),
    )
    assert executor._qa_delivery_targets([task], run_id="factory-current") == list(target_files)
    expected = target_files if required is None else required
    assert executor._qa_delivery_targets([task], run_id="factory-current", required_only=True) == list(expected)


@pytest.mark.asyncio
async def test_real_canonical_qa_retains_fail_and_appends_new_independent_pass(
    tmp_path: Path,
    authored: tuple[Any, Any, Any],  # noqa: F811
) -> None:
    from polaris.cells.control_plane.run_ledger.public import (
        AppendRunLedgerEventCommandV1,
        ReadRunLedgerProjectionQueryV1,
        append_run_ledger_event,
        evaluate_task_boundary_verdict,
        read_run_ledger_projection,
    )
    from polaris.cells.events.fact_stream.public import (
        BootstrapFactStreamWorkspaceCommandV1,
        QueryFactEventsV1,
        bootstrap_fact_stream_workspace,
        query_fact_events,
    )
    from polaris.cells.qa.audit_verdict.public import CommitQaRoleVerdictCommandV1, commit_qa_role_verdict

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("execution.control_plane", "task_runtime.execution"),
            maintenance_reason="qa_lineage_test",
        )
    )
    portfolio, contract, _fs = authored
    executor = OrchestrationStageExecutor(tmp_path)
    adapter = QAAdapter(workspace=str(tmp_path))
    boundary = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="DEO-LIFECYCLE",
        run_id="run",
        target_files=["src/a.py"],
        completed_artifacts=["src/a.py"],
    )
    assert boundary.ok is True
    token = {
        "token_id": "read-only-test-lineage",
        "run_id": "run",
        "task_id": "DEO-LIFECYCLE",
        "project_id": contract.project_id,
        "capability_audit": {"ok": True, "issues": []},
        "gate_policy": {},
    }
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run",
            event={
                "event_type": "task_boundary_verdict",
                "task_id": "DEO-LIFECYCLE",
                "run_id": "run",
                "task_boundary_verdict": boundary.to_dict(),
                "job_token": token,
            },
        )
    )
    outcomes = []
    for ordinal in (1, 2):
        if ordinal == 2:
            session = NativeValidationSession(
                workspace=tmp_path,
                project_id=contract.project_id,
                run_id="run",
                completion_contract_hash=contract.contract_hash,
            )
            session.before_repair()
            guard = await DirectorQualityRepairCandidateGuard.capture(
                workspace=tmp_path,
                candidate_id="qa-ledger-revision",
                target_files=["src/a.py"],
            )
            attempt, effects = await _real_effect(tmp_path)
            await guard.seal_effect()
            pending = {
                "task_id": "DEO-LIFECYCLE",
                "task_row_id": attempt.task_id,
                "execution_attempt": attempt,
                "task_completion_projection": _projection(portfolio, contract),
                "mutation_committed": True,
                "candidate_guard": guard,
            }
            try:
                session.candidate(pending=pending, results=effects)
                verified = await session.run_command(
                    [
                        "/usr/bin/python3",
                        "-c",
                        "from pathlib import Path; assert Path('src/a.py').read_text(encoding='utf-8') == 'after\\n'",
                    ],
                    10.0,
                )
                assert verified["passed"] is True and verified["exit_code"] == 0
                settled = await quality._settle_pending_workspace_quality_repair_attempt(
                    executor,
                    pending,
                    accepted=True,
                    reason="real_qa_ledger_revision_test",
                )
                assert settled is not None and settled["outcome"] == "completed"
            finally:
                session.close()
        # Independent QA observes current source after the effect/verifier;
        # neither the candidate provider nor its claimed success sets PASS.
        passed = (tmp_path / "src/a.py").read_text(encoding="utf-8") == "after\n"
        report = adapter._write_qa_report(
            review_type="quality_gate",
            target="Current src/a.py behavior",
            raw_output="independent observation",
            task_id="task-0-qa",
            run_id=f"qa-{ordinal}",
            context={"factory_run_id": "run"},
            review_result={
                "verdict": "PASS" if passed else "FAIL",
                "passed": passed,
                "score": 100 if passed else 0,
                "critical_issues": [] if passed else ["src/a.py: incorrect behavior"],
            },
        )
        report_payload = json.loads(report.read_text(encoding="utf-8"))
        report_hash = hashlib.sha256(
            json.dumps(report_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        outcome = commit_qa_role_verdict(
            CommitQaRoleVerdictCommandV1(
                workspace=str(tmp_path),
                task_id="DEO-LIFECYCLE",
                run_id="run",
                verdict="PASS" if passed else "FAIL",
                passed=passed,
                score=100 if passed else 0,
                findings=() if passed else ("src/a.py: incorrect answer",),
                target_files=("src/a.py",),
                report_ref="runtime/qa/report.json",
                report_content_hash=report_hash,
                job_token=token,
            )
        )
        assert outcome.metadata["qa_verdict_committed"] is True
        outcomes.append(outcome)
    assert [item.verdict for item in outcomes] == ["FAIL", "PASS"]
    facts = query_fact_events(
        QueryFactEventsV1(
            workspace=str(tmp_path),
            stream="execution.control_plane",
            run_id="run",
            strict_integrity=True,
        )
    )
    verdict_events = [
        fact["payload"]["event"]
        for fact in facts.events
        if fact["payload"]["event"].get("gate", {}).get("name") == "qa_verdict"
    ]
    assert [event["gate"]["ok"] for event in verdict_events] == [False, True]
    assert verdict_events[0]["append_id"] != verdict_events[1]["append_id"]
    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id="run",
        )
    ).projection
    qa_gates = [gate for gate in projection["run_projection"]["effective_gates"] if gate["name"] == "qa_verdict"]
    assert len(qa_gates) == 1 and qa_gates[0]["ok"] is True
    assert qa_gates[0]["gate_revision"] == 2
    assert qa_gates[0]["supersedes_content_id"] == verdict_events[0]["content_id"]

    # A different host task/run cannot revise this epoch's QA obligation.
    for foreign_run, foreign_task in (("run", "FOREIGN"), ("foreign-run", "DEO-LIFECYCLE")):
        foreign_token = {**token, "run_id": foreign_run, "task_id": foreign_task}
        rejected = commit_qa_role_verdict(
            CommitQaRoleVerdictCommandV1(
                workspace=str(tmp_path),
                task_id=foreign_task,
                run_id=foreign_run,
                verdict="PASS",
                passed=True,
                report_content_hash=report_hash,
                job_token=foreign_token,
            )
        )
        assert rejected.verdict != "PASS"
    unchanged = read_run_ledger_projection(ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run"))
    current = [
        gate
        for gate in unchanged.projection["run_projection"]["effective_gates"]
        if gate["name"] == "qa_verdict" and gate["task_id"] == "DEO-LIFECYCLE"
    ]
    assert len(current) == 1 and current[0]["gate_revision"] == 2 and current[0]["ok"] is True

    # A stale PASS observation cannot override a newly failed physical epoch.
    _fs.workspace_remove("src/a.py", missing_ok=False)
    missing = evaluate_task_boundary_verdict(
        workspace=tmp_path, task_id="DEO-LIFECYCLE", run_id="run", target_files=["src/a.py"], completed_artifacts=[]
    )
    assert missing.ok is False
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run",
            event={
                "event_type": "task_boundary_verdict",
                "task_id": "DEO-LIFECYCLE",
                "run_id": "run",
                "task_boundary_verdict": missing.to_dict(),
                "job_token": token,
            },
        )
    )
    stale = commit_qa_role_verdict(
        CommitQaRoleVerdictCommandV1(
            workspace=str(tmp_path),
            task_id="DEO-LIFECYCLE",
            run_id="run",
            verdict="PASS",
            passed=True,
            report_ref="runtime/qa/report.json",
            report_content_hash=report_hash,
            job_token=token,
        )
    )
    assert stale.verdict != "PASS"
