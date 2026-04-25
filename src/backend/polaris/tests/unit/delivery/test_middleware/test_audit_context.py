"""Tests for polaris.delivery.http.middleware.audit_context.

Covers AuditContextMiddleware, ID generation, and context extraction.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from polaris.delivery.http.middleware.audit_context import (
    AuditContextMiddleware,
    _generate_run_id,
    _generate_task_id,
    _generate_trace_id,
    get_audit_context_middleware,
)


class TestIdGeneration:
    """Tests for trace/run/task ID generation functions."""

    def test_generate_trace_id_length(self) -> None:
        trace_id = _generate_trace_id()
        assert len(trace_id) == 16

    def test_generate_trace_id_is_hex(self) -> None:
        trace_id = _generate_trace_id()
        int(trace_id, 16)  # Should not raise
        assert trace_id.isalnum()

    def test_generate_trace_id_uniqueness(self) -> None:
        ids = set()
        for _ in range(100):
            ids.add(_generate_trace_id())
        # Should generate unique IDs
        assert len(ids) == 100

    def test_generate_run_id_is_valid_uuid(self) -> None:
        run_id = _generate_run_id()
        uuid.UUID(run_id)  # Should not raise
        assert len(run_id) == 36  # Standard UUID format

    def test_generate_run_id_uniqueness(self) -> None:
        ids = set()
        for _ in range(100):
            ids.add(_generate_run_id())
        assert len(ids) == 100

    def test_generate_task_id_is_valid_uuid(self) -> None:
        task_id = _generate_task_id()
        uuid.UUID(task_id)  # Should not raise
        assert len(task_id) == 36

    def test_generate_task_id_uniqueness(self) -> None:
        ids = set()
        for _ in range(100):
            ids.add(_generate_task_id())
        assert len(ids) == 100


class TestAuditContextMiddleware:
    """Tests for AuditContextMiddleware middleware logic."""

    def test_excluded_paths_constant(self) -> None:
        assert "/health" in AuditContextMiddleware.EXCLUDED_PATHS
        assert "/metrics" in AuditContextMiddleware.EXCLUDED_PATHS
        assert "/favicon.ico" in AuditContextMiddleware.EXCLUDED_PATHS

    def test_should_setup_context_excludes_health(self) -> None:
        middleware = AuditContextMiddleware(MagicMock())
        assert middleware._should_setup_context("/health") is False
        assert middleware._should_setup_context("/health/live") is False

    def test_should_setup_context_excludes_metrics(self) -> None:
        middleware = AuditContextMiddleware(MagicMock())
        assert middleware._should_setup_context("/metrics") is False

    def test_should_setup_context_allows_normal_paths(self) -> None:
        middleware = AuditContextMiddleware(MagicMock())
        assert middleware._should_setup_context("/api/users") is True
        assert middleware._should_setup_context("/v1/posts") is True

    def test_extract_or_generate_ids_uses_header_values(self) -> None:
        middleware = AuditContextMiddleware(MagicMock())
        request = MagicMock()
        request.headers.get.side_effect = lambda k, d=None: {
            "X-Trace-ID": "trace-header-123",
            "X-Run-ID": "run-header-456",
            "X-Task-ID": "task-header-789",
        }.get(k, d if d is not None else "")

        trace_id, run_id, task_id = middleware._extract_or_generate_ids(request)

        assert trace_id == "trace-header-123"
        assert run_id == "run-header-456"
        assert task_id == "task-header-789"

    def test_extract_or_generate_ids_generates_when_missing(self) -> None:
        middleware = AuditContextMiddleware(MagicMock())
        request = MagicMock()
        request.headers.get.return_value = ""

        trace_id, run_id, task_id = middleware._extract_or_generate_ids(request)

        # Should generate values
        assert len(trace_id) == 16
        assert len(run_id) == 36
        assert len(task_id) == 36

    def test_extract_or_generate_ids_generates_partial_headers(self) -> None:
        middleware = AuditContextMiddleware(MagicMock())
        request = MagicMock()
        # Only trace-id provided
        request.headers.get = lambda k, d=None: "provided-trace" if k == "X-Trace-ID" else (d if d is not None else "")

        trace_id, run_id, task_id = middleware._extract_or_generate_ids(request)

        assert trace_id == "provided-trace"
        assert len(run_id) == 36
        assert len(task_id) == 36

    def test_middleware_enabled_by_default(self) -> None:
        middleware = AuditContextMiddleware(MagicMock())
        assert middleware._enabled is True

    def test_middleware_can_be_disabled(self) -> None:
        middleware = AuditContextMiddleware(MagicMock(), enabled=False)
        assert middleware._enabled is False


class TestGetAuditContextMiddlewareFactory:
    """Tests for get_audit_context_middleware factory function."""

    def test_factory_returns_middleware_instance(self) -> None:
        middleware = get_audit_context_middleware(MagicMock())
        assert isinstance(middleware, AuditContextMiddleware)

    def test_factory_respects_enabled_param(self) -> None:
        middleware = get_audit_context_middleware(MagicMock(), enabled=False)
        assert middleware._enabled is False


class TestAuditContextMiddlewareBehavior:
    """Tests for middleware behavior with mocked dependencies."""

    def test_middleware_disabled_returns_call_next(self) -> None:
        """When disabled, middleware just calls call_next without setup."""
        middleware = AuditContextMiddleware(MagicMock(), enabled=False)
        request = MagicMock()
        request.url.path = "/api/test"
        request.headers.get.return_value = ""

        mock_response = MagicMock()
        call_next_called = False

        async def call_next(req):
            nonlocal call_next_called
            call_next_called = True
            return mock_response

        # Use pytest's asyncio support
        import asyncio

        async def run_test():
            return await middleware.dispatch(request, call_next)

        result = asyncio.run(run_test())

        assert call_next_called is True
        assert result is mock_response


class TestAuditContextEdgeCases:
    """Tests for edge cases in audit context handling."""

    def test_should_setup_context_handles_root_path(self) -> None:
        middleware = AuditContextMiddleware(MagicMock())
        assert middleware._should_setup_context("/") is True

    def test_should_setup_context_handles_deep_paths(self) -> None:
        middleware = AuditContextMiddleware(MagicMock())
        assert middleware._should_setup_context("/api/v1/users/123/posts/456") is True

    def test_extract_ids_handles_empty_string_headers(self) -> None:
        middleware = AuditContextMiddleware(MagicMock())
        request = MagicMock()
        request.headers.get = lambda k, d=None: "" if k in ("X-Trace-ID", "X-Run-ID", "X-Task-ID") else None

        trace_id, run_id, task_id = middleware._extract_or_generate_ids(request)

        assert len(trace_id) == 16
        assert len(run_id) == 36
        assert len(task_id) == 36

    def test_extract_ids_handles_none_headers(self) -> None:
        middleware = AuditContextMiddleware(MagicMock())
        request = MagicMock()
        request.headers.get = lambda k, d=None: None

        trace_id, run_id, task_id = middleware._extract_or_generate_ids(request)

        assert len(trace_id) == 16
        assert len(run_id) == 36
        assert len(task_id) == 36
