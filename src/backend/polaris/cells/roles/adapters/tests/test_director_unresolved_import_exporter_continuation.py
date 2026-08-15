"""Unresolved-import continuation must include the in-scope exporter module."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.adapters.internal.director import quality_gate as quality_gate_module
from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
    _materialization_plan_probe_requires_task_boundary_triage,
)


def test_unresolved_import_continuation_uses_in_scope_exporter_module(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """L2-12 source-core: importer ``__init__.py`` is out of write-scope.

    The quality diagnostic names ``from 'src.engine.forecast'``. That file is
    the owner task target, so Director must continue locally instead of
    fail-closing ``director_materialization_quality_failed``.
    """

    engine = tmp_path / "src" / "engine"
    engine.mkdir(parents=True)
    (engine / "__init__.py").write_text(
        "from src.engine.forecast import known_weather_to_genre\n",
        encoding="utf-8",
    )
    (engine / "forecast.py").write_text("def forecast():\n    return None\n", encoding="utf-8")

    def _fake_runtime_boundary(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        del args, kwargs
        return [], {
            "plan_probe_preaudit": {
                "status": "coverage_matched_but_unplannable",
                "covered_unplannable_source_tools": ["deterministic_unresolved_import_symbol_repair"],
                "covered_unplannable_diagnostic_count": 1,
            }
        }

    monkeypatch.setattr(
        quality_gate_module,
        "run_materialization_quality_public_boundary",
        _fake_runtime_boundary,
    )

    _tool_results, summary = quality_gate_module._run_materialization_quality_public_boundary(
        SimpleNamespace(workspace=str(tmp_path)),
        task={"target_files": ["src/engine/forecast.py"]},
        task_id="TASK-3-source-core",
        artifact_quality_errors=[
            (
                "Artifact quality scan failed: unresolved import symbol "
                "'known_weather_to_genre' from 'src.engine.forecast' in "
                "src/engine/__init__.py (sibling module does not define it)"
            )
        ],
    )

    assert summary["task_boundary_director_continuation_allowed"] is True
    assert summary["task_boundary_continuation_reason"] == "current_task_unresolved_import"
    assert summary["task_boundary_continuation_target_files"] == ["src/engine/forecast.py"]
    assert _materialization_plan_probe_requires_task_boundary_triage(summary) is False
