from __future__ import annotations

from typing import Any

from polaris.cells.roles.adapters.internal.director import quality_gate


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


def test_post_llm_materialization_guard_routes_runtime_covered_errors(monkeypatch: Any, tmp_path: Any) -> None:
    captured: dict[str, Any] = {}
    diagnostic = "npm package manifest script 'build' recursively invokes itself via build -> build"

    def _collect_errors(
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
        return [diagnostic]

    def _run_runtime_repair(
        adapter: Any,
        *,
        task: dict[str, Any],
        task_id: str,
        artifact_quality_errors: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        captured["task_id"] = task_id
        captured["artifact_quality_errors"] = list(artifact_quality_errors)
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

    monkeypatch.setattr(quality_gate, "_collect_materialization_quality_errors", _collect_errors)
    monkeypatch.setattr(quality_gate, "has_materialization_quality_runtime_repair_coverage", lambda errors: True)
    monkeypatch.setattr(quality_gate, "run_materialization_quality_repairs", _run_runtime_repair)

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
    assert captured["artifact_quality_errors"] == [diagnostic]
    assert results[0]["result"]["source_tool"] == "deterministic_npm_script_contract_repair"
    assert summary["stage"] == "post_llm_materialization_runtime_guard"
    assert summary["attempted"] is True
    assert summary["write_tool_evidence"] is True


def test_post_llm_materialization_guard_does_not_repair_uncovered_errors(monkeypatch: Any, tmp_path: Any) -> None:
    calls: dict[str, int] = {"runtime": 0}

    monkeypatch.setattr(
        quality_gate,
        "_collect_materialization_quality_errors",
        lambda *args, **kwargs: ["unknown artifact quality failure"],
    )
    monkeypatch.setattr(
        quality_gate,
        "has_materialization_quality_runtime_repair_coverage",
        lambda errors: False,
    )

    def _unexpected_runtime_repair(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        calls["runtime"] += 1
        return [], {}

    monkeypatch.setattr(quality_gate, "run_materialization_quality_repairs", _unexpected_runtime_repair)

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
