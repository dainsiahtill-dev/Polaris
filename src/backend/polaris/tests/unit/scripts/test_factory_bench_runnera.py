"""Tests for the factory-bench runner verdict semantics."""

from __future__ import annotations

import inspect
import json
import os
import sys
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
from polaris.tests.unit.scripts._factory_bench_runner_audit_helpers import (
    _factory_chain_destructive_findings,
    _guard_runner_external_io,
    _matching_isolated_launch_identity,
    _record,
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
    _next_immutable_json_path,
    _project_workspace_for_run,
    _sanitize_run_id,
    _write_immutable_json,
    apply_factory_bench_gates,
    discover_artifacts,
    map_factory_run_to_chain_results,
    read_chain_results_from_runtime_dirs,
    resolve_runtime_dirs_for_workspace,
)
from scripts.factory_bench.test_bench_to_backend_integration import (
    _assert_retired_consumer,
    _consumer_batch,
    _consumer_case,
)


@pytest.fixture(autouse=True)
def _isolate_instance_registry(monkeypatch: Any, tmp_path: Path) -> None:
    _guard_runner_external_io(monkeypatch, tmp_path)
    monkeypatch.setenv("KERNELONE_INSTANCE_HOME", str(tmp_path / "instances-home"))
    monkeypatch.setenv("FACTORY_BENCH_LAUNCHER_INSTANCE_MODE", "observed")
    monkeypatch.setattr(bench_cli, "persist_real_run_gate_ledger", lambda *_args, **_kwargs: {"ok": True})
    bench.configure_bench_backend("", "", "")


def test_unmocked_runner_external_io_is_rejected_before_launch() -> None:
    import subprocess
    import urllib.request

    with pytest.raises(AssertionError, match="unmocked runner external IO"):
        subprocess.Popen([sys.executable, "-c", "raise SystemExit(99)"])
    with pytest.raises(AssertionError, match="unmocked runner external IO"):
        urllib.request.urlopen("http://127.0.0.1:49977")
    with pytest.raises(AssertionError, match="unmocked runner external IO"):
        from polaris.cells.instances.internal.service import InstanceSupervisor

        InstanceSupervisor()._start_backend()


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
    assert first["runtime_root"] == str((workspace / ".polaris" / "runtime").resolve())
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
    monkeypatch.setattr(bench_session, "_wait_backend_health", lambda *_args, **_kwargs: True)
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
    monkeypatch.setattr(bench_session, "_wait_backend_health", lambda *_args, **_kwargs: True)
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
            {
                "instance_id": "owned-prior-failed-backend-alive",
                "kind": "bench_project",
                "status": "failed",
                "backend_alive": True,
                "frontend_alive": False,
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
                "instance_id": "owned-prior-stopped",
                "kind": "bench_project",
                "status": "stopped",
                "backend_alive": False,
                "frontend_alive": False,
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
        ],
    )

    def _stop(_self: InstanceSupervisor, instance_id: str) -> dict[str, str]:
        stopped.append(instance_id)
        return {"instance_id": instance_id, "status": "stopped"}

    monkeypatch.setattr(InstanceSupervisor, "stop_instance", _stop)

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
    assert stopped == ["owned-prior", "owned-prior-failed-backend-alive"]
    assert captured["metadata"]["instance_launch_receipt"]["resume_predecessor_instance_ids"] == [
        "owned-prior",
        "owned-prior-failed-backend-alive",
    ]


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

    event_count_after = int(bench_cli.load_run_ledger_projection(workspace, run_id="run-task-boundary")["event_count"])

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


def test_runner_audits_llm_routes_for_llm_backed_roles_only(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Fixed-route preflight retired; direct request preserves actual-role projection without invented PM/CE invocation."""
    inputs, report, http, _ = _consumer_case(tmp_path)
    assert bench.FACTORY_BENCH_REQUIRED_LLM_ROLES == ("pm", "chief_engineer", "director", "qa")
    request = json.loads((inputs.evidence_dir / "formal-request.json").read_text(encoding="utf-8"))
    assert request["role"] == "director" and request["workflow"] == "direct"
    assert report["observation"]["platform"]["active_roles"] == ["director"]
    assert all("/v2/factory/" not in path and "llm/status" not in path for _, path, _ in http.calls)


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


def test_run_chain_preserves_serial_director_workflow_by_default(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Subprocess serial protocol retired; default single Director formal request preserved."""
    _assert_retired_consumer(tmp_path, writer="run_chain")
    inputs, _, _, _ = _consumer_case(tmp_path / "formal")
    request = json.loads((inputs.evidence_dir / "formal-request.json").read_text(encoding="utf-8"))
    assert request["workflow"] == "direct" and request["role"] == "director"


def test_run_chain_can_enable_parallel_director_workflow(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Legacy parallel subprocess writer rejects before target or log effects."""
    _assert_retired_consumer(tmp_path, writer="run_chain", director_workflow_execution_mode="parallel")


def test_run_chain_task_market_driver_plans_then_dispatches_market(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Task Market dispatch no longer authorizes new engineering writes."""
    _assert_retired_consumer(tmp_path, writer="run_chain", director_dispatch_driver="task_market")


def test_main_task_market_driver_uses_http_factory_chain_without_legacy_fallback(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """Migrated consumer: Retired Task Market option fails before effects instead of falling back to Factory."""
    base = tmp_path / "batch"
    assert bench_cli.main(["--director-dispatch-driver", "task_market", "--work-dir", str(base)]) == 2
    assert not base.exists()


def test_main_marks_backend_session_failed_when_run_aborts(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Partial launch stays external failure/unknown cleanup; no old bench-session product write."""
    code, summary, traces = _consumer_batch(tmp_path, monkeypatch, fail_start=True)
    record = summary["records"][0]
    assert code != 0 and record["launch"]["harness_status"] == "UNKNOWN"
    assert record["resources_closed"] is False and record["catalog_quality"] == "UNKNOWN"
    assert traces[0][1].calls == []
    assert not Path(record["workspace"]).exists()


def test_main_rejects_unknown_explicit_project_ids(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Keep rejection of partial explicit catalog selection through current real CLI."""
    base = tmp_path / "batch"
    assert bench_cli.main(["--dry-run", "--project-ids", "L1-01,NO-SUCH", "--work-dir", str(base)]) == 2
    assert not base.exists()


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
    """Migrated consumer: Preserve all120/L1-L12 selection; dry-run cannot become delivery evidence or allocate targets."""
    base = tmp_path / "batch"
    assert bench_cli.main(["--dry-run", "--work-dir", str(base)]) == 0
    summary = json.loads(next((base / "reports").glob("*/summary.json")).read_text(encoding="utf-8"))
    assert summary["catalog_total"] == summary["selected_count"] == len(summary["records"]) == 120
    assert {r["original_project"]["level"] for r in summary["records"]} == set(range(1, 13))
    assert all(r["harness_status"] == "NOT_RUN" for r in summary["records"])
    assert not (base / "workspaces").exists()


def test_main_default_max_failed_zero_does_not_early_stop(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Preserve no default early stop after closed rejection; unknown resource fence remains separate."""
    code, summary, traces = _consumer_batch(tmp_path, monkeypatch, states=("rejected", "unknown"))
    assert code == 1 and len(traces) == len(summary["records"]) == 2
    assert [r["project_id"] for r in summary["records"]] == ["L1-01", "L1-02"]
    assert all(r["resources_closed"] for r in summary["records"])
    assert summary["records"][0]["observation"]["platform"]["verification"] == "failed"


# --- run_factory_chain (API path) ---


def test_run_factory_chain_success(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Actual shared adapter observes current acceptance, preserves goal/checks and cleanup without Factory verdict."""
    inputs, report, http, supervisor = _consumer_case(tmp_path, state="accepted", workflow="collaborative")
    request = json.loads((inputs.evidence_dir / "formal-request.json").read_text(encoding="utf-8"))
    assert request["instruction"].endswith(inputs.requirements_file.read_text(encoding="utf-8"))
    assert request["workflow"] == "collaborative" and request["role"] == "director"
    assert request["required_checks"] == []
    assert report["observation"]["harness_status"] == "OBSERVED_ACCEPTED" and report["exit_code"] == 0
    assert report["cleanup"]["harness_status"] == "STOPPED"
    assert report["delivery_proven"] is False and report["catalog_quality"] == "UNKNOWN"
    assert [p for m, p, _ in http.calls if m == "POST"] == ["/v2/engineering/runs"]
    assert supervisor.stops == [inputs.instance_id]


def test_run_factory_chain_director_resume_uses_existing_pm_ce_evidence(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Legacy IDs/checkpoints never upgrade engine or authorize replay; history bytes preserved."""
    legacy = tmp_path / "history"
    legacy.mkdir()
    sentinel = legacy / "checkpoint.json"
    sentinel.write_text('{"run_id":"old-run","unknown_effect":true}', encoding="utf-8")
    original = sentinel.read_bytes()
    _assert_retired_consumer(tmp_path / "attempt", start_from="director", resume_workspace=legacy)
    assert sentinel.read_bytes() == original
    assert sorted(p.name for p in legacy.iterdir()) == ["checkpoint.json"]


def test_director_resume_accepts_fact_authoritative_runtime_without_task_file_mirror(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """Migrated consumer: Legacy IDs/checkpoints never upgrade engine or authorize replay; history bytes preserved."""
    legacy = tmp_path / "history"
    legacy.mkdir()
    sentinel = legacy / "checkpoint.json"
    sentinel.write_text('{"run_id":"old-run","unknown_effect":true}', encoding="utf-8")
    original = sentinel.read_bytes()
    _assert_retired_consumer(tmp_path / "attempt", start_from="director", resume_workspace=legacy)
    assert sentinel.read_bytes() == original
    assert sorted(p.name for p in legacy.iterdir()) == ["checkpoint.json"]


def test_run_factory_chain_director_resume_without_same_run_checkpoint_fails_closed(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """Migrated consumer: Legacy IDs/checkpoints never upgrade engine or authorize replay; history bytes preserved."""
    legacy = tmp_path / "history"
    legacy.mkdir()
    sentinel = legacy / "checkpoint.json"
    sentinel.write_text('{"run_id":"old-run","unknown_effect":true}', encoding="utf-8")
    original = sentinel.read_bytes()
    _assert_retired_consumer(tmp_path / "attempt", start_from="director", resume_workspace=legacy)
    assert sentinel.read_bytes() == original
    assert sorted(p.name for p in legacy.iterdir()) == ["checkpoint.json"]


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
    """Migrated consumer: Actual adapter persists failed launch before HTTP/target operation; no implicit retry."""
    inputs, report, http, _ = _consumer_case(tmp_path, fail_start=True)
    assert report["launch"]["harness_status"] == "UNKNOWN"
    assert report["exit_code"] != 0 and report["resources_closed"] is False
    assert http.calls == [] and not inputs.workspace.exists()
    assert json.loads((inputs.evidence_dir / "binding.json").read_text(encoding="utf-8"))["phase"] == "instance_unknown"


def test_run_factory_chain_event_wait_timeout(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Keep bounded wait, exactly owned cancellation and confirmed cleanup through formal API."""
    inputs, report, http, _ = _consumer_case(tmp_path, state="running")
    assert report["observation"]["harness_status"] == "TIMEOUT" and report["exit_code"] == 124
    assert report["final_observation"]["platform"]["status"] == "cancelled"
    assert report["cleanup"]["harness_status"] == "STOPPED"
    posts = [p for m, p, _ in http.calls if m == "POST"]
    assert posts == ["/v2/engineering/runs", "/v2/engineering/runs/consumer-l1-01/cancel"]
    assert not inputs.workspace.exists()


def test_run_factory_chain_failed_status(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Current failed evidence/QA stay failure, not missing; no silent repair or retry."""
    _, report, http, _ = _consumer_case(tmp_path, state="rejected")
    assert report["observation"]["harness_status"] == "REJECTED" and report["exit_code"] == 1
    platform = report["observation"]["platform"]
    assert platform["qa_verdict"] == "rejected" and platform["verification"] == "failed"
    assert platform["checks"][0]["exit_code"] == 1
    assert report["cleanup"]["harness_status"] == "STOPPED"
    assert len([c for c in http.calls if c[0] == "POST"]) == 1


def test_run_factory_chain_on_stage_change_callback(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Scoped persisted snapshots replace stage callback; completion never implies acceptance."""
    inputs, report, _, _ = _consumer_case(tmp_path, stages=True, workflow="collaborative")
    snapshots = [json.loads(p.read_text(encoding="utf-8")) for p in (inputs.evidence_dir / "snapshots").glob("*.json")]
    assert any(s["active_roles"] == ["chief_engineer"] and s["status"] == "running" for s in snapshots)
    assert any(s["active_roles"] == [] and s["status"] == "completed" for s in snapshots)
    assert report["observation"]["platform"]["last_seq"] == 2
    assert report["observation"]["platform"]["project_outcome"] == "not_accepted"


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
    """Migrated consumer: One batch identity preserves separate per-workspace run and request identities."""
    code, summary, traces = _consumer_batch(tmp_path, monkeypatch, states=("accepted", "accepted"))
    assert code == 0 and len(summary["records"]) == 2
    assert len({r["workspace"] for r in summary["records"]}) == 2
    requests = [
        json.loads((inputs.evidence_dir / "formal-request.json").read_text(encoding="utf-8")) for inputs, _, _ in traces
    ]
    assert len({r["request_key"] for r in requests}) == 2
    assert len({r["observation"]["platform"]["run_id"] for r in summary["records"]}) == 2
    assert all(summary["batch_id"] in str(inputs.evidence_dir) for inputs, _, _ in traces)
