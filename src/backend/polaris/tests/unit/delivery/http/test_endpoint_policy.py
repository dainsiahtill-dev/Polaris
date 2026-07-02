"""Tests for canonical HTTP endpoint policy classification."""

from __future__ import annotations

from polaris.delivery.http.endpoint_policy import (
    EndpointPolicy,
    classify_endpoint,
    is_always_rate_limit_exempt,
    is_bootstrap_rate_limit_sensitive,
    is_loopback_rate_limit_exempt,
    is_observability_exempt,
    is_public_probe,
)


def test_public_probes_are_public_but_only_health_is_low_signal() -> None:
    for path in ("/health", "/ready", "/live"):
        assert classify_endpoint(path) == EndpointPolicy.PUBLIC_PROBE
        assert is_public_probe(path) is True
    assert is_always_rate_limit_exempt("/health") is True
    assert is_observability_exempt("/health") is True
    for path in ("/ready", "/live"):
        assert is_always_rate_limit_exempt(path) is False
        assert is_observability_exempt(path) is False
    for path in ("/health/live", "/metrics/prometheus"):
        assert is_always_rate_limit_exempt(path) is True
        assert is_observability_exempt(path) is True


def test_v2_probes_are_auth_probes_and_diagnostic_visible() -> None:
    for path in ("/v2/health", "/v2/ready", "/v2/live"):
        assert classify_endpoint(path) == EndpointPolicy.AUTH_PROBE
        assert is_public_probe(path) is False
        assert is_always_rate_limit_exempt(path) is False
        assert is_observability_exempt(path) is False


def test_removed_stream_paths_are_normal_absent_actions() -> None:
    for path in ("/v2/stream/health", "/v2/stream/chat", "/v2/stream/chat/backpressure"):
        assert classify_endpoint(path) == EndpointPolicy.AUTH_ACTION
        assert is_public_probe(path) is False
        assert is_always_rate_limit_exempt(path) is False
        assert is_observability_exempt(path) is False


def test_bootstrap_endpoints_are_loopback_sensitive_not_public() -> None:
    for path in ("/v2/settings", "/v2/runtime/storage/layout", "/v2/state/snapshot", "/llm/status", "/v2/memos/list"):
        assert classify_endpoint(path) == EndpointPolicy.AUTH_BOOTSTRAP
        assert is_bootstrap_rate_limit_sensitive(path) is True
        assert is_always_rate_limit_exempt(path) is False
        assert is_public_probe(path) is False


def test_retired_memos_alias_is_not_bootstrap_sensitive() -> None:
    assert classify_endpoint("/memos/list") == EndpointPolicy.AUTH_ACTION
    assert is_bootstrap_rate_limit_sensitive("/memos/list") is False


def test_retired_system_aliases_are_not_bootstrap_sensitive() -> None:
    for path in ("/settings", "/state/snapshot", "/app/shutdown"):
        assert classify_endpoint(path) == EndpointPolicy.AUTH_ACTION
        assert is_bootstrap_rate_limit_sensitive(path) is False


def test_factory_control_plane_paths_are_loopback_rate_limit_exempt_only() -> None:
    for path in (
        "/v2/context/99d3de73eedeba4206d0dce2",
        "/v2/context/99d3de73eedeba4206d0dce2/final-request",
        "/v2/factory/runs",
        "/v2/factory/runs/factory_123",
        "/v2/factory/runs/factory_123/artifacts",
        "/v2/factory/bench/sessions",
        "/v2/factory/bench/sessions/bench-1/events",
    ):
        assert classify_endpoint(path) == EndpointPolicy.AUTH_ACTION
        assert is_loopback_rate_limit_exempt(path) is True
        assert is_always_rate_limit_exempt(path) is False
        assert is_public_probe(path) is False


def test_retired_runtime_storage_layout_aliases_are_normal_actions() -> None:
    for path in ("/runtime/storage-layout", "/runtime/storage/layout", "/v2/runtime/storage-layout"):
        assert classify_endpoint(path) == EndpointPolicy.AUTH_ACTION
        assert is_bootstrap_rate_limit_sensitive(path) is False


def test_normal_action_default_policy() -> None:
    assert classify_endpoint("/v2/pm/status") == EndpointPolicy.AUTH_ACTION
    assert is_observability_exempt("/v2/pm/status") is False
    assert is_always_rate_limit_exempt("/v2/pm/status") is False
    assert is_loopback_rate_limit_exempt("/v2/pm/status") is False
