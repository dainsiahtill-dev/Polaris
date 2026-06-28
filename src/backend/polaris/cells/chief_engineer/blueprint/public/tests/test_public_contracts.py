"""Unit tests for `chief_engineer/blueprint` public contracts."""

from __future__ import annotations

import pytest
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ChiefEngineerBlueprintErrorV1,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    HandoffDecisionV1,
    RegisterRiskCommandV1,
    RegisterTechDebtCommandV1,
    RiskRecordV1,
    RiskSeverity,
    RiskStatus,
    TaskBlueprintGeneratedEventV1,
    TaskBlueprintResultV1,
)
from polaris.cells.chief_engineer.blueprint.public.service import (
    BlueprintPersistence,
    build_ce_handoff_decision,
    generate_task_blueprint,
    get_blueprint_status,
)


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
            reason="legacy gate allowed",
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

    def test_generate_task_blueprint_promotes_nested_construction_plan_files(self, tmp_path) -> None:
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
        assert "src/engine/types.ts" in result.target_files
        assert "src/engine/simulation.ts" in result.target_files
        assert "src/engine/renderer.ts" in result.target_files
        assert "../outside.ts" not in result.target_files
        assert "/tmp/absolute.ts" not in result.target_files
        assert "npm run build" not in result.target_files
        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
        assert isinstance(persisted, dict)
        assert persisted["target_files"] == list(result.target_files)
        assert persisted["context"]["target_files"] == persisted["target_files"]
        assert "src/engine/types.ts" in persisted["scope_paths"]
        assert "src/engine/types.ts" in persisted["llm_blueprint"]["projected_target_files"]
        module_paths = {
            module["path"] for module in persisted["module_interface_contract"]["modules"] if isinstance(module, dict)
        }
        assert "src/engine/types.ts" in module_paths

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

    def test_query_missing_task_blueprint(self, tmp_path) -> None:
        status = get_blueprint_status(
            GetBlueprintStatusQueryV1(
                task_id="missing",
                workspace=str(tmp_path),
            )
        )

        assert status.ok is False
        assert status.status == "missing"
