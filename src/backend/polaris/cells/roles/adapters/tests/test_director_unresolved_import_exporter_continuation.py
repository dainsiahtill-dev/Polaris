"""Unresolved-import continuation must include the in-scope exporter module."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
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


def test_execute_method_quality_helper_annotates_unresolved_import_continuation(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Quality loop imports execute_method._helpers, not quality_gate wrapper."""

    from polaris.cells.roles.adapters.internal.director.execute_method import _helpers as helpers_module

    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("from src.engine import KNOWN_RULES\n", encoding="utf-8")
    (src / "radio.py").write_text("def broadcast() -> str:\n    return ''\n", encoding="utf-8")
    (src / "main.py").write_text("def main() -> None:\n    return None\n", encoding="utf-8")
    (src / "engine").mkdir()
    (src / "engine" / "__init__.py").write_text("from src.engine.forecast import known_rules\n", encoding="utf-8")

    def _fake_runtime(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        del args, kwargs
        return [], {
            "plan_probe_preaudit": {
                "status": "coverage_matched_but_unplannable",
                "covered_unplannable_source_tools": ["deterministic_unresolved_import_symbol_repair"],
                "covered_unplannable_diagnostic_count": 1,
            }
        }

    monkeypatch.setattr(
        helpers_module,
        "run_materialization_quality_public_boundary",
        _fake_runtime,
    )
    _tool_results, summary = helpers_module._run_materialization_quality_public_boundary(
        SimpleNamespace(workspace=str(tmp_path)),
        task={"target_files": ["src/__init__.py", "src/radio.py", "src/main.py"]},
        task_id="TASK-3-source-modules",
        artifact_quality_errors=[
            (
                "Artifact quality scan failed: unresolved import symbol "
                "'KNOWN_RULES' from 'src.engine' in src/__init__.py "
                "(sibling module does not define it)"
            )
        ],
    )
    assert summary["task_boundary_director_continuation_allowed"] is True
    assert summary["task_boundary_continuation_reason"] == "current_task_unresolved_import"
    assert summary["task_boundary_continuation_target_files"] == ["src/__init__.py"]
    assert _materialization_plan_probe_requires_task_boundary_triage(summary) is False


def test_unresolved_import_errors_outside_write_scope_are_deferred(
    tmp_path: Path,
) -> None:
    """Live L2-12 TASK-3-source-core: Mood/Weather on ``src/__init__.py``.

    Those diagnostics belong to TASK-1.  They must not fail source-core or
    block LLM repair of the in-scope ``known_weather_to_genre`` exporter.
    """

    from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
        _filter_unresolved_import_errors_to_task_write_scope,
    )

    errors = [
        (
            "Artifact quality scan failed: unresolved import symbol 'Mood' "
            "from 'src.models' in src/__init__.py (sibling module does not define it)"
        ),
        (
            "Artifact quality scan failed: unresolved import symbol 'Weather' "
            "from 'src.models' in src/__init__.py (sibling module does not define it)"
        ),
        (
            "Artifact quality scan failed: unresolved import symbol "
            "'known_weather_to_genre' from 'src.engine.forecast' in "
            "src/engine/__init__.py (sibling module does not define it)"
        ),
    ]
    retained = _filter_unresolved_import_errors_to_task_write_scope(
        errors,
        task={"target_files": ["src/engine/__init__.py", "src/engine/forecast.py"]},
    )
    assert retained == [errors[2]]

    modules_only_from_tests = [
        (
            "Artifact quality scan failed: unresolved import symbol 'Mood' "
            "from 'src.models' in tests/test_product.py (sibling module does not define it)"
        ),
        (
            "Artifact quality scan failed: unresolved import symbol 'parse_mood' "
            "from 'src.models' in tests/test_product.py (sibling module does not define it)"
        ),
    ]
    assert (
        _filter_unresolved_import_errors_to_task_write_scope(
            modules_only_from_tests,
            task={"target_files": ["src/__init__.py", "src/radio.py", "src/main.py"]},
        )
        == []
    )


@pytest.mark.asyncio
async def test_mixed_exporter_scope_does_not_block_in_scope_forecast_repair(
    tmp_path: Path,
) -> None:
    """Live L2-12 173: Mood on src/__init__.py must not block forecast.py edit."""

    from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
        _run_materialization_quality_repair_retry,
    )

    src = tmp_path / "src"
    (src / "models").mkdir(parents=True)
    (src / "engine").mkdir(parents=True)
    (src / "__init__.py").write_text("from src.models import Mood\n", encoding="utf-8")
    (src / "models" / "__init__.py").write_text("__all__ = ['Mood']\n", encoding="utf-8")
    (src / "engine" / "__init__.py").write_text(
        "from src.engine.forecast import known_weather_to_genre\n",
        encoding="utf-8",
    )
    (src / "engine" / "forecast.py").write_text("def forecast():\n    return None\n", encoding="utf-8")
    invoked: list[str] = []

    class _Execution:
        @staticmethod
        def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
            del result
            return []

        @staticmethod
        async def execute_tools(
            content: str,
            target_task_id: str,
            update_task_progress: Any,
            **_: Any,
        ) -> list[dict[str, Any]]:
            del content, target_task_id, update_task_progress
            return []

    class _Adapter:
        workspace = str(tmp_path)
        _execution = _Execution()
        _update_task_progress = staticmethod(lambda *args, **kwargs: None)

        async def _invoke_role_dialogue_with_timeout(
            self,
            message: str,
            *,
            context: dict[str, Any],
            timeout_seconds: float,
            stage_label: str,
        ) -> dict[str, Any]:
            del context, timeout_seconds, stage_label
            invoked.append(message)
            return {"success": True, "content": "", "tool_results": []}

    errors = [
        (
            "Artifact quality scan failed: unresolved import symbol 'Mood' "
            "from 'src.models' in src/__init__.py (sibling module does not define it)"
        ),
        (
            "Artifact quality scan failed: unresolved import symbol "
            "'known_weather_to_genre' from 'src.engine.forecast' in "
            "src/engine/__init__.py (sibling module does not define it)"
        ),
    ]
    _tools, summary = await _run_materialization_quality_repair_retry(
        _Adapter(),
        task={
            "id": "TASK-3-source-core",
            "target_files": ["src/engine/__init__.py", "src/engine/forecast.py"],
        },
        target_task_id="TASK-3-source-core",
        run_id="run-173",
        context={},
        original_message="Repair engine exports.",
        llm_call_timeout=1.0,
        artifact_quality_errors=errors,
        changed_files=["src/engine/__init__.py", "src/engine/forecast.py"],
        repair_attempt=2,
    )
    assert summary.get("stage") != "task_boundary_semantic_exporter_scope_conflict"
    assert summary.get("llm_fallback_blocked") is not True
    assert invoked, "in-scope forecast repair must reach Director LLM"
