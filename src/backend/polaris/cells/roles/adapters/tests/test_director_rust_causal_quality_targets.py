"""Rust behavioral verifier failures must route to their implementation owner."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
    _run_materialization_quality_repair_retry,
)
from polaris.cells.roles.adapters.public import resolve_director_causal_quality_repair_target_files


def test_cmake_missing_source_routes_to_declaring_manifest(tmp_path: Path) -> None:
    """A missing CMake source is repaired at the manifest, not outside task scope.

    Exact L3-24 r27 declared ``CMakeLists.txt`` in TASK-1 but the generated
    manifest referenced two undeclared, absent ``.cpp`` files.  QA preserved
    the full CMake diagnostic; causal target discovery nevertheless omitted the
    manifest, leaving Factory with only out-of-scope missing paths and an
    unrelated header.  The existing manifest is the legal owner of its source
    list and must reach the owner-selection frontier.
    """

    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(sample LANGUAGES CXX)\n"
        "add_library(sample src/existing.cpp src/missing.cpp)\n",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "existing.cpp").write_text("int existing() { return 1; }\n", encoding="utf-8")
    errors = [
        "### FAILING_TUS src/existing.cpp\n"
        "### src/existing.cpp\n"
        "src/existing.cpp:1:1: error: 'missing_symbol' was not declared in this scope",
        "Artifact quality scan failed: workspace validation command failed (cmake --build build):\n"
        "CMake Error at CMakeLists.txt:3 (add_library):\n"
        "  Cannot find source file:\n\n"
        "    src/missing.cpp\n\n"
        "CMake Error at CMakeLists.txt:3 (add_library): No SOURCES given to target: sample"
    ]

    targets = resolve_director_causal_quality_repair_target_files(
        artifact_quality_errors=errors,
        changed_files=["CMakeLists.txt", "src/existing.cpp"],
        workspace_full=str(tmp_path),
    )

    assert targets[0] == "CMakeLists.txt"


def test_rust_failed_test_title_routes_to_matching_implementation_module(tmp_path: Path) -> None:
    """A failing ``patience_*`` test must not force repair onto ``src/lib.rs``.

    Exact L3-23 r19 reported three behavioral assertions in ``tests/edges.rs``.
    The previous causal resolver returned no Rust implementation target, so the
    owner fallback forced ``src/lib.rs`` and the repair invented a nonexistent
    API.  Resolve the module named by the failed tests and referenced Rust types
    while keeping the later CE/JobToken scope check authoritative.
    """

    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "fantasy_restaurant"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "domain.rs").write_text(
        "pub struct PartyId(String);\npub struct Tick(u32);\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "patience.rs").write_text(
        "pub enum PatienceOutcome { None }\npub struct PatienceTracker;\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "lib.rs").write_text(
        "pub mod domain;\npub mod patience;\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "edges.rs").write_text(
        "use fantasy_restaurant::domain::{PartyId, Tick};\n"
        "use fantasy_restaurant::patience::{PatienceOutcome, PatienceTracker};\n\n"
        "#[test]\n"
        "fn patience_tick_with_same_value_is_a_noop() {\n"
        "    let tracker = PatienceTracker;\n"
        "    let _ = (tracker, PartyId, Tick, PatienceOutcome::None);\n"
        "}\n",
        encoding="utf-8",
    )
    errors = [
        "---- patience_tick_with_same_value_is_a_noop stdout ----\n"
        "thread 'patience_tick_with_same_value_is_a_noop' panicked at tests/edges.rs:7:5:\n"
        "assertion `left == right` failed\n  left: Some(3)\n right: Some(5)"
    ]

    targets = resolve_director_causal_quality_repair_target_files(
        artifact_quality_errors=errors,
        changed_files=["src/domain.rs", "src/patience.rs", "src/lib.rs", "tests/edges.rs"],
        workspace_full=str(tmp_path),
    )

    assert targets == ["src/patience.rs"]


@pytest.mark.asyncio
async def test_rust_behavior_residual_refreshes_factory_forced_repair_target(tmp_path: Path) -> None:
    """A changed verifier class must replace the stale Factory repair target.

    Exact L3-23 r19 first repaired compiler errors in ``src/lib.rs``.  Cargo
    revalidation then exposed only ``patience_*`` behavior failures, but the
    live repair loop retained the old Factory-forced ``src/lib.rs`` lease even
    though its causal resolver identified ``src/patience.rs``.
    """

    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "fantasy_restaurant"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "domain.rs").write_text(
        "pub struct PartyId(String);\npub struct Tick(u32);\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "patience.rs").write_text(
        "pub enum PatienceOutcome { None }\npub struct PatienceTracker;\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "lib.rs").write_text(
        "pub mod domain;\npub mod patience;\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "edges.rs").write_text(
        "use fantasy_restaurant::domain::{PartyId, Tick};\n"
        "use fantasy_restaurant::patience::{PatienceOutcome, PatienceTracker};\n\n"
        "#[test]\n"
        "fn patience_tick_with_same_value_is_a_noop() {\n"
        "    let tracker = PatienceTracker;\n"
        "    let _ = (tracker, PartyId, Tick, PatienceOutcome::None);\n"
        "}\n",
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
            del timeout_seconds, stage_label
            captured["message"] = message
            captured["context"] = context
            return {"content": "", "tool_results": []}

    adapter = _RecordingAdapter(
        _execution=SimpleNamespace(
            extract_kernel_tool_results=lambda result: [],
            execute_tools=lambda *args, **kwargs: _no_tools(),
        )
    )
    error_text = (
        "---- patience_tick_with_same_value_is_a_noop stdout ----\n"
        "thread 'patience_tick_with_same_value_is_a_noop' panicked at tests/edges.rs:7:5:\n"
        "assertion `left == right` failed\n  left: Some(3)\n right: Some(5)"
    )

    _, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task={
            "task_id": "TASK-1",
            "target_files": ["src/domain.rs", "src/lib.rs", "src/patience.rs"],
            "metadata": {"external_task_id": "TASK-1", "factory_run_id": "factory_rust_behavior"},
        },
        target_task_id="TASK-1",
        run_id="factory_rust_behavior",
        context={
            "run_id": "factory_rust_behavior",
            "director_quality_repair": {"repair_target_files": ["src/lib.rs"]},
        },
        original_message="Repair the failing patience behavior.",
        llm_call_timeout=30.0,
        artifact_quality_errors=[error_text],
        changed_files=["src/domain.rs", "src/lib.rs", "src/patience.rs", "tests/edges.rs"],
        repair_attempt=2,
    )

    assert summary["repair_target_files"] == ["src/patience.rs"]
