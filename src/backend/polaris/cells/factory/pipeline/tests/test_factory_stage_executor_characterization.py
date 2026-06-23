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
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.roles.adapters.internal.qa_adapter import _extract_workspace_quality_summary
from polaris.kernelone.storage import resolve_logical_path


def _executor(workspace: Path) -> OrchestrationStageExecutor:
    return OrchestrationStageExecutor(workspace)


# ---------------------------------------------------------------------------
# Pure text-shaping helpers
# ---------------------------------------------------------------------------


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
        summary = _extract_workspace_quality_summary(compact)

        assert summary is not None
        assert summary["passed"] is False
        assert summary["command_count"] == 2
        assert summary["prepare_passed_count"] == 1
        assert summary["check_passed_count"] == 0
        assert summary["repair_attempted"] is True
        assert summary["repair_success"] is False

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
        ],
    )
    def test_normalize_declared_delivery_target(self, value: str, expected: str) -> None:
        assert OrchestrationStageExecutor._normalize_declared_delivery_target(value) == expected

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
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {"id": "TASK-1", "target_files": ["package.json"]},
                    {"id": "TASK-2", "target_files": ["src/index.ts"]},
                ]
            },
        )
        run = FactoryRun(
            id="factory-capture-tasks",
            config=FactoryConfig(name="capture-tasks"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )

        result = await executor._execute_director_dispatch(
            run,
            {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 2},
        )

        assert result.status == "success"
        assert executor.captured_tasks == ["TASK-1", "TASK-2"]

    @pytest.mark.asyncio
    async def test_continues_after_partial_fanout_failure_when_taskboard_progresses(self, tmp_path: Path) -> None:
        executor = _PartialFailureProgressExecutor(tmp_path)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {"id": "TASK-1", "target_files": ["package.json"]},
                    {"id": "TASK-2", "target_files": ["src/index.ts"]},
                ]
            },
        )
        run = FactoryRun(
            id="factory-progress",
            config=FactoryConfig(name="progress"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )

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
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {"id": "TASK-1", "target_files": ["package.json"]},
                    {"id": "TASK-2", "target_files": ["src/index.ts"]},
                ]
            },
        )
        run = FactoryRun(
            id="factory-all-bindings-failed",
            config=FactoryConfig(name="all-bindings-failed"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )

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
        executor._write_json_artifact(
            "tasks/plan.json",
            {"tasks": [{"id": "TASK-1", "target_files": ["package.json", "src/index.ts"]}]},
        )
        run = FactoryRun(
            id="factory-quality-handoff",
            config=FactoryConfig(name="quality-handoff"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-22T00:00:00+00:00",
        )

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
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc"},"devDependencies":{"typescript":"latest"}}',
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")

        executor = _SingleBindingQualityHandoffExecutor(tmp_path)
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {"id": "TASK-1", "target_files": ["package.json", "src/index.ts"]},
                    {"id": "TASK-2", "target_files": ["src/engine.ts"], "depends_on": ["TASK-1"]},
                    {"id": "TASK-3", "target_files": ["tests/verify.test.ts"], "depends_on": ["TASK-2"]},
                ]
            },
        )
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
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "target_files": ["package.json", "src/index.ts", "index.html"],
                    }
                ]
            },
        )
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
    async def test_idle_unresolved_artifacts_enter_quality_gate_handoff(self, tmp_path: Path) -> None:
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
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "target_files": ["package.json", "src/index.ts", "index.html", "README.md"],
                    }
                ]
            },
        )
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

        result = await executor._execute_director_dispatch(
            run,
            {"director_max_rounds": 1, "timeout": 1, "execution_mode": "parallel", "max_workers": 2},
        )

        assert result.status == "success"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["quality_gate_handoff"] is True
        assert payload["failure_stage"] == ""
        assert payload["error_code"] is None
        assert payload["taskboard"]["converged"] is False
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff" in codes
        assert "director.taskboard_unresolved_quality_handoff" in codes
        assert "director.taskboard_not_converged" not in codes
        assert "director.binding_fanout_all_failed" not in codes

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
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {"id": "TASK-1", "target_files": ["package.json"]},
                    {"id": "TASK-2", "target_files": ["src/index.ts"]},
                ]
            },
        )
        run = FactoryRun(
            id="factory-no-convergence",
            config=FactoryConfig(name="no-convergence"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )

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
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {"id": "TASK-1", "target_files": ["src/index.ts"]},
                ]
            },
        )
        run = FactoryRun(
            id="factory-timeout",
            config=FactoryConfig(name="timeout"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )

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
