"""Characterization tests for Chief Engineer handoff + schema-repair guards (part 1)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BlueprintPersistence,
    ChiefEngineerPortfolioTaskV1,
    ChiefEngineerSemanticRepairCandidateV1,
    chief_engineer_semantic_repair_task_set_hash,
    project_completion_catalog_snapshot_hash,
)
from polaris.cells.factory.pipeline.internal import (
    factory_stage_executor as stage_executor_module,
)
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.tests._characterization_helpers import (
    _assert_no_chief_engineer_lease_keeper_threads,
    _capture_chief_engineer_lease_keepers,
    _executor,
    _factory_stage_context,
    _invalid_chief_engineer_stream_result,
    _invalid_structured_transport_chief_engineer_result,
    _library_completion_requirements,
    _single_task_chief_engineer_result,
    _thinking_only_chief_engineer_result,
    _write_minimal_chief_engineer_plan,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_logical_path


def _semantic_artifact_patch_result(
    command: Any,
    *,
    path: str,
    obligation_id: str,
) -> Any:
    result = _single_task_chief_engineer_result()
    payload = {
        "base_candidate_hash": command.context["chief_engineer_semantic_repair_base_candidate_hash"],
        "diagnosis_hash": command.context["chief_engineer_semantic_repair_diagnosis_hash"],
        "artifact_upserts": [
            {
                "obligation_id": obligation_id,
                "path": path,
                "semantic_role": "source",
                "applicability": "required",
                "owner_task_id": "TASK-CANCEL",
            }
        ],
        "entrypoint_upserts": [],
        "behavior_invariant_upserts": [],
        "task_behavior_ref_replacements": {},
    }
    result.output = json.dumps(payload)
    result.metadata["structured_output"] = payload
    return result


class TestChiefEngineerHandoffGuards:
    def test_structural_recovery_saves_provider_call_and_emits_hash_only_signal(self, tmp_path: Path) -> None:
        executor = OrchestrationStageExecutor(tmp_path)
        valid = _single_task_chief_engineer_result().metadata["structured_output"]
        malformed = json.loads(json.dumps(valid))
        malformed["shared_behavior_contract"] = malformed["construction_plan"].pop("shared_behavior_contract")
        failed = _invalid_structured_transport_chief_engineer_result()
        failed.metadata["tool_call"] = {
            "tool": "submit_structured_role_output",
            "arguments": malformed,
        }

        recovered = executor._recover_chief_engineer_portfolio_structural_result(
            result=failed,
            portfolio_task_ids=("TASK-CANCEL",),
        )
        signals: list[dict[str, Any]] = []
        executor._append_chief_engineer_structural_recovery_signal(
            result=recovered,
            stage_signals=signals,
            task_id="CE-PORTFOLIO-test",
        )

        assert recovered.ok is True
        assert recovered.metadata["structured_output"] == valid
        assert recovered.metadata["tool_call"]["arguments"] == valid
        assert signals == [
            {
                "code": "chief_engineer.portfolio_structural_recovered",
                "severity": "warning",
                "detail": "Relocated CE tool arguments and revalidated the exact portfolio schema.",
                "task_id": "CE-PORTFOLIO-test",
                "source_hash": recovered.metadata["chief_engineer_portfolio_structural_recovery"]["source_hash"],
                "recovered_hash": recovered.metadata["chief_engineer_portfolio_structural_recovery"]["recovered_hash"],
                "repair_codes": ["move_root_shared_behavior_contract"],
                "provider_call_consumed": False,
            }
        ]

    def test_structural_recovery_refuses_payload_that_remains_schema_invalid(self, tmp_path: Path) -> None:
        executor = OrchestrationStageExecutor(tmp_path)
        failed = _invalid_structured_transport_chief_engineer_result()
        failed.metadata["tool_call"] = {
            "tool": "submit_structured_role_output",
            "arguments": {
                "construction_plan": {
                    "task_plans": {},
                    "project_interface_contract": {
                        "provider_declarations": [],
                        "consumer_declarations": [],
                    },
                },
                "shared_behavior_contract": {"invariants": []},
            },
        }

        recovered = executor._recover_chief_engineer_portfolio_structural_result(
            result=failed,
            portfolio_task_ids=("TASK-CANCEL",),
        )

        assert recovered is failed

    def test_structural_recovery_accepts_ce_provider_item_wrappers_before_schema_retry(self, tmp_path: Path) -> None:
        executor = OrchestrationStageExecutor(tmp_path)
        valid = _single_task_chief_engineer_result().metadata["structured_output"]
        malformed = json.loads(json.dumps(valid))
        task_plan = malformed["construction_plan"]["task_plans"]["TASK-CANCEL"]
        task_plan["behavior_invariant_refs"] = ["INV-1", "INV-2"]
        malformed["construction_plan"]["task_plans"]["TASK-OTHER"] = {
            "behavior_invariant_refs": ["INV-1", "INV-2"],
            "implementation": ["Consume the shared behavior"],
            "verification": ["Verify the shared behavior"],
        }
        malformed["construction_plan"]["shared_behavior_contract"]["item"] = {
            "invariant_id": "INV-1",
            "statement": "A lifted singleton remains behaviorally stable.",
            "owner_task_id": "TASK-CANCEL",
            "consumer_task_ids": {"item": ["TASK-CANCEL"]},
            "covered_obligation_ids": {"item": ["artifact-1"]},
            "verification_examples": {"item": {"given": "input", "when": "processed", "then": "stable"}},
        }
        malformed["construction_plan"]["item"] = [
            {
                "invariant_id": "INV-2",
                "statement": "A lifted array member remains behaviorally stable.",
                "owner_task_id": "TASK-CANCEL",
                "consumer_task_ids": {"item": ["TASK-CANCEL"]},
                "covered_obligation_ids": {"item": ["artifact-1"]},
                "verification_examples": {"item": {"given": "input", "when": "processed", "then": "stable"}},
            }
        ]
        malformed["construction_plan"]["TASK-CANCEL"] = {
            "behavior_invariant_refs": {"item": ["INV-1", "INV-2"]},
        }
        failed = _invalid_structured_transport_chief_engineer_result()
        failed.metadata["tool_call"] = {
            "tool": "submit_structured_role_output",
            "arguments": malformed,
        }

        recovered = executor._recover_chief_engineer_portfolio_structural_result(
            result=failed,
            portfolio_task_ids=("TASK-CANCEL", "TASK-OTHER"),
        )

        assert recovered.ok is True
        construction = recovered.metadata["structured_output"]["construction_plan"]
        assert set(construction) == {
            "project_design_intent",
            "project_interface_contract",
            "shared_behavior_contract",
            "task_plans",
        }
        assert len(construction["shared_behavior_contract"]["invariants"]) == 2
        assert set(recovered.metadata["chief_engineer_portfolio_structural_recovery"]["repair_codes"]) >= {
            "move_shared_behavior_item_to_invariants",
            "move_construction_items_to_behavior_invariants",
            "remove_redundant_lifted_task_plan",
            "unwrap_behavior_invariant_array_items",
        }

    def test_structural_recovery_normalizes_schema_valid_success_before_business_validation(
        self, tmp_path: Path
    ) -> None:
        executor = OrchestrationStageExecutor(tmp_path)
        result = _single_task_chief_engineer_result()
        payload = result.metadata["structured_output"]
        payload["construction_plan"]["task_plans"]["TASK-OTHER"] = {
            "behavior_invariant_refs": ["INV-1"],
            "implementation": ["Consume cancellation behavior"],
            "verification": ["Verify cancellation behavior"],
        }
        payload["construction_plan"]["task_plans"]["TASK-CANCEL"]["behavior_invariant_refs"] = ["INV-1"]
        payload["construction_plan"]["shared_behavior_contract"]["invariants"] = [
            {
                "invariant_id": "INV-1",
                "statement": "Consumers observe cancellation.",
                "owner_task_id": "TASK-CANCEL",
                "consumer_task_ids": ["TASK-CANCEL", "TASK-OTHER"],
                "covered_obligation_ids": ["ART-SRC"],
                "verification_examples": [{"given": "cancelled", "when": "read", "then": "observed"}],
            }
        ]

        recovered = executor._recover_chief_engineer_portfolio_structural_result(
            result=result,
            portfolio_task_ids=("TASK-CANCEL", "TASK-OTHER"),
        )

        assert recovered is not result
        invariant = recovered.metadata["structured_output"]["construction_plan"]["shared_behavior_contract"][
            "invariants"
        ][0]
        assert invariant["consumer_task_ids"] == ["TASK-OTHER"]
        assert recovered.metadata["chief_engineer_portfolio_structural_recovery"]["repair_codes"] == [
            "remove_behavior_owner_from_consumers"
        ]

    def test_semantic_diagnosis_uses_stable_codes_and_minimal_operations(self, tmp_path: Path) -> None:
        task_ids = ("TASK-1",)
        candidate = ChiefEngineerSemanticRepairCandidateV1(
            workspace=str(tmp_path),
            project_id="project-1",
            run_id="factory-run-candidate",
            pm_contract_hash="a" * 64,
            task_ids=task_ids,
            task_set_hash=chief_engineer_semantic_repair_task_set_hash(task_ids),
            candidate={
                "construction_plan": {"task_plans": {}},
                "project_completion_contract": {"obligations": {}},
                "risk_flags": [],
            },
        )

        diagnosis = OrchestrationStageExecutor._chief_engineer_semantic_repair_diagnosis(
            candidate=candidate,
            output_errors=[
                "project_completion_contract delivery depth infeasible: prod_files=1 < 2",
                "shared_behavior_contract.invariants missing owner binding",
            ],
        )

        assert diagnosis.diagnostic_codes == (
            "chief_engineer.delivery_depth.prod_files_below_minimum",
            "chief_engineer.shared_behavior_contract.invalid",
        )
        assert diagnosis.allowed_operations == (
            "artifact_upsert",
            "behavior_invariant_upsert",
            "task_behavior_ref_replace",
        )

    def test_semantic_diagnosis_preserves_cross_task_behavior_coverage_failure_class(self) -> None:
        task_ids = ("TASK-PROD", "TASK-TEST")
        candidate = ChiefEngineerSemanticRepairCandidateV1(
            workspace="/tmp/workspace",
            project_id="L3-22",
            run_id="factory-test",
            pm_contract_hash="a" * 64,
            task_ids=task_ids,
            task_set_hash=chief_engineer_semantic_repair_task_set_hash(task_ids),
            candidate={
                "construction_plan": {"task_plans": {}},
                "project_completion_contract": {"obligations": {}},
                "risk_flags": [],
            },
        )

        diagnosis = OrchestrationStageExecutor._chief_engineer_semantic_repair_diagnosis(
            candidate=candidate,
            output_errors=[
                "shared_behavior_contract behavior invariant lacks cross-task "
                "production-and-test obligation coverage: test_owner=TASK-TEST"
            ],
        )

        assert diagnosis.diagnostic_codes == (
            "chief_engineer.shared_behavior_contract.cross_task_production_test_coverage_missing",
        )
        assert diagnosis.allowed_operations == (
            "behavior_invariant_upsert",
            "task_behavior_ref_replace",
        )

    def test_test_depth_diagnosis_authorizes_atomic_cross_task_behavior_binding(self) -> None:
        """Exact L3-23 r06: new test artifacts must be bindable in the same patch."""

        task_ids = ("TASK-PROD", "TASK-TEST")
        candidate = ChiefEngineerSemanticRepairCandidateV1(
            workspace="/tmp/workspace",
            project_id="L3-23",
            run_id="factory-r06",
            pm_contract_hash="a" * 64,
            task_ids=task_ids,
            task_set_hash=chief_engineer_semantic_repair_task_set_hash(task_ids),
            candidate={
                "construction_plan": {"task_plans": {}},
                "project_completion_contract": {"obligations": {}},
                "risk_flags": [],
            },
        )

        diagnosis = OrchestrationStageExecutor._chief_engineer_semantic_repair_diagnosis(
            candidate=candidate,
            output_errors=[
                "project_completion_contract delivery depth infeasible: test_files=1 < 2"
            ],
        )

        assert diagnosis.diagnostic_codes == (
            "chief_engineer.delivery_depth.test_files_below_minimum",
            "chief_engineer.shared_behavior_contract.cross_task_production_test_coverage_missing",
        )
        assert diagnosis.allowed_operations == (
            "artifact_upsert",
            "behavior_invariant_upsert",
            "task_behavior_ref_replace",
        )

    def test_portfolio_validation_allows_missing_advisory_task_plan_overlays(self) -> None:
        payload = dict(_single_task_chief_engineer_result().metadata["structured_output"])
        construction_plan = dict(payload["construction_plan"])
        construction_plan.pop("task_plans")
        construction_plan["project_interface_contract"] = {}
        payload["construction_plan"] = construction_plan

        errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
            payload,
            task_ids=("TASK-CANCEL",),
        )

        assert errors == []

    def test_portfolio_validation_still_rejects_unknown_task_plan_overlay(self) -> None:
        payload = dict(_single_task_chief_engineer_result().metadata["structured_output"])
        construction_plan = dict(payload["construction_plan"])
        construction_plan["task_plans"] = {
            "TASK-UNKNOWN": {"behavior_invariant_refs": [], "implementation": ["Do not execute"]}
        }
        payload["construction_plan"] = construction_plan

        errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
            payload,
            task_ids=("TASK-CANCEL",),
        )

        assert errors == ["task_plans contains unknown task ids: TASK-UNKNOWN"]

    def test_portfolio_schema_requires_shared_behavior_contract_and_task_refs(self) -> None:
        contract = OrchestrationStageExecutor._chief_engineer_structured_output_contract(("TASK-A", "TASK-B"))
        schema = contract.json_schema
        construction = schema["properties"]["construction_plan"]

        assert "shared_behavior_contract" in construction["required"]
        assert construction["properties"]["shared_behavior_contract"]["required"] == ["invariants"]
        task_plans = construction["properties"]["task_plans"]["properties"]
        assert task_plans["TASK-A"]["required"] == ["behavior_invariant_refs"]
        assert task_plans["TASK-B"]["required"] == ["behavior_invariant_refs"]

    def test_portfolio_transport_rejects_missing_shared_behavior_contract(self) -> None:
        payload = dict(_single_task_chief_engineer_result().metadata["structured_output"])
        construction_plan = dict(payload["construction_plan"])
        construction_plan.pop("shared_behavior_contract")
        payload["construction_plan"] = construction_plan

        errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
            payload,
            task_ids=("TASK-CANCEL",),
        )

        assert errors == ["construction_plan.shared_behavior_contract must be an object"]

    def test_portfolio_output_rejects_behavior_owner_repeated_as_consumer(self) -> None:
        payload = json.loads(json.dumps(_single_task_chief_engineer_result().metadata["structured_output"]))
        payload["construction_plan"]["shared_behavior_contract"] = {
            "invariants": [
                {
                    "invariant_id": "INV-CANCEL",
                    "statement": "Cancellation remains observable across task boundaries.",
                    "owner_task_id": "TASK-CANCEL",
                    "consumer_task_ids": ["TASK-CANCEL"],
                    "covered_obligation_ids": ["artifact-source"],
                    "verification_examples": [
                        {
                            "given": "an active task",
                            "when": "cancellation is requested",
                            "then": "the task reports cancellation",
                        }
                    ],
                }
            ]
        }

        errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
            payload,
            task_ids=("TASK-CANCEL",),
        )

        assert errors == [
            "shared_behavior_contract.invariants[0] invalid: consumer_task_ids must not contain owner_task_id"
        ]

    def test_portfolio_output_reports_unknown_behavior_obligations_with_depth_deficits(self) -> None:
        payload = json.loads(json.dumps(_single_task_chief_engineer_result().metadata["structured_output"]))
        payload["construction_plan"]["shared_behavior_contract"] = {
            "invariants": [
                {
                    "invariant_id": "INV-CANCEL",
                    "statement": "Cancellation remains observable across task boundaries.",
                    "owner_task_id": "TASK-CANCEL",
                    "consumer_task_ids": ["TASK-VERIFY"],
                    "covered_obligation_ids": ["OBL-UNKNOWN"],
                    "verification_examples": [
                        {
                            "given": "an active task",
                            "when": "cancellation is requested",
                            "then": "the task reports cancellation",
                        }
                    ],
                }
            ]
        }
        task = ChiefEngineerPortfolioTaskV1(
            task_id="TASK-CANCEL",
            objective="Deliver a real project",
            target_files=("src/cancel.py", "tests/test_cancel.py"),
            scope_paths=("src/cancel.py", "tests/test_cancel.py"),
            delivery_depth_contract={"minimums": {"min_prod_files": 2, "min_test_files": 2}},
        )

        errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
            payload,
            task_ids=("TASK-CANCEL", "TASK-VERIFY"),
            tasks=(task,),
        )

        assert (
            "shared_behavior_contract.invariants[0].covered_obligation_ids reference unknown completion "
            "obligations: OBL-UNKNOWN"
        ) in errors
        assert "project_completion_contract delivery depth infeasible: prod_files=1 < 2" in errors
        assert "project_completion_contract delivery depth infeasible: test_files=1 < 2" in errors

    def test_portfolio_output_accepts_behavior_reference_to_verification_obligation(self) -> None:
        """Exact L3-22 r23/r24: verification obligations are completion obligations."""

        payload = json.loads(json.dumps(_single_task_chief_engineer_result().metadata["structured_output"]))
        verification_id = payload["project_completion_contract"]["obligations"]["verification"][0]["obligation_id"]
        payload["construction_plan"]["shared_behavior_contract"] = {
            "invariants": [
                {
                    "invariant_id": "INV-VERIFY",
                    "statement": "The shared behavior is proven by its command-backed verifier.",
                    "owner_task_id": "TASK-CANCEL",
                    "consumer_task_ids": ["TASK-VERIFY"],
                    "covered_obligation_ids": [verification_id],
                    "verification_examples": [
                        {
                            "given": "a completed implementation",
                            "when": "the verifier runs",
                            "then": "the shared behavior is observed",
                        }
                    ],
                }
            ]
        }

        errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
            payload,
            task_ids=("TASK-CANCEL", "TASK-VERIFY"),
        )

        assert not any("unknown completion obligations" in error for error in errors)

    def test_portfolio_output_routes_cross_task_behavior_coverage_gap_to_semantic_repair(self) -> None:
        """L3-22 r46: schema-valid invariants must connect production and test facts."""

        payload = json.loads(json.dumps(_single_task_chief_engineer_result().metadata["structured_output"]))
        payload["construction_plan"]["task_plans"] = {
            "TASK-PROD": {"behavior_invariant_refs": ["INV-SHARED"]},
            "TASK-TEST": {"behavior_invariant_refs": ["INV-SHARED"]},
        }
        payload["project_completion_contract"] = _library_completion_requirements(
            "src/product.py",
            owner_task_ids=("TASK-PROD",),
            test_path="tests/test_product.py",
            test_owner_task_id="TASK-TEST",
        )
        payload["construction_plan"]["shared_behavior_contract"] = {
            "invariants": [
                {
                    "invariant_id": "INV-SHARED",
                    "statement": "Tests observe the production behavior owned by TASK-PROD.",
                    "owner_task_id": "TASK-PROD",
                    "consumer_task_ids": ["TASK-TEST"],
                    # A test verifier expands only to the test artifact. It does
                    # not prove which production artifact owns the behavior.
                    "covered_obligation_ids": ["verify-test"],
                    "verification_examples": [
                        {
                            "given": "a production implementation",
                            "when": "the test verifier runs",
                            "then": "the shared behavior is observed",
                        }
                    ],
                }
            ]
        }

        errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
            payload,
            task_ids=("TASK-PROD", "TASK-TEST"),
        )

        assert errors == [
            "shared_behavior_contract behavior invariant lacks cross-task production-and-test obligation coverage: "
            "test_owner=TASK-TEST"
        ]

        payload["construction_plan"]["shared_behavior_contract"]["invariants"][0]["covered_obligation_ids"] = [
            "artifact-1",
            "verify-test",
        ]

        assert (
            OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
                payload,
                task_ids=("TASK-PROD", "TASK-TEST"),
            )
            == []
        )

    def test_portfolio_validation_rejects_delivery_depth_authority_deficit(self) -> None:
        payload = dict(_single_task_chief_engineer_result().metadata["structured_output"])
        task = ChiefEngineerPortfolioTaskV1(
            task_id="TASK-CANCEL",
            objective="Deliver a real project",
            target_files=("src/cancel.py", "tests/test_cancel.py"),
            scope_paths=("src/cancel.py", "tests/test_cancel.py"),
            delivery_depth_contract={"minimums": {"min_prod_files": 2, "min_test_files": 2}},
        )

        errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
            payload,
            tasks=(task,),
        )

        assert "project_completion_contract delivery depth infeasible: prod_files=1 < 2" in errors
        assert "project_completion_contract delivery depth infeasible: test_files=1 < 2" in errors

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
                        "shared_behavior_contract": {"invariants": []},
                        "task_plans": {
                            "TASK-1": {
                                "behavior_invariant_refs": [],
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

        assert result.status == "success", result.output
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
        assert task_plans_schema.get("required", []) == []
        assert task_plans_schema["additionalProperties"] is False
        assert command.structured_output_contract.json_schema["properties"]["construction_plan"]["required"] == [
            "project_interface_contract",
            "shared_behavior_contract",
        ]
        project_interface_schema = command.structured_output_contract.json_schema["properties"]["construction_plan"][
            "properties"
        ]["project_interface_contract"]
        assert set(project_interface_schema["properties"]) == {
            "provider_declarations",
            "consumer_declarations",
        }
        # Both lists are CE advisory evidence.  The owner parser defaults an
        # omitted list to [], then _project_interface_seed projects the full
        # authoritative ownership carrier.  Provider transport must not reject
        # that already-supported omission before owner normalization runs.
        assert project_interface_schema.get("required", []) == []
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
                "language": "python",
                "target_files": ["src/owner.py"],
                "project_declared_entrypoint_targets": ["src/owner.py", "src/other-owner.py"],
                "metadata": {
                    "topology_authority": "chief_engineer",
                    "required_source_kinds": ["domain_modules", "entrypoint"],
                },
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
        assert portfolio_tasks[0].entrypoint_targets == ("src/owner.py",)
        assert portfolio_tasks[0].topology_authority == "chief_engineer"
        assert portfolio_tasks[0].required_source_kinds == ("domain_modules", "entrypoint")
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
        # The primary CE attempt remains streaming; bounded schema repair is
        # atomic so only a complete forced result-tool payload is validated.
        assert repair_command.stream is False
        assert repair_command.metadata["max_retries"] == 0
        assert repair_command.metadata["validate_output"] is True
        assert repair_command.metadata["temperature"] == 0.0
        assert repair_command.metadata["llm_max_tokens"] == 8_192
        assert repair_command.metadata["reasoning_budget_tokens"] == 2_048
        assert repair_command.context["chief_engineer_schema_repair"] is True
        assert repair_command.context["llm_max_tokens"] == 8_192
        assert repair_command.context["reasoning_budget_tokens"] == 2_048
        assert repair_command.context["language"] == "python"
        assert repair_command.context["task_type"] == "implement"
        assert repair_command.context["prompt_stage"] == "blueprint"
        assert repair_command.context["artifact"] == "library"
        assert (
            repair_command.context["chief_engineer_schema_repair_prompt_profile_source"]
            == "primary_final_request_context_audit"
        )
        assert repair_command.metadata["inherited_prompt_profile_identity"] == {
            "language": "python",
            "task_type": "implement",
            "prompt_stage": "blueprint",
            "artifact": "library",
        }
        assert repair_command.structured_output_contract is not None
        repair_task_plans_schema = repair_command.structured_output_contract.json_schema["properties"][
            "construction_plan"
        ]["properties"]["task_plans"]
        assert repair_task_plans_schema.get("required", []) == []

        assert repair_task_plans_schema["additionalProperties"] is False
        assert repair_command.context["failure_feedback"] == {
            "schema_version": "factory.chief_engineer_schema_repair.failure_evidence.v1",
            "failure_class": "output_validation_failed",
            "failure_stage": "chief_engineer_review",
            "detail": "Output validation failed: malformed chief engineer JSON",
            "prior_output_sha256": hashlib.sha256(invalid_output.encode("utf-8")).hexdigest(),
            "prior_output_chars": len(invalid_output),
            "evidence_refs": [],
            "delivery_depth_minimums": {},
            "observed_invalid_root_members": [],
            "expected_root_members": [
                "construction_plan",
                "project_completion_contract",
                "risk_flags",
                "scope_for_apply",
            ],
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
        assert primary_session["status"] == "completed"
        assert repair_session["status"] == "completed"
        assert primary_task["status"] == "completed"
        assert repair_task["status"] == "completed"

        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["llm_call_count"] == 2
        assert [signal["code"] for signal in review["signals"]] == [
            "chief_engineer.output_schema_repair_started",
            "chief_engineer.structured_candidate_persisted",
        ]
        assert len(keepers) == 2
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_final_structural_repair_restores_full_portfolio_output_budget(self) -> None:
        """A final full reconstruction must not inherit the 8K patch ceiling.

        The first structural repair remains deliberately small.  When it still
        omits a required portfolio section, round two has to reconstruct the
        same multi-task portfolio as the primary CE call and therefore needs
        the task-scaled portfolio budget.  Typed semantic patches remain small.
        """

        assert (
            OrchestrationStageExecutor._chief_engineer_schema_repair_output_tokens(
                task_count=3,
                repair_round=1,
                semantic_patch=False,
            )
            == 8_192
        )
        assert (
            OrchestrationStageExecutor._chief_engineer_schema_repair_output_tokens(
                task_count=3,
                repair_round=2,
                semantic_patch=False,
            )
            == 16_384
        )
        assert (
            OrchestrationStageExecutor._chief_engineer_schema_repair_output_tokens(
                task_count=3,
                repair_round=2,
                semantic_patch=True,
            )
            == 8_192
        )

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
            "delivery_depth_minimums": {},
            "observed_invalid_root_members": [],
            "expected_root_members": [
                "construction_plan",
                "project_completion_contract",
                "risk_flags",
                "scope_for_apply",
            ],
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

    def test_chief_engineer_schema_valid_depth_deficit_uses_bounded_repair(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A schema-valid but infeasible completion contract gets one CE-local repair."""

        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-CANCEL",
                        "title": "Implement cancellation coverage",
                        "goal": "Exercise the Chief Engineer cancellation path.",
                        "language": "python",
                        "target_files": [
                            "src/cancel.py",
                            "src/cancel_policy.py",
                            "tests/test_cancel.py",
                        ],
                        "scope_paths": ["src", "tests"],
                        "acceptance_criteria": ["cancellation is observable"],
                        "execution_checklist": ["Suspend the claimed attempt"],
                        "delivery_depth_contract": {"minimums": {"min_prod_files": 2, "min_test_files": 1}},
                        "metadata": {
                            "topology_authority": "chief_engineer",
                            "required_source_kinds": ["domain_modules", "tests"],
                        },
                    }
                ]
            },
        )
        primary = _single_task_chief_engineer_result()
        commands: list[Any] = []

        class _RepairingRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                if len(commands) == 1:
                    return primary
                return _semantic_artifact_patch_result(
                    command,
                    path="src/cancel_policy.py",
                    obligation_id="artifact-cancel-policy",
                )

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _RepairingRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-semantic-depth-repair",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-21T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success", result.output
        assert [command.task_id for command in commands] == [
            f"CE-PORTFOLIO-{run.id}",
            f"CE-PORTFOLIO-{run.id}-SEMANTIC-PATCH",
        ]
        feedback = commands[1].context["failure_feedback"]
        assert feedback["failure_class"] == "output_validation_failed"
        assert "delivery depth infeasible: prod_files=1 < 2" in feedback["detail"]
        assert commands[1].structured_output_contract.schema_name == "chief_engineer_semantic_repair_patch"
        assert commands[1].context["chief_engineer_semantic_repair"] is True
        assert commands[1].context["chief_engineer_semantic_repair_candidate"]["candidate_hash"]
        assert commands[1].context["chief_engineer_semantic_repair_diagnosis"]["diagnostic_codes"] == [
            "chief_engineer.delivery_depth.prod_files_below_minimum"
        ]
        assert "allowed_completion_obligation_ids" in commands[1].objective
        assert "never copy diagnostic prose" in commands[1].objective
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["generated_blueprints"] == 1
        assert review["llm_call_count"] == 2
        assert review["signals"][0]["code"] == "chief_engineer.output_contract_repair_started"
        assert len(keepers) == 2
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_semantic_repair_fails_before_provider_when_pm_authority_is_infeasible(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An occupied exact-only PM scope cannot authorize a fabricated repair path."""

        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-CANCEL",
                        "title": "Implement cancellation coverage",
                        "goal": "Exercise the Chief Engineer cancellation path.",
                        "target_files": ["src/cancel.py"],
                        "scope_paths": ["src/cancel.py"],
                        "acceptance_criteria": ["cancellation is observable"],
                        "execution_checklist": ["Implement the exact target"],
                        "delivery_depth_contract": {"minimums": {"min_prod_files": 2}},
                        "metadata": {"topology_authority": "pm", "required_source_kinds": []},
                    }
                ]
            },
        )
        commands: list[Any] = []

        class _SingleProviderCallRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                return _single_task_chief_engineer_result()

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _SingleProviderCallRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-semantic-authority-infeasible",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-23T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "failed"
        assert result.metadata["error_code"] == "chief_engineer.semantic_repair_authority_infeasible"
        assert [command.task_id for command in commands] == [f"CE-PORTFOLIO-{run.id}"]
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["llm_call_count"] == 1
        assert review["signals"][-1]["code"] == "chief_engineer.semantic_repair_authority_infeasible"
        assert "chief_engineer.output_contract_repair_started" not in {signal["code"] for signal in review["signals"]}
        assert len(keepers) == 1
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_final_contract_repair_closes_remaining_depth_deficit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-CANCEL",
                        "title": "Implement cancellation coverage",
                        "goal": "Exercise the Chief Engineer cancellation path.",
                        "language": "python",
                        "target_files": ["src/cancel.py", "tests/test_cancel.py"],
                        "scope_paths": ["src", "tests"],
                        "acceptance_criteria": ["cancellation is observable"],
                        "execution_checklist": ["Suspend the claimed attempt"],
                        "delivery_depth_contract": {"minimums": {"min_prod_files": 2, "min_test_files": 1}},
                        "metadata": {
                            "topology_authority": "chief_engineer",
                            "required_source_kinds": ["domain_modules", "tests"],
                        },
                    }
                ]
            },
        )
        first = _single_task_chief_engineer_result()
        commands: list[Any] = []

        class _EventuallyFeasibleRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                if len(commands) == 1:
                    return first
                if len(commands) == 2:
                    return _single_task_chief_engineer_result()
                return _semantic_artifact_patch_result(
                    command,
                    path="src/support.py",
                    obligation_id="artifact-support",
                )

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _EventuallyFeasibleRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-semantic-depth-final-repair",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-23T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success", result.output
        assert [command.task_id for command in commands] == [
            f"CE-PORTFOLIO-{run.id}",
            f"CE-PORTFOLIO-{run.id}-SEMANTIC-PATCH",
            f"CE-PORTFOLIO-{run.id}-SEMANTIC-PATCH-REPAIR-2",
        ]
        assert (
            commands[1].context["chief_engineer_semantic_repair_base_candidate_hash"]
            == commands[2].context["chief_engineer_semantic_repair_base_candidate_hash"]
        )
        assert (
            commands[1].context["chief_engineer_semantic_repair_diagnosis_hash"]
            == commands[2].context["chief_engineer_semantic_repair_diagnosis_hash"]
        )
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["generated_blueprints"] == 1
        assert review["llm_call_count"] == 3
        assert [signal["code"] for signal in review["signals"][:2]] == [
            "chief_engineer.output_contract_repair_started",
            "chief_engineer.output_contract_final_repair_started",
        ]
        assert len(keepers) == 3
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_final_repair_recovers_second_schema_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        _write_minimal_chief_engineer_plan(executor)
        first = _invalid_chief_engineer_stream_result()
        second = _invalid_chief_engineer_stream_result()
        second.error_message = (
            "structured_output_payload_schema_mismatch:$:'project_completion_contract' is a required property"
        )
        # A transport/normalization failure may not carry the final-request
        # audit back in RoleExecutionResult. Later bounded repair must retain
        # the primary call's exact profile identity instead of re-inferring or
        # masking the physical failure with profile-identity-missing.
        second.metadata = {"provider_id": "test-provider", "model": "test-model"}
        results = [first, second, _single_task_chief_engineer_result()]
        commands: list[Any] = []

        class _EventuallySchemaValidRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                return results.pop(0)

        monkeypatch.setattr(
            stage_executor_module,
            "RoleRuntimeService",
            _EventuallySchemaValidRoleRuntimeService,
        )
        run = FactoryRun(
            id="factory-run-second-schema-failure-final-repair",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-23T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success", result.output
        assert [command.task_id for command in commands] == [
            f"CE-PORTFOLIO-{run.id}",
            f"CE-PORTFOLIO-{run.id}-SCHEMA-REPAIR",
            f"CE-PORTFOLIO-{run.id}-CONTRACT-REPAIR-2",
        ]
        final_feedback = commands[-1].context["failure_feedback"]
        assert "'project_completion_contract' is a required property" in final_feedback["detail"]
        assert commands[-1].metadata["inherited_prompt_profile_identity"] == {
            "language": "python",
            "task_type": "implement",
            "prompt_stage": "blueprint",
            "artifact": "library",
        }
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["generated_blueprints"] == 1
        assert review["llm_call_count"] == 3
        assert [signal["code"] for signal in review["signals"][:2]] == [
            "chief_engineer.output_schema_repair_started",
            "chief_engineer.output_contract_final_repair_started",
        ]
        assert len(keepers) == 3
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_final_schema_repair_gets_one_typed_semantic_patch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two schema failures must not strand a schema-valid semantic deficit."""

        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-CANCEL",
                        "title": "Implement cancellation coverage",
                        "goal": "Exercise the Chief Engineer cancellation path.",
                        "language": "python",
                        "target_files": ["src/cancel.py", "src/support.py", "tests/test_cancel.py"],
                        "scope_paths": ["src", "tests"],
                        "acceptance_criteria": ["cancellation is observable"],
                        "execution_checklist": ["Suspend the claimed attempt"],
                        "delivery_depth_contract": {"minimums": {"min_prod_files": 2, "min_test_files": 1}},
                        "metadata": {
                            "topology_authority": "chief_engineer",
                            "required_source_kinds": ["domain_modules", "tests"],
                        },
                    }
                ]
            },
        )
        first = _invalid_chief_engineer_stream_result()
        second = _invalid_chief_engineer_stream_result()
        second.error_message = (
            "structured_output_payload_schema_mismatch:$:'project_completion_contract' is a required property"
        )
        commands: list[Any] = []

        class _SchemaThenSemanticRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                if len(commands) == 1:
                    return first
                if len(commands) == 2:
                    return second
                if len(commands) == 3:
                    return _single_task_chief_engineer_result()
                return _semantic_artifact_patch_result(
                    command,
                    path="src/support.py",
                    obligation_id="artifact-support",
                )

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _SchemaThenSemanticRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-final-schema-then-semantic-repair",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-23T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success", result.output
        assert [command.task_id for command in commands] == [
            f"CE-PORTFOLIO-{run.id}",
            f"CE-PORTFOLIO-{run.id}-SCHEMA-REPAIR",
            f"CE-PORTFOLIO-{run.id}-CONTRACT-REPAIR-2",
            f"CE-PORTFOLIO-{run.id}-SEMANTIC-PATCH-REPAIR-3",
        ]
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["generated_blueprints"] == 1
        assert review["llm_call_count"] == 4
        assert [signal["code"] for signal in review["signals"][:3]] == [
            "chief_engineer.output_schema_repair_started",
            "chief_engineer.output_contract_final_repair_started",
            "chief_engineer.final_schema_candidate_semantic_repair_started",
        ]
        assert len(keepers) == 4
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_schema_repair_semantic_deficit_uses_typed_final_patch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """L3-22 r06: schema-invalid -> schema-valid depth deficit -> typed patch."""

        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-CANCEL",
                        "title": "Implement cancellation coverage",
                        "goal": "Exercise the Chief Engineer cancellation path.",
                        "language": "python",
                        "target_files": ["src/cancel.py", "tests/test_cancel.py"],
                        "scope_paths": ["src", "tests"],
                        "acceptance_criteria": ["cancellation is observable"],
                        "execution_checklist": ["Suspend the claimed attempt"],
                        "delivery_depth_contract": {"minimums": {"min_prod_files": 2, "min_test_files": 1}},
                        "metadata": {
                            "topology_authority": "chief_engineer",
                            "required_source_kinds": ["domain_modules", "tests"],
                        },
                    }
                ]
            },
        )
        commands: list[Any] = []

        class _R06RoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                if len(commands) == 1:
                    return _invalid_chief_engineer_stream_result()
                if len(commands) == 2:
                    return _single_task_chief_engineer_result()
                return _semantic_artifact_patch_result(
                    command,
                    path="src/support.py",
                    obligation_id="artifact-support",
                )

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _R06RoleRuntimeService)
        run = FactoryRun(
            id="factory-run-r06-schema-then-semantic",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-23T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success", result.output
        assert [command.task_id for command in commands] == [
            f"CE-PORTFOLIO-{run.id}",
            f"CE-PORTFOLIO-{run.id}-SCHEMA-REPAIR",
            f"CE-PORTFOLIO-{run.id}-SEMANTIC-PATCH-REPAIR-2",
        ]
        assert commands[1].structured_output_contract.schema_name == "chief_engineer_blueprint_portfolio"
        assert commands[2].structured_output_contract.schema_name == "chief_engineer_semantic_repair_patch"
        assert commands[2].context["chief_engineer_semantic_repair_diagnosis"]["diagnostic_codes"] == [
            "chief_engineer.delivery_depth.prod_files_below_minimum"
        ]
        provider_patch_context = commands[2].context["chief_engineer_semantic_repair_provider_context"]
        assert provider_patch_context["current"]["artifacts"][0]["obligation_id"] == "artifact-1"
        assert "artifact-1" in commands[2].objective
        assert "Call the required result-submission tool exactly once" in commands[2].objective
        assert "Return JSON only" not in commands[2].objective
        task_authority = provider_patch_context["task_authority"]["TASK-CANCEL"]
        assert task_authority["target_files"] == ["src/cancel.py", "tests/test_cancel.py"]
        assert task_authority["expandable_scope_paths"] == ["src", "tests"]
        assert task_authority["topology_authority"] == "chief_engineer"
        review = json.loads(
            Path(
                resolve_logical_path(
                    tmp_path,
                    f"runtime/state/blueprints/{run.id}.review.json",
                )
            ).read_text(encoding="utf-8")
        )
        assert review["llm_call_count"] == 3
        assert [signal["code"] for signal in review["signals"][:3]] == [
            "chief_engineer.output_schema_repair_started",
            "chief_engineer.schema_repair_candidate_frozen",
            "chief_engineer.output_contract_final_repair_started",
        ]
        assert len(keepers) == 3
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_semantic_repair_budget_is_two_bounded_calls(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-CANCEL",
                        "title": "Implement cancellation coverage",
                        "goal": "Exercise the Chief Engineer cancellation path.",
                        "language": "python",
                        "target_files": ["src/cancel.py", "tests/test_cancel.py"],
                        "scope_paths": ["src", "tests"],
                        "acceptance_criteria": ["cancellation is observable"],
                        "execution_checklist": ["Suspend the claimed attempt"],
                        "delivery_depth_contract": {"minimums": {"min_prod_files": 2, "min_test_files": 1}},
                        "metadata": {
                            "topology_authority": "chief_engineer",
                            "required_source_kinds": ["domain_modules", "tests"],
                        },
                    }
                ]
            },
        )
        commands: list[Any] = []

        class _StillInfeasibleRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                return _single_task_chief_engineer_result()

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _StillInfeasibleRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-semantic-depth-repair-bounded",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-21T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "failed"
        assert [command.task_id for command in commands] == [
            f"CE-PORTFOLIO-{run.id}",
            f"CE-PORTFOLIO-{run.id}-SEMANTIC-PATCH",
            f"CE-PORTFOLIO-{run.id}-SEMANTIC-PATCH-REPAIR-2",
        ]
        assert commands[-1].context["chief_engineer_repair_round"] == 2
        assert commands[-1].context["chief_engineer_semantic_repair_diagnosis"]["diagnostic_codes"] == [
            "chief_engineer.delivery_depth.prod_files_below_minimum"
        ]
        # A frozen semantic candidate plus typed diagnosis may retry one
        # transport-validation miss in place.  This is NOT another CE semantic
        # round: PM authority, candidate hash, diagnosis hash, claim, and role
        # remain unchanged.  Plain schema reconstruction stays at zero retries.
        assert commands[1].metadata["max_retries"] == 1
        assert commands[2].metadata["max_retries"] == 1
        assert result.metadata["error_code"] == "chief_engineer.semantic_repair_exhausted"
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["llm_call_count"] == 3
        assert [signal["code"] for signal in review["signals"]] == [
            "chief_engineer.output_contract_repair_started",
            "chief_engineer.output_contract_final_repair_started",
            "chief_engineer.semantic_repair_exhausted",
        ]
        assert len(keepers) == 3
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_thinking_only_repair_falls_back_to_pm_authority_after_one_attempt(
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

        assert result.status == "success", result.output
        assert len(commands) == 2
        assert commands[-1].task_id.endswith("-SCHEMA-REPAIR")
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["generated_blueprints"] == 1
        signal_codes = [signal["code"] for signal in review["signals"]]
        assert "chief_engineer.advisory_projection_fallback" in signal_codes
        fallback_signal = next(
            signal for signal in review["signals"] if signal["code"] == "chief_engineer.advisory_projection_fallback"
        )
        assert fallback_signal["pm_authority_preserved"] is True
        assert fallback_signal["scope_expansion_allowed"] is False
        assert fallback_signal["provider_calls_capped"] == 2
        assert fallback_signal["context_snapshot_ref"] == "abcdef123456abcdef123456"
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
        assert "task-plan overlays are advisory only" in objective
        assert "every task plan: concrete files" not in objective
        contract = OrchestrationStageExecutor._chief_engineer_structured_output_contract(("TASK-1", "TASK-2"))
        assert set(contract.json_schema["required"]) == {
            "construction_plan",
            "project_completion_contract",
            "risk_flags",
        }
        assert "required top-level keys: construction_plan, project_completion_contract, risk_flags" in objective
        assert "optional scope_for_apply" in objective
        obligation_schemas = contract.json_schema["properties"]["project_completion_contract"]["properties"][
            "obligations"
        ]["properties"]
        assert all(
            "minItems" not in obligation_schemas[field] for field in ("artifacts", "entrypoints", "verification")
        )
        task_plan_schema = contract.json_schema["properties"]["construction_plan"]["properties"]["task_plans"][
            "properties"
        ]["TASK-1"]
        assert task_plan_schema["properties"]["scope_for_apply"]["type"] == "array"
        assert task_plan_schema["properties"]["risk_flags"]["type"] == "array"
        assert "Arrays may be empty only when" in objective
        assert "enough distinct task-owned production/test source artifacts" in objective
        assert "Call the required submit_structured_role_output result-submission tool exactly once" in objective
        assert "Emit no assistant prose or raw JSON outside that tool call" in objective
        assert "Return JSON only" not in objective

    def test_chief_engineer_portfolio_tasks_bind_go_source_and_cli_authority(self, tmp_path: Path) -> None:
        executor = OrchestrationStageExecutor(tmp_path)

        portfolio_tasks = executor._chief_engineer_portfolio_tasks(
            [
                {
                    "id": "TASK-GO-CLI",
                    "goal": "Implement the Go command-line entrypoint",
                    "language": "go",
                    "target_files": ["main.go", "internal/engine.go"],
                    "project_declared_entrypoint_targets": ["main.go"],
                    "delivery_depth_contract": {"project_type": "cli_game"},
                    "verification_commands": [
                        {
                            "modality": "entrypoint",
                            "argv": ["go", "run", "."],
                            "cwd": ".",
                        }
                    ],
                    "metadata": {
                        "topology_authority": "chief_engineer",
                        "required_source_kinds": ["domain_modules", "entrypoint"],
                    },
                }
            ]
        )

        assert portfolio_tasks[0].primary_language == "go"
        assert portfolio_tasks[0].allowed_source_suffixes == (".go",)
        assert portfolio_tasks[0].entrypoint_kind_authority == "cli"

    def test_chief_engineer_portfolio_tasks_reject_unknown_delegated_language(self, tmp_path: Path) -> None:
        executor = OrchestrationStageExecutor(tmp_path)

        with pytest.raises(stage_executor_module._ChiefEngineerPortfolioAuthorityError) as exc_info:
            executor._chief_engineer_portfolio_tasks(
                [
                    {
                        "id": "TASK-UNKNOWN",
                        "goal": "Implement a delegated topology",
                        "language": "unknown-language",
                        "target_files": ["src/main.unknown"],
                        "metadata": {"topology_authority": "chief_engineer"},
                    }
                ]
            )

        assert exc_info.value.code == "chief_engineer.topology_language_authority_missing"
