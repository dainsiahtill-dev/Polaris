"""Unit tests for `chief_engineer/blueprint` public contracts."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import polaris.cells.chief_engineer.blueprint.public.service as blueprint_service_module
import pytest
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    HandoffDecisionV1,
    QueryBlueprintProvenanceV1,
    RegisterRiskCommandV1,
    RegisterTechDebtCommandV1,
    RiskRecordV1,
    RiskSeverity,
    RiskStatus,
    TaskBlueprintGeneratedEventV1,
    TaskBlueprintProvenanceSnapshotV1,
    TaskBlueprintResultV1,
)
from polaris.cells.chief_engineer.blueprint.public.service import (
    BlueprintPersistence,
    build_ce_handoff_decision,
    build_chief_engineer_blueprint_portfolio,
    generate_task_blueprint,
    get_blueprint_status,
    project_chief_engineer_task_blueprint,
    query_blueprint_provenance,
)
from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.kernelone.quality.file_ownership_ledger import (
    FileOwnershipLedgerError,
    read_file_owners,
)
from polaris.kernelone.storage import resolve_storage_roots


def _producer_v1_hashable(value):
    if isinstance(value, dict):
        return {
            str(key): _producer_v1_hashable(item)
            for key, item in value.items()
            if str(key) not in {"blueprint_hash", "capability_token", "job_token"}
        }
    if isinstance(value, (list, tuple)):
        return [_producer_v1_hashable(item) for item in value]
    return value


def _pm_task_payload() -> dict:
    return {
        "id": "TASK-1",
        "goal": "Implement the declared task contract.",
        "target_files": ["src/main.py", "tests/test_main.py"],
    }


def _blueprint_provenance_payload() -> dict:
    pm_task = _pm_task_payload()
    payload = {
        "schema_version": "chief_engineer.blueprint.v1",
        "blueprint_id": "ce_TASK-1_20260718010101000000",
        "task_id": "TASK-1",
        "run_id": "factory-run-1",
        "summary": "Implement the declared task contract.",
        "pm_task": pm_task,
        "pm_contract_hash": stable_hash(_producer_v1_hashable(pm_task)),
        "target_files": ["src/main.py", "tests/test_main.py"],
        "context": {
            "capability_token": {"token_id": "cap-1"},
            "nested": [{"job_token": {"token_id": "job-1"}, "kept": "yes"}],
        },
    }
    payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))
    return payload


def _blueprint_provenance_query(payload: dict | None = None) -> QueryBlueprintProvenanceV1:
    blueprint = payload or _blueprint_provenance_payload()
    blueprint_id = "ce_TASK-1_20260718010101000000"
    return QueryBlueprintProvenanceV1(
        blueprint=blueprint,
        expected_pm_task=_pm_task_payload(),
        expected_factory_run_id="factory-run-1",
        expected_task_id="TASK-1",
        expected_blueprint_id=blueprint_id,
        expected_logical_path=f"runtime/blueprints/{blueprint_id}.json",
    )


def _blueprint_provenance_snapshot_kwargs() -> dict:
    payload = _blueprint_provenance_payload()
    pm_contract_hash = stable_hash(_producer_v1_hashable(_pm_task_payload()))
    return {
        "logical_path": f"runtime/blueprints/{payload['blueprint_id']}.json",
        "factory_run_id": "factory-run-1",
        "task_id": "TASK-1",
        "blueprint_id": payload["blueprint_id"],
        "embedded_blueprint_hash": payload["blueprint_hash"],
        "recomputed_blueprint_hash": payload["blueprint_hash"],
        "matches": True,
        "pm_contract_hash": pm_contract_hash,
        "recomputed_pm_contract_hash": pm_contract_hash,
        "pm_task_canonical_hash": stable_hash(_pm_task_payload()),
        "target_files": ("src/main.py", "tests/test_main.py"),
    }


class TestGovernanceEnumFailClosed:
    """Tier-1 governance contracts must fail-closed on invalid enum input."""

    def test_invalid_severity_string_raises(self) -> None:
        with pytest.raises(ValueError):
            RiskRecordV1(
                risk_id="r1",
                task_id="t1",
                title="t",
                severity="apocalyptic",  # type: ignore[arg-type]
                owner="ce",
                mitigation="m",
                status=RiskStatus.OPEN,
                detected_at="2026-06-17T00:00:00Z",
            )

    def test_empty_severity_string_raises(self) -> None:
        # Fail-closed: an empty severity must NOT silently default to medium.
        with pytest.raises(ValueError):
            RegisterRiskCommandV1(
                task_id="t1",
                title="t",
                severity="",  # type: ignore[arg-type]
                owner="ce",
                mitigation="m",
                workspace="/repo",
            )

    def test_invalid_tech_debt_severity_raises(self) -> None:
        with pytest.raises(ValueError):
            RegisterTechDebtCommandV1(
                title="t",
                description="d",
                severity="nuclear",  # type: ignore[arg-type]
                surface="s",
                owner="ce",
                workspace="/repo",
            )

    def test_valid_severity_enum_passes(self) -> None:
        record = RiskRecordV1(
            risk_id="r1",
            task_id="t1",
            title="t",
            severity=RiskSeverity.BLOCKER,
            owner="ce",
            mitigation="m",
            status=RiskStatus.OPEN,
            detected_at="2026-06-17T00:00:00Z",
        )
        assert record.severity is RiskSeverity.BLOCKER


class TestHandoffDecisionContract:
    """Director-handoff decision contract invariants."""

    def test_negative_count_raises(self) -> None:
        with pytest.raises(ValueError):
            HandoffDecisionV1(
                allowed=False,
                blueprint_id="ce_x",
                blocker_count=-1,
                warning_count=0,
                open_blocker_risk_count=0,
            )

    def test_empty_blueprint_id_raises(self) -> None:
        with pytest.raises(ValueError):
            HandoffDecisionV1(
                allowed=True,
                blueprint_id="",
                blocker_count=0,
                warning_count=0,
                open_blocker_risk_count=0,
            )

    def test_to_dict_round_trips(self) -> None:
        decision = HandoffDecisionV1(
            allowed=False,
            blueprint_id="ce_x",
            blocker_count=2,
            warning_count=1,
            open_blocker_risk_count=1,
            task_id="t1",
            blockers=("a", "b"),
            reason="2 quality-gate blocker(s)",
            evaluated_at="2026-06-17T00:00:00Z",
        )
        data = decision.to_dict()
        assert data["allowed"] is False
        assert data["blocker_count"] == 2
        assert data["blockers"] == ["a", "b"]
        assert data["open_blocker_risk_count"] == 1

    def test_strict_ce_handoff_decision_binds_required_hashes(self, tmp_path) -> None:
        base_decision = HandoffDecisionV1(
            allowed=True,
            blueprint_id="ce_TASK-1",
            task_id="TASK-1",
            blocker_count=0,
            warning_count=0,
            open_blocker_risk_count=0,
            reason="handoff allowed",
            evaluated_at="2026-06-27T00:00:00Z",
        )
        blueprint = {
            "task_id": "TASK-1",
            "blueprint_id": "ce_TASK-1",
            "pm_contract_ref": "tasks/plan.json",
            "pm_contract_hash": "pm-contract-hash",
            "blueprint_ref": "runtime/blueprints/ce_TASK-1.json",
            "blueprint_hash": "blueprint-hash",
            "execution_profile_ref": "runtime/contracts/profile.json",
            "execution_profile_hash": "execution-profile-hash",
        }

        decision = build_ce_handoff_decision(
            str(tmp_path),
            blueprint=blueprint,
            blueprint_id="ce_TASK-1",
            task_id="TASK-1",
            base_decision=base_decision,
        )

        payload = decision.to_dict()
        assert decision.allowed is True
        assert payload["schema_version"] == "polaris.ce_handoff_decision.v1"
        assert payload["policy_version"] == "chief_engineer.handoff.v1"
        assert payload["bindings"] == {
            "pm_contract_ref": "tasks/plan.json",
            "pm_contract_hash": "pm-contract-hash",
            "blueprint_ref": "runtime/blueprints/ce_TASK-1.json",
            "blueprint_hash": "blueprint-hash",
            "execution_profile_ref": "runtime/contracts/profile.json",
            "execution_profile_hash": "execution-profile-hash",
        }
        assert payload["decision_hash"]

    def test_strict_ce_handoff_decision_fails_closed_without_required_bindings(self, tmp_path) -> None:
        base_decision = HandoffDecisionV1(
            allowed=True,
            blueprint_id="ce_TASK-2",
            task_id="TASK-2",
            blocker_count=0,
            warning_count=0,
            open_blocker_risk_count=0,
            reason="base gate allowed",
            evaluated_at="2026-06-27T00:00:00Z",
        )

        decision = build_ce_handoff_decision(
            str(tmp_path),
            blueprint={"task_id": "TASK-2", "blueprint_id": "ce_TASK-2"},
            blueprint_id="ce_TASK-2",
            task_id="TASK-2",
            base_decision=base_decision,
        )

        assert decision.allowed is False
        assert "missing required handoff binding: pm_contract_hash" in decision.blockers
        assert "missing required handoff binding: execution_profile_hash" in decision.blockers


class TestChiefEngineerBlueprintPortfolio:
    """Project-level CE advice must remain task-scoped and non-authoritative."""

    @staticmethod
    def _tasks() -> tuple[ChiefEngineerPortfolioTaskV1, ...]:
        return (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Build the shared provider and task A adapter",
                target_files=("src/shared.py", "src/a.py"),
                scope_paths=("src/a",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Build the task B consumer",
                target_files=("src/shared.py", "src/b.py"),
                scope_paths=("src/b",),
                dependencies=("TASK-A",),
            ),
        )

    def test_builds_shared_task_overlays_and_interface_bindings(self, tmp_path: Path) -> None:
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-portfolio-1",
            tasks=self._tasks(),
            llm_blueprint={
                "construction_plan": {
                    "preparation": ["Inspect the shared interface"],
                    "nested": {"shared": True, "owner": "shared"},
                    "files": [
                        "src/shared.py",
                        "src/a.py",
                        "src/b.py",
                        "../plan_escape.py",
                    ],
                    "project_interface_contract": {
                        "provider_declarations": [{"task_id": "TASK-A", "interface": "SharedProvider"}],
                        "consumer_declarations": [{"task_id": "TASK-B", "interface": "SharedProvider"}],
                    },
                    "task_plans": {
                        "TASK-A": {
                            "implementation": ["Implement provider adapter"],
                            "nested": {"owner": "task-a"},
                            "scope_for_apply": [
                                "src/a.py",
                                "../scope_escape.py",
                                "/tmp/absolute.py",
                            ],
                            "risk_flags": [
                                {
                                    "severity": "medium",
                                    "title": "Task A ordering",
                                    "mitigation": "Serialize updates",
                                }
                            ],
                        },
                        "TASK-B": {
                            "implementation": ["Implement consumer adapter"],
                            "scope_for_apply": ["src/b.py"],
                        },
                    },
                },
                "scope_for_apply": [
                    "src/shared.py",
                    "src/a.py",
                    "src/b.py",
                    "src/outside.py",
                ],
                "risk_flags": [
                    {
                        "severity": "HIGH",
                        "title": "Shared API drift",
                        "mitigation": "Pin interface",
                    }
                ],
            },
        )

        portfolio = build_chief_engineer_blueprint_portfolio(command)

        assert portfolio.llm_blueprint_consumed is True
        assert portfolio.usage_mode == "advisory_overlay"
        assert portfolio.authority == "advisory_only"
        assert portfolio.handoff_ready is False
        assert portfolio.execution_authorized is False
        assert portfolio.task_ids == ("TASK-A", "TASK-B")
        interface = portfolio.project_interface_contract
        assert interface.task_file_ownership == {
            "TASK-A": ("src/shared.py", "src/a.py"),
            "TASK-B": ("src/shared.py", "src/b.py"),
        }
        assert interface.file_task_ownership["src/shared.py"] == ("TASK-A", "TASK-B")
        assert interface.provider_declarations == ({"task_id": "TASK-A", "interface": "SharedProvider"},)
        assert interface.consumer_declarations == ({"task_id": "TASK-B", "interface": "SharedProvider"},)
        assert portfolio.project_interface_contract_hash == interface.contract_hash
        assert portfolio.project_interface_contract_ref == interface.contract_ref

        for task_id in portfolio.task_ids:
            overlay = portfolio.task_overlays[task_id]
            assert overlay["portfolio_hash"] == portfolio.portfolio_hash
            assert overlay["project_interface_contract_hash"] == interface.contract_hash
            assert overlay["project_interface_contract_ref"] == interface.contract_ref
            assert overlay["reference"]["portfolio_hash"] == portfolio.portfolio_hash
            assert overlay["reference"]["project_interface_contract_hash"] == interface.contract_hash
            assert overlay["handoff_ready"] is False
            assert overlay["execution_authorized"] is False

        task_a = project_chief_engineer_task_blueprint(portfolio, "TASK-A")
        assert set(task_a) == {"construction_plan", "scope_for_apply", "risk_flags"}
        assert "task_plans" not in task_a["construction_plan"]
        assert task_a["construction_plan"]["preparation"] == ["Inspect the shared interface"]
        assert task_a["construction_plan"]["implementation"] == ["Implement provider adapter"]
        assert task_a["construction_plan"]["nested"] == {"shared": True, "owner": "task-a"}
        assert task_a["scope_for_apply"] == ["src/shared.py", "src/a.py"]
        assert task_a["risk_flags"] == [
            "[high] Shared API drift (mitigation: Pin interface)",
            "[medium] Task A ordering (mitigation: Serialize updates)",
        ]

        task_a_advisory = portfolio.scope_advisory["TASK-A"]
        rejected_paths = {item["path"] for item in task_a_advisory["rejected_suggestions"]}
        assert rejected_paths >= {
            "src/b.py",
            "src/outside.py",
            "../scope_escape.py",
            "/tmp/absolute.py",
        }
        assert task_a_advisory["construction_plan_paths_outside_pm_authority"] == ["src/b.py"]
        assert task_a_advisory["construction_plan_rejected_paths"] == [
            {
                "path": "../plan_escape.py",
                "reason": "parent_traversal_not_allowed",
                "source": "construction_plan",
            }
        ]

        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(portfolio.portfolio_id)
        assert persisted == portfolio.to_dict()
        assert persisted["project_interface_contract_hash"] == interface.contract_hash

    def test_generate_task_blueprint_projects_portfolio_evidence_to_top_level(
        self,
        tmp_path: Path,
    ) -> None:
        task = ChiefEngineerPortfolioTaskV1(
            task_id="TASK-PROJECTION",
            objective="Build project interface audit projection",
            target_files=("src/interface_projection.py",),
            scope_paths=("src/interface_projection.py",),
        )
        portfolio = build_chief_engineer_blueprint_portfolio(
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace=str(tmp_path),
                run_id="run-projection",
                tasks=(task,),
                llm_blueprint={
                    "construction_plan": {
                        "implementation": ["Project shared interface evidence"],
                        "project_interface_contract": {
                            "provider_declarations": [],
                            "consumer_declarations": [],
                        },
                    },
                    "scope_for_apply": ["src/interface_projection.py"],
                    "risk_flags": [],
                },
            )
        )
        interface_payload = portfolio.project_interface_contract.to_dict()
        context = {
            "task_title": "Project interface audit projection",
            "target_files": ["src/interface_projection.py"],
            "scope_paths": ["src/interface_projection.py"],
            "acceptance_criteria": ["Portfolio evidence is directly auditable"],
            "execution_checklist": ["Persist top-level portfolio evidence"],
            "delivery_plan_document": {
                "schema_version": "polaris.delivery_plan_document.v1",
                "title": "Project interface audit projection",
                "user_journey": ["Generate portfolio", "Inspect task blueprint evidence"],
            },
            "delivery_depth_contract": {
                "schema_version": "polaris.delivery_depth_contract.v1",
                "behavior_contract": {"rule_matrix": ["Portfolio interface evidence remains directly auditable"]},
            },
            "task": task.to_dict(),
            **portfolio.to_task_blueprint_context(),
        }

        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id=task.task_id,
                workspace=str(tmp_path),
                objective=task.objective,
                run_id="run-projection",
                context=context,
                llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id),
            )
        )

        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id or "")
        assert isinstance(persisted, dict)
        assert persisted["blueprint_portfolio_ref"] == portfolio.portfolio_path
        assert persisted["blueprint_portfolio_hash"] == portfolio.portfolio_hash
        assert persisted["project_interface_contract_ref"] == portfolio.project_interface_contract_ref
        assert persisted["project_interface_contract_hash"] == portfolio.project_interface_contract_hash
        assert persisted["project_interface_contract"] == interface_payload
        assert persisted["context"]["project_interface_contract"] == interface_payload
        assert persisted["target_files"] == ["src/interface_projection.py"]
        assert persisted["scope_paths"] == ["src/interface_projection.py"]
        assert "portfolio_path" not in persisted

    def test_task_contract_normalizes_duplicates_and_rejects_unsafe_paths(self) -> None:
        task = ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Build one module",
            target_files=("./src/module.py", "src/module.py"),
            scope_paths=("src", "src"),
            dependencies=("TASK-0", "TASK-0"),
        )

        assert task.target_files == ("src/module.py",)
        assert task.scope_paths == ("src",)
        assert task.dependencies == ("TASK-0",)

        with pytest.raises(ValueError, match="workspace-relative"):
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-ABS",
                objective="Reject absolute path",
                target_files=("/tmp/outside.py",),
            )
        with pytest.raises(ValueError, match="parent traversal"):
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-DOTDOT",
                objective="Reject traversal",
                target_files=("src/module.py",),
                scope_paths=("../outside",),
            )

    def test_command_rejects_duplicate_task_ids(self) -> None:
        task = ChiefEngineerPortfolioTaskV1(
            task_id="TASK-DUP",
            objective="Build duplicate detector",
            target_files=("src/duplicate.py",),
        )

        with pytest.raises(ValueError, match="duplicate task_id"):
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace="/repo",
                run_id="run-duplicate",
                tasks=(task, task),
            )

    def test_unknown_llm_task_plan_fails_closed(self, tmp_path: Path) -> None:
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-unknown-plan",
            tasks=self._tasks(),
            llm_blueprint={
                "construction_plan": {"task_plans": {"TASK-UNKNOWN": {"implementation": ["Do not run"]}}},
                "scope_for_apply": [],
                "risk_flags": [],
            },
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            build_chief_engineer_blueprint_portfolio(command)

        assert exc_info.value.code == "unknown_blueprint_portfolio_task_plan"
        assert exc_info.value.details["unknown_task_ids"] == ["TASK-UNKNOWN"]
        assert BlueprintPersistence(str(tmp_path), ensure_directory=False).list_all() == []

    def test_no_llm_portfolio_is_stable_offline_diagnostic_only(self, tmp_path: Path) -> None:
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-offline",
            tasks=(
                ChiefEngineerPortfolioTaskV1(
                    task_id="TASK-OFFLINE",
                    objective="Inspect a PM task without a CE LLM response",
                    target_files=("src/offline.py",),
                    scope_paths=("tests",),
                ),
            ),
        )

        first = build_chief_engineer_blueprint_portfolio(command)
        second = build_chief_engineer_blueprint_portfolio(command)

        assert second.portfolio_id == first.portfolio_id
        assert second.portfolio_hash == first.portfolio_hash
        assert second.reference == first.reference
        assert first.llm_blueprint_consumed is False
        assert first.usage_mode == "offline_diagnostic_only"
        assert first.handoff_ready is False
        assert first.execution_authorized is False
        assert first.project_interface_contract.provider_declarations == ()
        assert first.project_interface_contract.consumer_declarations == ()
        overlay = first.task_overlays["TASK-OFFLINE"]
        assert overlay["portfolio_hash"] == first.portfolio_hash
        assert overlay["project_interface_contract_hash"] == first.project_interface_contract_hash
        assert overlay["llm_blueprint_consumed"] is False
        assert overlay["usage_mode"] == "offline_diagnostic_only"
        assert overlay["handoff_ready"] is False
        assert overlay["execution_authorized"] is False

        pending_payload = first.to_dict()
        pending_payload["portfolio_hash"] = "pending"
        pending_payload["reference"]["portfolio_hash"] = "pending"
        for task_overlay in pending_payload["task_overlays"].values():
            task_overlay["portfolio_hash"] = "pending"
            task_overlay["reference"]["portfolio_hash"] = "pending"
        assert blueprint_service_module._portfolio_hash(pending_payload) == first.portfolio_hash
        assert blueprint_service_module._portfolio_hash(first.to_dict()) == first.portfolio_hash

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            project_chief_engineer_task_blueprint(first, "TASK-OFFLINE")
        assert exc_info.value.code == "blueprint_portfolio_offline_diagnostic_only"

        diagnostic = project_chief_engineer_task_blueprint(
            first,
            "TASK-OFFLINE",
            allow_offline_diagnostic=True,
        )
        assert diagnostic["construction_plan"]["diagnostic_only"] is True
        assert diagnostic["scope_for_apply"] == ["src/offline.py", "tests"]
        assert BlueprintPersistence(str(tmp_path), ensure_directory=False).list_all() == [first.portfolio_id]


class TestGenerateTaskBlueprintCommandV1HappyPath:
    def test_minimal(self) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="task-1",
            workspace="/repo",
            objective="Implement login",
        )
        assert cmd.task_id == "task-1"
        assert cmd.workspace == "/repo"
        assert cmd.objective == "Implement login"

    def test_full(self) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="task-1",
            workspace="/repo",
            objective="Implement login",
            run_id="run-1",
            constraints={"max_time": 300},
            context={"user": "alice"},
        )
        assert cmd.run_id == "run-1"
        assert cmd.constraints == {"max_time": 300}
        assert cmd.context == {"user": "alice"}

    def test_constraints_are_copied(self) -> None:
        original = {"max_time": 300}
        cmd = GenerateTaskBlueprintCommandV1(task_id="task-1", workspace="/repo", objective="x", constraints=original)
        original.clear()
        assert cmd.constraints == {"max_time": 300}


class TestGenerateTaskBlueprintCommandV1EdgeCases:
    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            GenerateTaskBlueprintCommandV1(task_id="", workspace="/r", objective="x")

    def test_whitespace_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            GenerateTaskBlueprintCommandV1(task_id="  ", workspace="/r", objective="x")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            GenerateTaskBlueprintCommandV1(task_id="t", workspace="", objective="x")

    def test_empty_objective_raises(self) -> None:
        with pytest.raises(ValueError, match="objective"):
            GenerateTaskBlueprintCommandV1(task_id="t", workspace="/r", objective="")


class TestGetBlueprintStatusQueryV1HappyPath:
    def test_minimal(self) -> None:
        q = GetBlueprintStatusQueryV1(task_id="task-1", workspace="/repo")
        assert q.task_id == "task-1"
        assert q.workspace == "/repo"
        assert q.run_id is None

    def test_with_run_id(self) -> None:
        q = GetBlueprintStatusQueryV1(task_id="task-1", workspace="/repo", run_id="run-1")
        assert q.run_id == "run-1"


class TestGetBlueprintStatusQueryV1EdgeCases:
    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            GetBlueprintStatusQueryV1(task_id="", workspace="/repo")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            GetBlueprintStatusQueryV1(task_id="task-1", workspace="")


class TestQueryBlueprintProvenanceV1:
    def test_valid_blueprint_returns_typed_snapshot(self) -> None:
        payload = _blueprint_provenance_payload()

        result = query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert isinstance(result, TaskBlueprintProvenanceSnapshotV1)
        assert result.schema_version == "chief_engineer.blueprint_provenance.v1"
        assert result.blueprint_schema_version == "chief_engineer.blueprint.v1"
        assert result.hash_scheme == "chief_engineer.blueprint_hash.v1"
        assert result.logical_path == f"runtime/blueprints/{payload['blueprint_id']}.json"
        assert result.factory_run_id == "factory-run-1"
        assert result.task_id == "TASK-1"
        assert result.blueprint_id == payload["blueprint_id"]
        assert result.embedded_blueprint_hash == payload["blueprint_hash"]
        assert result.recomputed_blueprint_hash == payload["blueprint_hash"]
        assert result.matches is True
        assert result.pm_contract_hash == stable_hash(_producer_v1_hashable(_pm_task_payload()))
        assert result.recomputed_pm_contract_hash == stable_hash(_producer_v1_hashable(_pm_task_payload()))
        assert result.pm_task_canonical_hash == stable_hash(_pm_task_payload())
        assert result.target_files == ("src/main.py", "tests/test_main.py")
        assert result.to_dict()["recomputed_pm_contract_hash"] == result.recomputed_pm_contract_hash

    def test_semantic_mutation_fails_closed(self) -> None:
        payload = _blueprint_provenance_payload()
        payload["summary"] = "mutated after producer hash"

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_hash_mismatch"

    def test_embedded_hash_mismatch_fails_closed(self) -> None:
        payload = _blueprint_provenance_payload()
        payload["blueprint_hash"] = "b" * 64

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_hash_mismatch"

    @pytest.mark.parametrize(
        ("field", "value", "error_code"),
        [
            ("run_id", "other-run", "blueprint_provenance_run_id_mismatch"),
            ("task_id", "TASK-2", "blueprint_provenance_task_id_mismatch"),
            ("blueprint_id", "ce_other", "blueprint_provenance_blueprint_id_mismatch"),
            ("schema_version", "chief_engineer.blueprint.v2", "blueprint_provenance_schema_mismatch"),
        ],
    )
    def test_payload_identity_or_schema_mismatch_fails_closed(
        self,
        field: str,
        value: str,
        error_code: str,
    ) -> None:
        payload = _blueprint_provenance_payload()
        payload[field] = value
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == error_code

    def test_noncanonical_logical_path_fails_closed(self) -> None:
        query = _blueprint_provenance_query()
        forged = QueryBlueprintProvenanceV1(
            blueprint=query.blueprint,
            expected_pm_task=query.expected_pm_task,
            expected_factory_run_id=query.expected_factory_run_id,
            expected_task_id=query.expected_task_id,
            expected_blueprint_id=query.expected_blueprint_id,
            expected_logical_path=f".polaris/blueprints/{query.expected_blueprint_id}.json",
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(forged)

        assert exc_info.value.code == "blueprint_provenance_logical_path_mismatch"

    def test_missing_pm_task_fails_closed_without_alias_fallback(self) -> None:
        payload = _blueprint_provenance_payload()
        payload.pop("pm_task")
        payload["context"]["pm_task_contract"] = _pm_task_payload()
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_pm_task_invalid"

    def test_pm_task_mismatch_fails_closed(self) -> None:
        payload = _blueprint_provenance_payload()
        payload["pm_task"]["goal"] = "mutated PM task"
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_pm_task_mismatch"

    def test_embedded_pm_contract_hash_mismatch_fails_closed(self) -> None:
        payload = _blueprint_provenance_payload()
        payload["pm_contract_hash"] = "b" * 64
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_pm_contract_hash_mismatch"

    def test_blueprint_target_files_must_exactly_match_expected_pm_targets(self) -> None:
        payload = _blueprint_provenance_payload()
        payload["target_files"] = ["src/other.py", "tests/test_main.py"]
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_target_files_mismatch"

    @pytest.mark.parametrize(
        "unsafe_path",
        [
            "/absolute.py",
            "src\\main.py",
            "src/\x00main.py",
            "",
            ".",
            "..",
            "src/./main.py",
            "src/../main.py",
            "src//main.py",
            "src/\x01main.py",
            "e\u0301.py",
            "a" * 1025,
        ],
    )
    def test_blueprint_target_files_reject_unsafe_paths(self, unsafe_path: str) -> None:
        payload = _blueprint_provenance_payload()
        payload["target_files"] = [unsafe_path]
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_target_files_invalid"

    @pytest.mark.parametrize("invalid_targets", [None, "src/main.py", ("src/main.py",)])
    def test_blueprint_target_files_reject_missing_or_non_list(self, invalid_targets: object) -> None:
        payload = _blueprint_provenance_payload()
        if invalid_targets is None:
            payload.pop("target_files")
        else:
            payload["target_files"] = invalid_targets
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_target_files_invalid"

    @pytest.mark.parametrize(
        "invalid_targets",
        [
            ["src/main.py", "src/main.py"],
            [f"src/file-{index}.py" for index in range(513)],
        ],
    )
    def test_blueprint_target_files_reject_duplicates_and_overflow(self, invalid_targets: list[str]) -> None:
        payload = _blueprint_provenance_payload()
        payload["target_files"] = invalid_targets
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_target_files_invalid"

    @pytest.mark.parametrize(
        "invalid_expected_targets",
        [None, "src/main.py", ("src/main.py",), ["src/../main.py"]],
    )
    def test_expected_pm_target_files_reject_missing_non_list_or_unsafe(
        self,
        invalid_expected_targets: object,
    ) -> None:
        expected_pm_task = _pm_task_payload()
        if invalid_expected_targets is None:
            expected_pm_task.pop("target_files")
        else:
            expected_pm_task["target_files"] = invalid_expected_targets
        valid_query = _blueprint_provenance_query()
        query = QueryBlueprintProvenanceV1(
            blueprint=valid_query.blueprint,
            expected_pm_task=expected_pm_task,
            expected_factory_run_id=valid_query.expected_factory_run_id,
            expected_task_id=valid_query.expected_task_id,
            expected_blueprint_id=valid_query.expected_blueprint_id,
            expected_logical_path=valid_query.expected_logical_path,
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(query)

        assert exc_info.value.code == "blueprint_provenance_expected_target_files_invalid"

    @pytest.mark.parametrize("embedded_hash", ["", "A" * 64, "a" * 63, "g" * 64, 7])
    def test_invalid_embedded_hash_fails_closed(self, embedded_hash: object) -> None:
        payload = _blueprint_provenance_payload()
        payload["blueprint_hash"] = embedded_hash

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_hash_invalid"

    def test_nested_ignored_keys_preserve_producer_v1_compatibility(self) -> None:
        payload = _blueprint_provenance_payload()
        original_hash = payload["blueprint_hash"]
        payload["context"]["capability_token"] = {"token_id": "cap-rotated"}
        payload["context"]["nested"][0]["job_token"] = {"token_id": "job-rotated"}

        result = query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert result.matches is True
        assert result.recomputed_blueprint_hash == original_hash

    def test_query_and_service_do_not_mutate_input(self) -> None:
        payload = _blueprint_provenance_payload()
        before = deepcopy(payload)
        query = _blueprint_provenance_query(payload)

        result = query_blueprint_provenance(query)

        assert result.matches is True
        assert payload == before
        assert query.blueprint == before
        assert query.blueprint is not payload
        assert query.expected_pm_task == _pm_task_payload()

    @pytest.mark.parametrize(
        ("field", "invalid_value"),
        [
            ("expected_factory_run_id", 7),
            ("expected_factory_run_id", "x" * 257),
            ("expected_task_id", "task\ncontrol"),
            ("expected_task_id", "e\u0301"),
            ("expected_blueprint_id", "../unsafe"),
            ("expected_blueprint_id", "x" * 257),
            ("expected_logical_path", "/absolute/blueprint.json"),
            ("expected_logical_path", "runtime//blueprints/value.json"),
            ("expected_logical_path", "x" * 1025),
        ],
    )
    def test_query_dto_rejects_unbounded_or_unsafe_identity_and_path(
        self,
        field: str,
        invalid_value: object,
    ) -> None:
        valid = _blueprint_provenance_query()
        values = {
            "blueprint": valid.blueprint,
            "expected_pm_task": valid.expected_pm_task,
            "expected_factory_run_id": valid.expected_factory_run_id,
            "expected_task_id": valid.expected_task_id,
            "expected_blueprint_id": valid.expected_blueprint_id,
            "expected_logical_path": valid.expected_logical_path,
        }
        values[field] = invalid_value

        with pytest.raises((TypeError, ValueError)):
            QueryBlueprintProvenanceV1(**values)


class TestTaskBlueprintProvenanceSnapshotV1StrictDto:
    @pytest.mark.parametrize("invalid_matches", [1, 0, "true", None])
    def test_matches_requires_exact_bool(self, invalid_matches: object) -> None:
        values = _blueprint_provenance_snapshot_kwargs()
        values["matches"] = invalid_matches

        with pytest.raises(TypeError, match="matches"):
            TaskBlueprintProvenanceSnapshotV1(**values)

    @pytest.mark.parametrize(
        ("field", "invalid_value"),
        [
            ("schema_version", "chief_engineer.blueprint_provenance.v2"),
            ("blueprint_schema_version", "chief_engineer.blueprint.v2"),
            ("hash_scheme", "chief_engineer.blueprint_hash.v2"),
        ],
    )
    def test_schema_and_hash_scheme_are_frozen(self, field: str, invalid_value: str) -> None:
        values = _blueprint_provenance_snapshot_kwargs()
        values[field] = invalid_value

        with pytest.raises(ValueError, match=field):
            TaskBlueprintProvenanceSnapshotV1(**values)

    @pytest.mark.parametrize(
        ("field", "invalid_value"),
        [
            ("embedded_blueprint_hash", "A" * 64),
            ("recomputed_blueprint_hash", "a" * 63),
            ("pm_contract_hash", "g" * 64),
            ("recomputed_pm_contract_hash", 7),
            ("pm_task_canonical_hash", ""),
        ],
    )
    def test_all_hash_fields_require_lower_sha256(self, field: str, invalid_value: object) -> None:
        values = _blueprint_provenance_snapshot_kwargs()
        values[field] = invalid_value

        with pytest.raises((TypeError, ValueError), match=field):
            TaskBlueprintProvenanceSnapshotV1(**values)

    @pytest.mark.parametrize(
        ("field", "invalid_value"),
        [
            ("factory_run_id", 7),
            ("factory_run_id", "x" * 257),
            ("task_id", "task\ncontrol"),
            ("task_id", "e\u0301"),
            ("blueprint_id", "../unsafe"),
            ("logical_path", "/absolute/blueprint.json"),
            ("logical_path", "runtime//blueprints/value.json"),
            ("logical_path", "x" * 1025),
        ],
    )
    def test_identity_and_logical_path_are_strict(self, field: str, invalid_value: object) -> None:
        values = _blueprint_provenance_snapshot_kwargs()
        values[field] = invalid_value

        with pytest.raises((TypeError, ValueError), match=field):
            TaskBlueprintProvenanceSnapshotV1(**values)

    @pytest.mark.parametrize(
        "invalid_target_files",
        [
            ["src/main.py"],
            ("src/main.py", "src/main.py"),
            ("src/../main.py",),
            tuple(f"src/file-{index}.py" for index in range(513)),
        ],
    )
    def test_target_files_are_bounded_unique_safe_tuple(self, invalid_target_files: object) -> None:
        values = _blueprint_provenance_snapshot_kwargs()
        values["target_files"] = invalid_target_files

        with pytest.raises((TypeError, ValueError), match="target_files"):
            TaskBlueprintProvenanceSnapshotV1(**values)


class TestTaskBlueprintGeneratedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = TaskBlueprintGeneratedEventV1(
            event_id="evt-1",
            task_id="task-1",
            workspace="/repo",
            blueprint_path="/repo/.blueprint/task-1.yaml",
            generated_at="2026-03-24T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.blueprint_path == "/repo/.blueprint/task-1.yaml"
        assert evt.risk_level is None

    def test_with_risk_level(self) -> None:
        evt = TaskBlueprintGeneratedEventV1(
            event_id="evt-1",
            task_id="task-1",
            workspace="/repo",
            blueprint_path="/repo/.blueprint/task-1.yaml",
            generated_at="2026-03-24T10:00:00Z",
            risk_level="medium",
        )
        assert evt.risk_level == "medium"


class TestTaskBlueprintGeneratedEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            TaskBlueprintGeneratedEventV1(
                event_id="",
                task_id="task-1",
                workspace="/repo",
                blueprint_path="/bp",
                generated_at="2026-03-24T10:00:00Z",
            )

    def test_empty_blueprint_path_raises(self) -> None:
        with pytest.raises(ValueError, match="blueprint_path"):
            TaskBlueprintGeneratedEventV1(
                event_id="e1",
                task_id="task-1",
                workspace="/repo",
                blueprint_path="",
                generated_at="2026-03-24T10:00:00Z",
            )


class TestTaskBlueprintResultV1HappyPath:
    def test_success(self) -> None:
        res = TaskBlueprintResultV1(
            ok=True,
            task_id="task-1",
            workspace="/repo",
            status="generated",
            blueprint_path="/bp.yaml",
        )
        assert res.ok is True
        assert res.blueprint_id is None
        assert res.blueprint_path == "/bp.yaml"
        assert res.blueprint_hash == ""
        assert res.recommendations == ()
        assert res.risks == ()

    def test_with_blueprint_id(self) -> None:
        res = TaskBlueprintResultV1(
            ok=True,
            task_id="task-1",
            workspace="/repo",
            status="generated",
            blueprint_id="bp-1",
        )
        assert res.blueprint_id == "bp-1"

    def test_failure(self) -> None:
        res = TaskBlueprintResultV1(
            ok=False,
            task_id="task-1",
            workspace="/repo",
            status="failed",
            summary="Blueprint generation failed",
        )
        assert res.ok is False
        assert res.status == "failed"

    def test_recommendations_normalized_to_tuple(self) -> None:
        res = TaskBlueprintResultV1(
            ok=True,
            task_id="task-1",
            workspace="/repo",
            status="ok",
            recommendations=["use cache", "add retry"],  # type: ignore[arg-type]
        )
        assert res.recommendations == ("use cache", "add retry")


class TestTaskBlueprintResultV1EdgeCases:
    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            TaskBlueprintResultV1(ok=True, task_id="", workspace="/repo", status="ok")

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            TaskBlueprintResultV1(ok=True, task_id="task-1", workspace="/repo", status="")


class TestChiefEngineerBlueprintErrorV1:
    def test_default_values(self) -> None:
        err = ChiefEngineerBlueprintErrorV1("blueprint generation failed")
        assert str(err) == "blueprint generation failed"
        assert err.code == "chief_engineer_blueprint_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = ChiefEngineerBlueprintErrorV1(
            "timeout",
            code="blueprint_timeout",
            details={"task_id": "task-1"},
        )
        assert err.code == "blueprint_timeout"
        assert err.details == {"task_id": "task-1"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            ChiefEngineerBlueprintErrorV1("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            ChiefEngineerBlueprintErrorV1("error", code="  ")


class TestChiefEngineerBlueprintPublicService:
    def test_generate_ready_blueprint_records_only_authoritative_file_owners(self, tmp_path) -> None:
        command = GenerateTaskBlueprintCommandV1(
            task_id="TASK-OWNERS-READY",
            workspace=str(tmp_path),
            objective="Build task ownership projection",
            llm_blueprint={
                "construction_plan": {
                    "files": [
                        {"path": "src/advisory_only.py"},
                    ],
                },
            },
            context={
                "task_title": "Task ownership projection",
                "target_files": ["src/ownership.py", "tests/test_ownership.py"],
                "acceptance_criteria": ["Ownership projection is durable"],
                "execution_checklist": ["Register target-file ownership"],
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "title": "Task ownership delivery plan",
                    "user_journey": ["Generate blueprint", "Route Director handoff"],
                },
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "behavior_contract": {
                        "rule_matrix": ["Ready blueprints record only PM-authorized files"],
                    },
                },
            },
        )

        result = generate_task_blueprint(command)

        assert result.ok is True
        owners = read_file_owners(
            str(tmp_path),
            str(resolve_storage_roots(str(tmp_path)).runtime_root),
            ["src/ownership.py", "tests/test_ownership.py", "src/advisory_only.py"],
        )
        assert owners == {
            "src/ownership.py": {
                "owner_step_id": "TASK-OWNERS-READY",
                "owner_parent": "TASK-OWNERS-READY",
            },
            "tests/test_ownership.py": {
                "owner_step_id": "TASK-OWNERS-READY",
                "owner_parent": "TASK-OWNERS-READY",
            },
        }

    def test_generate_denied_blueprint_does_not_register_file_owners(self, tmp_path, monkeypatch) -> None:
        def fail_if_called(*_args: object, **_kwargs: object) -> dict[str, dict[str, str]]:
            raise AssertionError("denied CE handoff must not register file owners")

        monkeypatch.setattr(blueprint_service_module, "record_task_file_owners", fail_if_called)
        command = GenerateTaskBlueprintCommandV1(
            task_id="TASK-OWNERS-DENIED",
            workspace=str(tmp_path),
            objective="Build flavor recipe planner",
            context={
                "task_title": "Flavor recipe planner",
                "target_files": ["src/models/flavor.rs", "src/engine/palette_rules.rs"],
                "acceptance_criteria": ["cargo test passes", "recipe behavior tests pass"],
                "execution_checklist": ["Implement flavor model", "Implement palette rules"],
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
                    "behavior_contract": {
                        "rule_matrix": ["treasure cargo affects route budget"],
                    },
                },
            },
        )

        result = generate_task_blueprint(command)

        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["handoff_ready"] is False

    def test_generate_fails_before_persisting_ready_blueprint_without_owner_evidence(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        def fail_registration(*_args: object, **_kwargs: object) -> dict[str, dict[str, str]]:
            raise FileOwnershipLedgerError("simulated ownership write failure")

        monkeypatch.setattr(blueprint_service_module, "record_task_file_owners", fail_registration)
        command = GenerateTaskBlueprintCommandV1(
            task_id="TASK-OWNERS-FAIL",
            workspace=str(tmp_path),
            objective="Build task ownership projection",
            context={
                "task_title": "Task ownership projection",
                "target_files": ["src/ownership.py"],
                "acceptance_criteria": ["Ownership projection is durable"],
                "execution_checklist": ["Register target-file ownership"],
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "title": "Task ownership delivery plan",
                    "user_journey": ["Generate blueprint", "Route Director handoff"],
                },
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "behavior_contract": {
                        "rule_matrix": ["Ready blueprints require ownership evidence"],
                    },
                },
            },
        )

        with pytest.raises(FileOwnershipLedgerError, match="simulated ownership write failure"):
            generate_task_blueprint(command)

        assert BlueprintPersistence(str(tmp_path), ensure_directory=False).list_all() == []

    def test_generate_factory_pm_task_contract_is_exact_authority(self, tmp_path) -> None:
        exact_pm_task = {
            "id": "TASK-FACTORY",
            "goal": "Persist the exact Factory PM task contract.",
            "target_files": ["src/exact.py"],
            "acceptance_criteria": ["Exact PM task provenance is durable"],
            "steps": ["Generate the CE blueprint"],
        }
        alias_task = {
            "id": "TASK-ALIAS",
            "goal": "This compatibility alias must not replace Factory authority.",
            "target_files": ["src/alias.py"],
        }
        exact_before = deepcopy(exact_pm_task)
        command = GenerateTaskBlueprintCommandV1(
            task_id="TASK-FACTORY",
            workspace=str(tmp_path),
            objective="Persist exact Factory PM task provenance",
            run_id="factory-run-exact",
            context={
                "pm_task_contract": exact_pm_task,
                "pm_contract_hash": "1" * 64,
                "contract_hash": "2" * 64,
                "task": alias_task,
                "pm_task": alias_task,
                "target_files": ["src/exact.py"],
                "acceptance_criteria": ["Exact PM task provenance is durable"],
                "execution_checklist": ["Generate the CE blueprint"],
            },
        )

        result = generate_task_blueprint(command)

        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["pm_task"] == exact_pm_task
        expected_hash = stable_hash(_producer_v1_hashable(exact_pm_task))
        assert persisted["pm_contract_hash"] == expected_hash
        assert persisted["contract_hash"] == expected_hash
        assert persisted["context"]["pm_contract_hash"] == expected_hash
        assert persisted["context"]["contract_hash"] == expected_hash
        assert exact_pm_task == exact_before

    @pytest.mark.parametrize("invalid_pm_task_contract", [None, {}, [], "not-a-mapping"])
    def test_generate_rejects_invalid_explicit_factory_contract_before_persistence(
        self,
        tmp_path,
        monkeypatch,
        invalid_pm_task_contract: object,
    ) -> None:
        ownership_calls: list[tuple[object, ...]] = []

        def record_ownership(*args: object, **_kwargs: object) -> dict[str, dict[str, str]]:
            ownership_calls.append(args)
            return {}

        monkeypatch.setattr(blueprint_service_module, "record_task_file_owners", record_ownership)
        command = GenerateTaskBlueprintCommandV1(
            task_id="TASK-INVALID-FACTORY",
            workspace=str(tmp_path),
            objective="Reject invalid exact Factory PM task provenance",
            context={
                "pm_task_contract": invalid_pm_task_contract,
                "task": {
                    "id": "TASK-ALIAS",
                    "goal": "Compatibility alias must never rescue an invalid Factory slot.",
                },
                "target_files": ["src/should-not-persist.py"],
                "acceptance_criteria": ["Invalid Factory contracts fail closed"],
                "execution_checklist": ["Reject before every write"],
            },
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            generate_task_blueprint(command)

        assert exc_info.value.code == "blueprint_pm_task_contract_invalid"
        assert exc_info.value.details["field"] == "pm_task_contract"
        assert ownership_calls == []
        assert BlueprintPersistence(str(tmp_path), ensure_directory=False).list_all() == []

    def test_generate_and_query_task_blueprint(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="PM-42",
            workspace=str(tmp_path),
            objective="Build Director task board",
            run_id="run-1",
            constraints={"guardrail": "no target project writes"},
            llm_blueprint={
                "construction_plan": {
                    "preparation": ["Confirm runtime state contract"],
                    "implementation": ["Define typed task state", "Render board state transitions"],
                    "module_boundaries": [
                        "DirectorTaskPanel owns rendering only",
                        "runtime client owns WebSocket state",
                    ],
                    "verification": ["npm run build", "focused component smoke test"],
                },
                "scope_for_apply": ["src/frontend/src/app/components/director/DirectorTaskPanel.tsx"],
                "risk_flags": ["runtime state drift between task board and ledger"],
            },
            context={
                "task_title": "Director board",
                "acceptance_criteria": ["Task board shows claimed/running/completed states"],
                "execution_checklist": ["Create component", "Wire state", "Add focused tests"],
                "target_files": ["src/frontend/src/app/components/director/DirectorTaskPanel.tsx"],
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "title": "Director board delivery plan",
                    "user_journey": ["Open board", "Inspect task states"],
                },
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "behavior_contract": {
                        "rule_matrix": ["claimed tasks render as claimed", "running tasks render progress"],
                    },
                },
                "task": {
                    "id": "PM-42",
                    "acceptance_criteria": ["Task board shows claimed/running/completed states"],
                    "execution_checklist": ["Create component", "Wire state", "Add focused tests"],
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        assert result.blueprint_id is not None
        assert result.blueprint_hash
        assert result.status == "generated"
        assert result.blueprint_path == f"runtime/blueprints/{result.blueprint_id}.json"
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["blueprint_hash"] == result.blueprint_hash
        assert persisted["target_files"] == ["src/frontend/src/app/components/director/DirectorTaskPanel.tsx"]
        assert persisted["acceptance_criteria"] == ["Task board shows claimed/running/completed states"]
        assert persisted["execution_checklist"] == ["Create component", "Wire state", "Add focused tests"]
        assert persisted["pm_task"]["id"] == "PM-42"
        assert persisted["contract_completeness"]["handoff_ready"] is True
        assert persisted["contract_completeness"]["depth_contract_ready"] is True
        assert persisted["delivery_plan_document"]["schema_version"] == "polaris.delivery_plan_document.v1"
        assert persisted["delivery_depth_contract"]["schema_version"] == "polaris.delivery_depth_contract.v1"
        assert persisted["behavior_contract"]["rule_matrix"][0] == "claimed tasks render as claimed"
        assert persisted["ce_handoff"]["llm_blueprint_consumed"] is True
        assert persisted["ce_handoff"]["llm_blueprint_authority"] == "advisory_only"
        assert persisted["llm_blueprint"]["authoritative"] is False
        assert persisted["llm_blueprint"]["implementation_phases"] == [
            "Confirm runtime state contract",
            "Define typed task state",
            "Render board state transitions",
        ]
        assert persisted["llm_blueprint"]["verification_steps"] == [
            "npm run build",
            "focused component smoke test",
        ]
        assert persisted["llm_blueprint"]["risk_flags"] == ["runtime state drift between task board and ledger"]

        status = get_blueprint_status(
            GetBlueprintStatusQueryV1(
                task_id="PM-42",
                workspace=str(tmp_path),
                run_id="run-1",
            )
        )

        assert status.ok is True
        assert status.blueprint_id == result.blueprint_id
        assert status.blueprint_hash == result.blueprint_hash
        assert status.summary.startswith("Chief Engineer blueprint for PM-42")

    def test_generate_task_blueprint_wraps_bare_behavior_contract(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-7",
            workspace=str(tmp_path),
            objective="Build scoring engine",
            context={
                "acceptance_criteria": ["Score normal, boundary, and invalid inputs"],
                "execution_checklist": ["Implement scorer", "Add behavior tests"],
                "target_files": ["src/engine/scorer.ts", "tests/scorer.test.ts"],
                "behavior_contract": {
                    "rule_matrix": [{"input": "high signal", "expected": "priority"}],
                    "edge_cases": ["missing signal", "negative weight"],
                    "required_behavior_tests": ["normal", "boundary", "invalid"],
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        assert result.blueprint_id is not None
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["contract_completeness"]["depth_contract_ready"] is False
        assert persisted["delivery_depth_contract"]["schema_version"] == "polaris.delivery_depth_contract.v1"
        assert persisted["delivery_depth_contract"]["source"] == "context.behavior_contract"
        assert persisted["behavior_contract"]["required_behavior_tests"] == ["normal", "boundary", "invalid"]
        assert persisted["behavior_contract"]["rule_matrix"][0]["expected"] == "priority"

    def test_generate_task_blueprint_preserves_pm_task_test_targets(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-JS",
            workspace=str(tmp_path),
            objective="Build JavaScript product contract",
            context={
                "target_files": ["src/index.js"],
                "acceptance_criteria": ["`npm test` and external validation pass"],
                "execution_checklist": ["Implement source", "Add tests", "Run validation"],
                "task": {
                    "id": "TASK-JS",
                    "target_files": [
                        "src/index.js",
                        "src/engine/rules.js",
                        "tests/product.test.js",
                        "tests/test_product.py",
                        "README.md",
                    ],
                    "scope_paths": ["src/index.js", "tests/product.test.js"],
                    "acceptance_criteria": ["`npm test` and external validation pass"],
                    "execution_checklist": ["Implement source", "Add tests", "Run validation"],
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        assert result.target_files == (
            "src/index.js",
            "src/engine/rules.js",
            "tests/product.test.js",
            "tests/test_product.py",
            "README.md",
        )

    def test_generate_task_blueprint_records_advisory_construction_plan_files_without_promoting(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-TS-FILES",
            workspace=str(tmp_path),
            objective="Build browser simulation with shared TypeScript engine types",
            llm_blueprint={
                "construction_plan": {
                    "phase_1_bootstrap": {
                        "files": [
                            {"path": "package.json", "contract": "Node scripts and TypeScript config"},
                            {"path": "index.html", "contract": "Browser shell"},
                        ],
                    },
                    "phase_2_engine_core": {
                        "files": [
                            {
                                "path": "src/engine/types.ts",
                                "contract": "Shared domain interfaces for simulation and renderer",
                            },
                            {"path": "src/engine/simulation.ts"},
                            {"path": "src/engine/renderer.ts"},
                            {"path": "../outside.ts"},
                            {"path": "/tmp/absolute.ts"},
                        ],
                    },
                    "verification": ["npm run build", "npm test"],
                },
            },
            context={
                "language": "typescript",
                "target_files": ["package.json", "index.html", "src/web.ts"],
                "scope_paths": ["package.json", "index.html", "src/web.ts"],
                "acceptance_criteria": ["npm run build passes"],
                "execution_checklist": ["Implement browser entrypoint and engine"],
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        assert result.target_files == ("package.json", "index.html", "src/web.ts")
        assert "src/engine/types.ts" not in result.target_files
        assert "src/engine/simulation.ts" not in result.target_files
        assert "src/engine/renderer.ts" not in result.target_files
        assert "../outside.ts" not in result.target_files
        assert "/tmp/absolute.ts" not in result.target_files
        assert "npm run build" not in result.target_files
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["target_files"] == list(result.target_files)
        assert persisted["context"]["target_files"] == persisted["target_files"]
        assert "src/engine/types.ts" not in persisted["scope_paths"]
        assert "src/engine/types.ts" in persisted["llm_blueprint"]["projected_target_files"]
        assert persisted["llm_blueprint"]["projected_target_file_authority"] == "advisory_only_not_scope_authority"
        assert "src/engine/types.ts" in persisted["llm_blueprint"]["advisory_target_files_not_promoted"]
        module_paths = {
            module["path"] for module in persisted["module_interface_contract"]["modules"] if isinstance(module, dict)
        }
        assert "src/engine/types.ts" not in module_paths

    def test_generate_task_blueprint_materializes_depth_contract_test_target(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-TS",
            workspace=str(tmp_path),
            objective="Build TypeScript market behavior",
            context={
                "language": "typescript",
                "target_files": ["package.json", "src/index.ts", "src/models/Market.ts"],
                "acceptance_criteria": ["npm run build passes"],
                "execution_checklist": ["Implement source modules"],
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "source": "factory.catalog_contract",
                    "language": "typescript",
                    "minimums": {
                        "min_test_files": 1,
                        "min_test_assertions": 8,
                    },
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        assert "tests/behavior.test.ts" in result.target_files
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert "tests/behavior.test.ts" in persisted["target_files"]
        assert "tests/behavior.test.ts" in persisted["scope_paths"]
        assert persisted["context"]["target_files"] == persisted["target_files"]
        assert persisted["context"]["scope_paths"] == persisted["scope_paths"]
        assert any("min_test_files=1" in item for item in persisted["acceptance_criteria"])
        assert any("min_test_assertions=8" in item for item in persisted["execution_checklist"])
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["target_files"] == list(result.target_files)
        assert persisted["contract_completeness"]["handoff_ready"] is True

    def test_generate_task_blueprint_does_not_promote_default_test_into_manifest_boundary(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-JS-MANIFEST",
            workspace=str(tmp_path),
            objective="Build JavaScript package manifest and script contract only.",
            context={
                "language": "javascript",
                "target_files": ["package.json"],
                "scope_paths": ["package.json"],
                "project_declared_target_files": [
                    "package.json",
                    "src/index.js",
                    "tests/product.test.js",
                ],
                "acceptance_criteria": ["package manifest exists"],
                "execution_checklist": ["Materialize only the listed manifest file"],
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "language": "javascript",
                    "product_intent": {
                        "primary_entities": ["meteor", "wish", "queue", "priority"],
                    },
                    "minimums": {
                        "min_test_files": 1,
                        "min_test_assertions": 8,
                    },
                },
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "language": "javascript",
                    "product_summary": {
                        "core_terms": ["meteor", "wish", "queue", "priority"],
                    },
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        assert result.target_files == ("package.json",)
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["target_files"] == ["package.json"]
        semantic_alignment = persisted["contract_completeness"]["semantic_alignment"]
        assert semantic_alignment["support_boundary"] is True
        assert persisted["contract_completeness"]["handoff_ready"] is True

    def test_generate_task_blueprint_adds_existing_test_targets_to_scope(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-TS-SCOPE",
            workspace=str(tmp_path),
            objective="Build TypeScript market behavior tests",
            context={
                "language": "typescript",
                "target_files": ["package.json", "src/index.ts", "tests/verify.test.ts"],
                "scope_paths": ["package.json", "src/index.ts"],
                "acceptance_criteria": ["npm run test passes"],
                "execution_checklist": ["Implement source modules and tests"],
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "language": "typescript",
                    "minimums": {
                        "min_test_files": 1,
                        "min_test_assertions": 8,
                    },
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert "tests/verify.test.ts" in persisted["target_files"]
        assert "tests/verify.test.ts" in persisted["scope_paths"]
        assert persisted["context"]["target_files"] == persisted["target_files"]
        assert persisted["context"]["scope_paths"] == persisted["scope_paths"]

    def test_generate_task_blueprint_does_not_block_path_only_domain_mismatch(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-L2-RUST",
            workspace=str(tmp_path),
            objective="Build pirate treasure budget planner",
            context={
                "task_title": "Pirate treasure budget planner",
                "target_files": [
                    "src/models/flavor.rs",
                    "src/models/recipe.rs",
                    "src/engine/palette_rules.rs",
                    "src/engine/plating_runner.rs",
                ],
                "acceptance_criteria": [
                    "deterministic checks cover content_any: treasure|budget|port|reef",
                    "cargo test passes",
                ],
                "execution_checklist": [
                    "Implement flavor and recipe models",
                    "Implement palette rules and plating runner",
                ],
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
                    "behavior_contract": {
                        "rule_matrix": [
                            "treasure cargo affects route budget",
                            "port fees affect unlock decisions",
                            "reef danger changes final recommendation",
                        ],
                    },
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["contract_completeness"]["handoff_ready"] is True
        assert persisted["handoff_ready"] is True
        semantic_alignment = persisted["contract_completeness"]["semantic_alignment"]
        assert semantic_alignment["expected_terms"] == ["budget", "port", "reef", "treasure"]
        assert semantic_alignment["target_file_matches"] == []
        assert semantic_alignment["advisory"]
        assert persisted["contract_completeness"]["semantic_blockers"] == []
        governance = persisted["governance"]["quality_gate"]
        assert governance["passed"] is True
        assert governance["blockers"] == []

    def test_generate_task_blueprint_allows_support_boundary_with_delivery_semantics(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-JS-SUPPORT-BOUNDARY",
            workspace=str(tmp_path),
            objective="在工作区根交付 流星愿望队列 的 JavaScript/npm 项目骨架。 "
            "Scope this task to project manifest and runtime entrypoint only.",
            context={
                "task_title": "流星愿望队列 JavaScript/npm 项目骨架 - manifest and entrypoint",
                "language": "javascript",
                "target_files": ["package.json", "src/index.js"],
                "acceptance_criteria": [
                    "verify package.json exists",
                    "verify src/index.js exists",
                    "package/test/build scripts are internally consistent",
                ],
                "execution_checklist": [
                    "Materialize only the listed support boundary files",
                    "Keep downstream source modules out of this boundary",
                ],
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "language": "javascript",
                    "product_summary": {
                        "intent": "Deliver a meteor wish queue with observable behavior.",
                        "core_terms": ["meteor", "wish", "queue", "priority"],
                    },
                },
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "language": "javascript",
                    "product_intent": {
                        "subject": "meteor wish queue",
                        "primary_entities": ["meteor", "wish", "queue", "priority"],
                    },
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["contract_completeness"]["handoff_ready"] is True
        assert persisted["handoff_ready"] is True
        semantic_alignment = persisted["contract_completeness"]["semantic_alignment"]
        assert semantic_alignment["support_boundary"] is True
        assert semantic_alignment["planning_text_matches"] == []
        assert semantic_alignment["blockers"] == []
        assert any("support boundary" in item for item in semantic_alignment["advisory"])

    def test_generate_task_blueprint_allows_blueprint_overlay_semantic_alignment(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-JS-CORE",
            workspace=str(tmp_path),
            objective="在工作区根交付 流星愿望队列。 Scope this task to core engine/service modules only.",
            context={
                "task_title": "实现 流星愿望队列 - core engine/service modules",
                "language": "javascript",
                "target_files": ["src/engine/rules.js", "src/engine/runner.js"],
                "acceptance_criteria": [
                    "verify src/engine/rules.js exists",
                    "verify src/engine/runner.js exists",
                ],
                "execution_checklist": ["Materialize only the listed core engine files."],
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "language": "javascript",
                    "product_summary": {
                        "intent": "Deliver a meteor wish queue with observable behavior.",
                        "core_terms": ["meteor", "wish", "queue", "priority"],
                    },
                },
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "language": "javascript",
                    "product_intent": {
                        "subject": "meteor wish queue",
                        "primary_entities": ["meteor", "wish", "queue", "priority"],
                    },
                },
            },
            llm_blueprint={
                "construction_plan": {
                    "rules": [
                        "Implement meteor to wish scoring.",
                        "Implement queue priority sorting.",
                    ],
                    "runner": ["Return visible queue metrics for each wish."],
                },
                "scope_for_apply": ["src/engine/rules.js", "src/engine/runner.js"],
                "risk_flags": [],
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["contract_completeness"]["handoff_ready"] is True
        assert persisted["handoff_ready"] is True
        semantic_alignment = persisted["contract_completeness"]["semantic_alignment"]
        assert semantic_alignment["planning_text_matches"] == []
        assert set(semantic_alignment["blueprint_text_matches"]) >= {"meteor", "priority", "queue", "wish"}
        assert semantic_alignment["blockers"] == []
        assert any("CE blueprint overlay" in item for item in semantic_alignment["advisory"])

    def test_generate_task_blueprint_blocks_zero_match_despite_partial_overlay(self, tmp_path) -> None:
        """Zero-match planning text must still block even if the overlay mentions a domain term.

        The union escape only applies when the PM-scoped planning text itself
        carries at least one domain term; a completely off-domain objective
        (PM scoped the wrong task) must stay blocked so the gate keeps its
        PM-contract-teeth. Uses NON-support-boundary target files so the
        semantic gate (not the structural support-boundary exemption) decides.
        """
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-GO-OFFDOMAIN",
            workspace=str(tmp_path),
            objective="Implement flavor recipe planner",
            context={
                "task_title": "Flavor recipe planner",
                "language": "go",
                "target_files": ["engine/flavor.go", "engine/recipe.go"],
                "scope_paths": ["engine/flavor.go", "engine/recipe.go"],
                "acceptance_criteria": ["cargo test passes"],
                "execution_checklist": ["Implement flavor and recipe models."],
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
            llm_blueprint={
                "construction_plan": {
                    "notes": ["Domain alignment for treasure budget happens downstream."],
                },
                "scope_for_apply": ["engine/flavor.go"],
                "risk_flags": [],
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["contract_completeness"]["handoff_ready"] is False
        assert persisted["handoff_ready"] is False
        semantic_alignment = persisted["contract_completeness"]["semantic_alignment"]
        assert semantic_alignment["planning_text_matches"] == []
        assert semantic_alignment["support_boundary"] is False
        assert persisted["contract_completeness"]["semantic_blockers"]
        governance = persisted["governance"]["quality_gate"]
        assert governance["passed"] is False
        assert any("contract semantic blocker" in item for item in governance["blockers"])

    def test_generate_task_blueprint_allows_mixed_manifest_boundary_structural(self, tmp_path) -> None:
        """A manifest+entrypoint+test boundary is structurally exempt via support boundary.

        Regression for the L1-04 TASK-2-foundation block: a sub-task scoped to
        "project manifest and build contract only" targets go.mod (language-
        neutral manifest, now a support file), main.go (entrypoint, already a
        support file), and behavior_test.go (a behavior test). Such a boundary
        carries no domain implementation, so the semantic gate defers to the
        delivery context instead of demanding domain-term similarity.
        """
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-GO-MANIFEST-FOUNDATION",
            workspace=str(tmp_path),
            objective="实现 ASCII 魔法宠物终端 的解锁规则、谜语验证、展厅布局和可执行 Go 入口。 Scope this task to project manifest and build contract only.",
            context={
                "task_title": "实现 ASCII 魔法宠物终端 Go 规则引擎与 CLI 入口 - project manifest and build contract",
                "language": "go",
                "target_files": ["go.mod", "main.go", "behavior_test.go"],
                "scope_paths": ["go.mod", "behavior_test.go"],
                "acceptance_criteria": [
                    "verify go.mod exists",
                    "package/test/build scripts and module settings are internally consistent.",
                ],
                "execution_checklist": ["Materialize only the listed target files."],
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "language": "go",
                    "product_summary": {
                        "core_terms": ["pet", "spell", "mood", "ascii"],
                    },
                },
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "language": "go",
                    "product_intent": {
                        "subject": "ASCII 魔法宠物终端",
                        "primary_entities": ["pet", "spell", "mood", "ascii"],
                    },
                },
            },
            llm_blueprint={
                "construction_plan": {
                    "contract_alignment": {
                        "summary": "Anchor the Go module path so downstream packages can import the pet and spell models consistently.",
                    },
                    "manifest_materialization": {
                        "target_file": "go.mod",
                        "director_command": "go mod init example/pet",
                    },
                },
                "scope_for_apply": ["go.mod"],
                "risk_flags": [],
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["contract_completeness"]["handoff_ready"] is True
        semantic_alignment = persisted["contract_completeness"]["semantic_alignment"]
        assert semantic_alignment["support_boundary"] is True
        assert semantic_alignment["blockers"] == []

    def test_generate_task_blueprint_blocks_domain_mismatched_planning_text(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-L2-RUST-MISMATCHED-PLAN",
            workspace=str(tmp_path),
            objective="Build flavor recipe planner",
            context={
                "task_title": "Flavor recipe planner",
                "target_files": [
                    "src/models/flavor.rs",
                    "src/models/recipe.rs",
                    "src/engine/palette_rules.rs",
                    "src/engine/plating_runner.rs",
                ],
                "acceptance_criteria": [
                    "cargo test passes",
                    "recipe behavior tests pass",
                ],
                "execution_checklist": [
                    "Implement flavor and recipe models",
                    "Implement palette rules and plating runner",
                ],
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
                    "behavior_contract": {
                        "rule_matrix": [
                            "treasure cargo affects route budget",
                            "port fees affect unlock decisions",
                            "reef danger changes final recommendation",
                        ],
                    },
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["contract_completeness"]["handoff_ready"] is False
        assert persisted["handoff_ready"] is False
        semantic_alignment = persisted["contract_completeness"]["semantic_alignment"]
        assert semantic_alignment["planning_text_matches"] == []
        assert persisted["contract_completeness"]["semantic_blockers"]
        governance = persisted["governance"]["quality_gate"]
        assert governance["passed"] is False
        assert any("contract semantic blocker" in item for item in governance["blockers"])

    def test_generate_task_blueprint_allows_domain_aligned_handoff(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-L2-RUST-OK",
            workspace=str(tmp_path),
            objective="Build pirate treasure budget planner",
            context={
                "task_title": "Pirate treasure budget planner",
                "target_files": [
                    "src/models/treasure.rs",
                    "src/models/budget.rs",
                    "src/models/port.rs",
                    "src/engine/treasure_rules.rs",
                    "src/engine/reef_runner.rs",
                ],
                "acceptance_criteria": [
                    "treasure, budget, port, and reef behavior tests pass",
                    "cargo test passes",
                ],
                "execution_checklist": [
                    "Implement treasure and budget models",
                    "Implement port fee and reef risk rules",
                    "Add normal, boundary, and invalid input tests",
                ],
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
                    "behavior_contract": {
                        "rule_matrix": [
                            "treasure cargo affects route budget",
                            "port fees affect unlock decisions",
                            "reef danger changes final recommendation",
                        ],
                    },
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["contract_completeness"]["handoff_ready"] is True
        assert persisted["handoff_ready"] is True
        semantic_alignment = persisted["contract_completeness"]["semantic_alignment"]
        assert semantic_alignment["ready"] is True
        assert set(semantic_alignment["target_file_matches"]) >= {"budget", "port", "reef", "treasure"}

    def test_generate_task_blueprint_adds_module_interface_contract(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-PY-WEATHER",
            workspace=str(tmp_path),
            objective="Implement mini planet weather balloon Python engine and CLI",
            context={
                "task_title": "Mini planet weather balloon",
                "language": "python",
                "target_files": [
                    "src/models/weather.py",
                    "src/engine/forecast.py",
                    "src/radio.py",
                    "src/main.py",
                ],
                "acceptance_criteria": [
                    "weather, cloud, and wind behavior tests pass",
                    "python src/main.py returns success",
                ],
                "execution_checklist": [
                    "Implement weather model",
                    "Implement forecast rules",
                    "Wire radio output and CLI",
                ],
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "language": "python",
                    "product_summary": {
                        "intent": "Deliver a mini planet weather balloon with observable forecast behavior.",
                        "core_terms": ["planet", "weather", "cloud", "wind"],
                    },
                },
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "language": "python",
                    "product_intent": {
                        "subject": "mini planet weather balloon",
                        "primary_entities": ["planet", "weather", "cloud", "wind"],
                    },
                    "behavior_contract": {
                        "rule_matrix": [
                            "weather mood affects cloud cover",
                            "wind strength changes radio warning",
                            "unknown mood uses explicit fallback",
                        ],
                    },
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        interface_contract = persisted["module_interface_contract"]
        assert interface_contract["schema_version"] == "chief_engineer.module_interface_contract.v1"
        assert interface_contract["language"] == "python"
        assert interface_contract["rules"]
        weather_module = next(
            module for module in interface_contract["modules"] if module["path"] == "src/models/weather.py"
        )
        assert weather_module["role"] == "domain_model"
        assert "weather" in weather_module["owner_terms"]
        assert "Weather" in weather_module["planned_public_symbols"]
        forecast_module = next(
            module for module in interface_contract["modules"] if module["path"] == "src/engine/forecast.py"
        )
        assert forecast_module["role"] == "core_engine"
        assert "forecast" in forecast_module["planned_public_symbols"]
        assert persisted["context"]["module_interface_contract"] == interface_contract

    def test_generate_task_blueprint_uses_javascript_module_symbols_not_class_stubs(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-JS-METEOR",
            workspace=str(tmp_path),
            objective="Implement meteor wish queue JavaScript source modules",
            context={
                "task_title": "Meteor wish queue source modules",
                "language": "javascript",
                "target_files": [
                    "src/engine/rules.js",
                    "src/engine/runner.js",
                    "src/meteor.js",
                    "src/wish.js",
                    "src/queue.js",
                    "src/priority.js",
                ],
                "acceptance_criteria": [
                    "meteor, wish, queue, and priority behavior tests pass",
                    "npm test returns success",
                ],
                "execution_checklist": [
                    "Implement source modules",
                    "Keep engine imports aligned with owner modules",
                ],
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "language": "javascript",
                    "product_summary": {
                        "intent": "Deliver a meteor wish queue with observable behavior.",
                        "core_terms": ["meteor", "wish", "queue", "priority"],
                    },
                },
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "language": "javascript",
                    "product_intent": {
                        "subject": "meteor wish queue",
                        "primary_entities": ["meteor", "wish", "queue", "priority"],
                    },
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        interface_contract = persisted["module_interface_contract"]
        meteor_module = next(module for module in interface_contract["modules"] if module["path"] == "src/meteor.js")
        assert "meteor" in meteor_module["owner_terms"]
        assert "createMeteor" in meteor_module["planned_public_symbols"]
        assert "validateMeteor" in meteor_module["planned_public_symbols"]
        assert "Meteor" not in meteor_module["planned_public_symbols"]
        assert meteor_module["symbol_source"] == "heuristic_path_guess"
        assert meteor_module["symbol_confidence"] == 0.35
        queue_module = next(module for module in interface_contract["modules"] if module["path"] == "src/queue.js")
        assert "createQueue" in queue_module["planned_public_symbols"]
        assert "Queue" in queue_module["planned_public_symbols"]

    def test_generate_task_blueprint_blocks_duplicate_owner_when_existing_exports_disagree(self, tmp_path) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-JS-METEOR-MODELS",
            workspace=str(tmp_path),
            objective="Implement meteor wish queue JavaScript model mirror",
            context={
                "task_title": "Meteor wish queue model mirror",
                "language": "javascript",
                "target_files": ["src/models/meteor.js"],
                "acceptance_criteria": ["verify src/models/meteor.js exists"],
                "execution_checklist": ["Implement source module", "Run npm test"],
                "existing_target_files": [
                    {
                        "path": "src/meteor.js",
                        "exports": "export function createMeteor(options = {}) {}\n"
                        "export function advanceMeteor(meteor, target) {}\n",
                    }
                ],
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "language": "javascript",
                    "product_summary": {
                        "intent": "Deliver a meteor wish queue with observable behavior.",
                        "core_terms": ["meteor", "wish"],
                    },
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["handoff_ready"] is False
        assert any(
            "module_interface_contract owner conflict" in item
            for item in persisted["contract_completeness"]["semantic_blockers"]
        )
        interface_contract = persisted["module_interface_contract"]
        assert interface_contract["interface_conflicts"][0]["planned_path"] == "src/models/meteor.js"
        assert interface_contract["interface_conflicts"][0]["actual_owner_path"] == "src/meteor.js"
        meteor_module = interface_contract["modules"][0]
        assert meteor_module["symbol_source"] == "heuristic_path_guess_with_actual_owner_conflict"
        assert meteor_module["interface_conflict"]["actual_public_symbols"] == ["createMeteor", "advanceMeteor"]

    def test_generate_task_blueprint_uses_workspace_symbol_index_when_context_lacks_existing_exports(
        self, tmp_path
    ) -> None:
        source = tmp_path / "src" / "meteor.js"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "export function createMeteor(options = {}) { return options; }\n"
            "export function advanceMeteor(meteor, target) { return { meteor, target }; }\n",
            encoding="utf-8",
        )
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="TASK-JS-METEOR-MODELS",
            workspace=str(tmp_path),
            objective="Implement meteor wish queue JavaScript model mirror",
            context={
                "task_title": "Meteor wish queue model mirror",
                "language": "javascript",
                "target_files": ["src/models/meteor.js"],
                "acceptance_criteria": ["verify src/models/meteor.js exists"],
                "execution_checklist": ["Implement source module", "Run npm test"],
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "language": "javascript",
                    "product_summary": {
                        "intent": "Deliver a meteor wish queue with observable behavior.",
                        "core_terms": ["meteor", "wish"],
                    },
                },
            },
        )

        result = generate_task_blueprint(cmd)

        assert result.ok is True
        assert any(item["path"] == "src/meteor.js" for item in result.existing_target_files)
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["handoff_ready"] is False
        interface_contract = persisted["module_interface_contract"]
        assert interface_contract["actual_interface_snapshot_sources"] == ["workspace_symbol_index"]
        assert interface_contract["actual_interface_snapshot_file_count"] >= 1
        assert interface_contract["interface_conflicts"][0]["actual_owner_path"] == "src/meteor.js"
        assert interface_contract["interface_conflicts"][0]["actual_public_symbols"] == [
            "createMeteor",
            "advanceMeteor",
        ]

        status = get_blueprint_status(
            GetBlueprintStatusQueryV1(task_id="TASK-JS-METEOR-MODELS", workspace=str(tmp_path))
        )
        assert status.ok is True
        assert any(item["path"] == "src/meteor.js" for item in status.existing_target_files)
        assert status.module_interface_contract["interface_conflicts"][0]["actual_owner_path"] == "src/meteor.js"

    def test_query_missing_task_blueprint(self, tmp_path) -> None:
        status = get_blueprint_status(
            GetBlueprintStatusQueryV1(
                task_id="missing",
                workspace=str(tmp_path),
            )
        )

        assert status.ok is False
        assert status.status == "missing"
