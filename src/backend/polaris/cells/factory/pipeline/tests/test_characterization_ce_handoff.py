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


class TestChiefEngineerHandoffGuards:
    def test_schema_valid_candidate_round_trips_only_for_exact_same_run_authority(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        structured_output = {
            "construction_plan": {"task_plans": {}},
            "project_completion_contract": {"obligations": {}},
            "risk_flags": [],
        }

        candidate_ref = executor._persist_chief_engineer_structured_candidate(
            run_id="factory-run-candidate",
            pm_contract_hash="a" * 64,
            task_ids=("TASK-1", "TASK-2"),
            structured_output=structured_output,
            evidence={
                "provider": "provider-a",
                "model": "model-a",
                "context_snapshot_ref": "a" * 24,
                "request_hash": "b" * 64,
            },
        )

        assert candidate_ref.endswith("factory-run-candidate.ce-structured-candidate.json")
        assert executor._load_chief_engineer_structured_candidate(
            run_id="factory-run-candidate",
            pm_contract_hash="a" * 64,
            task_ids=("TASK-1", "TASK-2"),
        ) == (structured_output, candidate_ref)
        assert (
            executor._load_chief_engineer_structured_candidate(
                run_id="factory-run-candidate",
                pm_contract_hash="c" * 64,
                task_ids=("TASK-1", "TASK-2"),
            )
            is None
        )

    def test_schema_valid_candidate_hash_tamper_fails_closed(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        candidate_ref = executor._persist_chief_engineer_structured_candidate(
            run_id="factory-run-candidate-tamper",
            pm_contract_hash="a" * 64,
            task_ids=("TASK-1",),
            structured_output={"risk_flags": []},
            evidence={},
        )
        candidate_path = Path(resolve_logical_path(tmp_path, candidate_ref))
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        payload["structured_output"] = {"risk_flags": ["tampered"]}
        candidate_path.write_text(json.dumps(payload), encoding="utf-8")

        assert (
            executor._load_chief_engineer_structured_candidate(
                run_id="factory-run-candidate-tamper",
                pm_contract_hash="a" * 64,
                task_ids=("TASK-1",),
            )
            is None
        )

    def test_schema_valid_candidate_materializes_portfolio_without_provider_call(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Candidate recovery distinguishes missing portfolio from immutable conflict."""

        executor = _executor(tmp_path)
        _write_minimal_chief_engineer_plan(executor)
        run = FactoryRun(
            id="factory-run-candidate-materialize",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )
        executor._persist_chief_engineer_structured_candidate(
            run_id=run.id,
            pm_contract_hash="a" * 64,
            task_ids=("TASK-CANCEL",),
            structured_output=executor._chief_engineer_authoritative_pm_projection_candidate(),
            evidence={
                "provider": "test-provider",
                "model": "test-model",
                "context_snapshot_ref": "a" * 24,
            },
        )

        class _ProviderMustNotRun:
            async def execute_role_task(self, command: Any) -> Any:
                raise AssertionError(f"unexpected provider call: {command.task_id}")

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _ProviderMustNotRun)

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success", result.output
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["generated_blueprints"] == 1
        assert review["llm_call_count"] == 0
        assert "chief_engineer.structured_candidate_materialized" in {signal["code"] for signal in review["signals"]}

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
        construction_plan["task_plans"] = {"TASK-UNKNOWN": {"implementation": ["Do not execute"]}}
        payload["construction_plan"] = construction_plan

        errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
            payload,
            task_ids=("TASK-CANCEL",),
        )

        assert errors == ["task_plans contains unknown task ids: TASK-UNKNOWN"]

    def test_portfolio_validation_rejects_delivery_depth_authority_deficit(self) -> None:
        payload = dict(_single_task_chief_engineer_result().metadata["structured_output"])
        task = ChiefEngineerPortfolioTaskV1(
            task_id="TASK-CANCEL",
            objective="Deliver a real project",
            target_files=("src/main.py",),
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
            "project_interface_contract"
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
        assert repair_command.stream is True
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
        repaired = _single_task_chief_engineer_result()
        repaired_payload = dict(repaired.metadata["structured_output"])
        repaired_payload["scope_for_apply"] = ["src/cancel.py", "src/cancel_policy.py"]
        repaired_payload["project_completion_contract"] = _library_completion_requirements(
            "src/cancel.py",
            "src/cancel_policy.py",
            owner_task_ids=("TASK-CANCEL", "TASK-CANCEL"),
            test_path="tests/test_cancel.py",
            test_owner_task_id="TASK-CANCEL",
        )
        repaired.metadata["structured_output"] = repaired_payload
        repaired.output = json.dumps(repaired_payload)
        results = [primary, repaired]
        commands: list[Any] = []

        class _RepairingRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                return results.pop(0)

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
            f"CE-PORTFOLIO-{run.id}-SCHEMA-REPAIR",
        ]
        feedback = commands[1].context["failure_feedback"]
        assert feedback["failure_class"] == "output_validation_failed"
        assert "delivery depth infeasible: prod_files=1 < 2" in feedback["detail"]
        assert (
            "verification modality must be exactly one of: environment_prep, build, test, lint, entrypoint"
            in commands[1].objective
        )
        assert "Use test for QA/domain/behavior verification" in commands[1].objective
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

    def test_chief_engineer_semantic_repair_budget_is_one_call(
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
        assert len(commands) == 2
        assert result.metadata["error_code"] == "chief_engineer.portfolio_output_invalid"
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["llm_call_count"] == 2
        assert [signal["code"] for signal in review["signals"]] == [
            "chief_engineer.output_contract_repair_started",
            "chief_engineer.portfolio_output_invalid",
        ]
        assert len(keepers) == 2
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
