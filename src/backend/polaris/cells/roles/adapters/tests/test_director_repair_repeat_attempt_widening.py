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


def _recording_adapter(tmp_path: Path, captured: dict[str, Any]) -> Any:
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
            captured["invoked"] = True
            return {"content": "", "tool_results": []}

    return _RecordingAdapter(
        _execution=SimpleNamespace(
            extract_kernel_tool_results=lambda result: [],
            execute_tools=lambda *args, **kwargs: _no_tools(),
        )
    )


@pytest.mark.asyncio
async def test_repeat_attempt_defers_cross_task_declaration_home(tmp_path: Path) -> None:
    """Attempt >= 2 must rebind when the declaration home is another PM task.

    Live L1-06: TASK-1-source-core owns generator.* while MoonError belongs in
    moon.hpp (TASK-1-source-models). Mixed in-scope + out-of-scope used to keep
    the LLM on the use site, so Factory never saw task_boundary_repair_targets_deferred.
    """

    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "generator.hpp").write_text(
        '#pragma once\n#include "models/moon.hpp"\nnamespace moonpost { void use(MoonError err = MoonError::Ok); }\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "generator.cpp").write_text('#include "engine/generator.hpp"\n', encoding="utf-8")
    (tmp_path / "src" / "models" / "moon.hpp").write_text(
        "#pragma once\nnamespace moonpost { struct Moon { int phase; }; }\n",
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}
    adapter = _recording_adapter(tmp_path, captured)
    error_text = (
        "Workspace validation failed: In file included from src/engine/generator.cpp:1:\n"
        "src/engine/generator.hpp:3:45: error: 'MoonError' has not been declared\n"
    )

    results, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task={
            "task_id": "TASK-1-source-core",
            "target_files": ["src/engine/generator.cpp", "src/engine/generator.hpp"],
            "metadata": {"external_task_id": "TASK-1-source-core", "factory_run_id": "factory_widen_cross"},
        },
        target_task_id="TASK-1-source-core",
        run_id="factory_widen_cross",
        context={"run_id": "factory_widen_cross"},
        original_message="Repair the missing MoonError declaration.",
        llm_call_timeout=30.0,
        artifact_quality_errors=[error_text],
        changed_files=["src/engine/generator.cpp", "src/engine/generator.hpp", "src/models/moon.hpp"],
        repair_attempt=1,
    )

    assert results == []
    assert captured.get("invoked") is None
    assert summary["stage"] == "task_boundary_repair_targets_deferred"
    assert summary["llm_fallback_blocked"] is True
    assert summary["repair_target_files"] == []
    scope_filter = summary["task_boundary_scope_filter"]
    out_of_scope = scope_filter.get("out_of_scope_repair_target_files") or []
    assert "src/models/moon.hpp" in out_of_scope
    assert "src/engine/generator.hpp" not in out_of_scope


@pytest.mark.asyncio
async def test_missing_member_defers_to_struct_owner_header(tmp_path: Path) -> None:
    """g++ 'struct Moon has no member' must rebind to moon.hpp, not keep editing the use site."""

    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "generator.cpp").write_text(
        '#include "engine/generator.hpp"\nvoid f(const moonpost::Moon& m) { (void)m.last_error; }\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine" / "generator.hpp").write_text("#pragma once\n", encoding="utf-8")
    (tmp_path / "src" / "models" / "moon.hpp").write_text(
        "#pragma once\nnamespace moonpost { struct Moon { int phase; }; }\n",
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}
    adapter = _recording_adapter(tmp_path, captured)
    error_text = (
        "src/engine/generator.cpp:159:22: error: ‘const struct moonpost::Moon’ has no member named ‘last_error’\n"
    )

    results, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task={
            "task_id": "TASK-1-source-core",
            "target_files": ["src/engine/generator.cpp", "src/engine/generator.hpp"],
            "metadata": {"external_task_id": "TASK-1-source-core", "factory_run_id": "factory_member"},
        },
        target_task_id="TASK-1-source-core",
        run_id="factory_member",
        context={"run_id": "factory_member"},
        original_message="Repair the missing Moon member.",
        llm_call_timeout=30.0,
        artifact_quality_errors=[error_text],
        changed_files=["src/engine/generator.cpp", "src/models/moon.hpp"],
        repair_attempt=1,
    )

    assert results == []
    assert captured.get("invoked") is None
    assert summary["stage"] == "task_boundary_repair_targets_deferred"
    out_of_scope = (summary["task_boundary_scope_filter"] or {}).get("out_of_scope_repair_target_files") or []
    assert "src/models/moon.hpp" in out_of_scope


@pytest.mark.asyncio
async def test_missing_member_defers_even_when_factory_forced_only_use_site(tmp_path: Path) -> None:
    """Factory diagnostic targets are use-site paths; still rebind the owner header."""

    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "generator.cpp").write_text(
        "void f(const moonpost::Moon& m) { (void)m.status; }\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "models" / "moon.hpp").write_text(
        "#pragma once\nnamespace moonpost { struct Moon { int phase; }; }\n",
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}
    adapter = _recording_adapter(tmp_path, captured)
    error_text = "src/engine/generator.cpp:162:22: error: ‘const struct moonpost::Moon’ has no member named ‘status’\n"

    results, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task={
            "task_id": "TASK-1-source-core",
            "target_files": ["src/engine/generator.cpp", "src/engine/generator.hpp"],
            "metadata": {"external_task_id": "TASK-1-source-core", "factory_run_id": "factory_forced_use_site"},
        },
        target_task_id="TASK-1-source-core",
        run_id="factory_forced_use_site",
        context={
            "run_id": "factory_forced_use_site",
            "director_quality_repair": {"repair_target_files": ["src/engine/generator.cpp"]},
        },
        original_message="Repair the missing Moon member.",
        llm_call_timeout=30.0,
        artifact_quality_errors=[error_text],
        changed_files=["src/engine/generator.cpp", "src/models/moon.hpp"],
        repair_attempt=2,
    )

    assert results == []
    assert captured.get("invoked") is None
    assert summary["stage"] == "task_boundary_repair_targets_deferred"
    out_of_scope = (summary["task_boundary_scope_filter"] or {}).get("out_of_scope_repair_target_files") or []
    assert "src/models/moon.hpp" in out_of_scope


@pytest.mark.asyncio
async def test_repeat_attempt_keeps_rebound_declaration_owner(tmp_path: Path) -> None:
    """After Factory rebinds to the models owner, do not bounce back to the use site."""

    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "generator.hpp").write_text(
        "#pragma once\nnamespace moonpost { void use(MoonError err = MoonError::Ok); }\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "models" / "moon.hpp").write_text(
        "#pragma once\nnamespace moonpost { struct Moon { int phase; }; }\n",
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}
    adapter = _recording_adapter(tmp_path, captured)
    error_text = "src/engine/generator.hpp:2:32: error: 'MoonError' has not been declared\n"

    results, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task={
            "task_id": "TASK-1-source-models",
            "target_files": ["src/models/moon.hpp", "src/models/moon.cpp"],
            "metadata": {"external_task_id": "TASK-1-source-models", "factory_run_id": "factory_widen_rebind"},
        },
        target_task_id="TASK-1-source-models",
        run_id="factory_widen_rebind",
        context={"run_id": "factory_widen_rebind", "target_files": ["src/models/moon.hpp"]},
        original_message="Declare MoonError on the owning models header.",
        llm_call_timeout=30.0,
        artifact_quality_errors=[error_text],
        changed_files=["src/engine/generator.hpp", "src/models/moon.hpp"],
        repair_attempt=2,
    )

    del results
    assert captured.get("invoked") is True
    assert summary.get("stage") != "task_boundary_repair_targets_deferred"
    assert "src/models/moon.hpp" in summary["repair_target_files"]
    assert "src/engine/generator.hpp" not in summary["repair_target_files"]
    message = str(captured.get("message") or "")
    assert "src/models/moon.hpp" in message


@pytest.mark.asyncio
async def test_factory_forced_rebind_targets_do_not_reopen_sibling_models(tmp_path: Path) -> None:
    """Owner-rebind batches must stay on Factory-forced declaration homes.

    Live L1-06 R4/R5: rebound to moon.hpp for MoonError but widening re-added
    every source-models file, so the model edited stamp.hpp instead.
    """

    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "generator.hpp").write_text(
        '#pragma once\n#include "models/moon.hpp"\nnamespace moonpost { void use(MoonError); }\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "models" / "moon.hpp").write_text(
        "#pragma once\nnamespace moonpost { struct MoonInfo { int phase; }; }\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "models" / "stamp.hpp").write_text(
        "#pragma once\nnamespace moonpost { struct Stamp {}; }\n",
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}
    adapter = _recording_adapter(tmp_path, captured)
    error_text = "src/engine/generator.hpp:2:32: error: 'MoonError' has not been declared\n"

    results, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task={
            "task_id": "TASK-1-source-models",
            "target_files": [
                "src/models/moon.hpp",
                "src/models/moon.cpp",
                "src/models/stamp.hpp",
                "src/models/stamp.cpp",
            ],
            "metadata": {"external_task_id": "TASK-1-source-models", "factory_run_id": "factory_forced"},
        },
        target_task_id="TASK-1-source-models",
        run_id="factory_forced",
        context={
            "run_id": "factory_forced",
            "target_files": ["src/models/moon.hpp"],
            "director_quality_repair": {"repair_target_files": ["src/models/moon.hpp"]},
        },
        original_message="Declare MoonError on the owning moon header.",
        llm_call_timeout=30.0,
        artifact_quality_errors=[error_text],
        changed_files=[
            "src/engine/generator.hpp",
            "src/models/moon.hpp",
            "src/models/stamp.hpp",
        ],
        repair_attempt=2,
    )

    del results
    assert captured.get("invoked") is True
    assert summary["repair_target_files"] == ["src/models/moon.hpp"]
    assert "src/models/stamp.hpp" not in summary["repair_target_files"]


@pytest.mark.asyncio
async def test_repeat_attempt_stays_on_use_site_for_redefinition_residual(tmp_path: Path) -> None:
    """A multiple-definition residual is owned by the use-site task.

    Live L1-06 retry: after source-core invented local MoonPhase/StampDenomination
    in generator.hpp, g++ reported redefinition against moon.hpp/stamp.hpp.
    Treating every non-anchor out-of-scope file as a declaration home rebound
    away from the file that must delete the duplicate enums.
    """

    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "generator.hpp").write_text(
        '#pragma once\n#include "models/postcard.hpp"\nnamespace moonpost { enum class MoonPhase { NewMoon = 0 }; }\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "models" / "moon.hpp").write_text(
        "#pragma once\n#include <cstdint>\nnamespace moonpost { enum class MoonPhase : std::uint8_t { NewMoon = 0 }; }\n",
        encoding="utf-8",
    )
    (tmp_path / "readme.md").write_text("docs\n", encoding="utf-8")
    captured: dict[str, Any] = {}
    adapter = _recording_adapter(tmp_path, captured)
    error_text = (
        "src/engine/generator.hpp:49:12: error: different underlying type in enum "
        "‘enum class moonpost::MoonPhase’\n"
        "src/models/moon.hpp:17:12: note: previous definition here\n"
        "src/engine/generator.hpp:49:12: error: multiple definition of "
        "‘enum class moonpost::MoonPhase’\n"
    )

    results, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task={
            "task_id": "TASK-1-source-core",
            "target_files": ["src/engine/generator.cpp", "src/engine/generator.hpp"],
            "metadata": {"external_task_id": "TASK-1-source-core", "factory_run_id": "factory_redef"},
        },
        target_task_id="TASK-1-source-core",
        run_id="factory_redef",
        context={"run_id": "factory_redef"},
        original_message="Remove the duplicate MoonPhase enum.",
        llm_call_timeout=30.0,
        artifact_quality_errors=[error_text],
        changed_files=[
            "src/engine/generator.hpp",
            "src/models/moon.hpp",
            "readme.md",
            ".catalog_meta.json",
        ],
        repair_attempt=2,
    )

    del results
    assert captured.get("invoked") is True
    assert summary.get("stage") != "task_boundary_repair_targets_deferred"
    assert "src/engine/generator.hpp" in summary["repair_target_files"]
    assert "src/models/moon.hpp" not in summary["repair_target_files"]
