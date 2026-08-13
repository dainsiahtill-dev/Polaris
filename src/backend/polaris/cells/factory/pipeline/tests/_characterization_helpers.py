"""Shared helpers for the OrchestrationStageExecutor characterization suite.

Extracted from the historical monolithic characterization test file so each per-domain
module can import these pure helpers. Tests remain characterization tests: they freeze
*current* observed behavior of the executor helper clusters before the god-class is
decomposed into sibling collaborators.
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
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
    derive_project_kind_authority_from_catalog_snapshot,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    TaskBlueprintResultV1,
    _issue_chief_engineer_portfolio_authority_carrier,
)
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


__all__ = [
    "_characterization_authority_port",
    "_factory_stage_context",
    "_bootstrap_fact_stream_workspace",
    "_executor",
    "_library_completion_requirements",
    "_write_minimal_chief_engineer_plan",
    "_single_task_chief_engineer_result",
    "_invalid_chief_engineer_stream_result",
    "_thinking_only_chief_engineer_result",
    "_invalid_structured_transport_chief_engineer_result",
    "_capture_chief_engineer_lease_keepers",
    "_assert_no_chief_engineer_lease_keeper_threads",
    "_authoritative_task_projection",
    "_with_task_runtime_authority",
    "_factory_workspace_run_lease",
    "_write_review_for_blueprint",
    "_write_handoff_ready_review_for_tasks",
    "_generate_domain_blueprint",
    "_verified_delivery_recovery_authority",
]


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
        "pm_task_contracts": projection.get("pm_task_contracts")
        or [{"id": task_id, "task_id": task_id} for task_id in task_ids],
        "task_runtime_projection": {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
            "owner_scope": "pm_contract_tasks",
            "owned_task_ids": list(task_ids),
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
    run_id = f"characterization-{task_id.lower()}"
    project_id = f"project-{task_id.lower()}"
    portfolio_task = ChiefEngineerPortfolioTaskV1(
        task_id=task_id,
        objective=objective,
        target_files=tuple(target_files),
        scope_paths=tuple(target_files),
    )
    catalog_snapshot = {"project_kind": "library"}
    catalog_path = workspace / ".polaris" / "catalog_contract.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog_snapshot), encoding="utf-8")
    verifier_policy_hash = "b" * 64
    verifier_policy_snapshot = {
        "schema_version": "evidence_policy.v1",
        "source": "control_plane.verifier_policy.evidence_policy_compiler",
        "policy_hash": verifier_policy_hash,
        "required_evidence_modalities": ["command"],
    }
    build_authority = VerificationCommandAuthorityV1(
        task_id=task_id,
        modality="build",
        argv=("python", "-m", "compileall", "."),
    )
    artifact_obligations = [
        {
            "obligation_id": f"artifact-{index}",
            "path": path,
            "semantic_role": "source",
            "applicability": "required",
            "owner_task_id": task_id,
        }
        for index, path in enumerate(target_files, start=1)
    ]
    portfolio = build_chief_engineer_blueprint_portfolio(
        BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(workspace),
            run_id=run_id,
            tasks=(portfolio_task,),
            authority_carrier=_issue_chief_engineer_portfolio_authority_carrier(
                workspace=str(workspace),
                run_id=run_id,
                project_id=project_id,
                pm_stage_event_id=f"pm-stage-{run_id}",
                pm_contract_hash="a" * 64,
                tasks=(portfolio_task,),
                catalog_snapshot=catalog_snapshot,
                catalog_snapshot_hash=project_completion_catalog_snapshot_hash(catalog_snapshot),
                verifier_policy_hash=verifier_policy_hash,
                verifier_policy_snapshot=verifier_policy_snapshot,
                verifier_policy_snapshot_hash=project_completion_verifier_policy_snapshot_hash(
                    verifier_policy_snapshot
                ),
                verification_command_authority=(build_authority,),
            ),
            llm_blueprint={
                "construction_plan": {
                    "implementation": list(execution_checklist),
                    "project_interface_contract": {
                        "provider_declarations": [],
                        "consumer_declarations": [],
                    },
                },
                "scope_for_apply": list(target_files),
                "risk_flags": [],
                "project_completion_contract": {
                    "obligations": {
                        "artifacts": [
                            *artifact_obligations,
                            {
                                "obligation_id": "artifact-test-na",
                                "path": "tests",
                                "semantic_role": "test",
                                "applicability": "not_applicable",
                                "owner_task_id": None,
                            },
                        ],
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
                                "covers_obligation_ids": [item["obligation_id"] for item in artifact_obligations],
                                "owner_task_id": task_id,
                            },
                            {
                                "obligation_id": "verify-test-na",
                                "modality": "test",
                                "command_authority_hash": None,
                                "applicability": "not_applicable",
                                "covers_obligation_ids": [],
                                "owner_task_id": None,
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
                    }
                },
            },
        )
    )
    return generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id=task_id,
            workspace=str(workspace),
            objective=objective,
            run_id=run_id,
            context={
                "task_title": objective,
                "target_files": target_files,
                "scope_paths": target_files,
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
                "pm_task_contract": {
                    "id": task_id,
                    "goal": objective,
                    "target_files": target_files,
                    "scope_paths": target_files,
                    "acceptance_criteria": acceptance_criteria,
                    "execution_checklist": execution_checklist,
                },
                **portfolio.to_task_blueprint_context(),
            },
            llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task_id),
        )
    )


# ---------------------------------------------------------------------------
# Pure text-shaping helpers
# ---------------------------------------------------------------------------


def _verified_delivery_recovery_authority(
    *,
    quality_authorized: bool = True,
) -> stage_executor_module.helpers.CanonicalFactoryAuthority:
    return stage_executor_module.helpers.CanonicalFactoryAuthority(
        source_valid=True,
        task_runtime_projection_authoritative=True,
        contract_task_scope_valid=True,
        task_runtime_converged=False,
        terminal_runtime_delivery_recovered=True,
        task_boundary_present=True,
        task_boundary_completed_verified=True,
        qa_verdict_present=True,
        qa_verdict_passed=quality_authorized,
        sequence_barrier_satisfied=True,
        evidence_policy_passed=True,
        projection_passed=True,
        reason_code="canonical_projection_authorized",
        detail="verified delivery recovery test",
        failure_class="",
        responsible_layer="",
        task_count=1,
        expected_task_ids=("TASK-3",),
        missing_runtime_task_ids=(),
        unexpected_runtime_task_ids=(),
        incomplete_task_ids=("TASK-3",),
        incomplete_runtime_task_ids=("TASK-3",),
        missing_task_boundary_ids=(),
        recovered_runtime_task_ids=("TASK-3",),
    )
