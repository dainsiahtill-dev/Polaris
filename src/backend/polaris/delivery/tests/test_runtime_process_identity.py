"""Runtime process identity remains bounded and independent from source freshness."""

import argparse
import inspect
from pathlib import Path

import pytest
from polaris.delivery.cli import backend
from polaris.delivery.http.routers import system


def test_process_identity_does_not_rehash_backend_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unexpected_source_scan() -> str:
        raise AssertionError("process identity must not scan the backend source tree")

    monkeypatch.setattr(system, "_compute_backend_source_fingerprint", _unexpected_source_scan)

    payload = system._build_runtime_process_identity_response()

    assert payload["pid"] > 0
    assert payload["backend_root"]
    assert payload["source"] == "runtime/fingerprint:process_startup"
    assert "current_source_fingerprint" not in payload
    assert "stale_since_startup" not in payload


def test_process_identity_route_stays_off_the_shared_worker_pool() -> None:
    """Factory load must not queue supervisor attestation behind sync work."""

    route = next(route for route in system.router.routes if route.path == "/v2/runtime/process-identity")

    assert inspect.iscoroutinefunction(route.endpoint)
    assert len(route.dependant.dependencies) == 1
    assert inspect.iscoroutinefunction(route.dependant.dependencies[0].call)


def test_process_identity_reports_immutable_workspace_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_INSTANCE_WORKSPACE", "/srv/polaris/main")
    monkeypatch.setattr(system, "_active_workspace_from_request", lambda _request: "/tmp/bench-project")

    payload = system._build_runtime_process_identity_response()

    assert payload["workspace"] == "/srv/polaris/main"
    assert payload["active_workspace"] == "/tmp/bench-project"
    assert payload["workspace_binding_match"] is False


def test_backend_cli_records_immutable_instance_workspace_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    # Register both keys before the CLI mutates os.environ so monkeypatch
    # restores the process environment for every later endpoint test.
    monkeypatch.setenv("KERNELONE_WORKSPACE", "/before/workspace")
    monkeypatch.setenv("KERNELONE_INSTANCE_WORKSPACE", "/before/binding")
    args = argparse.Namespace(
        workspace=str(workspace),
        runtime_root=None,
        token=None,
        port=49977,
        instance_id="main",
        kind="development",
        cors_origins=None,
    )

    backend._apply_env(args)

    expected = str(workspace.resolve())
    assert backend.os.environ["KERNELONE_WORKSPACE"] == expected
    assert backend.os.environ["KERNELONE_INSTANCE_WORKSPACE"] == expected
    assert backend.os.environ["KERNELONE_RUNTIME_ROOT"] == str((workspace / ".polaris" / "runtime").resolve())
    assert "KERNELONE_RUNTIME_CACHE_ROOT" not in backend.os.environ
