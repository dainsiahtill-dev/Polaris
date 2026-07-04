from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.adapters.internal.director import materialization_quality_boundary
from polaris.cells.roles.adapters.public import service as public_service


def test_materialization_quality_boundary_preserves_typed_issues_without_fallback(
    monkeypatch: Any,
) -> None:
    diagnostic = "Artifact quality scan failed: declared target file missing 'src/main.py'"
    typed_issue = {
        "code": "declared_target_missing",
        "message": "declared target file missing 'src/main.py'",
        "path": "src/main.py",
        "severity": "error",
        "source": "declared_target_contract",
        "metadata": {"raw": diagnostic},
    }
    captured: dict[str, Any] = {}

    def _unexpected_fallback(errors: list[str]) -> tuple[dict[str, Any], ...]:
        msg = f"fallback parser should not receive typed issue errors: {errors!r}"
        raise AssertionError(msg)

    def _capture_command(command: Any) -> Any:
        captured["errors"] = command.artifact_quality_errors
        captured["issues"] = command.artifact_quality_issues
        return SimpleNamespace(tool_results=(), summary={"attempted": False})

    monkeypatch.setattr(
        materialization_quality_boundary,
        "artifact_quality_issues_from_errors",
        _unexpected_fallback,
    )
    monkeypatch.setattr(
        public_service,
        "run_director_materialization_quality_repair_schedule_result",
        _capture_command,
    )

    tool_results, summary = materialization_quality_boundary.run_materialization_quality_public_boundary(
        SimpleNamespace(),
        task={"id": "TASK-1"},
        task_id="TASK-1",
        artifact_quality_errors=[diagnostic],
        artifact_quality_issues=(typed_issue,),
    )

    assert tool_results == []
    assert summary == {"attempted": False}
    assert captured == {
        "errors": (diagnostic,),
        "issues": (typed_issue,),
    }
