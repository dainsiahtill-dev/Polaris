from __future__ import annotations

from typing import Any

import polaris.kernelone.quality.artifact_quality as artifact_quality_module
from polaris.cells.roles.adapters.internal.director import quality_gate
from polaris.kernelone.quality import artifact_quality_issues_for_errors


class _Adapter:
    def __init__(self, workspace: str) -> None:
        self.workspace = workspace


def _successful_write_result(path: str) -> dict[str, Any]:
    return {
        "tool_name": "write_file",
        "success": True,
        "effect_receipt": {"ok": True},
        "result": {"ok": True, "file": path},
    }


def test_artifact_quality_issues_for_errors_preserves_typed_issue_without_fallback_duplicate() -> None:
    diagnostic = "Artifact quality scan failed: declared target file missing 'src/main.py'"
    typed_issue = {
        "code": "declared_target_missing",
        "message": "declared target file missing 'src/main.py'",
        "path": "src/main.py",
        "severity": "error",
        "source": "artifact_quality",
        "metadata": {"raw": diagnostic},
    }

    issues = artifact_quality_issues_for_errors([diagnostic], (typed_issue,))

    assert issues == (typed_issue,)


def test_artifact_quality_issues_for_errors_only_fallback_parses_residual_errors(monkeypatch: Any) -> None:
    typed_diagnostic = "Artifact quality scan failed: declared target file missing 'src/main.py'"
    residual_diagnostic = "Artifact quality scan failed: syntax error in src/main.py: invalid syntax"
    typed_issue = {
        "code": "declared_target_missing",
        "message": "declared target file missing 'src/main.py'",
        "path": "src/main.py",
        "severity": "error",
        "source": "artifact_quality",
        "metadata": {"raw": typed_diagnostic},
    }
    fallback_issue = {
        "code": "syntax_error",
        "message": "syntax error in src/main.py: invalid syntax",
        "path": "src/main.py",
        "severity": "error",
        "source": "fallback",
        "metadata": {"raw": residual_diagnostic},
    }
    captured_errors: list[str] = []

    def _fallback(errors: list[str]) -> tuple[dict[str, Any], ...]:
        captured_errors.extend(errors)
        return (fallback_issue,)

    monkeypatch.setattr(artifact_quality_module, "artifact_quality_issues_from_errors", _fallback)

    issues = artifact_quality_issues_for_errors(
        [typed_diagnostic, residual_diagnostic],
        (typed_issue,),
    )

    assert captured_errors == [residual_diagnostic]
    assert issues == (typed_issue, fallback_issue)


def test_artifact_quality_issues_for_errors_dedupes_scanner_issues_by_structured_key() -> None:
    diagnostic = "shared raw diagnostic"
    first_issue = {
        "code": "first_issue",
        "message": diagnostic,
        "path": "src/first.py",
        "severity": "error",
        "metadata": {"raw": diagnostic},
    }
    second_issue = {
        "code": "second_issue",
        "message": diagnostic,
        "path": "src/second.py",
        "severity": "error",
        "metadata": {"raw": diagnostic},
    }

    issues = artifact_quality_issues_for_errors(
        [diagnostic],
        (first_issue, second_issue),
    )

    assert issues == (first_issue, second_issue)


def test_collect_materialization_quality_findings_projects_missing_declared_target_as_typed_issue(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    captured_errors: list[str] = []

    def _unexpected_fallback(errors: list[str]) -> tuple[dict[str, Any], ...]:
        captured_errors.extend(errors)
        return ()

    monkeypatch.setattr(quality_gate, "artifact_quality_issues_from_errors", _unexpected_fallback)

    errors, issues = quality_gate._collect_materialization_quality_findings(
        _Adapter(str(tmp_path)),
        task={"target_files": ["src/main.py"]},
        all_affected_files=[],
        workspace_name=tmp_path.name,
    )

    diagnostic = "Artifact quality scan failed: declared target file missing 'src/main.py'"
    assert errors == [diagnostic]
    assert captured_errors == []
    assert issues == (
        {
            "code": "declared_target_missing",
            "message": "declared target file missing 'src/main.py'",
            "path": "src/main.py",
            "severity": "error",
            "source": "declared_target_contract",
            "metadata": {
                "raw": diagnostic,
                "declared_target_path": "src/main.py",
            },
        },
    )


def test_post_llm_materialization_guard_routes_runtime_covered_errors(monkeypatch: Any, tmp_path: Any) -> None:
    captured: dict[str, Any] = {}
    diagnostic = "npm package manifest script 'build' recursively invokes itself via build -> build"
    typed_issue = {
        "code": "npm_manifest_invalid",
        "message": diagnostic,
        "path": "package.json",
        "severity": "error",
        "source": "artifact_quality",
        "metadata": {"raw": diagnostic},
    }

    def _collect_findings(
        adapter: Any,
        *,
        task: dict[str, Any],
        all_affected_files: list[str],
        workspace_name: str,
        context: dict[str, Any] | None = None,
    ) -> list[str]:
        captured["scan_paths"] = list(all_affected_files)
        captured["workspace_name"] = workspace_name
        captured["context"] = dict(context or {})
        return [diagnostic], (typed_issue,)

    def _has_coverage(errors: list[str], *, artifact_quality_issues: tuple[dict[str, Any], ...] = ()) -> bool:
        captured["coverage_errors"] = list(errors)
        captured["coverage_issues"] = tuple(dict(item) for item in artifact_quality_issues)
        return True

    def _run_runtime_repair(
        adapter: Any,
        *,
        task: dict[str, Any],
        task_id: str,
        artifact_quality_errors: list[str],
        artifact_quality_issues: tuple[dict[str, Any], ...] = (),
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        captured["task_id"] = task_id
        captured["artifact_quality_errors"] = list(artifact_quality_errors)
        captured["artifact_quality_issues"] = tuple(dict(item) for item in artifact_quality_issues)
        return (
            [
                {
                    **_successful_write_result("package.json"),
                    "result": {
                        "ok": True,
                        "file": "package.json",
                        "source_tool": "deterministic_npm_script_contract_repair",
                    },
                },
            ],
            {"source_tools": ["deterministic_npm_script_contract_repair"]},
        )

    monkeypatch.setattr(quality_gate, "_collect_materialization_quality_findings", _collect_findings)
    monkeypatch.setattr(quality_gate, "has_materialization_quality_runtime_repair_coverage", _has_coverage)
    monkeypatch.setattr(quality_gate, "_run_materialization_quality_public_boundary", _run_runtime_repair)

    results, summary = quality_gate._run_post_llm_materialization_runtime_guard(
        _Adapter(str(tmp_path)),
        task={"id": "task-1"},
        target_task_id="task-1",
        context={"run_id": "run-1"},
        changed_files=["src/verify.ts"],
        repair_tool_results=[_successful_write_result("package.json")],
    )

    assert captured["scan_paths"] == ["package.json", "src/verify.ts"]
    assert captured["workspace_name"] == tmp_path.name
    assert captured["context"] == {"run_id": "run-1"}
    assert captured["task_id"] == "task-1"
    assert captured["coverage_errors"] == [diagnostic]
    assert captured["coverage_issues"] == (typed_issue,)
    assert captured["artifact_quality_errors"] == [diagnostic]
    assert captured["artifact_quality_issues"] == (typed_issue,)
    assert results[0]["result"]["source_tool"] == "deterministic_npm_script_contract_repair"
    assert summary["stage"] == "post_llm_materialization_runtime_guard"
    assert summary["attempted"] is True
    assert summary["artifact_quality_issue_count"] == 1
    assert summary["write_tool_evidence"] is True


def test_post_llm_materialization_guard_does_not_repair_uncovered_errors(monkeypatch: Any, tmp_path: Any) -> None:
    calls: dict[str, int] = {"runtime": 0}

    monkeypatch.setattr(
        quality_gate,
        "_collect_materialization_quality_findings",
        lambda *args, **kwargs: (["unknown artifact quality failure"], ()),
    )
    monkeypatch.setattr(
        quality_gate,
        "has_materialization_quality_runtime_repair_coverage",
        lambda errors, *, artifact_quality_issues=(): False,
    )

    def _unexpected_runtime_repair(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        calls["runtime"] += 1
        return [], {}

    monkeypatch.setattr(quality_gate, "_run_materialization_quality_public_boundary", _unexpected_runtime_repair)

    results, summary = quality_gate._run_post_llm_materialization_runtime_guard(
        _Adapter(str(tmp_path)),
        task={},
        target_task_id="task-1",
        context={},
        changed_files=["src/main.ts"],
        repair_tool_results=[_successful_write_result("src/main.ts")],
    )

    assert results == []
    assert summary["attempted"] is False
    assert summary["reason"] == "post_llm_errors_not_runtime_covered"
    assert calls["runtime"] == 0
