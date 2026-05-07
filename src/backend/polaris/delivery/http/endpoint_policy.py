"""Canonical HTTP endpoint policy for auth, rate limiting, and observability."""

from __future__ import annotations

from enum import StrEnum


class EndpointPolicy(StrEnum):
    """Operational policy class for an HTTP path."""

    PUBLIC_PROBE = "public_probe"
    AUTH_PROBE = "auth_probe"
    AUTH_BOOTSTRAP = "auth_bootstrap"
    AUTH_ACTION = "auth_action"
    INFRASTRUCTURE = "infrastructure"
    STREAM_RUNTIME = "stream_runtime"


_PUBLIC_PROBES = frozenset({"/health", "/ready", "/live"})
_INFRASTRUCTURE_PATHS = frozenset({"/metrics", "/favicon.ico"})
_LOW_SIGNAL_PATHS = frozenset({"/health", "/metrics", "/favicon.ico"})
_LOW_SIGNAL_PREFIXES = tuple(f"{path}/" for path in _LOW_SIGNAL_PATHS)
_AUTH_PROBES = frozenset(
    {
        "/v2/health",
        "/v2/ready",
        "/v2/live",
        "/v2/stream/health",
        "/v2/observability/health",
        "/v2/observability/health/backend",
    }
)
_AUTH_BOOTSTRAP_EXACT = frozenset(
    {
        "/settings",
        "/v2/settings",
        "/runtime/storage-layout",
        "/runtime/storage/layout",
        "/v2/runtime/storage-layout",
        "/v2/runtime/storage/layout",
        "/v2/runtime/diagnostics",
        "/state/snapshot",
        "/v2/state/snapshot",
        "/llm/status",
        "/v2/llm/status",
        "/memos/list",
        "/v2/memos/list",
    }
)
_STREAM_PREFIXES = ("/v2/ws/", "/v2/stream/")


def normalize_path(path: str) -> str:
    """Normalize an HTTP request path for policy classification."""
    normalized = "/" + str(path or "").strip().lstrip("/")
    if len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized


def classify_endpoint(path: str) -> EndpointPolicy:
    """Classify an HTTP path into its canonical operational policy."""
    normalized = normalize_path(path)
    if normalized in _PUBLIC_PROBES:
        return EndpointPolicy.PUBLIC_PROBE
    if normalized in _INFRASTRUCTURE_PATHS:
        return EndpointPolicy.INFRASTRUCTURE
    if normalized in _AUTH_PROBES:
        return EndpointPolicy.AUTH_PROBE
    if normalized in _AUTH_BOOTSTRAP_EXACT:
        return EndpointPolicy.AUTH_BOOTSTRAP
    if any(normalized.startswith(prefix) for prefix in _STREAM_PREFIXES):
        return EndpointPolicy.STREAM_RUNTIME
    return EndpointPolicy.AUTH_ACTION


def is_public_probe(path: str) -> bool:
    """Return whether path is a no-auth process probe."""
    return classify_endpoint(path) == EndpointPolicy.PUBLIC_PROBE


def is_observability_exempt(path: str) -> bool:
    """Return whether detailed logs/metrics/audit context should skip this path."""
    normalized = normalize_path(path)
    return normalized in _LOW_SIGNAL_PATHS or normalized.startswith(_LOW_SIGNAL_PREFIXES)


def is_always_rate_limit_exempt(path: str) -> bool:
    """Return whether a path should never consume the normal request bucket."""
    normalized = normalize_path(path)
    return normalized in _LOW_SIGNAL_PATHS or normalized.startswith(_LOW_SIGNAL_PREFIXES)


def is_bootstrap_rate_limit_sensitive(path: str) -> bool:
    """Return whether path is startup-critical and should be loopback-exempt."""
    return classify_endpoint(path) == EndpointPolicy.AUTH_BOOTSTRAP
