"""Repeat-attempt repair target widening (same-task escalation) tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
    _materialized_task_declared_target_files,
    _run_materialization_quality_repair_retry,
)


def test_materialized_task_declared_targets_prefers_existing_files(tmp_path: Path) -> None:
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "models" / "moon.hpp").write_text("#pragma once\n", encoding="utf-8")
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "generator.hpp").write_text("#pragma once\n", encoding="utf-8")

    task = {
        "target_files": [
            "src/engine/generator.hpp",
            "src/engine/generator.cpp",  # not materialized in this fixture
            "src/models/moon.hpp",
        ],
        "metadata": {"scope_paths": ["./src/models/moon.hpp", "README.md"]},
    }

    targets = _materialized_task_declared_target_files(task, str(tmp_path))

    assert targets == ["src/engine/generator.hpp", "src/models/moon.hpp"]


@pytest.mark.asyncio
async def test_repeat_attempt_widens_authorized_targets_to_task_scope(tmp_path: Path) -> None:
    """Attempt >= 2 must authorize the claimed task's own materialized files.

    Live L1-06: every round anchored authorization on generator.hpp/.cpp
    while the missing ``MoonError`` declaration belonged in moon.hpp — a
    same-task file the tool-path contract forbade editing.
    """

    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "generator.hpp").write_text(
        '#pragma once\n#include "models/moon.hpp"\n'
        "namespace moonpost {\nstd::string phase_of(const Moon&, MoonError err = MoonError::Ok);\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "generator.cpp").write_text('#include "engine/generator.hpp"\n', encoding="utf-8")
    (tmp_path / "src" / "models" / "moon.hpp").write_text(
        "#pragma once\nnamespace moonpost {\nstruct Moon { int phase; };\n}\n",
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    async def _no_tools() -> list[dict[str, Any]]:
        return []

    class _RecordingAdapter(SimpleNamespace):
        workspace = str(tmp_path)

        def _promote_task_contract_to_runtime_context(self, *, task, context, workspace) -> None:
            del task, context, workspace

        @staticmethod
        def _update_task_progress(*args: Any, **kwargs: Any) -> None:
            del args, kwargs

        async def _invoke_role_dialogue_with_timeout(self, message, *, context, timeout_seconds, stage_label):
            captured["message"] = message
            captured["context"] = context
            return {"content": "", "tool_results": []}

    adapter = _RecordingAdapter(
        _execution=SimpleNamespace(
            extract_kernel_tool_results=lambda result: [],
            execute_tools=lambda *args, **kwargs: _no_tools(),
        )
    )

    task = {
        "task_id": "TASK-1-source-core",
        "target_files": [
            "src/engine/generator.cpp",
            "src/engine/generator.hpp",
            "src/models/moon.hpp",
        ],
        "metadata": {"external_task_id": "TASK-1-source-core", "factory_run_id": "factory_widen_test"},
    }
    error_text = (
        "Workspace validation failed: In file included from src/engine/generator.cpp:1:\n"
        "src/engine/generator.hpp:3:45: error: 'MoonError' has not been declared\n"
    )

    results, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task=task,
        target_task_id="TASK-1-source-core",
        run_id="factory_widen_test",
        context={"run_id": "factory_widen_test"},
        original_message="Repair the missing MoonError declaration.",
        llm_call_timeout=30.0,
        artifact_quality_errors=[error_text],
        changed_files=["src/engine/generator.cpp", "src/engine/generator.hpp", "src/models/moon.hpp"],
        repair_attempt=2,
    )

    del results
    assert summary["repair_target_files"] == [
        "src/engine/generator.cpp",
        "src/engine/generator.hpp",
        "src/models/moon.hpp",
    ]
    message = str(captured.get("message") or "")
    assert "src/models/moon.hpp" in message
