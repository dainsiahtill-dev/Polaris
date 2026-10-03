"""Causal owner routing must not convert observation into write authority."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director import quality_gate
from polaris.cells.roles.adapters.internal.director.quality_gate._language_targets import (
    _resolve_javascript_relative_import_target,
)
from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
    _go_compiler_diagnostic_anchor_files,
    _materialization_plan_probe_requires_task_boundary_triage,
    _run_materialization_quality_repair_retry,
    _unresolved_import_exporter_paths,
)


class _RecordingAdapter:
    """Replace external dialogue only; target selection and guards remain real."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = str(workspace)
        self.contexts: list[dict[str, Any]] = []
        self._execution = SimpleNamespace(
            extract_kernel_tool_results=lambda result: [],
            execute_tools=self._execute_tools,
        )

    @staticmethod
    async def _execute_tools(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    @staticmethod
    def _update_task_progress(*args: Any, **kwargs: Any) -> None:
        return None

    async def _invoke_role_dialogue_with_timeout(
        self, message: str, *, context: dict[str, Any], timeout_seconds: float, stage_label: str
    ) -> dict[str, Any]:
        self.contexts.append(context)
        return {"content": "", "tool_results": []}


def _write(workspace: Path, path: str, text: str) -> None:
    target = workspace / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


async def _retry(
    adapter: _RecordingAdapter,
    targets: list[str],
    errors: list[str],
    *,
    context: dict[str, Any] | None = None,
    repair_attempt: int = 2,
) -> dict[str, Any]:
    _, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task={"task_id": "owner", "target_files": targets},
        target_task_id="owner",
        run_id="causal-routing-fixture",
        context=context or {},
        original_message="Repair only the original owner scope.",
        llm_call_timeout=10,
        artifact_quality_errors=errors,
        changed_files=targets,
        repair_attempt=repair_attempt,
    )
    return summary


def _requirements_observer_error(workspace: Path) -> str:
    return (
        "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_files.py' "
        "(returncode=1); tail:\n"
        f'  File "{workspace / "tests/test_files.py"}", line 9, in test_requirements_exists\n'
        f"AssertionError: requirements.txt must exist at {workspace / 'requirements.txt'}\n"
    )


@pytest.mark.parametrize("forced", [[], ["requirements.txt"]])
@pytest.mark.asyncio
async def test_foreign_cause_is_deferred_before_owned_observer_widening(tmp_path: Path, forced: list[str]) -> None:
    _write(tmp_path, "tests/test_files.py", "import unittest\n")
    _write(tmp_path, "main.py", "print('ok')\n")
    adapter = _RecordingAdapter(tmp_path)
    summary = await _retry(
        adapter,
        ["tests/test_files.py", "main.py"],
        [_requirements_observer_error(tmp_path)],
        context={"director_quality_repair": {"repair_target_files": forced}},
    )
    assert summary["stage"] == "task_boundary_repair_targets_deferred"
    assert summary["repair_target_files"] == []
    assert summary["task_boundary_scope_filter"]["out_of_scope_repair_target_files"] == ["requirements.txt"]
    assert adapter.contexts == []
    assert not (tmp_path / "requirements.txt").exists()


@pytest.mark.asyncio
async def test_mixed_owned_and_foreign_causes_keep_owned_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(quality_gate, "has_materialization_quality_runtime_repair_coverage", lambda errors: False)
    _write(tmp_path, "tests/test_files.py", "import unittest\n")
    _write(tmp_path, "src/main.py", "def broken(:\n")
    adapter = _RecordingAdapter(tmp_path)
    summary = await _retry(
        adapter,
        ["src/main.py"],
        [_requirements_observer_error(tmp_path), "src/main.py:1:12: SyntaxError: invalid syntax"],
    )
    assert "src/main.py" in summary["repair_target_files"]
    assert "requirements.txt" not in summary["repair_target_files"]
    assert adapter.contexts


@pytest.mark.asyncio
async def test_actual_owned_test_source_error_is_not_suppressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(quality_gate, "has_materialization_quality_runtime_repair_coverage", lambda errors: False)
    _write(tmp_path, "tests/test_files.py", "def broken(:\n")
    adapter = _RecordingAdapter(tmp_path)
    summary = await _retry(adapter, ["tests/test_files.py"], ["tests/test_files.py:1:12: SyntaxError: invalid syntax"])
    assert summary["repair_target_files"] == ["tests/test_files.py"]
    assert adapter.contexts


@pytest.mark.asyncio
async def test_qualified_forced_producer_rebind_survives_foreign_use_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(quality_gate, "has_materialization_quality_runtime_repair_coverage", lambda errors: False)
    _write(tmp_path, "src/models/moon.hpp", "#pragma once\nstruct Moon {};\n")
    _write(tmp_path, "foreign.cpp", "void use(MoonError);\n")
    adapter = _RecordingAdapter(tmp_path)
    summary = await _retry(
        adapter,
        ["src/models/moon.hpp"],
        ["foreign.cpp:1:10: error: 'MoonError' has not been declared"],
        context={"director_quality_repair": {"repair_target_files": ["src/models/moon.hpp"]}},
    )
    assert summary["repair_target_files"] == ["src/models/moon.hpp"]
    assert adapter.contexts


@pytest.mark.parametrize(
    "output_suffix,source_suffix", [(".js", ".ts"), (".js", ".tsx"), (".mjs", ".mts"), (".cjs", ".cts")]
)
def test_javascript_specifier_resolves_unique_authored_peer(
    tmp_path: Path, output_suffix: str, source_suffix: str
) -> None:
    _write(tmp_path, f"src/verify{source_suffix}", "export const verify = () => true;\n")
    (tmp_path / "tests").mkdir()
    target = _resolve_javascript_relative_import_target(tmp_path / "tests", f"../src/verify{output_suffix}", tmp_path)
    assert target == tmp_path / f"src/verify{source_suffix}"


@pytest.mark.parametrize("specifier", ["../src/verify.js", "../src/verify"])
def test_javascript_ambiguous_source_peers_fail_closed(tmp_path: Path, specifier: str) -> None:
    _write(tmp_path, "src/verify.ts", "export const verify = 1;\n")
    _write(tmp_path, "src/verify.tsx", "export const verify = 2;\n")
    (tmp_path / "tests").mkdir()
    assert _resolve_javascript_relative_import_target(tmp_path / "tests", specifier, tmp_path) is None


def test_javascript_source_peer_cannot_escape_workspace(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write(outside, "verify.ts", "export const verify = 1;\n")
    (workspace / "src").symlink_to(outside, target_is_directory=True)
    assert _resolve_javascript_relative_import_target(workspace, "./src/verify.js", workspace) is None


def test_javascript_unresolved_exporter_never_becomes_python_path() -> None:
    error = "unresolved import symbol 'runVerification' from '../src/verify.js' in tests/verify.test.ts"
    assert _unresolved_import_exporter_paths([error]) == []


def test_go_duplicate_secondary_declaration_is_owner_not_assertion() -> None:
    errors = [
        "simulator.go:33:17: method Scene.Spawn already declared at ./scene.go:56:17",
        "engine_test.go:20: expected scene.Spawn to succeed",
    ]
    assert _go_compiler_diagnostic_anchor_files(errors) == ["simulator.go", "scene.go"]


def test_owned_import_annotation_does_not_bypass_unplannable_interface_gate() -> None:
    summary = {
        "plan_probe_preaudit": {
            "status": "coverage_matched_but_unplannable",
            "covered_unplannable_source_tools": ["deterministic_typescript_missing_export_repair"],
        },
        "task_boundary_director_continuation_allowed": True,
        "task_boundary_continuation_reason": "current_task_unresolved_import",
    }
    assert _materialization_plan_probe_requires_task_boundary_triage(summary) is True


@pytest.mark.parametrize("authorized", [False, True])
@pytest.mark.asyncio
async def test_missing_export_retry_consumes_existing_interface_authorization(tmp_path: Path, authorized: bool) -> None:
    _write(tmp_path, "src/verify.ts", "export const verify = () => true;\n")
    _write(tmp_path, "tests/verify.test.ts", "import { runVerification } from '../src/verify.js';\n")
    adapter = _RecordingAdapter(tmp_path)
    context = {
        "director_interface_discrepancy_retry": {
            "authorized": authorized,
            "reason": "coverage_matched_but_unplannable",
            "recommended_owner": "director",
            "recommended_route": "director_retry_with_interface_discrepancy_context",
        }
    }
    summary = await _retry(
        adapter,
        ["src/verify.ts", "tests/verify.test.ts"],
        ["unresolved import symbol 'runVerification' from '../src/verify.js' in tests/verify.test.ts"],
        context=context,
    )
    if authorized:
        assert summary.get("llm_fallback_blocked") is not True
        assert adapter.contexts
    else:
        assert summary["stage"] == "runtime_plan_probe_unplannable"
        assert summary["llm_fallback_blocked"] is True
        assert adapter.contexts == []
    assert (tmp_path / "src/verify.ts").read_text(encoding="utf-8") == "export const verify = () => true;\n"


@pytest.mark.asyncio
async def test_ordinary_owned_import_adaptation_keeps_legacy_continuation(tmp_path: Path) -> None:
    _write(tmp_path, "src/main.py", "from src.radio import render_broadcast\n")
    _write(tmp_path, "src/radio.py", "def broadcast():\n    return 'ready'\n")
    adapter = _RecordingAdapter(tmp_path)
    summary = await _retry(
        adapter,
        ["src/main.py", "src/radio.py"],
        ["unresolved import symbol 'render_broadcast' from 'src.radio' in src/main.py"],
    )
    assert summary.get("llm_fallback_blocked") is not True
    assert adapter.contexts
    assert (tmp_path / "src/radio.py").read_text(encoding="utf-8") == "def broadcast():\n    return 'ready'\n"


def test_javascript_exporter_paths_resolve_workspace_source_peer(tmp_path: Path) -> None:
    _write(tmp_path, "src/verify.ts", "export const verify = 1;\n")
    _write(tmp_path, "tests/verify.test.ts", "import { verify } from '../src/verify.js';\n")
    error = "unresolved import symbol 'runVerification' from '../src/verify.js' in tests/verify.test.ts"
    assert _unresolved_import_exporter_paths([error], workspace_full=str(tmp_path)) == ["src/verify.ts"]


@pytest.mark.parametrize("repair_attempt", [1, 2])
@pytest.mark.asyncio
async def test_authorized_owned_importer_repair_never_admits_missing_reference(
    tmp_path: Path, repair_attempt: int
) -> None:
    importer = "src/entry/browser-main.ts"
    reference = "src/engine/renderer.ts"
    _write(tmp_path, importer, "import { render } from '../engine/renderer.js';\n")
    adapter = _RecordingAdapter(tmp_path)
    summary = await _retry(
        adapter,
        [importer, reference],
        [f"Artifact quality scan failed: unresolved relative import '../engine/renderer.js' in {importer}"],
        context={
            "director_execution_envelope": {"authorization": {"allowed_write_paths": [importer]}},
            "director_interface_discrepancy_retry": {
                "authorized": True,
                "reason": "coverage_matched_but_unplannable",
                "recommended_owner": "director",
                "recommended_route": "director_retry_with_interface_discrepancy_context",
            },
        },
        repair_attempt=repair_attempt,
    )
    assert summary["repair_target_files"] == [importer]
    assert adapter.contexts[0]["metadata"]["tool_path_contract"]["allowed_target_files"] == [importer]
    assert not (tmp_path / reference).exists()


@pytest.mark.asyncio
async def test_repeat_mixed_cpp_diagnostics_keep_actual_owned_compile_site(tmp_path: Path) -> None:
    _write(tmp_path, "src/engine.cpp", "void broken() {}\n")
    _write(tmp_path, "foreign.cpp", "void broken() {}\n")
    adapter = _RecordingAdapter(tmp_path)
    summary = await _retry(
        adapter,
        ["src/engine.cpp"],
        ["src/engine.cpp:1:1: error: expected ';'\nforeign.cpp:1:1: error: expected ';'"],
    )
    assert summary["repair_target_files"] == ["src/engine.cpp"]
    assert adapter.contexts


@pytest.mark.asyncio
async def test_pytest_observer_location_does_not_become_owned_compiler_cause(tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_files.py", "import unittest\n")
    adapter = _RecordingAdapter(tmp_path)
    summary = await _retry(
        adapter,
        ["tests/test_files.py"],
        [_requirements_observer_error(tmp_path) + "tests/test_files.py:9: AssertionError: requirements.txt missing"],
    )
    assert summary["stage"] == "task_boundary_repair_targets_deferred"
    assert summary["task_boundary_scope_filter"]["out_of_scope_repair_target_files"] == ["requirements.txt"]
    assert adapter.contexts == []


@pytest.mark.asyncio
async def test_unrelated_owned_factory_hint_cannot_replace_foreign_primary_cause(tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_files.py", "import unittest\n")
    _write(tmp_path, "main.py", "print('ok')\n")
    adapter = _RecordingAdapter(tmp_path)
    summary = await _retry(
        adapter,
        ["tests/test_files.py", "main.py"],
        [_requirements_observer_error(tmp_path)],
        context={"director_quality_repair": {"repair_target_files": ["main.py"]}},
    )
    assert summary["stage"] == "task_boundary_repair_targets_deferred"
    assert summary["repair_target_files"] == []
    assert summary["task_boundary_scope_filter"]["out_of_scope_repair_target_files"] == ["requirements.txt"]
    assert adapter.contexts == []


def test_bare_javascript_specifier_does_not_bind_same_named_python_file(tmp_path: Path) -> None:
    _write(tmp_path, "lodash.py", "def map():\n    return []\n")
    _write(tmp_path, "src/main.ts", "import { map } from 'lodash';\n")
    error = "unresolved import symbol 'map' from 'lodash' in src/main.ts"
    assert _unresolved_import_exporter_paths([error], workspace_full=str(tmp_path)) == []


def test_explicit_existing_javascript_file_precedes_typed_fallback_peers(tmp_path: Path) -> None:
    _write(tmp_path, "src/verify.js", "export const verify = 1;\n")
    _write(tmp_path, "src/verify.ts", "export const verify = 2;\n")
    (tmp_path / "tests").mkdir()
    assert _resolve_javascript_relative_import_target(tmp_path / "tests", "../src/verify.js", tmp_path) == (
        tmp_path / "src/verify.js"
    )


@pytest.mark.asyncio
async def test_real_rust_behavior_producer_evidence_overrides_stale_factory_hint(tmp_path: Path) -> None:
    _write(tmp_path, "src/lib.rs", "pub mod patience;\n")
    _write(tmp_path, "src/patience.rs", "pub struct PatienceTracker;\n")
    _write(
        tmp_path,
        "tests/edges.rs",
        "#[test]\nfn patience_tick_is_a_noop() { let tracker = PatienceTracker; }\n",
    )
    adapter = _RecordingAdapter(tmp_path)
    summary = await _retry(
        adapter,
        ["src/lib.rs", "src/patience.rs"],
        [
            "---- patience_tick_is_a_noop stdout ----\n"
            "thread 'patience_tick_is_a_noop' panicked at tests/edges.rs:2:5:\n"
            "assertion `left == right` failed\n  left: 3\n right: 5"
        ],
        context={"director_quality_repair": {"repair_target_files": ["src/lib.rs"]}},
    )
    assert summary["repair_target_files"] == ["src/patience.rs"]
