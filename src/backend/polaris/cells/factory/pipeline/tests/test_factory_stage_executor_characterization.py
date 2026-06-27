"""Characterization tests for ``OrchestrationStageExecutor`` helper clusters.

These tests freeze the *current* behavior of the pure helpers, artifact
filesystem I/O, mirroring, package.json parsing, real-subprocess quality
command execution, the director-evidence truth tables, and the PM/text-shaping
glue BEFORE the god-class is decomposed into sibling collaborators. They exist
to guard a behavior-preserving refactor; they assert observed outputs derived
from reading the source, not idealized contracts.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import GenerateTaskBlueprintCommandV1, generate_task_blueprint
from polaris.cells.chief_engineer.blueprint.public.contracts import TaskBlueprintResultV1
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.roles.adapters.public import extract_workspace_quality_summary
from polaris.kernelone.storage import resolve_logical_path


def _executor(workspace: Path) -> OrchestrationStageExecutor:
    return OrchestrationStageExecutor(workspace)


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
        assert results
        assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in summary["source_tools"]
        assert 'import { Note } from "./models/Note.js";' in repaired
        assert "module.exports" not in repaired
        assert "require(" not in repaired

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
        assert results
        assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in summary["source_tools"]
        assert 'import { Note } from "../models/Note.js";' in repaired
        assert "export default AlchemyEngine;" in repaired
        assert "export { buildDefaultEngine };" in repaired
        assert 'export const VERSION = "1.0.0";' in repaired
        assert "module.exports" not in repaired

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
        assert results
        assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in summary["source_tools"]
        assert 'import * as AlchemyEngine from "./engine/AlchemyEngine.js";' in repaired
        assert "const engine = new AlchemyEngine.AlchemyEngine();" in repaired
        assert "const { Note, DreamCard, Recipe } = AlchemyEngine;" in repaired
        assert "module.exports" not in repaired

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
        assert results
        assert "deterministic_javascript_missing_method_runtime_repair" in summary["source_tools"]
        assert "addRecipe(recipe)" in repaired
        assert "transmute(notes)" in repaired
        assert "dreamCards: result.dreamCards ?? result.cards ?? []" in repaired
        assert "rituals: result.rituals ?? []" in repaired

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
        assert results
        assert "deterministic_javascript_missing_method_runtime_repair" in summary["source_tools"]
        assert "run(notes)" in repaired
        assert "const result = this.transmute(notes);" in repaired
        assert "cards: result.cards ?? result.dreamCards ?? []" in repaired
        assert "untouched: result.untouched ?? result.unmatched ?? result.unconsumed ?? result.embers ?? []" in repaired

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
        assert results
        assert "deterministic_javascript_missing_method_runtime_repair" in summary["source_tools"]
        assert "matchesAll(notes)" in repaired
        assert "return this.isSatisfiedBy(notes);" in repaired
        assert "this.keywords = Array.isArray(keywords) ? keywords.map(String) : [];" in repaired
        assert "this.absurdityBoost = Number.isFinite(absurdityBoost) ? absurdityBoost : 0;" in repaired
        assert "this.ritual = ritual;" in repaired

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
        assert results
        assert "deterministic_javascript_missing_method_runtime_repair" in summary["source_tools"]
        assert "const normalizedId" in repaired
        assert "const normalizedNarrative" in repaired
        assert "this.id = normalizedId;" in repaired
        assert "this.narrative = normalizedNarrative;" in repaired
        assert "this.body =" in repaired
        assert "this.tags = Array.isArray(tags) ? tags.map(String) : [];" in repaired
        assert "createdAt: this.createdAt instanceof Date ? this.createdAt.toISOString() : this.createdAt" in repaired
        assert "body: this.body" in repaired
        assert "tags: this.tags" in repaired
        assert "this.fragments = Array.isArray(fragments) ? fragments.map(String) : [];" in repaired
        assert "this.absurdity = Number.isFinite(absurdity) ? absurdity : 0;" in repaired
        assert "this.ritual = ritual;" in repaired
        assert "export function composeTitle" in repaired

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
        assert results
        assert "deterministic_javascript_missing_method_runtime_repair" in summary["source_tools"]
        assert "listRecipes()" in repaired
        assert "return Array.isArray(this.recipes) ? [...this.recipes] : [];" in repaired
        assert "transmute(notes)" in repaired
        assert "dreamCards: result.dreamCards ?? result.cards ?? []" in repaired
        assert "unmatched: result.unmatched ?? result.unconsumed ?? []" in repaired

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
        assert results
        assert "deterministic_javascript_typescript_annotation_repair" in summary["source_tools"]
        assert ": unknown" not in repaired
        assert "): any" not in repaired
        assert "return undefined" not in repaired

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
        assert results
        assert "deterministic_javascript_missing_export_repair" in summary["source_tools"]
        assert "export function run(...args)" in repaired
        assert "return { ok: true, entrypoint };" in repaired

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
        assert results
        assert "deterministic_javascript_missing_export_repair" in summary["source_tools"]
        assert "export const defaultRecipes = new AlchemyEngine().defaultRecipes();" in repaired
        assert "export function defaultRecipes" not in repaired

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
        assert results
        assert "deterministic_javascript_missing_export_repair" in summary["source_tools"]
        assert "export function refineDreamNotes(...args)" in repaired
        assert 'summary: values.join(" | ")' in repaired

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
        assert results
        assert "deterministic_javascript_missing_export_repair" in summary["source_tools"]
        assert "function refineDreamNotes(...args)" in repaired
        assert '"[dream] " + line' in repaired
        assert "return VERSION;" in repaired
        assert 'export const VERSION = "0.2.0";' in repaired

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
        assert results
        assert "deterministic_javascript_missing_export_repair" in summary["source_tools"]
        assert 'export const APP_NAME = "dream-note-alchemy-furnace";' in repaired
        assert 'export const APP_VERSION = "0.1.0";' in repaired
        assert 'export const APP_DESCRIPTION = "Dream note alchemy CLI";' in repaired
        assert "name: APP_NAME" in repaired
        assert "version: APP_VERSION" in repaired
        assert "description: APP_DESCRIPTION" in repaired

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
        assert results
        assert "deterministic_javascript_missing_export_repair" in summary["source_tools"]
        assert 'export const ALCHEMY_FURNACE = "dream-note-alchemy-furnace";' in repaired
        assert "export function refineDreamNote(...args)" in repaired
        assert 'const source = typeof args[0] === "string" ? args[0] : "";' in repaired
        assert "const refined = source.trim();" in repaired
        assert 'tag: refined.length > 0 ? "dream-fragment" : "empty"' in repaired


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
                "context_snapshot_ref": "runtime/contexts/ab/cdef.json",
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
        assert evidence["context_snapshot_ref"] == "runtime/contexts/ab/cdef.json"
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

    def test_workspace_quality_commands_rust_project_include_cargo_check(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "kitchen-flavor-palette"\n', encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")

        assert executor._workspace_quality_commands({}) == [["cargo", "check", "--quiet"]]

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

    def test_workspace_quality_commands_mixed_rust_python_keep_cargo_check_first(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "kitchen-flavor-palette"\n', encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("pub fn ok() {}\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_product.py").write_text("def test_contract():\n    assert True\n", encoding="utf-8")

        commands = executor._workspace_quality_commands({})

        assert commands == [["cargo", "check", "--quiet"]]

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
    @pytest.mark.asyncio
    async def test_repairs_typescript_failures_and_reruns_commands(
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

        assert passed is True
        assert calls == [["npm", "run", "build"], ["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is True
        assert [item["phase"] for item in payload["commands"]] == ["check", "check_after_repair"]
        assert "deterministic_typescript_missing_export_repair" in payload["repair"]["source_tools"]
        assert "deterministic_typescript_tsconfig_lib_repair" in payload["repair"]["source_tools"]

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
    async def test_repairs_typescript_failures_across_multiple_rounds(
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

        assert passed is True
        assert calls == [["npm", "run", "build"], ["npm", "run", "build"], ["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is True
        assert [item["phase"] for item in payload["commands"]] == [
            "check",
            "check_after_repair",
            "check_after_repair_2",
        ]
        assert len(payload["repair"]["rounds"]) == 2
        assert "deterministic_typescript_missing_export_repair" in payload["repair"]["source_tools"]
        assert "deterministic_typescript_missing_member_repair" in payload["repair"]["source_tools"]

    @pytest.mark.asyncio
    async def test_repairs_typescript_enum_member_separator_and_reruns_commands(
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

        assert passed is True
        assert calls == [["npm", "run", "build"], ["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is True
        assert [item["phase"] for item in payload["commands"]] == ["check", "check_after_repair"]
        assert "deterministic_typescript_enum_member_separator_repair" in payload["repair"]["source_tools"]
        assert "  WaningCrescent," in moonphase.read_text(encoding="utf-8")
        assert "  phase: MoonPhase;" in moonphase.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_repairs_typescript_unresolved_identifier_alias_and_reruns_commands(
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

        assert passed is True
        assert calls == [["npm", "run", "build"], ["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is True
        assert [item["phase"] for item in payload["commands"]] == ["check", "check_after_repair"]
        assert "deterministic_typescript_unresolved_identifier_repair" in payload["repair"]["source_tools"]
        repaired_source = simulation.read_text(encoding="utf-8")
        assert "return newState;" in repaired_source
        assert "`${state.moonPhase}`;" in repaired_source
        assert "`${state.humidity}`;" in repaired_source
        assert "`${state.tick}`;" in repaired_source


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

    def test_metadata_indicates_execution(self) -> None:
        assert (
            OrchestrationStageExecutor._metadata_indicates_execution({"task_status_counts": {"completed": 1}}) is True
        )
        assert OrchestrationStageExecutor._metadata_indicates_execution({"task_status_counts": {"pending": 5}}) is False
        assert OrchestrationStageExecutor._metadata_indicates_execution({}) is False

    def test_workspace_materialized_delivery_evidence_recognizes_rust_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("pub fn budget_total() -> u64 { 1 }\n", encoding="utf-8")
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "budget-map"\nversion = "0.1.0"\nedition = "2021"\n',
            encoding="utf-8",
        )

        executor = _executor(tmp_path)

        assert executor._workspace_has_materialized_delivery_evidence([]) is True

    def test_has_execution_evidence_from_completed_delta(self) -> None:
        assert OrchestrationStageExecutor._has_director_execution_evidence(
            attempts=[],
            initial_stats={"completed": 0, "failed": 0},
            final_stats={"completed": 2, "failed": 0},
            converged=False,
        )

    def test_has_execution_evidence_from_attempt_progress(self) -> None:
        assert OrchestrationStageExecutor._has_director_execution_evidence(
            attempts=[{"progress_made": True}],
            initial_stats={"completed": 0, "failed": 0},
            final_stats={"completed": 0, "failed": 0},
            converged=False,
        )

    def test_no_execution_evidence(self) -> None:
        assert not OrchestrationStageExecutor._has_director_execution_evidence(
            attempts=[{"progress_made": False, "metadata": {}}],
            initial_stats={"completed": 0, "failed": 0},
            final_stats={"completed": 0, "failed": 0},
            converged=False,
        )

    def test_is_director_no_materialized_changes_from_message(self) -> None:
        result = CommandResult(run_id="r", status="failed", message="error=director_no_materialized_changes")
        assert OrchestrationStageExecutor._is_director_no_materialized_changes(result) is True

    def test_is_director_no_materialized_changes_false_when_completed(self) -> None:
        result = CommandResult(run_id="r", status="completed", message="director_no_materialized_changes")
        assert OrchestrationStageExecutor._is_director_no_materialized_changes(result) is False

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

    async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
        del kwargs
        return self.results.pop(0)

    def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
        del additional_events
        return True, []


class TestDirectorDispatchLoop:
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
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker
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
            ),
            timeout=0.5,
        )

        assert result.status == "completed"
        assert sorted(executor.started_waits) == ["run-1", "run-2"]
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert [item["status"] for item in per_binding] == ["completed", "completed"]

    @pytest.mark.asyncio
    async def test_director_binding_fanout_terminal_counts_end_wait_even_when_run_status_running(
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
            ) -> CommandResult:
                del service, initial_result, timeout_seconds, cancel_event, abort_checker
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
            ),
            timeout=1.0,
        )

        assert service.queries >= 1
        assert result.status == "failed"
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert per_binding[0]["status"] == "failed"
        assert per_binding[0]["terminal_source"] == "task_status_counts"
        assert per_binding[0]["queried_status"] == "running"
        assert "cancel_signal_sent" not in per_binding[0]

    @pytest.mark.asyncio
    async def test_director_binding_fanout_workspace_terminal_failed_taskboard_ends_wait(
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
            ) -> CommandResult:
                del service, initial_result, timeout_seconds, cancel_event, abort_checker
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
            ),
            timeout=1.0,
        )

        assert service.queries >= 1
        assert result.status == "failed"
        per_binding = (result.metadata or {}).get("per_binding")
        assert isinstance(per_binding, list)
        assert per_binding[0]["status"] == "failed"
        assert per_binding[0]["terminal_source"] == "workspace_taskboard_counts"
        assert per_binding[0]["queried_status"] == "running"
        assert "cancel_signal_sent" not in per_binding[0]

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
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker
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
        )
        result = await executor._execute_director_binding_fanout(
            service=service,
            workspace=str(tmp_path),
            tasks=["TASK-1"],
            base_options={"execution_mode": "parallel", "max_workers": 1},
            bindings=[binding],
            timeout_seconds=10,
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
            {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 2},
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
            {"director_max_rounds": 3, "timeout": 1, "execution_mode": "parallel", "max_workers": 2},
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
            {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 2},
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["error_code"] == "director.binding_fanout_all_failed"
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.binding_fanout_all_failed" in codes
        assert "director.dispatch_converged_after_partial_failure" in codes

    @pytest.mark.asyncio
    async def test_materialization_quality_failure_with_artifacts_enters_quality_gate_handoff(
        self, tmp_path: Path
    ) -> None:
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
            {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 1},
        )

        assert result.status == "success"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["quality_gate_handoff"] is True
        assert payload["failure_stage"] == ""
        assert payload["error_code"] is None
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff" in codes
        assert "director.binding_fanout_all_failed" not in codes

    @pytest.mark.asyncio
    async def test_single_binding_materialization_quality_handoff_stops_before_no_claim_retry(
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

            def _read_claimable_director_task_ids(self, *, limit: int) -> list[str]:
                del limit
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
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker
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
            {"director_max_rounds": 3, "timeout": 1, "execution_mode": "serial", "max_workers": 1},
        )

        assert result.status == "success"
        assert executor.execute_calls == 1
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert len(payload["attempts"]) == 1
        assert payload["quality_gate_handoff"] is True
        assert payload["failure_stage"] == ""
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff_ready" in codes
        assert "director.materialization_quality_handoff" in codes
        assert "director.partial_failure_progress_continued" not in codes

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

            def _read_claimable_director_task_ids(self, *, limit: int) -> list[str]:
                del limit
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
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker
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
            {"director_max_rounds": 3, "timeout": 1, "execution_mode": "serial", "max_workers": 1},
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
    async def test_missing_write_receipt_with_artifacts_enters_quality_gate_handoff(self, tmp_path: Path) -> None:
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
        tasks = [
            {
                "id": "TASK-1",
                "target_files": ["package.json", "src/index.ts", "index.html"],
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
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 2},
        )

        assert result.status == "success"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["quality_gate_handoff"] is True
        assert payload["failure_stage"] == ""
        assert payload["error_code"] is None
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff" in codes
        assert "director.binding_fanout_all_failed" not in codes

    @pytest.mark.asyncio
    async def test_idle_claimable_unresolved_artifacts_do_not_enter_quality_gate_handoff(
        self,
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
        tasks = [
            {
                "id": "TASK-1",
                "target_files": ["package.json", "src/index.ts", "index.html", "README.md"],
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
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 2},
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["quality_gate_handoff"] is False
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["error_code"] == "director.binding_fanout_all_failed"
        assert payload["taskboard"]["converged"] is False
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff" not in codes
        assert "director.taskboard_unresolved_quality_handoff" not in codes
        assert "director.binding_fanout_all_failed" in codes

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
            {"director_max_rounds": 2, "timeout": 1, "execution_mode": "parallel", "max_workers": 2},
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert len(payload["attempts"]) == 2
        assert payload["taskboard"]["converged"] is False
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.taskboard_not_converged" in codes
        assert "director.run_status_non_success" not in codes

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
            {"timeout": 1, "execution_mode": "parallel", "max_workers": 1},
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
            {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 1},
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.dispatch_timeout" in codes
        assert payload.get("error_code") == "director.dispatch_timeout"
        assert "timed out" in (payload.get("root_cause_hint") or "").lower()


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
