"""Characterization tests for ``OrchestrationStageExecutor`` helper clusters.

These tests freeze the *current* behavior of the pure helpers, artifact
filesystem I/O, mirroring, package.json parsing, real-subprocess quality
command execution, the director-evidence truth tables, and the PM/text-shaping
glue BEFORE the god-class is decomposed into sibling collaborators. They exist
to guard a behavior-preserving refactor; they assert observed outputs derived
from reading the source, not idealized contracts.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import inspect
import json
import logging
import os
import shutil
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BlueprintPersistence,
    GenerateTaskBlueprintCommandV1,
    VerificationCommandAuthorityV1,
    derive_project_kind_authority_from_catalog_snapshot,
    generate_task_blueprint,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import TaskBlueprintResultV1
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.events.fact_stream.public.service import (
    QueryFactEventsV1,
    query_fact_events,
)
from polaris.cells.factory.pipeline.internal import (
    factory_stage_executor as stage_executor_module,
    factory_workspace_quality as workspace_quality_module,
)
from polaris.cells.factory.pipeline.internal.factory_deadline_policy import (
    FactoryDeadlineBudgetPolicyV1,
    FactoryDeadlineDispositionV1,
    build_task_dependency_schedule,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
)
from polaris.cells.factory.pipeline.internal.factory_run_completion import RunCompletionWaiter
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.internal.factory_settlement_consumer import _fencing_token
from polaris.cells.factory.pipeline.internal.factory_stage_helpers import (
    evaluate_canonical_factory_authority,
)
from polaris.cells.factory.pipeline.internal.run_ledger import load_run_ledger_projection
from polaris.cells.roles.adapters.public import (
    build_director_materialization_quality_repair_message,
    extract_workspace_quality_summary,
    resolve_director_semantic_quality_repair_target_files,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    ObservableTaskRowsProjectionV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_logical_path


def _characterization_authority_port() -> FactoryRoleEvidenceAuthorityPort:
    """Return an exact production port type with test-only grant behavior."""

    port = object.__new__(FactoryRoleEvidenceAuthorityPort)
    bindings: list[FactoryRoleEvidenceAuthorityBindingV1] = []
    grant_caps = {
        "architect": 1,
        "pm": 2,
        "chief_engineer": 1,
        "director": 512,
        "qa": 1,
    }

    async def acquire_cutoff(request: object) -> object:
        del request
        raise AssertionError("characterization test must not acquire cutoff")

    async def resolve_cutoff_proof(ack: object) -> object:
        del ack
        raise AssertionError("characterization test must not resolve cutoff proof")

    def reject_physical_attempt(command: object) -> object:
        raise AssertionError(command)

    def require_grant_capacity(role: str, count: int) -> None:
        assert role == "director"
        assert len(bindings) + count <= 512

    def mint_authority_binding(role: str) -> FactoryRoleEvidenceAuthorityBindingV1:
        if sum(binding.role == role for binding in bindings) >= grant_caps[role]:
            raise RuntimeError("factory_role_evidence_stage_grant_cardinality_exceeded")
        binding = FactoryRoleEvidenceAuthorityBindingV1(
            schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
            verification_scope="factory",
            factory_run_id="characterization-run",
            role=role,
            cutoff_port=port,
            physical_attempt_control_port=port,
            attempt_budget=32,
            execution_authority_hash=hashlib.sha256(f"grant-{len(bindings)}".encode()).hexdigest(),
        )
        bindings.append(binding)
        return binding

    def revoke_authority_binding(binding: FactoryRoleEvidenceAuthorityBindingV1) -> None:
        del binding

    port.acquire_cutoff = acquire_cutoff  # type: ignore[method-assign]
    port.resolve_cutoff_proof = resolve_cutoff_proof  # type: ignore[method-assign]
    port.reserve = reject_physical_attempt  # type: ignore[attr-defined]
    port.begin_start = reject_physical_attempt  # type: ignore[attr-defined]
    port.commit_started = reject_physical_attempt  # type: ignore[attr-defined]
    port.abort_reservation = reject_physical_attempt  # type: ignore[attr-defined]
    port.mark_start_ambiguous = reject_physical_attempt  # type: ignore[attr-defined]
    port.settle = reject_physical_attempt  # type: ignore[attr-defined]
    port.terminal_persistence_failed = reject_physical_attempt  # type: ignore[attr-defined]
    port.require_grant_capacity = require_grant_capacity  # type: ignore[method-assign]
    port.mint_authority_binding = mint_authority_binding  # type: ignore[method-assign]
    port.revoke_authority_binding = revoke_authority_binding  # type: ignore[method-assign]
    port._test_minted_authority_bindings = bindings  # type: ignore[attr-defined]
    return port


def _factory_stage_context(context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Explicitly bind an exact test cutoff port for one direct stage call."""

    result = dict(context or {})
    result[stage_executor_module.FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY] = _characterization_authority_port()
    return result


def _bootstrap_fact_stream_workspace(workspace: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace.resolve()),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_stage_executor_characterization_test_bootstrap",
        )
    )


@pytest.fixture(autouse=True)
def _bootstrap_real_fact_stream_workspace(request: pytest.FixtureRequest) -> None:
    """Provision FactStream before characterization tests use a real workspace."""

    if "tmp_path" not in request.fixturenames:
        return
    _bootstrap_fact_stream_workspace(Path(request.getfixturevalue("tmp_path")))


def _executor(workspace: Path) -> OrchestrationStageExecutor:
    executor = OrchestrationStageExecutor(workspace)
    catalog_snapshot = {"project_kind": "library"}
    catalog_snapshot_hash = project_completion_catalog_snapshot_hash(catalog_snapshot)

    async def _test_portfolio_authority(
        self: OrchestrationStageExecutor,
        *,
        run: FactoryRun,
        pm_tasks: list[dict[str, Any]],
        portfolio_tasks: tuple[Any, ...],
    ) -> Any:
        del self, pm_tasks
        catalog_path = workspace / ".polaris" / "catalog_contract.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(catalog_snapshot), encoding="utf-8")
        task_ids = tuple(sorted(task.task_id for task in portfolio_tasks))
        command_authority = tuple(
            VerificationCommandAuthorityV1(
                task_id=task_id,
                modality=modality,  # type: ignore[arg-type]
                argv=argv,
                cwd=".",
            )
            for task_id in task_ids
            for modality, argv in (
                ("environment_prep", ("python", "-m", "pip", "install", "-e", ".")),
                ("build", ("python", "-m", "compileall", ".")),
                ("test", ("pytest", "-q")),
                ("entrypoint", ("python", "-m", "src.main")),
            )
        )
        policy = {
            "schema_version": "evidence_policy.v1",
            "policy_hash": "b" * 64,
            "source": "control_plane.verifier_policy.evidence_policy_compiler",
            "required_evidence_modalities": ["command"],
        }
        return stage_executor_module._ChiefEngineerPortfolioAuthorityV1(
            project_id=run.config.name,
            pm_stage_event_id=f"pm-stage-{run.id}",
            pm_contract_hash="a" * 64,
            pm_task_ids=task_ids,
            catalog_snapshot=catalog_snapshot,
            catalog_snapshot_hash=catalog_snapshot_hash,
            project_kind_authority=derive_project_kind_authority_from_catalog_snapshot(
                project_id=run.config.name,
                run_id=run.id,
                pm_contract_hash="a" * 64,
                catalog_snapshot=catalog_snapshot,
                catalog_snapshot_hash=catalog_snapshot_hash,
            ),
            verifier_policy_hash="b" * 64,
            verifier_policy=policy,
            verifier_policy_snapshot_hash=project_completion_verifier_policy_snapshot_hash(policy),
            verification_command_authority=command_authority,
        )

    executor._load_chief_engineer_portfolio_authority = MethodType(  # type: ignore[method-assign]
        _test_portfolio_authority,
        executor,
    )
    return executor


def _library_completion_requirements(
    *target_files: str,
    owner_task_ids: tuple[str, ...],
    test_path: str,
    test_owner_task_id: str,
) -> dict[str, Any]:
    assert len(owner_task_ids) == len(target_files)
    artifacts = [
        {
            "obligation_id": f"artifact-{index}",
            "path": path,
            "semantic_role": "source",
            "applicability": "required",
            "owner_task_id": owner_task_ids[index - 1],
        }
        for index, path in enumerate(target_files, start=1)
    ]
    test_artifact_id = "artifact-test"
    artifacts.append(
        {
            "obligation_id": test_artifact_id,
            "path": test_path,
            "semantic_role": "test",
            "applicability": "required",
            "owner_task_id": test_owner_task_id,
        }
    )
    build_authority = VerificationCommandAuthorityV1(
        task_id=owner_task_ids[0],
        modality="build",
        argv=("python", "-m", "compileall", "."),
    )
    test_authority = VerificationCommandAuthorityV1(
        task_id=test_owner_task_id,
        modality="test",
        argv=("pytest", "-q"),
    )
    return {
        "obligations": {
            "artifacts": artifacts,
            "entrypoints": [
                {
                    "obligation_id": "entrypoint-library-na",
                    "kind": "library",
                    "applicability": "not_applicable",
                    "owner_task_id": None,
                    "source_path": None,
                    "runtime_path": None,
                    "command": None,
                }
            ],
            "verification": [
                {
                    "obligation_id": "verify-build",
                    "modality": "build",
                    "command_authority_hash": build_authority.authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": [artifacts[0]["obligation_id"]],
                    "owner_task_id": owner_task_ids[0],
                },
                {
                    "obligation_id": "verify-test",
                    "modality": "test",
                    "command_authority_hash": test_authority.authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": [test_artifact_id],
                    "owner_task_id": test_owner_task_id,
                },
                {
                    "obligation_id": "verify-environment-na",
                    "modality": "environment_prep",
                    "command_authority_hash": None,
                    "applicability": "not_applicable",
                    "covers_obligation_ids": [],
                    "owner_task_id": None,
                },
            ],
        },
    }


def test_executor_constructor_does_not_bootstrap_fact_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pure executor construction must never provision or maintain FactStream."""

    def _fail_bootstrap(_workspace: Path) -> None:
        pytest.fail("executor construction must not bootstrap FactStream")

    monkeypatch.setitem(globals(), "_bootstrap_fact_stream_workspace", _fail_bootstrap)

    executor = _executor(Path("."))

    assert isinstance(executor, OrchestrationStageExecutor)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage",
    ("pm_planning", "chief_engineer_review", "director_dispatch", "quality_gate"),
)
async def test_direct_stage_missing_cutoff_port_fails_before_service_or_role_call(
    stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every role stage must reject missing live authority before dispatch."""

    executor = _executor(tmp_path)
    run = FactoryRun(
        id=f"missing-authority-{stage}",
        config=FactoryConfig(name="missing-authority", stages=[stage]),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-07-19T00:00:00+00:00",
    )
    service_or_role_calls: list[str] = []

    def unexpected_service(_context: dict[str, Any]) -> object:
        service_or_role_calls.append("service")
        raise AssertionError("service dispatch must not run without cutoff authority")

    class _UnexpectedRoleRuntimeService:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            service_or_role_calls.append("role")
            raise AssertionError("role dispatch must not run without cutoff authority")

    monkeypatch.setattr(executor, "_build_orchestration_service", unexpected_service)
    monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _UnexpectedRoleRuntimeService)

    with pytest.raises(RuntimeError, match=r"^factory_role_evidence_live_cutoff_port_required$"):
        await executor.execute(stage, run, {})

    assert service_or_role_calls == []


def _write_minimal_chief_engineer_plan(executor: OrchestrationStageExecutor) -> None:
    executor._write_json_artifact(
        "tasks/plan.json",
        {
            "tasks": [
                {
                    "id": "TASK-CANCEL",
                    "title": "Implement cancellation coverage",
                    "goal": "Exercise the Chief Engineer cancellation path.",
                    "target_files": ["src/cancel.py", "tests/test_cancel.py"],
                    "scope_paths": ["src/cancel.py", "tests/test_cancel.py"],
                    "acceptance_criteria": ["cancellation is observable"],
                    "execution_checklist": ["Suspend the claimed attempt"],
                }
            ]
        },
    )


def _single_task_chief_engineer_result() -> SimpleNamespace:
    payload = {
        "construction_plan": {
            "project_design_intent": "Keep cancellation behavior behind one stable module boundary.",
            "project_interface_contract": {
                "provider_declarations": [
                    {
                        "path": "src/cancel.py",
                        "name": "build_cancellation_plan",
                        "symbol_kind": "function",
                        "signature": "build_cancellation_plan() -> dict[str, object]",
                        "semantic_role": "build cancellation behavior",
                    }
                ],
                "consumer_declarations": [],
            },
            "task_plans": {
                "TASK-CANCEL": {
                    "implementation": ["Implement cancellation behavior"],
                    "verification": ["Verify cancellation behavior"],
                }
            },
        },
        "scope_for_apply": ["src/cancel.py"],
        "risk_flags": [],
        "project_completion_contract": _library_completion_requirements(
            "src/cancel.py",
            owner_task_ids=("TASK-CANCEL",),
            test_path="tests/test_cancel.py",
            test_owner_task_id="TASK-CANCEL",
        ),
    }
    return SimpleNamespace(
        ok=True,
        output=json.dumps(payload),
        error_message="",
        error_code="",
        metadata={
            "provider_id": "test-provider",
            "model": "test-model",
            "structured_output": payload,
            "final_request_context_audit": {"context_window_utilization": 0.25},
            "context_snapshot_ref": "abcdef123456abcdef123456",
        },
        usage={},
    )


def _invalid_chief_engineer_stream_result(output: str = '{"construction_plan": <invalid>}') -> SimpleNamespace:
    return SimpleNamespace(
        ok=False,
        status="failed",
        output=output,
        error_message="Output validation failed: malformed chief engineer JSON",
        error_code="output_validation_failed",
        metadata={
            "provider_id": "test-provider",
            "model": "test-model",
            "final_request_context_audit": {"context_window_utilization": 0.25},
            "context_snapshot_ref": "abcdef123456abcdef123456",
            "output_validation": {
                "success": False,
                "errors": ["malformed chief engineer JSON"],
                "suggestions": ["return one JSON object"],
                "quality_score": 0.0,
            },
        },
        usage={},
    )


def _thinking_only_chief_engineer_result() -> SimpleNamespace:
    return SimpleNamespace(
        ok=False,
        status="failed",
        output="",
        error_message="model returned thinking-only response; awaiting user clarification",
        error_code="model_thinking_only_response",
        error_category="output_contract_failure",
        metadata={
            "provider_id": "test-provider",
            "model": "test-model",
            "final_request_context_audit": {"context_window_utilization": 0.25},
            "context_snapshot_ref": "abcdef123456abcdef123456",
        },
        usage={},
    )


def _invalid_structured_transport_chief_engineer_result() -> SimpleNamespace:
    error = "structured_output_payload_schema_mismatch:$:'scope_for_apply' is a required property"
    return SimpleNamespace(
        ok=False,
        status="failed",
        output="",
        error_message=error,
        error_code="call_error",
        error_category="unknown",
        metadata={
            "provider_id": "test-provider",
            "model": "test-model",
            "final_request_context_audit": {"context_window_utilization": 0.25},
            "context_snapshot_ref": "abcdef123456abcdef123456",
        },
        usage={},
    )


def _capture_chief_engineer_lease_keepers(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Any]:
    keepers: list[Any] = []
    keeper_type = stage_executor_module._ChiefEngineerExecutionAttemptLeaseKeeper
    original_start = keeper_type.start

    def _tracked_start(keeper: Any) -> None:
        keepers.append(keeper)
        original_start(keeper)

    monkeypatch.setattr(keeper_type, "start", _tracked_start)
    return keepers


def _assert_no_chief_engineer_lease_keeper_threads() -> None:
    leaked_threads = [
        thread.name for thread in threading.enumerate() if thread.name.startswith("polaris-ce-attempt-lease-")
    ]
    assert leaked_threads == []


def _authoritative_task_projection(
    workspace: Path,
    rows: tuple[dict[str, Any], ...],
) -> ObservableTaskRowsProjectionV1:
    return ObservableTaskRowsProjectionV1(
        workspace=str(workspace),
        source="task_runtime.execution_fact",
        authoritative=True,
        degraded=False,
        rows=rows,
        readiness={"ready": True, "blocking_reasons": []},
    )


def _with_task_runtime_authority(
    projection: dict[str, Any],
    *,
    task_ids: tuple[str, ...] = ("TASK-1",),
    incomplete_task_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Attach the canonical TaskRuntime authority required by Factory gates."""

    incomplete = set(incomplete_task_ids)
    return {
        **projection,
        "task_runtime_projection": {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
            "row_count": len(task_ids),
            "rows": [
                {
                    "task_id": task_id,
                    "status": "pending" if task_id in incomplete else "completed",
                    "execution_state": ("pending" if task_id in incomplete else "completed"),
                    "fact_event_seq": index,
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                }
                for index, task_id in enumerate(task_ids, start=1)
            ],
            "readiness": {"ready": True, "blocking_reasons": []},
        },
    }


def _factory_workspace_run_lease(
    workspace: Path,
    *,
    run_id: str,
    fencing_token: int,
) -> dict[str, Any]:
    return {
        "schema_version": "factory.workspace-run-lease.v1",
        "workspace": str(workspace.resolve()),
        "run_id": run_id,
        "state": "active",
        "version": 3,
        "fencing_token": fencing_token,
        "acquired_at": "2026-07-13T00:00:00+00:00",
        "updated_at": "2026-07-13T00:01:00+00:00",
        "expires_at": "2026-07-13T01:00:00+00:00",
        "stage_execution_claim": {"nonce": "lease-projection-original"},
    }


def test_materialize_pm_task_projects_current_factory_workspace_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_fact_stream_workspace(workspace)
    executor = _executor(workspace)
    run_id = "factory-run-lease-current"
    current_lease = _factory_workspace_run_lease(
        workspace,
        run_id=run_id,
        fencing_token=41,
    )
    expected_lease = json.loads(json.dumps(current_lease, ensure_ascii=False))
    forged_task_lease = _factory_workspace_run_lease(
        workspace,
        run_id="factory-run-forged",
        fencing_token=999,
    )
    monkeypatch.setattr(
        TaskRuntimeService,
        "_publish_factory_execution_event",
        lambda _service, _payload: True,
    )
    tasks = [
        {
            "id": "PM-LEASE-1",
            "objective": "Preserve Factory workspace authority provenance",
            "metadata": {
                "factory_workspace_run_lease": forged_task_lease,
            },
        }
    ]

    summary = executor._materialize_pm_plan_taskboard(
        tasks,
        run_id=run_id,
        source_stage="pm_planning",
        run_metadata={"factory_workspace_run_lease": current_lease},
    )
    current_lease["fencing_token"] = 88
    current_lease["stage_execution_claim"]["nonce"] = "mutated-after-materialization"

    row = TaskRuntimeService(str(workspace)).get_task("PM-LEASE-1")

    assert summary["ensured_count"] == 1
    assert summary["bound_count"] == 1
    assert row is not None
    assert row["metadata"]["factory_run_id"] == run_id
    assert row["metadata"]["factory_workspace_run_lease"] == expected_lease

    refreshed_lease = _factory_workspace_run_lease(
        workspace,
        run_id=run_id,
        fencing_token=41,
    )
    refreshed_lease["version"] = 4
    refreshed_lease["updated_at"] = "2026-07-13T00:03:00+00:00"
    expected_refreshed_lease = json.loads(json.dumps(refreshed_lease, ensure_ascii=False))
    refresh_summary = executor._materialize_pm_plan_taskboard(
        tasks,
        run_id=run_id,
        source_stage="director_dispatch",
        run_metadata={"factory_workspace_run_lease": refreshed_lease},
    )
    refreshed_lease["stage_execution_claim"]["nonce"] = "mutated-after-refresh"
    row = TaskRuntimeService(str(workspace)).get_task("PM-LEASE-1")

    assert refresh_summary["binding_failures"] == []
    assert row is not None
    assert row["status"] == "pending"
    assert row["metadata"]["factory_workspace_run_lease"] == expected_refreshed_lease
    metadata_refresh_events = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="updated",
        )
    ).events
    assert len(metadata_refresh_events) == 1
    metadata_refresh_payload = metadata_refresh_events[0]["payload"]
    assert metadata_refresh_payload["status"] == "pending"
    assert metadata_refresh_payload["details"]["status"] == ""
    assert metadata_refresh_payload["details"]["metadata_updated"] is True

    task_runtime = TaskRuntimeService(str(workspace))
    claimed = task_runtime.claim_execution(
        row["id"],
        worker_id="director",
        role_id="director",
        run_id="director-child-run",
        selection_source="task_id_lookup",
    )
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claimed["execution_attempt"])
    completed = task_runtime.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary="fenced terminal fact committed",
        )
    )
    assert completed["success"] is True
    terminal_events = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="completed",
        )
    ).events
    assert len(terminal_events) == 1

    terminal_payload = terminal_events[0]["payload"]
    assert terminal_payload["factory_run_id"] == run_id
    assert _fencing_token(terminal_payload) == 41
    assert terminal_payload["factory_workspace_run_lease"] == expected_refreshed_lease


@pytest.mark.parametrize(
    "run_metadata",
    [
        None,
        {},
        {"factory_workspace_run_lease": "not-a-lease-mapping"},
    ],
)
def test_materialize_pm_task_never_trusts_task_supplied_workspace_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_metadata: dict[str, Any] | None,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_fact_stream_workspace(workspace)
    monkeypatch.setattr(
        TaskRuntimeService,
        "_publish_factory_execution_event",
        lambda _service, _payload: True,
    )

    _executor(workspace)._materialize_pm_plan_taskboard(
        [
            {
                "id": "PM-LEASE-UNTRUSTED",
                "objective": "Reject task-supplied Factory authority",
                "metadata": {
                    "factory_workspace_run_lease": {
                        "run_id": "forged-run",
                        "fencing_token": 777,
                    }
                },
            }
        ],
        run_id="factory-run-without-lease-projection",
        source_stage="pm_planning",
        run_metadata=run_metadata,
    )

    row = TaskRuntimeService(str(workspace)).get_task("PM-LEASE-UNTRUSTED")

    assert row is not None
    assert "factory_workspace_run_lease" not in row["metadata"]


@pytest.mark.parametrize(
    ("sequence_ready", "boundary_status", "qa_ok", "policy_ok", "expected", "reason_code"),
    [
        (False, "completed_verified", True, True, False, "canonical_sequence_barrier_unsatisfied"),
        (True, "incomplete_materialization", True, True, False, "task_boundary_not_completed_verified"),
        (True, "completed_verified", False, True, False, "qa_verdict_failed"),
        (True, "completed_verified", True, False, False, "evidence_policy_failed"),
        (True, "completed_verified", True, True, True, "canonical_projection_authorized"),
    ],
)
def test_canonical_factory_authority_conflict_matrix(
    sequence_ready: bool,
    boundary_status: str,
    qa_ok: bool,
    policy_ok: bool,
    expected: bool,
    reason_code: str,
) -> None:
    boundary_ok = boundary_status == "completed_verified"
    projection = _with_task_runtime_authority(
        {
            "source": "run_ledger",
            "integrity_ok": policy_ok,
            "outcome_ok": policy_ok and boundary_ok and qa_ok,
            "task_boundary": {
                "latest_by_task": {
                    "TASK-1": {
                        "task_id": "TASK-1",
                        "status": boundary_status,
                        "ok": boundary_ok,
                        "failure_class": "PASSED" if boundary_ok else "INCOMPLETE_MATERIALIZATION",
                        "responsible_layer": "execution_control_plane",
                    }
                }
            },
            "gates": [
                {
                    "name": "qa_verdict",
                    "ok": qa_ok,
                    "append_id": "qa-append-1",
                    "content_id": "qa-content-1",
                }
            ],
            "evidence_policy": {
                "integrity_ok": policy_ok,
                "outcome_ok": policy_ok,
                "missing_required_modalities": [] if policy_ok else ["command"],
                "failed_required_modalities": [],
            },
        }
    )

    authority = evaluate_canonical_factory_authority(
        projection,
        sequence_barrier_satisfied=sequence_ready,
    )

    assert authority.quality_stage_authorized is expected
    assert authority.reason_code == reason_code


def test_run_completion_blocks_degraded_task_runtime_projection(tmp_path: Path) -> None:
    waiter = RunCompletionWaiter(tmp_path)
    degraded = ObservableTaskRowsProjectionV1(
        workspace=str(tmp_path),
        source="task_runtime.transitional_file_fallback",
        authoritative=False,
        degraded=True,
        rows=(
            {
                "id": "TASK-1",
                "workflow_run_id": "run-1",
                "execution_state": "completed",
                "fact_event_seq": 9,
            },
        ),
        readiness={"ready": False, "blocking_reasons": ["task_row_file_fallback_required"]},
    )
    waiter._observable_task_rows_projection = lambda: degraded  # type: ignore[method-assign]

    result = waiter.canonical_terminal_result(run_id="run-1", process_terminal=True)

    assert result is not None
    assert result.status == "blocked"
    assert result.reason_code == "task_runtime_fact_projection_not_ready"
    assert result.metadata["canonical_authoritative"] is False
    assert result.metadata["degraded"] is True


def test_run_completion_conflict_matrix_prefers_failure(tmp_path: Path) -> None:
    waiter = RunCompletionWaiter(tmp_path)
    projection = _authoritative_task_projection(tmp_path, ())
    waiter._observable_task_rows_projection = lambda: projection  # type: ignore[method-assign]
    waiter._task_runtime_terminal_result = lambda **_kwargs: CommandResult(  # type: ignore[method-assign]
        run_id="run-1",
        status="completed",
        message="task runtime complete",
        metadata={"canonical_authoritative": True, "fact_event_seq": 11},
    )
    waiter._committed_turn_outcome_result = lambda **_kwargs: CommandResult(  # type: ignore[method-assign]
        run_id="run-1",
        status="failed",
        message="turn outcome failed",
        metadata={"canonical_authoritative": True, "fact_event_seq": 7},
    )

    result = waiter.canonical_terminal_result(run_id="run-1", process_terminal=True)

    assert result is not None
    assert result.status == "failed"
    assert result.metadata["terminal_source"] == "canonical_conflict_matrix"
    assert result.metadata["canonical_conflict"] is True


def test_run_completion_does_not_promote_turn_outcome_over_active_task_runtime(
    tmp_path: Path,
) -> None:
    waiter = RunCompletionWaiter(tmp_path)
    projection = _authoritative_task_projection(
        tmp_path,
        (
            {
                "id": "TASK-1",
                "workflow_run_id": "run-1",
                "execution_state": "in_progress",
                "fact_event_seq": 12,
                "metadata": {
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                },
            },
        ),
    )
    waiter._observable_task_rows_projection = lambda: projection  # type: ignore[method-assign]
    waiter._committed_turn_outcome_result = lambda **_kwargs: CommandResult(  # type: ignore[method-assign]
        run_id="run-1",
        status="completed",
        message="one role turn completed",
        metadata={"canonical_authoritative": True, "fact_event_seq": 13},
    )

    result = waiter.canonical_terminal_result(run_id="run-1", process_terminal=True)

    assert result is None


@pytest.mark.asyncio
async def test_run_completion_cancel_during_dispatch_waits_for_canonical_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _FakeOrchestrationService:
        def __init__(self) -> None:
            self.active_task = asyncio.create_task(asyncio.sleep(60))
            self._active_runs = {"run-1": self.active_task}
            self.cancelled: list[str] = []

        async def cancel_run(self, run_id: str, force: bool = False) -> None:
            del force
            self.cancelled.append(run_id)

    class _FakeCommandService:
        async def query_run_status(self, run_id: str) -> CommandResult:
            return CommandResult(run_id=run_id, status="running", message="dispatching")

    fake_orchestration = _FakeOrchestrationService()

    async def _get_orchestration_service() -> _FakeOrchestrationService:
        return fake_orchestration

    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
        _get_orchestration_service,
    )
    reads = 0

    def _projection() -> ObservableTaskRowsProjectionV1:
        nonlocal reads
        reads += 1
        status = "in_execution" if reads < 4 else "completed"
        now = datetime.now(timezone.utc)
        return _authoritative_task_projection(
            tmp_path,
            (
                {
                    "id": "TASK-1",
                    "task_id": "TASK-1",
                    "workflow_run_id": "run-1",
                    "execution_state": status,
                    "running": status == "in_execution",
                    "fact_event_seq": reads,
                    "last_heartbeat_at": (now - timedelta(seconds=1)).isoformat(),
                    "lease_expires_at": (now + timedelta(seconds=30)).isoformat(),
                    "metadata": {
                        "source": "task_runtime.execution_fact",
                        "status_source": "task_runtime.execution_fact",
                    },
                },
            ),
        )

    waiter = RunCompletionWaiter(tmp_path)
    waiter._observable_task_rows_projection = _projection  # type: ignore[method-assign]
    cancel_event = asyncio.Event()
    cancel_event.set()

    result = await waiter.wait(
        _FakeCommandService(),
        CommandResult(run_id="run-1", status="running", message="submitted"),
        timeout_seconds=1,
        cancel_event=cancel_event,
    )

    assert result.status == "completed"
    assert result.metadata["canonical_authoritative"] is True
    assert result.metadata["terminal_source"] == "task_runtime.execution_fact"
    assert fake_orchestration.cancelled == []
    fake_orchestration.active_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await fake_orchestration.active_task


@pytest.mark.asyncio
async def test_quality_gate_authority_ignores_report_and_workspace_display_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    run = FactoryRun(
        id="factory-authority",
        config=FactoryConfig(name="authority"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-07-13T00:00:00+00:00",
    )
    executor._write_json_artifact(
        "runtime/qa/report.json",
        {
            "passed": False,
            "score": 0,
            "critical_issue_count": 9,
            "warnings": ["display_only"],
        },
    )

    async def _workspace_checks(
        _run: FactoryRun,
        _context: dict[str, Any],
    ) -> tuple[bool, str]:
        return False, ""

    class _Service:
        async def execute_qa_run(self, **_kwargs: Any) -> CommandResult:
            return CommandResult(run_id="qa-run", status="running", message="submitted")

    async def _wait(*_args: Any, **_kwargs: Any) -> CommandResult:
        return CommandResult(
            run_id="qa-run",
            status="completed",
            message="committed",
            metadata={
                "canonical_authoritative": True,
                "terminal_source": "task_runtime.execution_fact",
                "fact_event_seq": 19,
            },
        )

    projection = _with_task_runtime_authority(
        {
            "source": "run_ledger",
            "integrity_ok": True,
            "outcome_ok": True,
            "task_boundary": {
                "latest_by_task": {
                    "TASK-1": {
                        "task_id": "TASK-1",
                        "status": "completed_verified",
                        "ok": True,
                        "failure_class": "PASSED",
                        "responsible_layer": "execution_control_plane",
                    }
                }
            },
            "gates": [
                {
                    "name": "qa_verdict",
                    "ok": True,
                    "append_id": "qa-append-2",
                    "content_id": "qa-content-2",
                }
            ],
            "evidence_policy": {
                "integrity_ok": True,
                "outcome_ok": True,
                "missing_required_modalities": [],
                "failed_required_modalities": [],
            },
        }
    )
    monkeypatch.setattr(executor, "_run_workspace_quality_checks", _workspace_checks)
    monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: _Service())
    monkeypatch.setattr(executor, "_wait_run_completion", _wait)
    monkeypatch.setattr(executor, "_canonical_factory_projection", lambda _run, _context: projection)

    result = await executor._execute_quality_gate(
        run,
        _factory_stage_context({"qa_target": "Quality gate"}),
    )

    assert result.status == "success"
    assert "canonical_authorized=True" in str(result.output)
    assert "report_consistent=False" in str(result.output)


def test_read_claimable_director_task_ids_uses_observable_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        TaskRuntimeService,
        "query_observable_task_rows_projection",
        lambda runtime: _authoritative_task_projection(
            Path(runtime.workspace),
            (
                {"id": 1, "status": "pending", "metadata": {"pm_task_id": "TASK-1"}},
                {"id": 2, "status": "ready", "metadata": {"external_task_id": "TASK-2"}},
                {"id": 3, "status": "pending", "blocked_by": [1]},
                {"id": 4, "status": "completed", "metadata": {"pm_task_id": "TASK-4"}},
            ),
        ),
    )

    claimable = _executor(tmp_path)._read_claimable_director_task_ids(limit=10)

    assert claimable == ["TASK-1", "TASK-2"]


def test_read_claimable_director_task_ids_excludes_trusted_internal_ce_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    factory_run_id = "factory-run-mixed-task-domains"

    def _metadata(external_task_id: str) -> dict[str, str]:
        return {
            "factory_run_id": factory_run_id,
            "factory_stage": "chief_engineer_review",
            "role": "chief_engineer",
            "external_task_id": external_task_id,
            "source_task_id": external_task_id,
            "materialized_by": "runtime.task_runtime",
        }

    monkeypatch.setattr(
        TaskRuntimeService,
        "query_observable_task_rows_projection",
        lambda runtime: _authoritative_task_projection(
            Path(runtime.workspace),
            (
                {
                    "id": 1,
                    "status": "ready",
                    "metadata": {
                        "factory_run_id": factory_run_id,
                        "pm_task_id": "TASK-2",
                        "external_task_id": "TASK-2",
                    },
                },
                {
                    "id": 2,
                    "status": "pending",
                    "metadata": _metadata(f"CE-PORTFOLIO-{factory_run_id}"),
                },
                {
                    "id": 3,
                    "status": "ready",
                    "metadata": _metadata(f"CE-PORTFOLIO-{factory_run_id}-SCHEMA-REPAIR"),
                },
            ),
        ),
    )

    claimable = _executor(tmp_path)._read_claimable_director_task_ids(
        limit=10,
        factory_run_id=factory_run_id,
    )

    assert claimable == ["TASK-2"]


def test_unresolved_task_ids_use_same_external_identity_as_claims() -> None:
    rows = [
        {"id": 1, "status": "pending", "metadata": {"external_task_id": "TASK-1"}},
        {"id": 2, "status": "ready", "metadata": {"pm_task_id": "TASK-2"}},
        {"id": 3, "status": "in_progress", "metadata": {"source_task_id": "TASK-3"}},
        {"id": 4, "status": "completed", "metadata": {"external_task_id": "TASK-4"}},
    ]

    unresolved = OrchestrationStageExecutor._unresolved_task_ids_from_rows(rows)

    assert unresolved == ("TASK-1", "TASK-2", "TASK-3")


def test_director_dependency_schedule_excludes_trusted_internal_ce_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    factory_run_id = "factory-run-schema-repair"
    rows = [
        {"id": 1, "status": "pending", "metadata": {"external_task_id": "TASK-1"}},
        {"id": 2, "status": "ready", "metadata": {"external_task_id": "TASK-2"}},
        {
            "id": 3,
            "status": "pending",
            "metadata": {
                "factory_run_id": factory_run_id,
                "factory_stage": "chief_engineer_review",
                "role": "chief_engineer",
                "external_task_id": f"CE-PORTFOLIO-{factory_run_id}",
                "source_task_id": f"CE-PORTFOLIO-{factory_run_id}",
                "materialized_by": "runtime.task_runtime",
            },
        },
    ]
    monkeypatch.setattr(executor, "_read_observable_task_rows", lambda **_kwargs: rows)

    schedule = executor._director_dependency_schedule(
        [
            {"id": "TASK-1"},
            {"id": "TASK-2", "depends_on": ["TASK-1"]},
        ],
        factory_run_id=factory_run_id,
    )

    assert schedule.valid is True
    assert schedule.active_task_ids == ("TASK-1", "TASK-2")
    assert schedule.blockers == ()


def test_director_dependency_schedule_keeps_untrusted_unknown_task_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    rows = [
        {"id": 1, "status": "pending", "metadata": {"external_task_id": "TASK-1"}},
        {
            "id": 2,
            "status": "pending",
            "metadata": {
                "factory_run_id": "factory-run",
                "factory_stage": "chief_engineer_review",
                "role": "chief_engineer",
                "external_task_id": "UNTRUSTED-INTERNAL-LOOKALIKE",
                "source_task_id": "UNTRUSTED-INTERNAL-LOOKALIKE",
                # Deliberately lacks the TaskRuntime materialization provenance.
            },
        },
    ]
    monkeypatch.setattr(executor, "_read_observable_task_rows", lambda **_kwargs: rows)

    schedule = executor._director_dependency_schedule(
        [{"id": "TASK-1"}],
        factory_run_id="factory-run",
    )

    assert schedule.valid is False
    assert schedule.blockers == ("unknown_active_task_ids:UNTRUSTED-INTERNAL-LOOKALIKE",)


def test_read_claimable_director_task_ids_skips_execution_owned_states(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        TaskRuntimeService,
        "query_observable_task_rows_projection",
        lambda runtime: _authoritative_task_projection(
            Path(runtime.workspace),
            (
                {"id": 1, "status": "pending", "metadata": {"external_task_id": "TASK-PENDING"}},
                {"id": 2, "status": "in_progress", "metadata": {"external_task_id": "TASK-IN-PROGRESS"}},
                {"id": 3, "status": "running", "metadata": {"external_task_id": "TASK-RUNNING"}},
                {"id": 4, "status": "claimed", "metadata": {"external_task_id": "TASK-CLAIMED"}},
            ),
        ),
    )

    claimable = _executor(tmp_path)._read_claimable_director_task_ids(limit=10)

    assert claimable == ["TASK-PENDING"]


def test_taskboard_stats_read_observable_owner_projection_when_stats_diverge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = {"observable_stats": 0, "raw_stats": 0}

    class _DivergedTaskRuntime:
        def __init__(self, workspace: str) -> None:
            assert workspace == str(tmp_path)

        def get_observable_task_row_stats(self) -> dict[str, int]:
            calls["observable_stats"] += 1
            return {"total": 2, "pending": 0, "ready": 0, "completed": 1, "failed": 1}

        def get_task_row_stats(self) -> dict[str, int]:
            calls["raw_stats"] += 1
            return {"total": 2, "pending": 2, "ready": 2, "completed": 0, "failed": 0}

    monkeypatch.setattr(stage_executor_module, "TaskRuntimeService", _DivergedTaskRuntime)

    stats = _executor(tmp_path)._read_taskboard_stats()

    assert calls["observable_stats"] == 1
    assert calls["raw_stats"] == 0
    assert stats["total"] == 2
    assert stats["pending"] == 0
    assert stats["ready"] == 0
    assert stats["completed"] == 1
    assert stats["failed"] == 1
    assert OrchestrationStageExecutor._is_taskboard_converged(stats) is True


# ---------------------------------------------------------------------------
# WS2 observable stats source regression guards (AST-level)
# ---------------------------------------------------------------------------
# These tests statically inspect the *source code* of _read_taskboard_stats
# to prove it delegates to get_observable_task_row_stats() and never falls
# back to get_task_row_stats() or list_observable_task_rows().  If a future
# refactor accidentally swaps the method name, these tests fail at collection
# time even before any monkeypatching exercises the runtime path.
# ---------------------------------------------------------------------------


def test_read_taskboard_stats_ast_calls_observable_not_legacy() -> None:
    """_read_taskboard_stats() must call get_observable_task_row_stats().

    It must never call the legacy get_task_row_stats() compatibility wrapper,
    which would produce identical numbers today but bypasses the
    fact-overlay contract that observable stats enforce.
    """
    src = textwrap.dedent(inspect.getsource(OrchestrationStageExecutor._read_taskboard_stats))
    tree = ast.parse(src)

    calls_on_instance: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Call)
        ):
            calls_on_instance.add(node.func.attr)

    assert "get_observable_task_row_stats" in calls_on_instance, (
        "_read_taskboard_stats() must call get_observable_task_row_stats() on "
        "TaskRuntimeService; the observable projection is the WS2 contract"
    )
    assert "get_task_row_stats" not in calls_on_instance, (
        "_read_taskboard_stats() must not call the legacy get_task_row_stats() "
        "wrapper — use get_observable_task_row_stats() directly"
    )


def test_read_taskboard_stats_ast_does_not_list_rows() -> None:
    """_read_taskboard_stats() must not call list_observable_task_rows().

    Stats aggregation belongs in the task-runtime service layer via
    get_observable_task_row_stats().  Factory must not reimplement
    row-level counting; _read_observable_task_rows() remains available for
    claimable-row inspection.
    """
    src = textwrap.dedent(inspect.getsource(OrchestrationStageExecutor._read_taskboard_stats))
    tree = ast.parse(src)

    calls_on_instance: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Call)
        ):
            calls_on_instance.add(node.func.attr)

    assert "list_observable_task_rows" not in calls_on_instance, (
        "_read_taskboard_stats() must not call list_observable_task_rows() — "
        "that method is for claimable-row inspection, not stats aggregation; "
        "use get_observable_task_row_stats() instead"
    )


def test_materialization_quality_target_filter_prefers_ts_source_over_compiled_outputs(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "dist").mkdir(parents=True)
    (tmp_path / "tests" / "behavior.test.ts").write_text("export const source = 1;\n", encoding="utf-8")
    (tmp_path / "tests" / "behavior.test.js").write_text("export const source = 1;\n", encoding="utf-8")
    (tmp_path / "dist" / "main.js").write_text("export const compiled = 1;\n", encoding="utf-8")
    errors = [
        "\n".join(
            [
                "TypeScript project typecheck failed:",
                "tests/behavior.test.ts(10,1): error TS1003: Identifier expected.",
                "tests/behavior.test.js(10,1): error TS1003: Identifier expected.",
                "dist/main.js(1,1): error TS1003: Identifier expected.",
            ]
        )
    ]

    targets = resolve_director_semantic_quality_repair_target_files(
        artifact_quality_errors=errors,
        changed_files=["tests/behavior.test.ts", "tests/behavior.test.js", "dist/main.js"],
        workspace_full=str(tmp_path),
    )

    assert targets == ["tests/behavior.test.ts"]


def test_workspace_quality_diagnostic_targets_include_language_neutral_manifests(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    executor = _executor(tmp_path)

    targets = executor._workspace_quality_repair_diagnostic_target_files(
        [
            "go.mod: malformed module path",
            "pyproject.toml: invalid project scripts table",
            "CMakeLists.txt: CMake configure failed",
        ]
    )

    assert targets == ["go.mod", "pyproject.toml", "CMakeLists.txt"]


def test_workspace_quality_plan_probe_reads_relevant_base_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export const value = 1;\n", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "main.js").write_text("compiled\n", encoding="utf-8")
    executor = _executor(tmp_path)
    captured: dict[str, Any] = {}

    def fake_query(query: Any) -> SimpleNamespace:
        captured["artifact_quality_errors"] = query.artifact_quality_errors
        captured["artifact_quality_issues"] = tuple(query.artifact_quality_issues)
        captured["base_files"] = dict(query.base_files)
        captured["metadata"] = dict(query.metadata)
        return SimpleNamespace(
            to_dict=lambda: {
                "schema_version": "director.repair_plan_probe_result.v1",
                "status": "coverage_matched_but_unplannable",
                "coverage_is_not_planning": True,
            }
        )

    monkeypatch.setattr(
        "polaris.cells.director.runtime.public.query_director_repair_plan_probe",
        fake_query,
    )

    result = executor._workspace_quality_repair_plan_probe_report(
        ["src/main.ts(1,1): error TS2322: Type 'string' is not assignable to type 'number'."]
    )

    assert result["status"] == "coverage_matched_but_unplannable"
    assert captured["base_files"] == {"src/main.ts": "export const value = 1;\n"}
    assert captured["metadata"]["coverage_is_not_planning"] is True
    assert captured["artifact_quality_errors"] == (
        "src/main.ts(1,1): error TS2322: Type 'string' is not assignable to type 'number'.",
    )
    assert captured["artifact_quality_issues"]
    typed_issue = captured["artifact_quality_issues"][0]
    assert typed_issue["code"]
    assert typed_issue["path"] == "src/main.ts"
    assert "TS2322" in typed_issue["message"]


def test_workspace_quality_repair_transports_nested_command_diagnostics_without_wrapper_gaps(
    tmp_path: Path,
) -> None:
    """Real verifier output must reach M10 as repairable diagnostics, not gate wrappers."""

    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (tmp_path / "package.json").write_text(
        '{"type":"module","scripts":{"build":"tsc","test":"node --test tests/verify.test.ts"},'
        '"devDependencies":{"typescript":"5.5.4"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"module":"ESNext","moduleResolution":"Bundler"}}\n',
        encoding="utf-8",
    )
    (src / "web.ts").write_text(
        "interface DrawingSurface { width: number; height: number }\n"
        "declare const ctx: CanvasRenderingContext2D;\n"
        "declare function render(surface: DrawingSurface): void;\n"
        "render(ctx);\n",
        encoding="utf-8",
    )
    (src / "verify.ts").write_text("export const verify = (): boolean => true;\n", encoding="utf-8")
    (tests / "verify.test.ts").write_text(
        'import { verify } from "../src/verify.js";\nvoid verify;\n',
        encoding="utf-8",
    )
    executor = _executor(tmp_path)
    results = [
        {
            "command": ["npm", "run", "build"],
            "phase": "check",
            "exit_code": 2,
            "passed": False,
            "stdout_tail": (
                "src/web.ts(4,8): error TS2345: Argument of type 'CanvasRenderingContext2D' "
                "is not assignable to parameter of type 'DrawingSurface'.\n"
                "  Type 'CanvasRenderingContext2D' is missing the following properties "
                "from type 'DrawingSurface': width, height"
            ),
            "stderr_tail": "",
        },
        {
            "command": ["npm", "test"],
            "phase": "check",
            "exit_code": 1,
            "passed": False,
            "stdout_tail": (
                "Error [ERR_MODULE_NOT_FOUND]: Cannot find module "
                f"'{src / 'verify.js'}' imported from {tests / 'verify.test.ts'}"
            ),
            "stderr_tail": "",
        },
    ]

    repair_errors = executor._workspace_quality_repair_errors(results)
    coverage = executor._workspace_quality_repair_coverage_report(repair_errors)
    probe = executor._workspace_quality_repair_plan_probe_report(repair_errors)

    assert len(repair_errors) == 2
    assert all("workspace validation command failed" not in error for error in repair_errors)
    assert any("TS2345" in error for error in repair_errors)
    assert any("ERR_MODULE_NOT_FOUND" in error for error in repair_errors)
    assert coverage["uncovered_diagnostic_count"] == 0
    assert probe["status"] != "coverage_gap_uncovered_diagnostics"
    matched_tools = {
        source_tool
        for item in coverage["items"]
        for source_tool in item["matched_source_tools"]
    }
    assert "deterministic_typescript_argument_shape_adapter_repair" in matched_tools
    assert "deterministic_typescript_local_js_import_repair" in matched_tools


def test_quality_gate_failure_stage_does_not_add_qa_llm_warning_for_deterministic_blocker(
    tmp_path: Path,
) -> None:
    executor = _executor(tmp_path)
    run = FactoryRun(
        id="run-deterministic-failure",
        config=FactoryConfig(name="demo"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-06-30T00:00:00Z",
    )

    result = executor._build_quality_gate_failure_stage(
        run,
        reason_code="workspace_quality_gate_failed",
        detail="npm test failed",
        context={},
    )

    assert result.status == "failed"
    report = json.loads(executor._artifact_path("runtime/qa/report.json").read_text(encoding="utf-8"))
    assert report["warnings"] == ["workspace_quality_gate_failed"]
    assert "qa_llm_judgement_unavailable" not in report["warnings"]


def test_workspace_validation_artifact_writes_run_ledger_command_evidence(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    run = FactoryRun(
        id="run-workspace-validation",
        config=FactoryConfig(name="demo"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-06-30T00:00:00Z",
    )

    artifact = executor._write_workspace_validation_artifact(
        run,
        {"project_id": "L1-ledger", "target_files": ["src/index.js"]},
        {
            "schema_version": "factory.workspace_quality_checks.v1",
            "factory_run_id": run.id,
            "passed": True,
            "commands": [
                {
                    "command": ["npm", "test"],
                    "passed": True,
                    "exit_code": 0,
                }
            ],
            "repair": {"attempted": False},
        },
    )

    projection = load_run_ledger_projection(tmp_path, run_id=run.id)
    assert artifact == "runtime/qa/workspace-validation.json"
    assert projection["gate_count"] == 1
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == []
    assert projection["evidence_modalities"]["command"]["ok"] == 1


def test_pm_plan_validation_contract_hygiene_defers_test_acceptance_to_validation_task() -> None:
    payload = {
        "tasks": [
            {
                "id": "TASK-1",
                "goal": "Create implementation modules",
                "scope": "src",
                "target_files": ["package.json", "tsconfig.json", "src/index.ts"],
                "steps": ["Create implementation files"],
                "acceptance": ["`npm run build` and `npm run test` pass for the implementation."],
                "acceptance_criteria": ["`npm run build` and `npm run test` pass for the implementation."],
            },
            {
                "id": "TASK-2",
                "goal": "Create verification assets",
                "scope": "tests",
                "target_files": ["src/verify.ts", "tests/verify.test.ts", "README.md"],
                "steps": ["Create test coverage"],
                "acceptance": ["`npm run test` executes real verification and returns PASS."],
                "depends_on": ["TASK-1"],
            },
        ]
    }

    tasks = OrchestrationStageExecutor._pm_plan_tasks_from_payload(payload)

    first_acceptance = " ".join(tasks[0]["acceptance"]).lower()
    first_acceptance_criteria = " ".join(tasks[0]["acceptance_criteria"]).lower()
    assert "npm run test" not in first_acceptance
    assert "npm run test" not in first_acceptance_criteria
    assert "build/start checks" in first_acceptance
    assert "build/start checks" in first_acceptance_criteria
    assert tasks[0]["metadata"]["validation_contract_hygiene"]["downstream_validation_targets"] == [
        "src/verify.ts",
        "tests/verify.test.ts",
    ]
    assert "npm run test" in " ".join(tasks[1]["acceptance"]).lower()


def _write_review_for_blueprint(
    executor: OrchestrationStageExecutor,
    *,
    run_id: str,
    task_id: str,
    blueprint_id: str,
) -> None:
    executor._write_json_artifact(
        f"runtime/state/blueprints/{run_id}.review.json",
        {
            "schema_version": "factory.chief_engineer_review.v1",
            "factory_run_id": run_id,
            "blueprints": [
                {
                    "task_id": task_id,
                    "blueprint_id": blueprint_id,
                    "blueprint_path": f"runtime/state/blueprints/{blueprint_id}.json",
                }
            ],
        },
    )


def _write_handoff_ready_review_for_tasks(
    executor: OrchestrationStageExecutor,
    *,
    run_id: str,
    tasks: list[dict[str, Any]],
) -> None:
    rows: list[dict[str, str]] = []
    for index, task in enumerate(tasks, start=1):
        task_id = str(task.get("id") or task.get("task_id") or f"TASK-{index}")
        raw_targets = task.get("target_files")
        target_files = (
            [str(item) for item in raw_targets if str(item).strip()]
            if isinstance(raw_targets, list)
            else ["src/index.ts"]
        )
        result = _generate_domain_blueprint(
            Path(executor.workspace),
            task_id=task_id,
            objective=f"Build pirate treasure budget planner for {task_id}",
            target_files=target_files,
            acceptance_criteria=[
                "treasure, budget, port, and reef behavior tests pass",
                "project validation passes",
            ],
            execution_checklist=[
                "Implement treasure and budget models",
                "Implement port fee and reef risk rules",
            ],
        )
        assert result.ok is True
        rows.append(
            {
                "task_id": task_id,
                "blueprint_id": result.blueprint_id,
                "blueprint_path": f"runtime/state/blueprints/{result.blueprint_id}.json",
            }
        )
    executor._write_json_artifact(
        f"runtime/state/blueprints/{run_id}.review.json",
        {
            "schema_version": "factory.chief_engineer_review.v1",
            "factory_run_id": run_id,
            "blueprints": rows,
        },
    )


def _generate_domain_blueprint(
    workspace: Path,
    *,
    task_id: str,
    objective: str,
    target_files: list[str],
    acceptance_criteria: list[str],
    execution_checklist: list[str],
) -> TaskBlueprintResultV1:
    return generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id=task_id,
            workspace=str(workspace),
            objective=objective,
            context={
                "task_title": objective,
                "target_files": target_files,
                "acceptance_criteria": acceptance_criteria,
                "execution_checklist": execution_checklist,
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "product_summary": {
                        "intent": "Deliver a pirate treasure budget planner.",
                        "core_terms": ["treasure", "budget", "port", "reef"],
                    },
                },
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "product_intent": {
                        "subject": "pirate treasure budget planner",
                        "primary_entities": ["treasure", "budget", "port", "reef"],
                    },
                },
            },
        )
    )


# ---------------------------------------------------------------------------
# Pure text-shaping helpers
# ---------------------------------------------------------------------------


class TestChiefEngineerHandoffGuards:
    def test_task_blueprint_context_injects_catalog_delivery_depth_contract(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        catalog_path = tmp_path / ".polaris" / "catalog_contract.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(
            json.dumps(
                {
                    "project_id": "L2-07",
                    "primary_language": "typescript",
                    "project_type": "management_game",
                    "feature_keywords": ["market", "fairy", "inventory", "reputation"],
                    "level": 2,
                    "level_contract": {
                        "schema_version": "factory-bench.level_contract.v1",
                        "level": 2,
                        "minimums": {
                            "min_test_files": 1,
                            "min_test_assertions": 8,
                        },
                        "required_evidence": ["Tests assert business results"],
                        "anti_hollow_delivery": ["Do not pass tests that only check files exist"],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        context = executor._task_blueprint_context(
            {
                "id": "TASK-1",
                "title": "Build TypeScript market models",
                "target_files": ["package.json", "src/index.ts"],
                "acceptance_criteria": ["npm run build passes"],
                "execution_checklist": ["Implement source"],
            },
            run_id="factory_test",
            index=1,
        )

        depth_contract = context["delivery_depth_contract"]
        assert depth_contract["source"] == "factory.catalog_contract"
        assert depth_contract["language"] == "typescript"
        assert depth_contract["minimums"]["min_test_files"] == 1
        assert depth_contract["minimums"]["min_test_assertions"] == 8
        assert context["metadata"]["delivery_depth_contract"] == depth_contract
        assert context["pm_task_contract"]["id"] == "TASK-1"
        assert context["pm_task_contract"]["target_files"] == ["package.json", "src/index.ts"]
        assert context["target_files"] == ["package.json", "src/index.ts"]

    def test_task_blueprint_context_merges_catalog_minimums_into_existing_depth_contract(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        catalog_path = tmp_path / ".polaris" / "catalog_contract.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(
            json.dumps(
                {
                    "project_id": "L2-08",
                    "primary_language": "javascript",
                    "project_type": "collaboration_toy",
                    "feature_keywords": ["meteor", "wish", "queue", "priority"],
                    "level": 2,
                    "level_contract": {
                        "schema_version": "factory-bench.level_contract.v1",
                        "level": 2,
                        "minimums": {
                            "min_prod_files": 6,
                            "min_prod_lines": 500,
                            "min_test_assertions": 8,
                        },
                        "required_evidence": ["Factory audit implementation_depth passes"],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        context = executor._task_blueprint_context(
            {
                "id": "TASK-1",
                "title": "Build meteor wish queue",
                "target_files": ["src/index.js"],
                "acceptance_criteria": ["src/index.js exists"],
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "source": "pm.deterministic_synthesis",
                    "product_intent": {
                        "subject": "流星愿望队列",
                        "primary_entities": ["meteor", "wish", "queue", "priority"],
                    },
                    "behavior_contract": {
                        "rule_matrix": ["priority queue ordering is observable"],
                    },
                },
            },
            run_id="factory_test",
            index=1,
        )

        depth_contract = context["delivery_depth_contract"]
        assert depth_contract["source"] == "pm.deterministic_synthesis"
        assert depth_contract["minimums"]["min_prod_files"] == 6
        assert depth_contract["minimums"]["min_prod_lines"] == 500
        assert depth_contract["level_contract"]["minimums"]["min_test_assertions"] == 8
        assert depth_contract["product_intent"]["subject"] == "流星愿望队列"
        assert context["level_contract"]["level"] == 2
        assert context["metadata"]["factory_bench_level"] == 2
        assert context["metadata"]["factory_bench_project_id"] == "L2-08"

    def test_chief_engineer_review_consumes_llm_blueprint_overlay(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Build mood color wheel",
                        "goal": "Build a mood color wheel with doodle behavior.",
                        "target_files": ["models/mood.go", "engine/wheel.go", "main.go", "engine/wheel_test.go"],
                        "scope_paths": ["models/mood.go", "engine/wheel.go", "main.go", "engine/wheel_test.go"],
                        "acceptance_criteria": [
                            "mood, color, wheel, and doodle behavior tests pass",
                            "go test ./... passes",
                        ],
                        "execution_checklist": [
                            "Implement mood and color models",
                            "Implement wheel and doodle rules",
                        ],
                        "delivery_plan_document": {
                            "schema_version": "polaris.delivery_plan_document.v1",
                            "product_summary": {
                                "intent": "Deliver a mood color wheel.",
                                "core_terms": ["mood", "color", "wheel", "doodle"],
                            },
                        },
                        "delivery_depth_contract": {
                            "schema_version": "polaris.delivery_depth_contract.v1",
                            "product_intent": {
                                "subject": "mood color wheel",
                                "primary_entities": ["mood", "color", "wheel", "doodle"],
                            },
                        },
                    }
                ]
            },
        )

        captured_commands: list[Any] = []

        class _FakeRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                captured_commands.append(command)
                ce_output = {
                    "construction_plan": {
                        "project_design_intent": "Keep rendering behind a stable wheel report interface.",
                        "project_interface_contract": {
                            "provider_declarations": [
                                {
                                    "path": "engine/wheel.go",
                                    "name": "BuildWheelReport",
                                    "symbol_kind": "function",
                                    "signature": "BuildWheelReport(mood Mood) WheelReport",
                                    "semantic_role": "build a color wheel report",
                                }
                            ],
                            "consumer_declarations": [
                                {
                                    "path": "main.go",
                                    "name": "BuildWheelReport",
                                    "provider_path": "engine/wheel.go",
                                    "semantic_role": "render the CLI report",
                                }
                            ],
                        },
                        "task_plans": {
                            "TASK-1": {
                                "preparation": ["Confirm Go module boundary"],
                                "implementation": ["Model mood palette", "Render wheel report"],
                                "verification": ["go test ./...", "go run ."],
                            }
                        },
                    },
                    "scope_for_apply": ["models/mood.go", "engine/wheel.go", "main.go"],
                    "risk_flags": [
                        {
                            "level": "warning",
                            "description": "visual entrypoint can drift from engine rules",
                            "mitigation": "assert report output in tests",
                        }
                    ],
                    "project_completion_contract": _library_completion_requirements(
                        "models/mood.go",
                        "engine/wheel.go",
                        "main.go",
                        owner_task_ids=("TASK-1", "TASK-1", "TASK-1"),
                        test_path="engine/wheel_test.go",
                        test_owner_task_id="TASK-1",
                    ),
                }
                return SimpleNamespace(
                    ok=True,
                    output=json.dumps(ce_output, ensure_ascii=False),
                    error_message="",
                    error_code="",
                    metadata={
                        "provider_id": "test-provider",
                        "model": "test-model",
                        "structured_output": ce_output,
                        "final_request_context_audit": {"context_window_utilization": 0.42},
                        "context_snapshot_ref": "runtime/contexts/aa/abcdef123456abcdef123456.json",
                    },
                    usage={},
                )

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _FakeRoleRuntimeService)
        run = FactoryRun(
            id="factory-run",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success"
        review_path = Path(resolve_logical_path(tmp_path, "runtime/state/blueprints/factory-run.review.json"))
        review = json.loads(review_path.read_text(encoding="utf-8"))
        row = review["blueprints"][0]
        assert row["llm_blueprint_consumed"] is True
        assert row["llm_blueprint_keys"] == ["construction_plan", "risk_flags", "scope_for_apply"]
        blueprint = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(row["blueprint_id"])
        assert isinstance(blueprint, dict)
        assert blueprint["llm_blueprint"]["implementation_phases"] == [
            "Confirm Go module boundary",
            "Model mood palette",
            "Render wheel report",
        ]
        assert blueprint["llm_blueprint"]["verification_steps"] == ["go test ./...", "go run ."]
        assert blueprint["ce_handoff"]["llm_blueprint_consumed"] is True
        assert len(captured_commands) == 1
        command = captured_commands[0]
        assert command.stream is True
        assert command.context["delivery_mode"] == "analyze_only"
        assert command.context["llm_max_tokens"] == 16_384
        assert command.context["reasoning_budget_tokens"] == 4_096
        assert command.context["temperature"] == 0.2
        assert command.context["response_format_mode"] == "json"
        assert command.context["chief_engineer_json_contract_required"] is True
        assert "_transaction_kernel_forced_tool_definitions" not in command.context
        assert "_transaction_kernel_forced_tool_choice" not in command.context
        assert command.structured_output_contract is not None
        assert command.structured_output_contract.schema_name == "chief_engineer_blueprint_portfolio"
        task_plans_schema = command.structured_output_contract.json_schema["properties"]["construction_plan"][
            "properties"
        ]["task_plans"]
        assert task_plans_schema["required"] == ["TASK-1"]
        assert task_plans_schema["additionalProperties"] is False
        project_interface_schema = command.structured_output_contract.json_schema["properties"]["construction_plan"][
            "properties"
        ]["project_interface_contract"]
        assert set(project_interface_schema["properties"]) == {
            "provider_declarations",
            "consumer_declarations",
        }
        assert project_interface_schema["additionalProperties"] is False
        completion_schema = command.structured_output_contract.json_schema["properties"]["project_completion_contract"]
        assert completion_schema["additionalProperties"] is False
        obligations_schema = completion_schema["properties"]["obligations"]
        assert obligations_schema["required"] == ["artifacts", "entrypoints", "verification"]
        assert obligations_schema["properties"]["verification"]["items"]["properties"]["covers_obligation_ids"] == {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        }
        verification_item_schema = obligations_schema["properties"]["verification"]["items"]
        assert "command_authority_hash" in verification_item_schema["properties"]
        assert "command" not in verification_item_schema["properties"]
        assert "owner_task_id" in obligations_schema["properties"]["artifacts"]["items"]["required"]
        assert command.context["project_completion_authority"]["pm_contract_hash"] == "a" * 64
        assert command.context["project_completion_authority"]["covered_task_ids"] == ["TASK-1"]
        assert command.metadata["project_completion_authority"]["verifier_policy_hash"] == "b" * 64
        assert command.context["project_completion_authority"]["verification_command_authority"]
        assert command.metadata["max_retries"] == 0
        assert command.metadata["temperature"] == 0.2
        assert command.metadata["reasoning_budget_tokens"] == 4_096
        assert command.metadata["response_format_mode"] == "json"
        assert command.metadata["chief_engineer_json_contract_required"] is True
        assert command.execution_attempt is not None
        assert command.execution_attempt.session_id.startswith("tx-")
        assert command.execution_attempt.attempt == 1
        assert command.execution_attempt.external_task_id == f"CE-PORTFOLIO-{run.id}"
        assert command.execution_attempt.role_id == "chief_engineer"
        assert command.execution_attempt.run_id == run.id

    def test_chief_engineer_review_accepts_omitted_advisory_scope_with_audit_signal(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Missing CE scope advice must not discard an otherwise valid portfolio.

        ``scope_for_apply`` is advisory only: PM target/scope paths remain the
        authority and the blueprint projection already rejects scope expansion.
        The omission must stay visible as an audit warning rather than being
        synthesized or treated as a fatal provider-schema defect.
        """

        executor = _executor(tmp_path)
        _write_minimal_chief_engineer_plan(executor)
        captured_commands: list[Any] = []

        class _ScopeOmittingRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                captured_commands.append(command)
                result = _single_task_chief_engineer_result()
                payload = dict(result.metadata["structured_output"])
                payload.pop("scope_for_apply")
                result.output = json.dumps(payload)
                result.metadata["structured_output"] = payload
                return result

        monkeypatch.setattr(
            stage_executor_module,
            "RoleRuntimeService",
            _ScopeOmittingRoleRuntimeService,
        )
        run = FactoryRun(
            id="factory-run-scope-advisory-omitted",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-07-27T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success"
        assert len(captured_commands) == 1
        contract = captured_commands[0].structured_output_contract
        assert contract is not None
        assert "scope_for_apply" in contract.json_schema["properties"]
        assert "scope_for_apply" not in contract.json_schema["required"]

        review_path = Path(
            resolve_logical_path(
                tmp_path,
                f"runtime/state/blueprints/{run.id}.review.json",
            )
        )
        review = json.loads(review_path.read_text(encoding="utf-8"))
        assert review["generated_blueprints"] == 1
        omission_signal = next(
            signal for signal in review["signals"] if signal["code"] == "chief_engineer.scope_advisory_omitted"
        )
        assert omission_signal["severity"] == "warning"
        assert omission_signal["pm_authority_preserved"] is True
        assert omission_signal["scope_expansion_allowed"] is False

    def test_chief_engineer_review_fails_before_provider_without_committed_pm_authority(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = OrchestrationStageExecutor(tmp_path)
        _write_minimal_chief_engineer_plan(executor)
        provider_calls: list[Any] = []

        class _UnexpectedRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                provider_calls.append(command)
                raise AssertionError("provider dispatch must not run without committed PM authority")

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _UnexpectedRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-missing-pm-authority",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "failed"
        assert result.metadata["error_code"] == "chief_engineer.project_completion_authority_invalid"
        assert provider_calls == []

    def test_chief_engineer_portfolio_authority_uses_committed_pm_and_compiled_verifier_policy(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = OrchestrationStageExecutor(tmp_path)
        catalog_path = tmp_path / ".polaris" / "catalog_contract.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(
            json.dumps(
                {
                    "project_id": "catalog-project-owner",
                    "project_type": "service",
                }
            ),
            encoding="utf-8",
        )
        pm_tasks = [
            {
                "id": "TASK-OWNER",
                "goal": "Bind exact completion owner",
                "target_files": ["src/owner.py"],
                "acceptance_criteria": ["python -m compileall src passes"],
                "verification_commands": [
                    {
                        "modality": "build",
                        "argv": ["python", "-m", "compileall", "src"],
                        "cwd": ".",
                    }
                ],
            }
        ]
        portfolio_tasks = executor._chief_engineer_portfolio_tasks(pm_tasks)
        store_calls: list[tuple[Path, bool]] = []

        class _FakeFactoryStore:
            def __init__(self, base_dir: Path, *, create_root: bool = True) -> None:
                store_calls.append((base_dir, create_root))

            async def get_authoritative_events(self, run_id: str) -> list[dict[str, Any]]:
                assert run_id == "factory-run-owner"
                return [{"type": "stage_completed", "event_id": "pm-stage-event"}]

        monkeypatch.setattr(stage_executor_module, "FactoryStore", _FakeFactoryStore)
        monkeypatch.setattr(
            stage_executor_module,
            "reduce_factory_stage_persistence",
            lambda _events, *, factory_run_id: SimpleNamespace(
                commits=(
                    SimpleNamespace(
                        stage="pm_planning",
                        stage_completed_event_id="pm-stage-event",
                        factory_run_id=factory_run_id,
                    ),
                )
            ),
        )
        monkeypatch.setattr(
            stage_executor_module,
            "revalidate_pm_stage_artifact_binding",
            lambda **_kwargs: SimpleNamespace(
                item=SimpleNamespace(canonical_json_sha256="c" * 64),
                document={"tasks": pm_tasks},
                task_ids=("TASK-OWNER",),
            ),
        )
        policy_commands: list[Any] = []

        def _compile_policy(command: Any) -> Any:
            policy_commands.append(command)
            return SimpleNamespace(
                policy={
                    "schema_version": "evidence_policy.v1",
                    "policy_hash": "d" * 64,
                    "source": "control_plane.verifier_policy.evidence_policy_compiler",
                    "required_evidence_modalities": ["command"],
                }
            )

        monkeypatch.setattr(stage_executor_module, "compile_evidence_policy", _compile_policy)
        run = FactoryRun(
            id="factory-run-owner",
            # The run name is a display label, not canonical project identity.
            config=FactoryConfig(name="Factory Run - pm"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        authority = asyncio.run(
            executor._load_chief_engineer_portfolio_authority(
                run=run,
                pm_tasks=pm_tasks,
                portfolio_tasks=portfolio_tasks,
            )
        )

        assert authority.project_id == "catalog-project-owner"
        assert authority.pm_contract_hash == "c" * 64
        assert authority.pm_task_ids == ("TASK-OWNER",)
        assert authority.project_kind_authority.project_kind == "application"
        assert authority.project_kind_authority.source_ref == "chief_engineer.committed_pm_catalog_snapshot"
        assert authority.project_kind_authority.justification == (
            "conservative_application_without_explicit_library_authority"
        )
        assert authority.verifier_policy_hash == "d" * 64
        assert len(authority.verification_command_authority) == 1
        assert authority.verification_command_authority[0].argv == (
            "python",
            "-m",
            "compileall",
            "src",
        )
        assert store_calls[0][1] is False
        assert policy_commands[0].target_files == ("src/owner.py",)
        assert policy_commands[0].acceptance_criteria == ("python -m compileall src passes",)
        assert policy_commands[0].explicit_required_modalities == ("command",)

    def test_chief_engineer_project_kind_requires_explicit_catalog_library_authority(
        self,
        tmp_path: Path,
    ) -> None:
        catalog_path = tmp_path / ".polaris" / "catalog_contract.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(
            json.dumps(
                {
                    "project_id": "library-project",
                    "project_kind": "library",
                    "project_type": "python_package",
                }
            ),
            encoding="utf-8",
        )
        executor = OrchestrationStageExecutor(tmp_path)
        catalog_snapshot = executor._chief_engineer_catalog_snapshot()
        catalog_snapshot_hash = project_completion_catalog_snapshot_hash(catalog_snapshot)

        authority = executor._chief_engineer_project_kind_authority(
            project_id="library-project",
            run_id="factory-run-library",
            pm_contract_hash="c" * 64,
            catalog_snapshot=catalog_snapshot,
            catalog_snapshot_hash=catalog_snapshot_hash,
        )

        assert authority.project_kind == "library"
        assert authority.justification == "catalog_explicit_project_kind:library"
        assert len(authority.source_hash) == 64

    def test_chief_engineer_unknown_catalog_project_kind_fails_closed(
        self,
        tmp_path: Path,
    ) -> None:
        catalog_path = tmp_path / ".polaris" / "catalog_contract.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(
            json.dumps({"project_kind": "service"}),
            encoding="utf-8",
        )
        executor = OrchestrationStageExecutor(tmp_path)
        catalog_snapshot = executor._chief_engineer_catalog_snapshot()
        catalog_snapshot_hash = project_completion_catalog_snapshot_hash(catalog_snapshot)

        with pytest.raises(stage_executor_module._ChiefEngineerPortfolioAuthorityError) as exc_info:
            executor._chief_engineer_project_kind_authority(
                project_id="project-owner",
                run_id="factory-run-owner",
                pm_contract_hash="c" * 64,
                catalog_snapshot=catalog_snapshot,
                catalog_snapshot_hash=catalog_snapshot_hash,
            )

        assert exc_info.value.code == "chief_engineer.project_completion_project_kind_authority_invalid"

    def test_chief_engineer_missing_pm_command_authority_fails_before_provider_dispatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        _write_minimal_chief_engineer_plan(executor)
        provider_calls: list[Any] = []

        async def _load_missing_authority(
            self: OrchestrationStageExecutor,
            *,
            run: FactoryRun,
            pm_tasks: list[dict[str, Any]],
            portfolio_tasks: tuple[Any, ...],
        ) -> Any:
            del run, portfolio_tasks
            return self._chief_engineer_verification_command_authority(pm_tasks)

        class _UnexpectedRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                provider_calls.append(command)
                raise AssertionError("provider dispatch must not run without PM command authority")

        executor._load_chief_engineer_portfolio_authority = MethodType(  # type: ignore[method-assign]
            _load_missing_authority,
            executor,
        )
        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _UnexpectedRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-missing-command-authority",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "failed"
        assert (
            result.metadata["error_code"] == "chief_engineer.project_completion_verification_command_authority_missing"
        )
        assert provider_calls == []

    def test_chief_engineer_missing_structured_verification_commands_has_stable_pre_provider_code(
        self,
        tmp_path: Path,
    ) -> None:
        executor = OrchestrationStageExecutor(tmp_path)

        with pytest.raises(stage_executor_module._ChiefEngineerPortfolioAuthorityError) as exc_info:
            executor._chief_engineer_verification_command_authority(
                [
                    {
                        "id": "TASK-NO-COMMAND-AUTHORITY",
                        "goal": "Must not infer commands from prose",
                        "target_files": ["src/main.py"],
                        "acceptance_criteria": ["echo ok", "python --version"],
                    }
                ]
            )

        assert exc_info.value.code == "chief_engineer.project_completion_verification_command_authority_missing"

    @pytest.mark.parametrize(
        "argv",
        (
            ["echo", "ok"],
            ["printf", "ok"],
            ["python", "--version"],
            ["node", "--help"],
            ["python", "-m", "src.main", "--help"],
            ["true"],
            ["python", "-c", "pass"],
        ),
    )
    def test_chief_engineer_fake_structured_verifier_is_rejected_pre_provider(
        self,
        tmp_path: Path,
        argv: list[str],
    ) -> None:
        executor = OrchestrationStageExecutor(tmp_path)

        with pytest.raises(stage_executor_module._ChiefEngineerPortfolioAuthorityError) as exc_info:
            executor._chief_engineer_verification_command_authority(
                [
                    {
                        "id": "TASK-FAKE-COMMAND",
                        "goal": "Do not accept a no-op as delivery evidence",
                        "target_files": ["src/main.py"],
                        "verification_commands": [
                            {
                                "modality": "test",
                                "argv": argv,
                                "cwd": ".",
                            }
                        ],
                    }
                ]
            )

        assert exc_info.value.code == "chief_engineer.project_completion_verification_command_authority_invalid"
        assert "proof-of-work" in str(exc_info.value)

    def test_chief_engineer_portfolio_authority_rejects_mutable_pm_path_drift_before_policy_compile(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = OrchestrationStageExecutor(tmp_path)
        pm_tasks = [
            {
                "id": "TASK-OWNER",
                "goal": "Bind exact completion owner",
                "target_files": ["src/owner.py"],
            }
        ]
        portfolio_tasks = executor._chief_engineer_portfolio_tasks(pm_tasks)

        class _FakeFactoryStore:
            def __init__(self, _base_dir: Path, *, create_root: bool = True) -> None:
                assert create_root is False

            async def get_authoritative_events(self, _run_id: str) -> list[dict[str, Any]]:
                return [{"type": "stage_completed", "event_id": "pm-stage-event"}]

        monkeypatch.setattr(stage_executor_module, "FactoryStore", _FakeFactoryStore)
        monkeypatch.setattr(
            stage_executor_module,
            "reduce_factory_stage_persistence",
            lambda _events, *, factory_run_id: SimpleNamespace(
                commits=(
                    SimpleNamespace(
                        stage="pm_planning",
                        stage_completed_event_id="pm-stage-event",
                        factory_run_id=factory_run_id,
                    ),
                )
            ),
        )
        monkeypatch.setattr(
            stage_executor_module,
            "revalidate_pm_stage_artifact_binding",
            lambda **_kwargs: SimpleNamespace(
                item=SimpleNamespace(canonical_json_sha256="c" * 64),
                document={
                    "tasks": [
                        {
                            "id": "TASK-OWNER",
                            "goal": "Bind exact completion owner",
                            "target_files": ["src/committed.py"],
                        }
                    ]
                },
                task_ids=("TASK-OWNER",),
            ),
        )
        monkeypatch.setattr(
            stage_executor_module,
            "compile_evidence_policy",
            lambda _command: pytest.fail("policy compile must not run after committed PM path drift"),
        )
        run = FactoryRun(
            id="factory-run-owner-drift",
            config=FactoryConfig(name="project-owner"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        with pytest.raises(RuntimeError, match="chief_engineer_project_completion_pm_document_mismatch"):
            asyncio.run(
                executor._load_chief_engineer_portfolio_authority(
                    run=run,
                    pm_tasks=pm_tasks,
                    portfolio_tasks=portfolio_tasks,
                )
            )

    @pytest.mark.parametrize("project_id", [" project-owner", "project\nowner", "x" * 129])
    def test_chief_engineer_portfolio_authority_rejects_invalid_project_id_before_ledger_read(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        project_id: str,
    ) -> None:
        executor = OrchestrationStageExecutor(tmp_path)
        pm_tasks = [
            {
                "id": "TASK-OWNER",
                "goal": "Bind exact completion owner",
                "target_files": ["src/owner.py"],
            }
        ]
        portfolio_tasks = executor._chief_engineer_portfolio_tasks(pm_tasks)

        class _UnexpectedFactoryStore:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                raise AssertionError("invalid project authority must fail before ledger access")

        monkeypatch.setattr(stage_executor_module, "FactoryStore", _UnexpectedFactoryStore)
        run = FactoryRun(
            id="factory-run-invalid-project-owner",
            config=FactoryConfig(name=project_id),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        with pytest.raises(RuntimeError, match="chief_engineer_project_completion_project_id_"):
            asyncio.run(
                executor._load_chief_engineer_portfolio_authority(
                    run=run,
                    pm_tasks=pm_tasks,
                    portfolio_tasks=portfolio_tasks,
                )
            )

    def test_chief_engineer_schema_repair_uses_separate_claim_and_closes_stage(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        _write_minimal_chief_engineer_plan(executor)
        invalid_output = '{"construction_plan": <invalid>, "scope_for_apply": ["src/cancel.py"]}'
        results = [_invalid_chief_engineer_stream_result(invalid_output), _single_task_chief_engineer_result()]
        commands: list[Any] = []

        class _RepairingRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                return results.pop(0)

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _RepairingRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-schema-repair",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        context = _factory_stage_context()
        result = asyncio.run(executor._execute_chief_engineer_review(run, context))

        assert result.status == "success"
        assert [command.task_id for command in commands] == [
            f"CE-PORTFOLIO-{run.id}",
            f"CE-PORTFOLIO-{run.id}-SCHEMA-REPAIR",
        ]
        repair_command = commands[1]
        assert repair_command.stream is True
        assert repair_command.metadata["max_retries"] == 0
        assert repair_command.metadata["validate_output"] is True
        assert repair_command.metadata["temperature"] == 0.0
        assert repair_command.metadata["llm_max_tokens"] == 8_192
        assert repair_command.metadata["reasoning_budget_tokens"] == 2_048
        assert repair_command.context["chief_engineer_schema_repair"] is True
        assert repair_command.context["llm_max_tokens"] == 8_192
        assert repair_command.context["reasoning_budget_tokens"] == 2_048
        assert repair_command.structured_output_contract is not None
        repair_task_plans_schema = repair_command.structured_output_contract.json_schema["properties"][
            "construction_plan"
        ]["properties"]["task_plans"]
        assert repair_task_plans_schema["required"] == ["TASK-CANCEL"]
        assert repair_task_plans_schema["additionalProperties"] is False
        assert repair_command.context["failure_feedback"] == {
            "schema_version": "factory.chief_engineer_schema_repair.failure_evidence.v1",
            "failure_class": "output_validation_failed",
            "failure_stage": "chief_engineer_review",
            "detail": "Output validation failed: malformed chief engineer JSON",
            "prior_output_sha256": hashlib.sha256(invalid_output.encode("utf-8")).hexdigest(),
            "prior_output_chars": len(invalid_output),
            "evidence_refs": [],
        }
        assert invalid_output not in repair_command.objective
        assert "Do not copy, quote, continue, or textually repair" in repair_command.objective
        assert hashlib.sha256(invalid_output.encode("utf-8")).hexdigest() in repair_command.objective
        assert "TASK-CANCEL" in repair_command.objective
        assert repair_command.execution_attempt is not None
        assert repair_command.execution_attempt.external_task_id == repair_command.task_id
        authority_port = context[stage_executor_module.FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY]
        assert len(authority_port._test_minted_authority_bindings) == 1

        task_runtime = TaskRuntimeService(str(tmp_path))
        primary_task = task_runtime.get_task(f"CE-PORTFOLIO-{run.id}")
        repair_task = task_runtime.get_task(f"CE-PORTFOLIO-{run.id}-SCHEMA-REPAIR")
        assert primary_task is not None
        assert repair_task is not None
        primary_session = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/tasks/task_{primary_task['id']}.session.json")).read_text(
                encoding="utf-8"
            )
        )
        repair_session = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/tasks/task_{repair_task['id']}.session.json")).read_text(
                encoding="utf-8"
            )
        )
        assert primary_session["status"] == "suspended"
        assert repair_session["status"] == "completed"

        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["llm_call_count"] == 2
        assert [signal["code"] for signal in review["signals"]] == ["chief_engineer.output_schema_repair_started"]
        assert len(keepers) == 2
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_thinking_only_result_uses_bounded_schema_repair(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        _write_minimal_chief_engineer_plan(executor)
        results = [_thinking_only_chief_engineer_result(), _single_task_chief_engineer_result()]
        commands: list[Any] = []

        class _RepairingRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                return results.pop(0)

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _RepairingRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-thinking-only-repair",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success"
        assert [command.task_id for command in commands] == [
            f"CE-PORTFOLIO-{run.id}",
            f"CE-PORTFOLIO-{run.id}-SCHEMA-REPAIR",
        ]
        repair_command = commands[1]
        assert repair_command.context["failure_feedback"] == {
            "schema_version": "factory.chief_engineer_schema_repair.failure_evidence.v1",
            "failure_class": "thinking_only_response",
            "failure_stage": "chief_engineer_review",
            "detail": "model returned thinking-only response; awaiting user clarification",
            "prior_output_sha256": hashlib.sha256(b"").hexdigest(),
            "prior_output_chars": 0,
            "evidence_refs": [],
        }
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["generated_blueprints"] == 1
        assert review["llm_call_count"] == 2
        assert review["signals"][0]["prior_failure_class"] == "thinking_only_response"
        assert len(keepers) == 2
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_structured_result_mismatch_uses_bounded_schema_repair(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        _write_minimal_chief_engineer_plan(executor)
        results = [
            _invalid_structured_transport_chief_engineer_result(),
            _single_task_chief_engineer_result(),
        ]
        commands: list[Any] = []

        class _RepairingRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                return results.pop(0)

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _RepairingRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-structured-result-repair",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success"
        assert [command.task_id for command in commands] == [
            f"CE-PORTFOLIO-{run.id}",
            f"CE-PORTFOLIO-{run.id}-SCHEMA-REPAIR",
        ]
        repair_command = commands[1]
        assert repair_command.context["failure_feedback"]["failure_class"] == "output_validation_failed"
        assert repair_command.context["failure_feedback"]["detail"].startswith(
            "structured_output_payload_schema_mismatch:$:"
        )
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["generated_blueprints"] == 1
        assert review["llm_call_count"] == 2
        assert review["signals"][0]["code"] == "chief_engineer.output_schema_repair_started"
        assert review["signals"][0]["prior_failure_class"] == "output_validation_failed"
        assert len(keepers) == 2
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_thinking_only_repair_is_bounded_to_one_attempt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        _write_minimal_chief_engineer_plan(executor)
        commands: list[Any] = []

        class _AlwaysThinkingOnlyRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                return _thinking_only_chief_engineer_result()

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _AlwaysThinkingOnlyRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-thinking-only-bounded",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "failed"
        assert len(commands) == 2
        assert commands[-1].task_id.endswith("-SCHEMA-REPAIR")
        assert len(keepers) == 2
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_schema_repair_objective_is_bounded_and_excludes_corrupt_bytes(self) -> None:
        invalid_output = '{"construction_plan": <invalid>}' + (" duplicated-corruption" * 4_000)
        prior_result = _invalid_chief_engineer_stream_result(invalid_output)
        prior_result.error_message = "schema failure " + ("detail" * 2_000)

        objective = OrchestrationStageExecutor._chief_engineer_schema_repair_objective(
            prior_result=prior_result,
            portfolio_task_ids=("TASK-1", "TASK-2"),
        )

        assert invalid_output not in objective
        assert len(objective) < 5_000
        assert hashlib.sha256(invalid_output.encode("utf-8")).hexdigest() in objective
        assert f"Excluded prior output UTF-8 character count: {len(invalid_output)}" in objective
        assert 'Validated PM task ids: ["TASK-1", "TASK-2"]' in objective
        assert "placeholder syntax" in objective
        assert "project_completion_contract" in objective
        assert "project_completion_contract.obligations" in objective
        assert "artifacts, entrypoints, and verification" in objective
        contract = OrchestrationStageExecutor._chief_engineer_structured_output_contract(("TASK-1", "TASK-2"))
        assert set(contract.json_schema["required"]) == {
            "construction_plan",
            "project_completion_contract",
            "risk_flags",
        }
        assert "required top-level keys: construction_plan, project_completion_contract, risk_flags" in objective
        assert "optional scope_for_apply" in objective

    def test_chief_engineer_schema_repair_is_bounded_to_one_attempt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        _write_minimal_chief_engineer_plan(executor)
        commands: list[Any] = []

        class _AlwaysInvalidRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                return _invalid_chief_engineer_stream_result()

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _AlwaysInvalidRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-schema-repair-bounded",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "failed"
        assert len(commands) == 2
        assert commands[-1].task_id.endswith("-SCHEMA-REPAIR")
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["llm_call_count"] == 2
        assert [signal["code"] for signal in review["signals"]] == [
            "chief_engineer.output_schema_repair_started",
            "chief_engineer.llm_review_failed",
        ]
        assert len(keepers) == 2
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_execution_attempt_reuses_claim_on_replay_and_rotates_after_requeue(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        objective = "Produce one durable Chief Engineer portfolio."
        lease_budget = executor._chief_engineer_execution_attempt_lease_budget(240)

        task_id, first_attempt = executor._claim_chief_engineer_execution_attempt(
            run_id="factory-run-identity",
            portfolio_task_id="CE-PORTFOLIO-factory-run-identity",
            objective=objective,
            lease_budget=lease_budget,
        )
        replay_task_id, replay_attempt = executor._claim_chief_engineer_execution_attempt(
            run_id="factory-run-identity",
            portfolio_task_id="CE-PORTFOLIO-factory-run-identity",
            objective=objective,
            lease_budget=lease_budget,
        )

        assert replay_task_id == task_id
        assert replay_attempt.session_id == first_attempt.session_id
        assert replay_attempt.attempt == first_attempt.attempt

        task_runtime = TaskRuntimeService(str(tmp_path))
        suspended = task_runtime.settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(tmp_path),
                identity=replay_attempt,
                outcome="suspended",
                summary="retry the CE portfolio claim",
            )
        )
        assert suspended["success"] is True

        requeued_task_id, requeued_attempt = executor._claim_chief_engineer_execution_attempt(
            run_id="factory-run-identity",
            portfolio_task_id="CE-PORTFOLIO-factory-run-identity",
            objective=objective,
            lease_budget=lease_budget,
        )

        assert requeued_task_id == task_id
        assert requeued_attempt.session_id != first_attempt.session_id
        assert requeued_attempt.attempt == first_attempt.attempt + 1

    def test_chief_engineer_execution_attempt_lease_covers_admitted_long_call(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        pm_tasks = [
            {
                "id": "TASK-LONG-CE",
                "title": "Plan a long Chief Engineer review",
                "goal": "Exercise an admitted CE timeout above the historical 240 second lease.",
            }
        ]
        configured_timeout = executor._chief_engineer_llm_timeout_seconds({"chief_engineer_llm_timeout_seconds": 300})
        admission = executor._chief_engineer_deadline_projection_decision(
            {},
            requested_timeout_seconds=configured_timeout,
            dependency_schedule=build_task_dependency_schedule(pm_tasks),
        )
        assert admission.disposition is FactoryDeadlineDispositionV1.EXECUTE
        assert admission.timeout_seconds == 300
        lease_budget = executor._chief_engineer_execution_attempt_lease_budget(admission.timeout_seconds)

        task_id, attempt = executor._claim_chief_engineer_execution_attempt(
            run_id="factory-run-long-ce",
            portfolio_task_id="CE-PORTFOLIO-factory-run-long-ce",
            objective="Produce a long-running Chief Engineer portfolio.",
            lease_budget=lease_budget,
        )
        session_path = Path(resolve_logical_path(tmp_path, f"runtime/tasks/task_{task_id}.session.json"))
        claimed_session = json.loads(session_path.read_text(encoding="utf-8"))
        lease_seconds = (
            datetime.fromisoformat(claimed_session["lease_expires_at"])
            - datetime.fromisoformat(claimed_session["claimed_at"])
        ).total_seconds()
        assert lease_seconds == 330
        assert lease_seconds > admission.timeout_seconds

        executor._settle_chief_engineer_execution_attempt(
            task_id=task_id,
            execution_attempt=attempt,
            stage_status="success",
            summary="long CE review completed within admitted budget",
        )

        settled_session = json.loads(session_path.read_text(encoding="utf-8"))
        assert settled_session["status"] == "completed"
        assert settled_session["resumable"] is False
        completed_events = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
                event_type="completed",
            )
        ).events
        assert len(completed_events) == 1

    def test_chief_engineer_execution_attempt_lease_budget_is_bounded(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for env_key in stage_executor_module._CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS:
            monkeypatch.delenv(env_key, raising=False)

        maximum = stage_executor_module.MAX_LLM_PROVIDER_TIMEOUT_SECONDS
        grace = stage_executor_module._CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS
        assert (
            OrchestrationStageExecutor._chief_engineer_llm_timeout_seconds(
                {"chief_engineer_llm_timeout_seconds": maximum + 1}
            )
            == maximum
        )
        assert (
            OrchestrationStageExecutor._chief_engineer_llm_timeout_seconds(
                {"chief_engineer_llm_timeout_seconds": "1e100000"}
            )
            == maximum
        )
        assert (
            OrchestrationStageExecutor._chief_engineer_llm_timeout_seconds(
                {"chief_engineer_llm_timeout_seconds": "inf"}
            )
            == stage_executor_module._DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS
        )
        maximum_budget = OrchestrationStageExecutor._chief_engineer_execution_attempt_lease_budget(maximum)
        assert maximum_budget.lease_ttl_seconds == maximum + grace
        assert 0 < maximum_budget.heartbeat_interval_seconds < maximum_budget.lease_ttl_seconds
        with pytest.raises(ValueError, match="chief_engineer_execution_timeout_seconds_out_of_bounds"):
            OrchestrationStageExecutor._chief_engineer_execution_attempt_lease_budget(0)
        with pytest.raises(ValueError, match="chief_engineer_execution_timeout_seconds_out_of_bounds"):
            OrchestrationStageExecutor._chief_engineer_execution_attempt_lease_budget(maximum + 1)

    def test_chief_engineer_lease_renews_during_synchronous_post_processing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        _write_minimal_chief_engineer_plan(executor)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        heartbeat_calls: list[dict[str, Any]] = []
        post_processing_heartbeats_ready = threading.Event()
        original_heartbeat = stage_executor_module.heartbeat_task_runtime_execution_attempt
        original_extract = executor._ce_extract_llm_evidence
        fast_budget = stage_executor_module._ChiefEngineerExecutionAttemptLeaseBudget(
            lease_ttl_seconds=1,
            heartbeat_interval_seconds=0.05,
        )
        renewals_to_cross_initial_lease = (
            int(fast_budget.lease_ttl_seconds / fast_budget.heartbeat_interval_seconds) + 2
        )
        monkeypatch.setattr(
            executor,
            "_chief_engineer_execution_attempt_lease_budget",
            lambda _timeout: fast_budget,
        )

        def _record_heartbeat(command: Any) -> Any:
            result = original_heartbeat(command)
            heartbeat_calls.append(
                {
                    "task_id": command.identity.task_id,
                    "session_id": command.identity.session_id,
                    "lease_ttl_seconds": command.lease_ttl_seconds,
                }
            )
            if len(heartbeat_calls) >= renewals_to_cross_initial_lease:
                post_processing_heartbeats_ready.set()
            return result

        def _blocking_extract(ce_result: Any, *, task_id: str, run_id: str) -> dict[str, Any]:
            assert post_processing_heartbeats_ready.wait(timeout=2.0)
            return original_extract(ce_result, task_id=task_id, run_id=run_id)

        class _SuccessfulRoleRuntimeService:
            async def execute_role_task(self, _command: Any) -> Any:
                return _single_task_chief_engineer_result()

        monkeypatch.setattr(stage_executor_module, "heartbeat_task_runtime_execution_attempt", _record_heartbeat)
        monkeypatch.setattr(executor, "_ce_extract_llm_evidence", _blocking_extract)
        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _SuccessfulRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-ce-heartbeat-post-processing",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success"
        assert len(keepers) == 1
        keeper = keepers[0]
        assert keeper.heartbeat_count >= renewals_to_cross_initial_lease
        assert keeper.is_alive is False
        _assert_no_chief_engineer_lease_keeper_threads()
        assert len(heartbeat_calls) >= renewals_to_cross_initial_lease
        assert len(heartbeat_calls) * fast_budget.heartbeat_interval_seconds > fast_budget.lease_ttl_seconds
        assert all(call["task_id"] == keeper.task_id for call in heartbeat_calls)
        assert all(call["session_id"] == keeper.execution_attempt.session_id for call in heartbeat_calls)
        assert all(call["lease_ttl_seconds"] == fast_budget.lease_ttl_seconds for call in heartbeat_calls)
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.get_task(f"CE-PORTFOLIO-{run.id}")
        assert task is not None
        session_path = Path(resolve_logical_path(tmp_path, f"runtime/tasks/task_{task['id']}.session.json"))
        session = json.loads(session_path.read_text(encoding="utf-8"))
        assert session["status"] == "completed"

    def test_chief_engineer_heartbeat_failure_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        executor = _executor(tmp_path)
        _write_minimal_chief_engineer_plan(executor)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        heartbeat_failed = threading.Event()
        heartbeat_recovered = threading.Event()
        heartbeat_calls = 0
        original_heartbeat = stage_executor_module.heartbeat_task_runtime_execution_attempt
        fast_budget = stage_executor_module._ChiefEngineerExecutionAttemptLeaseBudget(
            lease_ttl_seconds=2,
            heartbeat_interval_seconds=0.01,
        )
        monkeypatch.setattr(
            executor,
            "_chief_engineer_execution_attempt_lease_budget",
            lambda _timeout: fast_budget,
        )

        def _fail_then_renew_heartbeat(command: Any) -> Any:
            nonlocal heartbeat_calls
            heartbeat_calls += 1
            if heartbeat_calls == 1:
                heartbeat_failed.set()
                return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
                    success=False,
                    code="file_lock_timeout",
                    workspace=command.workspace,
                    identity=command.identity,
                    evidence_anchor={"synthetic": True},
                )
            result = original_heartbeat(command)
            heartbeat_recovered.set()
            return result

        class _SuccessfulRoleRuntimeService:
            async def execute_role_task(self, _command: Any) -> Any:
                assert await asyncio.to_thread(heartbeat_failed.wait, 2.0)
                assert await asyncio.to_thread(heartbeat_recovered.wait, 2.0)
                return _single_task_chief_engineer_result()

        monkeypatch.setattr(
            stage_executor_module,
            "heartbeat_task_runtime_execution_attempt",
            _fail_then_renew_heartbeat,
        )
        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _SuccessfulRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-ce-heartbeat-failed",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        with caplog.at_level(logging.ERROR, logger=stage_executor_module.__name__):
            result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success"
        assert "code=chief_engineer.execution_attempt_heartbeat_failed" in caplog.text
        assert "file_lock_timeout" in caplog.text
        assert len(keepers) == 1
        assert keepers[0].is_alive is False
        _assert_no_chief_engineer_lease_keeper_threads()
        assert keepers[0].heartbeat_count >= 1
        assert heartbeat_calls >= 2
        assert keepers[0].failure is None
        assert keepers[0].incidents[0].reason == "file_lock_timeout"
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.get_task(f"CE-PORTFOLIO-{run.id}")
        assert task is not None
        session_path = Path(resolve_logical_path(tmp_path, f"runtime/tasks/task_{task['id']}.session.json"))
        session = json.loads(session_path.read_text(encoding="utf-8"))
        assert session["status"] == "completed"
        completed_events = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
                event_type="completed",
            )
        ).events
        assert len(completed_events) == 1

    def test_chief_engineer_lease_keeper_records_system_exit_as_fail_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A BaseException cannot silently terminate the keeper thread."""

        entered = threading.Event()
        identity = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=str(tmp_path),
            task_id=91,
            external_task_id="CE-PORTFOLIO-system-exit",
            session_id="system-exit-session",
            attempt=1,
            role_id="chief_engineer",
            worker_id="factory-chief-engineer",
            run_id="system-exit-run",
            lease_expires_at="2026-07-14T00:05:00+00:00",
        )
        keeper = stage_executor_module._ChiefEngineerExecutionAttemptLeaseKeeper(
            workspace=str(tmp_path),
            task_id=identity.task_id,
            execution_attempt=identity,
            budget=stage_executor_module._ChiefEngineerExecutionAttemptLeaseBudget(
                lease_ttl_seconds=2,
                heartbeat_interval_seconds=0.01,
            ),
        )

        def _raise_system_exit(_command: Any) -> Any:
            entered.set()
            raise SystemExit("synthetic keeper boundary")

        monkeypatch.setattr(stage_executor_module, "heartbeat_task_runtime_execution_attempt", _raise_system_exit)
        keeper.start()
        assert entered.wait(timeout=2)
        stopped = keeper.stop()

        assert stopped.thread_exited is True
        assert keeper.failure is not None
        assert keeper.failure.error_type == "SystemExit"
        assert keeper.incidents[-1].reason == "heartbeat_exception"
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_blocked_heartbeat_blocks_settlement_without_deadlock(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unresolved heartbeat makes settlement fail closed rather than race it."""

        entered = threading.Event()
        release = threading.Event()
        identity = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=str(tmp_path),
            task_id=92,
            external_task_id="CE-PORTFOLIO-blocked",
            session_id="blocked-session",
            attempt=1,
            role_id="chief_engineer",
            worker_id="factory-chief-engineer",
            run_id="blocked-run",
            lease_expires_at="2026-07-14T00:05:00+00:00",
        )
        keeper = stage_executor_module._ChiefEngineerExecutionAttemptLeaseKeeper(
            workspace=str(tmp_path),
            task_id=identity.task_id,
            execution_attempt=identity,
            budget=stage_executor_module._ChiefEngineerExecutionAttemptLeaseBudget(
                lease_ttl_seconds=2,
                heartbeat_interval_seconds=0.02,
            ),
        )
        scope = stage_executor_module._ChiefEngineerExecutionAttemptLeaseScope()
        scope.bind_claim(task_id=identity.task_id, execution_attempt=identity)

        def _block_heartbeat(command: Any) -> Any:
            entered.set()
            assert release.wait(timeout=2)
            return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
                success=True,
                code="heartbeat_renewed",
                workspace=command.workspace,
                identity=command.identity,
                renewed_identity=command.identity,
            )

        monkeypatch.setattr(stage_executor_module, "heartbeat_task_runtime_execution_attempt", _block_heartbeat)
        scope.start_keeper(keeper)
        assert entered.wait(timeout=2)
        started_at = time.monotonic()
        should_settle, failure = scope.begin_settlement()
        elapsed_seconds = time.monotonic() - started_at
        assert should_settle is False
        assert failure is not None
        assert failure.reason == "heartbeat_thread_stop_timeout"
        assert elapsed_seconds < 0.5

        release.set()
        assert keeper.stop().thread_exited is True
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_heartbeat_failure_does_not_mask_cancellation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        executor = _executor(tmp_path)
        _write_minimal_chief_engineer_plan(executor)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        heartbeat_failed = threading.Event()
        cancellation = asyncio.CancelledError("canonical cancellation after heartbeat failure")
        fast_budget = stage_executor_module._ChiefEngineerExecutionAttemptLeaseBudget(
            lease_ttl_seconds=2,
            heartbeat_interval_seconds=0.01,
        )
        monkeypatch.setattr(
            executor,
            "_chief_engineer_execution_attempt_lease_budget",
            lambda _timeout: fast_budget,
        )

        def _raise_heartbeat(_command: Any) -> Any:
            heartbeat_failed.set()
            raise RuntimeError("heartbeat_failed_before_cancellation")

        class _CancelledRoleRuntimeService:
            async def execute_role_task(self, _command: Any) -> Any:
                assert await asyncio.to_thread(heartbeat_failed.wait, 2.0)
                raise cancellation

        monkeypatch.setattr(stage_executor_module, "heartbeat_task_runtime_execution_attempt", _raise_heartbeat)
        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _CancelledRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-ce-heartbeat-failed-cancelled",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        with (
            caplog.at_level(logging.ERROR, logger=stage_executor_module.__name__),
            pytest.raises(asyncio.CancelledError) as raised,
        ):
            asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert raised.value is cancellation
        assert "heartbeat_failed_before_cancellation" in caplog.text
        assert len(keepers) == 1
        assert keepers[0].is_alive is False
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_review_uses_one_portfolio_call_for_multiple_tasks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        delivery_plan_document = {
            "schema_version": "polaris.delivery_plan_document.v1",
            "product_summary": {
                "intent": "Deliver a coherent weather engine and CLI.",
                "core_terms": ["planet", "weather", "cloud", "wind"],
            },
        }
        delivery_depth_contract = {
            "schema_version": "polaris.delivery_depth_contract.v1",
            "product_intent": {
                "subject": "planet weather",
                "primary_entities": ["planet", "weather", "cloud", "wind"],
            },
        }
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Build weather models",
                        "goal": "Implement weather and wind domain models.",
                        "target_files": ["src/models/weather.py"],
                        "scope_paths": ["src/models/weather.py"],
                        "acceptance_criteria": ["weather model validates cloud and wind"],
                        "execution_checklist": ["Implement immutable weather model"],
                        "delivery_plan_document": delivery_plan_document,
                        "delivery_depth_contract": delivery_depth_contract,
                    },
                    {
                        "id": "TASK-2",
                        "title": "Build forecast engine",
                        "goal": "Use the weather model from a forecast engine.",
                        "depends_on": ["TASK-1"],
                        "target_files": ["src/engine/forecast.py", "tests/test_forecast.py"],
                        "scope_paths": ["src/engine/forecast.py", "tests/test_forecast.py"],
                        "acceptance_criteria": ["forecast consumes the shared weather model"],
                        "execution_checklist": ["Implement forecast rules"],
                        "delivery_plan_document": delivery_plan_document,
                        "delivery_depth_contract": delivery_depth_contract,
                    },
                ]
            },
        )
        calls: list[Any] = []

        class _PortfolioRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                calls.append(command)
                ce_output = {
                    "construction_plan": {
                        "project_design_intent": "Keep domain state independent from forecast orchestration.",
                        "project_interface_contract": {
                            "provider_declarations": [
                                {
                                    "path": "src/models/weather.py",
                                    "name": "WeatherReport",
                                    "symbol_kind": "class",
                                    "signature": "WeatherReport(cloud: float, wind: float)",
                                }
                            ],
                            "consumer_declarations": [
                                {
                                    "path": "src/engine/forecast.py",
                                    "name": "WeatherReport",
                                    "provider_path": "src/models/weather.py",
                                }
                            ],
                        },
                        "task_plans": {
                            "TASK-1": {
                                "implementation": ["Define WeatherReport and validation boundaries"],
                                "verification": ["Validate cloud and wind boundaries"],
                            },
                            "TASK-2": {
                                "implementation": [
                                    "Import WeatherReport and map planet weather, cloud, and wind forecast rules"
                                ],
                                "verification": [
                                    "Exercise the planet weather provider-consumer contract for cloud and wind"
                                ],
                            },
                        },
                    },
                    "scope_for_apply": ["src/models/weather.py", "src/engine/forecast.py"],
                    "risk_flags": [],
                    "project_completion_contract": _library_completion_requirements(
                        "src/models/weather.py",
                        "src/engine/forecast.py",
                        owner_task_ids=("TASK-1", "TASK-2"),
                        test_path="tests/test_forecast.py",
                        test_owner_task_id="TASK-2",
                    ),
                }
                return SimpleNamespace(
                    ok=True,
                    output=json.dumps(ce_output),
                    error_message="",
                    error_code="",
                    metadata={
                        "provider_id": "test-provider",
                        "model": "test-model",
                        "structured_output": ce_output,
                        "final_request_context_audit": {"context_window_utilization": 0.35},
                        "context_snapshot_ref": "abcdef123456abcdef123456",
                    },
                    usage={},
                )

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _PortfolioRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-portfolio",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success"
        assert len(calls) == 1
        assert calls[0].task_id == "CE-PORTFOLIO-factory-run-portfolio"
        assert calls[0].context["delivery_mode"] == "analyze_only"
        assert calls[0].context["llm_max_tokens"] == 16_384
        assert calls[0].context["task_count"] == 2
        assert len(calls[0].context["pm_task_contract"]["tasks"]) == 2
        review_path = Path(resolve_logical_path(tmp_path, "runtime/state/blueprints/factory-run-portfolio.review.json"))
        review = json.loads(review_path.read_text(encoding="utf-8"))
        assert review["llm_call_count"] == 1
        assert review["generated_blueprints"] == 2
        assert review["portfolio"]["portfolio_hash"]
        assert review["portfolio"]["project_completion_contract_hash"]
        assert review["project_interface_contract"]["project_interface_contract_hash"]
        blueprints = [
            BlueprintPersistence(str(tmp_path), ensure_directory=False).load(row["blueprint_id"])
            for row in review["blueprints"]
        ]
        assert all(isinstance(blueprint, dict) for blueprint in blueprints)
        portfolio_hashes = {str(blueprint["blueprint_portfolio_hash"]) for blueprint in blueprints if blueprint}
        interface_hashes = {str(blueprint["project_interface_contract_hash"]) for blueprint in blueprints if blueprint}
        assert portfolio_hashes == {review["portfolio"]["portfolio_hash"]}
        assert interface_hashes == {review["project_interface_contract"]["project_interface_contract_hash"]}

    def test_chief_engineer_review_fails_closed_after_llm_timeout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        delivery_plan_document = {
            "schema_version": "polaris.delivery_plan_document.v1",
            "language": "javascript",
            "product_summary": {
                "intent": "Deliver a meteor wish queue.",
                "core_terms": ["meteor", "wish", "queue", "priority"],
            },
        }
        delivery_depth_contract = {
            "schema_version": "polaris.delivery_depth_contract.v1",
            "language": "javascript",
            "product_intent": {
                "subject": "meteor wish queue",
                "primary_entities": ["meteor", "wish", "queue", "priority"],
            },
        }
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-CORE",
                        "title": "实现 流星愿望队列 - core engine/service modules",
                        "goal": "在工作区根交付 流星愿望队列。 Scope this task to core engine/service modules only.",
                        "target_files": ["src/engine/rules.js", "src/engine/runner.js"],
                        "scope_paths": ["src/engine/rules.js", "src/engine/runner.js"],
                        "acceptance_criteria": [
                            "verify src/engine/rules.js exists",
                            "verify src/engine/runner.js exists",
                        ],
                        "execution_checklist": ["Materialize only the listed core engine files."],
                        "delivery_plan_document": delivery_plan_document,
                        "delivery_depth_contract": delivery_depth_contract,
                    }
                ]
            },
        )

        class _TimeoutRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                return SimpleNamespace(
                    ok=False,
                    status="failed",
                    output="",
                    error_code="provider_timeout",
                    error_message="Request timeout (55.0s)",
                    metadata={
                        "provider_id": "kimi",
                        "model": "kimi-for-coding",
                    },
                    usage={},
                )

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _TimeoutRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-timeout-projection",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "failed"
        review_path = Path(
            resolve_logical_path(tmp_path, "runtime/state/blueprints/factory-run-timeout-projection.review.json")
        )
        review = json.loads(review_path.read_text(encoding="utf-8"))
        assert review["generated_blueprints"] == 0
        assert [signal["severity"] for signal in review["signals"]] == ["error"]
        signal = review["signals"][0]
        assert signal["code"] == "chief_engineer.llm_review_failed"
        assert signal["severity"] == "error"
        assert signal["recoverable"] is False
        assert review["portfolio"] == {}
        assert review["llm_call_count"] == 1
        assert len(keepers) == 1
        assert keepers[0].is_alive is False
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_cancellation_suspends_claimed_attempt_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        _write_minimal_chief_engineer_plan(executor)

        class _CancelledRoleRuntimeService:
            async def execute_role_task(self, _command: Any) -> Any:
                raise asyncio.CancelledError("test CE cancellation")

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _CancelledRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-ce-cancelled",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        with pytest.raises(asyncio.CancelledError, match="test CE cancellation"):
            asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.get_task(f"CE-PORTFOLIO-{run.id}")
        assert task is not None
        session_path = Path(resolve_logical_path(tmp_path, f"runtime/tasks/task_{task['id']}.session.json"))
        session = json.loads(session_path.read_text(encoding="utf-8"))
        assert session["status"] == "suspended"
        assert session["resumable"] is True

        suspended_events = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
                event_type="suspended",
            )
        ).events
        terminal_events = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
            )
        ).events
        assert len(suspended_events) == 1
        assert [event["event_type"] for event in terminal_events].count("suspended") == 1
        assert all(event["event_type"] not in {"completed", "failed"} for event in terminal_events)
        assert len(keepers) == 1
        assert keepers[0].is_alive is False
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_cancellation_survives_settlement_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        _write_minimal_chief_engineer_plan(executor)
        cancellation = asyncio.CancelledError("original CE cancellation")
        settlement_calls: list[dict[str, Any]] = []

        class _CancelledRoleRuntimeService:
            async def execute_role_task(self, _command: Any) -> Any:
                raise cancellation

        def _fail_settlement(**kwargs: Any) -> None:
            settlement_calls.append(dict(kwargs))
            raise RuntimeError("synthetic settlement failure")

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _CancelledRoleRuntimeService)
        monkeypatch.setattr(executor, "_settle_chief_engineer_execution_attempt", _fail_settlement)
        run = FactoryRun(
            id="factory-run-ce-cancelled-settlement-failure",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        with (
            caplog.at_level(logging.ERROR, logger=stage_executor_module.__name__),
            pytest.raises(asyncio.CancelledError) as raised,
        ):
            asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert raised.value is cancellation
        assert len(settlement_calls) == 1
        assert "Chief Engineer cancellation settlement failed" in caplog.text
        assert "synthetic settlement failure" in caplog.text

        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.get_task(f"CE-PORTFOLIO-{run.id}")
        assert task is not None
        session_path = Path(resolve_logical_path(tmp_path, f"runtime/tasks/task_{task['id']}.session.json"))
        session = json.loads(session_path.read_text(encoding="utf-8"))
        assert session["status"] == "active"
        execution_events = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
            )
        ).events
        assert all(event["event_type"] not in {"completed", "failed", "suspended"} for event in execution_events)
        assert len(keepers) == 1
        assert keepers[0].is_alive is False
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_review_blocks_without_deadline_projection_or_llm_call(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        delivery_plan_document = {
            "schema_version": "polaris.delivery_plan_document.v1",
            "product_summary": {
                "intent": "Deliver a meteor wish queue.",
                "core_terms": ["meteor", "wish", "queue", "priority"],
            },
        }
        delivery_depth_contract = {
            "schema_version": "polaris.delivery_depth_contract.v1",
            "product_intent": {
                "subject": "meteor wish queue",
                "primary_entities": ["meteor", "wish", "queue", "priority"],
            },
        }
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Build meteor wish queue",
                        "goal": "Build a meteor wish queue with prioritization behavior.",
                        "target_files": ["package.json", "src/index.js", "src/engine/rules.js"],
                        "scope_paths": ["package.json", "src/index.js", "src/engine/rules.js"],
                        "acceptance_criteria": ["npm test passes", "npm start prints queue status"],
                        "execution_checklist": ["Implement queue model", "Implement prioritization rules"],
                        "delivery_plan_document": delivery_plan_document,
                        "delivery_depth_contract": delivery_depth_contract,
                    },
                    {
                        "id": "TASK-2",
                        "title": "Add meteor wish queue tests",
                        "goal": "Validate meteor wish queue prioritization behavior.",
                        "target_files": ["tests/product.test.js"],
                        "scope_paths": ["tests/product.test.js"],
                        "acceptance_criteria": ["npm test covers normal and boundary queues"],
                        "execution_checklist": ["Add tests for priority ordering"],
                        "delivery_plan_document": delivery_plan_document,
                        "delivery_depth_contract": delivery_depth_contract,
                    },
                ]
            },
        )

        class _UnexpectedRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                raise AssertionError(f"CE LLM should not be called under deadline projection: {command!r}")

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _UnexpectedRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-projection",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 155.0

        result = asyncio.run(
            executor._execute_chief_engineer_review(
                run,
                _factory_stage_context(
                    {
                        "factory_run_deadline_epoch_seconds": deadline_epoch,
                        "director_first_materialization_min_budget_seconds": 60,
                        "quality_gate_reserved_budget_seconds": 20,
                    }
                ),
            )
        )

        assert result.status == "failed"
        review_path = Path(
            resolve_logical_path(tmp_path, "runtime/state/blueprints/factory-run-projection.review.json")
        )
        review = json.loads(review_path.read_text(encoding="utf-8"))
        assert [signal["code"] for signal in review["signals"]] == [
            "chief_engineer.deadline_admission_blocked",
        ]
        assert review["blueprints"] == []
        assert review["portfolio"] == {}
        assert review["llm_call_count"] == 0

    def test_director_handoff_guard_allows_ready_blueprint(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = _generate_domain_blueprint(
            tmp_path,
            task_id="TASK-1",
            objective="Build pirate treasure budget planner",
            target_files=["models/capsule.go", "engine/museum.go"],
            acceptance_criteria=[
                "treasure, budget, port, and reef behavior tests pass",
                "go test ./... passes",
            ],
            execution_checklist=[
                "Implement treasure and budget models",
                "Implement port fee and reef risk rules",
            ],
        )
        assert result.ok is True
        _write_review_for_blueprint(
            executor,
            run_id="run-1",
            task_id="TASK-1",
            blueprint_id=result.blueprint_id,
        )

        signals = executor._chief_engineer_handoff_signals_for_director(
            [{"id": "TASK-1", "target_files": ["models/capsule.go"]}],
            run_id="run-1",
        )

        assert signals == []

    def test_director_handoff_guard_blocks_missing_blueprint(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)

        signals = executor._chief_engineer_handoff_signals_for_director(
            [{"id": "TASK-1", "target_files": ["models/capsule.go"]}],
            run_id="run-1",
        )

        assert [signal["code"] for signal in signals] == ["director.chief_engineer_handoff_missing"]
        assert signals[0]["severity"] == "error"

    def test_director_handoff_guard_does_not_use_stale_persisted_blueprint_without_review(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = _generate_domain_blueprint(
            tmp_path,
            task_id="TASK-1",
            objective="Build pirate treasure budget planner",
            target_files=["models/capsule.go", "engine/museum.go"],
            acceptance_criteria=[
                "treasure, budget, port, and reef behavior tests pass",
                "go test ./... passes",
            ],
            execution_checklist=[
                "Implement treasure and budget models",
                "Implement port fee and reef risk rules",
            ],
        )
        assert result.ok is True

        signals = executor._chief_engineer_handoff_signals_for_director(
            [{"id": "TASK-1", "target_files": ["models/capsule.go"]}],
            run_id="different-run-without-review",
        )

        assert [signal["code"] for signal in signals] == ["director.chief_engineer_handoff_missing"]
        assert signals[0]["severity"] == "error"

    def test_director_handoff_guard_blocks_unready_blueprint(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = _generate_domain_blueprint(
            tmp_path,
            task_id="TASK-1",
            objective="Build flavor recipe planner",
            target_files=["models/flavor.go", "engine/palette.go"],
            acceptance_criteria=["recipe behavior tests pass", "go test ./... passes"],
            execution_checklist=["Implement flavor model", "Implement palette rules"],
        )
        assert result.ok is True
        _write_review_for_blueprint(
            executor,
            run_id="run-1",
            task_id="TASK-1",
            blueprint_id=result.blueprint_id,
        )

        signals = executor._chief_engineer_handoff_signals_for_director(
            [{"id": "TASK-1", "target_files": ["models/flavor.go"]}],
            run_id="run-1",
        )

        assert [signal["code"] for signal in signals] == ["director.chief_engineer_handoff_blocked"]
        assert signals[0]["severity"] == "error"
        assert signals[0]["blockers"]


class TestTextShapingHelpers:
    def test_compact_text_under_limit_returns_stripped(self) -> None:
        assert OrchestrationStageExecutor._compact_text_for_prompt("  hello  ", max_chars=100) == "hello"

    def test_compact_text_over_limit_inserts_omission_marker(self) -> None:
        text = "A" * 60 + "B" * 60
        result = OrchestrationStageExecutor._compact_text_for_prompt(text, max_chars=30)
        assert "[... omitted" in result
        assert "chars for PM planning context ...]" in result
        # head=20 (30*2//3), tail=10, omitted=120-20-10=90
        assert "omitted 90 chars" in result
        assert result.startswith("A" * 20)
        assert result.endswith("B" * 10)

    def test_compact_text_handles_none(self) -> None:
        assert OrchestrationStageExecutor._compact_text_for_prompt(None, max_chars=10) == ""  # type: ignore[arg-type]

    def test_compact_workspace_quality_evidence_for_qa_preserves_parseable_failure(self) -> None:
        payload = {
            "schema_version": "factory.workspace_quality_checks.v1",
            "source": "factory_stage_executor",
            "factory_run_id": "factory-run-1",
            "workspace": "/tmp/project",
            "passed": False,
            "commands": [
                {
                    "command": ["npm", "install"],
                    "phase": "prepare",
                    "passed": True,
                    "exit_code": 0,
                    "stdout_tail": "ok\n" + ("x" * 10_000),
                },
                {
                    "command": ["npm", "run", "build"],
                    "phase": "check",
                    "passed": False,
                    "exit_code": 2,
                    "stdout_tail": "src/app.ts(1,1): error TS1005\n" + ("y" * 10_000),
                },
            ],
            "repair": {
                "attempted": True,
                "success": False,
                "source_tools": ["deterministic_ts"],
                "evidence": ["repair failed " + ("z" * 1000)],
            },
        }

        compact = OrchestrationStageExecutor._compact_workspace_quality_evidence_for_qa(
            json.dumps(payload, ensure_ascii=False)
        )
        summary = extract_workspace_quality_summary(compact)

        assert summary is not None
        assert summary["passed"] is False
        assert summary["command_count"] == 2
        assert summary["prepare_passed_count"] == 1
        assert summary["check_passed_count"] == 0
        assert summary["repair_attempted"] is True
        assert summary["repair_success"] is False

    def test_workspace_quality_repair_original_message_uses_data_plane_blueprint_summary(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Build canvas flight entrypoint",
                        "goal": "Render a paper plane flight canvas.",
                        "scope": "index.html, src/web.ts",
                        "target_files": ["index.html", "src/web.ts"],
                        "steps": ["Create browser bootstrap", "Draw a non-empty first frame"],
                        "acceptance": ["npm run build passes", "canvas paints pixels"],
                        "metadata": {"internal": "not prompt data"},
                    }
                ]
            },
        )
        executor._write_json_artifact(
            "runtime/state/blueprints/factory-run.review.json",
            {
                "schema_version": "factory.chief_engineer_review.v1",
                "source": "factory_stage_executor",
                "factory_run_id": "factory-run",
                "generated_blueprints": 1,
                "total_tasks": 1,
                "blueprints": [
                    {
                        "task_id": "TASK-1",
                        "status": "generated",
                        "blueprint_id": "ce_TASK-1_test",
                        "blueprint_path": "runtime/blueprints/ce_TASK-1_test.json",
                        "summary": "Use src/web.ts as the browser bootstrap for index.html.",
                        "recommendations": ["Keep DOM canvas code out of the Node CLI entrypoint."],
                        "risks": ["API drift between renderer and models."],
                    }
                ],
                "metadata": {"internal": "not prompt data"},
            },
        )

        message = executor._workspace_quality_repair_original_message(
            run_id="factory-run",
            target_files=["index.html", "src/web.ts"],
        )

        assert "Chief Engineer blueprint evidence" in message
        assert "ce_TASK-1_test" in message
        assert "Build canvas flight entrypoint" in message
        assert "factory_run_id" not in message
        assert '"metadata"' not in message
        assert '"source"' not in message

    def test_workspace_quality_repair_original_message_uses_workspace_local_blueprint_summary(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Build package entrypoint",
                        "goal": "Deliver a runnable npm project.",
                        "target_files": ["package.json", "src/index.js"],
                    }
                ]
            },
        )
        blueprint_dir = tmp_path / ".polaris" / "blueprints"
        blueprint_dir.mkdir(parents=True)
        (blueprint_dir / "latest.review.json").write_text(
            json.dumps(
                {
                    "schema_version": "factory.chief_engineer_review.v1",
                    "generated_blueprints": 1,
                    "blueprints": [
                        {
                            "task_id": "TASK-1",
                            "status": "generated",
                            "blueprint_id": "ce_TASK-1_workspace_local",
                            "summary": "Keep the package entrypoint aligned with declared exports.",
                            "recommendations": ["Do not shrink the manifest around missing targets."],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        message = executor._workspace_quality_repair_original_message(
            run_id="factory-run",
            target_files=["package.json", "src/index.js"],
        )

        assert "Chief Engineer blueprint evidence" in message
        assert "workspace-local:.polaris/blueprints/latest.review.json" in message
        assert "ce_TASK-1_workspace_local" in message
        assert "Do not shrink the manifest" in message

    def test_workspace_quality_runtime_repair_task_carries_workspace_blueprint_metadata(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Build package entrypoint",
                        "goal": "Deliver a runnable npm project.",
                        "target_files": ["package.json", "src/index.js"],
                    }
                ]
            },
        )
        blueprint_dir = tmp_path / ".polaris" / "blueprints"
        blueprint_dir.mkdir(parents=True)
        (blueprint_dir / "factory-run.review.json").write_text(
            json.dumps(
                {
                    "schema_version": "factory.chief_engineer_review.v1",
                    "generated_blueprints": 1,
                    "blueprints": [
                        {
                            "task_id": "TASK-1",
                            "status": "generated",
                            "blueprint_id": "ce_TASK-1_runtime_schedule",
                            "summary": "Runtime repair must retain CE handoff evidence.",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        captured: dict[str, Any] = {}

        def fake_run_schedule(adapter: Any, *, task: dict[str, Any], task_id: str, artifact_quality_errors: list[str]):
            del adapter, task_id, artifact_quality_errors
            captured["task"] = task
            return [], {"attempted": False}

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.run_director_materialization_quality_repair_schedule",
            fake_run_schedule,
        )

        executor._apply_workspace_quality_repairs(
            run_id="factory-run",
            artifact_quality_errors=["Artifact quality scan failed: workspace validation command failed"],
        )

        metadata = captured["task"]["metadata"]
        assert metadata["ce_blueprint"]["artifact"] == "workspace-local:.polaris/blueprints/factory-run.review.json"
        assert "ce_TASK-1_runtime_schedule" in metadata["ce_blueprint"]["evidence"]
        assert metadata["factory_workspace_quality_repair"]["target_files"] == ["package.json", "src/index.js"]

    def test_quality_repair_prompt_compaction_preserves_blueprint_after_four_goals(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
        original_message = "\n".join(
            [
                "Factory workspace quality repair contract:",
                "goal: deliver manifest",
                "goal: deliver core engine",
                "goal: deliver supporting modules",
                "goal: deliver runtime entrypoint",
                "- Chief Engineer blueprint evidence:",
                "  artifact: workspace-local:.polaris/blueprints/latest.review.json",
                '  "blueprint_id": "ce_TASK-1_preserve_blueprint"',
                '  "summary": "Keep manifest scripts aligned with declared targets."',
                '  "recommendations": ["Create missing entrypoint instead of shrinking scripts."]',
            ]
        )

        message = build_director_materialization_quality_repair_message(
            original_message=original_message,
            artifact_quality_errors=["Artifact quality scan failed: npm run build references missing src/index.js"],
            changed_files=["package.json"],
            repair_target_files=["package.json"],
            workspace_full=str(tmp_path),
        )

        assert "Chief Engineer blueprint evidence" in message
        assert "ce_TASK-1_preserve_blueprint" in message
        assert "Create missing entrypoint instead of shrinking scripts" in message

    def test_strip_prompt_meta_lines_removes_matching_lines(self) -> None:
        text = "keep this\n这是提示词内容\nalso keep\nsystem prompt here\nfinal"
        result = OrchestrationStageExecutor._strip_prompt_meta_lines(text)
        assert result == "keep this\nalso keep\nfinal"

    def test_strip_prompt_meta_lines_empty(self) -> None:
        assert OrchestrationStageExecutor._strip_prompt_meta_lines("") == ""

    def test_is_substantive_doc_text_requires_two_headings_and_min_chars(self) -> None:
        good = "# Title\n" + ("body " * 60) + "\n## Section\nmore"
        assert OrchestrationStageExecutor._is_substantive_doc_text(good) is True
        assert OrchestrationStageExecutor._is_substantive_doc_text("# one\nshort") is False
        assert OrchestrationStageExecutor._is_substantive_doc_text("x" * 300) is False


class TestDeliveryTargetNormalization:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("./src/app.py", "src/app.py"),
            ("`workspace/src/app.py`", "src/app.py"),
            ("https://example.com", ""),
            ("#anchor", ""),
            ("runtime/x.py", ""),
            (".git/config", ""),
            (".polaris/state", ""),
            ("src/", ""),
            ("", ""),
            ("../escape.py", ""),
            ("/abs/path.py", "abs/path.py"),
            ('func main() {\n\tprintln("not a path")\n}', ""),
            (" SortedExhibits returns exhibits ordered by Position ascending.", ""),
            ("src/" + ("x" * 241) + ".go", ""),
        ],
    )
    def test_normalize_declared_delivery_target(self, value: str, expected: str) -> None:
        assert OrchestrationStageExecutor._normalize_declared_delivery_target(value) == expected

    def test_collect_declared_delivery_targets_rejects_code_fragments(self, tmp_path: Path) -> None:
        source_fragment = (
            " SortedExhibits returns exhibits ordered by Position ascending.\n"
            "func (g *Gallery) SortedExhibits() []*Exhibit {\n"
            "\treturn nil\n"
            "}\n"
        )
        executor = _executor(tmp_path)

        targets = executor._collect_declared_delivery_targets(
            [
                {
                    "target_files": ["models/gallery.go", source_fragment],
                    "steps": [source_fragment, "run go test ./..."],
                }
            ]
        )

        assert targets == ["models/gallery.go"]

    def test_missing_declared_delivery_targets_handles_invalid_pathlike_input(self, tmp_path: Path) -> None:
        source_fragment = " SortedExhibits returns exhibits ordered by Position ascending.\nfunc broken() {}"
        executor = _executor(tmp_path)

        missing = executor._missing_declared_delivery_targets(
            [{"target_files": ["models/gallery.go", source_fragment]}]
        )

        assert missing == ["models/gallery.go"]

    def test_extend_artifacts_dedupes_and_normalizes(self) -> None:
        artifacts: list[str] = ["a/b.py"]
        OrchestrationStageExecutor._extend_artifacts(artifacts, "a\\b.py", "c.py", "", "c.py")
        assert artifacts == ["a/b.py", "c.py"]


class TestBoolFromContextOrEnv:
    def test_context_value_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_ENV", "false")
        assert (
            OrchestrationStageExecutor._bool_from_context_or_env(
                {"flag": True}, "flag", env_var="MY_ENV", default=False
            )
            is True
        )

    def test_env_fallback_truthy_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_ENV", "on")
        assert OrchestrationStageExecutor._bool_from_context_or_env({}, "flag", env_var="MY_ENV", default=False) is True

    def test_env_fallback_falsy_tokens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_ENV", "disabled")
        assert OrchestrationStageExecutor._bool_from_context_or_env({}, "flag", env_var="MY_ENV", default=True) is False

    def test_default_when_unset(self) -> None:
        assert OrchestrationStageExecutor._bool_from_context_or_env({}, "flag", default=True) is True

    def test_unrecognized_token_returns_default(self) -> None:
        assert OrchestrationStageExecutor._bool_from_context_or_env({"flag": "maybe"}, "flag", default=True) is True


class TestPMDeterministicContractMetadata:
    def test_no_metadata_keeps_pm_llm_path(self) -> None:
        run = FactoryRun(
            id="factory-no-bench",
            config=FactoryConfig(name="regular-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        assert OrchestrationStageExecutor._pm_deterministic_contract_metadata_for_context(run, {}) == {}

    def test_factory_bench_metadata_enables_preemptive_deterministic_pm(self) -> None:
        run = FactoryRun(
            id="factory-bench",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
            metadata={
                "factory_start_request": {
                    "metadata": {
                        "factory_bench_project_id": "L1-06",
                        "factory_bench_level": 1,
                    }
                }
            },
        )

        metadata = OrchestrationStageExecutor._pm_deterministic_contract_metadata_for_context(run, {})

        assert metadata["deterministic_pm_contracts"] is True
        assert metadata["factory_bench_project_id"] == "L1-06"
        assert metadata["factory_bench_deterministic_pm"] is True
        assert metadata["pm_route_audit_probe"] is True
        assert metadata["factory_recovery"] == "bench_preemptive_deterministic_contracts"

    def test_explicit_context_flag_enables_deterministic_pm_without_bench_semantics(self) -> None:
        run = FactoryRun(
            id="factory-explicit",
            config=FactoryConfig(name="explicit-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        metadata = OrchestrationStageExecutor._pm_deterministic_contract_metadata_for_context(
            run,
            {"deterministic_pm_contracts": "yes"},
        )

        assert metadata == {
            "deterministic_pm_contracts": True,
            "factory_recovery": "explicit_deterministic_contracts",
        }


class TestTrimCommandOutput:
    def test_under_limit_unchanged(self) -> None:
        assert OrchestrationStageExecutor._trim_command_output("short", limit=100) == "short"

    def test_over_limit_keeps_tail(self) -> None:
        assert OrchestrationStageExecutor._trim_command_output("abcdef", limit=3) == "def"


class TestWorkspaceQualityRepairEvidence:
    @staticmethod
    def _assert_requires_canonical_attempt(
        *,
        results: list[dict[str, Any]],
        summary: dict[str, Any],
        source_tool: str,
        materialized_text: str,
        original_marker: str,
    ) -> None:
        """Factory may discover repairs, but it cannot execute outside roles.kernel."""

        assert results == []
        assert source_tool in summary["source_tools"]
        assert summary["write_tool_evidence"] is False
        assert original_marker in materialized_text
        non_effect_evidence = summary["non_effect_evidence_results"]
        assert summary["non_effect_evidence_result_count"] == len(non_effect_evidence)
        assert any(
            str((item.get("result") or {}).get("error_code") or "") == "deo_deferred_repair_attempt_required"
            for item in non_effect_evidence
        )

    def test_compacts_write_hash_and_diff_evidence(self) -> None:
        evidence = OrchestrationStageExecutor._workspace_quality_repair_evidence(
            [
                {
                    "tool": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/simulation.ts",
                        "operation": "modify",
                        "before_sha256": "a" * 64,
                        "after_sha256": "b" * 64,
                        "diff_excerpt": "--- a/src/simulation.ts\n+++ b/src/simulation.ts\n+export type GardenConfig = any;",
                    },
                }
            ]
        )

        assert any(
            item.startswith("repair_write:tool=deterministic_typescript_missing_export_repair") for item in evidence
        )
        assert "repair_hash:file=src/simulation.ts;before=aaaaaaaaaaaaaaaa;after=bbbbbbbbbbbbbbbb" in evidence
        assert any("export type GardenConfig" in item for item in evidence)

    def test_interface_discrepancy_evidence_recognizes_cross_language_symbol_mismatches(self) -> None:
        cases = (
            "go test ./... :: src/main.go:17: undefined: NewCapsule",
            "cargo check :: error[E0432]: unresolved import `crate::engine::Forecast`",
            "cargo check :: error[E0583]: file not found for module `engine`",
            "g++ :: fatal error: engine/forecast.hpp: No such file or directory",
            "ld :: undefined reference to `ForecastEngine::run()`",
        )

        for diagnostic in cases:
            evidence = OrchestrationStageExecutor._workspace_quality_interface_discrepancy_evidence(
                {
                    "plan_probe_preaudit": {
                        "status": "coverage_matched_but_unplannable",
                        "plannable_source_tools": [],
                        "covered_unplannable_source_tools": ["deterministic_cross_language_symbol_repair"],
                        "covered_unplannable_diagnostic_count": 1,
                    }
                },
                [diagnostic],
            )

            assert evidence["recommended_owner"] == "chief_engineer"
            assert evidence["recommended_route"] == "pending_design_interface_contract"
            assert evidence["cross_artifact_route"] == "contract_amendment_request"
            assert evidence["schema_version"] == "director.interface_discrepancy_receipt.v1"
            assert evidence["source"] == "factory.pipeline.workspace_quality"
            assert evidence["reason"] == "coverage_matched_but_unplannable"
            assert evidence["director_retry_allowed"] is False
            assert evidence["llm_fallback_blocked"] is True

    def test_applies_javascript_esm_commonjs_entrypoint_repair(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "dream-note-alchemy-furnace",
                    "type": "module",
                    "main": "src/index.js",
                    "scripts": {"start": "node src/index.js"},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "models" / "Note.js").write_text(
            "export class Note {}\nexport default Note;\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.js").write_text(
            '"use strict";\n'
            'const Note = require("./models/Note");\n'
            "function main() { return new Note(); }\n"
            "if (require.main === module) { main(); }\n"
            "module.exports = { main, Note };\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-esm-cjs",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:2\n"
                "ReferenceError: require is not defined in ES module scope. "
                'package.json contains "type": "module".'
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_esm_commonjs_entrypoint_repair",
            materialized_text=repaired,
            original_marker="module.exports = { main, Note };",
        )

    def test_applies_javascript_esm_commonjs_default_imported_module_repair(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "package.json").write_text(
            json.dumps({"type": "module", "main": "src/index.js", "scripts": {"start": "node src/index.js"}}),
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.js").write_text(
            'import AlchemyEngine from "./engine/AlchemyEngine.js";\n'
            'import { buildDefaultEngine } from "./engine/AlchemyEngine.js";\n'
            "export function main() {\n"
            "  return new AlchemyEngine(buildDefaultEngine());\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "models" / "Note.js").write_text(
            "export class Note {}\nexport default Note;\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            '"use strict";\n\n'
            'const Note = require("../models/Note");\n\n'
            "class AlchemyEngine {\n"
            "  constructor() {\n"
            "    this.notes = [new Note()];\n"
            "  }\n"
            "}\n\n"
            "function buildDefaultEngine() {\n"
            "  return { notes: [] };\n"
            "}\n\n"
            "module.exports = AlchemyEngine;\n"
            "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
            'module.exports.VERSION = "1.0.0";\n',
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-default-import-cjs",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:1\n"
                "SyntaxError: The requested module './engine/AlchemyEngine.js' "
                "does not provide an export named 'default'"
            ],
        )

        repaired = (tmp_path / "src" / "engine" / "AlchemyEngine.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_esm_commonjs_entrypoint_repair",
            materialized_text=repaired,
            original_marker="module.exports = AlchemyEngine;",
        )

    def test_applies_javascript_esm_commonjs_repair_for_namespace_require_binding(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "package.json").write_text(
            json.dumps({"type": "module", "main": "src/index.js", "scripts": {"start": "node src/index.js"}}),
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            "export class AlchemyEngine {}\nexport class Recipe {}\nexport class Note {}\nexport class DreamCard {}\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.js").write_text(
            'const AlchemyEngine = require("./engine/AlchemyEngine");\n'
            "const { Note, DreamCard, Recipe } = AlchemyEngine;\n"
            "function buildDemoEngine() {\n"
            "  const engine = new AlchemyEngine();\n"
            "  return { engine, Note, DreamCard, Recipe };\n"
            "}\n"
            "module.exports = { buildDemoEngine };\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-cjs-namespace-binding",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:1\n"
                "ReferenceError: require is not defined in ES module scope\n"
                'package.json contains "type": "module"'
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_esm_commonjs_entrypoint_repair",
            materialized_text=repaired,
            original_marker='const AlchemyEngine = require("./engine/AlchemyEngine");',
        )

    def test_applies_javascript_missing_method_runtime_repair(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "index.js").write_text(
            'import { AlchemyEngine } from "./engine/AlchemyEngine.js";\n'
            "function main() {\n"
            "  const engine = new AlchemyEngine();\n"
            "  const notes = [{ id: 'n1' }];\n"
            "  engine.addRecipe({ name: 'moon' });\n"
            "  const { dreamCards, rituals } = engine.transmute(notes);\n"
            "  return { dreamCards, rituals };\n"
            "}\n"
            "main();\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            "export class AlchemyEngine {\n"
            "  constructor({ recipes = [] } = {}) {\n"
            "    this.recipes = recipes;\n"
            "  }\n\n"
            "  refine(notes) {\n"
            "    return { dreamCards: notes, unconsumed: [] };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-missing-method",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:4\n"
                "  engine.addRecipe({ name: 'moon' });\n"
                "         ^\n\n"
                "TypeError: engine.addRecipe is not a function"
            ],
        )

        repaired = (tmp_path / "src" / "engine" / "AlchemyEngine.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_method_runtime_repair",
            materialized_text=repaired,
            original_marker="refine(notes)",
        )

    def test_applies_javascript_missing_method_runtime_repair_aliases_run_to_transmute_result_shape(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "index.js").write_text(
            'import { AlchemyEngine } from "./engine/AlchemyEngine.js";\n'
            "function main() {\n"
            "  const engine = new AlchemyEngine();\n"
            "  const notes = [{ id: 'n1' }];\n"
            "  const result = engine.run(notes);\n"
            "  return result.cards.length + result.untouched.length;\n"
            "}\n"
            "main();\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            "export class AlchemyEngine {\n"
            "  transmute(notes) {\n"
            "    return { dreamCards: notes, embers: [] };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-runtime-run-method",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:5\n"
                "  const result = engine.run(notes);\n"
                "                        ^\n\n"
                "TypeError: engine.run is not a function"
            ],
        )

        repaired = (tmp_path / "src" / "engine" / "AlchemyEngine.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_method_runtime_repair",
            materialized_text=repaired,
            original_marker="transmute(notes)",
        )

    def test_applies_javascript_missing_method_runtime_repair_for_imported_loop_variable_class(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "index.js").write_text(
            'import { Recipe } from "./models/Recipe.js";\n'
            'import { AlchemyEngine } from "./engine/AlchemyEngine.js";\n'
            "const recipes = [new Recipe({ name: 'moon', keywords: ['moon'], absurdityBoost: 4, ritual: 'hum' })];\n"
            "new AlchemyEngine({ recipes }).transmute([{ content: 'moon', matchesAllTags: () => true, intensity: 1 }]);\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            'import { Recipe } from "../models/Recipe.js";\n'
            "export class AlchemyEngine {\n"
            "  constructor({ recipes = [] } = {}) { this.recipes = recipes; }\n"
            "  pickRecipeFor(notes) {\n"
            "    for (const recipe of this.recipes) {\n"
            "      if (recipe.matchesAll(notes)) return recipe;\n"
            "    }\n"
            "    return null;\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "models" / "Recipe.js").write_text(
            "export class Recipe {\n"
            "  constructor({ name, requiredTags = [] } = {}) {\n"
            "    this.name = name;\n"
            "    this.requiredTags = requiredTags;\n"
            "  }\n"
            "  isSatisfiedBy(notes) { return Array.isArray(notes); }\n"
            "}\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-runtime-loop-var-method",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/engine/AlchemyEngine.js:6\n"
                "      if (recipe.matchesAll(notes)) return recipe;\n"
                "                 ^\n\n"
                "TypeError: recipe.matchesAll is not a function"
            ],
        )

        repaired = (tmp_path / "src" / "models" / "Recipe.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_method_runtime_repair",
            materialized_text=repaired,
            original_marker="isSatisfiedBy(notes)",
        )

    def test_applies_javascript_missing_method_runtime_repair_for_constructor_object_contracts(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "models" / "DreamCard.js").write_text(
            "export class DreamCard {\n"
            "  constructor({ id, title, narrative, sourceNoteIds = [] } = {}) {\n"
            '    if (!id) throw new Error("DreamCard requires an id");\n'
            '    if (!title) throw new Error("DreamCard requires a title");\n'
            '    if (!narrative) throw new Error("DreamCard requires a narrative");\n'
            "    this.id = id;\n"
            "    this.title = title;\n"
            "    this.narrative = narrative;\n"
            "    this.sourceNoteIds = sourceNoteIds;\n"
            "  }\n"
            "  toJSON() {\n"
            "    return {\n"
            "      id: this.id,\n"
            "      title: this.title,\n"
            "      narrative: this.narrative,\n"
            "    };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "smoke.test.js").write_text(
            'import { DreamCard } from "../src/models/DreamCard.js";\n'
            "new DreamCard({\n"
            '  title: "Library of Forgotten Names",\n'
            '  body: "Each book whispered a name I almost remembered.",\n'
            '  tags: ["memory", "library"],\n'
            "  createdAt: new Date(),\n"
            "});\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            'import * as DreamCard from "../models/DreamCard.js";\n'
            "DreamCard.composeTitle(0.42);\n"
            "new DreamCard.DreamCard({ title: 'x', fragments: ['a'], absurdity: 4, ritual: 'hum' });\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-constructor-contract",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm test): "
                "Error: DreamCard requires an id\n"
                f"    at new DreamCard (file://{tmp_path}/src/models/DreamCard.js:3:20)"
            ],
        )

        repaired = (tmp_path / "src" / "models" / "DreamCard.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_method_runtime_repair",
            materialized_text=repaired,
            original_marker="DreamCard requires an id",
        )

    def test_applies_javascript_missing_method_runtime_collection_and_refine_alias_repair(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "index.js").write_text(
            'import { AlchemyEngine } from "./engine/AlchemyEngine.js";\n'
            "function main() {\n"
            "  const engine = new AlchemyEngine({ recipes: [] });\n"
            "  const notes = [{ id: 'n1' }];\n"
            "  engine.listRecipes().length;\n"
            "  const { dreamCards, unmatched } = engine.transmute(notes);\n"
            "  return { dreamCards, unmatched };\n"
            "}\n"
            "main();\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            "export class AlchemyEngine {\n"
            "  constructor({ recipes = [] } = {}) {\n"
            "    this.recipes = recipes;\n"
            "  }\n\n"
            "  registerRecipe(recipe) {\n"
            "    this.recipes.push(recipe);\n"
            "    return recipe;\n"
            "  }\n\n"
            "  refine(notes) {\n"
            "    return { cards: notes, unmatched: [] };\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-missing-method-list",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:5\n"
                "  engine.listRecipes().length;\n"
                "         ^\n\n"
                "TypeError: engine.listRecipes is not a function"
            ],
        )

        repaired = (tmp_path / "src" / "engine" / "AlchemyEngine.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_method_runtime_repair",
            materialized_text=repaired,
            original_marker="registerRecipe(recipe)",
        )

    def test_applies_javascript_typescript_annotation_repair(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "index.js").write_text(
            "export function refineDreamNotes(..._args: unknown[]): any {\n  return undefined;\n}\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "test_basic.js").write_text(
            'import { refineDreamNotes } from "../src/index.js";\n'
            "const result = refineDreamNotes({ notes: ['有效便签'] });\n"
            "assert.equal(result.count, 1);\n"
            "assert.equal(result.distilled[0], '[提炼] 有效便签');\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-ts-annotation",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                f"file://{tmp_path}/src/index.js:1\n"
                "export function refineDreamNotes(..._args: unknown[]): any {\n"
                "                                         ^\n\n"
                "SyntaxError: Unexpected token ':'"
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_typescript_annotation_repair",
            materialized_text=repaired,
            original_marker=": unknown",
        )

    def test_applies_javascript_missing_export_repair(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "index.js").write_text("console.log('dream note app');\n", encoding="utf-8")
        (tmp_path / "tests" / "test_basic.js").write_text(
            'import { run } from "../src/index.js";\n'
            "const output = run();\n"
            "assert.equal(output.ok, true);\n"
            "assert.match(output.entrypoint, /src[\\\\/]+index\\.js$/);\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-missing-export",
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved import symbol 'run' "
                "from '../src/index.js' in tests/test_basic.js"
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_export_repair",
            materialized_text=repaired,
            original_marker="console.log('dream note app');",
        )

    def test_applies_javascript_missing_export_repair_for_iterable_method_contract(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "engine" / "AlchemyEngine.js").write_text(
            "export class AlchemyEngine {\n  defaultRecipes() {\n    return [{ name: 'starter' }];\n  }\n}\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "alchemyEngine.test.js").write_text(
            'import { AlchemyEngine, defaultRecipes } from "../src/engine/AlchemyEngine.js";\n'
            "const engine = new AlchemyEngine();\n"
            "for (const recipe of defaultRecipes) engine.addRecipe(recipe);\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-iterable-export",
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved import symbol 'defaultRecipes' "
                "from '../src/engine/AlchemyEngine.js' in tests/alchemyEngine.test.js",
            ],
        )

        repaired = (tmp_path / "src" / "engine" / "AlchemyEngine.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_export_repair",
            materialized_text=repaired,
            original_marker="defaultRecipes()",
        )

    def test_applies_javascript_export_contract_repair_for_wrong_existing_function(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "index.js").write_text(
            "export function refineDreamNotes(cards) {\n  if (!Array.isArray(cards)) return [];\n  return cards;\n}\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "smoke.test.js").write_text(
            'import assert from "node:assert/strict";\n'
            'import { refineDreamNotes } from "../src/index.js";\n'
            "const result = refineDreamNotes('a glowing key', 'silent bell', 'paper moon');\n"
            "assert.equal(result.count, 3);\n"
            "assert.equal(result.summary, 'a glowing key | silent bell | paper moon');\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-export-contract",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm test): "
                f"file://{tmp_path}/tests/smoke.test.js:5\n"
                "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:\n"
                "\n"
                "undefined !== 3"
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_export_repair",
            materialized_text=repaired,
            original_marker="if (!Array.isArray(cards)) return [];",
        )

    def test_applies_javascript_export_contract_repair_for_text_and_semver(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "package.json").write_text('{"version":"0.2.0"}', encoding="utf-8")
        (tmp_path / "src" / "index.js").write_text(
            "function refineDreamNotes(notes) {\n"
            "  return [];\n"
            "}\n\n"
            "export function getVersion(...args) {\n"
            "  return { ok: true };\n"
            "}\n\n"
            "export { refineDreamNotes };\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "smoke.test.js").write_text(
            'import assert from "node:assert/strict";\n'
            'import { refineDreamNotes, getVersion, VERSION } from "../src/index.js";\n'
            "const result = refineDreamNotes('  first dream  \\n\\n second dream ');\n"
            'assert.equal(result, "[dream] first dream\\n[dream] second dream");\n'
            "const v = getVersion();\n"
            "assert.equal(typeof v, 'string');\n"
            "assert.ok(/^\\d+\\.\\d+\\.\\d+/.test(v));\n"
            "assert.equal(typeof VERSION, 'string');\n"
            "assert.equal(VERSION, getVersion());\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-text-contract",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm test): "
                f"file://{tmp_path}/tests/smoke.test.js:4\n"
                "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal"
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_export_repair",
            materialized_text=repaired,
            original_marker="function refineDreamNotes(notes)",
        )

    def test_applies_javascript_export_contract_repair_for_app_metadata(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "dream-note-alchemy-furnace",
                    "version": "0.1.0",
                    "description": "Dream note alchemy CLI",
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.js").write_text(
            "export function getAppInfo() {\n  return { ok: true };\n}\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "version.test.js").write_text(
            'import assert from "node:assert/strict";\n'
            'import { APP_NAME, APP_VERSION, APP_DESCRIPTION, getAppInfo } from "../src/index.js";\n'
            "assert.equal(typeof APP_NAME, 'string');\n"
            "assert.ok(APP_NAME.length > 0);\n"
            "assert.match(APP_VERSION, /^\\d+\\.\\d+\\.\\d+/);\n"
            "assert.equal(typeof APP_DESCRIPTION, 'string');\n"
            "const info = getAppInfo();\n"
            "assert.equal(info.name, APP_NAME);\n"
            "assert.equal(info.version, APP_VERSION);\n"
            "assert.equal(info.description, APP_DESCRIPTION);\n",
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-app-metadata-contract",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm test): "
                f"file://{tmp_path}/tests/version.test.js:8\n"
                "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal",
                "Artifact quality scan failed: unresolved import symbol 'APP_DESCRIPTION' "
                "from '../src/index.js' in tests/version.test.js (sibling module does not define it)",
                "Artifact quality scan failed: unresolved import symbol 'APP_NAME' "
                "from '../src/index.js' in tests/version.test.js (sibling module does not define it)",
                "Artifact quality scan failed: unresolved import symbol 'APP_VERSION' "
                "from '../src/index.js' in tests/version.test.js (sibling module does not define it)",
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_export_repair",
            materialized_text=repaired,
            original_marker="return { ok: true };",
        )

    def test_applies_javascript_export_contract_repair_for_asserted_literal_and_note_shape(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "dream-note-alchemy-furnace",
                    "version": "0.1.0",
                    "description": "Dream note alchemy CLI",
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.js").write_text(
            "export function main() {\n  return true;\n}\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "test_index.js").write_text(
            'import assert from "node:assert/strict";\n'
            'import { ALCHEMY_FURNACE, refineDreamNote } from "../src/index.js";\n'
            'assert.equal(typeof ALCHEMY_FURNACE, "string");\n'
            'assert.equal(ALCHEMY_FURNACE, "dream-note-alchemy-furnace");\n'
            'const result = refineDreamNote("  flying over paper lanterns  ");\n'
            "assert.deepEqual(result, {\n"
            '  source: "  flying over paper lanterns  ",\n'
            '  refined: "flying over paper lanterns",\n'
            '  tag: "dream-fragment",\n'
            "});\n"
            'const empty = refineDreamNote("   ");\n'
            'assert.equal(empty.source, "   ");\n'
            'assert.equal(empty.refined, "");\n'
            'assert.equal(empty.tag, "empty");\n',
            encoding="utf-8",
        )

        results, summary = executor._apply_workspace_quality_repairs(
            run_id="factory-js-note-contract",
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved import symbol 'ALCHEMY_FURNACE' "
                "from '../src/index.js' in tests/test_index.js (sibling module does not define it)",
                "Artifact quality scan failed: unresolved import symbol 'refineDreamNote' "
                "from '../src/index.js' in tests/test_index.js (sibling module does not define it)",
            ],
        )

        repaired = (tmp_path / "src" / "index.js").read_text(encoding="utf-8")
        self._assert_requires_canonical_attempt(
            results=results,
            summary=summary,
            source_tool="deterministic_javascript_missing_export_repair",
            materialized_text=repaired,
            original_marker="export function main()",
        )


# ---------------------------------------------------------------------------
# Artifact path / read / write / audit
# ---------------------------------------------------------------------------


class TestArtifactStore:
    def test_artifact_path_rewrites_docs_to_workspace(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        resolved = executor._artifact_path("docs/plan.md")
        expected = Path(resolve_logical_path(str(tmp_path), "workspace/docs/plan.md")).resolve()
        assert resolved == expected

    def test_artifact_path_rewrites_tasks_to_runtime(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        resolved = executor._artifact_path("tasks/plan.json")
        expected = Path(resolve_logical_path(str(tmp_path), "runtime/tasks/plan.json")).resolve()
        assert resolved == expected

    def test_write_and_read_text_artifact_roundtrip(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        target = executor._write_text_artifact("docs/notes.md", "hello world")
        assert target.read_text(encoding="utf-8") == "hello world"
        assert executor._read_text_artifact("docs/notes.md") == "hello world"

    def test_read_text_artifact_min_chars_gate(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        executor._write_text_artifact("docs/tiny.md", "ab")
        assert executor._read_text_artifact("docs/tiny.md", min_chars=5) == ""

    def test_read_missing_artifact_returns_empty(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        assert executor._read_text_artifact("docs/absent.md") == ""

    def test_write_json_artifact_roundtrip(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        target = executor._write_json_artifact("tasks/data.json", {"k": "值"})
        assert json.loads(target.read_text(encoding="utf-8")) == {"k": "值"}

    def test_artifact_exists_min_chars(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        executor._write_text_artifact("docs/x.md", "abc")
        assert executor._artifact_exists("docs/x.md", min_chars=3) is True
        assert executor._artifact_exists("docs/x.md", min_chars=4) is False
        assert executor._artifact_exists("docs/x.md", min_chars=0) is True
        assert executor._artifact_exists("docs/absent.md") is False

    def test_missing_artifacts_filters(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        executor._write_text_artifact("docs/present.md", "content")
        assert executor._missing_artifacts(["docs/present.md", "docs/absent.md"]) == ["docs/absent.md"]

    def test_copy_text_artifact_if_present_copies(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        executor._write_text_artifact("docs/src.md", "payload")
        result = executor._copy_text_artifact_if_present("docs/src.md", "docs/dst.md")
        assert result == "docs/dst.md"
        assert executor._read_text_artifact("docs/dst.md") == "payload"

    def test_copy_text_artifact_if_present_skips_absent(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        assert executor._copy_text_artifact_if_present("docs/absent.md", "docs/dst.md") == ""

    def test_write_stage_signal_artifact(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        rel = executor._write_stage_signal_artifact(stage="pm_planning", run_id="run-1", signals=[{"code": "x"}])
        assert rel == "runtime/signals/pm_planning.signals.json"
        payload = json.loads(executor._artifact_path(rel).read_text(encoding="utf-8"))
        assert payload["stage"] == "pm_planning"
        assert payload["factory_run_id"] == "run-1"
        assert payload["signals"] == [{"code": "x"}]
        assert payload["source"] == "factory_stage_executor"

    def test_ensure_pm_plan_contract_available_copies_latest_plan_mirror(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        latest_plan = Path(resolve_logical_path(str(tmp_path), "workspace/plans/latest.plan.json"))
        latest_plan.parent.mkdir(parents=True, exist_ok=True)
        latest_plan.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "TASK-1",
                            "goal": "Implement Rust API",
                            "scope": "src/lib.rs",
                            "steps": ["Create crate"],
                            "acceptance": ["cargo test passes"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        source = executor._ensure_pm_plan_contract_available()

        assert source == ".polaris/plans/latest.plan.json"
        plan = json.loads(executor._artifact_path("tasks/plan.json").read_text(encoding="utf-8"))
        assert plan["tasks"][0]["id"] == "TASK-1"

    def test_enrich_pm_plan_contract_artifact_injects_depth_and_declared_targets(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        catalog_path = tmp_path / ".polaris" / "catalog_contract.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(
            json.dumps(
                {
                    "project_id": "L2-08",
                    "primary_language": "javascript",
                    "feature_keywords": ["meteor", "wish", "queue", "priority"],
                    "level": 2,
                    "level_contract": {
                        "level": 2,
                        "minimums": {
                            "min_prod_files": 6,
                            "min_prod_lines": 500,
                            "min_test_assertions": 8,
                        },
                        "required_evidence": ["implementation_depth passes"],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1-foundation",
                        "goal": "Create package manifest",
                        "target_files": ["package.json"],
                        "context_files": ["docs/design.md"],
                    },
                    {
                        "id": "TASK-2-entrypoint",
                        "goal": "Create declared entrypoint",
                        "target_files": ["src/index.js", "src/engine/rules.js"],
                    },
                ]
            },
        )

        summary = executor._enrich_pm_plan_contract_artifact("tasks/plan.json")

        assert summary["changed"] is True
        assert summary["task_count"] == 2
        assert summary["declared_target_count"] == 3
        plan = json.loads(executor._artifact_path("tasks/plan.json").read_text(encoding="utf-8"))
        for task in plan["tasks"]:
            depth_contract = task["delivery_depth_contract"]
            assert depth_contract["minimums"]["min_prod_files"] == 6
            assert depth_contract["minimums"]["min_prod_lines"] == 500
            declared_targets = task["metadata"]["project_declared_target_files"]
            assert declared_targets == ["package.json", "src/index.js", "src/engine/rules.js"]
            assert "docs/design.md" not in declared_targets
            assert task["metadata"]["manifest_entrypoint_contract"]["allowed_local_entrypoints"] == declared_targets

    def test_ensure_chief_engineer_blueprint_artifact_present_rewrites_missing_result(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        result = TaskBlueprintResultV1(
            ok=True,
            task_id="TASK-1",
            workspace=str(tmp_path),
            status="generated",
            blueprint_id="ce_TASK-1_test",
            blueprint_path="runtime/blueprints/ce_TASK-1_test.json",
            summary="Blueprint summary",
            recommendations=("Keep scope tight",),
            risks=("Missing tests",),
            target_files=("src/lib.rs",),
            acceptance_criteria=("cargo test passes",),
            execution_checklist=("implement module",),
            scope_paths=("src/lib.rs",),
            objective="Implement Rust module",
            dependencies=("TASK-0",),
        )

        rewrote = executor._ensure_chief_engineer_blueprint_artifact_present(
            result=result,
            task={"id": "TASK-1", "title": "Rust module", "goal": "Implement Rust module"},
            task_context={"task_index": 1},
            constraints={"acceptance": ["cargo test passes"]},
            run_id="factory-run",
        )

        assert rewrote is True
        payload = json.loads(
            executor._artifact_path("runtime/blueprints/ce_TASK-1_test.json").read_text(encoding="utf-8")
        )
        assert payload["handoff_ready"] is True
        assert payload["contract_completeness"]["reconstructed_from_result"] is True
        assert payload["target_files"] == ["src/lib.rs"]
        assert payload["acceptance_criteria"] == ["cargo test passes"]
        assert (
            executor._ensure_chief_engineer_blueprint_artifact_present(
                result=result,
                task={},
                task_context={},
                constraints={},
                run_id="factory-run",
            )
            is False
        )

    def test_chief_engineer_llm_evidence_extracts_final_request_audit(self) -> None:
        ce_result = SimpleNamespace(
            metadata={
                "provider": "openai",
                "model": "gpt-5",
                "final_request_context_audit": {
                    "schema_version": "llm.final_request_context_audit.v1",
                    "final_request_token_estimate": 42000,
                },
                "context_snapshot_ref": "runtime/contexts/ab/abcdef123456abcdef123456.json",
            },
            usage={"cache_hit": False},
        )

        evidence = OrchestrationStageExecutor._ce_extract_llm_evidence(
            ce_result,
            task_id="TASK-1",
            run_id="factory-run",
        )

        assert evidence["provider"] == "openai"
        assert evidence["model"] == "gpt-5"
        assert evidence["context_snapshot_ref"] == "abcdef123456abcdef123456"
        assert evidence["final_request_context_audit"]["final_request_token_estimate"] == 42000
        assert OrchestrationStageExecutor._ce_missing_final_request_evidence(evidence) == []

    def test_chief_engineer_llm_evidence_marks_missing_final_request_audit(self) -> None:
        ce_result = SimpleNamespace(metadata={"provider": "openai", "model": "gpt-5"}, usage={})

        evidence = OrchestrationStageExecutor._ce_extract_llm_evidence(
            ce_result,
            task_id="TASK-1",
            run_id="factory-run",
        )

        assert OrchestrationStageExecutor._ce_missing_final_request_evidence(evidence) == [
            "final_request_context_audit",
            "context_snapshot_ref",
        ]

    def test_chief_engineer_portfolio_rejects_present_invalid_advisory_scope(self) -> None:
        payload = dict(_single_task_chief_engineer_result().metadata["structured_output"])
        payload["scope_for_apply"] = "src/cancel.py"

        errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
            payload,
            task_ids=("TASK-CANCEL",),
        )

        assert "scope_for_apply must be an array" in errors

    @pytest.mark.parametrize(
        ("ce_result", "expected"),
        [
            (_invalid_chief_engineer_stream_result(), True),
            (_thinking_only_chief_engineer_result(), True),
            (
                SimpleNamespace(
                    error_category="unknown",
                    error_code="call_error",
                    error_message=(
                        "structured_output_payload_schema_mismatch:$:'scope_for_apply' is a required property"
                    ),
                    status="failed",
                ),
                True,
            ),
            (
                SimpleNamespace(
                    error_category="provider_backend_failure",
                    error_code="circuit_open",
                    error_message="CircuitOpenError: circuit breaker is open",
                    status="failed",
                ),
                False,
            ),
            (
                SimpleNamespace(
                    error_category="semantic_rejection",
                    error_code="chief_engineer_design_rejected",
                    error_message="The proposed architecture violates the PM contract",
                    status="failed",
                ),
                False,
            ),
        ],
    )
    def test_chief_engineer_portfolio_schema_repair_admission_is_narrow(
        self,
        ce_result: SimpleNamespace,
        expected: bool,
    ) -> None:
        assert OrchestrationStageExecutor._ce_portfolio_result_allows_schema_repair(ce_result) is expected

    def test_chief_engineer_structured_result_schema_mismatch_is_output_validation_failure(self) -> None:
        ce_result = SimpleNamespace(
            error_code="call_error",
            error_message=("structured_output_payload_schema_mismatch:$:'scope_for_apply' is a required property"),
        )

        assert OrchestrationStageExecutor._ce_schema_repair_failure_class(ce_result) == "output_validation_failed"

    def test_emit_audit_event_appends(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        executor._emit_audit_event("ce.call", task_id="t1")
        executor._emit_audit_event("ce.call", task_id="t2")
        audit_path = tmp_path / ".polaris" / "audit" / "ce.call.json"
        entries = json.loads(audit_path.read_text(encoding="utf-8"))
        assert len(entries) == 2
        assert entries[0]["event_type"] == "ce.call"
        assert entries[0]["task_id"] == "t1"
        assert entries[1]["task_id"] == "t2"

    def test_chief_engineer_llm_call_audit_mirrors_to_canonical_llm_events(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        executor._emit_audit_event(
            "chief_engineer.llm_call",
            task_id="TASK-1",
            run_id="factory-run",
            provider="openai",
            model="gpt-5",
            context_snapshot_ref="runtime/contexts/aa/aaaabbbbccccddddeeeeffff.json",
            final_request_context_audit={
                "schema_version": "llm.final_request_context_audit.v1",
                "final_request_token_estimate": 42000,
            },
        )
        executor._emit_audit_event(
            "chief_engineer.llm_call",
            task_id="TASK-2",
            run_id="factory-run",
            provider="openai",
            model="gpt-5",
            context_snapshot_ref="runtime/contexts/aa/111122223333444455556666.json",
            final_request_context_audit={
                "schema_version": "llm.final_request_context_audit.v1",
                "final_request_token_estimate": 43000,
            },
        )

        audit_path = tmp_path / ".polaris" / "audit" / "chief_engineer.llm_call.json"
        assert audit_path.exists()
        events_path = Path(resolve_logical_path(str(tmp_path), "runtime/events/chief_engineer.llm.events.jsonl"))
        rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        assert len(rows) == 2
        row = rows[0]
        assert row["role"] == "chief_engineer"
        assert row["event"] == "llm_call_end"
        assert row["context_snapshot_ref"] == "aaaabbbbccccddddeeeeffff"
        assert row["final_request_context_audit"]["final_request_token_estimate"] == 42000
        assert rows[1]["context_snapshot_ref"] == "111122223333444455556666"


class TestMirrorHelpers:
    def test_mirror_docs_artifacts(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        executor._write_text_artifact("docs/plan.md", "plan body")
        executor._write_text_artifact("docs/architecture.md", "arch body")
        artifacts: list[str] = []
        executor._mirror_docs_artifacts("run-9", artifacts)
        assert "workspace/roles/architect/run-9/plan.md" in artifacts
        assert "workspace/roles/architect/run-9/architecture.md" in artifacts

    def test_mirror_pm_plan_artifacts(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        executor._write_json_artifact("tasks/plan.json", {"tasks": [{"id": "t"}]})
        artifacts: list[str] = []
        executor._mirror_pm_plan_artifacts("run-9", artifacts)
        assert "workspace/roles/pm/run-9/plan.json" in artifacts
        assert "workspace/plans/run-9.plan.json" in artifacts
        assert "workspace/plans/latest.plan.json" in artifacts

    def test_load_pm_plan_tasks_falls_back_to_mirrored_plan(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        plan_path = tmp_path / ".polaris" / "plans" / "latest.plan.json"
        plan_path.parent.mkdir(parents=True)
        plan_path.write_text(
            json.dumps({"tasks": [{"id": "TASK-1", "target_files": ["main.go"]}]}),
            encoding="utf-8",
        )

        assert executor._load_pm_plan_tasks("tasks/plan.json") == [{"id": "TASK-1", "target_files": ["main.go"]}]

    def test_mirror_director_artifacts(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        executor._write_json_artifact("dispatch/log.json", {"status": "ok"})
        artifacts: list[str] = []
        executor._mirror_director_artifacts("run-9", artifacts)
        assert "workspace/roles/director/run-9/dispatch.log.json" in artifacts
        assert "workspace/dispatch/latest.log.json" in artifacts

    def test_mirror_quality_gate_artifacts(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        executor._write_json_artifact("runtime/qa/report.json", {"passed": True})
        executor._write_json_artifact("runtime/qa/workspace-validation.json", {"passed": True})
        artifacts: list[str] = []
        executor._mirror_quality_gate_artifacts("run-9", artifacts)
        assert "workspace/roles/qa/run-9/report.json" in artifacts
        assert "workspace/qa/latest.report.json" in artifacts
        assert "workspace/roles/qa/run-9/workspace-validation.json" in artifacts
        assert "workspace/qa/latest.workspace-validation.json" in artifacts

    def test_mirror_chief_engineer_artifacts(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        review_rel = "runtime/state/blueprints/run-9.review.json"
        executor._write_json_artifact(review_rel, {"k": "v"})
        bp_rel = "runtime/blueprints/bp1.json"
        executor._write_json_artifact(bp_rel, {"id": "bp1"})
        artifacts: list[str] = []
        executor._mirror_chief_engineer_artifacts(
            "run-9",
            [{"blueprint_path": bp_rel, "blueprint_id": "bp1"}],
            review_rel,
            artifacts,
        )
        assert "workspace/roles/chief_engineer/run-9/review.json" in artifacts
        assert "workspace/blueprints/latest.review.json" in artifacts
        assert "workspace/roles/chief_engineer/run-9/blueprints/bp1.json" in artifacts
        assert "workspace/blueprints/bp1.json" in artifacts


class TestQualityGateDeadlineHandling:
    def test_default_deadline_policy_blocks_ce_when_clipped_budget_below_generation_floor(self) -> None:
        # A 508s horizon over a 5-task serial chain leaves only ~105-108s for the CE
        # stage after reserving the full Director critical path (400s) + QA + safety.
        # That is below the modeled physical floor (~205s) to stream the 16384-token
        # portfolio, so admission must fail closed instead of EXECUTE a doomed call.
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 508.0
        tasks = [
            {
                "id": f"TASK-{index}",
                "depends_on": [] if index == 1 else [f"TASK-{index - 1}"],
            }
            for index in range(1, 6)
        ]

        decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {"factory_run_deadline_epoch_seconds": deadline_epoch},
            requested_timeout_seconds=240,
            dependency_schedule=build_task_dependency_schedule(tasks),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert decision.reason == "insufficient_factory_deadline_for_chief_engineer_portfolio"
        assert decision.reserved_downstream_seconds == 400.0
        assert decision.timeout_seconds == 0

    def test_chief_engineer_deadline_projection_not_used_without_factory_deadline(self) -> None:
        decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {},
            requested_timeout_seconds=123,
            dependency_schedule=build_task_dependency_schedule([{"id": "TASK-1"}]),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
        assert decision.remaining_seconds is None
        assert decision.timeout_seconds == 123

    def test_chief_engineer_deadline_projection_blocks_when_available_budget_below_generation_floor(self) -> None:
        # 180s horizon with a reduced downstream reserve (125s) leaves ~50-55s for CE.
        # That exceeds min_start (40s) but is far below the ~205s physical floor to
        # stream the full portfolio, so admission must fail closed.
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 180.0
        decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 60,
                "quality_gate_reserved_budget_seconds": 30,
            },
            requested_timeout_seconds=240,
            dependency_schedule=build_task_dependency_schedule([{"id": "TASK-1"}]),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert decision.reason == "insufficient_factory_deadline_for_chief_engineer_portfolio"
        assert decision.reserved_downstream_seconds == 125.0
        assert decision.timeout_seconds == 0

    def test_chief_engineer_schema_repair_uses_smaller_output_token_floor(self) -> None:
        # The bounded output-schema repair requests only 8192 tokens (floor ~102s),
        # far below the full-portfolio floor (~205s). A budget that is below the
        # portfolio floor but above the repair floor must still admit the repair.
        # 400s horizon, reduced downstream reserve (125s) -> ~272-275s available.
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 400.0
        schedule = build_task_dependency_schedule([{"id": "TASK-1"}])
        portfolio_decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 60,
                "quality_gate_reserved_budget_seconds": 30,
            },
            requested_timeout_seconds=240,
            dependency_schedule=schedule,
        )
        repair_decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 60,
                "quality_gate_reserved_budget_seconds": 30,
            },
            requested_timeout_seconds=240,
            dependency_schedule=schedule,
            output_tokens=8_192,
        )

        # ~272-275s available: above the portfolio floor -> both EXECUTE; repair floor is smaller.
        assert portfolio_decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
        assert repair_decision.disposition is FactoryDeadlineDispositionV1.EXECUTE

    def test_chief_engineer_schema_repair_floor_admits_where_portfolio_floor_blocks(self) -> None:
        # 230s horizon, reduced downstream reserve (125s) -> ~102-105s available.
        # Below the portfolio floor (~205s) but at/above the repair floor (~102.4s):
        # portfolio must BLOCK, repair must be admitted at the boundary.
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 230.0
        schedule = build_task_dependency_schedule([{"id": "TASK-1"}])
        portfolio_decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 60,
                "quality_gate_reserved_budget_seconds": 30,
            },
            requested_timeout_seconds=240,
            dependency_schedule=schedule,
        )
        repair_decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 60,
                "quality_gate_reserved_budget_seconds": 30,
            },
            requested_timeout_seconds=240,
            dependency_schedule=schedule,
            output_tokens=8_192,
        )

        assert portfolio_decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert portfolio_decision.reason == "insufficient_factory_deadline_for_chief_engineer_portfolio"
        assert repair_decision.disposition is FactoryDeadlineDispositionV1.EXECUTE

    def test_chief_engineer_deadline_projection_skips_llm_when_downstream_budget_is_at_risk(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 120.0
        decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 60,
                "quality_gate_reserved_budget_seconds": 30,
            },
            requested_timeout_seconds=240,
            dependency_schedule=build_task_dependency_schedule([{"id": "TASK-1"}]),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert decision.reason == "insufficient_factory_deadline_for_chief_engineer_portfolio"
        assert decision.timeout_seconds == 0

    def test_chief_engineer_deadline_projection_accounts_for_remaining_ce_fanout(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 360.0
        tasks = [
            {
                "id": f"TASK-{index}",
                "depends_on": [] if index == 1 else [f"TASK-{index - 1}"],
            }
            for index in range(1, 9)
        ]
        decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 150,
                "quality_gate_reserved_budget_seconds": 120,
            },
            requested_timeout_seconds=240,
            dependency_schedule=build_task_dependency_schedule(tasks),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert decision.critical_path_task_count == 8
        assert -237 <= float(decision.available_for_stage_seconds or 0.0) <= -235

    def test_director_dispatch_timeout_caps_to_factory_deadline(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 12.0
        timeout = OrchestrationStageExecutor._director_dispatch_timeout_seconds(
            {
                "director_dispatch_timeout_seconds": 1800,
                "llm_call_timeout_seconds": 1800,
                "factory_run_deadline_epoch_seconds": deadline_epoch,
            },
            task_count=2,
        )

        assert 1 <= timeout <= 12

    def test_director_dispatch_timeout_reserves_quality_gate_budget(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 300.0
        timeout = OrchestrationStageExecutor._director_dispatch_timeout_seconds(
            {
                "director_dispatch_timeout_seconds": 1800,
                "llm_call_timeout_seconds": 1800,
                "factory_run_deadline_epoch_seconds": deadline_epoch,
            },
            task_count=2,
        )

        assert 150 <= timeout <= 180

    def test_director_dispatch_timeout_preserves_quality_budget_during_materialization(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 190.0
        timeout = OrchestrationStageExecutor._director_dispatch_timeout_seconds(
            {
                "director_dispatch_timeout_seconds": 1800,
                "llm_call_timeout_seconds": 1800,
                "factory_run_deadline_epoch_seconds": deadline_epoch,
            },
            task_count=2,
            materialization_pending=True,
        )

        assert 130 <= timeout <= 136

    def test_director_dispatch_timeout_keeps_quality_reserve_after_materialization(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 190.0
        timeout = OrchestrationStageExecutor._director_dispatch_timeout_seconds(
            {
                "director_dispatch_timeout_seconds": 1800,
                "llm_call_timeout_seconds": 1800,
                "factory_run_deadline_epoch_seconds": deadline_epoch,
            },
            task_count=2,
            materialization_pending=False,
        )

        assert 65 <= timeout <= 70

    def test_director_dispatch_timeout_uses_quality_gate_reserve_override(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 300.0
        timeout = OrchestrationStageExecutor._director_dispatch_timeout_seconds(
            {
                "director_dispatch_timeout_seconds": 1800,
                "llm_call_timeout_seconds": 1800,
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "quality_gate_reserved_budget_seconds": 60,
            },
            task_count=2,
        )

        assert 210 <= timeout <= 240

    def test_director_dispatch_deadline_admission_blocks_short_materialization_budget(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 120.0
        decision = OrchestrationStageExecutor._director_dispatch_deadline_admission_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 90,
                "quality_gate_reserved_budget_seconds": 60,
            },
            requested_timeout_seconds=1800,
            first_materialization_pending=True,
            materialization_pending=True,
            dependency_schedule=build_task_dependency_schedule([{"id": "TASK-1"}]),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert decision.reason == "insufficient_factory_deadline_for_director_dispatch"
        assert decision.timeout_seconds == 0
        assert decision.minimum_start_budget_seconds == 90.0
        assert 50 <= float(decision.available_for_stage_seconds or 0.0) <= 55

    def test_director_dispatch_deadline_admission_allows_sufficient_budget(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 300.0
        decision = OrchestrationStageExecutor._director_dispatch_deadline_admission_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 90,
                "quality_gate_reserved_budget_seconds": 60,
            },
            requested_timeout_seconds=1800,
            first_materialization_pending=True,
            materialization_pending=True,
            dependency_schedule=build_task_dependency_schedule([{"id": "TASK-1"}]),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
        assert decision.reason == ""
        assert decision.minimum_start_budget_seconds == 90.0
        assert 230 <= decision.timeout_seconds <= 235

    def test_director_invalid_dependency_schedule_is_not_reported_as_deadline_exhaustion(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 300.0
        decision = OrchestrationStageExecutor._director_dispatch_deadline_admission_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 90,
                "quality_gate_reserved_budget_seconds": 60,
            },
            requested_timeout_seconds=180,
            first_materialization_pending=True,
            materialization_pending=True,
            dependency_schedule=build_task_dependency_schedule(
                [{"id": "TASK-1"}],
                active_task_ids=("TASK-1", "CE-PORTFOLIO-factory-run"),
            ),
        )

        code, detail, status, message = OrchestrationStageExecutor._director_admission_failure_projection(decision)

        assert decision.reason == "invalid_pm_task_dependency_schedule"
        assert code == "director.dispatch_dependency_schedule_blocker"
        assert "unknown_active_task_ids:CE-PORTFOLIO-factory-run" in detail
        assert status == "failed"
        assert "dependency schedule is invalid" in message

    def test_director_dispatch_deadline_admission_uses_standard_budget_after_first_round(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 213.0
        decision = OrchestrationStageExecutor._director_dispatch_deadline_admission_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 150,
                "quality_gate_reserved_budget_seconds": 120,
            },
            requested_timeout_seconds=1800,
            first_materialization_pending=False,
            materialization_pending=False,
            dependency_schedule=build_task_dependency_schedule([{"id": "TASK-1"}]),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
        assert decision.minimum_start_budget_seconds == stage_executor_module.FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
        assert 87 <= decision.timeout_seconds <= 88

    def test_r43_later_materialization_wave_uses_same_minimum_qa_reserve_as_timeout_projection(
        self,
    ) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 239.0
        decision = OrchestrationStageExecutor._director_dispatch_deadline_admission_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 90,
                "quality_gate_reserved_budget_seconds": 120,
            },
            requested_timeout_seconds=600,
            first_materialization_pending=False,
            materialization_pending=True,
            dependency_schedule=build_task_dependency_schedule(
                [
                    {"id": "TASK-2", "depends_on": []},
                    {"id": "TASK-3", "depends_on": ["TASK-2"]},
                ]
            ),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
        assert decision.minimum_start_budget_seconds == stage_executor_module.FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
        assert decision.reserved_downstream_seconds == 105
        assert 128 <= decision.execution_timeout_seconds <= 129
        assert decision.settlement_timeout_seconds == 5
        assert decision.reservation_breakdown["qa_finalization"] == 55
        assert decision.reservation_breakdown["qa_finalization_minimum_reserve_active"] == 1

    @pytest.mark.asyncio
    async def test_director_dispatch_deadline_admission_stops_before_llm_turn(self, tmp_path: Path) -> None:
        class _DeadlineAdmissionExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.execute_calls = 0

            def _read_taskboard_stats(self) -> dict[str, int]:
                return {
                    "total": 1,
                    "pending": 1,
                    "ready": 1,
                    "in_progress": 0,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                return ["TASK-1"]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                executor = self

                class _Service:
                    async def execute_director_run(self, **kwargs: object) -> CommandResult:
                        del kwargs
                        executor.execute_calls += 1
                        return CommandResult(run_id="director-started", status="running", message="submitted")

                return _Service()

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _DeadlineAdmissionExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="run-director-deadline",
            config=FactoryConfig(name="director-deadline"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-22T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 120.0

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {
                    "director_max_rounds": 1,
                    "execution_mode": "parallel",
                    "max_workers": 1,
                    "factory_run_deadline_epoch_seconds": deadline_epoch,
                    "director_first_materialization_min_budget_seconds": 90,
                    "quality_gate_reserved_budget_seconds": 60,
                }
            ),
        )

        assert result.status == "failed"
        assert executor.execute_calls == 0
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["error_code"] == "director.dispatch_deadline_blocker"
        signal = next(item for item in payload["signals"] if item.get("code") == "director.dispatch_deadline_blocker")
        assert signal["responsible_layer"] == "execution_control_plane"
        assert signal["disposition"] == FactoryDeadlineDispositionV1.BLOCK.value
        assert signal["timeout_seconds"] == 0

    @pytest.mark.asyncio
    async def test_quality_gate_deadline_insufficient_writes_fail_report(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-deadline",
            config=FactoryConfig(name="deadline-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )
        workspace_checks_called = False

        async def fake_workspace_checks(_run: FactoryRun, _context: dict[str, Any]) -> tuple[bool, str]:
            nonlocal workspace_checks_called
            workspace_checks_called = True
            return True, ""

        monkeypatch.setattr(executor, "_run_workspace_quality_checks", fake_workspace_checks)

        def fail_if_qa_started(_context: dict[str, Any]) -> object:
            raise AssertionError("QA orchestration should not start when the factory deadline is exhausted")

        monkeypatch.setattr(executor, "_build_orchestration_service", fail_if_qa_started)

        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 1.0
        result = await executor._execute_quality_gate(
            run,
            _factory_stage_context(
                {
                    "qa_target": "Quality gate",
                    "factory_run_deadline_epoch_seconds": deadline_epoch,
                    "factory_run_timeout_seconds": 540.0,
                    "factory_run_deadline_source": "test",
                }
            ),
        )

        assert result.status == "failed"
        assert workspace_checks_called is False
        assert "factory_quality_gate_deadline_insufficient_before_checks" in result.output
        report_path = Path(resolve_logical_path(tmp_path, "runtime/qa/report.json"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["passed"] is False
        assert report["verdict"] == "FAIL"
        assert "factory_quality_gate_deadline_insufficient_before_checks" in report["warnings"]
        assert Path(resolve_logical_path(tmp_path, "workspace/qa/latest.report.json")).is_file()

    @pytest.mark.asyncio
    async def test_quality_gate_uses_dynamic_qa_timeout_for_short_but_viable_deadline(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-short-viable-deadline",
            config=FactoryConfig(name="short-viable-deadline-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )
        workspace_checks_called = False
        qa_started = False

        async def fake_workspace_checks(_run: FactoryRun, _context: dict[str, Any]) -> tuple[bool, str]:
            nonlocal workspace_checks_called
            workspace_checks_called = True
            return True, ""

        class _FakeQaService:
            async def execute_qa_run(self, **_kwargs: Any) -> object:
                nonlocal qa_started
                qa_started = True
                return SimpleNamespace(status="running", message="started")

        async def fake_wait_run_completion(*_args: Any, **kwargs: Any) -> object:
            assert 1 <= int(kwargs["timeout_seconds"]) <= 44
            report_path = Path(resolve_logical_path(tmp_path, "runtime/qa/report.json"))
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "passed": True,
                        "score": 95,
                        "critical_issue_count": 0,
                        "warnings": [],
                    }
                ),
                encoding="utf-8",
            )
            return CommandResult(
                run_id="qa-run",
                status="completed",
                message="qa complete",
                metadata={
                    "canonical_authoritative": True,
                    "terminal_source": "task_runtime.execution_fact",
                    "fact_event_seq": 23,
                },
            )

        canonical_projection = _with_task_runtime_authority(
            {
                "source": "run_ledger",
                "integrity_ok": True,
                "outcome_ok": True,
                "task_boundary": {
                    "latest_by_task": {
                        "TASK-1": {
                            "task_id": "TASK-1",
                            "status": "completed_verified",
                            "ok": True,
                            "failure_class": "PASSED",
                            "responsible_layer": "execution_control_plane",
                        }
                    }
                },
                "gates": [
                    {
                        "name": "qa_verdict",
                        "ok": True,
                        "append_id": "qa-append-3",
                        "content_id": "qa-content-3",
                    }
                ],
                "evidence_policy": {
                    "integrity_ok": True,
                    "outcome_ok": True,
                    "missing_required_modalities": [],
                    "failed_required_modalities": [],
                },
            }
        )

        monkeypatch.setattr(executor, "_run_workspace_quality_checks", fake_workspace_checks)
        monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: _FakeQaService())
        monkeypatch.setattr(executor, "_wait_run_completion", fake_wait_run_completion)
        monkeypatch.setattr(
            executor,
            "_canonical_factory_projection",
            lambda _run, _context: canonical_projection,
        )

        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 44.4
        result = await executor._execute_quality_gate(
            run,
            _factory_stage_context(
                {
                    "qa_target": "Quality gate",
                    "factory_run_deadline_epoch_seconds": deadline_epoch,
                    "factory_run_timeout_seconds": 540.0,
                    "factory_run_deadline_source": "test",
                }
            ),
        )

        assert result.status == "success"
        assert workspace_checks_called is True
        assert qa_started is True
        assert "deadline_insufficient" not in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_report_missing_does_not_replace_canonical_verdict(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-report-missing",
            config=FactoryConfig(name="missing-report-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        async def fake_workspace_checks(_run: FactoryRun, _context: dict[str, Any]) -> tuple[bool, str]:
            executor._write_json_artifact("runtime/qa/workspace-validation.json", {"passed": True})
            return True, "runtime/qa/workspace-validation.json"

        class FakeQAService:
            async def execute_qa_run(self, **_kwargs: Any) -> CommandResult:
                return CommandResult(run_id="qa-run", status="running", message="started")

        async def fake_wait_run_completion(*_args: Any, **_kwargs: Any) -> CommandResult:
            return CommandResult(run_id="qa-run", status="completed", message="done")

        monkeypatch.setattr(executor, "_run_workspace_quality_checks", fake_workspace_checks)
        monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: FakeQAService())
        monkeypatch.setattr(executor, "_wait_run_completion", fake_wait_run_completion)

        result = await executor._execute_quality_gate(
            run,
            _factory_stage_context({"qa_target": "Quality gate"}),
        )

        assert result.status == "failed"
        assert "canonical_reason=task_runtime_tasks_missing" in result.output
        assert "report_ready=False" in result.output
        report_path = Path(resolve_logical_path(tmp_path, "runtime/qa/report.json"))
        assert report_path.exists() is False
        assert Path(resolve_logical_path(tmp_path, "workspace/qa/latest.report.json")).exists() is False

    @pytest.mark.asyncio
    async def test_quality_gate_workspace_validation_failure_still_runs_qa_judgement(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-workspace-fail",
            config=FactoryConfig(name="workspace-fail-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        async def fake_workspace_checks(_run: FactoryRun, _context: dict[str, Any]) -> tuple[bool, str]:
            executor._write_json_artifact(
                "runtime/qa/workspace-validation.json",
                {
                    "passed": False,
                    "commands": [
                        {
                            "command": ["npm", "run", "start"],
                            "passed": False,
                            "stderr_tail": "ReferenceError: exports is not defined in ES module scope",
                        }
                    ],
                    "repair": {
                        "residual_errors": [
                            "Artifact quality scan failed: workspace validation command failed (npm run start)"
                        ]
                    },
                },
            )
            return False, "runtime/qa/workspace-validation.json"

        qa_calls: list[dict[str, Any]] = []

        class _CapturingQaService:
            async def execute_qa_run(self, **kwargs: Any) -> CommandResult:
                qa_calls.append(dict(kwargs))
                executor._write_json_artifact(
                    "runtime/qa/report.json",
                    {
                        "passed": False,
                        "verdict": "FAIL",
                        "score": 0,
                        "critical_issue_count": 1,
                        "critical_issues": ["workspace_quality_gate_failed"],
                        "warnings": [],
                    },
                )
                return CommandResult(
                    run_id="qa-workspace-failure",
                    status="running",
                    message="QA run started",
                )

            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="completed", message="QA completed")

        monkeypatch.setattr(executor, "_run_workspace_quality_checks", fake_workspace_checks)
        monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: _CapturingQaService())

        result = await executor._execute_quality_gate(
            run,
            _factory_stage_context({"qa_target": "Quality gate"}),
        )

        assert result.status == "failed"
        assert "workspace_checks_diagnostic=False" in result.output
        assert "canonical_authorized=False" in result.output
        assert qa_calls
        qa_input = str(qa_calls[0]["options"]["input"])
        assert "Workspace quality evidence collected before QA judgement" in qa_input
        assert "runtime/qa/workspace-validation.json" in qa_input
        assert "ReferenceError: exports is not defined in ES module scope" in qa_input
        report_path = Path(resolve_logical_path(tmp_path, "runtime/qa/report.json"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["passed"] is False
        assert report["verdict"] == "FAIL"
        assert Path(resolve_logical_path(tmp_path, "workspace/qa/latest.report.json")).is_file()

    @pytest.mark.asyncio
    async def test_workspace_quality_deadline_insufficient_writes_validation_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-workspace-repair-deadline",
            config=FactoryConfig(name="workspace-repair-deadline-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
        monkeypatch.setattr(
            executor,
            "_run_workspace_quality_command",
            lambda _command, _timeout: {
                "command": ["npm", "run", "build"],
                "exit_code": 2,
                "passed": False,
                "stdout_tail": "src/main.ts(1,1): error TS2353: Object literal may only specify known properties.",
                "stderr_tail": "",
            },
        )
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_repairs",
            lambda **_kwargs: ([], {"attempted": True, "source_tools": [], "tool_results": 0}),
        )
        monkeypatch.setattr(
            executor,
            "_canonical_factory_projection",
            lambda _run, _context: _with_task_runtime_authority(
                {
                    "source": "run_ledger",
                    "task_boundary": {
                        "latest_by_task": {
                            "TASK-1": {
                                "task_id": "TASK-1",
                                "status": "completed_verified",
                                "ok": True,
                            }
                        }
                    },
                }
            ),
        )

        async def fail_if_llm_repair_started(**_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            raise AssertionError("workspace quality LLM repair should not start when deadline is insufficient")

        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fail_if_llm_repair_started)

        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 20.0
        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "factory_run_timeout_seconds": 540.0,
                "factory_run_deadline_source": "test",
            },
        )

        assert passed is False
        assert artifact == "runtime/qa/workspace-validation.json"
        payload = json.loads(Path(resolve_logical_path(tmp_path, artifact)).read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert "factory_quality_gate_workspace_checks_deadline_insufficient" in payload["warnings"]
        assert "remaining" in payload["error"]

    @pytest.mark.asyncio
    async def test_workspace_quality_command_timeout_preserves_qa_budget(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-workspace-command-deadline",
            config=FactoryConfig(name="workspace-command-deadline-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )
        observed_timeouts: list[float] = []

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
        monkeypatch.setattr(
            executor,
            "_canonical_factory_projection",
            lambda _run, _context: _with_task_runtime_authority(
                {
                    "source": "run_ledger",
                    "task_boundary": {
                        "latest_by_task": {
                            "TASK-1": {
                                "task_id": "TASK-1",
                                "status": "completed_verified",
                                "ok": True,
                            }
                        }
                    },
                }
            ),
        )

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            observed_timeouts.append(timeout_seconds)
            return {
                "command": command,
                "exit_code": 0,
                "passed": True,
                "stdout_tail": "build passed",
                "stderr_tail": "",
                "error": "",
            }

        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)

        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 70.0
        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "factory_run_timeout_seconds": 540.0,
                "factory_run_deadline_source": "test",
            },
        )

        assert passed is True
        assert observed_timeouts
        assert 1.0 <= observed_timeouts[0] <= 26.0
        payload = json.loads(Path(resolve_logical_path(tmp_path, artifact)).read_text(encoding="utf-8"))
        command = payload["commands"][0]
        assert command["deadline_capped_timeout_seconds"] <= 26.0
        assert command["configured_timeout_seconds"] == 240.0

    @pytest.mark.asyncio
    async def test_workspace_quality_skips_full_project_checks_when_source_tasks_not_unlocked(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "build": "tsc -p tsconfig.json",
                        "test": "vitest run",
                        "start": "npm run build && node dist/main.js",
                    },
                    "devDependencies": {"typescript": "^5.4.5", "vitest": "^1.6.0"},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "tsconfig.json").write_text(
            json.dumps({"include": ["src/**/*.ts", "tests/**/*.ts"]}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-source-task-blocked",
            config=FactoryConfig(name="source-task-blocked-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        def fail_if_command_runs(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del command, timeout_seconds
            raise AssertionError("workspace quality commands must not run before source tasks unlock")

        def fail_if_depth_runs(_context: dict[str, Any]) -> dict[str, Any] | None:
            raise AssertionError("delivery depth must not run before source tasks unlock")

        def fail_if_runtime_repair_runs(**_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            raise AssertionError("runtime repair must not run before source tasks unlock")

        async def fail_if_llm_repair_runs(**_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            raise AssertionError("LLM repair must not run before source tasks unlock")

        monkeypatch.setattr(
            executor,
            "_canonical_factory_projection",
            lambda _run, _context: _with_task_runtime_authority(
                {
                    "source": "run_ledger",
                    "task_boundary": {
                        "latest_by_task": {
                            "TASK-1": {
                                "task_id": "TASK-1",
                                "status": "completed_verified",
                                "ok": True,
                            },
                            "TASK-2": {
                                "task_id": "TASK-2",
                                "status": "dependency_not_unlocked",
                                "ok": False,
                                "failure_class": "DEPENDENCY_NOT_UNLOCKED",
                                "responsible_layer": "task_boundary",
                            },
                        }
                    },
                },
                task_ids=("TASK-1", "TASK-2"),
                incomplete_task_ids=("TASK-2",),
            ),
        )
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", fail_if_depth_runs)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fail_if_command_runs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", fail_if_runtime_repair_runs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fail_if_llm_repair_runs)

        passed, artifact = await executor._run_workspace_quality_checks(run, {})

        assert passed is False
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["commands"] == []
        assert payload["commands_skipped"] is True
        assert payload["failure_class"] == "DEPENDENCY_NOT_UNLOCKED"
        assert payload["responsible_layer"] == "task_boundary"
        assert payload["repair"]["attempted"] is False
        assert payload["repair"]["reason"] == "task_boundary_not_ready"
        assert payload["task_boundary_blocker"]["incomplete_task_ids"] == ["TASK-2"]
        assert "task_boundary_not_completed_verified" in payload["warnings"]

    @pytest.mark.asyncio
    async def test_workspace_quality_skips_checks_when_declared_test_targets_not_unlocked(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text("export const ready = true;\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "build": "tsc -p tsconfig.json",
                        "test": "vitest run tests/simulation.test.ts tests/verify.test.ts",
                        "start": "vite --host 127.0.0.1",
                    },
                    "devDependencies": {"typescript": "^5.4.5", "vitest": "^1.6.0", "vite": "^5.4.0"},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "tsconfig.json").write_text(
            json.dumps({"include": ["src/**/*.ts", "tests/**/*.ts"]}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-tests-blocked",
            config=FactoryConfig(name="tests-blocked-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        def fail_if_command_runs(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del command, timeout_seconds
            raise AssertionError("workspace quality commands must not run before declared test targets unlock")

        def fail_if_depth_runs(_context: dict[str, Any]) -> dict[str, Any] | None:
            raise AssertionError("delivery depth must not run before declared test targets unlock")

        def fail_if_runtime_repair_runs(**_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            raise AssertionError("runtime repair must not run before declared test targets unlock")

        async def fail_if_llm_repair_runs(**_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            raise AssertionError("LLM repair must not run before declared test targets unlock")

        monkeypatch.setattr(
            executor,
            "_canonical_factory_projection",
            lambda _run, _context: _with_task_runtime_authority(
                {
                    "source": "run_ledger",
                    "task_boundary": {
                        "latest_by_task": {
                            "TASK-1": {
                                "task_id": "TASK-1",
                                "status": "completed_verified",
                                "ok": True,
                            },
                            "TASK-2": {
                                "task_id": "TASK-2",
                                "status": "dependency_not_unlocked",
                                "ok": False,
                                "failure_class": "DEPENDENCY_NOT_UNLOCKED",
                                "responsible_layer": "task_boundary",
                            },
                            "TASK-3": {
                                "task_id": "TASK-3",
                                "status": "dependency_not_unlocked",
                                "ok": False,
                                "failure_class": "DEPENDENCY_NOT_UNLOCKED",
                                "responsible_layer": "task_boundary",
                            },
                        }
                    },
                },
                task_ids=("TASK-1", "TASK-2", "TASK-3"),
                incomplete_task_ids=("TASK-2", "TASK-3"),
            ),
        )
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", fail_if_depth_runs)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fail_if_command_runs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", fail_if_runtime_repair_runs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fail_if_llm_repair_runs)

        passed, artifact = await executor._run_workspace_quality_checks(run, {})

        assert passed is False
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["commands"] == []
        assert payload["commands_skipped"] is True
        assert payload["failure_class"] == "DEPENDENCY_NOT_UNLOCKED"
        assert payload["responsible_layer"] == "task_boundary"
        assert payload["task_boundary_blocker"]["incomplete_task_ids"] == ["TASK-2", "TASK-3"]
        assert "task_boundary_not_completed_verified" in payload["warnings"]


# ---------------------------------------------------------------------------
# package.json parsing
# ---------------------------------------------------------------------------


class TestPackageJsonParsing:
    def test_load_package_scripts(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "vitest", "build": "vite build", "empty": ""}}),
            encoding="utf-8",
        )
        scripts = executor._load_package_scripts()
        assert scripts == {"test": "vitest", "build": "vite build"}

    def test_load_package_scripts_missing_file(self, tmp_path: Path) -> None:
        assert _executor(tmp_path)._load_package_scripts() == {}

    def test_load_package_scripts_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{not json", encoding="utf-8")
        assert _executor(tmp_path)._load_package_scripts() == {}

    def test_external_dependencies_true(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"marked": "^1"}}), encoding="utf-8")
        assert executor._workspace_package_has_external_dependencies() is True

    def test_external_dependencies_false_when_empty(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "package.json").write_text(json.dumps({"dependencies": {}}), encoding="utf-8")
        assert executor._workspace_package_has_external_dependencies() is False

    def test_external_dependencies_missing_file(self, tmp_path: Path) -> None:
        assert _executor(tmp_path)._workspace_package_has_external_dependencies() is False

    def test_workspace_quality_commands_from_scripts(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "vitest", "build": "vite build"}}), encoding="utf-8"
        )
        assert executor._workspace_quality_commands({}) == [["npm", "run", "build"], ["npm", "test"]]

    def test_workspace_quality_commands_include_entrypoint_smoke(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "node --test", "start": "node src/index.js"}}),
            encoding="utf-8",
        )

        assert executor._workspace_quality_commands({}) == [["npm", "test"], ["npm", "run", "start"]]

    def test_workspace_quality_commands_python_project_include_real_gates(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_smoke.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")

        commands = executor._workspace_quality_commands({})

        assert commands == [
            [sys.executable, "-m", "compileall", "-q", "src", "tests", "main.py"],
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
            [sys.executable, "main.py"],
        ]

    def test_workspace_quality_commands_python_src_entrypoint_include_script_smoke(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_smoke.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

        commands = executor._workspace_quality_commands({})

        # NOTE: the ``python -m src.main`` module-style smoke was intentionally
        # removed — it raised ModuleNotFoundError for generated project layouts
        # whose entrypoint uses ``from src.x import ...`` style imports. Only the
        # direct ``python src/main.py`` script smoke remains.
        assert commands == [
            [sys.executable, "-m", "compileall", "-q", "src", "tests"],
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
            [sys.executable, "src/main.py"],
        ]

    def test_workspace_quality_commands_python_project_install_when_requirements_exists(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "requirements.txt").write_text("requests\n", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")

        commands = executor._workspace_quality_commands({})

        assert commands[0] == [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]

    def test_workspace_quality_commands_cpp_project_uses_cpp_check_not_python_harness(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\n", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_product.py").write_text("def test_contract():\n    assert True\n", encoding="utf-8")

        commands = executor._workspace_quality_commands({})

        assert len(commands) == 1
        assert commands[0][:2] == [sys.executable, "-c"]
        assert "g++" in commands[0][2]
        assert "unittest" not in commands[0][2]

    def test_workspace_quality_commands_rust_project_include_cargo_test(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "kitchen-flavor-palette"\n', encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")

        assert executor._workspace_quality_commands({}) == [["cargo", "test", "--quiet"]]

    def test_workspace_quality_commands_go_project_include_go_verify_and_entrypoint(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "go.mod").write_text("module timecapsule\n\ngo 1.22\n", encoding="utf-8")
        (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n", encoding="utf-8")
        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "capsule.go").write_text("package models\n\ntype Capsule struct{}\n", encoding="utf-8")

        commands = executor._workspace_quality_commands({})

        assert commands == [["go", "test", "./..."], ["go", "run", "."]]

    def test_workspace_quality_commands_mixed_go_python_keep_go_verify_first(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "go.mod").write_text("module timecapsule\n\ngo 1.22\n", encoding="utf-8")
        (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_product.py").write_text("def test_contract():\n    assert True\n", encoding="utf-8")

        commands = executor._workspace_quality_commands({})

        assert commands == [["go", "test", "./..."], ["go", "run", "."]]

    def test_workspace_quality_commands_mixed_rust_python_keep_native_cargo_test(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "kitchen-flavor-palette"\n', encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_product.py").write_text("def test_contract():\n    assert True\n", encoding="utf-8")

        commands = executor._workspace_quality_commands({})

        assert commands == [["cargo", "test", "--quiet"]]

    def test_workspace_quality_rust_test_cannot_mutate_target_workspace(self, tmp_path: Path) -> None:
        if not shutil.which("cargo"):
            pytest.skip("cargo unavailable")
        executor = _executor(tmp_path)
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "factory-rust-sandbox"\nversion = "0.1.0"\nedition = "2021"\n',
            encoding="utf-8",
        )
        (tmp_path / "src").mkdir()
        source = tmp_path / "src" / "lib.rs"
        source.write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "product.rs").write_text(
            "#[test]\nfn product_works() {\n"
            "    assert_eq!(factory_rust_sandbox::answer(), 42);\n"
            '    std::fs::write("src/lib.rs", "pub fn answer() -> u8 { 7 }\\n").unwrap();\n'
            f'    assert!(std::fs::write({json.dumps(source.as_posix())}, b"host mutation").is_err());\n'
            "}\n",
            encoding="utf-8",
        )

        result = executor._run_workspace_quality_command(["cargo", "test", "--quiet"], 30)

        assert result["passed"] is True
        assert result["sandboxed"] is True
        assert result["native_test_count"] >= 1
        assert source.read_text(encoding="utf-8") == "pub fn answer() -> u8 { 42 }\n"

    def test_workspace_quality_rust_test_rejects_zero_tests(self, tmp_path: Path) -> None:
        if not shutil.which("cargo"):
            pytest.skip("cargo unavailable")
        executor = _executor(tmp_path)
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "factory-rust-zero"\nversion = "0.1.0"\nedition = "2021"\n',
            encoding="utf-8",
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")

        result = executor._run_workspace_quality_command(["cargo", "test", "--quiet"], 30)

        assert result["exit_code"] == 0
        assert result["passed"] is False
        assert result["native_test_count"] == 0
        assert result["error"] == "cargo_test_zero_tests"

    def test_workspace_quality_rust_test_fails_closed_without_sandbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        if not shutil.which("cargo"):
            pytest.skip("cargo unavailable")
        executor = _executor(tmp_path)
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "factory-rust-no-sandbox"\nversion = "0.1.0"\nedition = "2021"\n',
            encoding="utf-8",
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")

        def unavailable_sandbox(**_kwargs: Any) -> Any:
            raise workspace_quality_module.NativeValidationSandboxError("bubblewrap unavailable")

        monkeypatch.setattr(
            workspace_quality_module,
            "sandboxed_cargo_test_command",
            unavailable_sandbox,
        )

        result = executor._run_workspace_quality_command(["cargo", "test", "--quiet"], 30)

        assert result["passed"] is False
        assert result["sandboxed"] is False
        assert str(result["error"]).startswith("native_validation_sandbox_unavailable:")

    def test_declared_delivery_targets_extract_explicit_file_tokens_from_task_text(self) -> None:
        targets = OrchestrationStageExecutor._collect_declared_delivery_targets(
            [
                {
                    "target_files": ["src/__init__.py"],
                    "steps": ["创建 requirements.txt 并运行 python -m pip install -r requirements.txt"],
                    "acceptance": ["README.md 说明如何执行 main.py"],
                }
            ]
        )

        assert "requirements.txt" in targets
        assert "README.md" in targets
        assert "main.py" in targets

    def test_declared_delivery_targets_collapse_file_as_directory_tokens(self) -> None:
        targets = OrchestrationStageExecutor._collect_declared_delivery_targets(
            [
                {
                    "target_files": ["src/models/pet.go/index.ts"],
                    "acceptance": ["verify src/engine/engine.go/index.ts exists"],
                }
            ]
        )

        assert "src/models/pet.go" in targets
        assert "src/engine/engine.go" in targets
        assert "src/models/pet.go/index.ts" not in targets
        assert "src/engine/engine.go/index.ts" not in targets

    def test_workspace_quality_commands_configured_override(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        commands = executor._workspace_quality_commands({"quality_commands": ["pytest -q", ["ruff", "check"]]})
        assert commands == [["pytest", "-q"], ["ruff", "check"]]

    def test_workspace_quality_commands_disabled(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        assert executor._workspace_quality_commands({"workspace_validation": False}) == []


# ---------------------------------------------------------------------------
# Real-subprocess quality command execution
# ---------------------------------------------------------------------------


class TestRunWorkspaceQualityCommand:
    def test_executable_not_found(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command(["definitely-not-a-real-binary-xyz"], 5.0)
        assert result["passed"] is False
        assert result["exit_code"] is None
        assert "executable not found" in result["error"]

    def test_real_subprocess_success(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command([sys.executable, "-c", "print('ok')"], 30.0)
        assert result["exit_code"] == 0
        assert result["passed"] is True
        assert "ok" in result["stdout_tail"]

    def test_real_subprocess_zero_exit_with_typescript_errors_fails(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command(
            [
                sys.executable,
                "-c",
                'print("src/main.ts(1,1): error TS2305: missing export"); print("TypeScript check skipped")',
            ],
            30.0,
        )
        assert result["exit_code"] == 0
        assert result["passed"] is False
        assert "TypeScript compiler errors" in result["error"]

    def test_real_subprocess_zero_exit_with_skipped_javac_failure_fails(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    'print("setUpClass (test_product.JavaCompileAndRunTests) ... '
                    "skipped 'javac (main) failed; cannot continue runtime tests.\\n"
                    "stderr:\\n"
                    "src/main/java/polaris/factory/Main.java:119: error: incompatible types'\", file=sys.stderr)"
                ),
            ],
            30.0,
        )
        assert result["exit_code"] == 0
        assert result["passed"] is False
        assert "skipped tests caused by compile/build failure" in result["error"]

    def test_real_subprocess_enriches_nested_javac_called_process_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        bin_dir = tmp_path / "bin"
        source_path = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "Main.java"
        output_dir = tmp_path / "build" / "classes"
        bin_dir.mkdir()
        source_path.parent.mkdir(parents=True)
        output_dir.mkdir(parents=True)
        source_path.write_text("package polaris.factory;\nclass Main {}\n", encoding="utf-8")
        fake_javac = bin_dir / "javac"
        fake_javac.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "print(f'{sys.argv[-1]}:7: error: cannot find symbol', file=sys.stderr)\n"
            "print('  symbol:   class RhythmReport', file=sys.stderr)\n"
            "print('1 error', file=sys.stderr)\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )
        fake_javac.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")

        result = executor._run_workspace_quality_command(
            [
                sys.executable,
                "-c",
                (
                    "import subprocess; "
                    "subprocess.run("
                    f"['javac', '-encoding', 'UTF-8', '-d', {str(output_dir)!r}, {str(source_path)!r}], "
                    "check=True, capture_output=True)"
                ),
            ],
            30.0,
        )

        assert result["exit_code"] == 1
        assert result["passed"] is False
        assert "Nested javac diagnostics from unittest subprocess" in result["stderr_tail"]
        assert "cannot find symbol" in result["stderr_tail"]
        assert "RhythmReport" in result["stderr_tail"]
        assert result["nested_diagnostics"] in result["stderr_tail"]

    def test_real_subprocess_nonzero_exit(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command([sys.executable, "-c", "import sys; sys.exit(3)"], 30.0)
        assert result["exit_code"] == 3
        assert result["passed"] is False

    def test_real_subprocess_nonzero_typescript_error_is_not_marked_masked(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command(
            [
                sys.executable,
                "-c",
                "import sys; print(\"src/engine/renderer.ts(1,3780): error TS1005: '}' expected.\"); sys.exit(2)",
            ],
            30.0,
        )
        assert result["exit_code"] == 2
        assert result["passed"] is False
        assert "error" not in result

    def test_real_subprocess_timeout(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command([sys.executable, "-c", "import time; time.sleep(5)"], 0.5)
        assert result["passed"] is False
        assert result["exit_code"] is None
        assert "timeout after" in result["error"]


class TestRunWorkspaceQualityChecks:
    @pytest.fixture(autouse=True)
    def canonical_task_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Start workspace-quality tests after the canonical task boundary."""

        monkeypatch.setattr(
            OrchestrationStageExecutor,
            "_canonical_factory_projection",
            lambda _executor, _run, _context: _with_task_runtime_authority(
                {
                    "source": "run_ledger",
                    "task_boundary": {
                        "latest_by_task": {
                            "TASK-1": {
                                "task_id": "TASK-1",
                                "status": "completed_verified",
                                "ok": True,
                            }
                        }
                    },
                }
            ),
        )

    @pytest.mark.asyncio
    async def test_typescript_repairs_require_canonical_director_execution_before_rerun(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "render.ts").write_text(
            "import { SimulationState, updateSimulation } from './simulation';\n"
            "type Snapshot = SimulationState;\n"
            "const current: Snapshot = updateSimulation({ speed: 1 });\n"
            "export { current };\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "simulation.ts").write_text(
            "export class GardenSimulation {\n"
            "  public start(): void {\n"
            "    window.setInterval(() => undefined, 1000);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp_path / "tsconfig.json").write_text(
            json.dumps(
                {
                    "compilerOptions": {
                        "target": "ES2020",
                        "module": "ES2020",
                        "lib": ["ES2020"],
                    },
                    "include": ["src/**/*.ts"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        run = FactoryRun(
            id="factory-quality-repair",
            config=FactoryConfig(name="quality-repair"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            repaired_source = (tmp_path / "src" / "simulation.ts").read_text(encoding="utf-8")
            repaired_tsconfig = json.loads((tmp_path / "tsconfig.json").read_text(encoding="utf-8"))
            repaired = (
                "export type SimulationState = any;" in repaired_source
                and "export function updateSimulation(..._args: unknown[]): any" in repaired_source
                and "DOM" in repaired_tsconfig["compilerOptions"]["lib"]
            )
            if repaired:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "build passed",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": (
                    "src/render.ts(1,10): error TS2305: Module '\"./simulation\"' has no exported member "
                    "'SimulationState'.\n"
                    "src/render.ts(1,27): error TS2305: Module '\"./simulation\"' has no exported member "
                    "'updateSimulation'.\n"
                    "src/simulation.ts(3,5): error TS2304: Cannot find name 'window'. "
                    "Do you need to change your target library? Try changing the 'lib' compiler option to include "
                    "'dom'."
                ),
                "stderr_tail": "",
                "error": "",
            }

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)

        passed, artifact = await executor._run_workspace_quality_checks(run, {})

        assert passed is False
        assert calls == [["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert [item["phase"] for item in payload["commands"]] == ["check"]
        assert payload["repair"]["write_tool_evidence"] is False
        assert payload["repair"]["tool_results"] == 0
        assert "export type SimulationState = any;" not in (tmp_path / "src" / "simulation.ts").read_text(
            encoding="utf-8"
        )

    @pytest.mark.asyncio
    async def test_repair_summary_success_requires_rerun_to_pass(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-repair-still-failing",
            config=FactoryConfig(name="quality-repair-still-failing"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": "src/index.ts(1,10): error TS2305: missing export",
                "stderr_tail": "",
                "error": "",
            }

        def fake_apply_workspace_quality_repairs(
            *,
            run_id: str,
            artifact_quality_errors: list[str],
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run_id == "factory-quality-repair-still-failing"
            assert artifact_quality_errors
            return (
                [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {
                            "source_tool": "deterministic_typescript_missing_export_repair",
                            "file": "src/index.ts",
                            "operation": "modify",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["deterministic_typescript_missing_export_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", fake_apply_workspace_quality_repairs)

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is False
        assert calls == [["npm", "run", "build"], ["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert payload["repair"]["attempted"] is True
        assert payload["repair"]["success"] is False
        assert payload["repair"]["revalidated"] is True
        assert payload["repair"]["residual_error_count"] == 1
        assert "TS2305" in payload["repair"]["residual_errors"][0]

    @pytest.mark.asyncio
    async def test_workspace_quality_delivery_depth_contract_enters_repair_loop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / ".polaris").mkdir(parents=True)
        (tmp_path / ".polaris" / "catalog_contract.json").write_text(
            json.dumps(
                {
                    "project_id": "depth-contract",
                    "level": 2,
                    "level_contract": {
                        "schema_version": "factory-bench.level_contract.v1",
                        "level": 2,
                        "minimums": {
                            "min_prod_files": 1,
                            "min_prod_lines": 3,
                            "min_behavior_symbols": 1,
                            "min_branch_count": 0,
                            "min_test_files": 0,
                            "min_test_assertions": 0,
                        },
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "src").mkdir()
        source_path = tmp_path / "src" / "index.ts"
        source_path.write_text("export function run() { return 1; }\n", encoding="utf-8")
        run = FactoryRun(
            id="factory-quality-depth-contract",
            config=FactoryConfig(name="quality-depth-contract"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            return {
                "command": command,
                "exit_code": 0,
                "passed": True,
                "stdout_tail": "test passed",
                "stderr_tail": "",
                "error": "",
            }

        def fake_apply_workspace_quality_repairs(
            *,
            run_id: str,
            artifact_quality_errors: list[str],
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run_id == "factory-quality-depth-contract"
            assert any("delivery_depth_contract_failed" in item for item in artifact_quality_errors)
            return (
                [],
                {
                    "attempted": True,
                    "success": False,
                    "source_tools": [],
                    "tool_results": 0,
                    "write_tool_evidence": False,
                },
            )

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run_id: str,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            del context
            assert run_id == "factory-quality-depth-contract"
            assert repair_attempt == 1
            assert any("production_source_lines=1 < 3" in item for item in artifact_quality_errors)
            source_path.write_text(
                "\n".join(
                    [
                        "export function run() {",
                        "  const value = 1;",
                        "  return value;",
                        "}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            return (
                [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {
                            "source_tool": "director_llm_workspace_quality_repair",
                            "file": "src/index.ts",
                            "operation": "modify",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["director_llm_workspace_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "test"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", fake_apply_workspace_quality_repairs)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_llm_repairs",
            fake_apply_workspace_quality_llm_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        assert calls == [["npm", "test"], ["npm", "test"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is True
        command_phases = [(item["command"], item["phase"], item["passed"]) for item in payload["commands"]]
        assert (["delivery_depth_contract"], "check", False) in command_phases
        assert (["delivery_depth_contract"], "check_after_repair", True) in command_phases
        assert payload["repair"]["attempted"] is True
        assert payload["repair"]["success"] is True

    @pytest.mark.asyncio
    async def test_workspace_quality_escalates_to_director_llm_repair_after_deterministic_noop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-llm-repair",
            config=FactoryConfig(name="quality-llm-repair"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        state = {"repaired": False}
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            if state["repaired"]:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "build passed",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": (
                    "FAIL tests/index.test.ts > updateFirefly > should bounce\n"
                    "AssertionError: expected 3 to be less than 0\n"
                    " ❯ tests/index.test.ts:80:26"
                ),
                "stderr_tail": "",
                "error": "",
            }

        def fake_apply_workspace_quality_repairs(
            *,
            run_id: str,
            artifact_quality_errors: list[str],
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run_id == "factory-quality-llm-repair"
            assert artifact_quality_errors
            return (
                [],
                {
                    "attempted": False,
                    "success": False,
                    "source_tools": [],
                    "tool_results": 0,
                },
            )

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run_id: str,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run_id == "factory-quality-llm-repair"
            assert context["workspace_quality_repair_max_rounds"] == 1
            assert artifact_quality_errors
            assert repair_attempt == 1
            state["repaired"] = True
            return (
                [
                    {
                        "tool": "write_file",
                        "tool_name": "write_file",
                        "success": True,
                        "result": {
                            "source_tool": "director_materialization_quality_repair",
                            "file": "src/index.ts",
                            "operation": "modify",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "repair_mode": "director_llm",
                    "source_tools": ["director_materialization_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "test"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", fake_apply_workspace_quality_repairs)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_llm_repairs",
            fake_apply_workspace_quality_llm_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        assert calls == [["npm", "test"], ["npm", "test"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is True
        assert payload["repair"]["success"] is True
        assert payload["repair"]["source_tools"] == ["director_materialization_quality_repair"]
        assert payload["repair"]["rounds"][0]["source_tools"] == ["director_materialization_quality_repair"]

    @pytest.mark.asyncio
    async def test_workspace_quality_llm_repair_context_includes_ce_blueprint_and_catalog(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / ".polaris").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".polaris" / "catalog_contract.json").write_text(
            json.dumps(
                {
                    "project_id": "L2-08",
                    "primary_language": "javascript",
                    "project_type": "collaboration_toy",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "engine" / "rules.js").write_text("export const meteor = 1;\n", encoding="utf-8")
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "goal": "Create source and entrypoint",
                        "target_files": ["package.json", "src/engine/rules.js", "src/index.js"],
                    }
                ]
            },
        )
        executor._write_json_artifact(
            "runtime/state/blueprints/factory-context.review.json",
            {
                "generated_blueprints": 1,
                "total_tasks": 1,
                "blueprints": [
                    {
                        "task_id": "TASK-1",
                        "status": "generated",
                        "blueprint_id": "ce_TASK-1",
                        "summary": "Chief Engineer blueprint defines source and entrypoint contracts.",
                    }
                ],
            },
        )
        from polaris.cells.runtime.task_runtime.public import TaskRuntimeService

        TaskRuntimeService(str(tmp_path)).ensure_task_row(
            external_task_id="TASK-1",
            subject="Create source and entrypoint",
            description="Own the JavaScript source repaired by workspace verification",
            metadata={
                "external_task_id": "TASK-1",
                "factory_run_id": "factory-context",
                "goal": "Create source and entrypoint",
                "scope": "Own the JavaScript source repaired by workspace verification",
                "target_files": ["package.json", "src/engine/rules.js", "src/index.js"],
                "acceptance_criteria": ["npm test passes"],
                "blueprint_id": "ce_TASK-1",
                "runtime_blueprint_path": ".polaris/blueprints/ce_TASK-1.json",
                "role": "director",
            },
        )
        captured: dict[str, Any] = {}

        async def fake_run_director_materialization_quality_repair(
            workspace: str,
            *,
            task: dict[str, Any],
            target_task_id: str,
            run_id: str,
            context: dict[str, Any],
            original_message: str,
            llm_call_timeout: float,
            artifact_quality_errors: list[str],
            changed_files: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
            from polaris.cells.roles.runtime.public.service import RoleRuntimeService

            command = ExecuteRoleSessionCommandV1(
                role="director",
                session_id=str(context["session_id"]),
                workspace=workspace,
                user_message="repair current verifier failure",
                run_id=run_id,
                task_id=target_task_id,
                context=context,
                metadata=dict(context.get("metadata") or {}),
            )
            attempt_validation = RoleRuntimeService()._validate_directed_effect_session_attempt(command)
            captured.update(
                {
                    "workspace": workspace,
                    "task": task,
                    "target_task_id": target_task_id,
                    "run_id": run_id,
                    "context": context,
                    "original_message": original_message,
                    "llm_call_timeout": llm_call_timeout,
                    "artifact_quality_errors": artifact_quality_errors,
                    "changed_files": changed_files,
                    "repair_attempt": repair_attempt,
                    "attempt_validation": attempt_validation,
                }
            )
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "file": "src/engine/rules.js",
                            "operation": "modify",
                            "source_tool": "director_materialization_quality_repair",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["director_materialization_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.run_director_materialization_quality_repair",
            fake_run_director_materialization_quality_repair,
        )

        _, summary = await executor._apply_workspace_quality_llm_repairs(
            run_id="factory-context",
            context={},
            artifact_quality_errors=["npm run build failed"],
            repair_attempt=1,
        )

        assert summary["repair_mode"] == "director_llm"
        repair_context = captured["context"]
        assert repair_context["language"] == "javascript"
        assert repair_context["programming_language"] == "javascript"
        assert repair_context["project_type"] == "collaboration_toy"
        assert repair_context["ce_blueprint"]["artifact"] == "runtime/state/blueprints/factory-context.review.json"
        assert "Chief Engineer blueprint" in repair_context["chief_engineer_blueprint_evidence"]
        # The QA retry must preserve the original TaskRuntime owner contract.
        # roles.adapters promotes PM/CE final-request evidence from these fields;
        # replacing them with a target-files-only shell makes the physical
        # provider request fail closed before Director can repair anything.
        assert captured["task"]["goal"] == "Create source and entrypoint"
        assert captured["task"]["scope"] == "Own the JavaScript source repaired by workspace verification"
        assert captured["task"]["acceptance_criteria"] == ["npm test passes"]
        assert captured["task"]["metadata"]["blueprint_id"] == "ce_TASK-1"
        assert captured["task"]["metadata"]["runtime_blueprint_path"] == ".polaris/blueprints/ce_TASK-1.json"
        from polaris.kernelone.events.final_request_evidence import looks_like_pm_contract_payload

        assert looks_like_pm_contract_payload(captured["task"]) is True
        assert captured["target_task_id"] == "TASK-1"
        execution_attempt = repair_context["task_runtime_execution_attempt"]
        authority = repair_context["task_runtime_execution_attempt_authority"]
        assert execution_attempt.external_task_id == captured["target_task_id"]
        assert execution_attempt.run_id == "factory-context"
        assert execution_attempt.role_id == "director"
        assert repair_context["session_id"] == execution_attempt.session_id
        assert captured["attempt_validation"].status == "valid"
        assert captured["attempt_validation"].execution_attempt == execution_attempt
        authority_snapshot = authority.snapshot(lock_timeout_seconds=5.0)
        assert authority_snapshot.success is True
        assert authority_snapshot.identity == execution_attempt
        assert summary["task_runtime_repair_attempt"] == {
            "task_id": captured["target_task_id"],
            "session_id": execution_attempt.session_id,
            "settled": True,
            "outcome": "completed",
        }
        task_rows = TaskRuntimeService(str(tmp_path)).list_task_rows(include_terminal=True)
        owner_row = next(row for row in task_rows if row["metadata"].get("external_task_id") == "TASK-1")
        assert owner_row["status"] == "completed"

    @pytest.mark.asyncio
    async def test_workspace_quality_ignores_deterministic_results_without_write_evidence(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-deterministic-no-write",
            config=FactoryConfig(name="quality-deterministic-no-write"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-23T00:00:00+00:00",
        )
        state = {"repaired": False}
        llm_repair_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            if state["repaired"]:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "test passed",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": "> node tests/run-tests.js",
                "stderr_tail": "Error: Cannot find module 'tests/run-tests.js'",
                "error": "",
            }

        def fake_apply_workspace_quality_repairs(
            *,
            run_id: str,
            artifact_quality_errors: list[str],
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run_id == "factory-quality-deterministic-no-write"
            assert artifact_quality_errors
            return (
                [
                    {
                        "tool": "inspect_package_script",
                        "success": False,
                        "result": {
                            "source_tool": "director_materialization_quality_repair",
                            "reason": "missing target remains unresolved",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": False,
                    "source_tools": ["director_materialization_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": False,
                },
            )

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run_id: str,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal llm_repair_calls
            assert run_id == "factory-quality-deterministic-no-write"
            assert context["workspace_quality_repair_max_rounds"] == 1
            assert artifact_quality_errors
            assert repair_attempt == 1
            llm_repair_calls += 1
            state["repaired"] = True
            return (
                [
                    {
                        "tool": "write_file",
                        "tool_name": "write_file",
                        "success": True,
                        "result": {
                            "source_tool": "director_materialization_quality_repair",
                            "file": "tests/run-tests.js",
                            "operation": "create",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "repair_mode": "director_llm",
                    "source_tools": ["director_materialization_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "test"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", fake_apply_workspace_quality_repairs)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_llm_repairs",
            fake_apply_workspace_quality_llm_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        assert llm_repair_calls == 1
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["repair"]["success"] is True
        assert payload["repair"]["write_tool_evidence"] is True
        assert payload["repair"]["rounds"][0]["evidence"] == [
            "repair_write:tool=director_materialization_quality_repair;file=tests/run-tests.js;operation=create"
        ]

    @pytest.mark.asyncio
    async def test_workspace_quality_projects_task_boundary_triage_without_llm_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-task-boundary-triage",
            config=FactoryConfig(name="quality-task-boundary-triage"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-28T00:00:00+00:00",
        )
        llm_repair_calls = 0
        post_repair_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": "tests/behavior.test.ts(3,10): error TS2305: Module has no exported member 'openMarket'.",
                "stderr_tail": "",
                "error": "",
            }

        def fake_apply_workspace_quality_repairs(
            *,
            run_id: str,
            artifact_quality_errors: list[str],
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run_id == "factory-quality-task-boundary-triage"
            assert artifact_quality_errors
            return (
                [],
                {
                    "stage": "runtime_plan_probe_unplannable",
                    "attempted": True,
                    "success": False,
                    "success_reason": "task_boundary_interface_discrepancy_required",
                    "tool_results": 0,
                    "source_tools": [],
                    "plan_probe_preaudit": {
                        "status": "coverage_matched_but_unplannable",
                        "plannable_source_tools": [],
                        "covered_unplannable_source_tools": ["deterministic_typescript_missing_export_repair"],
                    },
                    "interface_discrepancy_evidence": {
                        "schema_version": "director.interface_discrepancy_receipt.v1",
                        "reason": "coverage_matched_but_unplannable",
                        "recommended_owner": "chief_engineer",
                        "recommended_route": "pending_design_interface_contract",
                    },
                },
            )

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run_id: str,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
            interface_discrepancy_evidence: dict[str, Any] | None = None,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal llm_repair_calls
            del run_id, context, artifact_quality_errors, repair_attempt, interface_discrepancy_evidence
            llm_repair_calls += 1
            return [], {}

        def fake_apply_workspace_quality_cpp_post_repairs() -> list[dict[str, object]]:
            nonlocal post_repair_calls
            post_repair_calls += 1
            return []

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", fake_apply_workspace_quality_repairs)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_llm_repairs",
            fake_apply_workspace_quality_llm_repairs,
        )
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_cpp_post_repairs",
            fake_apply_workspace_quality_cpp_post_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is False
        assert llm_repair_calls == 0
        assert post_repair_calls == 0
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["warnings"] == ["task_boundary_interface_discrepancy_required"]
        assert payload["repair"]["task_boundary_triage_required"] is True
        assert payload["repair"]["success_reason"] == "task_boundary_interface_discrepancy_required"
        assert payload["repair"]["plan_probe_preaudit"]["status"] == "coverage_matched_but_unplannable"
        assert payload["repair"]["interface_discrepancy_evidence"]["reason"] == ("coverage_matched_but_unplannable")
        assert payload["repair"]["rounds"][0]["task_boundary_triage_required"] is True
        assert payload["repair"]["rounds"][0]["repair_summary"]["stage"] == "runtime_plan_probe_unplannable"

    @pytest.mark.asyncio
    async def test_workspace_quality_routes_local_task_boundary_triage_to_director_repair(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-local-task-boundary-triage",
            config=FactoryConfig(name="quality-local-task-boundary-triage"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-28T00:00:00+00:00",
        )
        state = {"repaired": False}
        llm_repair_contexts: list[dict[str, Any]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            if state["repaired"]:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "build passed",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": (
                    "src/main.ts(105,55): error TS2339: Property 'revenue' does not exist on type 'TransactionResult'."
                ),
                "stderr_tail": "",
                "error": "",
            }

        def fake_apply_workspace_quality_repairs(
            *,
            run_id: str,
            artifact_quality_errors: list[str],
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run_id == "factory-quality-local-task-boundary-triage"
            assert artifact_quality_errors
            return (
                [],
                {
                    "stage": "runtime_plan_probe_unplannable",
                    "attempted": True,
                    "success": False,
                    "success_reason": "task_boundary_interface_discrepancy_required",
                    "tool_results": 0,
                    "source_tools": [],
                    "plan_probe_preaudit": {
                        "status": "coverage_matched_but_unplannable",
                        "plannable_source_tools": [],
                        "covered_unplannable_source_tools": ["deterministic_typescript_missing_member_repair"],
                    },
                },
            )

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run_id: str,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
            interface_discrepancy_evidence: dict[str, Any] | None = None,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            del run_id, context, artifact_quality_errors, repair_attempt
            assert interface_discrepancy_evidence is not None
            assert interface_discrepancy_evidence["recommended_owner"] == "director"
            assert interface_discrepancy_evidence["director_retry_allowed"] is True
            llm_repair_contexts.append(interface_discrepancy_evidence)
            state["repaired"] = True
            return (
                [{"success": True, "tool": "write_file", "file": "src/main.ts", "operation": "update"}],
                {
                    "stage": "quality_repair",
                    "attempted": True,
                    "success": False,
                    "tool_results": 1,
                    "write_tool_evidence": True,
                    "source_tools": ["director_materialization_quality_repair"],
                    "interface_discrepancy_evidence": interface_discrepancy_evidence,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", fake_apply_workspace_quality_repairs)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_llm_repairs",
            fake_apply_workspace_quality_llm_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        assert len(llm_repair_contexts) == 1
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is True
        assert payload["repair"]["success"] is True
        assert payload["repair"]["rounds"][0]["repair_summary"]["stage"] == "quality_repair"

    @pytest.mark.asyncio
    async def test_workspace_quality_reruns_prepare_after_successful_repair(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-prepare-after-repair",
            config=FactoryConfig(name="quality-prepare-after-repair"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        state = {"repaired": False, "prepared_after_repair": False}
        phases_seen: list[str] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            is_prepare = command == ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]
            if is_prepare and state["repaired"]:
                state["prepared_after_repair"] = True
            if is_prepare:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "installed",
                    "stderr_tail": "",
                    "error": "",
                }
            if not state["repaired"]:
                return {
                    "command": command,
                    "exit_code": 2,
                    "passed": False,
                    "stdout_tail": "src/index.ts(1,10): error TS2305: missing export",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 0 if state["prepared_after_repair"] else 1,
                "passed": bool(state["prepared_after_repair"]),
                "stdout_tail": "build passed" if state["prepared_after_repair"] else "",
                "stderr_tail": "" if state["prepared_after_repair"] else "missing dependency",
                "error": "" if state["prepared_after_repair"] else "missing dependency",
            }

        def fake_apply_workspace_quality_repairs(
            *,
            run_id: str,
            artifact_quality_errors: list[str],
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run_id == "factory-quality-prepare-after-repair"
            assert artifact_quality_errors
            state["repaired"] = True
            return (
                [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {
                            "source_tool": "deterministic_typescript_missing_export_repair",
                            "file": "src/index.ts",
                            "operation": "modify",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["deterministic_typescript_missing_export_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        def record_phases(payload: dict[str, Any]) -> None:
            phases_seen.append(str(payload.get("phase") or ""))

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(
            executor,
            "_workspace_quality_prepare_commands",
            lambda commands, context: [["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]],
        )
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", fake_apply_workspace_quality_repairs)

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        for command_result in payload["commands"]:
            record_phases(command_result)
        assert phases_seen == ["prepare", "check", "prepare_after_repair", "check_after_repair"]
        assert payload["passed"] is True

    @pytest.mark.asyncio
    async def test_unplannable_cross_file_typescript_missing_export_routes_to_task_boundary_triage(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        model_dir = tmp_path / "src" / "models"
        model_dir.mkdir(parents=True)
        (tmp_path / "src" / "index.ts").write_text(
            "import { MoonPhaseModel } from './models/moonphase';\n"
            "export class Garden {\n"
            "  private moon = new MoonPhaseModel();\n"
            "  public snapshot(): unknown {\n"
            "    return this.moon.getState();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (model_dir / "moonphase.ts").write_text(
            "export enum MoonPhase {\n  New,\n  Full,\n}\n",
            encoding="utf-8",
        )
        run = FactoryRun(
            id="factory-quality-multiround-repair",
            config=FactoryConfig(name="quality-multiround-repair"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            model_text = (model_dir / "moonphase.ts").read_text(encoding="utf-8")
            if "export class MoonPhaseModel" not in model_text:
                return {
                    "command": command,
                    "exit_code": 2,
                    "passed": False,
                    "stdout_tail": (
                        "src/index.ts(1,10): error TS2305: Module '\"./models/moonphase\"' "
                        "has no exported member 'MoonPhaseModel'."
                    ),
                    "stderr_tail": "",
                    "error": "",
                }
            if "getState(" not in model_text:
                return {
                    "command": command,
                    "exit_code": 2,
                    "passed": False,
                    "stdout_tail": (
                        "src/index.ts(5,22): error TS2339: Property 'getState' does not exist on type 'MoonPhaseModel'."
                    ),
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 0,
                "passed": True,
                "stdout_tail": "build passed",
                "stderr_tail": "",
                "error": "",
            }

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)

        passed, artifact = await executor._run_workspace_quality_checks(run, {})

        assert passed is False
        assert calls == [["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert payload["warnings"] == ["task_boundary_interface_discrepancy_required"]
        assert [item["phase"] for item in payload["commands"]] == ["check"]
        repair = payload["repair"]
        assert repair["task_boundary_triage_required"] is True
        assert repair["success_reason"] == "task_boundary_interface_discrepancy_required"
        assert repair["plan_probe_preaudit"]["status"] == "coverage_matched_but_unplannable"
        assert repair["plan_probe_preaudit"]["covered_unplannable_diagnostic_count"] == 2
        assert repair["write_tool_evidence"] is False
        assert repair["tool_results"] == 0

    @pytest.mark.asyncio
    async def test_typescript_enum_repair_requires_canonical_director_execution_before_rerun(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        model_dir = tmp_path / "src" / "models"
        model_dir.mkdir(parents=True)
        moonphase = model_dir / "moonphase.ts"
        moonphase.write_text(
            "\n".join(
                [
                    "export enum MoonPhase {",
                    "  New,",
                    "  Full,",
                    "  WaningCrescent;",
                    "}",
                    "",
                    "export interface MoonState {",
                    "  phase: MoonPhase;",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        run = FactoryRun(
            id="factory-enum-repair",
            config=FactoryConfig(name="enum-repair"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            repaired_source = moonphase.read_text(encoding="utf-8")
            repaired = "  WaningCrescent," in repaired_source and "  phase: MoonPhase;" in repaired_source
            if repaired:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "build passed",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": (
                    "src/models/moonphase.ts(4,18): error TS1357: "
                    "An enum member name must be followed by a ',', '=', or '}'."
                ),
                "stderr_tail": "",
                "error": "",
            }

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)

        passed, artifact = await executor._run_workspace_quality_checks(run, {})

        assert passed is False
        assert calls == [["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert [item["phase"] for item in payload["commands"]] == ["check"]
        assert payload["repair"]["write_tool_evidence"] is False
        assert payload["repair"]["tool_results"] == 0
        assert "  WaningCrescent;" in moonphase.read_text(encoding="utf-8")
        assert "  phase: MoonPhase;" in moonphase.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_typescript_identifier_repair_requires_canonical_director_execution_before_rerun(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        engine_dir = tmp_path / "src" / "engine"
        engine_dir.mkdir(parents=True)
        simulation = engine_dir / "simulation.ts"
        simulation.write_text(
            "\n".join(
                [
                    "export interface GardenState { moonPhase: number; humidity: number; tick: number; }",
                    "",
                    "export function tickGarden(state: GardenState): GardenState {",
                    "  const newState = { ...state, tick: state.tick + 1 };",
                    "  return newState;",
                    "}",
                    "",
                    "export function getGardenSummary(state: GardenState): string {",
                    "  return [",
                    "    `${newState.moonPhase}`;",
                    "    `${newState.humidity}`;",
                    "    `${newState.tick}`;",
                    "  ].join('\\n');",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        run = FactoryRun(
            id="factory-unresolved-identifier-repair",
            config=FactoryConfig(name="unresolved-identifier-repair"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            repaired_source = simulation.read_text(encoding="utf-8")
            repaired = (
                "`${state.moonPhase}`;" in repaired_source
                and "`${state.humidity}`;" in repaired_source
                and "`${state.tick}`;" in repaired_source
                and "const newState = { ...state" in repaired_source
            )
            if repaired:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "build passed",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": (
                    "src/engine/simulation.ts(10,8): error TS2304: Cannot find name 'newState'.\n"
                    "src/engine/simulation.ts(11,8): error TS2304: Cannot find name 'newState'.\n"
                    "src/engine/simulation.ts(12,8): error TS2304: Cannot find name 'newState'."
                ),
                "stderr_tail": "",
                "error": "",
            }

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)

        passed, artifact = await executor._run_workspace_quality_checks(run, {})

        assert passed is False
        assert calls == [["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert [item["phase"] for item in payload["commands"]] == ["check"]
        assert payload["repair"]["write_tool_evidence"] is False
        assert payload["repair"]["tool_results"] == 0
        repaired_source = simulation.read_text(encoding="utf-8")
        assert "return newState;" in repaired_source
        assert "`${newState.moonPhase}`;" in repaired_source
        assert "`${newState.humidity}`;" in repaired_source
        assert "`${newState.tick}`;" in repaired_source


# ---------------------------------------------------------------------------
# Director-evidence truth tables
# ---------------------------------------------------------------------------


class TestDirectorEvidenceStatics:
    def test_is_taskboard_converged(self) -> None:
        assert OrchestrationStageExecutor._is_taskboard_converged(
            {"pending": 0, "ready": 0, "in_progress": 0, "blocked": 0}
        )
        assert not OrchestrationStageExecutor._is_taskboard_converged({"pending": 1})
        for active_status in ("in_design", "in_execution", "in_qa", "waiting_human"):
            assert not OrchestrationStageExecutor._is_taskboard_converged({active_status: 1})

    def test_has_director_progress(self) -> None:
        before = {"completed": 0}
        after = {"completed": 1}
        assert OrchestrationStageExecutor._has_director_progress(before, after) is True
        assert OrchestrationStageExecutor._has_director_progress(before, before) is False
        assert OrchestrationStageExecutor._has_director_progress({"in_execution": 1}, {"in_execution": 0}) is True

    def test_workspace_delivery_delta_counts_added_and_changed_files(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")

        before = executor._capture_workspace_delivery_state()
        (tmp_path / "src" / "index.ts").write_text("export const value = 22;\n", encoding="utf-8")
        (tmp_path / "src" / "main.ts").write_text("import './index';\n", encoding="utf-8")

        delta = OrchestrationStageExecutor._workspace_delivery_delta(
            before,
            executor._capture_workspace_delivery_state(),
        )

        assert delta["added_count"] == 1
        assert delta["changed_count"] == 1
        assert delta["delta_file_count"] == 2
        assert OrchestrationStageExecutor._workspace_delta_indicates_materialization_progress(delta) is True

    def test_workspace_delivery_delta_ignores_python_runtime_cache(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        before = executor._capture_workspace_delivery_state()
        cache_dir = tmp_path / "tests" / "__pycache__"
        cache_dir.mkdir(parents=True)
        (cache_dir / "test_product.cpython-312.pyc").write_bytes(b"cache")

        delta = OrchestrationStageExecutor._workspace_delivery_delta(
            before,
            executor._capture_workspace_delivery_state(),
        )

        assert delta["added_count"] == 0
        assert delta["changed_count"] == 0
        assert delta["delta_file_count"] == 0
        assert OrchestrationStageExecutor._workspace_delta_indicates_materialization_progress(delta) is False

    def test_legacy_text_and_metadata_authority_helpers_are_removed(self) -> None:
        for helper_name in (
            "_failed_task_records_indicate_materialization_quality_handoff",
            "_failed_task_records_indicate_quality_handoff",
            "_is_director_no_materialized_changes",
        ):
            assert not hasattr(OrchestrationStageExecutor, helper_name)

    def test_director_provider_rate_limit_signal_from_llm_error_event(self) -> None:
        signal = OrchestrationStageExecutor._director_provider_health_failure_signal_from_events(
            [
                {
                    "event": "llm_error",
                    "role": "director",
                    "terminal": True,
                    "provider_id": "minimax",
                    "model": "MiniMax-M3",
                    "source_path": "runtime/events/director.llm.events.jsonl",
                    "raw": {
                        "data": {
                            "error_category": "rate_limit",
                            "error_message": "429 Rate limited: Token Plan 用量上限",
                        }
                    },
                }
            ]
        )

        assert signal is not None
        assert signal["code"] == "director.provider_rate_limit"
        assert signal["failure_class"] == "RESOURCE_BUDGET_EXHAUSTED"
        assert signal["responsible_layer"] == "model_provider"
        assert signal["repairable_by_director"] is False

    def test_qa_report_has_warning(self) -> None:
        assert OrchestrationStageExecutor._qa_report_has_warning({"warnings": ["w1", "w2"]}, "w2") is True
        assert OrchestrationStageExecutor._qa_report_has_warning({"warnings": "w1,w2"}, "w2") is True
        assert OrchestrationStageExecutor._qa_report_has_warning({"warnings": ["w1"]}, "w2") is False


class _PartialFailureProgressExecutor(OrchestrationStageExecutor):
    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace)
        self.results = [
            CommandResult(
                run_id="director-round-1",
                status="failed",
                message="Director binding fanout: 2 bindings, 1 succeeded, 1 failed",
                metadata={
                    "binding_fanout": True,
                    "per_binding": [
                        {"provider_id": "p1", "model": "m1", "run_id": "r1", "status": "completed"},
                        {"provider_id": "p2", "model": "m2", "run_id": "r2", "status": "timeout"},
                    ],
                },
            ),
            CommandResult(
                run_id="director-round-2",
                status="completed",
                message="Run status: completed",
                metadata={
                    "binding_fanout": True,
                    "per_binding": [
                        {"provider_id": "p1", "model": "m1", "run_id": "r3", "status": "completed"},
                        {"provider_id": "p2", "model": "m2", "run_id": "r4", "status": "completed"},
                    ],
                },
            ),
        ]
        self.stats = [
            {"total": 2, "pending": 2, "ready": 2, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
            {"total": 2, "pending": 2, "ready": 2, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
            {"total": 2, "pending": 1, "ready": 1, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 0},
            {"total": 2, "pending": 1, "ready": 1, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 0},
            {"total": 2, "pending": 0, "ready": 0, "in_progress": 0, "completed": 2, "failed": 0, "blocked": 0},
            {"total": 2, "pending": 0, "ready": 0, "in_progress": 0, "completed": 2, "failed": 0, "blocked": 0},
        ]

    def _build_orchestration_service(self, context: dict) -> object:
        del context
        return object()

    def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        del context
        return [
            {"provider_id": "p1", "model": "m1"},
            {"provider_id": "p2", "model": "m2"},
        ]

    def _read_taskboard_stats(self) -> dict[str, int]:
        if len(self.stats) > 1:
            return dict(self.stats.pop(0))
        return dict(self.stats[0])

    def _canonical_factory_projection(
        self,
        _run: FactoryRun,
        _context: dict[str, Any],
    ) -> dict[str, Any]:
        completed = not self.results
        return _with_task_runtime_authority(
            {
                "source": "run_ledger",
                "task_boundary": {
                    "latest_by_task": {
                        task_id: {
                            "task_id": task_id,
                            "status": "completed_verified" if completed else "in_execution",
                            "ok": completed,
                        }
                        for task_id in ("TASK-1", "TASK-2")
                    }
                },
            },
            task_ids=("TASK-1", "TASK-2"),
        )

    async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
        del kwargs
        return self.results.pop(0)

    def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
        del additional_events
        return True, []


class TestDirectorDispatchLoop:
    @pytest.fixture(autouse=True)
    def _use_short_fake_dispatch_budget(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Keep fake dispatch tests fast without weakening production policy."""

        def _policy(context: dict[str, Any]) -> FactoryDeadlineBudgetPolicyV1:
            settlement_seconds = min(
                5,
                max(
                    0,
                    int(
                        context.get(
                            "director_dispatch_timeout_settle_grace_seconds",
                            0,
                        )
                    ),
                ),
            )
            return FactoryDeadlineBudgetPolicyV1(
                chief_engineer_min_start_seconds=1,
                director_first_task_min_seconds=1,
                director_followup_task_min_seconds=1,
                quality_gate_reserved_seconds=0,
                quality_gate_min_start_reserved_seconds=0,
                safety_seconds=0,
                director_settlement_barrier_seconds=settlement_seconds,
            )

        monkeypatch.setattr(
            OrchestrationStageExecutor,
            "_factory_deadline_budget_policy",
            staticmethod(_policy),
        )

    @pytest.mark.asyncio
    async def test_dependency_settle_barrier_exposes_new_claimable_task(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        claim_reads = iter(([], ["TASK-2"]))
        stats = {
            "total": 2,
            "pending": 1,
            "ready": 1,
            "in_progress": 0,
            "completed": 1,
            "failed": 0,
            "blocked": 0,
        }
        monkeypatch.setattr(
            executor,
            "_read_claimable_director_task_ids",
            lambda *, limit, factory_run_id="": list(next(claim_reads)),
        )
        monkeypatch.setattr(executor, "_read_taskboard_stats", lambda: dict(stats))

        task_ids, observed_stats = await executor._wait_for_claimable_director_tasks(
            limit=1,
            grace_seconds=0.2,
        )

        assert task_ids == ["TASK-2"]
        assert observed_stats == stats

    @pytest.mark.asyncio
    async def test_run_completion_waiter_cancel_event_propagates_to_active_orchestration_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(60))
                self._active_runs = {"run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        class _FakeCommandService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await RunCompletionWaiter(tmp_path).wait(
            _FakeCommandService(),
            CommandResult(run_id="run-1", status="running", message="submitted"),
            timeout_seconds=30,
            cancel_event=cancel_event,
        )

        assert result.status == "cancelled"
        assert result.message == "Run cancelled: factory_cancelled"
        assert fake_orchestration.cancelled == [("run-1", True)]
        await asyncio.sleep(0)
        assert fake_orchestration.active_task.cancelled()

    @pytest.mark.asyncio
    async def test_run_completion_waiter_cancel_event_preserves_active_director_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(60))
                self._active_runs = {"run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        class _FakeCommandService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.create_task_row(subject="cancelled active director task")
        claim = task_runtime.claim_execution(
            task["id"],
            worker_id="director",
            role_id="director",
            run_id="run-1",
            selection_source="unit",
        )
        assert claim["success"] is True
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await RunCompletionWaiter(tmp_path).wait(
            _FakeCommandService(),
            CommandResult(run_id="run-1", status="running", message="submitted"),
            timeout_seconds=30,
            cancel_event=cancel_event,
        )

        assert result.status == "cancelled"
        assert result.metadata["cancel_signal_sent"] is False
        assert result.metadata["cancel_reason"] == "factory_cancelled"
        assert result.metadata["inflight_run_continues"] is True
        assert result.metadata["terminal_source"] == "task_runtime_active_execution_barrier"
        assert result.metadata["active_task_count"] == 1
        assert result.metadata["active_task_ids"] == [str(task["id"])]
        assert result.metadata["barrier_cancel_deferred"] is True
        assert fake_orchestration.cancelled == []
        assert fake_orchestration.active_task.cancelled() is False
        guarded_heartbeat = task_runtime.heartbeat_execution(
            task["id"],
            session_id=str(claim["session"]["session_id"]),
        )
        assert guarded_heartbeat["success"] is True

        fake_orchestration.active_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_orchestration.active_task

    @pytest.mark.asyncio
    async def test_cancel_active_run_preserves_active_director_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(60))
                self._active_runs = {"run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.create_task_row(subject="direct cancellation active director task")
        claim = task_runtime.claim_execution(
            task["id"],
            worker_id="director",
            role_id="director",
            run_id="run-1",
            selection_source="unit",
        )
        assert claim["success"] is True

        result = await RunCompletionWaiter(tmp_path).cancel_active_run("run-1", reason="factory_stage_timeout")

        assert result is not None
        assert result.status == "timeout"
        assert result.metadata == {
            "cancel_signal_sent": False,
            "cancel_reason": "factory_stage_timeout",
            "inflight_run_continues": True,
            "terminal_source": "task_runtime_active_execution_barrier",
            "active_task_count": 1,
            "active_task_ids": [str(task["id"])],
        }
        assert fake_orchestration.cancelled == []
        assert fake_orchestration.active_task.cancelled() is False
        guarded_heartbeat = task_runtime.heartbeat_execution(
            task["id"],
            session_id=str(claim["session"]["session_id"]),
        )
        assert guarded_heartbeat["success"] is True

        fake_orchestration.active_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_orchestration.active_task

    @pytest.mark.asyncio
    async def test_run_completion_waiter_timeout_preserves_active_director_session_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(60))
                self._active_runs = {"run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        class _FakeCommandService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.create_task_row(subject="late director task")
        claim = task_runtime.claim_execution(
            task["id"],
            worker_id="director",
            role_id="director",
            run_id="run-1",
            selection_source="unit",
        )
        assert claim["success"] is True

        result = await RunCompletionWaiter(tmp_path).wait(
            _FakeCommandService(),
            CommandResult(run_id="run-1", status="running", message="submitted"),
            timeout_seconds=0,
        )

        assert result.status == "timeout"
        assert result.metadata == {
            "cancel_signal_sent": False,
            "cancel_reason": "factory_stage_timeout",
            "inflight_run_continues": True,
            "terminal_source": "task_runtime_active_execution_barrier",
            "active_task_count": 1,
            "active_task_ids": [str(task["id"])],
        }
        assert fake_orchestration.cancelled == []
        await asyncio.sleep(0)
        assert fake_orchestration.active_task.cancelled() is False
        guarded_heartbeat = task_runtime.heartbeat_execution(
            task["id"],
            session_id=str(claim["session"]["session_id"]),
        )
        assert guarded_heartbeat["success"] is True

        fake_orchestration.active_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_orchestration.active_task

    @pytest.mark.asyncio
    async def test_run_completion_waiter_timeout_matches_active_director_by_factory_run_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(60))
                self._active_runs = {"factory-run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        class _FakeCommandService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.create_task_row(
            subject="active director task owned by a factory run",
            metadata={"factory_run_id": "factory-run-1"},
        )
        claim = task_runtime.claim_execution(
            task["id"],
            worker_id="director",
            role_id="director",
            run_id="director-run-1",
            selection_source="unit",
        )
        assert claim["success"] is True

        result = await RunCompletionWaiter(tmp_path).wait(
            _FakeCommandService(),
            CommandResult(run_id="factory-run-1", status="running", message="submitted"),
            timeout_seconds=0,
        )

        assert result.status == "timeout"
        assert result.metadata == {
            "cancel_signal_sent": False,
            "cancel_reason": "factory_stage_timeout",
            "inflight_run_continues": True,
            "terminal_source": "task_runtime_active_execution_barrier",
            "active_task_count": 1,
            "active_task_ids": [str(task["id"])],
        }
        assert fake_orchestration.cancelled == []
        assert fake_orchestration.active_task.cancelled() is False
        guarded_heartbeat = task_runtime.heartbeat_execution(
            task["id"],
            session_id=str(claim["session"]["session_id"]),
        )
        assert guarded_heartbeat["success"] is True

        fake_orchestration.active_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_orchestration.active_task

    @pytest.mark.asyncio
    async def test_run_completion_waiter_soft_timeout_preserves_active_director_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(60))
                self._active_runs = {"run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        class _FakeCommandService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.create_task_row(subject="inflight director task")
        claim = task_runtime.claim_execution(
            task["id"],
            worker_id="director",
            role_id="director",
            run_id="run-1",
            selection_source="unit",
        )
        assert claim["success"] is True

        result = await RunCompletionWaiter(tmp_path).wait(
            _FakeCommandService(),
            CommandResult(run_id="run-1", status="running", message="submitted"),
            timeout_seconds=0,
            cancel_on_timeout=False,
        )

        assert result.status == "timeout"
        assert result.metadata["cancel_signal_sent"] is False
        assert result.metadata["cancel_reason"] == "factory_stage_timeout"
        assert result.metadata["inflight_run_continues"] is True
        assert result.metadata["canonical_authoritative"] is False
        assert fake_orchestration.cancelled == []
        assert fake_orchestration.active_task.cancelled() is False
        guarded_heartbeat = task_runtime.heartbeat_execution(
            task["id"],
            session_id=str(claim["session"]["session_id"]),
        )
        assert guarded_heartbeat["success"] is True

        fake_orchestration.active_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fake_orchestration.active_task

    @pytest.mark.asyncio
    async def test_run_completion_waiter_run_not_found_abort_preserves_child_director_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _FakeOrchestrationService:
            def __init__(self) -> None:
                self.active_task = asyncio.create_task(asyncio.sleep(0))
                self._active_runs = {"run-1": self.active_task}
                self.cancelled: list[tuple[str, bool]] = []

            async def cancel_run(self, run_id: str, force: bool = False) -> object:
                self.cancelled.append((run_id, force))
                self.active_task.cancel()
                return object()

        class _FakeCommandService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="completed", message="done")

        fake_orchestration = _FakeOrchestrationService()

        async def _fake_get_orchestration_service() -> _FakeOrchestrationService:
            return fake_orchestration

        async def _run_not_found_abort_checker() -> str:
            return "run_not_found"

        monkeypatch.setattr(
            "polaris.cells.orchestration.workflow_runtime.public.get_orchestration_service",
            _fake_get_orchestration_service,
        )
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.create_task_row(subject="director child run task")
        claim = task_runtime.claim_execution(
            task["id"],
            worker_id="director",
            role_id="director",
            run_id="run-1",
            selection_source="unit",
        )
        assert claim["success"] is True

        result = await RunCompletionWaiter(tmp_path).wait(
            _FakeCommandService(),
            CommandResult(run_id="run-1", status="running", message="submitted"),
            timeout_seconds=30,
            abort_checker=_run_not_found_abort_checker,
        )

        assert result.status == "failed"
        assert result.reason_code == "canonical_terminal_projection_missing"
        assert fake_orchestration.cancelled == []
        guarded_heartbeat = task_runtime.heartbeat_execution(
            task["id"],
            session_id=str(claim["session"]["session_id"]),
        )
        assert guarded_heartbeat["success"] is True

    @pytest.mark.asyncio
    async def test_director_timeout_settle_cancel_event_without_active_task_still_cancels_run(
        self,
        tmp_path: Path,
    ) -> None:
        class _FakeRunCompletionWaiter:
            def __init__(self) -> None:
                self.cancelled: list[tuple[str, str]] = []

            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                del run_id, process_terminal
                return None

            async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
                self.cancelled.append((run_id, reason))

        fake_waiter = _FakeRunCompletionWaiter()
        executor = _executor(tmp_path)
        executor._run_completion_waiter = fake_waiter  # type: ignore[assignment]
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await executor._settle_inflight_director_run_after_timeout(
            service=object(),  # type: ignore[arg-type]
            run_id="run-2",
            grace_seconds=30,
            cancel_event=cancel_event,
        )

        assert result is not None
        assert result.status == "cancelled"
        assert result.message == "Run cancelled: factory_cancelled"
        assert fake_waiter.cancelled == [("run-2", "factory_cancelled")]

    @pytest.mark.asyncio
    async def test_director_timeout_settle_cancel_event_prefers_canonical_terminal_outcome(
        self,
        tmp_path: Path,
    ) -> None:
        class _FakeRunCompletionWaiter:
            def __init__(self) -> None:
                self.cancelled: list[tuple[str, str]] = []

            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                del process_terminal
                return CommandResult(
                    run_id=run_id,
                    status="completed",
                    message="canonical outcome committed",
                    metadata={
                        "canonical_authoritative": True,
                        "fact_event_seq": 31,
                    },
                )

            async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
                self.cancelled.append((run_id, reason))

        class _FakeService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="completed", message="done")

        class _ActiveExecutor(OrchestrationStageExecutor):
            def _read_taskboard_stats(self) -> dict[str, int]:
                return {
                    "total": 1,
                    "pending": 0,
                    "ready": 0,
                    "in_progress": 1,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }

        fake_waiter = _FakeRunCompletionWaiter()
        executor = _ActiveExecutor(tmp_path)
        executor._run_completion_waiter = fake_waiter  # type: ignore[assignment]
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await executor._settle_inflight_director_run_after_timeout(
            service=_FakeService(),  # type: ignore[arg-type]
            run_id="run-2",
            grace_seconds=30,
            cancel_event=cancel_event,
        )

        assert result is not None
        assert result.status == "completed"
        assert result.message == "canonical outcome committed"
        assert fake_waiter.cancelled == []

    @pytest.mark.asyncio
    async def test_director_timeout_settle_cancel_event_preserves_active_task_runtime_barrier(
        self,
        tmp_path: Path,
    ) -> None:
        class _FakeRunCompletionWaiter:
            def __init__(self) -> None:
                self.cancelled: list[tuple[str, str]] = []

            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                if not process_terminal:
                    return None
                return CommandResult(
                    run_id=run_id,
                    status="completed",
                    message="canonical outcome committed",
                    metadata={
                        "canonical_authoritative": True,
                        "fact_event_seq": 32,
                    },
                )

            def active_execution_barrier_result(self, *, run_id: str, reason: str) -> CommandResult:
                return CommandResult(
                    run_id=run_id,
                    status="cancelled" if reason == "factory_cancelled" else "timeout",
                    message=f"Director run left active for execution-control-plane barrier: {reason}",
                    metadata={
                        "cancel_signal_sent": False,
                        "cancel_reason": reason,
                        "inflight_run_continues": True,
                        "terminal_source": "task_runtime_active_execution_barrier",
                        "active_task_count": 1,
                        "active_task_ids": ["TASK-1"],
                    },
                )

            async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
                self.cancelled.append((run_id, reason))

        class _SettlingService:
            def __init__(self) -> None:
                self.calls = 0

            async def query_run_status(self, run_id: str) -> CommandResult:
                self.calls += 1
                if self.calls == 1:
                    return CommandResult(run_id=run_id, status="running", message="settling")
                return CommandResult(run_id=run_id, status="completed", message="done")

        fake_waiter = _FakeRunCompletionWaiter()
        executor = OrchestrationStageExecutor(tmp_path)
        executor._run_completion_waiter = fake_waiter  # type: ignore[assignment]
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await executor._settle_inflight_director_run_after_timeout(
            service=_SettlingService(),  # type: ignore[arg-type]
            run_id="run-2",
            grace_seconds=30,
            cancel_event=cancel_event,
        )

        assert result is not None
        assert result.status == "completed"
        assert result.metadata["canonical_authoritative"] is True
        assert result.metadata["fact_event_seq"] == 32
        assert result.metadata["cancel_signal_sent"] is False
        assert result.metadata["barrier_cancel_deferred"] is True
        assert result.metadata["deferred_cancel_reason"] == "factory_cancelled"
        assert fake_waiter.cancelled == []

    @pytest.mark.asyncio
    async def test_director_timeout_settle_records_progress_without_extending_hard_deadline(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _ProgressWaiter:
            def __init__(self) -> None:
                self.marker_index = 0
                self.cancelled: list[tuple[str, str]] = []

            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                if not process_terminal:
                    return None
                return CommandResult(
                    run_id=run_id,
                    status="completed",
                    message="canonical outcome committed",
                    metadata={
                        "canonical_authoritative": True,
                        "fact_event_seq": 33,
                    },
                )

            def active_execution_progress_marker(
                self,
                *,
                run_id: str,
            ) -> tuple[tuple[str, str, str, str], ...]:
                del run_id
                self.marker_index += 1
                return (("TASK-1", str(self.marker_index), f"heartbeat-{self.marker_index}", "in_progress"),)

            async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
                self.cancelled.append((run_id, reason))

        class _ProgressService:
            def __init__(self) -> None:
                self.calls = 0

            async def query_run_status(self, run_id: str) -> CommandResult:
                self.calls += 1
                if self.calls < 3:
                    return CommandResult(run_id=run_id, status="running", message="settling")
                return CommandResult(run_id=run_id, status="completed", message="done")

        async def _yield_without_waiting(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", _yield_without_waiting)
        fake_waiter = _ProgressWaiter()
        executor = OrchestrationStageExecutor(tmp_path)
        executor._run_completion_waiter = fake_waiter  # type: ignore[assignment]

        result = await executor._settle_inflight_director_run_after_timeout(
            service=_ProgressService(),  # type: ignore[arg-type]
            run_id="run-progress",
            grace_seconds=1,
        )

        assert result is not None
        assert result.status == "completed"
        assert result.metadata["barrier_progress_extensions"] == 2
        assert result.metadata["barrier_progress_source"] == "task_runtime_execution_fact"
        assert result.metadata["barrier_max_total_seconds"] == 1.0
        assert fake_waiter.cancelled == []

    @pytest.mark.asyncio
    async def test_director_timeout_settle_grace_expiry_preserves_active_task_runtime_barrier(
        self,
        tmp_path: Path,
    ) -> None:
        class _FakeRunCompletionWaiter:
            def __init__(self) -> None:
                self.cancelled: list[tuple[str, str]] = []

            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                del run_id, process_terminal
                return None

            def active_execution_barrier_result(self, *, run_id: str, reason: str) -> CommandResult:
                return CommandResult(
                    run_id=run_id,
                    status="timeout",
                    message=f"Director run left active for execution-control-plane barrier: {reason}",
                    metadata={
                        "cancel_signal_sent": False,
                        "cancel_reason": reason,
                        "inflight_run_continues": True,
                        "terminal_source": "task_runtime_active_execution_barrier",
                        "active_task_count": 1,
                        "active_task_ids": ["TASK-1"],
                    },
                )

            async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
                self.cancelled.append((run_id, reason))

        class _FakeService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        fake_waiter = _FakeRunCompletionWaiter()
        executor = OrchestrationStageExecutor(tmp_path)
        executor._run_completion_waiter = fake_waiter  # type: ignore[assignment]

        result = await executor._settle_inflight_director_run_after_timeout(
            service=_FakeService(),  # type: ignore[arg-type]
            run_id="run-3",
            grace_seconds=1,
        )

        assert result is not None
        assert result.status == "timeout"
        expected_metadata = {
            "cancel_signal_sent": False,
            "cancel_reason": "factory_stage_timeout",
            "inflight_run_continues": True,
            "timeout_settle_grace_seconds": 1,
            "terminal_source": "task_runtime_active_execution_barrier",
            "active_task_count": 1,
            "active_task_ids": ["TASK-1"],
            "barrier_state": "timeout",
            "barrier_timeout": True,
            "failure_class": FailureClassV1.RESOURCE_BUDGET_EXHAUSTED.value,
            "responsible_layer": "execution_control_plane",
        }
        assert {key: result.metadata[key] for key in expected_metadata} == expected_metadata
        assert result.metadata["barrier_max_total_seconds"] == 1.0
        assert 0.0 <= result.metadata["barrier_elapsed_seconds"] <= 1.0
        assert fake_waiter.cancelled == []

    @pytest.mark.asyncio
    async def test_director_timeout_status_query_cannot_outlive_settlement_lease(
        self,
        tmp_path: Path,
    ) -> None:
        class _BoundedWaiter:
            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                del run_id, process_terminal
                return None

            def active_execution_progress_marker(
                self,
                *,
                run_id: str,
            ) -> tuple[tuple[str, str, str, str], ...]:
                del run_id
                return (("TASK-1", "lease-1", "active", "in_progress"),)

            def active_execution_barrier_result(
                self,
                *,
                run_id: str,
                reason: str,
            ) -> CommandResult:
                return CommandResult(
                    run_id=run_id,
                    status="timeout",
                    message=reason,
                    metadata={
                        "cancel_signal_sent": False,
                        "inflight_run_continues": True,
                    },
                )

            async def cancel_active_run(
                self,
                run_id: str,
                *,
                reason: str,
            ) -> CommandResult | None:
                del run_id, reason
                raise AssertionError("active TaskRuntime barrier must defer cancellation")

        class _BlockingStatusService:
            async def query_run_status(self, run_id: str) -> CommandResult:
                await asyncio.sleep(60)
                return CommandResult(run_id=run_id, status="running", message="late")

        executor = OrchestrationStageExecutor(tmp_path)
        executor._run_completion_waiter = _BoundedWaiter()  # type: ignore[assignment]
        loop = asyncio.get_running_loop()
        started_at = loop.time()

        result = await executor._settle_inflight_director_run_after_timeout(
            service=_BlockingStatusService(),  # type: ignore[arg-type]
            run_id="run-bounded-query",
            grace_seconds=1,
        )

        assert loop.time() - started_at < 1.25
        assert result is not None
        assert result.status == "timeout"
        assert result.metadata["barrier_max_total_seconds"] == 1.0
        assert result.metadata["inflight_run_continues"] is True

    @pytest.mark.asyncio
    async def test_director_binding_fanout_waits_submitted_runs_concurrently(self, tmp_path: Path) -> None:
        class _FanoutService:
            def __init__(self) -> None:
                self.next_id = 0

            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                self.next_id += 1
                return CommandResult(run_id=f"run-{self.next_id}", status="running", message="submitted")

        class _ConcurrentWaitExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.started_waits: list[str] = []
                self.all_waits_started = asyncio.Event()

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                self.started_waits.append(initial_result.run_id)
                if len(self.started_waits) >= 2:
                    self.all_waits_started.set()
                await self.all_waits_started.wait()
                return CommandResult(run_id=initial_result.run_id, status="completed", message="done")

        executor = _ConcurrentWaitExecutor(tmp_path)
        result = await asyncio.wait_for(
            executor._execute_director_binding_fanout(
                service=_FanoutService(),
                workspace=str(tmp_path),
                tasks=["TASK-1", "TASK-2"],
                base_options={"execution_mode": "parallel", "max_workers": 2},
                bindings=[
                    {"provider_id": "p1", "model": "m1", "binding_id": "b1"},
                    {"provider_id": "p2", "model": "m2", "binding_id": "b2"},
                ],
                timeout_seconds=10,
                authority_port=_characterization_authority_port(),
            ),
            timeout=0.5,
        )

        assert result.status == "completed"
        assert sorted(executor.started_waits) == ["run-1", "run-2"]
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert [item["status"] for item in per_binding] == ["completed", "completed"]

    @pytest.mark.asyncio
    async def test_director_binding_fanout_cancel_event_preserves_active_task_runtime_barrier(
        self,
        tmp_path: Path,
    ) -> None:
        class _FanoutService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-active", status="running", message="submitted")

            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="running", message="still running")

        class _ActiveExecutor(OrchestrationStageExecutor):
            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, abort_checker, cancel_on_timeout
                assert cancel_event is not None and cancel_event.is_set()
                return self._run_completion_waiter.active_execution_barrier_result(
                    run_id=initial_result.run_id,
                    reason="factory_cancelled",
                )

        class _RunScopedBarrierWaiter:
            def active_execution_barrier_result(self, *, run_id: str, reason: str) -> CommandResult:
                return CommandResult(
                    run_id=run_id,
                    status="cancelled",
                    message=f"Director run left active for execution-control-plane barrier: {reason}",
                    metadata={
                        "cancel_signal_sent": False,
                        "cancel_reason": reason,
                        "inflight_run_continues": True,
                        "terminal_source": "task_runtime_active_execution_barrier",
                        "active_task_count": 1,
                        "active_task_ids": ["TASK-1"],
                    },
                )

        executor = _ActiveExecutor(tmp_path)
        executor._run_completion_waiter = _RunScopedBarrierWaiter()  # type: ignore[assignment]
        executor._binding_status_probe_seconds = 0.01
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await asyncio.wait_for(
            executor._execute_director_binding_fanout(
                service=_FanoutService(),
                workspace=str(tmp_path),
                tasks=["TASK-1"],
                base_options={"execution_mode": "parallel", "max_workers": 1},
                bindings=[{"provider_id": "p1", "model": "m1", "binding_id": "b1"}],
                timeout_seconds=10,
                cancel_event=cancel_event,
                authority_port=_characterization_authority_port(),
            ),
            timeout=0.5,
        )

        assert result.status == "failed"
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert per_binding == [
            {
                "provider_id": "p1",
                "model": "m1",
                "binding_id": "b1",
                "run_id": "run-active",
                "status": "cancelled",
                "message": "Director run left active for execution-control-plane barrier: factory_cancelled",
                "assigned_tasks": ["TASK-1"],
                "assigned_task_count": 1,
                "cancel_signal_sent": False,
                "cancel_reason": "factory_cancelled",
                "inflight_run_continues": True,
                "terminal_source": "task_runtime_active_execution_barrier",
                "active_task_count": 1,
                "active_task_ids": ["TASK-1"],
            }
        ]

    @pytest.mark.asyncio
    async def test_director_binding_fanout_cancel_event_prefers_canonical_terminal_outcome(
        self,
        tmp_path: Path,
    ) -> None:
        class _FanoutService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-done", status="running", message="submitted")

            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="completed", message="done")

        class _ActiveExecutor(OrchestrationStageExecutor):
            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, initial_result, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                return CommandResult(
                    run_id="run-done",
                    status="completed",
                    message="canonical outcome committed",
                    metadata={
                        "canonical_authoritative": True,
                        "fact_event_seq": 34,
                    },
                )

            def _read_taskboard_stats(self) -> dict[str, int]:
                return {
                    "total": 1,
                    "pending": 0,
                    "ready": 0,
                    "in_progress": 1,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }

        executor = _ActiveExecutor(tmp_path)
        executor._binding_status_probe_seconds = 0.01
        cancel_event = asyncio.Event()
        cancel_event.set()

        result = await asyncio.wait_for(
            executor._execute_director_binding_fanout(
                service=_FanoutService(),
                workspace=str(tmp_path),
                tasks=["TASK-1"],
                base_options={"execution_mode": "parallel", "max_workers": 1},
                bindings=[{"provider_id": "p1", "model": "m1", "binding_id": "b1"}],
                timeout_seconds=10,
                cancel_event=cancel_event,
                authority_port=_characterization_authority_port(),
            ),
            timeout=0.5,
        )

        assert result.status == "completed"
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert per_binding[0]["status"] == "completed"
        assert per_binding[0]["message"] == "canonical outcome committed"
        assert "inflight_run_continues" not in per_binding[0]

    @pytest.mark.asyncio
    async def test_director_binding_fanout_ignores_command_result_task_status_counts(
        self,
        tmp_path: Path,
    ) -> None:
        class _FanoutService:
            def __init__(self) -> None:
                self.queries = 0

            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-stuck", status="running", message="submitted")

            async def query_run_status(self, run_id: str) -> CommandResult:
                self.queries += 1
                return CommandResult(
                    run_id=run_id,
                    status="running",
                    message="Run status: running",
                    metadata={
                        "task_status_counts": {
                            "completed": 1,
                            "failed": 1,
                            "pending": 0,
                            "ready": 0,
                            "in_progress": 0,
                            "blocked": 0,
                        }
                    },
                )

        class _TerminalProbeExecutor(OrchestrationStageExecutor):
            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, initial_result, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                await asyncio.sleep(0.05)
                return CommandResult(run_id="run-stuck", status="completed", message="actual run completed")

        service = _FanoutService()
        executor = _TerminalProbeExecutor(tmp_path)
        executor._binding_status_probe_seconds = 0.01

        result = await asyncio.wait_for(
            executor._execute_director_binding_fanout(
                service=service,
                workspace=str(tmp_path),
                tasks=["TASK-1", "TASK-2"],
                base_options={"execution_mode": "parallel", "max_workers": 2},
                bindings=[{"provider_id": "p1", "model": "m1", "binding_id": "b1"}],
                timeout_seconds=60,
                authority_port=_characterization_authority_port(),
            ),
            timeout=1.0,
        )

        assert service.queries == 0
        assert result.status == "completed"
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert per_binding[0]["status"] == "completed"
        assert per_binding[0]["message"] == "actual run completed"
        assert "task_status_counts" not in per_binding[0]

    @pytest.mark.asyncio
    async def test_director_binding_fanout_ignores_workspace_taskboard_counts_for_terminal_state(
        self,
        tmp_path: Path,
    ) -> None:
        class _FanoutService:
            def __init__(self) -> None:
                self.queries = 0

            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-stuck", status="running", message="submitted")

            async def query_run_status(self, run_id: str) -> CommandResult:
                self.queries += 1
                return CommandResult(
                    run_id=run_id,
                    status="running",
                    message="Run status: running",
                    metadata={
                        "task_status_counts": {
                            "completed": 0,
                            "failed": 0,
                            "pending": 1,
                            "ready": 0,
                            "in_progress": 0,
                            "blocked": 0,
                        }
                    },
                )

        class _TaskboardProbeExecutor(OrchestrationStageExecutor):
            def _read_taskboard_stats(self) -> dict[str, int]:
                return {
                    "total": 3,
                    "pending": 0,
                    "ready": 0,
                    "in_progress": 0,
                    "completed": 1,
                    "failed": 2,
                    "blocked": 0,
                }

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, initial_result, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                await asyncio.sleep(0.05)
                return CommandResult(run_id="run-stuck", status="completed", message="actual run completed")

        service = _FanoutService()
        executor = _TaskboardProbeExecutor(tmp_path)
        executor._binding_status_probe_seconds = 0.01

        result = await asyncio.wait_for(
            executor._execute_director_binding_fanout(
                service=service,
                workspace=str(tmp_path),
                tasks=["TASK-1", "TASK-2", "TASK-3"],
                base_options={"execution_mode": "parallel", "max_workers": 2},
                bindings=[{"provider_id": "p1", "model": "m1", "binding_id": "b1"}],
                timeout_seconds=60,
                authority_port=_characterization_authority_port(),
            ),
            timeout=1.0,
        )

        assert service.queries == 0
        assert result.status == "completed"
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert per_binding[0]["status"] == "completed"
        assert per_binding[0]["message"] == "actual run completed"
        assert "task_status_counts" not in per_binding[0]

    @pytest.mark.asyncio
    async def test_director_binding_fanout_counts_newly_quarantined_timeouts(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("KERNELONE_FACTORY_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT", "2")

        class _FanoutService:
            counter = 0

            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                self.counter += 1
                return CommandResult(run_id=f"run-timeout-{self.counter}", status="running", message="submitted")

        class _TimeoutExecutor(OrchestrationStageExecutor):
            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                return CommandResult(run_id=initial_result.run_id, status="timeout", message="timed out")

        service = _FanoutService()
        executor = _TimeoutExecutor(tmp_path)
        binding = {"provider_id": "p1", "model": "m1", "binding_id": "b1"}

        await executor._execute_director_binding_fanout(
            service=service,
            workspace=str(tmp_path),
            tasks=["TASK-1"],
            base_options={"execution_mode": "parallel", "max_workers": 1},
            bindings=[binding],
            timeout_seconds=10,
            authority_port=_characterization_authority_port(),
        )
        result = await executor._execute_director_binding_fanout(
            service=service,
            workspace=str(tmp_path),
            tasks=["TASK-1"],
            base_options={"execution_mode": "parallel", "max_workers": 1},
            bindings=[binding],
            timeout_seconds=10,
            authority_port=_characterization_authority_port(),
        )

        assert result.status == "failed"
        assert "1 quarantined" in result.message
        assert (result.metadata or {})["quarantined_binding_count"] == 1
        assert (result.metadata or {})["quarantined_skipped_count"] == 0
        per_binding = (result.metadata or {})["per_binding"]
        assert per_binding[0]["status"] == "timeout"
        assert per_binding[0]["quarantined"] is True
        assert per_binding[0]["timeout_count"] == 2

    @pytest.mark.asyncio
    async def test_director_binding_fanout_soft_timeout_preserves_submitted_run(
        self,
        tmp_path: Path,
    ) -> None:
        class _FanoutService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-soft-timeout", status="running", message="submitted")

        class _SoftTimeoutExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.cancel_on_timeout_values: list[bool] = []

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker
                self.cancel_on_timeout_values.append(cancel_on_timeout)
                return CommandResult(
                    run_id=initial_result.run_id,
                    status="timeout",
                    message="soft timed out",
                    metadata={
                        "cancel_signal_sent": bool(cancel_on_timeout),
                        "cancel_reason": "factory_stage_timeout",
                        "inflight_run_continues": not cancel_on_timeout,
                    },
                )

        executor = _SoftTimeoutExecutor(tmp_path)
        result = await executor._execute_director_binding_fanout(
            service=_FanoutService(),
            workspace=str(tmp_path),
            tasks=["TASK-1"],
            base_options={"execution_mode": "parallel", "max_workers": 1},
            bindings=[{"provider_id": "p1", "model": "m1", "binding_id": "b1"}],
            timeout_seconds=10,
            authority_port=_characterization_authority_port(),
        )

        assert executor.cancel_on_timeout_values == [False]
        assert result.status == "failed"
        per_binding = (result.metadata or {})["per_binding"]
        assert per_binding[0]["status"] == "timeout"
        assert per_binding[0]["cancel_signal_sent"] is False
        assert per_binding[0]["inflight_run_continues"] is True

    @pytest.mark.asyncio
    async def test_dispatch_passes_pm_plan_task_ids_to_director_fanout(self, tmp_path: Path) -> None:
        class _CaptureTasksExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.captured_tasks: list[str] | None = None
                self.stats = [
                    {
                        "total": 2,
                        "pending": 2,
                        "ready": 2,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 2,
                        "pending": 2,
                        "ready": 2,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 2,
                        "failed": 0,
                        "blocked": 0,
                    },
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [
                    {"provider_id": "p1", "model": "m1"},
                    {"provider_id": "p2", "model": "m2"},
                ]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            def _canonical_factory_projection(
                self,
                _run: FactoryRun,
                _context: dict[str, Any],
            ) -> dict[str, Any]:
                return _with_task_runtime_authority(
                    {
                        "source": "run_ledger",
                        "task_boundary": {
                            "latest_by_task": {
                                task_id: {
                                    "task_id": task_id,
                                    "status": "completed_verified",
                                    "ok": True,
                                }
                                for task_id in ("TASK-1", "TASK-2")
                            }
                        },
                    },
                    task_ids=("TASK-1", "TASK-2"),
                )

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                tasks = kwargs.get("tasks")
                self.captured_tasks = list(tasks) if isinstance(tasks, list) else None
                return CommandResult(
                    run_id="director-capture",
                    status="completed",
                    message="Run status: completed",
                    metadata={"task_status_counts": {"completed": 2}},
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _CaptureTasksExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["package.json"]},
            {"id": "TASK-2", "target_files": ["src/index.ts"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-capture-tasks",
            config=FactoryConfig(name="capture-tasks"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 2}
            ),
        )

        assert result.status == "success"
        assert executor.captured_tasks == ["TASK-1", "TASK-2"]

    @pytest.mark.asyncio
    async def test_continues_after_partial_fanout_failure_when_taskboard_progresses(self, tmp_path: Path) -> None:
        executor = _PartialFailureProgressExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["package.json"]},
            {"id": "TASK-2", "target_files": ["src/index.ts"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-progress",
            config=FactoryConfig(name="progress"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 3, "timeout": 1, "execution_mode": "parallel", "max_workers": 2}
            ),
        )

        assert result.status == "success"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert len(payload["attempts"]) == 2
        assert payload["taskboard"]["converged"] is True
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.partial_failure_progress_continued" in codes
        assert "director.run_status_non_success" not in codes

    @pytest.mark.asyncio
    async def test_fails_when_all_director_bindings_fail_even_if_taskboard_converges(self, tmp_path: Path) -> None:
        class _AllBindingsFailedExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 2,
                        "pending": 2,
                        "ready": 2,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 2,
                        "pending": 2,
                        "ready": 2,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 2,
                        "blocked": 0,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 2,
                        "blocked": 0,
                    },
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [
                    {"provider_id": "p1", "model": "m1"},
                    {"provider_id": "p2", "model": "m2"},
                ]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(
                    run_id="director-all-failed",
                    status="failed",
                    message="Director binding fanout: 2 bindings, 0 succeeded, 2 failed",
                    metadata={
                        "binding_fanout": True,
                        "active_binding_count": 2,
                        "per_binding": [
                            {"provider_id": "p1", "model": "m1", "run_id": "r1", "status": "failed"},
                            {"provider_id": "p2", "model": "m2", "run_id": "r2", "status": "failed"},
                        ],
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _AllBindingsFailedExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["package.json"]},
            {"id": "TASK-2", "target_files": ["src/index.ts"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-all-bindings-failed",
            config=FactoryConfig(name="all-bindings-failed"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 2}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["error_code"] == "director.canonical_task_boundary_missing"
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.canonical_task_boundary_missing" in codes
        assert "director.dispatch_converged_after_partial_failure" in codes

    @pytest.mark.asyncio
    async def test_materialization_quality_failure_with_artifacts_stays_failed(self, tmp_path: Path) -> None:
        class _MaterializationQualityHandoffExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 3,
                        "pending": 3,
                        "ready": 3,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 3,
                        "pending": 3,
                        "ready": 3,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 3,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 1,
                        "failed": 2,
                        "blocked": 0,
                    },
                    {
                        "total": 3,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 1,
                        "failed": 2,
                        "blocked": 0,
                    },
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [{"provider_id": "p-live", "model": "m-live", "binding_id": "b-live"}]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(
                    run_id="director-quality-failed",
                    status="failed",
                    message=(
                        "Director binding fanout: 3 bindings, 0 succeeded, 1 failed, 0 quarantined, 2 readiness-skipped"
                    ),
                    metadata={
                        "binding_fanout": True,
                        "active_binding_count": 1,
                        "readiness_skipped_count": 2,
                        "per_binding": [
                            {
                                "provider_id": "p-live",
                                "model": "m-live",
                                "binding_id": "b-live",
                                "run_id": "director-quality-failed",
                                "status": "failed",
                                "message": (
                                    "Run status: failed | failed_task=task-2-director "
                                    "| error=director_materialization_quality_failed"
                                ),
                                "task_status_counts": {"completed": 1, "failed": 2},
                            },
                            {
                                "provider_id": "p-dead",
                                "model": "m-dead",
                                "binding_id": "b-dead",
                                "run_id": "",
                                "status": "skipped",
                                "skipped": True,
                                "skip_reason": "provider_connectivity_unavailable",
                            },
                        ],
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        (tmp_path / "src").mkdir()
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc"},"devDependencies":{"typescript":"latest"}}',
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")

        executor = _MaterializationQualityHandoffExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["package.json", "src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-quality-handoff",
            config=FactoryConfig(name="quality-handoff"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-22T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 1}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["quality_gate_handoff"] is False
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["error_code"] == "director.canonical_task_boundary_missing"
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff" not in codes
        assert "director.canonical_task_boundary_missing" in codes

    @pytest.mark.asyncio
    async def test_single_binding_materialization_failure_stops_before_no_claim_retry(
        self,
        tmp_path: Path,
    ) -> None:
        class _SingleBindingQualityHandoffExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 3,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 2,
                    },
                    {
                        "total": 3,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 2,
                    },
                    {
                        "total": 3,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 1,
                        "blocked": 2,
                    },
                    {
                        "total": 3,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 1,
                        "blocked": 2,
                    },
                ]
                self.execute_calls = 0

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                return ["TASK-1"] if self.execute_calls == 0 else []

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                executor = self

                class _Service:
                    async def execute_director_run(self, **kwargs: object) -> CommandResult:
                        del kwargs
                        executor.execute_calls += 1
                        return CommandResult(run_id="director-quality-single", status="running", message="submitted")

                return _Service()

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                return CommandResult(
                    run_id=initial_result.run_id,
                    status="failed",
                    message=(
                        "Run status: failed | failed_task=task-0-director "
                        "| error=director_materialization_quality_failed"
                    ),
                    metadata={},
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc"},"devDependencies":{"typescript":"latest"}}',
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")
        (tmp_path / "src" / "engine.ts").write_text("export const engine = true;\n", encoding="utf-8")
        (tmp_path / "tests" / "verify.test.ts").write_text("import '../src/index';\n", encoding="utf-8")

        executor = _SingleBindingQualityHandoffExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["package.json", "src/index.ts"]},
            {"id": "TASK-2", "target_files": ["src/engine.ts"], "depends_on": ["TASK-1"]},
            {"id": "TASK-3", "target_files": ["tests/verify.test.ts"], "depends_on": ["TASK-2"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        executor._write_json_artifact(
            "tasks/task_1.json",
            {
                "id": "TASK-1",
                "status": "failed",
                "metadata": {
                    "last_execution_error": "director_materialization_quality_failed",
                    "adapter_result": {
                        "materialization_error": "director_materialization_quality_failed",
                        "materialization_mode": "write_tool_and_workspace_diff",
                    },
                },
            },
        )
        run = FactoryRun(
            id="factory-single-quality-handoff",
            config=FactoryConfig(name="quality-handoff"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-23T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 3, "timeout": 1, "execution_mode": "serial", "max_workers": 1}
            ),
        )

        assert result.status == "failed"
        assert executor.execute_calls == 1
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["attempts"]
        assert payload["attempts"][0]["run_id"] == "director-quality-single"
        assert payload["quality_gate_handoff"] is False
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["error_code"] == "director.canonical_task_boundary_missing"
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff_ready" not in codes
        assert "director.materialization_quality_handoff" not in codes
        assert "director.canonical_task_boundary_missing" in codes

    @pytest.mark.asyncio
    async def test_idle_blocked_materialization_quality_failure_with_missing_targets_stays_failed(
        self,
        tmp_path: Path,
    ) -> None:
        class _BlockedQualityFailureExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 2,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "waiting_human": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 1,
                    },
                    {
                        "total": 2,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "waiting_human": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 1,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "waiting_human": 0,
                        "completed": 0,
                        "failed": 1,
                        "blocked": 1,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "waiting_human": 0,
                        "completed": 0,
                        "failed": 1,
                        "blocked": 1,
                    },
                ]
                self.execute_calls = 0

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                return ["TASK-1"] if self.execute_calls == 0 else []

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                executor = self

                class _Service:
                    async def execute_director_run(self, **kwargs: object) -> CommandResult:
                        del kwargs
                        executor.execute_calls += 1
                        return CommandResult(run_id="director-blocked-quality", status="running", message="submitted")

                return _Service()

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                return CommandResult(
                    run_id=initial_result.run_id,
                    status="failed",
                    message=(
                        "Run status: failed | failed_task=task-0-director "
                        "| error=director_materialization_quality_failed"
                    ),
                    metadata={
                        "failed_task_count": 1,
                        "failed_tasks": [
                            {
                                "task_id": "task-0-director",
                                "error_message": "director_materialization_quality_failed",
                            }
                        ],
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        (tmp_path / "src").mkdir()
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc"},"devDependencies":{"typescript":"latest"}}',
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")

        executor = _BlockedQualityFailureExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["package.json", "src/index.ts"]},
            {"id": "TASK-2", "target_files": ["tests/verify.test.ts"], "depends_on": ["TASK-1"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-blocked-quality-handoff",
            config=FactoryConfig(name="blocked-quality-handoff"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-23T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 1, "execution_mode": "serial", "max_workers": 1}
            ),
        )

        assert result.status == "failed"
        assert executor.execute_calls == 1
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["quality_gate_handoff"] is False
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["taskboard"]["converged"] is False
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff_ready" not in codes
        assert "director.materialization_quality_handoff" not in codes
        assert "director.canonical_task_boundary_missing" in codes

    @pytest.mark.asyncio
    async def test_no_claimable_tasks_after_attempt_does_not_replay_requested_pm_tasks(self, tmp_path: Path) -> None:
        class _NoClaimableAfterProgressExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 2,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 1,
                    },
                    {
                        "total": 2,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 1,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 1,
                        "failed": 0,
                        "blocked": 1,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 1,
                        "failed": 0,
                        "blocked": 1,
                    },
                ]
                self.execute_calls = 0
                self.captured_tasks: list[list[str]] = []

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                return ["TASK-1"] if self.execute_calls == 0 else []

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                executor = self

                class _Service:
                    async def execute_director_run(self, **kwargs: object) -> CommandResult:
                        tasks = kwargs.get("tasks")
                        if isinstance(tasks, list):
                            executor.captured_tasks.append([str(item) for item in tasks])
                        executor.execute_calls += 1
                        return CommandResult(
                            run_id=f"director-{executor.execute_calls}", status="running", message="ok"
                        )

                return _Service()

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                return CommandResult(
                    run_id=initial_result.run_id,
                    status="completed",
                    message="Run status: completed",
                    metadata={"task_status_counts": {"completed": 1}},
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _NoClaimableAfterProgressExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["src/one.rs"]},
            {"id": "TASK-2", "target_files": ["src/two.rs"], "depends_on": ["TASK-1"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-no-claimable-after-progress",
            config=FactoryConfig(name="no-claimable-after-progress"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-23T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 3, "timeout": 1, "execution_mode": "serial", "max_workers": 1}
            ),
        )

        assert result.status == "failed"
        assert executor.execute_calls == 1
        assert executor.captured_tasks == [["TASK-1"]]
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.no_claimable_tasks_after_progress" in codes
        assert "director.taskboard_not_converged" in codes
        assert "director.run_status_non_success" not in codes

    @pytest.mark.asyncio
    async def test_no_claimable_followup_settlement_consumes_previous_execution_lease(
        self,
        tmp_path: Path,
    ) -> None:
        """An active child keeps the unused prior execution lease, not only 5s settle."""

        class _ActiveChildAfterLifecycleFailureExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 1,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 1,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 1,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 1,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 1,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 1,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 1,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 1,
                        "failed": 0,
                        "blocked": 0,
                    },
                ]
                self.execute_calls = 0
                self.settlement_grace_seconds: list[int] = []

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                return ["TASK-1"] if self.execute_calls == 0 else []

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                executor = self

                class _Service:
                    async def execute_director_run(self, **_kwargs: object) -> CommandResult:
                        executor.execute_calls += 1
                        return CommandResult(run_id="director-active-child", status="running", message="submitted")

                return _Service()

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                return CommandResult(
                    run_id=initial_result.run_id,
                    status="failed",
                    message="orchestration lifecycle ended before TaskRuntime child",
                )

            async def _settle_inflight_director_run_after_timeout(
                self,
                service: Any,
                *,
                run_id: str,
                grace_seconds: int,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
            ) -> CommandResult:
                del service, cancel_event, abort_checker
                self.settlement_grace_seconds.append(grace_seconds)
                return CommandResult(
                    run_id=run_id,
                    status="completed",
                    message="TaskRuntime child settled inside carried execution lease",
                    metadata={"canonical_authoritative": True},
                )

            def _active_director_execution_progress_marker(
                self,
                *,
                run_id: str,
            ) -> tuple[tuple[str, str, str, str], ...]:
                assert run_id == "director-active-child"
                return (("TASK-1", "7", "heartbeat-7", "in_progress"),)

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _ActiveChildAfterLifecycleFailureExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["src/main.rs"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-carry-previous-director-lease",
            config=FactoryConfig(name="carry-previous-director-lease"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-07-27T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {
                    "director_max_rounds": 2,
                    "director_dispatch_timeout_seconds": 60,
                    "director_first_materialization_min_budget_seconds": 10,
                    "director_timeout_settle_grace_seconds": 5,
                    "execution_mode": "serial",
                    "max_workers": 1,
                }
            ),
        )

        assert executor.execute_calls == 1
        assert len(executor.settlement_grace_seconds) == 1
        assert 55 <= executor.settlement_grace_seconds[0] <= 60

    def test_taskboard_active_execution_is_authoritative_when_lifecycle_marker_lags(self) -> None:
        assert OrchestrationStageExecutor._taskboard_has_active_execution(
            {"in_progress": 1, "completed": 2, "blocked": 1}
        )
        assert OrchestrationStageExecutor._taskboard_has_active_execution(
            {"in_execution": 1, "completed": 2}
        )
        assert not OrchestrationStageExecutor._taskboard_has_active_execution(
            {"in_progress": 0, "in_execution": 0, "completed": 3, "blocked": 1}
        )

    @pytest.mark.asyncio
    async def test_missing_write_receipt_with_artifacts_stays_failed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _MissingWriteReceiptHandoffExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 3,
                        "pending": 3,
                        "ready": 3,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 3,
                        "pending": 3,
                        "ready": 3,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 3,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 3,
                        "blocked": 0,
                    },
                    {
                        "total": 3,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 3,
                        "blocked": 0,
                    },
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [
                    {"provider_id": "p1", "model": "qwen-q6-a", "binding_id": "b1"},
                    {"provider_id": "p2", "model": "qwen-q6-b", "binding_id": "b2"},
                ]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(
                    run_id="director-receipt-failed",
                    status="failed",
                    message="Director binding fanout: 2 bindings, 0 succeeded, 2 failed",
                    metadata={
                        "binding_fanout": True,
                        "active_binding_count": 2,
                        "per_binding": [
                            {
                                "provider_id": "p1",
                                "model": "qwen-q6-a",
                                "binding_id": "b1",
                                "run_id": "r1",
                                "status": "failed",
                                "message": "Run status: failed | failed_task=task-1",
                            },
                            {
                                "provider_id": "p2",
                                "model": "qwen-q6-b",
                                "binding_id": "b2",
                                "run_id": "r2",
                                "status": "failed",
                                "message": "Run status: failed | failed_task=task-2",
                            },
                        ],
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        (tmp_path / "src").mkdir()
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc"},"devDependencies":{"typescript":"latest"}}',
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")

        executor = _MissingWriteReceiptHandoffExecutor(tmp_path)
        monkeypatch.setattr(
            TaskRuntimeService,
            "query_observable_task_rows_projection",
            lambda runtime: _authoritative_task_projection(
                Path(runtime.workspace),
                (
                    {
                        "id": 1,
                        "status": "pending",
                        "metadata": {"external_task_id": "TASK-1"},
                    },
                ),
            ),
        )
        tasks = [
            {
                "id": "TASK-1",
                "target_files": ["package.json", "src/index.ts"],
            }
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        executor._write_json_artifact(
            "tasks/task_1.json",
            {
                "id": "TASK-1",
                "status": "failed",
                "last_execution_error": "director_missing_write_receipt",
                "metadata": {
                    "adapter_result": {
                        "materialization_mode": "workspace_diff_without_write_tool",
                        "new_files": ["package.json", "src/index.ts"],
                    }
                },
            },
        )
        executor._write_json_artifact(
            "tasks/task_2.json",
            {
                "id": "TASK-2",
                "status": "failed",
                "metadata": {
                    "adapter_result": {
                        "materialization_mode": "no_materialized_changes",
                        "materialization_error": "director_no_materialized_changes",
                    }
                },
            },
        )
        run = FactoryRun(
            id="factory-receipt-handoff",
            config=FactoryConfig(name="receipt-handoff"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-23T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                *tasks,
                {
                    "id": "TASK-2",
                    "target_files": ["package.json", "src/index.ts"],
                },
            ],
        )

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 2}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["quality_gate_handoff"] is False
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["error_code"] == "director.canonical_task_boundary_missing"
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff" not in codes
        assert "director.canonical_task_boundary_missing" in codes

    @pytest.mark.asyncio
    async def test_idle_claimable_unresolved_artifacts_do_not_enter_quality_gate_handoff(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _IdleUnresolvedHandoffExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 4,
                        "pending": 4,
                        "ready": 4,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "running": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 4,
                        "pending": 4,
                        "ready": 4,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "running": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 4,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "running": 0,
                        "completed": 1,
                        "failed": 2,
                        "blocked": 0,
                    },
                    {
                        "total": 4,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "running": 0,
                        "completed": 1,
                        "failed": 2,
                        "blocked": 0,
                    },
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [
                    {"provider_id": "p1", "model": "qwen-q6-a", "binding_id": "b1"},
                    {"provider_id": "p2", "model": "qwen-q6-b", "binding_id": "b2"},
                ]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(
                    run_id="director-idle-unresolved",
                    status="failed",
                    message="Director binding fanout: 2 bindings, 0 succeeded, 2 failed",
                    metadata={
                        "binding_fanout": True,
                        "active_binding_count": 2,
                        "per_binding": [
                            {
                                "provider_id": "p1",
                                "model": "qwen-q6-a",
                                "binding_id": "b1",
                                "run_id": "r1",
                                "status": "cancelled",
                                "message": "Run cancelled: factory-bench event wait timeout after 2400s",
                            },
                            {
                                "provider_id": "p2",
                                "model": "qwen-q6-b",
                                "binding_id": "b2",
                                "run_id": "r2",
                                "status": "cancelled",
                                "message": "Run cancelled: factory-bench event wait timeout after 2400s",
                            },
                        ],
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        (tmp_path / "src").mkdir()
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc"},"devDependencies":{"typescript":"latest"}}',
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")

        executor = _IdleUnresolvedHandoffExecutor(tmp_path)
        monkeypatch.setattr(
            TaskRuntimeService,
            "query_observable_task_rows_projection",
            lambda runtime: _authoritative_task_projection(
                Path(runtime.workspace),
                (
                    {
                        "id": 1,
                        "status": "pending",
                        "metadata": {"external_task_id": "TASK-1"},
                    },
                ),
            ),
        )
        tasks = [
            {
                "id": "TASK-1",
                "target_files": ["package.json", "src/index.ts", "README.md"],
            }
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        executor._write_json_artifact(
            "tasks/task_1.json",
            {
                "id": "TASK-1",
                "status": "failed",
                "metadata": {
                    "adapter_result": {
                        "materialization_mode": "workspace_diff_without_write_tool",
                        "materialization_error": "director_missing_write_receipt",
                    }
                },
            },
        )
        executor._write_json_artifact(
            "tasks/task_2.json",
            {
                "id": "TASK-2",
                "status": "failed",
                "metadata": {
                    "runtime_execution": {"last_error": "director_materialization_quality_failed"},
                    "adapter_result": {
                        "materialization_error": "director_materialization_quality_failed",
                    },
                },
            },
        )
        run = FactoryRun(
            id="factory-idle-unresolved-handoff",
            config=FactoryConfig(name="idle-unresolved-handoff"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-23T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                *tasks,
                {
                    "id": "TASK-2",
                    "target_files": ["package.json", "src/index.ts"],
                },
            ],
        )

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 2}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["quality_gate_handoff"] is False
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["error_code"] == "director.canonical_task_boundary_missing"
        assert payload["taskboard"]["converged"] is False
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff" not in codes
        assert "director.taskboard_unresolved_quality_handoff" not in codes
        assert "director.canonical_task_boundary_missing" in codes

    @pytest.mark.asyncio
    async def test_fails_when_taskboard_not_converged_after_max_rounds(self, tmp_path: Path) -> None:
        """第一轮有进展但最终未收敛仍失败。"""

        class _NoConvergenceProgressExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.results = [
                    CommandResult(
                        run_id="director-round-1",
                        status="completed",
                        message="Run status: completed",
                        metadata={
                            "binding_fanout": True,
                            "per_binding": [
                                {"provider_id": "p1", "model": "m1", "run_id": "r1", "status": "completed"},
                                {"provider_id": "p2", "model": "m2", "run_id": "r2", "status": "completed"},
                            ],
                        },
                    ),
                    CommandResult(
                        run_id="director-round-2",
                        status="completed",
                        message="Run status: completed",
                        metadata={
                            "binding_fanout": True,
                            "per_binding": [
                                {"provider_id": "p1", "model": "m1", "run_id": "r3", "status": "completed"},
                                {"provider_id": "p2", "model": "m2", "run_id": "r4", "status": "completed"},
                            ],
                        },
                    ),
                ]
                # 第一轮后 pending 从 2 降到 1，第二轮后保持不变
                self.stats = [
                    {"total": 2, "pending": 2, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                    {"total": 2, "pending": 2, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                    {"total": 2, "pending": 1, "ready": 0, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 0},
                    {"total": 2, "pending": 1, "ready": 0, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 0},
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [
                    {"provider_id": "p1", "model": "m1"},
                    {"provider_id": "p2", "model": "m2"},
                ]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                return self.results.pop(0)

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _NoConvergenceProgressExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["package.json"]},
            {"id": "TASK-2", "target_files": ["src/index.ts"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-no-convergence",
            config=FactoryConfig(name="no-convergence"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 2, "timeout": 1, "execution_mode": "parallel", "max_workers": 2}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert len(payload["attempts"]) == 2
        assert payload["taskboard"]["converged"] is False
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.taskboard_not_converged" in codes
        assert "director.canonical_task_boundary_missing" in codes

    @pytest.mark.asyncio
    async def test_dynamic_director_rounds_cover_blocked_taskboard_total(self, tmp_path: Path) -> None:
        class _BlockedUnrollExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.rounds = 0
                self.stats = [
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 4},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 4},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 3},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 3},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 2, "failed": 0, "blocked": 2},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 2, "failed": 0, "blocked": 2},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 3, "failed": 0, "blocked": 1},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 3, "failed": 0, "blocked": 1},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 4, "failed": 0, "blocked": 0},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 4, "failed": 0, "blocked": 0},
                    {"total": 5, "pending": 0, "ready": 0, "in_progress": 0, "completed": 5, "failed": 0, "blocked": 0},
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [{"provider_id": "p1", "model": "m1"}]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            def _canonical_factory_projection(
                self,
                _run: FactoryRun,
                _context: dict[str, Any],
            ) -> dict[str, Any]:
                completed = self.rounds >= 5
                task_ids = tuple(f"TASK-{index}" for index in range(1, 6))
                return _with_task_runtime_authority(
                    {
                        "source": "run_ledger",
                        "task_boundary": {
                            "latest_by_task": {
                                f"TASK-{index}": {
                                    "task_id": f"TASK-{index}",
                                    "status": "completed_verified" if completed else "in_execution",
                                    "ok": completed,
                                }
                                for index in range(1, 6)
                            }
                        },
                    },
                    task_ids=task_ids,
                    incomplete_task_ids=() if completed else task_ids,
                )

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                self.rounds += 1
                return CommandResult(
                    run_id=f"director-round-{self.rounds}",
                    status="completed",
                    message="Run status: completed",
                    metadata={"task_status_counts": {"completed": self.rounds}},
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _BlockedUnrollExecutor(tmp_path)
        tasks = [{"id": f"TASK-{idx}", "target_files": [f"src/{idx}.rs"]} for idx in range(1, 6)]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-blocked-unroll",
            config=FactoryConfig(name="blocked-unroll"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-24T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context({"timeout": 1, "execution_mode": "parallel", "max_workers": 1}),
        )

        assert result.status == "success"
        assert executor.rounds == 5
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["taskboard"]["converged"] is True

    @pytest.mark.asyncio
    async def test_timeout_produces_terminal_status_with_diagnostic(self, tmp_path: Path) -> None:
        """超时应产生终端失败状态和明确的超时诊断信号。"""

        class _MockService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-1", status="timeout", message="Run timed out after 1 seconds")

            async def query_run_status(self, run_id: str) -> CommandResult:
                del run_id
                return CommandResult(run_id="run-1", status="timeout", message="Run timed out after 1 seconds")

        class _TimeoutExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                    {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return _MockService()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return []

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-1", status="timeout", message="Run timed out after 1 seconds")

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _TimeoutExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-timeout",
            config=FactoryConfig(name="timeout"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 1}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.dispatch_timeout" in codes
        assert payload.get("error_code") == "director.dispatch_timeout"
        assert "timed out" in (payload.get("root_cause_hint") or "").lower()

    @pytest.mark.asyncio
    async def test_timeout_after_workspace_delta_keeps_delta_diagnostic_only(self, tmp_path: Path) -> None:
        class _MockService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-1", status="running", message="submitted")

        class _DeltaTimeoutExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                    {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                    {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return _MockService()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return []

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, initial_result, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                (self.workspace / "src").mkdir(parents=True, exist_ok=True)
                (self.workspace / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")
                return CommandResult(run_id="run-1", status="timeout", message="Run timed out after 1 seconds")

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _DeltaTimeoutExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-timeout-delta",
            config=FactoryConfig(name="timeout-delta"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 1}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.workspace_delta_progress_detected" in codes
        assert "director.dispatch_timeout" in codes
        assert payload["attempts"][0]["progress_made"] is False
        assert payload["attempts"][0]["workspace_delta_progress"] is True
        assert payload["attempts"][0]["workspace_delta"]["added_sample"] == ["src/index.ts"]

    @pytest.mark.asyncio
    async def test_timeout_with_inflight_task_settles_late_director_success(self, tmp_path: Path) -> None:
        """A Director run that finishes during timeout grace should not leave TaskBoard partial."""

        class _MockService:
            def __init__(self, executor: _LateSuccessExecutor) -> None:
                self.executor = executor

            async def query_run_status(self, run_id: str) -> CommandResult:
                self.executor.taskboard_state = {
                    "total": 1,
                    "pending": 0,
                    "ready": 0,
                    "in_progress": 0,
                    "completed": 1,
                    "failed": 0,
                    "blocked": 0,
                }
                return CommandResult(
                    run_id=run_id,
                    status="completed",
                    message="Director completed 1/1 tasks",
                    metadata={"task_status_counts": dict(self.executor.taskboard_state)},
                )

        class _LateSuccessExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.claim_count = 0
                self.taskboard_state = {
                    "total": 1,
                    "pending": 1,
                    "ready": 1,
                    "in_progress": 0,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return _MockService(self)

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [{"binding_id": "director:test", "provider_id": "test", "model": "test"}]

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                self.claim_count += 1
                return ["TASK-1"] if self.claim_count == 1 else []

            def _read_taskboard_stats(self) -> dict[str, int]:
                return dict(self.taskboard_state)

            def _canonical_factory_projection(
                self,
                _run: FactoryRun,
                _context: dict[str, Any],
            ) -> dict[str, Any]:
                completed = int(self.taskboard_state.get("completed") or 0) == 1
                return _with_task_runtime_authority(
                    {
                        "source": "run_ledger",
                        "task_boundary": {
                            "latest_by_task": {
                                "TASK-1": {
                                    "task_id": "TASK-1",
                                    "status": "completed_verified" if completed else "in_execution",
                                    "ok": completed,
                                }
                            }
                        },
                    },
                    incomplete_task_ids=() if completed else ("TASK-1",),
                )

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                self.taskboard_state = {
                    "total": 1,
                    "pending": 0,
                    "ready": 0,
                    "in_progress": 1,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }
                return CommandResult(
                    run_id="director-late-success",
                    status="timeout",
                    message="Run timed out after 1 seconds",
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _LateSuccessExecutor(tmp_path)

        class _CommittedOutcomeWaiter:
            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                del process_terminal
                if int(executor.taskboard_state.get("completed") or 0) != 1:
                    return None
                return CommandResult(
                    run_id=run_id,
                    status="completed",
                    message="committed outcome visible",
                    metadata={
                        "canonical_authoritative": True,
                        "fact_event_seq": 35,
                    },
                )

            def active_execution_progress_marker(
                self,
                *,
                run_id: str,
            ) -> tuple[tuple[str, str, str, str], ...]:
                del run_id
                return ()

            async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
                del run_id, reason

        executor._run_completion_waiter = _CommittedOutcomeWaiter()  # type: ignore[assignment]
        tasks = [{"id": "TASK-1", "target_files": ["src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-late-success",
            config=FactoryConfig(name="late-success"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {
                    "director_max_rounds": 2,
                    "timeout": 2,
                    "execution_mode": "parallel",
                    "max_workers": 1,
                    "director_dispatch_timeout_settle_grace_seconds": 1,
                }
            ),
        )

        assert result.status == "success"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.inflight_timeout_settled" in codes
        assert "director.taskboard_not_converged" not in codes
        assert payload["attempts"][-1]["settled_after_timeout"] is True
        assert payload["taskboard"]["converged"] is True

    @pytest.mark.asyncio
    async def test_soft_timeout_settles_before_another_director_round(self, tmp_path: Path) -> None:
        submitted_deadlines: list[float] = []

        class _BarrierService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                options = kwargs.get("options")
                assert isinstance(options, dict)
                metadata = options.get("metadata")
                assert isinstance(metadata, dict)
                submitted_deadlines.append(float(metadata["factory_director_execution_deadline_epoch_seconds"]))
                return CommandResult(run_id="director-inflight", status="running", message="submitted")

        class _BarrierExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.execute_calls = 0
                self.claim_calls = 0
                self.settle_calls = 0
                self.execution_timeout_seconds = 0
                self.settlement_timeout_seconds = 0
                self.taskboard_state = {
                    "total": 1,
                    "pending": 1,
                    "ready": 1,
                    "in_progress": 0,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }

            def _build_orchestration_service(self, context: dict[str, Any]) -> _BarrierService:
                del context
                return _BarrierService()

            def _resolve_director_binding_fanout(
                self,
                context: dict[str, Any] | None = None,
            ) -> list[dict[str, str]]:
                del context
                return []

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                self.claim_calls += 1
                if self.claim_calls > 1:
                    raise AssertionError("a second Director round started before the inflight child settled")
                return ["TASK-1"]

            def _read_taskboard_stats(self) -> dict[str, int]:
                return dict(self.taskboard_state)

            def _canonical_factory_projection(
                self,
                _run: FactoryRun,
                _context: dict[str, Any],
            ) -> dict[str, Any]:
                completed = int(self.taskboard_state.get("completed") or 0) == 1
                return _with_task_runtime_authority(
                    {
                        "source": "run_ledger",
                        "task_boundary": {
                            "latest_by_task": {
                                "TASK-1": {
                                    "task_id": "TASK-1",
                                    "status": "completed_verified" if completed else "in_execution",
                                    "ok": completed,
                                }
                            }
                        },
                    },
                    incomplete_task_ids=() if completed else ("TASK-1",),
                )

            async def _wait_run_completion(self, *args: object, **kwargs: object) -> CommandResult:
                del args
                self.execute_calls += 1
                timeout_seconds = kwargs["timeout_seconds"]
                assert isinstance(timeout_seconds, int)
                self.execution_timeout_seconds = timeout_seconds
                self.taskboard_state.update({"pending": 0, "ready": 0, "in_progress": 1})
                return CommandResult(
                    run_id="director-inflight",
                    status="timeout",
                    message="soft timeout",
                    metadata={
                        "cancel_signal_sent": False,
                        "cancel_reason": "factory_stage_timeout",
                        "inflight_run_continues": True,
                    },
                )

            async def _settle_inflight_director_run_after_timeout(
                self, *args: object, **kwargs: object
            ) -> CommandResult:
                del args
                self.settle_calls += 1
                grace_seconds = kwargs["grace_seconds"]
                assert isinstance(grace_seconds, int)
                self.settlement_timeout_seconds = grace_seconds
                self.taskboard_state.update({"in_progress": 0, "completed": 1})
                target = self.workspace / "src/index.ts"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("export const ready = true;\n", encoding="utf-8")
                return CommandResult(
                    run_id="director-inflight",
                    status="completed",
                    message="settled",
                    metadata={
                        "canonical_authoritative": True,
                        "fact_event_seq": 36,
                        "task_status_counts": dict(self.taskboard_state),
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _BarrierExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-immediate-barrier",
            config=FactoryConfig(name="immediate-barrier"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-07-10T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {
                    "director_max_rounds": 2,
                    "execution_mode": "serial",
                    "max_workers": 1,
                    "director_dispatch_timeout_settle_grace_seconds": 5,
                }
            ),
        )

        assert result.status == "success"
        assert executor.execute_calls == 1
        assert executor.settle_calls == 1
        assert executor.claim_calls == 1
        assert len(submitted_deadlines) == 1
        assert submitted_deadlines[0] > time.time()
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["attempts"][0]["settlement_attempted"] is True
        assert payload["attempts"][0]["settled_after_timeout"] is True
        assert executor.execution_timeout_seconds > 0
        # An early lifecycle timeout does not prove that the admitted execution
        # budget was consumed.  The parent barrier must therefore spend the
        # remaining execution lease before its configured five-second
        # settlement reserve; otherwise Factory can close the stage authority
        # while the Director child is only just reaching Provider transport.
        assert executor.settlement_timeout_seconds > 5
        assert executor.execution_timeout_seconds <= payload["attempts"][0]["execution_timeout_seconds"]
        assert (
            payload["attempts"][0]["execution_timeout_seconds"] + payload["attempts"][0]["settlement_timeout_seconds"]
            == payload["attempts"][0]["timeout_seconds"]
        )
        assert payload["attempts"][0]["settlement_timeout_seconds"] == 5
        assert executor.settlement_timeout_seconds <= payload["attempts"][0]["timeout_seconds"]
        assert "director.inflight_timeout_settled" in {str(item.get("code") or "") for item in payload["signals"]}

    @pytest.mark.asyncio
    async def test_soft_timeout_barrier_expiry_fails_without_replaying_director(self, tmp_path: Path) -> None:
        class _BarrierService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="director-inflight", status="running", message="submitted")

        class _BarrierTimeoutExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.execute_calls = 0
                self.taskboard_state = {
                    "total": 1,
                    "pending": 1,
                    "ready": 1,
                    "in_progress": 0,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }

            def _build_orchestration_service(self, context: dict[str, Any]) -> _BarrierService:
                del context
                return _BarrierService()

            def _resolve_director_binding_fanout(
                self,
                context: dict[str, Any] | None = None,
            ) -> list[dict[str, str]]:
                del context
                return []

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                return ["TASK-1"]

            def _read_taskboard_stats(self) -> dict[str, int]:
                return dict(self.taskboard_state)

            async def _wait_run_completion(self, *args: object, **kwargs: object) -> CommandResult:
                del args, kwargs
                self.execute_calls += 1
                if self.execute_calls > 1:
                    raise AssertionError("barrier timeout must not replay the Director")
                self.taskboard_state.update({"pending": 0, "ready": 0, "in_progress": 1})
                return CommandResult(
                    run_id="director-inflight",
                    status="timeout",
                    message="soft timeout",
                    metadata={"inflight_run_continues": True, "cancel_signal_sent": False},
                )

            async def _settle_inflight_director_run_after_timeout(
                self, *args: object, **kwargs: object
            ) -> CommandResult:
                del args, kwargs
                return CommandResult(
                    run_id="director-inflight",
                    status="timeout",
                    message="barrier timeout",
                    metadata={
                        "inflight_run_continues": True,
                        "cancel_signal_sent": False,
                        "barrier_state": "timeout",
                        "barrier_timeout": True,
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _BarrierTimeoutExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-barrier-timeout",
            config=FactoryConfig(name="barrier-timeout"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-07-10T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {
                    "director_max_rounds": 2,
                    "execution_mode": "serial",
                    "max_workers": 1,
                    "director_dispatch_timeout_settle_grace_seconds": 5,
                }
            ),
        )

        assert result.status == "failed"
        assert executor.execute_calls == 1
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        codes = {str(item.get("code") or "") for item in payload["signals"]}
        assert "director.execution_barrier_timeout" in codes
        assert "director.taskboard_not_converged" not in codes
        assert payload["attempts"][0]["settlement_attempted"] is True
        assert payload["attempts"][0]["settled_after_timeout"] is False


class TestPmMetaDiagnostic:
    def test_is_pm_meta_diagnostic_task_true(self) -> None:
        task = {"title": "x", "goal": "多个任务标题/goal 重复", "description": ""}
        assert OrchestrationStageExecutor._is_pm_meta_diagnostic_task(task) is True

    def test_is_pm_meta_diagnostic_task_false(self) -> None:
        task = {"title": "实现登录", "goal": "完成登录功能", "description": "登录"}
        assert OrchestrationStageExecutor._is_pm_meta_diagnostic_task(task) is False


class TestTaskFieldAccessors:
    def test_task_string_first_nonempty(self) -> None:
        executor = _executor(Path("."))
        assert executor._task_string({"a": "", "b": "val"}, "a", "b") == "val"

    def test_task_string_numeric_coercion(self) -> None:
        executor = _executor(Path("."))
        assert executor._task_string({"n": 5}, "n") == "5"

    def test_task_string_list_flattens(self) -> None:
        executor = _executor(Path("."))
        assert executor._task_string_list({"k": ["a", "", "b"], "j": "c"}, "k", "j") == ["a", "b", "c"]

    def test_task_id_fallback(self) -> None:
        executor = _executor(Path("."))
        assert executor._task_id({}, 3) == "task-3"
        assert executor._task_id({"id": "X"}, 3) == "X"

    def test_task_objective_fallback(self) -> None:
        executor = _executor(Path("."))
        assert executor._task_objective({}) == "Prepare Director implementation blueprint"
        assert executor._task_objective({"goal": "G"}) == "G"

    def test_build_director_task_filter(self) -> None:
        executor = _executor(Path("."))
        assert executor._build_director_task_filter([]) == "Execute ready tasks from PM contract"
        result = executor._build_director_task_filter([{"title": "T1", "scope": "src/"}])
        assert "Execute PM tasks strictly in order:" in result
        assert "- T1 [scope: src/]" in result


class TestExistingTargetFileSummaries:
    """Cross-file coherence: a later task must see the API of files it depends on.

    Regression (factory-bench L1-03): TASK-1 created src/models/mood.py defining
    ``Mood`` as an enum; TASK-2 wrote src/main.py and — without the dependency
    signature — guessed ``Mood(mood=..., intensity=...)``, crashing entrypoint
    smoke with ``EnumType.__call__() got an unexpected keyword argument 'mood'``.
    The injection must surface the dependency file's signature, NOT just the
    task's own (not-yet-written) targets.
    """

    def test_dependency_file_signature_is_injected_for_later_task(self, tmp_path: Path) -> None:
        # TASK-1 already wrote the model (a dependency of TASK-2).
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "models" / "mood.py").write_text(
            "from enum import Enum\n\n\nclass Mood(Enum):\n    SUNNY = 'sunny'\n    CALM = 'calm'\n\n\n"
            "def derive_mood(weather):\n    return Mood.CALM\n",
            encoding="utf-8",
        )
        executor = _executor(tmp_path)

        # TASK-2 owns main.py (which does NOT exist yet) and depends on mood.py.
        task2 = {"target_files": ["src/main.py"]}
        summaries = executor._read_existing_target_file_summaries(task2)

        by_path = {s["path"]: s["exports"] for s in summaries}
        # The dependency file's real signature must be present even though it is
        # not one of TASK-2's own target_files.
        assert "src/models/mood.py" in by_path
        assert "class Mood(Enum):" in by_path["src/models/mood.py"]
        assert "def derive_mood" in by_path["src/models/mood.py"]
        # main.py is the task's own target and does not exist yet → not summarized.
        assert "src/main.py" not in by_path

    def test_runtime_and_dotpolaris_paths_are_excluded(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "core.py").write_text("def core():\n    return 1\n", encoding="utf-8")
        # Noise that must never enter Director context.
        (tmp_path / ".polaris" / "history").mkdir(parents=True)
        (tmp_path / ".polaris" / "history" / "leak.py").write_text("def leak():\n    return 1\n", encoding="utf-8")
        (tmp_path / "runtime").mkdir()
        (tmp_path / "runtime" / "noise.py").write_text("def noise():\n    return 1\n", encoding="utf-8")
        executor = _executor(tmp_path)

        summaries = executor._read_existing_target_file_summaries({"target_files": ["src/main.py"]})
        paths = {s["path"] for s in summaries}
        assert "src/core.py" in paths
        assert not any(".polaris" in p or "runtime/" in p for p in paths)

    def test_no_existing_files_returns_empty(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        assert executor._read_existing_target_file_summaries({"target_files": ["src/main.py"]}) == []


# ---------------------------------------------------------------------------
# WS4 typed-quality-issue seam regression guards
# ---------------------------------------------------------------------------


def test_workspace_quality_repair_issue_payloads_preserves_scanner_typed_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = "typed scanner diagnostic for src/main.py"
    typed_issue = {
        "code": "syntax_error",
        "message": raw,
        "path": "src/main.py",
        "severity": "error",
        "source": "source_syntax_checker",
        "metadata": {
            "raw": raw,
            "diagnostic_kind": "syntax_error",
            "scanner_owned": True,
        },
    }

    def fake_scan_workspace_artifact_quality_evidence(workspace: str) -> SimpleNamespace:
        assert workspace == str(tmp_path)
        return SimpleNamespace(errors=(raw,), issues=(typed_issue,))

    monkeypatch.setattr(
        "polaris.kernelone.quality.scan_workspace_artifact_quality_evidence",
        fake_scan_workspace_artifact_quality_evidence,
    )

    payloads = _executor(tmp_path)._workspace_quality_repair_issue_payloads([raw])

    assert len(payloads) == 1
    assert payloads[0]["code"] == "syntax_error"
    assert payloads[0]["path"] == "src/main.py"
    assert payloads[0]["source"] == "source_syntax_checker"
    assert payloads[0]["metadata"]["diagnostic_kind"] == "syntax_error"
    assert payloads[0]["metadata"]["scanner_owned"] is True


def test_workspace_quality_repair_issue_payloads_falls_back_to_string_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw = "Artifact quality scan failed: workspace path does not exist"

    def broken_scan_workspace_artifact_quality_evidence(workspace: str) -> SimpleNamespace:
        assert workspace == str(tmp_path)
        raise OSError("scanner unavailable")

    monkeypatch.setattr(
        "polaris.kernelone.quality.scan_workspace_artifact_quality_evidence",
        broken_scan_workspace_artifact_quality_evidence,
    )

    payloads = _executor(tmp_path)._workspace_quality_repair_issue_payloads([raw])

    assert len(payloads) == 1
    assert payloads[0]["message"] == raw.removeprefix("Artifact quality scan failed:").strip()
    assert payloads[0]["metadata"]["raw"] == raw


def test_chief_engineer_portfolio_context_includes_local_rework_failure_feedback(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    failure_feedback = {
        "schema_version": "factory.chief_engineer_local_rework.v1",
        "cycle": 1,
        "stage_output": "project_completion_contract.obligations is required",
        "preserved_pm_contract": True,
    }

    context = executor._chief_engineer_portfolio_context(
        [
            {
                "id": "TASK-1",
                "goal": "Build a runnable CLI",
                "target_files": ["src/main.py"],
                "scope_paths": ["src/main.py"],
                "acceptance_criteria": ["python src/main.py exits 0"],
                "steps": ["Implement the entrypoint"],
            }
        ],
        run_id="factory-local-ce-rework",
        failure_feedback=failure_feedback,
    )

    assert context["chief_engineer_local_rework"] is True
    assert context["failure_feedback"] == failure_feedback
    assert context["failure_feedback"] is not failure_feedback
