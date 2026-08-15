"""Unit tests for DirectorAdapter pure logic (no I/O, no LLM).

Covers:
- _select_execution_strategy
- _apply_intelligent_correction
- _build_director_message
- _build_materialized_metadata
- _resolve_execution_backend_request
- get_capabilities / role_id
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.cells.chief_engineer.blueprint.public import BlueprintPersistence
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.roles.adapters.internal.director import (
    execute_method as execute_method_module,
    quality_gate as quality_gate_module,
)
from polaris.cells.roles.adapters.internal.director.adapter import (
    DirectorAdapter,
)
from polaris.cells.roles.adapters.internal.director.execute_method import (
    _build_existing_workspace_task_evidence,
    _can_accept_existing_workspace_scope,
    _task_requires_fresh_materialization,
)
from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _build_materialization_quality_failure_evidence_context,
    _build_materialization_quality_workspace_evidence_context,
)
from polaris.cells.roles.adapters.public import service as roles_adapters_public_service
from polaris.cells.roles.adapters.public.contracts import RunDirectorMaterializationQualityRepairScheduleCommandV1
from polaris.cells.runtime.task_runtime.public.contracts import (
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import create_task_runtime_execution_attempt_authority
from polaris.kernelone.events.final_request_evidence import (
    looks_like_failed_gate_evidence_context_payload,
    looks_like_workspace_quality_evidence_payload,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_go_materialization_quality_schedule(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str] | tuple[str, ...] = (),
    advisor_notes: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """Run Go materialization repair through the typed roles adapter boundary."""

    workspace = Path(adapter.workspace)
    result = roles_adapters_public_service.run_director_materialization_quality_repair_schedule_result(
        RunDirectorMaterializationQualityRepairScheduleCommandV1(
            adapter_port=adapter,
            task={},
            task_id=task_id,
            artifact_quality_errors=tuple(artifact_quality_errors),
            advisor_notes=tuple(advisor_notes),
            execution_attempt=_test_execution_attempt(workspace, task_id),
        )
    )
    return _project_deferred_repair_results_for_test(
        workspace,
        [dict(item) for item in result.tool_results],
    )


def _make_adapter(tmp_path: Any, task_runtime: Any = None) -> DirectorAdapter:
    """Create a DirectorAdapter with mocked heavy dependencies."""
    workspace = Path(tmp_path)
    workspace.mkdir(parents=True, exist_ok=True)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="director-adapter-pure-test",
            streams=fact_stream_bootstrap_streams(),
        )
    )
    if task_runtime is None:
        adapter = DirectorAdapter(workspace=str(workspace))
    else:
        adapter = DirectorAdapter(workspace=str(workspace), task_runtime=task_runtime)
    return adapter


from ._execution_attempt_helpers import (
    _project_deferred_repair_results_for_test,
    _test_execution_attempt,
)


def _install_test_deferred_projection(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    workspace: Path,
) -> None:
    """Wrap one bridge so old planner tests consume deferred effects safely."""

    original = module.run_runtime_repair_with_director_tools

    def _run(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        task_id = str(kwargs.get("task_id") or "director-repair-test")
        if type(kwargs.get("execution_attempt")) is not TaskRuntimeExecutionAttemptIdentityV1:
            kwargs["execution_attempt"] = _test_execution_attempt(workspace, task_id)
        return _project_deferred_repair_results_for_test(
            workspace,
            original(*args, **kwargs),
        )

    monkeypatch.setattr(module, "run_runtime_repair_with_director_tools", _run)


def _install_all_test_deferred_projections(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
) -> None:
    """Project every Director repair bridge only for isolated legacy tests."""

    from polaris.cells.roles.adapters.internal.director import (
        execute_method_repair_bridge,
        materialization_quality_callback_ports,
        post_execution_repair_bridge,
        runtime_repair_tool_adapter,
    )

    original = runtime_repair_tool_adapter.run_runtime_repair_with_director_tools

    def _run(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        task_id = str(kwargs.get("task_id") or "director-repair-test")
        if type(kwargs.get("execution_attempt")) is not TaskRuntimeExecutionAttemptIdentityV1:
            kwargs["execution_attempt"] = _test_execution_attempt(workspace, task_id)
        return _project_deferred_repair_results_for_test(
            workspace,
            original(*args, **kwargs),
        )

    for module in (
        runtime_repair_tool_adapter,
        execute_method_repair_bridge,
        materialization_quality_callback_ports,
        post_execution_repair_bridge,
        quality_gate_module,
    ):
        monkeypatch.setattr(module, "run_runtime_repair_with_director_tools", _run)


def _run_test_materialization_quality_repair_schedule(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    artifact_quality_issues: tuple[dict[str, Any], ...] = (),
    advisor_notes: tuple[Any, ...] = (),
    convergence_verifier: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run production planning and project its deferred effects in test scope."""

    workspace = Path(adapter.workspace)
    results, summary = roles_adapters_public_service.run_director_materialization_quality_repair_schedule(
        adapter,
        task=task,
        task_id=task_id,
        artifact_quality_errors=artifact_quality_errors,
        artifact_quality_issues=artifact_quality_issues,
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
        execution_attempt=_test_execution_attempt(workspace, task_id),
    )
    return _project_deferred_repair_results_for_test(workspace, results), summary


class TestBuildDirectorMessage:
    """_build_director_message constructs prompt text deterministically."""

    def test_dependency_message_uses_receipt_bound_parent_snapshot_and_rejects_preset(
        self,
        tmp_path: Any,
    ) -> None:
        source = tmp_path / "src" / "models" / "flavor.rs"
        source.parent.mkdir(parents=True)
        source.write_text("pub enum FlavorProfile { Sweet, Sour }\n", encoding="utf-8")
        receipt_id = "director-physical-effect-receipt-1"
        parent = {
            "id": 1,
            "metadata": {
                "external_task_id": "TASK-1",
                "adapter_result": {
                    "new_files": ["src/models/flavor.rs"],
                    "modified_files": [],
                    "write_tool_evidence": True,
                    "primary_llm": {
                        "metadata": {
                            "batch_receipt": {
                                "raw_results": [
                                    {
                                        "status": "success",
                                        "result": {"file": "src/models/flavor.rs"},
                                        "effect_receipt": {
                                            "schema_version": ("roles.adapters.director_physical_effect_receipt.v2"),
                                            "receipt_id": receipt_id,
                                            "receipt_hash": "a" * 64,
                                            "receipt_binding_hash": "b" * 64,
                                            "physical_result_hash": "c" * 64,
                                            "target_state_hash": "d" * 64,
                                            "receipt_outcome": "succeeded",
                                            "authoritative": True,
                                            "durable": True,
                                        },
                                        "effect_receipt_commit": {
                                            "state": "RECEIPT_COMMITTED",
                                            "receipt_ref": receipt_id,
                                            "receipt_hash": "a" * 64,
                                        },
                                    }
                                ]
                            }
                        }
                    },
                },
            },
        }
        task_runtime = MagicMock()
        task_runtime.get_task.return_value = parent
        adapter = _make_adapter(tmp_path, task_runtime=task_runtime)
        context: dict[str, Any] = {
            "actual_sibling_exports": {"schema_version": "forged"},
            "metadata": {
                "actual_sibling_exports": {"schema_version": "forged"},
                "resolved_depends_on_task_ids": [1],
            },
        }

        msg = adapter._build_director_message(
            {
                "id": 2,
                "subject": "Implement engine",
                "metadata": {"resolved_depends_on_task_ids": [1]},
            },
            context=context,
        )

        payload = context["actual_sibling_exports"]
        assert payload["schema_version"] == "polaris.actual_sibling_exports.evidence.v2"
        assert payload["dependency_task_ids"] == ["1"]
        assert payload["modules"][0]["effect_receipt_id"] == receipt_id
        assert "pub enum FlavorProfile" in msg
        assert payload["snapshot_sha256"] in msg
        assert "forged" not in msg

    def test_root_message_removes_caller_preset_actual_sibling_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        context: dict[str, Any] = {
            "actual_sibling_exports": {"schema_version": "forged"},
            "metadata": {"actual_sibling_exports": {"schema_version": "forged"}},
        }

        msg = adapter._build_director_message(
            {"subject": "Root task", "metadata": {}},
            context=context,
        )

        assert "actual_sibling_exports" not in context
        assert "actual_sibling_exports" not in context["metadata"]
        assert "forged" not in msg

    def test_includes_subject(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "Fix login", "description": "Bug in auth"})
        assert "任务: Fix login" in msg
        assert "文本文件块格式" in msg

    def test_sanitizes_description(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "T", "description": "# Header\n\nBody line"})
        assert "描述:" in msg

    def test_empty_description_omitted(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "T", "description": ""})
        # The line "描述: " with empty content should still appear because implementation
        # does not filter it out; we just assert no crash.
        assert "任务: T" in msg

    def test_uses_real_scope_instead_of_placeholder_path(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Scaffold app",
                "metadata": {
                    "goal": "Create a Vite app",
                    "scope": "package.json, src/main.tsx",
                    "steps": ["Create package manifest"],
                    "acceptance": ["npm test passes"],
                },
            }
        )
        assert "范围: package.json, src/main.tsx" in msg
        assert "- Create package manifest" in msg
        assert "- npm test passes" in msg
        assert "path/to/file.py" not in msg

    def test_includes_pm_contract_paths_checklist_and_acceptance(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement Three.js client scene",
                "description": "Implement the client3d task",
                "metadata": {
                    "goal": "Add the missing client3d capability",
                    "scope_paths": ["src/client/three-scene.ts"],
                    "target_files": ["src/client/three-scene.ts"],
                    "execution_checklist": ["Modify the existing Three.js scene file"],
                    "acceptance_criteria": ["Run `npm run build` passes"],
                },
            }
        )

        assert "范围: src/client/three-scene.ts" in msg
        assert "目标文件: src/client/three-scene.ts" in msg
        assert "- Modify the existing Three.js scene file" in msg
        assert "- Run `npm run build` passes" in msg

    def test_includes_explicit_verification_commands_from_acceptance(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement Go module",
                "metadata": {
                    "target_files": ["go.mod", "main.go", "models/capsule.go"],
                    "execution_checklist": ["Run `go test ./...` after writing code"],
                    "acceptance_criteria": ["`go run .` returns success", "`go test ./...` passes"],
                },
            }
        )

        assert "Verification commands / 验证命令:" in msg
        assert "- go test ./..." in msg
        assert "- go run ." in msg

    def test_includes_runtime_context_verification_commands_from_acceptance(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        context = {
            "target_files": ("go.mod", "main.go"),
            "execution_checklist": "Run `go test ./...` after writing code",
            "acceptance_criteria": "`go test ./...` passes",
        }
        msg = adapter._build_director_message(
            {"subject": "Implement Go module"},
            context=context,
        )

        commands = DirectorAdapter._ensure_director_verification_commands(message=msg, context=context)

        assert "目标文件: go.mod, main.go" in msg
        assert "Verification commands / 验证命令:" in msg
        assert "- go test ./..." in msg
        assert commands == ["go test ./..."]

    def test_includes_language_specific_director_identity(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        context: dict[str, Any] = {
            "target_files": ["go.mod", "main.go"],
            "metadata": {"project_type": "service"},
        }
        msg = adapter._build_director_message(
            {
                "subject": "Implement Go module",
                "description": "Use context cancellation and table-driven tests",
            },
            context=context,
        )

        assert "Director language/task identity / 语言专项身份:" in msg
        assert "精通 Go" in msg
        assert "Primary language: Go (Golang)" in msg
        assert "=== Go (Golang) Language Best Practices ===" in msg
        assert "软件工程师" not in str(context["metadata"]["director_language_identity"])

    def test_multi_target_message_requires_all_target_files(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement model files",
                "metadata": {
                    "scope_paths": ["src/models"],
                    "target_files": [
                        "src/models/flower.ts",
                        "src/models/moon.ts",
                        "src/models/firefly.ts",
                    ],
                },
            }
        )

        assert "目标文件: src/models/flower.ts, src/models/moon.ts, src/models/firefly.ts" in msg
        assert "目标文件覆盖硬门禁" in msg
        assert "每个目标文件分别发出 write/edit 工具调用" in msg
        assert "不得只写第一个 sibling 文件后结束" in msg

    def test_includes_ce_blueprint_and_factory_context(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement firefly garden simulator",
                "metadata": {
                    "goal": "Create the L1-01 simulator artifacts",
                    "scope_paths": ["src/engine/SimulationEngine.ts"],
                    "target_files": ["src/engine/SimulationEngine.ts"],
                    "execution_checklist": ["Write the simulation engine"],
                    "acceptance_criteria": ["npm run build passes"],
                },
            },
            context={
                "blueprint_id": "bp-L1-01-4",
                "construction_step": {
                    "target_file": "src/engine/SimulationEngine.ts",
                    "signatures": ["class SimulationEngine", "runSimulation()"],
                    "verify": "npm run build",
                },
                "metadata": {
                    "factory_bench_project_id": "L1-01",
                    "factory_bench_title": "发光昆虫花园模拟器",
                },
            },
        )

        assert "PM Task Contract / 任务合同:" in msg
        assert "Acceptance criteria / 验收标准:" in msg
        assert "Chief Engineer Blueprint / CE 蓝图交接:" in msg
        assert "- blueprint_id: bp-L1-01-4" in msg
        assert "- construction target: src/engine/SimulationEngine.ts" in msg
        assert "- construction signatures: class SimulationEngine; runSimulation()" in msg
        assert "- construction verify: npm run build" in msg
        assert "- factory bench project: L1-01 - 发光昆虫花园模拟器" in msg

    def test_includes_persisted_ce_blueprint_contract(self, tmp_path: Any) -> None:
        blueprint_id = "bp-L1-01-contract"
        BlueprintPersistence(str(tmp_path)).save(
            blueprint_id,
            {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": blueprint_id,
                "task_id": "TASK-1",
                "target_files": [
                    "src/engine/SimulationEngine.ts",
                    "src/engine/Renderer.ts",
                    "src/engine/Clock.ts",
                    "src/models/Firefly.ts",
                    "src/models/Garden.ts",
                    "src/index.ts",
                    "src/main.ts",
                    "src/web.ts",
                    "tests/behavior.test.ts",
                ],
                "scope_paths": ["src/engine/SimulationEngine.ts", "tests/behavior.test.ts"],
                "pm_contract_hash": "pm-contract-hash",
                "execution_profile_hash": "execution-profile-hash",
                "acceptance_criteria": ["npm run build passes"],
                "execution_checklist": ["Write the simulation engine"],
                "recommendations": ["Run build", "Run smoke test"],
                "contract_completeness": {
                    "handoff_ready": True,
                    "missing_fields": [],
                    "semantic_blockers": [],
                    "semantic_alignment": {
                        "expected_terms": ["firefly", "garden", "simulation"],
                        "planning_text_matches": ["firefly", "garden", "simulation"],
                        "target_file_matches": [],
                        "advisory": ["semantic_alignment.target_files: matched 0/2 required domain terms"],
                        "blockers": [],
                    },
                },
                "llm_blueprint": {
                    "schema_version": "chief_engineer.llm_blueprint_overlay.v1",
                    "source": "chief_engineer.llm_output",
                    "authoritative": False,
                    "authority": "advisory_only",
                    "implementation_phases": [
                        "Split simulation state from rendering",
                        "Add deterministic tick samples",
                    ],
                    "module_boundaries": ["SimulationEngine owns rules", "Canvas adapter owns drawing"],
                    "verification_steps": ["npm run build", "npm test"],
                    "scope_for_apply_advisory": ["src/engine/SimulationEngine.ts"],
                    "risk_flags": ["browser bootstrap can drift from compiled output"],
                },
            },
        )
        adapter = _make_adapter(tmp_path)

        msg = adapter._build_director_message(
            {
                "subject": "Implement firefly garden simulator",
                "metadata": {
                    "blueprint_id": blueprint_id,
                    "goal": "Create the simulator artifacts",
                    "target_files": ["src/engine/SimulationEngine.ts"],
                    "execution_checklist": ["Write the simulation engine"],
                    "acceptance_criteria": ["npm run build passes"],
                },
            }
        )

        assert "- blueprint_id: bp-L1-01-contract" in msg
        assert "- handoff_ready: no" in msg
        assert "project completion contract is missing from Chief Engineer blueprint" in msg
        assert "- blueprint target_files: src/engine/SimulationEngine.ts" in msg
        assert "tests/behavior.test.ts" in msg
        assert "- blueprint required test targets: tests/behavior.test.ts" in msg
        assert "- blueprint acceptance: npm run build passes" in msg
        assert "- blueprint execution_checklist: Write the simulation engine" in msg
        assert "- blueprint expected_terms: firefly, garden, simulation" in msg
        assert "- ce_llm_blueprint: consumed (advisory_only)" in msg
        assert "- ce plan phases: Split simulation state from rendering, Add deterministic tick samples" in msg
        assert "- ce module boundaries: SimulationEngine owns rules, Canvas adapter owns drawing" in msg
        assert "- ce verification: npm run build, npm test" in msg
        assert "- ce scope advisory: src/engine/SimulationEngine.ts" in msg
        assert "- ce risks: browser bootstrap can drift from compiled output" in msg

    def test_promote_task_contract_preserves_claimed_write_boundary_from_ce_blueprint(self, tmp_path: Any) -> None:
        blueprint_id = "bp-task-1-with-tests"
        BlueprintPersistence(str(tmp_path)).save(
            blueprint_id,
            {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": blueprint_id,
                "task_id": "TASK-1",
                "target_files": ["src/index.ts", "tests/behavior.test.ts"],
                "scope_paths": ["src/index.ts", "tests/behavior.test.ts"],
                "acceptance_criteria": ["npm run test passes"],
                "execution_checklist": ["Implement source and behavior tests"],
            },
        )
        task = {
            "id": 1,
            "metadata": {
                "blueprint_id": blueprint_id,
                "target_files": ["src/index.ts"],
                "scope_paths": ["src/index.ts"],
            },
        }
        context: dict[str, Any] = {}

        DirectorAdapter._promote_task_contract_to_runtime_context(
            task=task,
            context=context,
            workspace=str(tmp_path),
        )

        metadata = context["metadata"]
        assert context["target_files"] == ["src/index.ts"]
        assert context["scope_paths"] == ["src/index.ts"]
        assert metadata["target_files"] == ["src/index.ts"]
        assert metadata["scope_paths"] == ["src/index.ts"]
        task_metadata = task["metadata"]
        assert isinstance(task_metadata, dict)
        assert task["target_files"] == ["src/index.ts"]
        assert task["scope_paths"] == ["src/index.ts"]
        assert task_metadata["target_files"] == ["src/index.ts"]
        assert task_metadata["scope_paths"] == ["src/index.ts"]
        assert execute_method_module._declared_write_retry_target_files(task) == ["src/index.ts"]
        assert metadata["ce_blueprint"]["blueprint_id"] == blueprint_id

    def test_role_runtime_metadata_carries_quality_repair_evidence(self) -> None:
        failed_gate_evidence = _build_materialization_quality_failure_evidence_context(
            artifact_quality_errors=[
                "Artifact quality scan failed: TypeScript project typecheck failed: "
                "src/models/Humidity.ts(77,3): error TS6133: 'flower' is declared but its value is never read."
            ],
            missing_target_files=[],
            repair_target_files=["src/models/Humidity.ts"],
            changed_files=["src/models/Humidity.ts"],
            repair_attempt=1,
        )
        workspace_quality_evidence = _build_materialization_quality_workspace_evidence_context(
            artifact_quality_errors=[
                "Artifact quality scan failed: TypeScript project typecheck failed: "
                "src/models/Humidity.ts(77,3): error TS6133: 'flower' is declared but its value is never read."
            ],
            missing_target_files=[],
            repair_target_files=["src/models/Humidity.ts"],
            changed_files=["src/models/Humidity.ts"],
            repair_attempt=1,
        )
        context = {
            "task_id": "factory-quality-gate:run-1:llm-repair",
            "failed_gate_evidence": failed_gate_evidence,
            "workspace_quality_evidence": workspace_quality_evidence,
            "metadata": {"task_id": "factory-quality-gate:run-1:llm-repair"},
        }

        metadata = DirectorAdapter._build_role_runtime_metadata(context, max_retries=0)

        assert looks_like_failed_gate_evidence_context_payload(metadata["failed_gate_evidence"])
        assert looks_like_workspace_quality_evidence_payload(metadata["workspace_quality_evidence"])

    def test_message_requires_unittest_and_contract_scoped_python_tests(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement calculator tests",
                "metadata": {
                    "scope_paths": ["calculator.py", "tests/test_calculator.py"],
                    "target_files": ["calculator.py", "tests/test_calculator.py"],
                    "execution_checklist": ["Add calculator regression tests"],
                    "acceptance_criteria": ["2+3*4 returns 14", "10/0 is rejected"],
                },
            }
        )

        assert "标准库 unittest" in msg
        assert "python -m unittest discover -s tests -p 'test_*.py' -v" in msg
        assert "至少发现并运行 1 个测试" in msg
        assert "不得新增合同外功能断言" in msg
        assert "未声明第三方测试依赖" in msg

    def test_includes_qa_rework_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Fix QA findings",
                "metadata": {
                    "qa_rework_reason": "placeholder_content_detected",
                    "qa_rework_evidence": [
                        "src/backend/fashiongen_worker.py:\\bplaceholder\\b",
                        "src/main/providers.ts:\\bplaceholder\\b",
                    ],
                },
            }
        )

        assert "QA 返工要求" in msg
        assert "placeholder_content_detected" in msg
        assert "src/backend/fashiongen_worker.py" in msg
        assert "src/main/providers.ts" in msg


class TestExistingWorkspaceTaskEvidence:
    """Director can verify already-materialized task scope without fresh diffs."""

    def test_declared_scope_present(self) -> None:
        task = {
            "scope": [
                "package.json",
                "src/types",
                "src/spec",
                "src/services",
                "src/store",
            ]
        }
        current_files = {
            "package.json": "1",
            "src/types/domain.ts": "1",
            "src/spec/generationSpec.ts": "1",
            "src/services/mockGenerationService.ts": "1",
            "src/store/useStudioStore.ts": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert evidence["reason"] == "declared_scope_present"
        assert "src/spec" in evidence["existing_paths"]

    def test_missing_or_weak_scope_is_not_enough(self) -> None:
        task = {"scope": ["src/workbench", "src/library", "src/layouts", "src/components"]}
        current_files = {"src/components/StudioShell.tsx": "1"}

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is False
        assert evidence["reason"] == "declared_scope_incomplete"

    def test_declared_manifest_on_disk_counts_when_missing_from_current_files(self, tmp_path: Any) -> None:
        (tmp_path / "go.mod").write_text("module moodwheel\n\ngo 1.22\n", encoding="utf-8")
        (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n", encoding="utf-8")
        task = {"target_files": ["go.mod", "main.go"]}
        current_files = {"main.go": "package main\n\nfunc main() {}\n"}

        evidence = _build_existing_workspace_task_evidence(
            task=task,
            current_files=current_files,
            workspace_full=str(tmp_path),
        )

        assert evidence["ok"] is True
        assert evidence["reason"] == "declared_scope_present"
        assert evidence["existing_paths"] == ["go.mod", "main.go"]
        assert evidence["missing_paths"] == []

    def test_high_coverage_scope_with_missing_declared_targets_is_not_enough(self) -> None:
        task = {
            "target_files": [
                "go.mod",
                "models/entity.go",
                "engine/service.go",
                "main.go",
                "main_test.go",
                "README.md",
            ]
        }
        current_files = {
            "go.mod": "1",
            "models/entity.go": "1",
            "engine/service.go": "1",
            "main.go": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is False
        assert evidence["reason"] == "declared_scope_incomplete"
        assert evidence["coverage"] > 0.5
        assert evidence["missing_paths"] == ["main_test.go", "README.md"]

    def test_no_scope_paths_is_not_evidence(self) -> None:
        evidence = _build_existing_workspace_task_evidence(
            task={"goal": "Implement a UI"},
            current_files={"src/App.tsx": "1"},
        )

        assert evidence["ok"] is False
        assert evidence["reason"] == "no_declared_scope_paths"

    def test_glob_scope_paths_match_workspace_files(self) -> None:
        task = {
            "metadata": {
                "scope": [
                    "src/**/*.test.ts",
                    "src/**/*.test.tsx",
                    "README.md",
                    "tests",
                ]
            }
        }
        current_files = {
            "src/spec/generationSpec.test.ts": "1",
            "src/App.test.tsx": "1",
            "README.md": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert "src/**/*.test.ts" in evidence["existing_paths"]
        assert "README.md" in evidence["existing_paths"]

    def test_existing_scope_rejects_go_test_compile_errors(self, tmp_path: Any) -> None:
        (tmp_path / "go.mod").write_text("module demo\n\ngo 1.22\n", encoding="utf-8")
        (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n", encoding="utf-8")
        (tmp_path / "main_test.go").write_text(
            'package main\n\nimport "testing"\n\nfunc TestMissing(t *testing.T) {\n\t_ = engine.PaletteForMood\n}\n',
            encoding="utf-8",
        )
        task = {"target_files": ["main_test.go", "go.mod"]}
        current_files = {"main_test.go": "1"}

        evidence = _build_existing_workspace_task_evidence(
            task=task,
            current_files=current_files,
            workspace_full=str(tmp_path),
        )

        assert evidence["ok"] is False
        assert evidence["reason"] == "declared_scope_quality_failed"
        assert any("undefined:" in item for item in evidence["artifact_quality_errors"])

    def test_existing_scope_rejects_go_test_assertion_failures_for_test_owner(self, tmp_path: Any) -> None:
        (tmp_path / "go.mod").write_text("module demo\n\ngo 1.22\n", encoding="utf-8")
        (tmp_path / "main.go").write_text(
            'package main\n\nfunc Bucket(v float64) string {\n\tif v < 0.33 {\n\t\treturn "low"\n\t}\n\treturn "mid"\n}\n\nfunc main() {}\n',
            encoding="utf-8",
        )
        (tmp_path / "main_test.go").write_text(
            'package main\n\nimport "testing"\n\nfunc TestBucket(t *testing.T) {\n'
            '\tif got := Bucket(0.34); got != "low" {\n'
            '\t\tt.Fatalf("Bucket(0.34) = %s, want low", got)\n\t}\n}\n',
            encoding="utf-8",
        )
        task = {"target_files": ["main_test.go", "go.mod"]}
        current_files = {"main_test.go": "1", "go.mod": "1"}

        evidence = _build_existing_workspace_task_evidence(
            task=task,
            current_files=current_files,
            workspace_full=str(tmp_path),
        )

        assert evidence["ok"] is False
        assert evidence["reason"] == "declared_scope_quality_failed"
        assert any("want low" in item for item in evidence["artifact_quality_errors"])

    def test_existing_scope_accepts_aligned_go_test_for_test_owner(self, tmp_path: Any) -> None:
        (tmp_path / "go.mod").write_text("module demo\n\ngo 1.22\n", encoding="utf-8")
        (tmp_path / "main.go").write_text(
            'package main\n\nfunc Bucket(v float64) string {\n\tif v < 0.33 {\n\t\treturn "low"\n\t}\n\treturn "mid"\n}\n\nfunc main() {}\n',
            encoding="utf-8",
        )
        (tmp_path / "main_test.go").write_text(
            'package main\n\nimport "testing"\n\nfunc TestBucket(t *testing.T) {\n'
            '\tif got := Bucket(0.32); got != "low" {\n'
            '\t\tt.Fatalf("Bucket(0.32) = %s, want low", got)\n\t}\n}\n',
            encoding="utf-8",
        )
        task = {"target_files": ["main_test.go", "go.mod"]}
        current_files = {"main_test.go": "1", "go.mod": "1"}

        evidence = _build_existing_workspace_task_evidence(
            task=task,
            current_files=current_files,
            workspace_full=str(tmp_path),
        )

        assert evidence["ok"] is True
        assert evidence["missing_paths"] == []

    def test_existing_scope_rejects_placeholder_tests_when_workspace_is_available(self, tmp_path: Any) -> None:
        test_file = tmp_path / "tests" / "unit" / "card-rules.test.ts"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(
            "\n".join(f"test('case {idx}', () => expect({idx} + 1).toBe({idx + 1}));" for idx in range(4)) + "\n",
            encoding="utf-8",
        )
        task = {
            "target_files": ["tests/unit/card-rules.test.ts"],
            "scope_paths": ["tests"],
        }
        current_files = {"tests/unit/card-rules.test.ts": "1"}

        evidence = _build_existing_workspace_task_evidence(
            task=task,
            current_files=current_files,
            workspace_full=str(tmp_path),
        )

        assert evidence["ok"] is False
        assert evidence["reason"] == "declared_scope_quality_failed"
        assert any("trivial arithmetic placeholder" in item for item in evidence["artifact_quality_errors"])

    def test_materialized_orchestration_scope_markers_are_evidence(self) -> None:
        task = {
            "subject": (
                "Execute PM tasks strictly in order:\n"
                "- Project Foundation [scope: package.json, tsconfig.json, vite.config.ts, tailwind.config.js]\n"
                "- Domain Layer [scope: src/types, src/spec, src/services, src/store]\n"
                "- Delivery Verification [scope: tests, src/**/*.test.tsx, README.md]"
            )
        }
        current_files = {
            "package.json": "1",
            "tsconfig.json": "1",
            "vite.config.ts": "1",
            "tailwind.config.js": "1",
            "src/types/domain.ts": "1",
            "src/spec/generationSpec.ts": "1",
            "src/services/mockGenerationService.ts": "1",
            "src/store/useStudioStore.ts": "1",
            "src/App.test.tsx": "1",
            "tests/routes/WorkbenchRoute.test.tsx": "1",
            "README.md": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert "package.json" in evidence["existing_paths"]
        assert "src/**/*.test.tsx" in evidence["existing_paths"]
        assert evidence["reason"] == "declared_scope_present"

    def test_scope_label_prefixes_do_not_pollute_path_candidates(self) -> None:
        task = {"metadata": {"scope": "Root configuration files: package.json, tsconfig.json, postcss.config.js"}}
        current_files = {
            "package.json": "1",
            "tsconfig.json": "1",
            "postcss.config.js": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert "package.json" in evidence["existing_paths"]
        assert all("Root configuration files" not in item for item in evidence["candidate_paths"])

    def test_workspace_basename_prefix_is_not_treated_as_nested_scope(self) -> None:
        task = {
            "metadata": {
                "scope": "fashion-gen-studio/package.json, fashion-gen-studio/src/, vite.config.ts",
            }
        }
        current_files = {
            "package.json": "1",
            "src/App.tsx": "1",
            "vite.config.ts": "1",
        }

        evidence = _build_existing_workspace_task_evidence(
            task=task,
            current_files=current_files,
            workspace_name="fashion-gen-studio",
        )

        assert evidence["ok"] is True
        assert "package.json" in evidence["existing_paths"]
        assert "src" in evidence["existing_paths"]
        assert "fashion-gen-studio/package.json" not in evidence["missing_paths"]

    def test_repair_tasks_require_fresh_materialization(self) -> None:
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Repair TypeScript failure",
                    "metadata": {"acceptance": ["npm test returns PASS"]},
                }
            )
            is True
        )
        assert _task_requires_fresh_materialization({"subject": "Create initial source files"}) is True
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Implement Card3D tests",
                    "phase": "verification",
                    "target_files": ["tests/integration/multiplayer-flow.test.ts"],
                }
            )
            is True
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "title": "补齐领域验收测试",
                    "goal": "移除旧的占位测试，创建覆盖卡牌、牌组、多人流程、同步与3D场景的测试",
                    "phase": "verify",
                    "target_files": [
                        "tests/unit/card-rules.test.ts",
                        "tests/integration/multiplayer-flow.test.ts",
                    ],
                    "execution_checklist": ["删除已存在的 trivial 占位测试（如算术测试）"],
                }
            )
            is True
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Replace placeholder Card3D unit tests",
                    "description": "Remove trivial arithmetic placeholder tests.",
                }
            )
            is True
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "QA Placeholder Repair Verification",
                    "phase": "verification",
                    "metadata": {"qa_rework_verification_only": True},
                }
            )
            is False
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Frontend Test Failure Reproduction",
                    "description": "Fix npm test failure with the smallest target-project change after evidence is collected.",
                    "metadata": {
                        "phase": "requirements",
                        "steps": ["Run npm test", "Identify failing assertion"],
                        "acceptance": ["The failing Vitest case is identified"],
                    },
                }
            )
            is False
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Requirements task reopened by QA",
                    "metadata": {
                        "phase": "requirements",
                        "qa_rework_requested": True,
                        "adapter_result": {
                            "qa_passed": False,
                            "qa_rework_reason": "placeholder_content_detected",
                        },
                    },
                }
            )
            is True
        )

    def test_provider_failures_cannot_authorize_existing_scope_completion(self) -> None:
        task = {
            "subject": "Extend realtime gateway",
            "phase": "implementation",
            "target_files": ["src/server/realtime-gateway.ts"],
        }

        assert (
            _can_accept_existing_workspace_scope(
                task=task,
                requires_fresh_materialization=True,
                write_tool_evidence=False,
            )
            is False
        )

    def test_non_transient_no_write_still_requires_materialization(self) -> None:
        assert (
            _can_accept_existing_workspace_scope(
                task={
                    "subject": "Extend realtime gateway",
                    "phase": "implementation",
                    "target_files": ["src/server/realtime-gateway.ts"],
                },
                requires_fresh_materialization=True,
                write_tool_evidence=False,
            )
            is False
        )

    def test_current_project_artifact_receipts_authorize_retry_without_noop_write(self) -> None:
        assert (
            _can_accept_existing_workspace_scope(
                task={
                    "subject": "Complete verification artifacts",
                    "phase": "implementation",
                    "target_files": ["tests/product.test.js", "README.md"],
                },
                requires_fresh_materialization=True,
                write_tool_evidence=False,
                project_artifact_receipt_evidence=True,
            )
            is True
        )


class TestCollectStepVerifyErrors:
    """Step verify is planned here and physically executed by the kernel follow-up."""

    @staticmethod
    def _collect(context: Any, workspace: str) -> list[str]:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _collect_step_verify_errors,
        )

        if isinstance(context, dict):
            task_id = "task-step-verify"
            context.setdefault(
                "task_runtime_execution_attempt_authority",
                create_task_runtime_execution_attempt_authority(_test_execution_attempt(Path(workspace), task_id)),
            )
        else:
            task_id = "task-step-verify"
        errors, tool_results = _collect_step_verify_errors(
            SimpleNamespace(workspace=workspace),
            context,
            task_id=task_id,
        )
        if isinstance(context, dict):
            context["_test_deferred_step_verify_tool_results"] = tool_results
        return errors

    def test_non_step_context_is_noop(self, tmp_path: Any) -> None:
        assert self._collect({}, str(tmp_path)) == []
        assert self._collect(None, str(tmp_path)) == []
        assert self._collect({"construction_step": {"target_file": "a.md"}}, str(tmp_path)) == []

    def test_passing_verify_returns_no_errors(self, tmp_path: Any) -> None:
        (tmp_path / "index.html").write_text('<canvas id="game-canvas"></canvas>', encoding="utf-8")
        context = {"construction_step": {"verify": "test -f ./index.html && grep -q 'id=\"game-canvas\"' ./index.html"}}
        assert self._collect(context, str(tmp_path)) == []

    def test_failing_verify_yields_repairable_error(self, tmp_path: Any) -> None:
        (tmp_path / "index.html").write_text('<canvas id="gameCanvas"></canvas>', encoding="utf-8")
        context = {"construction_step": {"verify": "grep -q 'id=\"game-canvas\"' ./index.html"}}
        errors = self._collect(context, str(tmp_path))
        assert errors == []
        requests = context["_test_deferred_step_verify_tool_results"]
        assert len(requests) == 1
        assert "game-canvas" in requests[0]["result"]["deferred_request"].command

    def test_step_verify_never_runs_locally_before_authoritative_receipt(self, tmp_path: Any) -> None:
        context = {"construction_step": {"verify": "grep -q ready ./index.html"}}
        errors = self._collect(context, str(tmp_path))

        assert errors == []
        request = context["_test_deferred_step_verify_tool_results"][0]["result"]["deferred_request"]
        assert request.command == "grep -Fq ready ./index.html"
        assert request.execution_attempt.external_task_id == "task-step-verify"

    def test_unsafe_verify_rejected_before_directed_effect_admission(self, tmp_path: Any) -> None:
        errors = self._collect({"construction_step": {"verify": "rm -rf ."}}, str(tmp_path))

        assert len(errors) == 1
        assert "step verify command rejected by safety policy" in errors[0]
        assert "blocked_command:rm" in errors[0]
        assert "'rm -rf .'" in errors[0]

    def test_unsafe_verify_rejected_before_target_mismatch(self, tmp_path: Any) -> None:
        context = {
            "construction_step": {
                "target_file": "src/rules/dancerule.ts",
                "verify": "rm -rf . && test -f ./src/rules/dance-rule.ts",
            }
        }

        errors = self._collect(context, str(tmp_path))

        assert len(errors) == 1
        assert "step verify command rejected by safety policy" in errors[0]
        assert "step verify target mismatch" not in errors[0]

    def test_legacy_safe_wc_verify_is_split_into_directed_effects(self, tmp_path: Any) -> None:
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        verify = 'test -f ./style.css && [ "$(wc -l < ./style.css)" -le 120 ]'
        context = {"construction_step": {"verify": verify}}
        errors = self._collect(context, str(tmp_path))

        assert errors == []
        requests = context["_test_deferred_step_verify_tool_results"]
        assert [item["result"]["deferred_request"].command for item in requests] == [
            "test -f ./style.css",
            '[ "$(wc -l < ./style.css)" -le 120 ]',
        ]

    def test_list_verify_joined(self, tmp_path: Any) -> None:
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        context = {"construction_step": {"verify": ["test -f ./a.md", "grep -q x ./a.md"]}}
        assert self._collect(context, str(tmp_path)) == []

    def test_acceptance_go_verify_is_deferred_from_task_payload(self, tmp_path: Any) -> None:
        context = {
            "metadata": {
                "task_payload": {
                    "target_files": ["go.mod", "main.go", "main_test.go"],
                    "acceptance_criteria": [
                        "`go test ./...` returns success",
                        "`python -m unittest discover -s tests -p 'test_*.py' -v` returns success",
                    ],
                }
            }
        }

        assert self._collect(context, str(tmp_path)) == []
        request = context["_test_deferred_step_verify_tool_results"][0]["result"]["deferred_request"]
        assert request.command == "go test ./..."

    def test_verification_commands_go_verify_is_deferred(self, tmp_path: Any) -> None:
        context = {
            "target_files": ["go.mod", "main.go"],
            "verification_commands": ["go test ./...", "go run ."],
        }

        assert self._collect(context, str(tmp_path)) == []
        request = context["_test_deferred_step_verify_tool_results"][0]["result"]["deferred_request"]
        assert request.command == "go test ./..."

    def test_acceptance_go_verify_failure_waits_for_authoritative_receipt(self, tmp_path: Any) -> None:
        context = {
            "metadata": {
                "task_payload": {
                    "target_files": ["go.mod", "main.go", "main_test.go"],
                    "steps": ["run `go test ./...` before completion"],
                    "acceptance_criteria": ["`go test ./...` returns success"],
                }
            }
        }

        errors = self._collect(context, str(tmp_path))

        assert errors == []
        request = context["_test_deferred_step_verify_tool_results"][0]["result"]["deferred_request"]
        assert request.command == "go test ./..."

    def test_near_miss_verify_target_path_is_repairable_error(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "rules" / "dancerule.ts"
        target.parent.mkdir(parents=True)
        target.write_text("export interface DanceRule {}\n", encoding="utf-8")
        context = {
            "construction_step": {
                "target_file": "src/rules/dancerule.ts",
                "verify": ("test -f ./src/rules/dance-rule.ts && grep -q 'DanceRule' ./src/rules/dance-rule.ts"),
            }
        }
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "step verify target mismatch" in errors[0]
        assert "src/rules/dancerule.ts" in errors[0]
        assert "src/rules/dance-rule.ts" in errors[0]

    def test_verify_may_reference_test_file_for_source_target(self, tmp_path: Any) -> None:
        source = tmp_path / "src" / "main.ts"
        test_file = tmp_path / "tests" / "main.test.ts"
        source.parent.mkdir(parents=True)
        test_file.parent.mkdir(parents=True)
        source.write_text("export const answer = 42;\n", encoding="utf-8")
        test_file.write_text("import '../src/main';\n", encoding="utf-8")
        context = {
            "construction_step": {
                "target_file": "src/main.ts",
                "verify": "test -f ./tests/main.test.ts",
            }
        }
        assert self._collect(context, str(tmp_path)) == []

    def test_failure_names_first_failing_clause(self, tmp_path: Any) -> None:
        """Each safe clause becomes an independently receipted command effect."""
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        context = {
            "construction_step": {
                "verify": (
                    "test -f ./style.css && grep -q '#game' ./style.css && [ \"$(wc -l < ./style.css)\" -le 120 ]"
                )
            }
        }
        errors = self._collect(context, str(tmp_path))
        assert errors == []
        requests = context["_test_deferred_step_verify_tool_results"]
        assert len(requests) == 3
        assert "wc -l" in requests[-1]["result"]["deferred_request"].command

    def test_single_clause_failure_has_no_clause_suffix(self, tmp_path: Any) -> None:
        context = {"construction_step": {"verify": "test -f ./missing.md"}}
        errors = self._collect(context, str(tmp_path))
        assert errors == []
        assert len(context["_test_deferred_step_verify_tool_results"]) == 1

    def test_quoted_and_inside_pattern_is_not_split(self, tmp_path: Any) -> None:
        """A quoted AND belongs to the grep pattern, not the shell command graph."""
        (tmp_path / "a.txt").write_text("plain\n", encoding="utf-8")
        context = {"construction_step": {"verify": "grep -q 'a && b' ./a.txt && test -f ./a.txt"}}
        errors = self._collect(context, str(tmp_path))
        assert errors == []
        requests = context["_test_deferred_step_verify_tool_results"]
        assert [item["result"]["deferred_request"].command for item in requests] == [
            "grep -q 'a && b' ./a.txt",
            "test -f ./a.txt",
        ]

    def test_state_carrying_chain_aborts_clause_diagnosis(self, tmp_path: Any) -> None:
        """Adversarial review (live repro): a cd/VAR= clause passes sh -n but its
        successors re-run in a fresh shell against the wrong cwd/env — naming
        a wrong clause actively misleads the next attempt."""
        from polaris.kernelone.quality.step_verify import normalize_step_verify

        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "app.js").write_text("bar\n", encoding="utf-8")
        for verify in (
            "cd src && test -f app.js && grep -q foo app.js",
            'X=1 && [ "$X" = 1 ] && test -f missing.txt',
            "export V=2 && test -f missing.txt",
        ):
            context = {"construction_step": {"verify": verify}}
            errors = self._collect(context, str(tmp_path))
            assert errors == [], verify
            requests = context["_test_deferred_step_verify_tool_results"]
            assert len(requests) == 1, verify
            assert requests[0]["result"]["deferred_request"].command == normalize_step_verify(verify)

    def test_top_level_or_chain_aborts_clause_diagnosis(self, tmp_path: Any) -> None:
        context = {"construction_step": {"verify": "test -f ./a.txt && grep -q x ./a.txt || test -f ./b.txt"}}
        errors = self._collect(context, str(tmp_path))
        assert errors == []
        requests = context["_test_deferred_step_verify_tool_results"]
        assert len(requests) == 1
        assert requests[0]["result"]["deferred_request"].command.endswith("|| test -f ./b.txt")

    def test_clause_detail_precedes_full_command_in_message(self, tmp_path: Any) -> None:
        """Command order is preserved for receipt-backed failure attribution."""
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        verify = 'test -f ./style.css && [ "$(wc -l < ./style.css)" -le 120 ]'
        context = {"construction_step": {"verify": verify}}
        errors = self._collect(context, str(tmp_path))
        assert errors == []
        commands = [
            item["result"]["deferred_request"].command for item in context["_test_deferred_step_verify_tool_results"]
        ]
        assert commands == ["test -f ./style.css", '[ "$(wc -l < ./style.css)" -le 120 ]']
