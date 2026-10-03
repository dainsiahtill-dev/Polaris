"""Missing compiled test references must retain the authorized manifest owner."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _filter_missing_workspace_file_errors_to_task_write_scope,
    _missing_materialization_quality_repair_target_files,
    _missing_workspace_file_quality_repair_target_files,
    _semantic_quality_repair_target_files,
)
from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
    _run_materialization_quality_repair_retry,
)
from polaris.cells.roles.adapters.tests.test_quality_repair_authorized_scope import _RepairBoundaryAdapter
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import get_default_adapter, set_default_adapter

MANIFESTS = ["package.json", "tsconfig.json"]
MISSING_JS = "tests/verify.test.js"
TEST_SOURCE = "tests/verify.test.ts"
ERROR = (
    "Artifact quality scan failed: workspace validation command failed (npm test): "
    "> example@0.1.0 test\n"
    "> tsc -p tsconfig.json && node --test --test-reporter spec tests/verify.test.js\n\n\n"
    "Could not find 'tests/verify.test.js'"
)


def _workspace(root: Path) -> None:
    (root / "package.json").write_text(
        json.dumps(
            {"scripts": {"test": "tsc -p tsconfig.json && node --test --test-reporter spec tests/verify.test.js"}}
        ),
        encoding="utf-8",
    )
    (root / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"rootDir": "src", "outDir": "dist"}, "include": ["src/**/*.ts"]}),
        encoding="utf-8",
    )
    source = root / TEST_SOURCE
    source.parent.mkdir(parents=True)
    source.write_text("import { test } from 'node:test';\n", encoding="utf-8")


def _task(allowed: list[str]) -> dict[str, Any]:
    return {
        "task_id": "TASK-1",
        # PM/project inventory is intentionally broader than CE write authority.
        "target_files": [*MANIFESTS, TEST_SOURCE, MISSING_JS],
        "director_execution_envelope": {"authorization": {"allowed_write_paths": allowed}},
    }


def test_scope_filter_retains_missing_compiled_test_diagnostic_for_manifest_owner(tmp_path: Path) -> None:
    _workspace(tmp_path)
    context: dict[str, Any] = {}
    task = _task(MANIFESTS)
    before = deepcopy(task)

    errors = _filter_missing_workspace_file_errors_to_task_write_scope(
        [ERROR], task=task, workspace_full=str(tmp_path), context=context
    )

    assert errors == [ERROR]
    assert context == {}
    assert task == before


def test_causal_candidates_are_existing_manifests_not_an_unowned_javascript_stub(tmp_path: Path) -> None:
    _workspace(tmp_path)

    assert (
        _semantic_quality_repair_target_files(
            artifact_quality_errors=[ERROR], changed_files=MANIFESTS, workspace_full=str(tmp_path)
        )
        == MANIFESTS
    )
    assert _missing_materialization_quality_repair_target_files(_task(MANIFESTS), str(tmp_path), [ERROR]) == []
    # Keep the physical missing-file fact intact; only its repair role changes.
    assert _missing_workspace_file_quality_repair_target_files(
        artifact_quality_errors=[ERROR], workspace_full=str(tmp_path)
    ) == [MISSING_JS]
    assert not (tmp_path / MISSING_JS).exists()


def test_explicitly_owned_javascript_target_keeps_original_missing_target_contract(tmp_path: Path) -> None:
    _workspace(tmp_path)

    assert _missing_materialization_quality_repair_target_files(
        _task([*MANIFESTS, MISSING_JS]), str(tmp_path), [ERROR]
    ) == [MISSING_JS]


@pytest.mark.parametrize("allowed", [MANIFESTS, ["package.json"], ["tsconfig.json"]])
@pytest.mark.asyncio
async def test_real_loop_offers_only_admitted_manifest_edits(tmp_path: Path, allowed: list[str]) -> None:
    _workspace(tmp_path)
    adapter = _RepairBoundaryAdapter(tmp_path)
    task = _task(allowed)
    context: dict[str, Any] = {}
    before_task, before_context = deepcopy(task), deepcopy(context)
    before_files = {path: (tmp_path / path).read_bytes() for path in [*MANIFESTS, TEST_SOURCE]}

    _, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task=task,
        target_task_id="TASK-1",
        run_id="factory-manifest-causal-unit",
        context=context,
        original_message="Repair the failing package test command without creating unowned artifacts.",
        llm_call_timeout=30,
        artifact_quality_errors=[ERROR],
        changed_files=MANIFESTS,
    )

    assert set(summary["repair_target_files"]) == set(allowed)
    assert summary.get("llm_fallback_blocked") is not True
    assert len(adapter.calls) == 1
    repair_context = adapter.calls[0]["context"]
    assert set(repair_context["metadata"]["tool_path_contract"]["allowed_target_files"]) == set(allowed)
    assert "write_only_single_target" not in repair_context["director_quality_repair"]
    assert not (tmp_path / MISSING_JS).exists()
    assert {path: (tmp_path / path).read_bytes() for path in before_files} == before_files
    assert task == before_task
    assert context == before_context


def test_foreign_manifests_do_not_keep_missing_artifact_error(tmp_path: Path) -> None:
    _workspace(tmp_path)
    task = _task([TEST_SOURCE])
    context: dict[str, Any] = {}

    assert (
        _filter_missing_workspace_file_errors_to_task_write_scope(
            [ERROR], task=task, workspace_full=str(tmp_path), context=context
        )
        == []
    )


@pytest.mark.parametrize("allowed", [[], [TEST_SOURCE]])
@pytest.mark.asyncio
async def test_foreign_authority_never_executes_manifest_or_generated_artifact(
    tmp_path: Path, allowed: list[str]
) -> None:
    _workspace(tmp_path)
    adapter = _RepairBoundaryAdapter(tmp_path)

    results, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task=_task(allowed),
        target_task_id="TASK-1",
        run_id="factory-manifest-causal-unit",
        context={"director_execution_envelope": {"authorization": {"allowed_write_paths": [*MANIFESTS, TEST_SOURCE]}}},
        original_message="Repair the failing test command.",
        llm_call_timeout=30,
        artifact_quality_errors=[ERROR],
        changed_files=MANIFESTS,
    )

    assert summary["repair_target_files"] == []
    assert results == []
    assert adapter.calls == []
    assert not (tmp_path / MISSING_JS).exists()


@pytest.mark.parametrize(
    "config",
    [
        {"compilerOptions": {"rootDir": "."}, "include": ["**/*.ts"]},
        {"compilerOptions": {"rootDir": "src"}, "include": ["src/**/*.ts", "tests/**/*.ts"]},
        {"extends": "./base.json", "compilerOptions": {"rootDir": "src"}, "include": ["src/**/*.ts"]},
    ],
)
def test_unknown_or_test_including_compiler_contract_does_not_invent_manifest_cause(
    tmp_path: Path, config: dict[str, Any]
) -> None:
    _workspace(tmp_path)
    (tmp_path / "tsconfig.json").write_text(json.dumps(config), encoding="utf-8")

    assert (
        _semantic_quality_repair_target_files(
            artifact_quality_errors=[ERROR], changed_files=MANIFESTS, workspace_full=str(tmp_path)
        )
        == []
    )
    assert (
        _filter_missing_workspace_file_errors_to_task_write_scope(
            [ERROR], task=_task(MANIFESTS), workspace_full=str(tmp_path)
        )
        == []
    )


def test_canonical_node_test_alias_keeps_manifest_candidates(tmp_path: Path) -> None:
    _workspace(tmp_path)
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "tsc --project ./tsconfig.json && node --test ./tests/verify.test.js"}}),
        encoding="utf-8",
    )

    assert (
        _semantic_quality_repair_target_files(
            artifact_quality_errors=[ERROR], changed_files=MANIFESTS, workspace_full=str(tmp_path)
        )
        == MANIFESTS
    )


def test_existing_javascript_artifact_does_not_reinterpret_stale_missing_error(tmp_path: Path) -> None:
    _workspace(tmp_path)
    (tmp_path / MISSING_JS).write_text("export {};\n", encoding="utf-8")

    assert (
        _semantic_quality_repair_target_files(
            artifact_quality_errors=[ERROR], changed_files=MANIFESTS, workspace_full=str(tmp_path)
        )
        == []
    )


def test_manifest_discovery_consumes_registered_kfs_reads_without_mutating_files(tmp_path: Path) -> None:
    _workspace(tmp_path)
    reads: list[tuple[str, str]] = []
    before = {path: (tmp_path / path).read_bytes() for path in [*MANIFESTS, TEST_SOURCE]}

    class RecordingAdapter(LocalFileSystemAdapter):
        def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
            reads.append((path, encoding))
            return super().read_text(path, encoding=encoding)

    original = get_default_adapter()
    set_default_adapter(RecordingAdapter())
    try:
        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=[ERROR], changed_files=MANIFESTS, workspace_full=str(tmp_path)
        )
    finally:
        set_default_adapter(original)

    assert targets == MANIFESTS
    assert reads == [(str(tmp_path / path), "utf-8") for path in MANIFESTS]
    assert {path: (tmp_path / path).read_bytes() for path in before} == before
    assert not (tmp_path / MISSING_JS).exists()


def test_manifest_discovery_does_not_bypass_kfs_read_denial(tmp_path: Path) -> None:
    _workspace(tmp_path)

    class DenyingAdapter(LocalFileSystemAdapter):
        def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
            raise PermissionError("registered KFS port denies this read")

    before = {path: (tmp_path / path).read_bytes() for path in [*MANIFESTS, TEST_SOURCE]}
    original = get_default_adapter()
    set_default_adapter(DenyingAdapter())
    try:
        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=[ERROR], changed_files=MANIFESTS, workspace_full=str(tmp_path)
        )
    finally:
        set_default_adapter(original)

    assert targets == []
    assert {path: (tmp_path / path).read_bytes() for path in before} == before


@pytest.mark.parametrize("escaped_path", [*MANIFESTS, TEST_SOURCE])
def test_manifest_discovery_rejects_files_resolving_outside_workspace(tmp_path: Path, escaped_path: str) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _workspace(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = root / escaped_path
    outside_file = outside / escaped.name
    outside_file.write_bytes(escaped.read_bytes())
    escaped.unlink()
    escaped.symlink_to(outside_file)
    before = outside_file.read_bytes()

    assert (
        _semantic_quality_repair_target_files(
            artifact_quality_errors=[ERROR], changed_files=MANIFESTS, workspace_full=str(root)
        )
        == []
    )
    assert outside_file.read_bytes() == before
    assert not (root / MISSING_JS).exists()


@pytest.mark.parametrize("change", ["no_manifest", "no_peer", "unrelated_script", "unrelated_missing_file"])
def test_unproven_manifest_cause_preserves_original_deferral(tmp_path: Path, change: str) -> None:
    _workspace(tmp_path)
    error = ERROR
    if change == "no_manifest":
        (tmp_path / "package.json").unlink()
    elif change == "no_peer":
        (tmp_path / TEST_SOURCE).unlink()
    elif change == "unrelated_script":
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "node --test other.test.js"}}), encoding="utf-8"
        )
    else:
        error = "Artifact quality scan failed: Could not find 'src/missing.js'"

    assert (
        _filter_missing_workspace_file_errors_to_task_write_scope(
            [error], task=_task(MANIFESTS), workspace_full=str(tmp_path)
        )
        == []
    )
    assert (
        _semantic_quality_repair_target_files(
            artifact_quality_errors=[error], changed_files=MANIFESTS, workspace_full=str(tmp_path)
        )
        == []
    )
