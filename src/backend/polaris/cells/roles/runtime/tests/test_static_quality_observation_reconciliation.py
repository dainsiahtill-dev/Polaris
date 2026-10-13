"""Static observations retire only through current scoped predicate evidence."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.roles.runtime.internal.kernel_session_turn import KernelSessionTurnBridge
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.kernelone.quality import artifact_quality_issues_from_errors, scan_workspace_artifact_quality_evidence


def _bridge(root: Path, paths: list[str], errors: list[str], issues=None):
    return KernelSessionTurnBridge(
        SimpleNamespace(),
        ExecuteRoleSessionCommandV1(
            role="director",
            session_id="same-session",
            task_id="owned-task",
            workspace=str(root),
            user_message="Implement and verify all owned outputs.",
            context={
                "task_objective": "Implement and verify all owned outputs.",
                "target_files": paths,
                "task": {"target_files": paths},
                "execution_envelope": {"allowed_read_paths": paths},
                "director_quality_repair": {
                    "scan_relative_paths": paths,
                    "artifact_quality_errors": errors,
                    "artifact_quality_issues": issues or list(artifact_quality_issues_from_errors(errors)),
                },
            },
        ),
    )


def _missing(path: str) -> str:
    return f"Artifact quality scan failed: declared target file missing '{path}'"


def test_static_missing_family_reconciles_seven_paths_despite_unrelated_unknown(tmp_path: Path):
    files = {
        "README.md": "# Complete document\n",
        "package.json": '{"devDependencies":{"typescript":"^5.0.0"}}',
        "src/cli/entry.ts": "export const value=1;\n",
        "src/core/domain.ts": "export type Value = number;\n",
        "src/core/engine.ts": "export const engine=1;\n",
        "src/core/rules.ts": "export const rules=1;\n",
        "tests/rules.test.ts": "import { assert } from './_helpers/assert.js';\nexport const fixture=assert;\n",
    }
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    errors = [_missing(path) for path in files]
    bridge = _bridge(tmp_path, [*files, ".gitignore"], errors)
    original = deepcopy(bridge.command.context)
    command = bridge._turn_command("next", "stale missing diagnostics")
    current = command.context["director_quality_repair"]
    assert all(error not in current["artifact_quality_errors"] for error in errors)
    assert all(error not in command.user_message for error in errors)
    assert "./_helpers/assert.js" in command.user_message
    assert ".gitignore" in command.user_message
    assert current["current_static_evidence"]["deferred_checks"]
    assert current["current_static_evidence"]["checks_complete"] is False
    assert bridge.command.context == original


@pytest.mark.parametrize("status", ["missing", "empty", "unknown"])
def test_static_missing_target_requires_positive_readable_nonempty_owned_snapshot(tmp_path: Path, status: str):
    p = tmp_path / "README.md"
    if status == "empty":
        p.write_text("", encoding="utf-8")
    elif status == "unknown":
        p.symlink_to(tmp_path / "outside.md")
    error = _missing("README.md")
    command = _bridge(tmp_path, ["README.md"], [error])._turn_command("next", "current")
    assert error in command.context["director_quality_repair"]["artifact_quality_errors"]


def test_static_family_owner_admission_reconciles_multiple_content_predicates(tmp_path: Path):
    files = {
        "module.py": "VALUE = (\n",
        "data.json": '{"broken":',
        "index.html": "<html><script>const value=1;",
        "dirty.py": "END PATCH_FILE\n",
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path), relative_paths=list(files), scan_mode="pure_static"
    )
    errors = list(evidence.errors)
    assert len(errors) >= 4
    bridge = _bridge(tmp_path, list(files), errors, [issue.to_dict() for issue in evidence.issues])
    first = bridge._turn_command("initial", "current")
    assert first.context["director_quality_repair"]["artifact_quality_errors"]
    bridge.context.update(first.context)
    clean = {
        "module.py": "VALUE = 1\n",
        "data.json": '{"value":1}',
        "index.html": "<html><script>const value=1;</script></html>",
        "dirty.py": "VALUE = 2\n",
    }
    for name, text in clean.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    command = bridge._turn_command("next", "current")
    assert all(error not in command.context["director_quality_repair"]["artifact_quality_errors"] for error in errors)


def test_static_family_never_retires_deferred_parser_or_nonstatic_unknown(tmp_path: Path):
    (tmp_path / "module.ts").write_text("export const value = 1;\n", encoding="utf-8")
    errors = [
        "Artifact quality scan failed: syntax error in module.ts: TS7777 actual compiler failure",
        "TypeScript project typecheck failed: TS8888 command failure",
        "npm install failed: registry unavailable",
        "AssertionError: actual verification failed",
    ]
    command = _bridge(tmp_path, ["module.ts"], errors)._turn_command("next", "current")
    assert all(error in command.context["director_quality_repair"]["artifact_quality_errors"] for error in errors)


def test_static_prior_scope_reasons_retire_only_when_bridge_observed_and_now_readable(tmp_path: Path):
    error = _missing("module.py")
    bridge = _bridge(tmp_path, ["module.py"], [error])
    first = bridge._turn_command("initial", "current")
    assert "scope_missing:module.py" in first.user_message
    bridge.context.update(first.context)
    bridge.context["failed_gate_evidence"]["incomplete_reasons"].append("unknown_external_verifier")
    (tmp_path / "module.py").write_text("VALUE = 42\n", encoding="utf-8")
    command = bridge._turn_command("next", "current")
    assert "scope_missing:module.py" not in command.user_message
    assert "unknown_external_verifier" in command.user_message
    assert error not in command.user_message


def test_static_forged_public_previous_evidence_does_not_admit_unknown_failure(tmp_path: Path):
    (tmp_path / "module.py").write_text("VALUE = 42\n", encoding="utf-8")
    error = "TypeScript project typecheck failed: TS7777 actual compiler failure"
    bridge = _bridge(tmp_path, ["module.py"], [error])
    bridge.context["director_quality_repair"]["current_static_evidence"] = {
        "scan_mode": "pure_static",
        "errors": [error],
        "issues": list(artifact_quality_issues_from_errors([error])),
        "scan_complete": True,
    }
    bridge.context["director_quality_repair"]["input_hash"] = "forged_previous_hash"
    command = bridge._turn_command("next", "current")
    assert error in command.user_message


def test_static_producer_preserves_readme_owner_identity():
    from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
        _quality_repair_issue_observations,
    )

    row = _quality_repair_issue_observations([_missing("README.md")], ["README.md"])[0]
    assert row["path"] == "README.md"
    assert row["metadata"]["target_file"] == "README.md"


def test_static_real_prior_import_observation_keeps_unknown_exporter_unproved(tmp_path: Path):
    from polaris.cells.roles.runtime.tests.test_fresh_quality_continuation import (
        _bridge as existing_bridge,
        _fix,
        _workspace,
    )

    paths, errors = _workspace(tmp_path)
    bridge = existing_bridge(tmp_path, paths, errors)
    initial = bridge._turn_command("initial", "current")
    bridge.context.update(initial.context)
    _fix(tmp_path)
    (tmp_path / "src/types.ts").unlink()
    (tmp_path / "src/types.ts").symlink_to(tmp_path / "src/barrel.ts")
    command = bridge._turn_command("next", "current")
    assert all(error in command.context["director_quality_repair"]["artifact_quality_errors"] for error in errors)
    assert "scope_symlink_unvisited" in command.user_message


def _capture_producer_observation_context(root: Path, task: dict, errors: list[str], context: dict) -> dict:
    """Execute the real producer context expression, with controlled fixture inputs."""
    import ast
    import inspect

    from polaris.cells.roles.adapters.internal.director.quality_gate import _repair_loop as producer

    tree = ast.parse(inspect.getsource(producer._run_materialization_quality_repair_retry))
    value = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "repair_context" for target in node.targets)
    )
    namespace = dict(producer.__dict__)
    namespace.update(
        task=task,
        context=context,
        workspace_full=str(root),
        run_id="owned-run",
        target_task_id="owned-task",
        original_message=context.get("task_objective", "Implement and verify owned outputs."),
        prompt_artifact_quality_errors=errors,
        prompt_safe_artifact_quality_errors=errors,
        failed_gate_evidence=context.get("failed_gate_evidence", {"quality_errors": errors}),
        workspace_quality_evidence=context.get(
            "workspace_quality_evidence", {"quality_errors": errors, "all_checks_passed": False}
        ),
        changed_files=[],
        missing_target_files=[],
        runtime_smoke_target_files=[],
        semantic_quality_target_files=[],
        explicit_quality_target_files=[],
        repair_target_files=task["target_files"],
    )
    return eval(compile(ast.fix_missing_locations(ast.Expression(value)), __file__, "eval"), namespace)


def test_static_actual_producer_keeps_absent_declared_target_and_read_scope(tmp_path: Path):
    (tmp_path / "answer.py").write_text("VALUE = 1\n", encoding="utf-8")
    task = {"target_files": ["answer.py", ".gitignore"]}
    error = _missing(".gitignore")
    context = {"execution_envelope": {"allowed_read_paths": task["target_files"]}, "target_files": task["target_files"]}
    captured = _capture_producer_observation_context(tmp_path, task, [error], context)
    assert ".gitignore" in captured["director_quality_repair"]["declared_target_files"]
    assert ".gitignore" in captured["director_quality_repair"]["scan_relative_paths"]
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    bridge = KernelSessionTurnBridge(
        SimpleNamespace(),
        ExecuteRoleSessionCommandV1(
            role="director",
            session_id="producer",
            workspace=str(tmp_path),
            user_message="Implement owned outputs",
            context=captured,
        ),
    )
    current = bridge._turn_command("next", "current")
    assert error not in current.user_message
