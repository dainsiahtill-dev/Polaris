from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    ControlPlaneRunLedgerV1Error,
    ReadRunLedgerProjectionQueryV1,
    TaskBoundaryFailureClassV1 as PublicTaskBoundaryFailureClassV1,
    append_run_ledger_event,
    read_run_ledger_projection,
)
from polaris.cells.control_plane.run_ledger.public.task_boundary import (
    ReprojectTaskBoundaryVerdictCommandV1,
    TaskBoundaryFailureClassV1,
    evaluate_task_boundary_verdict,
    normalize_task_boundary_verdict,
    reconcile_task_boundary_artifacts_with_workspace,
    reproject_task_boundary_verdict,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)


def test_task_boundary_reports_incomplete_materialization(tmp_path: Path) -> None:
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        target_files=["src/index.js"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "incomplete_materialization"
    assert verdict["failure_class"] == "INCOMPLETE_MATERIALIZATION"
    assert verdict["responsible_layer"] == "director"
    assert verdict["missing_target_files"] == ["src/index.js"]


def test_r181_reconcile_promotes_on_disk_pending_to_completed(tmp_path: Path) -> None:
    """Stale downstream_pending must not contradict files already on disk."""

    (tmp_path / "package.json").write_text('{"name":"garden"}\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export const x = 1;\n", encoding="utf-8")
    completed, pending = reconcile_task_boundary_artifacts_with_workspace(
        workspace=tmp_path,
        target_files=["src/main.ts"],
        completed_artifacts=["index.html"],
        downstream_pending_artifacts=["package.json", "src/main.ts", "src/missing.ts"],
    )
    assert "package.json" in completed
    assert "src/main.ts" in completed
    assert "index.html" in completed
    assert "src/missing.ts" in pending
    assert "package.json" not in pending
    assert "src/main.ts" not in pending


def test_r181_evaluate_boundary_ok_when_pending_files_already_on_disk(tmp_path: Path) -> None:
    """r181 false-incomplete: declared pending artifacts exist → completed_verified."""

    (tmp_path / "package.json").write_text(
        '{"name":"garden","main":"dist/main.js","scripts":{"build":"tsc"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("export function main(): void {}\n", encoding="utf-8")
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="3",
        run_id="factory_r181",
        target_files=["src/main.ts"],
        completed_artifacts=[],
        downstream_pending_artifacts=["package.json", "src/main.ts", "src/models/index.ts"],
    ).to_dict()
    # src/models/index.ts still missing → may fail incomplete if it is a target,
    # but as downstream-only pending, reconcile drops only on-disk paths.
    assert "package.json" in verdict["completed_artifacts"]
    assert "src/main.ts" in verdict["completed_artifacts"]
    assert "src/models/index.ts" in verdict["downstream_pending_artifacts"]
    assert verdict["ok"] is True
    assert verdict["status"] == "completed_verified"


def test_task_boundary_failure_class_is_public_contract_export() -> None:
    assert PublicTaskBoundaryFailureClassV1.MISSING_ENTRYPOINT_TARGET is (
        TaskBoundaryFailureClassV1.MISSING_ENTRYPOINT_TARGET
    )


def test_normalize_task_boundary_verdict_canonicalizes_failure_class_aliases() -> None:
    assert (
        normalize_task_boundary_verdict({"failure_class": "incomplete-materialization"})["failure_class"]
        == TaskBoundaryFailureClassV1.INCOMPLETE_MATERIALIZATION.value
    )
    assert (
        normalize_task_boundary_verdict({"failure_class": "missing entrypoint target"})["failure_class"]
        == TaskBoundaryFailureClassV1.MISSING_ENTRYPOINT_TARGET.value
    )
    assert (
        normalize_task_boundary_verdict({"failure_class": "missing-effect-receipt"})["failure_class"]
        == "MISSING_EFFECT_RECEIPT"
    )


def test_task_boundary_reports_missing_package_entrypoint_when_not_declared_downstream(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"start": "node src/index.js"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        target_files=["package.json"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "missing_entrypoint_target"
    assert verdict["failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert verdict["missing_entrypoint_targets"] == ["src/index.js"]


def test_task_boundary_allows_package_entrypoint_declared_downstream(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"start": "node src/index.js"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        target_files=["package.json"],
        downstream_pending_artifacts=["src/index.js"],
    ).to_dict()

    assert verdict["ok"] is True
    assert verdict["status"] == "completed_verified"


def test_task_boundary_ignores_missing_package_build_artifact_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "main": "dist/main.js",
                "module": "build/main.mjs",
                "scripts": {"start": "node dist/main.js"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        target_files=["package.json"],
    ).to_dict()

    assert verdict["ok"] is True
    assert verdict["status"] == "completed_verified"
    assert verdict["missing_entrypoint_targets"] == []


def test_task_boundary_reports_unresolved_local_import_in_current_source(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "meteor.js").write_text(
        'import { simpleHash32 } from "./_util/hash.js";\n'
        "export function meteorId(seed) { return simpleHash32(seed); }\n",
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1-source-modules",
        run_id="run-1",
        target_files=["src/meteor.js"],
        completed_artifacts=["src/meteor.js"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "unresolved_local_import"
    assert verdict["failure_class"] == "UNRESOLVED_LOCAL_IMPORT"
    assert verdict["responsible_layer"] == "director"
    assert verdict["unresolved_local_imports"] == ["src/meteor.js -> ./_util/hash.js (src/_util/hash.js)"]


def test_task_boundary_does_not_inherit_unresolved_import_from_other_task_completed_file(
    tmp_path: Path,
) -> None:
    """Live L2-17: TASK-2 must not fail-close on TASK-1-entrypoints barrel paths."""

    models_dir = tmp_path / "src" / "models"
    models_dir.mkdir(parents=True)
    (models_dir / "index.ts").write_text(
        "export { brandId } from './models/types.js';\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "web.ts").write_text(
        "export function boot(): void {}\n",
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-2",
        run_id="run-2",
        target_files=["src/web.ts"],
        completed_artifacts=["src/models/index.ts", "src/web.ts"],
        downstream_pending_artifacts=["src/verify.ts"],
    ).to_dict()

    assert verdict["ok"] is True
    assert verdict["status"] == "completed_verified"
    assert verdict["unresolved_local_imports"] == []


def test_task_boundary_reports_test_framework_content_in_non_test_source(tmp_path: Path) -> None:
    src_dir = tmp_path / "src" / "models"
    src_dir.mkdir(parents=True)
    (src_dir / "humidity.ts").write_text(
        'import { describe, it, expect } from "vitest";\n'
        "describe('humidity model', () => {\n"
        "  it('checks comfort', () => expect(1).toBe(1));\n"
        "});\n",
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1-source-modules",
        run_id="run-1",
        target_files=["src/models/humidity.ts"],
        completed_artifacts=["src/models/humidity.ts"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "artifact_semantic_mismatch"
    assert verdict["failure_class"] == "IMPLEMENTATION_DEFECT"
    assert verdict["responsible_layer"] == "director"
    assert verdict["artifact_semantic_mismatches"] == [
        "src/models/humidity.ts: non-test source contains test framework structure"
    ]


def test_task_boundary_allows_test_framework_content_in_test_source(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "humidity.test.ts").write_text(
        'import { describe, it, expect } from "vitest";\n'
        "describe('humidity model', () => {\n"
        "  it('checks comfort', () => expect(1).toBe(1));\n"
        "});\n",
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-2-tests",
        run_id="run-1",
        target_files=["tests/humidity.test.ts"],
        completed_artifacts=["tests/humidity.test.ts"],
    ).to_dict()

    assert verdict["ok"] is True
    assert verdict["status"] == "completed_verified"
    assert verdict["artifact_semantic_mismatches"] == []


def test_task_boundary_allows_local_import_declared_downstream(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "meteor.js").write_text(
        'import { simpleHash32 } from "./_util/hash.js";\n'
        "export function meteorId(seed) { return simpleHash32(seed); }\n",
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1-source-modules",
        run_id="run-1",
        target_files=["src/meteor.js"],
        completed_artifacts=["src/meteor.js"],
        downstream_pending_artifacts=["src/_util/hash.js"],
    ).to_dict()

    assert verdict["ok"] is True
    assert verdict["status"] == "completed_verified"
    assert verdict["unresolved_local_imports"] == []


def test_task_boundary_allows_existing_local_import_target(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    (src_dir / "_util").mkdir(parents=True)
    (src_dir / "_util" / "hash.js").write_text("export const simpleHash32 = () => 0;\n", encoding="utf-8")
    (src_dir / "meteor.js").write_text(
        'import { simpleHash32 } from "./_util/hash.js";\n'
        "export function meteorId(seed) { return simpleHash32(seed); }\n",
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1-source-modules",
        run_id="run-1",
        target_files=["src/meteor.js"],
        completed_artifacts=["src/meteor.js"],
    ).to_dict()

    assert verdict["ok"] is True
    assert verdict["status"] == "completed_verified"
    assert verdict["unresolved_local_imports"] == []


@pytest.mark.parametrize(
    ("specifier_name", "source_name"),
    [
        ("util.js", "util.ts"),
        ("widget.js", "widget.tsx"),
        ("helper.mjs", "helper.mts"),
        ("legacy.cjs", "legacy.cts"),
    ],
)
def test_task_boundary_allows_nodenext_typescript_sibling_for_emitted_specifier(
    tmp_path: Path, specifier_name: str, source_name: str
) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.ts").write_text(
        f'import {{ x }} from "./{specifier_name}";\nconsole.log(x);\n',
        encoding="utf-8",
    )
    (src_dir / source_name).write_text("export const x = 1;\n", encoding="utf-8")

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1-nodenext",
        run_id="run-1",
        target_files=["src/main.ts", f"src/{source_name}"],
        completed_artifacts=["src/main.ts", f"src/{source_name}"],
    ).to_dict()

    assert verdict["ok"] is True
    assert verdict["status"] == "completed_verified"
    assert verdict["unresolved_local_imports"] == []


def test_task_boundary_reports_unresolved_js_specifier_without_typescript_sibling(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.ts").write_text('import { x } from "./util.js";\nconsole.log(x);\n', encoding="utf-8")

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1-nodenext-missing",
        run_id="run-1",
        target_files=["src/main.ts"],
        completed_artifacts=["src/main.ts"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "unresolved_local_import"
    assert verdict["failure_class"] == "UNRESOLVED_LOCAL_IMPORT"
    assert verdict["unresolved_local_imports"] == ["src/main.ts -> ./util.js (src/util.js)"]


def test_task_boundary_mjs_specifier_not_rescued_by_plain_ts_sibling(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.ts").write_text('import { x } from "./helper.mjs";\nconsole.log(x);\n', encoding="utf-8")
    (src_dir / "helper.ts").write_text("export const x = 1;\n", encoding="utf-8")

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1-nodenext-mjs",
        run_id="run-1",
        target_files=["src/main.ts", "src/helper.ts"],
        completed_artifacts=["src/main.ts", "src/helper.ts"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "unresolved_local_import"
    assert verdict["failure_class"] == "UNRESOLVED_LOCAL_IMPORT"
    assert verdict["unresolved_local_imports"] == ["src/main.ts -> ./helper.mjs (src/helper.mjs)"]


def test_task_boundary_ignores_package_script_glob_patterns(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test:python": "python -m unittest discover -s tests -p 'test_*.py' -v",
                    "test:node": "node --test tests/**/*.test.js",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        target_files=["package.json"],
    ).to_dict()

    assert verdict["ok"] is True
    assert verdict["status"] == "completed_verified"
    assert verdict["missing_entrypoint_targets"] == []


def test_task_boundary_ignores_inline_javascript_in_package_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "copy:html": (
                        "node -e \"import('node:fs').then(fs=>"
                        "fs.copyFileSync('index.html','dist/index.html'))\""
                    )
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-inline-script",
        run_id="run-inline-script",
        target_files=["package.json", "index.html"],
        completed_artifacts=["package.json", "index.html"],
    ).to_dict()

    assert verdict["ok"] is True
    assert verdict["missing_entrypoint_targets"] == []


def test_task_boundary_reprojection_supersedes_false_failure_without_role_replay(tmp_path: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="task_boundary_reprojection_test",
        )
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "copy:html": (
                        "node -e \"import('node:fs').then(fs=>"
                        "fs.copyFileSync('index.html','dist/index.html'))\""
                    )
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    run_id = "director-reproject-1"
    task_id = "TASK-3"
    old_verdict = {
        "schema_version": "polaris.task_boundary_verdict.v1",
        "task_id": task_id,
        "run_id": run_id,
        "status": "missing_entrypoint_target",
        "ok": False,
        "failure_class": "MISSING_ENTRYPOINT_TARGET",
        "responsible_layer": "task_boundary",
        "reason": "legacy parser treated inline JavaScript as an entrypoint",
        "target_files": ["package.json", "index.html"],
        "completed_artifacts": ["package.json", "index.html"],
        "missing_entrypoint_targets": [
            "import('node:fs').then(fs=>fs.copyFileSync('index.html','dist/index.html'))"
        ],
    }
    old_receipt = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id=run_id,
            event={
                "event_type": "task_boundary_verdict",
                "stage": "task_boundary",
                "task_id": task_id,
                "run_id": run_id,
                "task_boundary_verdict": old_verdict,
                "job_token": {"run_id": run_id, "task_id": task_id, "project_id": task_id},
            },
        )
    ).receipt
    old_content_id = str(old_receipt["event"]["content_id"])
    command = ReprojectTaskBoundaryVerdictCommandV1(
        workspace=str(tmp_path),
        run_id=run_id,
        task_id=task_id,
        expected_content_id=old_content_id,
        reason="task_boundary_inline_script_parser_fixed",
    )

    result = reproject_task_boundary_verdict(command)
    repeated = reproject_task_boundary_verdict(command)
    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id=run_id, max_runs=1)
    ).projection

    assert result.status == "reprojected"
    assert result.changed is True
    assert result.idempotent is False
    assert result.verdict["ok"] is True
    assert result.verdict["reprojection"]["pm_restart_allowed"] is False
    assert result.verdict["reprojection"]["chief_engineer_restart_allowed"] is False
    assert result.verdict["reprojection"]["director_restart_allowed"] is False
    assert repeated.status == "idempotent"
    assert repeated.content_id == result.content_id
    assert projection["task_boundary"]["verdict_count"] == 2
    assert projection["task_boundary"]["historical_failed_count"] == 1
    assert projection["task_boundary"]["latest_by_task"][task_id]["ok"] is True

    with pytest.raises(ControlPlaneRunLedgerV1Error, match="task_boundary_reprojection_stale_predecessor"):
        reproject_task_boundary_verdict(
            ReprojectTaskBoundaryVerdictCommandV1(
                workspace=str(tmp_path),
                run_id=run_id,
                task_id=task_id,
                expected_content_id="f" * 64,
                reason="stale",
            )
        )


def test_task_boundary_reports_missing_html_script_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<html><body><script src="src/app.js"></script></body></html>',
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        target_files=["index.html"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert verdict["missing_entrypoint_targets"] == ["src/app.js"]


def test_task_boundary_does_not_treat_html_js_entrypoint_with_ts_source_as_scope_gap(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "web.ts").write_text("export function startBrowser(): void {}\n", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<html><body><script type="module" src="./src/web.js"></script></body></html>',
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-2",
        run_id="run-1",
        target_files=["index.html", "src/web.ts"],
        completed_artifacts=["index.html", "src/web.ts"],
    ).to_dict()

    assert verdict["ok"] is True
    assert verdict["status"] == "completed_verified"
    assert verdict["missing_entrypoint_targets"] == []


def test_task_boundary_reports_missing_go_main_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.22\n", encoding="utf-8")

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        target_files=["go.mod"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert verdict["missing_entrypoint_targets"] == ["main.go"]


def test_task_boundary_reports_missing_pyproject_script_module(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1.0'\n[project.scripts]\ndemo = 'src.main:main'\n",
        encoding="utf-8",
    )

    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        target_files=["pyproject.toml"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert verdict["missing_entrypoint_targets"] == ["src/main.py"]


def test_task_boundary_reports_tool_dispatch_dropped(tmp_path: Path) -> None:
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        tool_dispatch={"status": "dropped", "native_tool_calls_count": 1},
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "tool_dispatch_dropped"
    assert verdict["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert verdict["responsible_layer"] == "execution_control_plane"


def test_task_boundary_preserves_text_fallback_not_dispatched(tmp_path: Path) -> None:
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        tool_dispatch={
            "status": "blocked",
            "failure_class": "REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED",
            "text_fallback_requested": True,
            "parser_attempted": True,
            "reason": "text fallback parser produced no calls",
        },
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "required_tool_text_fallback_not_dispatched"
    assert verdict["failure_class"] == "REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED"
    assert verdict["responsible_layer"] == "execution_control_plane"
    assert verdict["tool_dispatch"]["parser_attempted"] is True


def test_task_boundary_reports_blocked_dependencies(tmp_path: Path) -> None:
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-2",
        run_id="run-1",
        blocked_dependencies=["TASK-1"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "dependency_not_unlocked"
    assert verdict["failure_class"] == "DEPENDENCY_NOT_UNLOCKED"
    assert verdict["blocked_dependencies"] == ["TASK-1"]


def test_task_boundary_reports_missing_required_evidence(tmp_path: Path) -> None:
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        required_evidence_modalities=["command", "tool_effect"],
        present_evidence_modalities=["tool_effect"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "execution_evidence_missing"
    assert verdict["failure_class"] == "EXECUTION_EVIDENCE_MISSING"
    assert verdict["missing_required_evidence_modalities"] == ["command"]


def test_task_boundary_reports_failed_required_evidence(tmp_path: Path) -> None:
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        required_evidence_modalities=["command"],
        failed_required_evidence_modalities=["command"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "required_evidence_failed"
    assert verdict["failure_class"] == "COMPILER_OR_TEST_FAILURE"
    assert verdict["failed_required_evidence_modalities"] == ["command"]


def test_task_boundary_failed_evidence_takes_precedence_over_missing_flag(tmp_path: Path) -> None:
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        required_evidence_modalities=["command"],
        missing_required_evidence_modalities=["command"],
        failed_required_evidence_modalities=["command"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "required_evidence_failed"
    assert verdict["failure_class"] == "COMPILER_OR_TEST_FAILURE"
    assert verdict["missing_required_evidence_modalities"] == []


def test_task_boundary_reports_missing_required_verifier(tmp_path: Path) -> None:
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        required_verifiers=["python -m unittest"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "required_verifier_missing"
    assert verdict["failure_class"] == "EXECUTION_EVIDENCE_MISSING"
    assert verdict["missing_required_verifiers"] == ["python -m unittest"]


def test_task_boundary_reports_failed_required_verifier(tmp_path: Path) -> None:
    verdict = evaluate_task_boundary_verdict(
        workspace=tmp_path,
        task_id="TASK-1",
        run_id="run-1",
        required_verifiers=["npm test"],
        failed_required_verifiers=["npm test"],
    ).to_dict()

    assert verdict["ok"] is False
    assert verdict["status"] == "required_verifier_failed"
    assert verdict["failure_class"] == "COMPILER_OR_TEST_FAILURE"
    assert verdict["failed_required_verifiers"] == ["npm test"]
