"""Tests for canonical HTTP endpoint policy classification."""

from __future__ import annotations

from polaris.delivery.http.endpoint_policy import (
    EndpointPolicy,
    classify_endpoint,
    is_always_rate_limit_exempt,
    is_bootstrap_rate_limit_sensitive,
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
    for path in ("/v2/health", "/v2/ready", "/v2/live", "/v2/stream/health"):
        assert classify_endpoint(path) == EndpointPolicy.AUTH_PROBE
        assert is_public_probe(path) is False
        assert is_always_rate_limit_exempt(path) is False
        assert is_observability_exempt(path) is False


def test_bootstrap_endpoints_are_loopback_sensitive_not_public() -> None:
    for path in ("/settings", "/runtime/storage-layout", "/llm/status", "/memos/list"):
        assert classify_endpoint(path) == EndpointPolicy.AUTH_BOOTSTRAP
        assert is_bootstrap_rate_limit_sensitive(path) is True
        assert is_always_rate_limit_exempt(path) is False
        assert is_public_probe(path) is False


def test_runtime_storage_layout_routes_are_backward_compatible_bootstrap() -> None:
    for path in (
        "/runtime/storage-layout",
        "/runtime/storage/layout",
        "/v2/runtime/storage-layout",
        "/v2/runtime/storage/layout",
    ):
        assert classify_endpoint(path) == EndpointPolicy.AUTH_BOOTSTRAP
        assert is_bootstrap_rate_limit_sensitive(path) is True


def test_normal_action_default_policy() -> None:
    assert classify_endpoint("/v2/pm/status") == EndpointPolicy.AUTH_ACTION
    assert is_observability_exempt("/v2/pm/status") is False
    assert is_always_rate_limit_exempt("/v2/pm/status") is False
