from __future__ import annotations


def test_runtime_projection_no_longer_exports_legacy_redirects() -> None:
    from polaris.cells.runtime.projection.internal import runtime_projection_service

    assert not hasattr(runtime_projection_service, "build_director_status_legacy")
    assert not hasattr(runtime_projection_service, "build_pm_status_legacy")


def test_runtime_ws_status_no_longer_exports_legacy_director_wrappers() -> None:
    from polaris.delivery.ws import runtime_endpoint

    assert not hasattr(runtime_endpoint, "_merge_director_runtime_payload")
    assert not hasattr(runtime_endpoint, "get_v2_director_runtime_status")
    assert not hasattr(runtime_endpoint, "_get_workflow_runtime_status")
