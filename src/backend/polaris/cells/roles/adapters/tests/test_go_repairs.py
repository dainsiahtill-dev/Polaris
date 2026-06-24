"""Tests for defensive Go import repair planning and execution."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.adapters.internal.director.deterministic_repairs import (
    generic_repairs,
)
from polaris.cells.roles.adapters.internal.director.deterministic_repairs.go_repairs import (
    GoFileRepairPlan,
    GoImportReplacement,
    extract_go_import_paths_from_errors,
    plan_go_module_import_repairs,
)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_extracts_only_supported_go_missing_package_diagnostics() -> None:
    paths = extract_go_import_paths_from_errors(
        [
            "main.go:4:2: no required module provides package old.example/app/internal/store; to add it:",
            "package old.example/app/pkg/api is not in std (/usr/local/go/src/old.example/app/pkg/api)",
            "cannot find module providing package old.example/app/pkg/model",
            "unrelated test failure",
        ]
    )

    assert paths == {
        "old.example/app/internal/store",
        "old.example/app/pkg/api",
        "old.example/app/pkg/model",
    }


def test_planner_rewrites_only_diagnostic_backed_import_literals(
    tmp_path: Path,
) -> None:
    _write_text(tmp_path / "go.mod", "module example.com/new/app\n\ngo 1.22\n")
    _write_text(tmp_path / "internal/store/store.go", "package store\n")
    source = """package main

import (
    alias "old.example/app/internal/store"
    "fmt"
)

const evidence = "old.example/app/internal/store"
"""
    _write_text(tmp_path / "cmd/server/main.go", source)

    plans = plan_go_module_import_repairs(
        tmp_path,
        artifact_quality_errors=[
            "cmd/server/main.go:4:5: no required module provides package old.example/app/internal/store"
        ],
    )

    assert len(plans) == 1
    plan = plans[0]
    assert plan.file == "cmd/server/main.go"
    assert 'alias "example.com/new/app/internal/store"' in plan.content
    assert 'const evidence = "old.example/app/internal/store"' in plan.content
    assert plan.replacements == (
        GoImportReplacement(
            before="old.example/app/internal/store",
            after="example.com/new/app/internal/store",
        ),
    )
    assert (tmp_path / "cmd/server/main.go").read_text(encoding="utf-8") == source


def test_planner_ignores_import_text_inside_comments(tmp_path: Path) -> None:
    _write_text(tmp_path / "go.mod", "module example.com/new/app\n")
    _write_text(tmp_path / "internal/store/store.go", "package store\n")
    source = """package main

/*
import "old.example/app/internal/store"
*/
import "old.example/app/internal/store"
"""
    _write_text(tmp_path / "main.go", source)

    plans = plan_go_module_import_repairs(
        tmp_path,
        artifact_quality_errors=[
            "no required module provides package old.example/app/internal/store"
        ],
    )

    assert len(plans) == 1
    assert '/*\nimport "old.example/app/internal/store"\n*/' in plans[0].content
    assert 'import "example.com/new/app/internal/store"' in plans[0].content


def test_planner_requires_quality_gate_evidence(tmp_path: Path) -> None:
    _write_text(tmp_path / "go.mod", "module example.com/new/app\n")
    _write_text(tmp_path / "internal/store/store.go", "package store\n")
    _write_text(
        tmp_path / "main.go",
        'package main\nimport "old.example/app/internal/store"\n',
    )

    assert plan_go_module_import_repairs(tmp_path, artifact_quality_errors=[]) == []


def test_planner_preserves_declared_external_dependency(tmp_path: Path) -> None:
    _write_text(
        tmp_path / "go.mod",
        "module example.com/new/app\n\nrequire old.example/app v1.2.3\n",
    )
    _write_text(tmp_path / "internal/store/store.go", "package store\n")
    _write_text(
        tmp_path / "main.go",
        'package main\nimport "old.example/app/internal/store"\n',
    )

    plans = plan_go_module_import_repairs(
        tmp_path,
        artifact_quality_errors=[
            "no required module provides package old.example/app/internal/store"
        ],
    )

    assert plans == []


class _Adapter:
    def __init__(self, workspace: Path) -> None:
        self.workspace = str(workspace)
        self._execution = SimpleNamespace(_message_bus=object())
        self.progress: list[tuple[str, str, str]] = []

    def _update_task_progress(
        self, task_id: str, status: str, *, current_file: str
    ) -> None:
        self.progress.append((task_id, status, current_file))


def test_executor_uses_real_write_tool_evidence(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    plan = GoFileRepairPlan(
        file="main.go",
        content='package main\nimport "example.com/new/app/internal/store"\n',
        replacements=(
            GoImportReplacement(
                before="old.example/app/internal/store",
                after="example.com/new/app/internal/store",
            ),
        ),
    )
    monkeypatch.setattr(
        generic_repairs,
        "plan_go_module_import_repairs",
        lambda *_args, **_kwargs: [plan],
    )
    calls: list[tuple[str, dict[str, Any], str]] = []

    def fake_execute_tool(
        _self: Any,
        tool_name: str,
        args: dict[str, Any],
        *,
        task_id: str = "",
    ) -> dict[str, Any]:
        calls.append((tool_name, args, task_id))
        return {
            "ok": True,
            "file": "main.go",
            "bytes_written": 64,
            "operation": "modify",
            "broadcast_ok": True,
            "director_policy": {"allowed": True},
        }

    monkeypatch.setattr(
        generic_repairs.DirectorToolExecutor, "execute_tool", fake_execute_tool
    )
    adapter = _Adapter(tmp_path)

    results = generic_repairs._apply_deterministic_go_module_import_repair(
        adapter,
        task_id="task-1",
        artifact_quality_errors=[
            "no required module provides package old.example/app/internal/store"
        ],
    )

    assert calls == [
        (
            "write_file",
            {"file": "main.go", "content": plan.content},
            "task-1",
        )
    ]
    assert results[0]["success"] is True
    assert results[0]["result"]["bytes_written"] == 64
    assert results[0]["result"]["director_policy"] == {"allowed": True}
    assert adapter.progress == [("task-1", "executing", "main.go")]


def test_executor_preserves_failed_tool_result_as_failure(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    plan = GoFileRepairPlan(
        file="main.go",
        content="package main\n",
        replacements=(GoImportReplacement(before="old/app", after="new/app"),),
    )
    monkeypatch.setattr(
        generic_repairs,
        "plan_go_module_import_repairs",
        lambda *_args, **_kwargs: [plan],
    )
    monkeypatch.setattr(
        generic_repairs.DirectorToolExecutor,
        "execute_tool",
        lambda *_args, **_kwargs: {
            "ok": False,
            "error": "Director write policy denied",
            "error_type": "director_write_policy_denied",
            "blocked": True,
        },
    )
    adapter = _Adapter(tmp_path)

    results = generic_repairs._apply_deterministic_go_module_import_repair(
        adapter,
        task_id="task-2",
        artifact_quality_errors=["no required module provides package old/app"],
    )

    assert results[0]["success"] is False
    assert results[0]["result"]["ok"] is False
    assert results[0]["result"]["error_type"] == "director_write_policy_denied"
    assert adapter.progress == []
