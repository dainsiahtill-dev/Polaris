"""Regression tests for defensive Go repair planning and execution."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director.deterministic_repairs import (
    generic_repairs,
)
from polaris.cells.roles.adapters.internal.director.deterministic_repairs.go_repairs import (
    GoFileRepairPlan,
    GoRepairBlocker,
    GoRepairPlan,
    extract_go_duplicate_names_from_errors,
    extract_go_import_paths_from_errors,
    plan_go_repairs,
)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_extracts_supported_go_diagnostics_only() -> None:
    import_paths = extract_go_import_paths_from_errors(
        [
            "main.go:4:2: no required module provides package old.example/app/internal/store; to add it:",
            "package old.example/app/pkg/api is not in std (/usr/local/go/src/old.example/app/pkg/api)",
            "cannot find module providing package old.example/app/pkg/model",
            "unrelated test failure",
        ]
    )
    duplicate_names = extract_go_duplicate_names_from_errors(
        [
            "b.go:3:6: Hello redeclared in this block",
            "method (*Server).Start already declared at a.go:8:18",
        ]
    )

    assert import_paths == {
        "old.example/app/internal/store",
        "old.example/app/pkg/api",
        "old.example/app/pkg/model",
    }
    assert duplicate_names == {"Hello", "Start"}


def test_planner_rewrites_only_diagnostic_backed_import_literals(
    tmp_path: Path,
) -> None:
    _write_text(tmp_path / "go.mod", "module example.com/new/app\n\ngo 1.22\n")
    _write_text(tmp_path / "internal/store/store.go", "package store\n")
    source = """package main

/* import "old.example/app/internal/store" */
import alias "old.example/app/internal/store"

const evidence = "old.example/app/internal/store"
"""
    _write_text(tmp_path / "cmd/server/main.go", source)

    plan = plan_go_repairs(
        tmp_path,
        artifact_quality_errors=[
            "cmd/server/main.go:4:8: no required module provides package old.example/app/internal/store"
        ],
    )

    assert len(plan.writes) == 1
    write = plan.writes[0]
    assert write.file == "cmd/server/main.go"
    assert 'alias "example.com/new/app/internal/store"' in write.content
    assert '/* import "old.example/app/internal/store" */' in write.content
    assert 'const evidence = "old.example/app/internal/store"' in write.content
    assert write.repair_kinds == ("go_import_path",)
    assert plan.blockers == ()
    assert (tmp_path / "cmd/server/main.go").read_text(encoding="utf-8") == source


def test_planner_repairs_unique_local_subpath_only(tmp_path: Path) -> None:
    _write_text(tmp_path / "go.mod", "module example.com/app\n")
    _write_text(tmp_path / "src/engine/engine.go", "package engine\n")
    _write_text(
        tmp_path / "main.go",
        'package main\nimport "example.com/app/hallucinated/src/engine"\n',
    )

    plan = plan_go_repairs(
        tmp_path,
        artifact_quality_errors=[
            "package example.com/app/hallucinated/src/engine is not in std"
        ],
    )

    assert len(plan.writes) == 1
    assert plan.writes[0].content.endswith('import "example.com/app/src/engine"\n')
    assert plan.blockers == ()


def test_planner_requires_quality_gate_evidence(tmp_path: Path) -> None:
    _write_text(tmp_path / "go.mod", "module example.com/new/app\n")
    _write_text(tmp_path / "internal/store/store.go", "package store\n")
    source = 'package main\nimport "old.example/app/internal/store"\n'
    _write_text(tmp_path / "main.go", source)

    plan = plan_go_repairs(tmp_path, artifact_quality_errors=[])

    assert plan == GoRepairPlan(writes=(), blockers=())
    assert (tmp_path / "main.go").read_text(encoding="utf-8") == source


def test_planner_preserves_declared_external_dependency(tmp_path: Path) -> None:
    _write_text(
        tmp_path / "go.mod",
        "module example.com/new/app\n\nrequire old.example/app v1.2.3\n",
    )
    _write_text(tmp_path / "internal/store/store.go", "package store\n")
    source = 'package main\nimport "old.example/app/internal/store"\n'
    _write_text(tmp_path / "main.go", source)

    plan = plan_go_repairs(
        tmp_path,
        artifact_quality_errors=[
            "no required module provides package old.example/app/internal/store"
        ],
    )

    assert plan.writes == ()
    assert plan.blockers[0].code == "go_import_repair_not_safe"
    assert "declared dependency" in plan.blockers[0].message
    assert (tmp_path / "main.go").read_text(encoding="utf-8") == source


def test_planner_comments_only_token_identical_duplicate(tmp_path: Path) -> None:
    _write_text(tmp_path / "go.mod", "module example.com/app\n")
    declaration = "func Hello(name string) string {\n\treturn name\n}\n"
    _write_text(tmp_path / "a.go", f"package app\n\n{declaration}")
    duplicate_source = f"package app\n\n{declaration}"
    _write_text(tmp_path / "b.go", duplicate_source)

    plan = plan_go_repairs(
        tmp_path,
        artifact_quality_errors=[
            "./b.go:3:6: Hello redeclared in this block\n"
            "./a.go:3:6: other declaration of Hello"
        ],
    )

    assert len(plan.writes) == 1
    assert plan.writes[0].file == "b.go"
    assert (
        "// [polaris deterministic repair] exact duplicate func Hello:"
        in plan.writes[0].content
    )
    assert plan.blockers == ()
    assert (tmp_path / "b.go").read_text(encoding="utf-8") == duplicate_source


def test_planner_blocks_nonidentical_duplicate_instead_of_merging_or_deleting(
    tmp_path: Path,
) -> None:
    _write_text(tmp_path / "go.mod", "module example.com/app\n")
    first = "package app\nfunc Hello() int { return 1 }\n"
    second = "package app\nfunc Hello() int { return 2 }\n"
    _write_text(tmp_path / "a.go", first)
    _write_text(tmp_path / "b.go", second)

    plan = plan_go_repairs(
        tmp_path,
        artifact_quality_errors=["b.go:2:6: Hello redeclared in this block"],
    )

    assert plan.writes == ()
    assert plan.blockers[0].code == "go_duplicate_declarations_differ"
    assert (tmp_path / "a.go").read_text(encoding="utf-8") == first
    assert (tmp_path / "b.go").read_text(encoding="utf-8") == second


class _Adapter:
    def __init__(self, workspace: Path) -> None:
        self.workspace = str(workspace)
        self._execution = SimpleNamespace(_message_bus=object())
        self.progress: list[tuple[str, str, str]] = []

    def _update_task_progress(
        self,
        task_id: str,
        status: str,
        *,
        current_file: str,
    ) -> None:
        self.progress.append((task_id, status, current_file))


def test_executor_uses_real_director_write_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write = GoFileRepairPlan(
        file="main.go",
        content='package main\nimport "example.com/app/internal/store"\n',
        repair_kinds=("go_import_path",),
        evidence=("compiler_diagnostic:old->new",),
    )
    monkeypatch.setattr(
        generic_repairs,
        "plan_go_repairs",
        lambda *_args, **_kwargs: GoRepairPlan(writes=(write,), blockers=()),
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
        generic_repairs.DirectorToolExecutor,
        "execute_tool",
        fake_execute_tool,
    )
    adapter = _Adapter(tmp_path)

    results = generic_repairs._apply_deterministic_go_repairs(
        adapter,
        task_id="task-1",
        artifact_quality_errors=["missing package"],
    )

    assert calls == [
        (
            "write_file",
            {"file": "main.go", "content": write.content},
            "task-1",
        )
    ]
    assert results[0]["success"] is True
    assert results[0]["result"]["bytes_written"] == 64
    assert results[0]["result"]["director_policy"] == {"allowed": True}
    assert adapter.progress == [("task-1", "executing", "main.go")]


def test_executor_preserves_write_failure_and_planner_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write = GoFileRepairPlan(
        file="main.go",
        content="package main\n",
        repair_kinds=("go_import_path",),
        evidence=("compiler_diagnostic:old->new",),
    )
    blocker = GoRepairBlocker(
        code="go_duplicate_declarations_differ",
        message="automatic merge is blocked",
        evidence=("a.go:2", "b.go:2"),
        files=("a.go", "b.go"),
    )
    monkeypatch.setattr(
        generic_repairs,
        "plan_go_repairs",
        lambda *_args, **_kwargs: GoRepairPlan(
            writes=(write,),
            blockers=(blocker,),
        ),
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

    results = generic_repairs._apply_deterministic_go_repairs(
        adapter,
        task_id="task-2",
        artifact_quality_errors=["duplicate"],
    )

    assert results[0]["success"] is False
    assert results[0]["result"]["error_type"] == "director_write_policy_denied"
    assert results[1]["success"] is False
    assert results[1]["result"]["error_type"] == blocker.code
    assert results[1]["result"]["blocked"] is True
    assert adapter.progress == []
