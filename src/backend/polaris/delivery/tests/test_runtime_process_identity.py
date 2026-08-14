"""Runtime process identity remains bounded and independent from source freshness."""

import inspect

import pytest
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
