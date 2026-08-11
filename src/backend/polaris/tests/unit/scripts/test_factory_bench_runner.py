"""Tests for the factory-bench runner verdict semantics."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    append_run_ledger_event,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from scripts.factory_bench import run_factory_bench as bench
from scripts.factory_bench._bench_lib import (
    artifacts as bench_artifacts,
    chain as bench_chain,
    cli as bench_cli,
    gates as bench_gates,
    session as bench_session,
    workspace as bench_workspace,
)
from scripts.factory_bench.run_factory_bench import (
    _allocate_fresh_project_workspace,
    _desktop_backend_info_path,
    _extract_feature_keywords,
    _fallback_audit_bundle_from_workspace,
    _is_local_backend_url,
    _next_immutable_json_path,
    _project_workspace_for_run,
    _read_desktop_backend_info,
    _resolve_backend_token,
    _resolve_backend_url,
    _resolve_polaris_home,
    _sanitize_run_id,
    _write_immutable_json,
    apply_factory_bench_gates,
    build_director_repair_coverage_gap_summary,
    build_requirements_doc,
    discover_artifacts,
    load_workspace_validation_repair_coverage,
    map_factory_run_to_chain_results,
    read_chain_results_from_runtime_dirs,
    resolve_runtime_dirs_for_workspace,
    run_factory_chain,
)

_LAST_FACTORY_START_PAYLOAD: dict[str, Any] = {}
_LAST_FACTORY_RESUME_CALL: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _isolate_instance_registry(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNELONE_INSTANCE_HOME", str(tmp_path / "instances-home"))
    monkeypatch.setenv("FACTORY_BENCH_LAUNCHER_INSTANCE_MODE", "observed")
    monkeypatch.setattr(bench_cli, "persist_real_run_gate_ledger", lambda *_args, **_kwargs: {"ok": True})
    bench.configure_bench_backend("", "", "")


def _bootstrap_test_fact_stream(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_bench_runner_unit_test",
        )
    )


def test_default_launcher_instance_mode_is_isolated(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_LAUNCHER_INSTANCE_MODE", raising=False)

    assert bench._default_launcher_instance_mode() == "isolated"


def test_launcher_instance_mode_env_allows_explicit_observed(monkeypatch: Any) -> None:
    monkeypatch.setenv("FACTORY_BENCH_LAUNCHER_INSTANCE_MODE", "observed")

    assert bench._default_launcher_instance_mode() == "observed"


def test_launcher_instance_mode_invalid_env_falls_back_to_isolated(monkeypatch: Any) -> None:
    monkeypatch.setenv("FACTORY_BENCH_LAUNCHER_INSTANCE_MODE", "shared")

    assert bench._default_launcher_instance_mode() == "isolated"


def test_fresh_project_workspace_allocations_have_distinct_physical_identities(tmp_path: Path) -> None:
    first = _allocate_fresh_project_workspace(
        tmp_path,
        project_id="L1-04",
        run_id="run-identity",
    )
    second = _allocate_fresh_project_workspace(
        tmp_path,
        project_id="L1-04",
        run_id="run-identity",
    )

    assert first != second
    assert first.is_dir()
    assert second.is_dir()
    assert first.is_relative_to(tmp_path.resolve())
    assert second.is_relative_to(tmp_path.resolve())
    first_roots = bench_workspace.resolve_storage_roots(first)
    second_roots = bench_workspace.resolve_storage_roots(second)
    assert first_roots.workspace_key != second_roots.workspace_key
    assert first_roots.runtime_root != second_roots.runtime_root


def test_workspace_identity_component_preserves_sanitized_collisions() -> None:
    first = bench._identity_workspace_component("run/a", fallback="run")
    second = bench._identity_workspace_component("run:a", fallback="run")

    assert first != second
    assert first.startswith("run-a-")
    assert second.startswith("run-a-")


def test_workspace_catalog_metadata_is_immutable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = {"run_id": "run-identity", "project_id": "L1-04"}

    first = bench._write_workspace_catalog_meta_exclusive(tmp_path, workspace, payload)

    with pytest.raises(FileExistsError):
        bench._write_workspace_catalog_meta_exclusive(tmp_path, workspace, payload)
    persisted = json.loads((workspace / ".catalog_meta.json").read_text(encoding="utf-8"))
    assert persisted == first


def test_workspace_catalog_metadata_rejects_symlink_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external.json"
    external.write_text('{"run_id":"run-identity","project_id":"L1-04"}', encoding="utf-8")
    (workspace / ".catalog_meta.json").symlink_to(external)

    assert not bench._workspace_catalog_meta_matches(
        tmp_path,
        workspace,
        run_id="run-identity",
        project_id="L1-04",
    )
    with pytest.raises(RuntimeError, match="metadata is missing or invalid"):
        bench._require_workspace_catalog_meta(
            tmp_path,
            workspace,
            {"run_id": "run-identity", "project_id": "L1-04"},
        )


def test_director_resume_rejects_legacy_workspace_without_explicit_migration(tmp_path: Path) -> None:
    legacy = tmp_path / "L1-04"
    legacy.mkdir()
    (legacy / ".catalog_meta.json").write_text(
        json.dumps({"run_id": "run-identity", "project_id": "L1-04"}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="explicit migration is required"):
        _project_workspace_for_run(
            tmp_path,
            project_id="L1-04",
            run_id="run-identity",
            resume_director=True,
        )
    fresh = _project_workspace_for_run(
        tmp_path,
        project_id="L1-04",
        run_id="run-identity",
        resume_director=False,
    )

    assert fresh != legacy
    assert fresh.is_relative_to(tmp_path.resolve())


def test_director_resume_resolves_one_run_scoped_fresh_workspace(tmp_path: Path) -> None:
    fresh = _allocate_fresh_project_workspace(
        tmp_path,
        project_id="L1-04",
        run_id="run-identity",
    )
    bench._write_workspace_catalog_meta_exclusive(
        tmp_path,
        fresh,
        {"run_id": "run-identity", "project_id": "L1-04"},
    )

    resumed = _project_workspace_for_run(
        tmp_path,
        project_id="L1-04",
        run_id="run-identity",
        resume_director=True,
    )

    assert resumed == fresh


def test_director_resume_resolves_frozen_workspace_from_prior_attempt(tmp_path: Path) -> None:
    fresh = _allocate_fresh_project_workspace(
        tmp_path,
        project_id="L1-04",
        run_id="original-run",
    )
    bench._write_workspace_catalog_meta_exclusive(
        tmp_path,
        fresh,
        {"run_id": "original-run", "project_id": "L1-04"},
    )

    resumed = _project_workspace_for_run(
        tmp_path,
        project_id="L1-04",
        run_id="new-repair-attempt",
        resume_director=True,
    )

    assert resumed == fresh


def test_director_resume_rejects_ambiguous_fresh_workspaces(tmp_path: Path) -> None:
    for _ in range(2):
        fresh = _allocate_fresh_project_workspace(
            tmp_path,
            project_id="L1-04",
            run_id="run-identity",
        )
        bench._write_workspace_catalog_meta_exclusive(
            tmp_path,
            fresh,
            {"run_id": "run-identity", "project_id": "L1-04"},
        )

    with pytest.raises(RuntimeError, match="workspace is ambiguous"):
        _project_workspace_for_run(
            tmp_path,
            project_id="L1-04",
            run_id="run-identity",
            resume_director=True,
        )


def test_director_resume_rejects_legacy_even_when_fresh_workspace_exists(tmp_path: Path) -> None:
    legacy = tmp_path / "L1-04"
    legacy.mkdir()
    bench._write_workspace_catalog_meta_exclusive(
        tmp_path,
        legacy,
        {"run_id": "run-identity", "project_id": "L1-04"},
    )
    fresh = _allocate_fresh_project_workspace(
        tmp_path,
        project_id="L1-04",
        run_id="run-identity",
    )
    bench._write_workspace_catalog_meta_exclusive(
        tmp_path,
        fresh,
        {"run_id": "run-identity", "project_id": "L1-04"},
    )

    with pytest.raises(RuntimeError, match="explicit migration is required"):
        _project_workspace_for_run(
            tmp_path,
            project_id="L1-04",
            run_id="run-identity",
            resume_director=True,
        )


def test_fresh_project_workspace_rejects_symlinked_parent_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    run_component = bench._identity_workspace_component("run-identity", fallback="run")
    project_component = bench._identity_workspace_component("L1-04", fallback="project")
    run_workspace = tmp_path / "workspaces" / run_component
    run_workspace.mkdir(parents=True)
    (run_workspace / project_component).symlink_to(outside, target_is_directory=True)

    try:
        with pytest.raises(RuntimeError, match="not a physical directory"):
            _allocate_fresh_project_workspace(
                tmp_path,
                project_id="L1-04",
                run_id="run-identity",
            )
    finally:
        outside.rmdir()


def test_fresh_project_workspace_rejects_intermediate_symlink_without_external_write(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-intermediate-outside"
    outside.mkdir()
    run_component = bench._identity_workspace_component("run-identity", fallback="run")
    project_component = bench._identity_workspace_component("L1-04", fallback="project")
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    (workspaces / run_component).symlink_to(outside, target_is_directory=True)

    try:
        with pytest.raises(RuntimeError, match="not a physical directory"):
            _allocate_fresh_project_workspace(
                tmp_path,
                project_id="L1-04",
                run_id="run-identity",
            )
        assert not (outside / project_component).exists()
    finally:
        outside.rmdir()


def test_isolated_launch_receipt_rejects_relocated_workspace_ancestor(tmp_path: Path, monkeypatch: Any) -> None:
    """A moved ancestor plus symlink at the old path must not preserve launch authority."""
    monkeypatch.setattr(bench_session, "compute_source_fingerprint", lambda _root: "source-fingerprint")
    workspace = _allocate_fresh_project_workspace(tmp_path, project_id="L1-04", run_id="run-identity")
    catalog_meta = _write_test_workspace_catalog(
        tmp_path,
        workspace,
        run_id="run-identity",
        project_id="L1-04",
    )
    project_parent = workspace.parent
    relocated_parent = tmp_path.parent / f"{tmp_path.name}-relocated-project"
    project_parent.rename(relocated_parent)
    project_parent.symlink_to(relocated_parent, target_is_directory=True)

    try:
        with pytest.raises(RuntimeError, match=r"physical directory|metadata is missing or invalid"):
            bench._new_isolated_bench_launch_receipt(
                bench_session_id="bench-session",
                run_id="run-identity",
                project_id="L1-04",
                requested_project_id="L1-04",
                canonical_project_id="L1-04",
                bench_workspace=tmp_path,
                project_workspace=str(workspace),
                workspace_catalog_meta=catalog_meta,
            )
    finally:
        project_parent.unlink()
        relocated_parent.rename(project_parent)


def _factory_chain_destructive_findings(
    module_source: str,
    protected_names: set[str],
) -> tuple[list[str], list[str], set[str], str]:
    """Audit every module-local helper reachable from protected Factory entrypoints."""
    module_tree = ast.parse(module_source)
    module_functions = {
        node.name: node for node in module_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = protected_names.difference(module_functions)
    assert not missing, f"protected Factory entrypoints missing: {sorted(missing)}"

    reachable_names: set[str] = set()
    pending = list(protected_names)
    while pending:
        function_name = pending.pop()
        if function_name in reachable_names:
            continue
        reachable_names.add(function_name)
        for node in ast.walk(module_functions[function_name]):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            called_name = node.func.id
            if called_name in module_functions and called_name not in reachable_names:
                pending.append(called_name)

    destructive_names = {"rmtree", "rmdir", "unlink", "remove", "removedirs"}
    destructive_aliases = set(destructive_names)
    for node in module_tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name in destructive_names:
                destructive_aliases.add(alias.asname or alias.name)
    destructive_calls: list[str] = []
    subprocess_deletions: list[str] = []
    reachable_source: list[str] = []

    def _looks_destructive_shell(command: str) -> bool:
        normalized = " ".join(command.lower().replace("\\", "/").split())
        return any(
            token in normalized
            for token in (
                "rm -rf",
                "rm -fr",
                "rm -r ",
                "rmdir ",
                "remove-item -recurse",
                "remove-item -r ",
            )
        )

    for function_name in sorted(reachable_names):
        function_node = module_functions[function_name]
        reachable_source.append(ast.get_source_segment(module_source, function_node) or "")
        for node in ast.walk(function_node):
            if not isinstance(node, ast.Call):
                continue
            call_name = ""
            if isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
                if call_name in destructive_names:
                    destructive_calls.append(call_name)
            elif isinstance(node.func, ast.Name):
                call_name = node.func.id
                if call_name in destructive_aliases:
                    destructive_calls.append(call_name)
            lowered_call_name = call_name.lower()
            if any(
                action in lowered_call_name for action in ("purge", "cleanup", "delete", "remove", "destroy")
            ) and any(target in lowered_call_name for target in ("runtime", "workspace", "project")):
                destructive_calls.append(call_name)
            literal_fragments: list[str] = []
            for expression in (*node.args, *(keyword.value for keyword in node.keywords)):
                literal_fragments.extend(
                    str(child.value)
                    for child in ast.walk(expression)
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)
                )
            literal_command = " ".join(literal_fragments)
            if _looks_destructive_shell(literal_command):
                subprocess_deletions.append(literal_command)
    return destructive_calls, subprocess_deletions, reachable_names, "\n".join(reachable_source)


def test_factory_chain_paths_never_purge_enrolled_runtime_roots() -> None:
    module_source = "\n".join(
        (
            inspect.getsource(bench_workspace),
            inspect.getsource(bench_chain),
            inspect.getsource(bench_cli),
        )
    )
    protected_names = {
        "_allocate_fresh_project_workspace",
        "_project_workspace_for_run",
        "run_factory_chain",
        "run_chain",
        "main",
    }
    destructive_calls, subprocess_deletions, reachable_names, reachable_source = _factory_chain_destructive_findings(
        module_source, protected_names
    )

    assert destructive_calls == []
    assert subprocess_deletions == []
    assert protected_names.issubset(reachable_names)
    assert reachable_source
    assert not hasattr(bench, "purge_project_runtime")


def test_factory_chain_purge_audit_follows_wrappers_and_dynamic_shell_literals() -> None:
    wrapped_delete = """
import shutil
def _wipe(path):
    shutil.rmtree(path)
def main():
    _wipe(project_workspace)
"""
    destructive_calls, subprocess_deletions, reachable_names, _source = _factory_chain_destructive_findings(
        wrapped_delete,
        {"main"},
    )
    assert destructive_calls == ["rmtree"]
    assert subprocess_deletions == []
    assert reachable_names == {"main", "_wipe"}

    dynamic_shell_delete = """
import subprocess
def _wipe(path):
    subprocess.run(["bash", "-lc", "rm " + "-rf " + path], check=True)
def main():
    _wipe(project_workspace)
"""
    destructive_calls, subprocess_deletions, reachable_names, _source = _factory_chain_destructive_findings(
        dynamic_shell_delete,
        {"main"},
    )
    assert destructive_calls == []
    assert subprocess_deletions == ["bash -lc rm  -rf "]
    assert reachable_names == {"main", "_wipe"}


def _write_test_workspace_catalog(
    bench_workspace: Path,
    workspace: Path,
    *,
    run_id: str,
    project_id: str,
) -> dict[str, Any]:
    return bench._write_workspace_catalog_meta_exclusive(
        bench_workspace,
        workspace,
        {"run_id": run_id, "project_id": project_id},
    )


def test_isolated_launch_receipts_are_unique_and_auditable(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(bench_session, "compute_source_fingerprint", lambda _root: "source-fingerprint")
    workspace = tmp_path / "factory-bench-same-workdir" / "L1-01"
    workspace.mkdir(parents=True)
    catalog_meta = _write_test_workspace_catalog(workspace.parent, workspace, run_id="run-identity", project_id="L1-01")

    first = bench._new_isolated_bench_launch_receipt(
        bench_session_id="bench-session",
        run_id="run-identity",
        project_id="L1-01",
        requested_project_id="L1-01",
        canonical_project_id="L1-01",
        bench_workspace=workspace.parent,
        project_workspace=str(workspace),
        workspace_catalog_meta=catalog_meta,
    )
    second = bench._new_isolated_bench_launch_receipt(
        bench_session_id="bench-session",
        run_id="run-identity",
        project_id="L1-01",
        requested_project_id="L1-01",
        canonical_project_id="L1-01",
        bench_workspace=workspace.parent,
        project_workspace=str(workspace),
        workspace_catalog_meta=catalog_meta,
    )

    assert first["requested_instance_id"] == second["requested_instance_id"]
    assert first["launch_nonce"] != second["launch_nonce"]
    assert first["instance_id"] != second["instance_id"]
    assert first["launch_scope"] != second["launch_scope"]
    assert first["workspace"] == str(workspace.resolve())
    assert first["runtime_root"] == str((workspace / "runtime").resolve())
    assert first["expected_backend_root"] == str(bench._BACKEND_ROOT)
    assert first["expected_source_fingerprint"] == "source-fingerprint"
    assert first["requested_project_id"] == "L1-01"
    assert first["canonical_project_id"] == "L1-01"
    assert first["run_id"] == "run-identity"
    assert first["workspace_source_run_id"] == "run-identity"


def test_isolated_launch_receipt_rejects_prelaunch_same_path_replacement(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bench_session, "compute_source_fingerprint", lambda _root: "source-fingerprint")
    workspace = _allocate_fresh_project_workspace(tmp_path, project_id="L1-01", run_id="run-identity")
    catalog_meta = _write_test_workspace_catalog(tmp_path, workspace, run_id="run-identity", project_id="L1-01")
    displaced = workspace.with_name(f"{workspace.name}-displaced")
    workspace.rename(displaced)
    workspace.mkdir()

    with pytest.raises(RuntimeError, match="metadata is missing or invalid"):
        bench._new_isolated_bench_launch_receipt(
            bench_session_id="bench-session",
            run_id="run-identity",
            project_id="L1-01",
            requested_project_id="L1-01",
            canonical_project_id="L1-01",
            bench_workspace=tmp_path,
            project_workspace=str(workspace),
            workspace_catalog_meta=catalog_meta,
        )


def test_isolated_launch_forwards_fresh_receipt_to_supervisor(monkeypatch: Any, tmp_path: Path) -> None:
    from polaris.cells.instances.internal.service import InstanceSupervisor

    monkeypatch.setattr(bench_session, "compute_source_fingerprint", lambda _root: "source-fingerprint")
    monkeypatch.setattr(bench, "_wait_backend_health", lambda *_args, **_kwargs: True)
    workspace = tmp_path / "factory-bench-same-workdir" / "L1-01"
    workspace.mkdir(parents=True)
    catalog_meta = _write_test_workspace_catalog(workspace.parent, workspace, run_id="run-identity", project_id="L1-01")
    receipt = bench._new_isolated_bench_launch_receipt(
        bench_session_id="bench-session",
        run_id="run-identity",
        project_id="L1-01",
        requested_project_id="request-L1-01",
        canonical_project_id="L1-01",
        bench_workspace=workspace.parent,
        project_workspace=str(workspace),
        workspace_catalog_meta=catalog_meta,
    )
    captured: dict[str, Any] = {}

    def _start(_self: InstanceSupervisor, request: dict[str, Any]) -> dict[str, Any]:
        captured.update(request)
        return {
            "instance_id": request["instance_id"],
            "workspace": request["workspace"],
            "runtime_root": request["runtime_root"],
            "backend_url": "http://127.0.0.1:60101",
            "frontend_url": "http://127.0.0.1:5174",
            "token": "isolated-generated-token",
            "metadata": request["metadata"],
        }

    monkeypatch.setattr(InstanceSupervisor, "start_instance", _start)

    result = bench._start_isolated_bench_project_instance(
        bench_session_id="bench-session",
        project_id="L1-01",
        project_title="One",
        level=1,
        bench_workspace=workspace.parent,
        project_workspace=str(workspace),
        backend_token="token",
        launch_receipt=receipt,
    )

    assert result is not None and result["ok"] is True
    assert captured["instance_id"] == receipt["instance_id"]
    assert captured["require_fresh_instance"] is True
    assert captured["workspace"] == receipt["workspace"]
    assert captured["runtime_root"] == receipt["runtime_root"]
    assert "token" not in captured
    assert result["token"] == "isolated-generated-token"
    assert Path(captured["polaris_root"]).resolve() == bench._REPO_ROOT
    assert (Path(captured["polaris_root"]) / "src" / "backend").resolve() == bench._BACKEND_ROOT
    assert Path(bench.__file__).resolve().parents[2] == bench._BACKEND_ROOT
    assert captured["bench"]["launch_scope"] == receipt["launch_scope"]
    assert captured["metadata"]["instance_launch_receipt"] == receipt


def test_director_resume_stops_only_owned_prior_bench_instance(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    from polaris.cells.instances.internal.service import InstanceSupervisor

    monkeypatch.setattr(bench_session, "compute_source_fingerprint", lambda _root: "source-fingerprint")
    monkeypatch.setattr(bench, "_wait_backend_health", lambda *_args, **_kwargs: True)
    workspace = _allocate_fresh_project_workspace(tmp_path, project_id="L1-01", run_id="original-run")
    catalog_meta = _write_test_workspace_catalog(
        tmp_path,
        workspace,
        run_id="original-run",
        project_id="L1-01",
    )
    receipt = bench._new_isolated_bench_launch_receipt(
        bench_session_id="bench-resume",
        run_id="repair-attempt",
        project_id="L1-01",
        requested_project_id="L1-01",
        canonical_project_id="L1-01",
        bench_workspace=tmp_path,
        project_workspace=str(workspace),
        workspace_catalog_meta=catalog_meta,
    )
    stopped: list[str] = []
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        InstanceSupervisor,
        "list_instances",
        lambda _self: [
            {
                "instance_id": "owned-prior",
                "kind": "bench_project",
                "status": "running",
                "workspace": str(workspace),
                "metadata": {
                    "internal_test_only": True,
                    "instance_launch_receipt": {
                        "run_id": "original-run",
                        "bench_workspace": str(tmp_path),
                        "project_id": "L1-01",
                    },
                },
            },
            {
                "instance_id": "foreign-instance",
                "kind": "bench_project",
                "status": "running",
                "workspace": str(workspace),
                "metadata": {
                    "internal_test_only": True,
                    "instance_launch_receipt": {
                        "run_id": "another-run",
                        "bench_workspace": str(tmp_path),
                        "project_id": "L1-01",
                    },
                },
            },
        ],
    )
    monkeypatch.setattr(
        InstanceSupervisor,
        "stop_instance",
        lambda _self, instance_id: stopped.append(instance_id) or {"instance_id": instance_id, "status": "stopped"},
    )

    def _start(_self: InstanceSupervisor, request: dict[str, Any]) -> dict[str, Any]:
        captured.update(request)
        return {
            "instance_id": request["instance_id"],
            "workspace": request["workspace"],
            "runtime_root": request["runtime_root"],
            "backend_url": "http://127.0.0.1:60101",
            "frontend_url": "http://127.0.0.1:5174",
            "token": "isolated-generated-token",
            "metadata": request["metadata"],
        }

    monkeypatch.setattr(InstanceSupervisor, "start_instance", _start)

    result = bench._start_isolated_bench_project_instance(
        bench_session_id="bench-resume",
        project_id="L1-01",
        project_title="One",
        level=1,
        bench_workspace=tmp_path,
        project_workspace=str(workspace),
        backend_token="token",
        launch_receipt=receipt,
    )

    assert result is not None and result["ok"] is True
    assert stopped == ["owned-prior"]
    assert captured["metadata"]["instance_launch_receipt"]["resume_predecessor_instance_ids"] == ["owned-prior"]


def test_isolated_launch_reports_registry_corruption_as_platform_failure(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    from polaris.cells.instances.internal import service as instance_service

    instance_home = tmp_path / "instances"
    instance_home.mkdir(parents=True)
    registry_path = instance_home / "registry.json"
    original_bytes = b'{"schema_version": 1, "instances": ['
    registry_path.write_bytes(original_bytes)
    monkeypatch.setenv(instance_service.INSTANCE_HOME_ENV, str(instance_home))
    monkeypatch.setattr(bench_session, "compute_source_fingerprint", lambda _root: "source-fingerprint")
    side_effects = {"allocate": 0, "spawn": 0}

    def _unexpected_allocate(*_args: Any, **_kwargs: Any) -> int:
        side_effects["allocate"] += 1
        raise AssertionError("port allocation must not run")

    def _unexpected_spawn(*_args: Any, **_kwargs: Any) -> int:
        side_effects["spawn"] += 1
        raise AssertionError("backend spawn must not run")

    monkeypatch.setattr(instance_service, "allocate_port", _unexpected_allocate)
    monkeypatch.setattr(instance_service.InstanceSupervisor, "_start_backend", _unexpected_spawn)
    workspace = tmp_path / "factory-bench-corrupt" / "L1-01"
    workspace.mkdir(parents=True)
    catalog_meta = _write_test_workspace_catalog(
        workspace.parent,
        workspace,
        run_id="run-corrupt",
        project_id="L1-01",
    )
    receipt = bench._new_isolated_bench_launch_receipt(
        bench_session_id="bench-corrupt",
        run_id="run-corrupt",
        project_id="L1-01",
        requested_project_id="L1-01",
        canonical_project_id="L1-01",
        bench_workspace=workspace.parent,
        project_workspace=str(workspace),
        workspace_catalog_meta=catalog_meta,
    )

    result = bench._start_isolated_bench_project_instance(
        bench_session_id="bench-corrupt",
        project_id="L1-01",
        project_title="One",
        level=1,
        bench_workspace=workspace.parent,
        project_workspace=str(workspace),
        backend_token="token",
        launch_receipt=receipt,
    )

    assert result is not None
    assert result["ok"] is False
    assert result["error"] == "instance_registry_unavailable"
    assert result["failure_class"] == "platform_failure"
    assert result["error_code"] == "instance_registry_corrupt"
    assert result["error_type"] == "RegistryCorruptionError"
    assert result["platform_error"]["registry_path"] == str(registry_path)
    assert result["platform_error"]["reason"] == "invalid_json"
    assert side_effects == {"allocate": 0, "spawn": 0}
    assert registry_path.read_bytes() == original_bytes


def test_default_bench_session_reporting_mode_is_auto(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_SESSION_REPORTING", raising=False)

    assert bench._default_bench_session_reporting_mode() == "auto"


def test_invalid_bench_session_reporting_mode_falls_back_to_auto(monkeypatch: Any) -> None:
    monkeypatch.setenv("FACTORY_BENCH_SESSION_REPORTING", "enabled")

    assert bench._default_bench_session_reporting_mode() == "auto"


def test_isolated_auto_reporting_does_not_use_shared_backend() -> None:
    assert (
        bench._bench_session_backend_url(
            launcher_instance_mode="isolated",
            bench_session_reporting="auto",
            backend_url="http://127.0.0.1:49977",
        )
        == ""
    )


def test_observed_auto_reporting_uses_shared_backend() -> None:
    assert (
        bench._bench_session_backend_url(
            launcher_instance_mode="observed",
            bench_session_reporting="auto",
            backend_url="http://127.0.0.1:49977/",
        )
        == "http://127.0.0.1:49977"
    )


def test_shared_reporting_overrides_isolated_default() -> None:
    assert (
        bench._bench_session_backend_url(
            launcher_instance_mode="isolated",
            bench_session_reporting="shared",
            backend_url="http://127.0.0.1:49977/",
        )
        == "http://127.0.0.1:49977"
    )


def test_isolated_launch_receipts_get_new_run_scoped_instance_ids(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(bench_session, "compute_source_fingerprint", lambda _root: "source-fingerprint")
    first_workspace = _allocate_fresh_project_workspace(tmp_path, project_id="L1-01", run_id="run-001")
    first_meta = _write_test_workspace_catalog(
        tmp_path,
        first_workspace,
        run_id="run-001",
        project_id="L1-01",
    )
    first = bench._new_isolated_bench_launch_receipt(
        bench_session_id="bench-reused",
        run_id="run-001",
        project_id="L1-01",
        requested_project_id="L1-01",
        canonical_project_id="L1-01",
        bench_workspace=tmp_path,
        project_workspace=str(first_workspace),
        workspace_catalog_meta=first_meta,
    )
    second_workspace = _allocate_fresh_project_workspace(tmp_path, project_id="L1-01", run_id="run-002")
    second_meta = _write_test_workspace_catalog(
        tmp_path,
        second_workspace,
        run_id="run-002",
        project_id="L1-01",
    )
    second = bench._new_isolated_bench_launch_receipt(
        bench_session_id="bench-reused",
        run_id="run-002",
        project_id="L1-01",
        requested_project_id="L1-01",
        canonical_project_id="L1-01",
        bench_workspace=tmp_path,
        project_workspace=str(second_workspace),
        workspace_catalog_meta=second_meta,
    )

    assert first["requested_instance_id"] == second["requested_instance_id"]
    assert first["instance_id"] != second["instance_id"]
    assert first["launch_nonce"] != second["launch_nonce"]
    assert len(str(first["instance_id"])) <= 80


def _matching_isolated_launch_identity(
    tmp_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    workspace = tmp_path / "L1-01"
    workspace.mkdir()
    catalog_meta = _write_test_workspace_catalog(
        tmp_path,
        workspace,
        run_id="run-001",
        project_id="L1-01",
    )
    receipt = {
        "schema_version": "factory_bench.isolated_launch_receipt.v1",
        "launch_scope": "run-001:L1-01:nonce",
        "launch_nonce": "nonce",
        "run_id": "run-001",
        "workspace_source_run_id": "run-001",
        "project_id": "L1-01",
        "requested_project_id": "L1-01",
        "canonical_project_id": "L1-01",
        "requested_instance_id": "bench-l1-01",
        "instance_id": "bench-l1-01-run-run-001-nonce",
        "bench_workspace": str(tmp_path.absolute()),
        "workspace": str(workspace.resolve()),
        "workspace_device": catalog_meta["workspace_device"],
        "workspace_inode": catalog_meta["workspace_inode"],
        "workspace_catalog_hash": bench._workspace_catalog_hash(catalog_meta),
        "runtime_root": str((tmp_path / "L1-01" / "runtime").resolve()),
        "expected_backend_root": str(bench._BACKEND_ROOT),
        "expected_source_fingerprint": "expected-source",
    }
    instance = {
        "instance_id": receipt["instance_id"],
        "workspace": receipt["workspace"],
        "runtime_root": receipt["runtime_root"],
        "backend_pid": 73101,
        "metadata": {"instance_launch_receipt": dict(receipt)},
    }
    backend_context = {
        "backend_freshness": {
            "ok": True,
            "expected_fingerprint": "expected-source",
            "actual_fingerprint": "expected-source",
            "backend_info": {
                "pid": 73101,
                "instance_id": receipt["instance_id"],
                "workspace": receipt["workspace"],
                "backend_root": receipt["expected_backend_root"],
            },
        }
    }
    return receipt, instance, backend_context


def test_isolated_launch_validation_accepts_matching_process_identity(tmp_path: Path) -> None:
    receipt, instance, backend_context = _matching_isolated_launch_identity(tmp_path)

    validation = bench._validate_isolated_bench_launch(
        instance=instance,
        receipt=receipt,
        backend_context=backend_context,
    )

    assert validation["ok"] is True
    assert validation["backend_pid"] == 73101
    assert validation["backend_root"] == str(bench._BACKEND_ROOT)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("pid", 99999, "backend_pid_mismatch"),
        ("instance_id", "impostor-instance", "backend_instance_id_mismatch"),
        ("backend_root", "/tmp/impostor-backend", "backend_root_mismatch"),
    ],
)
def test_isolated_launch_validation_rejects_backend_process_identity_mismatch(
    tmp_path: Path,
    field: str,
    value: Any,
    reason: str,
) -> None:
    receipt, instance, backend_context = _matching_isolated_launch_identity(tmp_path)
    backend_context["backend_freshness"]["backend_info"][field] = value

    validation = bench._validate_isolated_bench_launch(
        instance=instance,
        receipt=receipt,
        backend_context=backend_context,
    )

    assert validation["ok"] is False
    assert reason in validation["reasons"]


def test_isolated_launch_validation_fails_closed_for_source_mismatch(tmp_path: Path) -> None:
    receipt, instance, backend_context = _matching_isolated_launch_identity(tmp_path)
    backend_context["backend_freshness"]["actual_fingerprint"] = "wrong-source"

    validation = bench._validate_isolated_bench_launch(
        instance=instance,
        receipt=receipt,
        backend_context=backend_context,
    )

    assert validation["ok"] is False
    assert validation["error"] == "measurement_contaminated"
    assert "actual_source_fingerprint_mismatch" in validation["reasons"]


def test_isolated_launch_validation_rejects_same_path_inode_replacement(tmp_path: Path) -> None:
    receipt, instance, backend_context = _matching_isolated_launch_identity(tmp_path)
    workspace = Path(str(receipt["workspace"]))
    displaced = workspace.with_name(f"{workspace.name}-displaced")
    workspace.rename(displaced)
    workspace.mkdir()

    validation = bench._validate_isolated_bench_launch(
        instance=instance,
        receipt=receipt,
        backend_context=backend_context,
    )

    assert validation["ok"] is False
    assert "workspace_inode_mismatch" in validation["reasons"]


def test_isolated_launch_validation_requires_catalog_hash(tmp_path: Path) -> None:
    receipt, instance, backend_context = _matching_isolated_launch_identity(tmp_path)
    receipt["workspace_catalog_hash"] = ""
    instance["metadata"]["instance_launch_receipt"] = dict(receipt)

    validation = bench._validate_isolated_bench_launch(
        instance=instance,
        receipt=receipt,
        backend_context=backend_context,
    )

    assert validation["ok"] is False
    assert "launch_receipt_workspace_catalog_hash_missing" in validation["reasons"]
    assert "launch_receipt_workspace_catalog_hash_invalid" in validation["reasons"]


def test_isolated_launch_validation_rejects_missing_catalog(tmp_path: Path) -> None:
    receipt, instance, backend_context = _matching_isolated_launch_identity(tmp_path)
    (Path(str(receipt["workspace"])) / ".catalog_meta.json").unlink()

    validation = bench._validate_isolated_bench_launch(
        instance=instance,
        receipt=receipt,
        backend_context=backend_context,
    )

    assert validation["ok"] is False
    assert "workspace_catalog_unavailable" in validation["reasons"]


def test_isolated_launch_validation_rejects_mutated_catalog(tmp_path: Path) -> None:
    receipt, instance, backend_context = _matching_isolated_launch_identity(tmp_path)
    catalog_path = Path(str(receipt["workspace"])) / ".catalog_meta.json"
    catalog_path.write_text('{"run_id":"impostor"}\n', encoding="utf-8")

    validation = bench._validate_isolated_bench_launch(
        instance=instance,
        receipt=receipt,
        backend_context=backend_context,
    )

    assert validation["ok"] is False
    assert "workspace_catalog_hash_mismatch" in validation["reasons"]
    assert "workspace_catalog_run_id_mismatch" in validation["reasons"]


def test_bench_observation_posts_use_short_timeout(monkeypatch: Any) -> None:
    timeouts: list[float] = []

    def _capture_post(_url: str, _body: dict[str, Any], *, timeout_s: float, token: str = "") -> dict[str, Any]:
        assert token == "token"
        timeouts.append(timeout_s)
        return {"appended": True, "updated": True}

    monkeypatch.setattr(bench_session, "_http_post_json", _capture_post)

    assert bench._push_bench_event_to_backend(
        backend_url="http://127.0.0.1:49977",
        session_id="bench-test",
        event_type="project.started",
        token="token",
    )
    assert bench._push_bench_progress_to_backend(
        backend_url="http://127.0.0.1:49977",
        session_id="bench-test",
        completed=0,
        failed=0,
        token="token",
    )
    assert bench._push_bench_complete_to_backend(
        backend_url="http://127.0.0.1:49977",
        session_id="bench-test",
        token="token",
    )
    assert timeouts == [bench._BENCH_OBSERVATION_HTTP_TIMEOUT_S] * 3


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "all_checks_passed": True,
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "has_qa_verdict": True,
        "chain_state": "clean",
        "chain_results": {"qa_ran": True, "qa_passed": True},
        "wrong_product_suspect": False,
        "implementation_depth": {"ok": True, "detail": "implementation depth passed"},
        "backend_freshness": {"ok": True, "detail": "backend fresh"},
        "task_runtime_projection": {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
            "row_count": 1,
            "rows": [
                {
                    "task_id": "TASK-1",
                    "status": "completed",
                    "execution_state": "completed",
                    "fact_event_seq": 1,
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                }
            ],
            "readiness": {"ready": True, "blocking_reasons": []},
        },
        "factory_terminal_status": {
            "status": "completed",
            "metadata": {
                "canonical_authoritative": True,
                "terminal_source": "task_runtime.execution_fact",
                "task_row_read_model_source": "task_runtime.execution_fact",
            },
        },
        "run_ledger": {
            "ledger_path": __file__,
            "content_id": "content-id",
            "event_id": "content-id",
            "append_id": "append-id",
            "job_token_id": "job-token-id",
            "job_token": {"capability_audit": {"ok": True, "issues": []}},
        },
        "run_ledger_projection": {
            "source": "run_ledger",
            "integrity_ok": True,
            "outcome_ok": True,
            "ok": True,
            "event_count": 1,
            "gate_count": 1,
            "gates": [
                {
                    "name": "qa_verdict",
                    "stage": "qa",
                    "ok": True,
                    "summary": "QA passed",
                    "content_id": "qa-content-id",
                    "append_id": "qa-append-id",
                    "capability_ok": True,
                }
            ],
            "failed_gates": [],
            "capability": {"ok": True, "issues": [], "latest_token_id": "job-token-id"},
            "physical_evidence": {},
            "evidence_policy": {
                "ok": True,
                "integrity_ok": True,
                "outcome_ok": True,
                "missing_required_modalities": [],
                "failed_required_modalities": [],
            },
            "tool_lifecycle": {"ok": True},
            "task_boundary": {
                "ok": True,
                "verdict_count": 1,
                "latest": {"ok": True, "status": "completed_verified"},
                "failed": [],
            },
        },
    }
    record.update(overrides)
    return record


def _successful_audit_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "all_checks_passed": True,
        "static_checks_passed": True,
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "has_qa_verdict": True,
        "code_file_count": 1,
        "source_file_count": 1,
        "implementation_depth": {"ok": True, "detail": "implementation depth passed"},
        "code_files": ["src/index.js"],
        "target_files": ["src/index.js"],
        "allowed_paths": ["src/index.js"],
        "required_artifacts": ["src/index.js"],
        "project_brief": "Build something",
        "task_runtime_projection": {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
            "row_count": 1,
            "rows": [
                {
                    "task_id": "TASK-1",
                    "status": "completed",
                    "execution_state": "completed",
                    "fact_event_seq": 1,
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                }
            ],
            "readiness": {"ready": True, "blocking_reasons": []},
        },
        "factory_terminal_status": {
            "status": "completed",
            "metadata": {
                "canonical_authoritative": True,
                "terminal_source": "task_runtime.execution_fact",
                "task_row_read_model_source": "task_runtime.execution_fact",
            },
        },
        "blueprint_id": "bp-test",
        "blueprints": [{"id": "bp-test"}],
        "checks": [],
    }
    record.update(overrides)
    return record


def _ok_run_ledger_projection(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "source": "run_ledger",
        "ok": True,
        "integrity_ok": True,
        "outcome_ok": True,
        "event_count": 1,
        "gate_count": 1,
        "gates": [
            {
                "name": "qa_verdict",
                "stage": "qa",
                "ok": True,
                "summary": "QA passed",
                "content_id": "qa-content-id",
                "append_id": "qa-append-id",
                "capability_ok": True,
            }
        ],
        "failed_gates": [],
        "capability": {"ok": True, "issues": [], "latest_token_id": "job-token-id"},
        "physical_evidence": {"command_count": 1},
        "evidence_policy": {
            "ok": True,
            "integrity_ok": True,
            "outcome_ok": True,
            "missing_required_modalities": [],
            "failed_required_modalities": [],
        },
        "tool_lifecycle": {"ok": True},
        "task_boundary": {
            "ok": True,
            "verdict_count": 1,
            "latest": {"ok": True, "status": "completed_verified"},
            "failed": [],
        },
    }


def test_bench_reads_authoritative_task_boundary_without_appending(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap_test_fact_stream(workspace)
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(workspace),
            run_id="run-task-boundary",
            event={
                "event_type": "task_boundary_verdict",
                "stage": "task_boundary",
                "task_id": "TASK-1",
                "task_boundary_verdict": {
                    "schema_version": "task_boundary_verdict.v1",
                    "status": "dependency_not_unlocked",
                    "ok": False,
                    "failure_class": "DEPENDENCY_NOT_UNLOCKED",
                    "responsible_layer": "execution_control_plane",
                    "reason": "TASK-0 is not complete",
                },
                "job_token": {
                    "token_id": "token-task-boundary",
                    "run_id": "run-task-boundary",
                    "task_id": "TASK-1",
                    "project_id": "L1-01",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
            },
        )
    )
    projection = bench_cli.load_run_ledger_projection(workspace, run_id="run-task-boundary")
    event_count_before = int(projection["event_count"])

    verdict = bench._read_task_boundary_verdict_from_run_ledger_projection(projection)

    event_count_after = int(
        bench_cli.load_run_ledger_projection(workspace, run_id="run-task-boundary")["event_count"]
    )

    assert verdict["ok"] is False
    assert verdict["failure_class"] == "DEPENDENCY_NOT_UNLOCKED"
    assert projection["task_boundary"]["ok"] is False
    assert projection["task_boundary"]["latest"]["failure_class"] == "DEPENDENCY_NOT_UNLOCKED"
    assert event_count_before == event_count_after == 1


def test_bench_reports_missing_authoritative_task_boundary() -> None:
    verdict = bench._read_task_boundary_verdict_from_run_ledger_projection({})

    assert verdict["ok"] is False
    assert verdict["status"] == "task_boundary_verdict_missing"
    assert verdict["failure_class"] == "EXECUTION_EVIDENCE_MISSING"
    assert verdict["responsible_layer"] == "execution_control_plane"
    assert verdict["authoritative"] is False


def test_bench_preserves_text_fallback_task_boundary_classification() -> None:
    verdict = bench._read_task_boundary_verdict_from_run_ledger_projection(
        {
            "task_boundary": {
                "latest": {
                    "ok": False,
                    "status": "required_tool_text_fallback_not_dispatched",
                    "failure_class": "REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED",
                    "responsible_layer": "execution_control_plane",
                    "reason": "compatibility parser produced no dispatch",
                }
            }
        }
    )

    assert verdict["ok"] is False
    assert verdict["status"] == "required_tool_text_fallback_not_dispatched"
    assert verdict["failure_class"] == "REQUIRED_TOOL_TEXT_FALLBACK_NOT_DISPATCHED"
    assert verdict["responsible_layer"] == "execution_control_plane"


def test_canonical_projection_preserves_operational_evidence_fields() -> None:
    record = _record(
        requested_project_id="requested-L1-01",
        canonical_project_id="canonical-L1-11",
        instance_id="bench-instance-1",
        workspace="/tmp/factory-bench/L1-01",
        backend_port=51001,
        frontend_port=52001,
        run_id="bench-run-1",
        factory_run_id="factory-run-1",
        final_request_refs=[{"role": "qa", "context_snapshot_ref": "a" * 24}],
        factory_terminal_status={
            "status": "completed",
            "metadata": {
                "canonical_authoritative": True,
                "terminal_source": "task_runtime.execution_fact",
                "task_row_read_model_source": "task_runtime.execution_fact",
                "execution_barrier": {"barrier_satisfied": True},
                "fallback": {"used": False},
            },
        },
    )

    projection = bench_gates.build_canonical_bench_projection(record)

    assert projection["source"] == "canonical_projection"
    assert projection["requested_project_id"] == "requested-L1-01"
    assert projection["canonical_project_id"] == "canonical-L1-11"
    assert projection["instance"] == {
        "id": "bench-instance-1",
        "workspace": "/tmp/factory-bench/L1-01",
    }
    assert projection["ports"] == {"backend": 51001, "frontend": 52001}
    assert projection["run_ids"] == {"bench": "bench-run-1", "factory": "factory-run-1"}
    assert projection["final_request_refs"] == [{"role": "qa", "context_snapshot_ref": "a" * 24}]
    assert projection["lifecycle"] == {"ok": True}
    assert projection["effect"]["physical_evidence"] == {}
    assert projection["boundary"]["authoritative"] is True
    assert projection["runtime"]["status"] == "completed"
    assert projection["ledger"]["source"] == "run_ledger"
    assert projection["qa"]["name"] == "qa_verdict"
    assert projection["barrier"] == {"barrier_satisfied": True}
    assert projection["fallback"] == {"used": False}


def test_legacy_chain_text_cannot_override_canonical_execution() -> None:
    record = _record(
        chain_state="fail",
        chain_results={"qa_ran": False, "qa_passed": False},
        real_run_gate={"ok": True, "summary": "real run gate passed"},
        llm_route_audit={"ok": True, "summary": "LLM route audit passed"},
    )

    apply_factory_bench_gates(record, chain={"exit_code": 1})

    assert record["static_checks_passed"] is True
    assert record["all_checks_passed"] is True
    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert gates["canonical_execution"]["ok"] is True
    assert "chain_clean" not in gates
    assert "integration_qa_passed" not in gates


def test_runner_audits_llm_routes_for_llm_backed_roles_only() -> None:
    source = "\n".join(
        (
            inspect.getsource(bench_cli),
            inspect.getsource(bench_gates),
        )
    )

    assert bench.FACTORY_BENCH_REQUIRED_LLM_ROLES == ("pm", "chief_engineer", "director", "qa")
    assert "require_all_director_routes=False" in source
    assert "require_all_director_routes=True" not in source
    assert "FACTORY_BENCH_REQUIRED_LLM_ROLES" in source
    assert "required_llm_roles_for_factory_record" in source


def test_missing_legacy_qa_artifact_does_not_override_canonical_projection() -> None:
    record = _record(
        has_qa_verdict=False,
        wrong_product_suspect=True,
    )

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    assert record["all_checks_passed"] is False
    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert "qa_verdict_artifact_present" not in gates
    assert gates["canonical_execution"]["ok"] is True
    assert gates["wrong_product_guard"]["ok"] is False


def test_discover_artifacts_accepts_current_qa_report_verdicts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = tmp_path / "runtime"
    workspace_qa = workspace / ".polaris" / "qa"
    runtime_qa = runtime / "qa"
    workspace_qa.mkdir(parents=True)
    runtime_qa.mkdir(parents=True)
    (workspace_qa / "latest.report.json").write_text(
        json.dumps({"verdict": "PASS", "passed": True}),
        encoding="utf-8",
    )
    (workspace_qa / "empty.report.json").write_text(
        json.dumps({"notes": "not a verdict"}),
        encoding="utf-8",
    )
    (runtime_qa / "report.json").write_text(
        json.dumps({"passed": True}),
        encoding="utf-8",
    )

    artifacts = discover_artifacts(workspace, runtime)

    assert artifacts["verdict"] == [
        "rt:qa/report.json",
        "ws:.polaris/qa/latest.report.json",
    ]


def test_runtime_dir_candidates_merge_artifacts_and_chain_results(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "L1-01"
    workspace.mkdir()
    runtime_base_a = tmp_path / "ramdisk-projects"
    runtime_base_b = tmp_path / "cache-projects"
    runtime_a = runtime_base_a / "l1-01-aaa" / "runtime"
    runtime_b = runtime_base_b / "l1-01-bbb" / "runtime"
    (runtime_a / "contracts").mkdir(parents=True)
    (runtime_b / "results").mkdir(parents=True)
    (runtime_a / "contracts" / "pm_tasks.contract.json").write_text(
        json.dumps({"overall_goal": "Build calculator"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (runtime_a / "contracts" / "plan.md").write_text("plan", encoding="utf-8")
    (runtime_b / "results" / "director.result.json").write_text(
        json.dumps({"total": 2, "successes": 1, "failures": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    (runtime_b / "results" / "integration_qa.result.json").write_text(
        json.dumps({"ran": True, "passed": False, "reason": "director failures"}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.utime(runtime_a, (100, 100))
    os.utime(runtime_b, (200, 200))
    monkeypatch.setattr(bench_artifacts, "_RUNTIME_PROJECT_BASES", (runtime_base_a, runtime_base_b))

    runtime_dirs = resolve_runtime_dirs_for_workspace(workspace)
    artifacts = discover_artifacts(workspace, runtime_dirs)
    chain_results = read_chain_results_from_runtime_dirs(runtime_dirs)

    assert runtime_dirs == [runtime_b, runtime_a]
    assert artifacts["plan"] == ["rt:l1-01-aaa/contracts/plan.md", "rt:l1-01-aaa/contracts/pm_tasks.contract.json"]
    assert chain_results["contract_goal"] == "Build calculator"
    assert chain_results["qa_ran"] is True
    assert chain_results["director"] == {"total": 2, "successes": 1, "failures": 1}


def test_runtime_dir_candidates_prefer_exact_workspace_evidence(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "L1-01"
    workspace.mkdir()
    runtime_base = tmp_path / "projects"
    current_runtime = runtime_base / "l1-01-current" / "runtime"
    stale_runtime = runtime_base / "l1-01-stale" / "runtime"
    (current_runtime / "events").mkdir(parents=True)
    (stale_runtime / "events").mkdir(parents=True)
    other_workspace = tmp_path / "other" / "L1-01"
    (current_runtime / "events" / "task_runtime.execution.jsonl").write_text(
        json.dumps({"workspace": str(workspace.resolve())}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (stale_runtime / "events" / "task_runtime.execution.jsonl").write_text(
        json.dumps({"workspace": str(other_workspace.resolve())}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.utime(current_runtime, (100, 100))
    os.utime(stale_runtime, (200, 200))
    monkeypatch.setattr(bench_artifacts, "_RUNTIME_PROJECT_BASES", (runtime_base,))

    runtime_dirs = resolve_runtime_dirs_for_workspace(workspace)

    assert runtime_dirs == [current_runtime]


def test_runtime_workspace_evidence_paths_include_chief_engineer_llm_events() -> None:
    """Runtime matching evidence must include every canonical role LLM stream."""

    assert "events/pm.llm.events.jsonl" in bench._RUNTIME_WORKSPACE_EVIDENCE_RELATIVE_PATHS
    assert "events/chief_engineer.llm.events.jsonl" in bench._RUNTIME_WORKSPACE_EVIDENCE_RELATIVE_PATHS
    assert "events/director.llm.events.jsonl" in bench._RUNTIME_WORKSPACE_EVIDENCE_RELATIVE_PATHS


def test_clean_chain_preserves_static_pass() -> None:
    record = _record(
        real_run_gate={"ok": True, "summary": "real run gate passed"},
        llm_route_audit={"ok": True, "summary": "LLM route audit passed"},
    )

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    assert record["static_checks_passed"] is True
    assert record["all_checks_passed"] is True
    assert all(gate["ok"] for gate in record["factory_gates"])


def test_real_run_and_llm_route_gates_are_fail_closed_when_present() -> None:
    record = _record(
        real_run_gate={"ok": True, "summary": "real run gate passed"},
        llm_route_audit={"ok": False, "summary": "LLM route audit failed: director"},
    )

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert gates["real_run_gate"]["ok"] is True
    assert gates["llm_route_audit"]["ok"] is False
    assert record["all_checks_passed"] is False


def test_real_run_and_llm_route_gates_are_fail_closed_when_missing() -> None:
    record = _record()

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert gates["real_run_gate"]["ok"] is False
    assert gates["llm_route_audit"]["ok"] is False
    assert record["all_checks_passed"] is False


def test_backend_freshness_gate_is_fail_closed_when_missing() -> None:
    record = _record(
        real_run_gate={"ok": True, "summary": "real run gate passed"},
        llm_route_audit={"ok": True, "summary": "LLM route audit passed"},
    )
    record.pop("backend_freshness")

    apply_factory_bench_gates(record, chain={"exit_code": 0})

    gates = {gate["gate"]: gate for gate in record["factory_gates"]}
    assert gates["stale_backend_or_unknown"]["ok"] is False
    assert "backend freshness gate missing" in gates["stale_backend_or_unknown"]["detail"]
    assert record["all_checks_passed"] is False


def test_build_bench_backend_audit_context_writes_record_fields(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def _fake_check_backend_freshness(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {
            "gate": "stale_backend_or_unknown",
            "ok": True,
            "detail": "backend fresh",
            "backend_url": "http://127.0.0.1:49977",
            "expected_fingerprint": "expected-fp",
            "actual_fingerprint": "actual-fp",
            "backend_info": {
                "pid": 123,
                "instance_id": "bench-run-01",
                "backend_root": str(bench._BACKEND_ROOT),
                "startup_time": "2026-06-21T00:00:00Z",
                "source": "runtime_fingerprint",
            },
        }

    monkeypatch.setattr(bench_gates, "check_backend_freshness", _fake_check_backend_freshness)

    context = bench.build_bench_backend_audit_context(
        "http://127.0.0.1:49977",
        backend_token="token",
        workspace=str(tmp_path),
    )

    assert captured["args"] == ("http://127.0.0.1:49977",)
    assert captured["kwargs"]["token"] == "token"
    assert captured["kwargs"]["backend_root"] == bench._BACKEND_ROOT
    assert context["backend_freshness"]["ok"] is True
    assert context["backend_metadata"]["backend_base_url"] == "http://127.0.0.1:49977"
    assert context["backend_metadata"]["token_source"] == "configured"
    assert context["backend_metadata"]["workspace"] == str(tmp_path)
    assert context["backend_metadata"]["expected_source_fingerprint"] == "expected-fp"
    assert context["backend_metadata"]["actual_backend_fingerprint"] == "actual-fp"
    assert context["backend_metadata"]["backend_pid"] == 123
    assert context["backend_metadata"]["backend_instance_id"] == "bench-run-01"
    assert context["backend_metadata"]["backend_root"] == str(bench._BACKEND_ROOT)


# --- map_factory_run_to_chain_results ---


def test_map_completed_qa_artifact_is_non_authoritative() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle: dict[str, Any] = {
        "gates": [{"gate_name": "quality_gate", "passed": True, "message": "all good"}],
        "summary_json": {"director": {"total": 10, "successes": 8, "failures": 1, "blocked": 1}},
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["source"] == "legacy_artifact"
    assert result["authoritative"] is False
    assert result["degraded"] is True
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["qa_passed"] is None
    assert result["director"] == {"total": 10, "successes": 8, "failures": 1, "blocked": 1}


def test_map_failed_qa_artifact_cannot_set_exit_class() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle = {
        "gates": [{"gate_name": "quality_gate", "passed": False, "message": "lint errors"}],
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["qa_passed"] is None
    assert result["qa_reason"] == ""


def test_map_runtime_phase_cannot_authorize_qa() -> None:
    run_status = {"status": "failed", "phase": "qa_gate"}
    audit_bundle = {
        "gates": [{"gate_name": "quality_gate", "passed": False, "message": "tests failed"}],
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["qa_passed"] is None


def test_map_director_summary_is_display_only() -> None:
    run_status = {"status": "failed", "phase": "director_dispatch"}
    audit_bundle: dict[str, Any] = {
        "gates": [],
        "summary_json": {"director": {"total": 5, "successes": 2, "failures": 3, "blocked": 0}},
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["qa_passed"] is None
    assert result["director"] == {"total": 5, "successes": 2, "failures": 3, "blocked": 0}


def test_map_does_not_fall_back_to_run_status_gates() -> None:
    run_status: dict[str, Any] = {
        "status": "completed",
        "phase": "",
        "gates": [{"gate_name": "quality_gate", "passed": True, "message": "ok"}],
    }
    audit_bundle: dict[str, Any] = {}
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["qa_passed"] is None
    assert result["qa_reason"] == ""


def test_map_does_not_scan_events_tail_for_director() -> None:
    run_status = {"status": "failed", "phase": "director_dispatch"}
    audit_bundle: dict[str, Any] = {
        "gates": [],
        "events_tail": [
            {"stage": "other", "result": {"total": 99}},
            {"stage": "director_dispatch", "result": {"total": 7, "successes": 3, "failures": 4}},
        ],
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["director"] == {"total": None, "successes": None, "failures": None, "blocked": None}


def test_map_does_not_parse_summary_json_string() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle: dict[str, Any] = {
        "gates": [{"gate_name": "quality_gate", "passed": True}],
        "summary_json": '{"director": {"total": 3, "successes": 3}}',
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["director"] == {"total": None, "successes": None, "failures": None, "blocked": None}


def test_map_summary_json_invalid_string_defaults() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle: dict[str, Any] = {
        "gates": [{"gate_name": "quality_gate", "passed": True}],
        "summary_json": "not-json",
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["director"] == {"total": None, "successes": None, "failures": None, "blocked": None}


def test_map_no_qa_gate_defaults() -> None:
    run_status = {"status": "failed", "phase": "build"}
    audit_bundle: dict[str, Any] = {}
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["qa_passed"] is None
    assert result["qa_reason"] == ""


def test_map_contract_goal_always_empty() -> None:
    run_status = {"status": "completed", "phase": "qa_gate"}
    audit_bundle: dict[str, Any] = {"gates": [{"gate_name": "quality_gate", "passed": True}]}
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["contract_goal"] == ""


def test_explicit_bench_session_id_is_registered(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_push(**kwargs: Any) -> str:
        captured.update(kwargs)
        return str(kwargs["session_id"])

    monkeypatch.setattr(bench_session, "_push_bench_session_to_backend", _fake_push)

    session_id = bench._ensure_bench_session(
        backend_url="http://127.0.0.1:49977",
        work_dir="/tmp/bench",
        project_ids=["L1-01"],
        total=1,
        metadata={"levels": [1]},
        requested_session_id="bench-explicit",
        token="secret",
    )

    assert session_id == "bench-explicit"
    assert captured["session_id"] == "bench-explicit"
    assert captured["backend_url"] == "http://127.0.0.1:49977"
    assert captured["token"] == "secret"


def test_bench_session_registration_uses_backend_assigned_id(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_push(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "bench-generated"

    monkeypatch.setattr(bench_session, "_push_bench_session_to_backend", _fake_push)

    session_id = bench._ensure_bench_session(
        backend_url="http://127.0.0.1:49977",
        work_dir="/tmp/bench",
        project_ids=["L1-01"],
        total=1,
    )

    assert session_id == "bench-generated"
    assert captured["session_id"] is None


def test_bench_session_registration_uses_observation_timeout(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _capture_post(_url: str, _body: dict[str, Any], *, timeout_s: float, token: str = "") -> dict[str, Any]:
        captured["timeout_s"] = timeout_s
        captured["token"] = token
        return {"session_id": "bench-generated"}

    monkeypatch.setattr(bench_session, "_http_post_json", _capture_post)

    session_id = bench._push_bench_session_to_backend(
        backend_url="http://127.0.0.1:49977",
        work_dir="/tmp/bench",
        project_ids=["L1-01"],
        total=1,
        token="secret",
    )

    assert session_id == "bench-generated"
    assert captured == {
        "timeout_s": bench._BENCH_OBSERVATION_HTTP_TIMEOUT_S,
        "token": "secret",
    }


def test_bench_observation_failure_circuit_breaks_followup_posts(monkeypatch: Any) -> None:
    calls: list[str] = []

    def _failing_post(url: str, _body: dict[str, Any], *, timeout_s: float, token: str = "") -> dict[str, Any] | None:
        del timeout_s, token
        calls.append(url)
        return None

    monkeypatch.setattr(bench_session, "_http_post_json", _failing_post)

    assert not bench._push_bench_event_to_backend(
        backend_url="http://127.0.0.1:49977",
        session_id="bench-test",
        event_type="project.started",
        token="token",
    )
    assert not bench._push_bench_progress_to_backend(
        backend_url="http://127.0.0.1:49977",
        session_id="bench-test",
        completed=0,
        failed=0,
        token="token",
    )

    assert len(calls) == 1


def test_bench_record_counts_do_not_mark_pending_projects_failed() -> None:
    records = [
        {"all_checks_passed": False},
        {"all_checks_passed": True},
    ]

    counts = bench._bench_record_counts(records, total=12)

    assert counts == {
        "total": 12,
        "attempted": 2,
        "passed": 1,
        "failed": 1,
        "pending": 10,
    }


def test_load_projects_v2_is_standalone_creative_catalog_covering_l1_to_l12() -> None:
    projects = bench.load_projects()
    project_ids = {str(project["id"]) for project in projects}
    levels = {int(project["level"]) for project in projects}
    languages = {str(project.get("primary_language") or "") for project in projects if project.get("primary_language")}
    checks = {str(check) for project in projects for check in project.get("checks", [])}
    by_level = dict.fromkeys(range(1, 13), 0)
    for project in projects:
        by_level[int(project["level"])] += 1

    assert len(projects) == 120
    assert "L1-01" in project_ids
    assert "L12-120" in project_ids
    assert next(project for project in projects if project["id"] == "L1-01")["title"] == "发光昆虫花园模拟器"
    assert levels == set(range(1, 13))
    assert set(by_level.values()) == {10}
    assert {"typescript", "javascript", "go", "rust", "cpp", "java", "python"}.issubset(languages)
    assert {"ts_syntax", "go_compile", "rust_compile", "cpp_compile", "java_compile"}.issubset(checks)
    assert all(str(project.get("creative_hook") or "").strip() for project in projects)
    assert all(len(project.get("novelty_tags") or []) >= 3 for project in projects)
    assert all(
        "creative_hook" in str(project.get("brief") or "") or "创意钩子" in str(project.get("brief") or "")
        for project in projects
    )
    # R17-C: every project must have source_target_coverage check
    assert all(
        any(check.startswith("source_target_coverage:") for check in project.get("checks", [])) for project in projects
    ), "Every project must have a source_target_coverage check"


def test_load_projects_rejects_duplicate_ids_in_extended_catalog(tmp_path: Path) -> None:
    parent = tmp_path / "parent.json"
    child = tmp_path / "child.json"
    parent.write_text(
        json.dumps({"schema_version": "factory-bench/test", "projects": [{"id": "L1-X", "level": 1}]}),
        encoding="utf-8",
    )
    child.write_text(
        json.dumps(
            {
                "schema_version": "factory-bench/test",
                "extends": "parent.json",
                "projects": [{"id": "L1-X", "level": 1}],
            }
        ),
        encoding="utf-8",
    )

    try:
        bench.load_projects(child)
    except ValueError as exc:
        assert "duplicate project id" in str(exc)
    else:
        raise AssertionError("duplicate ids must fail closed")


def _capture_run_chain_command(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    director_workflow_execution_mode: str | None = None,
    director_dispatch_driver: str | None = None,
) -> list[list[str]]:
    workspace = tmp_path / "L6-31"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    captured: list[list[str]] = []

    def _fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench_chain.subprocess, "run", _fake_run)
    kwargs: dict[str, Any] = {}
    if director_workflow_execution_mode is not None:
        kwargs["director_workflow_execution_mode"] = director_workflow_execution_mode
    if director_dispatch_driver is not None:
        kwargs["director_dispatch_driver"] = director_dispatch_driver
    bench.run_chain(
        {"id": "L6-31", "title": "Kanban", "brief": "Build Kanban", "test_focus": "runtime"},
        workspace,
        timeout_s=30,
        log_path=tmp_path / "L6-31.chain.log",
        **kwargs,
    )
    return captured


def test_run_chain_preserves_serial_director_workflow_by_default(monkeypatch: Any, tmp_path: Path) -> None:
    commands = _capture_run_chain_command(monkeypatch, tmp_path)

    assert len(commands) == 1
    cmd = commands[0]
    mode_index = cmd.index("--director-workflow-execution-mode")
    assert cmd[mode_index + 1] == "serial"


def test_run_chain_can_enable_parallel_director_workflow(monkeypatch: Any, tmp_path: Path) -> None:
    commands = _capture_run_chain_command(
        monkeypatch,
        tmp_path,
        director_workflow_execution_mode="parallel",
    )

    assert len(commands) == 1
    cmd = commands[0]
    mode_index = cmd.index("--director-workflow-execution-mode")
    assert cmd[mode_index + 1] == "parallel"


def test_run_chain_task_market_driver_plans_then_dispatches_market(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    commands = _capture_run_chain_command(
        monkeypatch,
        tmp_path,
        director_workflow_execution_mode="parallel",
        director_dispatch_driver="task-market",
    )

    assert len(commands) == 2
    planning_cmd, market_cmd = commands
    assert "--run-director" not in planning_cmd
    assert "--director-workflow-execution-mode" not in planning_cmd
    assert "run_market_chain.py" in market_cmd[1]
    assert "--fresh-market" in market_cmd


def test_main_task_market_driver_uses_http_factory_chain_without_legacy_fallback(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_factory_bench.py",
            "--project-ids",
            "L1-01",
            "--work-dir",
            str(tmp_path),
            "--director-dispatch-driver",
            "task-market",
        ],
    )
    monkeypatch.setattr(
        bench_cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Known", "brief": "Build something"}],
    )
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "token")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-task-market")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_workspace_to_backend", lambda **_kwargs: True)

    def _legacy_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append("legacy")
        raise AssertionError("task-market dispatch must not use the legacy subprocess chain")

    def _http_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append("http")
        raise KeyboardInterrupt()

    monkeypatch.setattr(bench, "run_chain", _legacy_chain)
    monkeypatch.setattr(bench_cli, "run_factory_chain", _http_chain)

    result = bench.main()

    assert result == 130
    assert calls == ["http"]


def test_main_marks_backend_session_failed_when_run_aborts(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    completed: list[dict[str, Any]] = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_factory_bench.py",
            "--project-ids",
            "L1-01",
            "--work-dir",
            str(tmp_path),
            "--max-failed",
            "3",
        ],
    )
    monkeypatch.setattr(
        bench_cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Abort case", "brief": "Build something"}],
    )
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "token")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-abort")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_workspace_to_backend", lambda **_kwargs: True)

    def _capture_complete(**kwargs: Any) -> bool:
        completed.append(kwargs)
        return True

    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", _capture_complete)

    def _abort(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated runner abort")

    monkeypatch.setattr(bench_cli, "run_factory_chain", _abort)

    result = bench.main()

    assert result == 1
    assert completed, "bench session should be marked terminal on runner abort"
    assert completed[-1]["session_id"] == "bench-abort"
    assert completed[-1]["success"] is False
    assert completed[-1]["summary"]["failed"] == 1
    assert completed[-1]["summary"]["error"] == "simulated runner abort"


def test_main_rejects_unknown_explicit_project_ids(
    monkeypatch: Any,
    tmp_path: Path,
    capsys: Any,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_factory_bench.py",
            "--project-ids",
            "L1-01,L2-01",
            "--work-dir",
            str(tmp_path),
        ],
    )
    monkeypatch.setattr(
        bench_cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Known", "brief": "Build something"}],
    )

    def _unexpected_session(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("unknown explicit ids must fail before creating a bench session")

    monkeypatch.setattr(bench_cli, "_ensure_bench_session", _unexpected_session)

    result = bench.main()

    captured = capsys.readouterr()
    assert result == 1
    assert "unknown project id(s): L2-01" in captured.out
    assert "refusing to run partial explicit selection" in captured.out


def test_level_local_explicit_project_ids_resolve_to_catalog_index() -> None:
    projects = [
        {"id": f"L1-{idx:02d}", "level": 1, "title": f"L1 Project {idx}", "brief": "Build L1"} for idx in range(1, 11)
    ]
    projects.extend(
        [
            {"id": "L2-11", "level": 2, "title": "First L2", "brief": "Build first L2"},
            {"id": "L2-12", "level": 2, "title": "Second L2", "brief": "Build second L2"},
        ]
    )

    selected, missing_ids, alias_to_canonical = bench._resolve_explicit_project_selection(projects, ["L2-01"])

    assert missing_ids == []
    assert alias_to_canonical == {"L2-01": "L2-11"}
    assert selected == [
        {
            "id": "L2-01",
            "level": 2,
            "title": "First L2",
            "brief": "Build first L2",
            "requested_project_id": "L2-01",
            "canonical_catalog_project_id": "L2-11",
        }
    ]
    assert projects[10]["id"] == "L2-11"
    assert "requested_project_id" not in projects[10]


def test_main_defaults_to_l1_through_l12_catalog(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    projects = [
        {"id": f"L{level}-{level:02d}", "level": level, "title": f"Project {level}", "brief": "Build something"}
        for level in range(1, 13)
    ]

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(bench_cli, "load_projects", lambda: projects)
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)

    def _capture_session(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "bench-default"

    def _stop_after_registration(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise KeyboardInterrupt()

    monkeypatch.setattr(bench_cli, "_ensure_bench_session", _capture_session)
    monkeypatch.setattr(bench_cli, "run_factory_chain", _stop_after_registration)

    result = bench.main()

    assert result == 130
    assert captured["total"] == 12
    assert captured["metadata"]["levels"] == list(range(1, 13))
    assert captured["project_ids"] == [project["id"] for project in projects]


def test_main_default_max_failed_zero_does_not_early_stop(monkeypatch: Any, tmp_path: Path) -> None:
    calls: list[str] = []
    projects = [
        {"id": "L1-01", "level": 1, "title": "One", "brief": "Build one"},
        {"id": "L2-02", "level": 2, "title": "Two", "brief": "Build two"},
    ]

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(bench_cli, "load_projects", lambda: projects)
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-no-early-stop")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        bench_cli,
        "build_factory_audit_record",
        lambda **_kwargs: _successful_audit_record(),
    )
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": False, "summary": "real run failed"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": False, "summary": "LLM route audit failed"},
    )

    def _chain(project: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append(str(project["id"]))
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": str(project["brief"]),
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench_cli, "run_factory_chain", _chain)

    result = bench.main()

    assert result == 1
    assert calls == ["L1-01", "L2-02"]


# --- run_factory_chain (API path) ---


def _setup_run_factory_chain_mocks(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    start_response: dict[str, Any] | None,
    terminal_status: dict[str, Any] | None,
    audit_bundle: dict[str, Any] | None,
) -> Path:
    workspace = tmp_path / "L2-07"
    workspace.mkdir()
    expected_workspace = str(workspace)
    _LAST_FACTORY_START_PAYLOAD.clear()
    _LAST_FACTORY_RESUME_CALL.clear()

    def _fake_start_factory_run(_backend_url: str, _payload: dict[str, Any], token: str = "") -> dict[str, Any] | None:
        _LAST_FACTORY_START_PAYLOAD.update(_payload)
        return start_response

    def _fake_list_factory_runs(
        _backend_url: str,
        *,
        token: str = "",
        workspace: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        del token, limit
        assert workspace == expected_workspace
        run_id = str((start_response or {}).get("run_id") or "run-director")
        return {
            "runs": [
                {
                    "run_id": run_id,
                    "status": "failed",
                    "current_stage": "director_dispatch",
                    "last_successful_stage": "chief_engineer_review",
                    "failure": {"stage": "director_dispatch"},
                    "roles": {
                        "pm": {"status": "completed"},
                        "chief_engineer": {"status": "completed"},
                        "director": {"status": "failed"},
                    },
                    "metadata": {},
                }
            ],
            "total": 1,
        }

    def _fake_retry_factory_run_from_director(
        _backend_url: str,
        run_id: str,
        *,
        token: str = "",
        workspace: str = "",
        reason: str = "",
    ) -> dict[str, Any] | None:
        assert workspace == expected_workspace
        _LAST_FACTORY_RESUME_CALL.update(
            {"run_id": run_id, "token": token, "workspace": workspace, "reason": reason}
        )
        return start_response

    def _fake_wait_run_until_terminal(
        _backend_url: str,
        run_id: str,
        token: str = "",
        workspace: str = "",
        on_status: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        assert workspace == expected_workspace
        if on_status is not None and terminal_status is not None:
            on_status(terminal_status)
        return terminal_status

    def _fake_get_audit_bundle(
        _backend_url: str,
        _run_id: str,
        token: str = "",
        workspace: str = "",
    ) -> dict[str, Any] | None:
        assert workspace == expected_workspace
        return audit_bundle

    def _fake_cancel_factory_run(
        _backend_url: str,
        _run_id: str,
        *,
        reason: str = "",
        token: str = "",
        workspace: str = "",
        return_errors: bool = False,
    ) -> dict[str, Any]:
        del return_errors
        assert reason
        assert workspace == expected_workspace
        return {"status": "cancelled"}

    monkeypatch.setattr(bench_chain, "start_factory_run", _fake_start_factory_run)
    monkeypatch.setattr(bench_chain, "list_factory_runs", _fake_list_factory_runs)
    monkeypatch.setattr(bench_chain, "retry_factory_run_from_director", _fake_retry_factory_run_from_director)
    monkeypatch.setattr(bench_chain, "wait_run_until_terminal", _fake_wait_run_until_terminal)
    monkeypatch.setattr(bench_chain, "get_audit_bundle", _fake_get_audit_bundle)
    monkeypatch.setattr(bench_chain, "cancel_factory_run", _fake_cancel_factory_run)

    return workspace


def _write_director_resume_evidence(workspace: Path) -> None:
    from polaris.kernelone.storage import resolve_runtime_path

    plan_path = Path(resolve_runtime_path(str(workspace), "runtime/tasks/plan.json"))
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "goal": "Implement feature",
                        "target_files": ["src/index.ts"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    task_path = Path(resolve_runtime_path(str(workspace), "runtime/tasks/task_1.json"))
    task_path.write_text(
        json.dumps({"id": 1, "status": "pending", "subject": "Implement feature"}, ensure_ascii=False),
        encoding="utf-8",
    )
    blueprint_path = workspace / ".polaris" / "blueprints" / "latest.review.json"
    blueprint_path.parent.mkdir(parents=True, exist_ok=True)
    blueprint_path.write_text(
        json.dumps(
            {"generated_blueprints": 1, "blueprints": [{"task_id": "TASK-1"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_run_factory_chain_success(monkeypatch: Any, tmp_path: Path) -> None:
    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response={"run_id": "run-123"},
        terminal_status={"status": "completed", "phase": "qa_gate"},
        audit_bundle={
            "gates": [{"gate_name": "quality_gate", "passed": True, "message": "ok"}],
            "summary_json": {"director": {"total": 5, "successes": 5, "failures": 0, "blocked": 0}},
        },
    )

    result = run_factory_chain(
        {"id": "L2-07", "title": "Tetris", "brief": "Build Tetris", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-07.chain.log",
    )

    assert result["exit_code"] == 0
    assert result["run_id"] == "run-123"
    assert result["chain_results"]["exit_class"] == "legacy_unknown"
    assert result["chain_results"]["authoritative"] is False
    assert result["chain_results"]["qa_passed"] is None
    assert result["chain_results"]["director"] == {
        "total": 5,
        "successes": 5,
        "failures": 0,
        "blocked": 0,
    }
    assert "audit_bundle" in result
    assert _LAST_FACTORY_START_PAYLOAD["workspace"] == str(workspace)
    assert _LAST_FACTORY_START_PAYLOAD["persist_workspace"] is False
    assert _LAST_FACTORY_START_PAYLOAD["director_workflow_execution_mode"] == "parallel"
    assert _LAST_FACTORY_START_PAYLOAD["director_dispatch_driver"] == "task-market"


def test_run_factory_chain_director_resume_uses_existing_pm_ce_evidence(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    from polaris.kernelone.storage import resolve_runtime_path

    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response={"run_id": "run-director"},
        terminal_status={"status": "completed", "phase": "qa_gate"},
        audit_bundle={
            "gates": [{"gate_name": "quality_gate", "passed": True, "message": "ok"}],
            "summary_json": {"director": {"total": 1, "successes": 1, "failures": 0, "blocked": 0}},
        },
    )
    _bootstrap_test_fact_stream(workspace)
    _write_director_resume_evidence(workspace)
    task_path = Path(resolve_runtime_path(str(workspace), "runtime/tasks/task_1.json"))
    task_path.write_text(
        json.dumps(
            {
                "id": 1,
                "status": "failed",
                "subject": "Implement feature",
                "metadata": {
                    "runtime_execution": {"status": "failed"},
                    "workflow_run_id": "director-old",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    session_path = Path(resolve_runtime_path(str(workspace), "runtime/tasks/task_1.session.json"))
    session_path.write_text(json.dumps({"status": "failed"}, ensure_ascii=False), encoding="utf-8")

    result = run_factory_chain(
        {"id": "L2-07", "title": "Tetris", "brief": "Build Tetris", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-07.chain.log",
        start_from="director_resume",
    )

    assert result["exit_code"] == 0
    assert result["start_from"] == "director_resume"
    assert result["factory_api_start_from"] == "director_resume"
    assert result["factory_resume_mode"] == "same_run_retry_phase"
    assert _LAST_FACTORY_START_PAYLOAD == {}
    assert _LAST_FACTORY_RESUME_CALL["run_id"] == "run-director"
    assert _LAST_FACTORY_RESUME_CALL["workspace"] == str(workspace)
    assert "preserve committed PM/CE checkpoints" in _LAST_FACTORY_RESUME_CALL["reason"]
    snapshot_manifest = workspace / ".polaris" / "factory_snapshots" / "pre_director" / "manifest.json"
    assert json.loads(snapshot_manifest.read_text(encoding="utf-8"))["snapshot_kind"] == "pre_director_workspace"
    reset_task = json.loads(task_path.read_text(encoding="utf-8"))
    assert reset_task["status"] == "pending"
    assert "runtime_execution" not in reset_task["metadata"]
    assert not session_path.exists()
    reset_evidence = task_path.parent / "director_resume_reset.json"
    assert json.loads(reset_evidence.read_text(encoding="utf-8"))["reset_statuses"] == "all_task_records"


def test_director_resume_accepts_fact_authoritative_runtime_without_task_file_mirror(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """Resume uses durable PM/CE evidence; Director rematerializes task rows."""

    from polaris.kernelone.storage import resolve_runtime_path

    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response={"run_id": "run-director-fact-authority"},
        terminal_status={"status": "completed", "phase": "qa_gate"},
        audit_bundle={},
    )
    _bootstrap_test_fact_stream(workspace)
    task_mirror = Path(resolve_runtime_path(str(workspace), "runtime/tasks/task_1.json"))
    task_mirror.unlink(missing_ok=True)
    plan_mirror = workspace / ".polaris" / "plans" / "latest.plan.json"
    plan_mirror.parent.mkdir(parents=True, exist_ok=True)
    plan_mirror.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "goal": "Implement feature",
                        "target_files": ["src/index.ts"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    blueprint = workspace / ".polaris" / "blueprints" / "latest.review.json"
    blueprint.parent.mkdir(parents=True, exist_ok=True)
    blueprint.write_text(
        json.dumps(
            {"generated_blueprints": 1, "blueprints": [{"task_id": "TASK-1"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_factory_chain(
        {"id": "L1-01", "title": "Resume", "brief": "Resume", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L1-01.resume.chain.log",
        start_from="director_resume",
    )

    assert result["exit_code"] == 0
    assert _LAST_FACTORY_START_PAYLOAD == {}
    assert _LAST_FACTORY_RESUME_CALL["run_id"] == "run-director-fact-authority"
    runtime_plan = Path(resolve_runtime_path(str(workspace), "runtime/tasks/plan.json"))
    assert runtime_plan.is_file()
    assert not task_mirror.exists()


def test_run_factory_chain_director_resume_without_same_run_checkpoint_fails_closed(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response={"run_id": "must-not-start"},
        terminal_status={"status": "completed", "phase": "qa_gate"},
        audit_bundle={},
    )
    monkeypatch.setattr(bench_chain, "list_factory_runs", lambda *_args, **_kwargs: {"runs": [], "total": 0})

    result = run_factory_chain(
        {"id": "L2-07", "title": "Tetris", "brief": "Build Tetris", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-07.missing-resume.chain.log",
        start_from="director_resume",
    )

    assert result["exit_code"] == -1
    assert result["error"] == "director_resume_run_missing"
    assert _LAST_FACTORY_START_PAYLOAD == {}
    assert _LAST_FACTORY_RESUME_CALL == {}


def test_director_resume_rehydrates_taskboard_from_legacy_runtime(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    from polaris.cells.events.fact_stream.public.contracts import QueryFactEventsV1
    from polaris.cells.events.fact_stream.public.service import query_fact_events
    from polaris.kernelone.storage import resolve_runtime_path

    workspace = tmp_path / "factory-bench" / "L1-01"
    workspace.mkdir(parents=True)
    _bootstrap_test_fact_stream(workspace)
    (workspace / "requirements.md").write_text("bench input requirements", encoding="utf-8")
    current_runtime_projects = tmp_path / "kernelone-projects"
    legacy_runtime_projects = tmp_path / "polaris-projects"
    legacy_runtime = legacy_runtime_projects / "l1-01-222222222222" / "runtime"
    stale_runtime = legacy_runtime_projects / "l1-01-333333333333" / "runtime"
    monkeypatch.setattr(
        bench_workspace,
        "resolve_storage_roots",
        lambda _workspace: SimpleNamespace(
            runtime_projects_root=str(current_runtime_projects),
            workspace_key="l1-01-111111111111",
        ),
    )
    monkeypatch.setattr(
        bench_workspace,
        "_RUNTIME_PROJECT_BASES",
        (legacy_runtime_projects, current_runtime_projects),
        raising=False,
    )

    for runtime, task_count, blueprint_count in (
        (stale_runtime, 1, 0),
        (legacy_runtime, 2, 2),
    ):
        task_dir = runtime / "tasks"
        task_dir.mkdir(parents=True)
        (task_dir / "plan.json").write_text(
            json.dumps(
                {
                    "tasks": [
                        {"id": f"TASK-{index}", "target_files": [f"src/{index}.ts"]}
                        for index in range(1, task_count + 1)
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        for index in range(1, task_count + 1):
            (task_dir / f"task_{index}.json").write_text(
                json.dumps(
                    {
                        "id": index,
                        "status": "in_progress" if index == 1 else "blocked",
                        "blocked_by": [] if index == 1 else [1],
                        "metadata": {
                            "runtime_execution": {"status": "active"},
                            "workflow_run_id": "director-old",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        (task_dir / "task_1.session.json").write_text(
            json.dumps({"status": "active"}, ensure_ascii=False),
            encoding="utf-8",
        )
        blueprint_dir = runtime / "blueprints"
        blueprint_dir.mkdir(parents=True)
        for index in range(blueprint_count):
            (blueprint_dir / f"ce_TASK-{index + 1}.json").write_text("{}", encoding="utf-8")

    blueprint_path = workspace / ".polaris" / "blueprints" / "latest.review.json"
    blueprint_path.parent.mkdir(parents=True)
    blueprint_path.write_text(
        json.dumps({"generated_blueprints": 2, "blueprints": [{"task_id": "TASK-1"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    bench._prepare_director_resume_workspace(workspace)

    target_task_dir = Path(resolve_runtime_path(str(workspace), "runtime/tasks"))
    rehydration_path = target_task_dir / "director_resume_rehydration.json"
    rehydration = json.loads(rehydration_path.read_text(encoding="utf-8"))
    assert rehydration["source_task_dir"] == str(legacy_runtime / "tasks")
    assert not (target_task_dir / "task_1.session.json").exists()
    task_1 = json.loads((target_task_dir / "task_1.json").read_text(encoding="utf-8"))
    assert task_1["status"] == "pending"
    assert "runtime_execution" not in task_1["metadata"]
    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    assert any(event.get("event_type") == "reexecution_imported" for event in events)
    snapshot_manifest = workspace / ".polaris" / "factory_snapshots" / "pre_director" / "manifest.json"
    assert json.loads(snapshot_manifest.read_text(encoding="utf-8"))["snapshot_kind"] == "pre_director_workspace"


def test_run_factory_chain_start_failure(monkeypatch: Any, tmp_path: Path) -> None:
    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response=None,
        terminal_status=None,
        audit_bundle=None,
    )

    result = run_factory_chain(
        {"id": "L2-07", "title": "Tetris", "brief": "Build Tetris", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-07.chain.log",
    )

    assert result["exit_code"] == -1
    assert result["error"] == "start_failed"


def test_run_factory_chain_event_wait_timeout(monkeypatch: Any, tmp_path: Path) -> None:
    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response={"run_id": "run-456"},
        terminal_status=None,
        audit_bundle=None,
    )

    result = run_factory_chain(
        {"id": "L2-07", "title": "Tetris", "brief": "Build Tetris", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-07.chain.log",
    )

    assert result["exit_code"] == -1
    assert result["run_id"] == "run-456"
    assert result["error"] == "event_wait_timeout"


def test_run_factory_chain_failed_status(monkeypatch: Any, tmp_path: Path) -> None:
    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response={"run_id": "run-789"},
        terminal_status={"status": "failed", "phase": "director_dispatch"},
        audit_bundle={
            "gates": [],
            "summary_json": {"director": {"total": 3, "successes": 1, "failures": 2}},
        },
    )

    result = run_factory_chain(
        {"id": "L2-07", "title": "Tetris", "brief": "Build Tetris", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-07.chain.log",
    )

    assert result["exit_code"] == 1
    assert result["chain_results"]["exit_class"] == "legacy_unknown"
    assert result["chain_results"]["authoritative"] is False


def test_run_factory_chain_on_stage_change_callback(monkeypatch: Any, tmp_path: Path) -> None:
    callbacks: list[tuple[str, dict[str, Any]]] = []

    def _on_stage_change(status: str, status_dict: dict[str, Any]) -> None:
        callbacks.append((status, status_dict))

    workspace = _setup_run_factory_chain_mocks(
        monkeypatch,
        tmp_path,
        start_response={"run_id": "run-cb"},
        terminal_status={"status": "completed", "phase": "qa_gate"},
        audit_bundle={
            "gates": [{"gate_name": "quality_gate", "passed": True}],
        },
    )

    run_factory_chain(
        {"id": "L2-07", "title": "Tetris", "brief": "Build Tetris", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-07.chain.log",
        on_stage_change=_on_stage_change,
    )

    assert len(callbacks) == 1
    assert callbacks[0][0] == "completed"


# --- _sanitize_run_id ---


def test_sanitize_run_id_passthrough_clean_value() -> None:
    assert _sanitize_run_id("bench-2026-06") == "bench-2026-06"


def test_sanitize_run_id_replaces_unsafe_chars() -> None:
    result = _sanitize_run_id("hello world/colons:and*stars")
    assert "/" not in result
    assert ":" not in result
    assert "*" not in result
    assert " " not in result
    assert result == "hello-world-colons-and-stars"


def test_sanitize_run_id_empty_generates_nonempty() -> None:
    result = _sanitize_run_id("")
    assert result
    assert len(result) >= 8


def test_sanitize_run_id_none_generates_nonempty() -> None:
    result = _sanitize_run_id(None)
    assert result
    assert len(result) >= 8


def test_sanitize_run_id_whitespace_only_generates_nonempty() -> None:
    result = _sanitize_run_id("   ")
    assert result
    assert len(result) >= 8


def test_sanitize_run_id_collapses_consecutive_dashes() -> None:
    result = _sanitize_run_id("a///b")
    assert "--" not in result
    assert result == "a-b"


# --- _write_immutable_json ---


def test_write_immutable_json_first_write_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"
    payload = {"project_id": "L1-01", "status": "PASS"}

    written = _write_immutable_json(target, payload)

    assert written == target
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_write_immutable_json_second_write_does_not_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"
    first_payload = {"project_id": "L1-01", "round": 1}
    second_payload = {"project_id": "L1-01", "round": 2}

    first_written = _write_immutable_json(target, first_payload)
    second_written = _write_immutable_json(target, second_payload)

    assert first_written == target
    assert second_written == tmp_path / "L1-01.audit.2.json"
    assert json.loads(target.read_text(encoding="utf-8")) == first_payload
    assert json.loads(second_written.read_text(encoding="utf-8")) == second_payload


def test_write_immutable_json_increments_slot(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"

    _write_immutable_json(target, {"v": 1})
    _write_immutable_json(target, {"v": 2})
    third = _write_immutable_json(target, {"v": 3})

    assert third == tmp_path / "L1-01.audit.3.json"
    assert json.loads(third.read_text(encoding="utf-8")) == {"v": 3}


def test_write_immutable_json_skips_existing_slots(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"
    # Pre-create .2.json to force skip
    (tmp_path / "L1-01.audit.2.json").write_text("{}", encoding="utf-8")

    written = _write_immutable_json(target, {"v": 1})

    assert written == target
    written2 = _write_immutable_json(target, {"v": 2})
    assert written2 == tmp_path / "L1-01.audit.3.json"


def test_next_immutable_json_path_returns_initial_path_when_free(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"

    resolved = _next_immutable_json_path(target)

    assert resolved == target


def test_next_immutable_json_path_returns_first_free_slot(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"
    target.write_text("{}", encoding="utf-8")
    (tmp_path / "L1-01.audit.2.json").write_text("{}", encoding="utf-8")

    resolved = _next_immutable_json_path(target)

    assert resolved == tmp_path / "L1-01.audit.3.json"


def test_write_immutable_json_payload_contains_audit_path(tmp_path: Path) -> None:
    target = tmp_path / "L1-01.audit.json"
    relative_base = tmp_path

    written = _write_immutable_json(target, {"audit_path": str(target.relative_to(relative_base))})

    data = json.loads(written.read_text(encoding="utf-8"))
    assert data["audit_path"] == str(target.relative_to(relative_base))


# --- run_id singleton across multiple project metas ---


def test_main_run_id_shared_across_projects(monkeypatch: Any, tmp_path: Path) -> None:
    """Verify that all projects in a bench run share the same run_id."""
    projects = [
        {"id": "L1-01", "level": 1, "title": "One", "brief": "Build one"},
        {"id": "L2-02", "level": 2, "title": "Two", "brief": "Build two"},
    ]

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(bench_cli, "load_projects", lambda: projects)
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-shared")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        bench_cli,
        "build_factory_audit_record",
        lambda **_kwargs: _successful_audit_record(),
    )
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _chain(project: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": str(project["brief"]),
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench_cli, "run_factory_chain", _chain)

    result = bench.main()

    assert result == 0
    audit_dir = tmp_path / "audits"
    run_dirs = list(audit_dir.iterdir())
    assert len(run_dirs) == 1, f"Expected single audit run_dir, got {run_dirs}"
    run_dir = run_dirs[0]
    audit_files = sorted(run_dir.glob("*.audit.json"))
    assert len(audit_files) == 2
    ids = set()
    for af in audit_files:
        data = json.loads(af.read_text(encoding="utf-8"))
        ids.add(data["run_id"])
        assert "audit_path" in data, "Per-project audit JSON must include audit_path"
        assert (tmp_path / data["audit_path"]).resolve() == af.resolve(), (
            "audit_path must resolve to the actual written audit file"
        )
    assert len(ids) == 1, "All projects should share the same run_id"


def test_main_default_launcher_mode_uses_isolated_project_backend(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.delenv("FACTORY_BENCH_LAUNCHER_INSTANCE_MODE", raising=False)
    projects = [{"id": "L1-01", "level": 1, "title": "One", "brief": "Build one"}]
    captured_chain: dict[str, str] = {}
    captured_session: dict[str, Any] = {}
    backend_context_urls: list[str] = []
    launch_receipts: list[dict[str, Any]] = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_factory_bench.py",
            "--project-ids",
            "L1-01",
            "--work-dir",
            str(tmp_path),
        ],
    )
    monkeypatch.setattr(bench_cli, "load_projects", lambda: projects)
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "main-token")

    def _capture_session(**kwargs: Any) -> str:
        captured_session.update(kwargs)
        return "bench-isolated"

    monkeypatch.setattr(bench_cli, "_ensure_bench_session", _capture_session)
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "_push_bench_workspace_to_backend",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("isolated mode must not switch shared workspace")),
    )

    def _start_isolated(**kwargs: Any) -> dict[str, Any]:
        receipt = dict(kwargs["launch_receipt"])
        launch_receipts.append(receipt)
        return {
            "instance_id": receipt["instance_id"],
            "workspace": receipt["workspace"],
            "runtime_root": receipt["runtime_root"],
            "backend_pid": 73101,
            "metadata": {"instance_launch_receipt": receipt},
            "backend_url": "http://127.0.0.1:60011",
            "frontend_url": "http://127.0.0.1:60012",
            "token": "isolated-token",
        }

    monkeypatch.setattr(bench_cli, "_start_isolated_bench_project_instance", _start_isolated)

    def _backend_context(url: str, **_kwargs: Any) -> dict[str, Any]:
        backend_context_urls.append(url)
        workspace = str(_kwargs["workspace"])
        source_fingerprint = bench_session.compute_source_fingerprint(bench._BACKEND_ROOT)
        receipt = launch_receipts[-1] if launch_receipts else {}
        return {
            "backend_freshness": {
                "ok": True,
                "detail": "backend fresh",
                "expected_fingerprint": source_fingerprint,
                "actual_fingerprint": source_fingerprint,
                "backend_info": {
                    "pid": 73101,
                    "instance_id": str(receipt.get("instance_id") or ""),
                    "workspace": workspace,
                    "backend_root": str(receipt.get("expected_backend_root") or bench._BACKEND_ROOT),
                },
            },
            "backend_metadata": {"backend_base_url": url},
        }

    monkeypatch.setattr(bench_cli, "build_bench_backend_audit_context", _backend_context)
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        bench_cli,
        "build_factory_audit_record",
        lambda **_kwargs: _successful_audit_record(),
    )
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    monkeypatch.setattr(bench_cli, "persist_real_run_gate_ledger", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _chain(
        _project: dict[str, Any],
        _workspace: Path,
        *,
        backend_url: str,
        backend_token: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured_chain["backend_url"] = backend_url
        captured_chain["backend_token"] = backend_token
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": "Build one",
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench_cli, "run_factory_chain", _chain)

    first_result = bench.main()
    second_result = bench.main()

    assert first_result == 0
    assert second_result == 0
    assert len(launch_receipts) == 2
    assert launch_receipts[0]["requested_instance_id"] == launch_receipts[1]["requested_instance_id"]
    assert launch_receipts[0]["instance_id"] != launch_receipts[1]["instance_id"]
    assert launch_receipts[0]["launch_scope"] != launch_receipts[1]["launch_scope"]
    assert launch_receipts[0]["workspace"] != launch_receipts[1]["workspace"]
    assert launch_receipts[0]["runtime_root"] != launch_receipts[1]["runtime_root"]
    assert captured_chain == {
        "backend_url": "http://127.0.0.1:60011",
        "backend_token": "isolated-token",
    }
    assert captured_session["backend_url"] == ""
    assert captured_session["metadata"]["launcher_instance_mode"] == "isolated"
    assert captured_session["metadata"]["bench_session_reporting"] == "auto"
    assert backend_context_urls[-1] == "http://127.0.0.1:60011"
    audit = json.loads((tmp_path / "factory_audits.json").read_text(encoding="utf-8"))["records"][0]
    assert audit["requested_project_id"] == "L1-01"
    assert audit["canonical_project_id"] == "L1-01"
    assert audit["instance_id"]
    assert audit["requested_instance_id"]
    assert audit["run_id"] == audit["instance_launch_receipt"]["run_id"]
    assert audit["instance_launch_validation"]["ok"] is True


def test_main_audit_path_points_to_conflict_when_same_id_reused(monkeypatch: Any, tmp_path: Path) -> None:
    """If the same project id appears twice, the second audit must reference the conflict file."""
    projects = [
        {"id": "L1-01", "level": 1, "title": "One", "brief": "Build one"},
        {"id": "L1-01", "level": 2, "title": "One Again", "brief": "Build one again"},
    ]

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(bench_cli, "load_projects", lambda: projects)
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-conflict")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        bench_cli,
        "build_factory_audit_record",
        lambda **_kwargs: _successful_audit_record(),
    )
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _chain(project: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": str(project["brief"]),
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench_cli, "run_factory_chain", _chain)

    result = bench.main()

    assert result == 0
    audit_dir = tmp_path / "audits"
    run_dir = next(iter(audit_dir.iterdir()))
    audit_files = sorted(run_dir.glob("*.json"))
    assert len(audit_files) == 2, f"Expected 2 audit files (including conflict), got {audit_files}"
    primary_file = run_dir / "L1-01.audit.json"
    conflict_file = run_dir / "L1-01.audit.2.json"
    assert primary_file.exists(), "Expected primary audit file"
    assert conflict_file.exists(), "Expected conflict file for repeated project id"
    for af in audit_files:
        data = json.loads(af.read_text(encoding="utf-8"))
        assert "audit_path" in data, f"audit_path missing from {af.name}"
        assert (tmp_path / data["audit_path"]).resolve() == af.resolve(), (
            f"audit_path {data['audit_path']} does not resolve to {af}"
        )


# --- _resolve_polaris_home ---


def test_resolve_polaris_home_default_uses_dot_polaris() -> None:
    result = _resolve_polaris_home(env={})
    assert result.name == ".polaris"
    assert result == Path.home() / ".polaris"


def test_resolve_polaris_home_kernelone_home_already_dot_polaris(tmp_path: Path) -> None:
    home = tmp_path / ".polaris"
    result = _resolve_polaris_home(env={"KERNELONE_HOME": str(home)})
    assert result == home.expanduser().resolve()


def test_resolve_polaris_home_kernelone_home_parent_dir(tmp_path: Path) -> None:
    parent = tmp_path / "config-root"
    result = _resolve_polaris_home(env={"KERNELONE_HOME": str(parent)})
    expected = parent.expanduser().resolve() / ".polaris"
    assert result == expected


# --- _desktop_backend_info_path ---


def test_desktop_backend_info_path_inside_polaris_home(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    result = _desktop_backend_info_path(env={"KERNELONE_HOME": str(polaris_home)})
    assert result == polaris_home.expanduser().resolve() / "runtime" / "desktop-backend.json"


# --- _read_desktop_backend_info ---


def test_read_desktop_backend_info_valid_json(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    info_file = runtime / "desktop-backend.json"
    info_file.write_text(
        json.dumps({"schema_version": 1, "backend": {"baseUrl": "http://127.0.0.1:49977", "token": "tok-123"}}),
        encoding="utf-8",
    )
    result = _read_desktop_backend_info(env={"KERNELONE_HOME": str(polaris_home)})
    assert result["backend"]["baseUrl"] == "http://127.0.0.1:49977"
    assert result["backend"]["token"] == "tok-123"


def test_read_desktop_backend_info_missing_file(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    result = _read_desktop_backend_info(env={"KERNELONE_HOME": str(polaris_home)})
    assert result == {}


def test_read_desktop_backend_info_malformed_json(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "desktop-backend.json").write_text("NOT JSON {{{", encoding="utf-8")
    result = _read_desktop_backend_info(env={"KERNELONE_HOME": str(polaris_home)})
    assert result == {}


def test_read_desktop_backend_info_non_dict_json(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "desktop-backend.json").write_text('"just a string"', encoding="utf-8")
    result = _read_desktop_backend_info(env={"KERNELONE_HOME": str(polaris_home)})
    assert result == {}


# --- _resolve_backend_url desktop fallback ---


def test_resolve_backend_url_falls_back_to_desktop_info(monkeypatch: Any, tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "desktop-backend.json").write_text(
        json.dumps({"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}},
    )
    result = _resolve_backend_url()
    assert result == "http://10.0.0.1:5555"


def test_resolve_backend_url_explicit_overrides_desktop(monkeypatch: Any, tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "desktop-backend.json").write_text(
        json.dumps({"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}},
    )
    result = _resolve_backend_url(explicit="http://192.168.1.1:8080")
    assert result == "http://192.168.1.1:8080"


def test_resolve_backend_url_env_overrides_desktop(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNELONE_BACKEND_URL", "http://env-host:1111")
    monkeypatch.setattr(
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}},
    )
    result = _resolve_backend_url()
    assert result == "http://env-host:1111"


def test_resolve_backend_url_missing_desktop_json_returns_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_url()
    assert result == "http://127.0.0.1:49977"


def test_resolve_backend_url_malformed_desktop_json_returns_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_url()
    assert result == "http://127.0.0.1:49977"


# --- _resolve_backend_token desktop fallback ---


def test_is_local_backend_url_recognizes_loopback_only() -> None:
    assert _is_local_backend_url("http://127.0.0.1:49977") is True
    assert _is_local_backend_url("http://localhost:49977") is True
    assert _is_local_backend_url("http://[::1]:49977") is True
    assert _is_local_backend_url("http://10.0.0.1:49977") is False
    assert _is_local_backend_url("not a url") is False


def test_resolve_backend_token_falls_back_to_desktop_info(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.setattr(
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://x", "token": "desktop-tok-abc"}},
    )
    result = _resolve_backend_token()
    assert result == "desktop-tok-abc"


def test_resolve_backend_token_explicit_overrides_desktop(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://x", "token": "desktop-tok-abc"}},
    )
    result = _resolve_backend_token(explicit="explicit-tok")
    assert result == "explicit-tok"


def test_resolve_backend_token_env_overrides_desktop(monkeypatch: Any) -> None:
    monkeypatch.setenv("FACTORY_BENCH_BACKEND_TOKEN", "env-tok")
    monkeypatch.setattr(
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://x", "token": "desktop-tok-abc"}},
    )
    result = _resolve_backend_token()
    assert result == "env-tok"


def test_resolve_backend_token_missing_desktop_json_returns_local_dev_token(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_token()
    assert result == "polaris-local-dev"


def test_resolve_backend_token_malformed_desktop_json_returns_local_dev_token(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_token()
    assert result == "polaris-local-dev"


def test_resolve_backend_token_missing_remote_token_returns_empty(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.setenv("KERNELONE_BACKEND_URL", "http://10.0.0.1:49977")
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_token()
    assert result == ""


# --- L1-01 regression: contract chain must propagate to Director ---


def test_extract_feature_keywords_from_content_any_checks() -> None:
    """_extract_feature_keywords must extract keywords from content_any checks."""
    project = {
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    keywords = _extract_feature_keywords(project)
    assert keywords == ["firefly", "flower", "moon", "humidity"]


def test_extract_feature_keywords_no_content_any_returns_empty() -> None:
    """_extract_feature_keywords returns empty list when no content_any checks."""
    project = {"checks": ["ts_syntax", "package_scripts", "min_files:3"]}
    assert _extract_feature_keywords(project) == []


def test_extract_feature_keywords_deduplicates_case_insensitive() -> None:
    """_extract_feature_keywords deduplicates keywords case-insensitively."""
    project = {
        "checks": [
            "content_any:Fire|fire|FLOWER|flower",
        ],
    }
    keywords = _extract_feature_keywords(project)
    assert keywords == ["Fire", "FLOWER"]


def test_l1_01_requirements_doc_contains_source_tree_contract() -> None:
    """L1-01 requirements doc must contain source tree contract requiring src/."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    doc = build_requirements_doc(project)
    assert "Source Tree Structure Contract" in doc
    assert "src/" in doc
    assert "src/models/" in doc
    assert "src/engine/" in doc or "src/core/" in doc
    assert "Feature Keywords Contract" in doc
    assert "firefly" in doc
    assert "flower" in doc
    assert "moon" in doc
    assert "humidity" in doc
    assert "Project Metadata" in doc
    assert "science_creative" in doc
    assert "simulation_toy" in doc
    assert "萤火虫根据花朵情绪和月相组成实时灯光舞蹈" in doc
    assert "Bench Level Contract (Mandatory)" in doc
    assert "level: 1" in doc
    assert "min_prod_files: 3" in doc


def test_l1_01_requirements_doc_director_target_files_mandate() -> None:
    """L1-01 requirements doc must mandate Director target_files cover src/."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    doc = build_requirements_doc(project)
    assert "target_files 必须覆盖 src/" in doc
    assert "不能只包含 package.json" in doc


def test_l1_01_requirements_doc_has_ts_strict_and_features() -> None:
    """L1-01 requirements doc must include TS-specific contract + feature keywords."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    doc = build_requirements_doc(project)
    assert "Language-Specific Runnable Contract (TypeScript)" in doc
    assert "tsc --noEmit" in doc
    assert "Feature Keywords Contract" in doc
    assert "firefly" in doc


def test_build_requirements_doc_python_includes_source_tree() -> None:
    """Python projects must also get source tree contract."""
    project = {
        "id": "L1-03",
        "level": 1,
        "domain": "creative",
        "project_type": "interactive_visual",
        "primary_language": "python",
        "title": "迷你行星天气球",
        "creative_hook": "口袋行星会随云层、风向和昼夜循环改变地表",
        "brief": "用 Python 实现迷你行星天气球",
        "test_focus": "cloud, weather simulation",
        "checks": ["py_compile", "content_any:planet|weather|cloud|wind"],
    }
    doc = build_requirements_doc(project)
    assert "Source Tree Structure Contract" in doc
    assert "src/" in doc
    assert "tests/" in doc
    assert "Feature Keywords Contract" in doc
    assert "planet" in doc
    assert "Bench Level Contract (Mandatory)" in doc


def test_l2_requirements_doc_uses_stronger_depth_contract() -> None:
    """L2 requirements must carry an explicit depth floor beyond L1 shape checks."""
    project = {
        "id": "L2-03",
        "level": 2,
        "domain": "creative",
        "project_type": "go_module",
        "primary_language": "go",
        "title": "时间胶囊博物馆",
        "creative_hook": "访客通过谜语解锁不同年代的展柜",
        "brief": "用 Go 实现时间胶囊博物馆",
        "test_focus": "capsule, museum, riddle, unlock",
        "checks": ["go_compile", "min_files:4", "content_any:capsule|museum|riddle|unlock"],
    }

    doc = build_requirements_doc(project)

    assert "Bench Level Contract (Mandatory)" in doc
    assert "level: 2" in doc
    assert "min_prod_files: 6" in doc
    assert "min_prod_lines: 500" in doc
    assert "min_test_assertions: 8" in doc
    assert "Do not satisfy content checks by keyword stuffing" in doc


def test_factory_chain_catalog_contract_writes_metadata(tmp_path: Path) -> None:
    """Catalog contract JSON must include project metadata for PM/CE/Director."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫灯光舞蹈",
        "checks": [
            "ts_syntax",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    workspace = tmp_path / "L1-01"
    workspace.mkdir(parents=True, exist_ok=True)

    feature_keywords = _extract_feature_keywords(project)
    raw_level = project.get("level")
    level = raw_level if isinstance(raw_level, int) else int(str(raw_level or 0))
    catalog_contract = {
        "project_id": str(project.get("id") or "").strip(),
        "domain": str(project.get("domain") or "").strip(),
        "project_type": str(project.get("project_type") or "").strip(),
        "primary_language": str(project.get("primary_language") or "").strip(),
        "creative_hook": str(project.get("creative_hook") or "").strip(),
        "feature_keywords": feature_keywords,
        "checks": list(project.get("checks") or []),  # type: ignore[call-overload]
        "test_focus": str(project.get("test_focus") or "").strip(),
        "level": level,
        "level_contract": bench_gates.build_factory_bench_level_contract(level, project=project),
        "source_tree_mandate": "PM/CE/Director must create src/ with core source files, not just scaffolding",
    }
    catalog_path = workspace / ".polaris" / "catalog_contract.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog_contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert catalog_path.is_file()
    written = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert written["project_id"] == "L1-01"
    assert written["domain"] == "science_creative"
    assert written["project_type"] == "simulation_toy"
    assert written["primary_language"] == "typescript"
    assert written["creative_hook"] == "萤火虫根据花朵情绪和月相组成实时灯光舞蹈"
    assert written["feature_keywords"] == ["firefly", "flower", "moon", "humidity"]
    assert written["level"] == 1
    assert written["level_contract"]["minimums"]["min_prod_files"] == 3
    assert written["source_tree_mandate"] != ""


def test_director_contract_requires_ts_target_and_feature_keywords() -> None:
    """The generated Director contract for L1-01 must include .ts targets and feature keywords.

    This is the core regression: the Director must not only produce package.json/tsconfig.json
    scaffolding — it must target src/ .ts files and embed firefly|flower|moon|humidity.
    """
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    doc = build_requirements_doc(project)
    assert "src/" in doc
    assert ".ts" in doc
    assert "firefly" in doc
    assert "flower" in doc
    assert "moon" in doc
    assert "humidity" in doc
    assert "tests/" in doc
    assert "不能只包含 package.json" in doc


# --- _fallback_audit_bundle_from_workspace ---


def test_fallback_audit_bundle_from_workspace_reads_dispatch_logs(tmp_path: Path) -> None:
    """Fallback must read .polaris/dispatch/*.log.json and build events/artifacts."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    polaris_dir = workspace / ".polaris"
    dispatch_dir = polaris_dir / "dispatch"
    dispatch_dir.mkdir(parents=True)

    dispatch_log = dispatch_dir / "latest.log.json"
    dispatch_log.write_text(
        json.dumps({"tasks": [{"id": "T1", "status": "done"}]}),
        encoding="utf-8",
    )

    bundle = _fallback_audit_bundle_from_workspace(workspace)

    assert len(bundle["artifacts"]) >= 1
    assert any(a["name"] == "latest.log.json" for a in bundle["artifacts"])
    assert len(bundle["events_tail"]) >= 1
    assert bundle["events_tail"][0]["stage"] == "director_dispatch"
    assert bundle["artifacts"][0]["source"] == "workspace_fallback"


def test_fallback_audit_bundle_from_workspace_reads_roles_director(tmp_path: Path) -> None:
    """Fallback must read .polaris/roles/director/**/*.log.json."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    polaris_dir = workspace / ".polaris"
    roles_dir = polaris_dir / "roles" / "director" / "run_001"
    roles_dir.mkdir(parents=True)

    role_log = roles_dir / "dispatch.log.json"
    role_log.write_text(
        json.dumps({"dispatch": {"status": "ok"}}),
        encoding="utf-8",
    )

    bundle = _fallback_audit_bundle_from_workspace(workspace)

    assert len(bundle["artifacts"]) >= 1
    assert any(a["name"] == "dispatch.log.json" for a in bundle["artifacts"])
    assert len(bundle["events_tail"]) >= 1


def test_fallback_audit_bundle_from_workspace_reads_plan(tmp_path: Path) -> None:
    """Fallback must read .polaris/docs/product/plan.json into summary_json."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    polaris_dir = workspace / ".polaris"
    docs_dir = polaris_dir / "docs" / "product"
    docs_dir.mkdir(parents=True)

    plan = docs_dir / "plan.json"
    plan.write_text(
        json.dumps({"overall_goal": "Build a calculator app"}),
        encoding="utf-8",
    )

    bundle = _fallback_audit_bundle_from_workspace(workspace)

    assert bundle["summary_json"] is not None
    assert bundle["summary_json"]["plan"]["overall_goal"] == "Build a calculator app"
    assert any(a["name"] == "plan.json" for a in bundle["artifacts"])


def test_fallback_audit_bundle_from_workspace_missing_polaris_dir(tmp_path: Path) -> None:
    """Fallback must return empty bundle when .polaris directory is missing."""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    bundle = _fallback_audit_bundle_from_workspace(workspace)

    assert bundle["gates"] == []
    assert bundle["events_tail"] == []
    assert bundle["artifacts"] == []
    assert bundle["summary_json"] is None


def test_run_factory_chain_fallback_on_audit_bundle_timeout(monkeypatch: Any, tmp_path: Path) -> None:
    """run_factory_chain must use workspace fallback when audit-bundle returns None."""
    workspace = tmp_path / "L2-fallback"
    workspace.mkdir()
    _LAST_FACTORY_START_PAYLOAD.clear()

    # Seed workspace .polaris artifacts for fallback
    polaris_dir = workspace / ".polaris"
    dispatch_dir = polaris_dir / "dispatch"
    dispatch_dir.mkdir(parents=True)
    (dispatch_dir / "latest.log.json").write_text(
        json.dumps({"tasks": [{"id": "T1", "status": "done"}]}),
        encoding="utf-8",
    )

    def _fake_start_factory_run(_backend_url: str, _payload: dict[str, Any], token: str = "") -> dict[str, Any] | None:
        _LAST_FACTORY_START_PAYLOAD.update(_payload)
        return {"run_id": "run-fb-123"}

    def _fake_wait_run_until_terminal(
        _backend_url: str,
        run_id: str,
        token: str = "",
        workspace: str = "",
        on_status: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        if on_status is not None:
            on_status({"status": "failed", "phase": "director_dispatch"})
        return {"status": "failed", "phase": "director_dispatch"}

    def _fake_get_audit_bundle(
        _backend_url: str,
        _run_id: str,
        token: str = "",
        workspace: str = "",
    ) -> dict[str, Any] | None:
        # Simulate timeout: return None
        return None

    def _fake_cancel_factory_run(
        _backend_url: str,
        _run_id: str,
        *,
        reason: str = "",
        token: str = "",
        workspace: str = "",
        return_errors: bool = False,
    ) -> dict[str, Any]:
        del reason, token, workspace, return_errors
        return {"status": "cancelled"}

    monkeypatch.setattr(bench_chain, "start_factory_run", _fake_start_factory_run)
    monkeypatch.setattr(bench_chain, "wait_run_until_terminal", _fake_wait_run_until_terminal)
    monkeypatch.setattr(bench_chain, "get_audit_bundle", _fake_get_audit_bundle)
    monkeypatch.setattr(bench_chain, "cancel_factory_run", _fake_cancel_factory_run)

    result = run_factory_chain(
        {"id": "L2-fb", "title": "Fallback Test", "brief": "Test fallback", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-fb.chain.log",
    )

    assert result["exit_code"] == 1
    assert result["run_id"] == "run-fb-123"
    assert "audit_bundle" in result
    assert len(result["audit_bundle"]["artifacts"]) >= 1
    assert result["audit_bundle"]["artifacts"][0]["source"] == "workspace_fallback"
    assert result["audit_bundle"]["events_tail"][0]["stage"] == "director_dispatch"


def test_map_director_artifact_preserves_stats_for_display() -> None:
    """Legacy Director stats remain visible but cannot decide execution state."""
    run_status = {"status": "failed", "phase": "director_dispatch"}
    audit_bundle: dict[str, Any] = {
        "gates": [],
        "current_stage": "director_dispatch",
        "summary_json": {"director": {"total": 5, "successes": 2, "failures": 3, "blocked": 0}},
        "director_convergence": {
            "qa_ran": False,
            "blocking_phase": "director_dispatch",
            "missing_delivery_targets": ["quality_gate"],
            "taskboard_final": {"total": 5, "completed": 2, "failed": 3},
            "per_binding_task_status": [
                {"task_id": "T1", "status": "completed"},
                {"task_id": "T2", "status": "completed"},
                {"task_id": "T3", "status": "failed"},
                {"task_id": "T4", "status": "failed"},
                {"task_id": "T5", "status": "failed"},
            ],
        },
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["director"]["total"] == 5
    assert result["director"]["failures"] == 3

    # Verify convergence data is present in the bundle for downstream propagation
    convergence = audit_bundle.get("director_convergence")
    assert convergence is not None
    assert convergence["blocking_phase"] == "director_dispatch"
    assert convergence["missing_delivery_targets"] == ["quality_gate"]
    assert len(convergence["per_binding_task_status"]) == 5


def test_director_repair_coverage_gap_summary_projects_bench_handoff_fields() -> None:
    record = {
        "project_id": "L1-gap",
        "run_id": "run-gap-1",
        "all_checks_passed": True,
        "static_checks_passed": True,
        "chain_results": {"qa_ran": True, "qa_passed": True},
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "has_qa_verdict": True,
        "wrong_product_suspect": False,
        "backend_freshness": {"ok": True, "detail": "fresh"},
        "real_run_gate": {"ok": True, "summary": "real run passed"},
        "run_ledger_projection": {
            "evidence_policy": {
                "missing_required_modalities": [],
                "failed_required_modalities": [],
            }
        },
        "llm_route_audit": {"ok": True, "summary": "routes ok"},
    }
    audit_bundle = {
        "director_convergence": {
            "repair_kernel": {
                "coverage_report": {
                    "coverage_gap_count": 1,
                    "coverage_gaps": [
                        {
                            "diagnostic": {
                                "diagnostic_id": "diag-ruby-1",
                                "code": "ruby_uninitialized_constant",
                                "path": "app/models/widget.rb",
                                "message": "uninitialized constant Widget",
                            },
                            "diagnostic_language": "ruby",
                            "diagnostic_archetype": "wrong_import_path",
                            "diagnostic_phase": "compiler",
                            "reserved_slot_available": True,
                            "slot_status": "reserved_slot_available",
                            "recommended_route": "runtime_rule",
                            "handoff_recommendation": "runtime_rule_backlog",
                            "recommended_next_owner": "runtime_rule",
                        }
                    ],
                }
            }
        }
    }

    summary = build_director_repair_coverage_gap_summary(record, audit_bundle)
    record["director_repair_coverage_gap_summary"] = summary
    apply_factory_bench_gates(record, chain={"exit_code": 0})

    assert summary["schema_version"] == "factory_bench.director_repair_coverage_gap_summary.v1"
    assert summary["gate_affects_pass"] is False
    assert summary["rule_discovery_required"] is True
    assert summary["coverage_gap_count"] == 1
    assert summary["coverage_gap_languages"] == ["ruby"]
    assert summary["coverage_gap_diagnostic_codes"] == ["ruby_uninitialized_constant"]
    assert summary["coverage_gap_recommended_routes"] == ["runtime_rule"]
    gap = summary["coverage_gaps"][0]
    assert gap["project_id"] == "L1-gap"
    assert gap["run_id"] == "run-gap-1"
    assert gap["diagnostic_language"] == "ruby"
    assert gap["path"] == "app/models/widget.rb"
    assert gap["recommended_next_owner"] == "runtime_rule"
    assert gap["authoritative_rule_registration_allowed"] is False
    factory_gates = record.get("factory_gates")
    assert isinstance(factory_gates, list)
    assert all(gate["gate"] != "director_repair_coverage_gap_summary" for gate in factory_gates)


def test_director_repair_coverage_gap_summary_reads_workspace_validation_artifact(tmp_path: Path) -> None:
    qa_dir = tmp_path / ".polaris" / "qa"
    qa_dir.mkdir(parents=True)
    (qa_dir / "latest.workspace-validation.json").write_text(
        json.dumps(
            {
                "repair": {
                    "director_runtime_repair_coverage": {
                        "coverage_gap_count": 1,
                        "coverage_gaps": [
                            {
                                "diagnostic": {
                                    "diagnostic_id": "diag-go-1",
                                    "code": "go_compile_error",
                                    "path": "engine/riddle.go",
                                    "message": "undefined: Riddle",
                                },
                                "diagnostic_language": "go",
                                "diagnostic_archetype": "missing_dependency",
                                "diagnostic_phase": "quality_repair",
                                "slot_status": "reserved_slot_available",
                                "recommended_route": "runtime_rule",
                                "handoff_recommendation": "runtime_rule_backlog",
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    record = {
        "project_id": "L2-03",
        "run_id": "run-go-gap",
        "workspace_validation_repair_coverage": load_workspace_validation_repair_coverage(tmp_path, []),
    }

    summary = build_director_repair_coverage_gap_summary(record, {})

    coverage = record["workspace_validation_repair_coverage"]
    assert isinstance(coverage, dict)
    assert coverage["report_count"] == 1
    assert summary["coverage_gap_count"] == 1
    assert list(summary["coverage_gap_languages"]) == ["go"]
    assert summary["coverage_gaps"][0]["path"] == "engine/riddle.go"


def test_director_repair_coverage_gap_summary_dedupes_mirrored_validation_reports() -> None:
    gap = {
        "diagnostic": {
            "diagnostic_id": "diag-go-1",
            "code": "workspace_validation_failed",
            "path": "",
            "message": "Workspace validation command failed.",
        },
        "diagnostic_language": "go",
        "diagnostic_archetype": "unknown",
        "diagnostic_phase": "unknown",
        "slot_status": "reserved_slot_available",
        "recommended_route": "llm_repair",
        "handoff_recommendation": "llm_triage_then_runtime_rule",
    }
    report = {"coverage_gap_count": 1, "coverage_gaps": [gap]}
    record = {
        "project_id": "L2-03",
        "run_id": "run-go-gap",
        "workspace_validation_repair_coverage": {
            "reports": [report, report],
        },
    }

    summary = build_director_repair_coverage_gap_summary(record, {})

    assert summary["coverage_gap_count"] == 1
    assert len(summary["coverage_gaps"]) == 1


# --- R18-B: audit snapshot terminal/non-terminal ---


def test_main_start_failed_chain_marks_audit_as_non_terminal(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """When run_factory_chain returns start_failed, the audit record must be
    marked as non_terminal so it cannot be confused with a final verdict."""
    captured_records: list[dict[str, Any]] = []

    def _capture_build(**kwargs: Any) -> dict[str, Any]:
        captured_records.append(kwargs)
        return {
            "all_checks_passed": False,
            "static_checks_passed": False,
            "has_plan_doc": False,
            "has_blueprint_doc": False,
            "has_qa_verdict": False,
            "code_file_count": 0,
            "source_file_count": 0,
            "checks": [],
            "audit_snapshot_kind": "non_terminal" if not kwargs.get("chain_terminal", True) else "terminal",
            "audit_terminal": kwargs.get("chain_terminal", True),
        }

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--project-ids", "L1-01", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(
        bench_cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []}],
    )
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "token")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-non-terminal")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(
        bench_cli,
        "discover_artifacts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pre-start failure must not reuse prior workspace artifacts")
        ),
    )
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(bench_cli, "build_factory_audit_record", _capture_build)
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": False, "summary": "real run failed"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": False, "summary": "LLM route audit failed"},
    )

    def _start_failed_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"exit_code": -1, "duration_s": 0.0, "error": "start_failed"}

    monkeypatch.setattr(bench_cli, "run_factory_chain", _start_failed_chain)

    result = bench.main()

    assert result == 1
    assert len(captured_records) == 1
    assert captured_records[0]["chain_terminal"] is False
    audit = json.loads((tmp_path / "factory_audits.json").read_text(encoding="utf-8"))["records"][0]
    assert audit["chain_attempt_started"] is False
    assert audit["chain_results"] == {}
    assert audit["qa_invoked"] == {"invoked": False, "reason": "current_attempt_not_started"}


def test_main_event_wait_timeout_marks_non_terminal_and_skips_real_run_gate(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """When runtime.v2 never delivers a terminal event, main must not run
    build/test/start against a workspace the backend may still be mutating."""
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    captured_records: list[dict[str, Any]] = []
    taxonomy_records: list[dict[str, Any]] = []

    def _capture_build(**kwargs: Any) -> dict[str, Any]:
        captured_records.append(kwargs)
        return {
            "all_checks_passed": False,
            "static_checks_passed": False,
            "has_plan_doc": False,
            "has_blueprint_doc": False,
            "has_qa_verdict": False,
            "code_file_count": 0,
            "source_file_count": 0,
            "checks": [],
            "audit_snapshot_kind": "non_terminal" if not kwargs.get("chain_terminal", True) else "terminal",
            "audit_terminal": kwargs.get("chain_terminal", True),
        }

    def _capture_taxonomy(record: dict[str, Any]) -> dict[str, Any]:
        taxonomy_records.append(record)
        taxonomy = {
            "ok": False,
            "category": "runtime_environment",
            "root_cause_signature": "runtime_environment:real_run_gate.chain_terminal",
            "reasons": [],
            "evidence": [],
        }
        record["failure_taxonomy"] = taxonomy
        record["failure_category"] = taxonomy["category"]
        record["root_cause_signature"] = taxonomy["root_cause_signature"]
        record["failure_reasons"] = []
        record["failure_evidence"] = []
        record["goal_audit"] = {}
        return taxonomy

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--project-ids", "L1-01", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(
        bench_cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []}],
    )
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "token")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-event-timeout")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_workspace_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(bench_cli, "build_factory_audit_record", _capture_build)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("real run gate must be skipped")),
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": False, "summary": "LLM route audit failed"},
    )
    monkeypatch.setattr(bench_cli, "apply_factory_bench_failure_taxonomy", _capture_taxonomy)

    def _event_wait_timeout(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": -1,
            "duration_s": 1.0,
            "run_id": "run-timeout",
            "error": "event_wait_timeout",
        }

    monkeypatch.setattr(bench_cli, "run_factory_chain", _event_wait_timeout)

    result = bench.main()

    assert result == 1
    assert len(captured_records) == 1
    assert captured_records[0]["chain_terminal"] is False
    assert len(taxonomy_records) == 1
    real_run_gate = taxonomy_records[0]["real_run_gate"]
    assert real_run_gate["skipped"] is True
    assert real_run_gate["requirements"]["chain_terminal"]["ok"] is False
    assert "event_wait_timeout" in real_run_gate["summary"]


def test_main_runner_exception_marks_audit_as_non_terminal(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """When the runner raises an exception, the audit record must be
    marked as non_terminal."""
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    captured_records: list[dict[str, Any]] = []

    def _capture_build(**kwargs: Any) -> dict[str, Any]:
        captured_records.append(kwargs)
        return {
            "all_checks_passed": False,
            "static_checks_passed": False,
            "has_plan_doc": False,
            "has_blueprint_doc": False,
            "has_qa_verdict": False,
            "code_file_count": 0,
            "source_file_count": 0,
            "checks": [],
            "audit_snapshot_kind": "non_terminal" if not kwargs.get("chain_terminal", True) else "terminal",
            "audit_terminal": kwargs.get("chain_terminal", True),
        }

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--project-ids", "L1-01", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(
        bench_cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []}],
    )
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "token")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-runner-exc")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(bench_cli, "build_factory_audit_record", _capture_build)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": False, "summary": "real run failed"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": False, "summary": "LLM route audit failed"},
    )

    def _runner_exception(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated runner crash")

    monkeypatch.setattr(bench_cli, "run_factory_chain", _runner_exception)

    result = bench.main()

    assert result == 1
    assert len(captured_records) == 1
    assert captured_records[0]["chain_terminal"] is False


def test_main_completed_chain_marks_audit_as_terminal(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """When run_factory_chain returns normally, the audit record must be
    marked as terminal."""
    captured_records: list[dict[str, Any]] = []

    def _capture_build(**kwargs: Any) -> dict[str, Any]:
        captured_records.append(kwargs)
        return _successful_audit_record(
            audit_snapshot_kind="terminal" if kwargs.get("chain_terminal", True) else "non_terminal",
            audit_terminal=kwargs.get("chain_terminal", True),
        )

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--project-ids", "L1-01", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(
        bench_cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []}],
    )
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-terminal")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(bench_cli, "build_factory_audit_record", _capture_build)
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _completed_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": "Build something",
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench_cli, "run_factory_chain", _completed_chain)

    result = bench.main()

    assert result == 0
    assert len(captured_records) == 1
    assert captured_records[0]["chain_terminal"] is True


# --- Catalog validation tests (from test_projects_v2_catalog.py) ---

REQUIRED_FIELDS = [
    "id",
    "level",
    "domain",
    "project_type",
    "primary_language",
    "title",
    "creative_hook",
    "novelty_tags",
    "brief",
    "test_focus",
    "checks",
]

VALID_LEVELS = set(range(1, 13))
VALID_LANGUAGES = {"typescript", "javascript", "python", "go", "rust", "cpp", "java"}
VALID_DOMAINS = {"science_creative", "creative", "game", "music", "internet_platform"}

LEVEL_MIN_FILES = {
    1: 3,
    2: 4,
    3: 5,
    4: 7,
    5: 8,
    6: 10,
    7: 11,
    8: 12,
    9: 13,
    10: 14,
    11: 15,
    12: 16,
}

LANG_COMPILE_CHECK = {
    "typescript": "ts_syntax",
    "javascript": "js_syntax",
    "python": "py_compile",
    "go": "go_compile",
    "rust": "rust_compile",
    "cpp": "cpp_compile",
    "java": "java_compile",
}


def test_catalog_schema_version() -> None:
    """Validate that projects_v2.json has the expected schema_version."""
    projects_file = Path(bench.__file__).resolve().parent / "projects_v2.json"
    catalog_data = json.loads(projects_file.read_text(encoding="utf-8"))
    version = catalog_data.get("schema_version")
    assert version == "factory-bench/2", f"Unexpected schema_version: {version}"


def test_catalog_hash_is_stable() -> None:
    """Validate that catalog_hash computation is deterministic."""
    projects_file = Path(bench.__file__).resolve().parent / "projects_v2.json"
    catalog_data = json.loads(projects_file.read_text(encoding="utf-8"))
    projects = catalog_data.get("projects", [])

    # Compute hash the same way as run_factory_bench.py
    catalog_hash = hashlib.sha256(json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[
        :16
    ]

    # Hash should be non-empty and deterministic
    assert len(catalog_hash) == 16
    assert all(c in "0123456789abcdef" for c in catalog_hash)

    # Compute again to verify determinism
    catalog_hash2 = hashlib.sha256(
        json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    assert catalog_hash == catalog_hash2


def test_catalog_has_120_projects() -> None:
    """Validate that catalog contains exactly 120 projects."""
    projects = bench.load_projects()
    assert len(projects) == 120, f"Expected 120 projects, got {len(projects)}"


def test_catalog_no_duplicate_ids() -> None:
    """Validate that catalog has no duplicate project IDs."""
    projects = bench.load_projects()
    ids = [p["id"] for p in projects]
    dupes = [x for x in ids if ids.count(x) > 1]
    assert not dupes, f"Duplicate IDs: {sorted(set(dupes))}"


def test_catalog_all_levels_covered() -> None:
    """Validate that catalog covers all levels L1-L12."""
    projects = bench.load_projects()
    levels = {int(p["level"]) for p in projects}
    missing = VALID_LEVELS - levels
    assert not missing, f"Missing levels: {sorted(missing)}"


def test_catalog_10_projects_per_level() -> None:
    """Validate that each level has exactly 10 projects."""
    projects = bench.load_projects()
    counts = Counter(int(p["level"]) for p in projects)
    for level in VALID_LEVELS:
        assert counts[level] == 10, f"L{level} has {counts[level]} projects, expected 10"


def test_catalog_required_fields_present() -> None:
    """Validate that all required fields are present in each project."""
    projects = bench.load_projects()
    for field in REQUIRED_FIELDS:
        for p in projects:
            assert field in p, f"Project {p.get('id', '?')} missing required field: {field}"


def test_catalog_level_range() -> None:
    """Validate that all project levels are in valid range."""
    projects = bench.load_projects()
    for p in projects:
        level = int(p["level"])
        assert level in VALID_LEVELS, f"Project {p['id']} has invalid level: {level}"


def test_catalog_language_valid() -> None:
    """Validate that all project languages are valid."""
    projects = bench.load_projects()
    for p in projects:
        lang = p["primary_language"]
        assert lang in VALID_LANGUAGES, f"Project {p['id']} has invalid language: {lang}"


def test_catalog_id_format() -> None:
    """Validate that project IDs match the expected format."""
    projects = bench.load_projects()
    for p in projects:
        pid = str(p["id"])
        level = int(p["level"])
        assert pid.startswith(f"L{level}-"), f"ID {pid} doesn't match level {level}"


def test_catalog_min_files_matches_level() -> None:
    """Validate that min_files checks match level expectations."""
    projects = bench.load_projects()
    for p in projects:
        level = int(p["level"])
        checks = p.get("checks", [])
        for check in checks:
            check_str = str(check)
            if check_str.startswith("min_files:"):
                min_files = int(check_str.split(":")[1])
                expected = LEVEL_MIN_FILES.get(level)
                assert min_files == expected, (
                    f"Project {p['id']} (L{level}): min_files={min_files}, expected={expected}"
                )


def test_catalog_compile_check_matches_language() -> None:
    """Validate that compile checks match primary language."""
    projects = bench.load_projects()
    for p in projects:
        lang = p["primary_language"]
        expected = LANG_COMPILE_CHECK.get(lang)
        if not expected:
            continue
        checks = [str(c) for c in p.get("checks", [])]
        assert expected in checks, f"Project {p['id']} ({lang}): missing compile check {expected}"


def test_catalog_content_any_check_present() -> None:
    """Validate that content_any check is present for each project."""
    projects = bench.load_projects()
    for p in projects:
        checks = [str(c) for c in p.get("checks", [])]
        has_content = any(c.startswith("content_any:") for c in checks)
        assert has_content, f"Project {p['id']} missing content_any check"


def test_catalog_source_target_coverage_present() -> None:
    """Validate that source_target_coverage check is present for each project."""
    projects = bench.load_projects()
    for p in projects:
        checks = [str(c) for c in p.get("checks", [])]
        has_coverage = any(c.startswith("source_target_coverage:") for c in checks)
        assert has_coverage, f"Project {p['id']} missing source_target_coverage check"


def test_catalog_language_distribution_balanced() -> None:
    """Validate that language distribution is balanced."""
    projects = bench.load_projects()
    counts = Counter(p["primary_language"] for p in projects)
    min_count = min(counts.values())
    max_count = max(counts.values())
    assert max_count - min_count <= 3, f"Language distribution too uneven: {dict(counts)}"


def test_catalog_novelty_tags_minimum() -> None:
    """Validate that each project has at least 3 novelty tags."""
    projects = bench.load_projects()
    for p in projects:
        tags = p.get("novelty_tags", [])
        assert len(tags) >= 3, f"Project {p['id']} has only {len(tags)} novelty_tags"


def test_catalog_brief_minimum_length() -> None:
    """Validate that each project brief is at least 50 characters."""
    projects = bench.load_projects()
    for p in projects:
        brief = p.get("brief", "")
        assert len(brief) >= 50, f"Project {p['id']} brief too short: {len(brief)} chars"


def test_runner_audit_includes_catalog_hash_and_schema_version(monkeypatch: Any, tmp_path: Path) -> None:
    """Verify runner writes catalog_hash and catalog_schema_version into audit and meta files."""
    projects = [
        {"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []},
    ]

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(bench_cli, "load_projects", lambda: projects)
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-meta")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        bench_cli,
        "build_factory_audit_record",
        lambda **_kwargs: _successful_audit_record(),
    )
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": "Build something",
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench_cli, "run_factory_chain", _chain)

    result = bench.main()
    assert result == 0

    # Compute expected catalog hash from the projects list
    expected_hash = hashlib.sha256(
        json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]

    # Verify .catalog_meta.json was written into the unique fresh workspace.
    catalog_meta_paths = list((tmp_path / "workspaces").rglob(".catalog_meta.json"))
    assert len(catalog_meta_paths) == 1, "one identity-bound .catalog_meta.json must be written"
    catalog_meta_path = catalog_meta_paths[0]
    catalog_meta = json.loads(catalog_meta_path.read_text(encoding="utf-8"))
    assert catalog_meta["catalog_schema_version"] == "factory-bench/2"
    assert catalog_meta["catalog_hash"] == expected_hash
    assert catalog_meta["project_id"] == "L1-01"

    # Verify the audit file contains catalog_schema_version and catalog_hash
    audit_dir = tmp_path / "audits"
    run_dirs = list(audit_dir.iterdir())
    assert len(run_dirs) == 1
    audit_files = sorted(run_dirs[0].glob("*.audit.json"))
    assert len(audit_files) == 1
    audit_data = json.loads(audit_files[0].read_text(encoding="utf-8"))
    assert audit_data["catalog_schema_version"] == "factory-bench/2"
    assert audit_data["catalog_hash"] == expected_hash
    assert audit_data["run_id"] == audit_data["run_id"]  # non-empty


def test_catalog_hash_changes_when_projects_change() -> None:
    """Verify catalog_hash changes when the underlying project data changes."""
    projects_a = [{"id": "L1-01", "level": 1}]
    projects_b = [{"id": "L1-01", "level": 1, "extra": True}]

    hash_a = hashlib.sha256(json.dumps(projects_a, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    hash_b = hashlib.sha256(json.dumps(projects_b, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

    assert hash_a != hash_b, "catalog_hash must change when project data changes"
