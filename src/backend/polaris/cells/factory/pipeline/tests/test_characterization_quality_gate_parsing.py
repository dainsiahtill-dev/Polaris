"""Characterization tests for package.json parsing + director evidence + artifact/mirror helpers."""

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


from polaris.cells.factory.pipeline.tests._characterization_helpers import (  # noqa: F401
    _executor,
    _invalid_chief_engineer_stream_result,
    _single_task_chief_engineer_result,
    _thinking_only_chief_engineer_result,
)


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

        assert commands[0][:2] == [sys.executable, "-c"]
        assert "g++" in commands[0][2]
        assert not any(cmd[:3] == [sys.executable, "-m", "compileall"] for cmd in commands)
        assert not any(cmd[:3] == [sys.executable, "-m", "pip"] for cmd in commands)

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
