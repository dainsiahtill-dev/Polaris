"""Characterization tests for ``OrchestrationStageExecutor`` helper clusters.

These tests freeze the *current* behavior of the pure helpers, artifact
filesystem I/O, mirroring, package.json parsing, real-subprocess quality
command execution, the director-evidence truth tables, and the PM/text-shaping
glue BEFORE the god-class is decomposed into sibling collaborators. They exist
to guard a behavior-preserving refactor; they assert observed outputs derived
from reading the source, not idealized contracts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
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
        assert executor._workspace_quality_commands({}) == [["npm", "test"], ["npm", "run", "build"]]

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

    def test_real_subprocess_nonzero_exit(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = executor._run_workspace_quality_command([sys.executable, "-c", "import sys; sys.exit(3)"], 30.0)
        assert result["exit_code"] == 3
        assert result["passed"] is False

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
                "export type SimulationState = unknown;" in repaired_source
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


# ---------------------------------------------------------------------------
# Director-evidence truth tables
# ---------------------------------------------------------------------------


class TestDirectorEvidenceStatics:
    def test_is_taskboard_converged(self) -> None:
        assert OrchestrationStageExecutor._is_taskboard_converged(
            {"pending": 0, "ready": 0, "in_progress": 0, "blocked": 0}
        )
        assert not OrchestrationStageExecutor._is_taskboard_converged({"pending": 1})

    def test_has_director_progress(self) -> None:
        before = {"completed": 0}
        after = {"completed": 1}
        assert OrchestrationStageExecutor._has_director_progress(before, after) is True
        assert OrchestrationStageExecutor._has_director_progress(before, before) is False

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

    def _resolve_director_binding_fanout(self) -> list[dict[str, str]]:
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

            def _resolve_director_binding_fanout(self) -> list[dict[str, str]]:
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

            def _resolve_director_binding_fanout(self) -> list[dict[str, str]]:
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

            def _resolve_director_binding_fanout(self) -> list[dict[str, str]]:
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
