"""Tests for trace/context module."""

from __future__ import annotations

import os
from unittest.mock import patch

from polaris.kernelone.trace.context import (
    ContextManager,
    PolarisContext,
    _current_metadata,
    _current_span_stack,
    _generate_id,
    ensure_context,
    get_context,
    get_trace_id,
    inherit_context,
    new_trace,
)


class TestGenerateId:
    """Tests for _generate_id function."""

    def test_returns_string(self) -> None:
        """Returns a string."""
        result = _generate_id("test")
        assert isinstance(result, str)

    def test_contains_prefix(self) -> None:
        """Contains the provided prefix."""
        result = _generate_id("req")
        assert result.startswith("hp-req-")

    def test_unique_values(self) -> None:
        """Multiple calls produce unique values."""
        ids = [_generate_id("test") for _ in range(10)]
        assert len(set(ids)) == 10

    def test_hex_suffix(self) -> None:
        """Suffix is hex characters only."""
        result = _generate_id("test")
        suffix = result.split("-")[-1]
        assert len(suffix) == 8
        assert all(c in "0123456789abcdef" for c in suffix)


class TestPolarisContext:
    """Tests for PolarisContext dataclass."""

    def test_creation_with_defaults(self) -> None:
        """Can create with minimal fields."""
        ctx = PolarisContext(trace_id="hp-test-12345678")
        assert ctx.trace_id == "hp-test-12345678"
        assert ctx.run_id is None
        assert ctx.span_stack == []
        assert ctx.metadata == {}

    def test_creation_with_all_fields(self) -> None:
        """Can create with all fields."""
        ctx = PolarisContext(
            trace_id="t1",
            run_id="r1",
            request_id="req1",
            workflow_id="w1",
            task_id="task1",
            workspace="/tmp/ws",
            span_stack=[{"span_id": "s1", "name": "root"}],
            metadata={"key": "value"},
        )
        assert ctx.request_id == "req1"
        assert ctx.workspace == "/tmp/ws"

    def test_to_dict(self) -> None:
        """to_dict produces correct structure."""
        ctx = PolarisContext(trace_id="t1", run_id="r1")
        d = ctx.to_dict()
        assert d["trace_id"] == "t1"
        assert d["run_id"] == "r1"
        assert "span_depth" in d
        assert d["span_depth"] == 0

    def test_to_env_vars(self) -> None:
        """to_env_vars produces correct env mapping."""
        ctx = PolarisContext(
            trace_id="t1",
            run_id="r1",
            workspace="/ws",
        )
        env = ctx.to_env_vars()
        assert env["KERNELONE_TRACE_ID"] == "t1"
        assert env["KERNELONE_RUN_ID"] == "r1"
        assert env["KERNELONE_WORKSPACE"] == "/ws"

    def test_to_env_vars_omits_none(self) -> None:
        """None values are omitted from env vars."""
        ctx = PolarisContext(trace_id="t1")
        env = ctx.to_env_vars()
        assert "KERNELONE_RUN_ID" not in env
        assert "KERNELONE_REQUEST_ID" not in env

    def test_from_env_vars_with_trace_id(self) -> None:
        """Can reconstruct from env vars."""
        with patch.dict(
            os.environ,
            {
                "KERNELONE_TRACE_ID": "t1",
                "KERNELONE_RUN_ID": "r1",
            },
            clear=False,
        ):
            ctx = PolarisContext.from_env_vars()
            assert ctx is not None
            assert ctx.trace_id == "t1"
            assert ctx.run_id == "r1"

    def test_from_env_vars_without_trace_id(self) -> None:
        """Returns None when trace_id is not set."""
        with patch.dict(os.environ, {}, clear=True):
            ctx = PolarisContext.from_env_vars()
            assert ctx is None

    def test_with_span(self) -> None:
        """with_span creates new context with added span."""
        ctx = PolarisContext(trace_id="t1")
        child = ctx.with_span("child-operation")
        assert len(child.span_stack) == 1
        assert child.span_stack[0]["name"] == "child-operation"
        assert child.span_stack[0]["parent_span_id"] is None

    def test_with_span_nested(self) -> None:
        """Nested spans have correct parent."""
        ctx = PolarisContext(trace_id="t1")
        child = ctx.with_span("child")
        grandchild = child.with_span("grandchild")
        assert len(grandchild.span_stack) == 2
        assert grandchild.span_stack[1]["parent_span_id"] == child.span_stack[0]["span_id"]

    def test_with_metadata(self) -> None:
        """with_metadata adds metadata."""
        ctx = PolarisContext(trace_id="t1", metadata={"a": 1})
        new_ctx = ctx.with_metadata(b=2)
        assert new_ctx.metadata == {"a": 1, "b": 2}

    def test_immutability(self) -> None:
        """Original context is not modified."""
        ctx = PolarisContext(trace_id="t1", metadata={"a": 1})
        new_ctx = ctx.with_metadata(b=2)
        assert ctx.metadata == {"a": 1}
        assert new_ctx.metadata == {"a": 1, "b": 2}


class TestContextManager:
    """Tests for ContextManager static methods."""

    def setup_method(self) -> None:
        """Clear context before each test."""
        ContextManager.clear()

    def teardown_method(self) -> None:
        """Clear context after each test."""
        ContextManager.clear()

    def test_get_current_auto_creates(self) -> None:
        """get_current auto-creates trace_id if missing."""
        ctx = ContextManager.get_current()
        assert ctx.trace_id.startswith("hp-auto-")

    def test_set_context(self) -> None:
        """set_context stores all fields."""
        ctx = PolarisContext(trace_id="t1", run_id="r1")
        ContextManager.set_context(ctx)
        current = ContextManager.get_current()
        assert current.trace_id == "t1"
        assert current.run_id == "r1"

    def test_clear(self) -> None:
        """clear resets all context vars."""
        ctx = PolarisContext(trace_id="t1")
        ContextManager.set_context(ctx)
        ContextManager.clear()
        current = ContextManager.get_current()
        assert current.trace_id != "t1"

    def test_bind_context_restores(self) -> None:
        """bind_context restores previous context on exit."""
        ctx1 = PolarisContext(trace_id="t1")
        ContextManager.set_context(ctx1)
        ctx2 = PolarisContext(trace_id="t2")
        with ContextManager.bind_context(ctx2):
            current = ContextManager.get_current()
            assert current.trace_id == "t2"
        current = ContextManager.get_current()
        assert current.trace_id == "t1"

    def test_bind_context_nested(self) -> None:
        """Nested bind_context works correctly."""
        ctx1 = PolarisContext(trace_id="t1")
        ContextManager.set_context(ctx1)
        with ContextManager.bind_context(PolarisContext(trace_id="t2")):
            with ContextManager.bind_context(PolarisContext(trace_id="t3")):
                assert ContextManager.get_current().trace_id == "t3"
            assert ContextManager.get_current().trace_id == "t2"
        assert ContextManager.get_current().trace_id == "t1"

    def test_bind_context_exception(self) -> None:
        """Context is restored even on exception."""
        ctx1 = PolarisContext(trace_id="t1")
        ContextManager.set_context(ctx1)
        try:
            with ContextManager.bind_context(PolarisContext(trace_id="t2")):
                raise RuntimeError("test")
        except RuntimeError:
            pass
        assert ContextManager.get_current().trace_id == "t1"


class TestGetContext:
    """Tests for get_context convenience function."""

    def setup_method(self) -> None:
        """Clear context before each test."""
        ContextManager.clear()

    def teardown_method(self) -> None:
        """Clear context after each test."""
        ContextManager.clear()

    def test_returns_polaris_context(self) -> None:
        """Returns a PolarisContext."""
        ctx = get_context()
        assert isinstance(ctx, PolarisContext)

    def test_auto_generates_trace_id(self) -> None:
        """Auto-generates trace_id if none exists."""
        ctx = get_context()
        assert ctx.trace_id.startswith("hp-auto-")


class TestGetTraceId:
    """Tests for get_trace_id convenience function."""

    def setup_method(self) -> None:
        """Clear context before each test."""
        ContextManager.clear()

    def teardown_method(self) -> None:
        """Clear context after each test."""
        ContextManager.clear()

    def test_returns_string(self) -> None:
        """Returns a string."""
        tid = get_trace_id()
        assert isinstance(tid, str)

    def test_starts_with_prefix(self) -> None:
        """Starts with hp-auto- prefix."""
        tid = get_trace_id()
        assert tid.startswith("hp-auto-")

    def test_consistent_within_context(self) -> None:
        """Same trace_id returned within same context."""
        tid1 = get_trace_id()
        tid2 = get_trace_id()
        assert tid1 == tid2


class TestNewTrace:
    """Tests for new_trace context manager."""

    def setup_method(self) -> None:
        """Clear context before each test."""
        ContextManager.clear()

    def teardown_method(self) -> None:
        """Clear context after each test."""
        ContextManager.clear()

    def test_creates_trace_context(self) -> None:
        """Creates new trace context."""
        with new_trace("api-request") as ctx:
            assert ctx.trace_id.startswith("hp-api-request-")

    def test_includes_metadata(self) -> None:
        """Metadata is included in context."""
        with new_trace("test", metadata={"key": "val"}) as ctx:
            assert ctx.metadata == {"key": "val"}

    def test_extra_metadata(self) -> None:
        """Extra kwargs become metadata."""
        with new_trace("test", extra_key="extra_val") as ctx:
            assert ctx.metadata == {"extra_key": "extra_val"}

    def test_restores_previous(self) -> None:
        """Previous context is restored on exit."""
        ctx1 = PolarisContext(trace_id="t1")
        ContextManager.set_context(ctx1)
        with new_trace("test"):
            pass
        assert ContextManager.get_current().trace_id == "t1"

    def test_all_optional_fields(self) -> None:
        """All optional fields can be set."""
        with new_trace(
            "test",
            run_id="r1",
            request_id="req1",
            workflow_id="w1",
            task_id="task1",
            workspace="/ws",
        ) as ctx:
            assert ctx.run_id == "r1"
            assert ctx.request_id == "req1"
            assert ctx.workflow_id == "w1"
            assert ctx.task_id == "task1"
            assert ctx.workspace == "/ws"


class TestInheritContext:
    """Tests for inherit_context context manager."""

    def setup_method(self) -> None:
        """Clear context before each test."""
        ContextManager.clear()

    def teardown_method(self) -> None:
        """Clear context after each test."""
        ContextManager.clear()

    def test_inherits_trace_id(self) -> None:
        """Child inherits parent's trace_id."""
        parent = PolarisContext(trace_id="t1")
        with inherit_context(parent) as child:
            assert child.trace_id == "t1"

    def test_overrides(self) -> None:
        """Overrides can change fields."""
        parent = PolarisContext(trace_id="t1", run_id="r1")
        with inherit_context(parent, run_id="r2") as child:
            assert child.trace_id == "t1"
            assert child.run_id == "r2"

    def test_adds_span(self) -> None:
        """span_name adds new span to stack."""
        parent = PolarisContext(trace_id="t1")
        with inherit_context(parent, span_name="child") as child:
            assert len(child.span_stack) == 1
            assert child.span_stack[0]["name"] == "child"

    def test_uses_current_if_no_parent(self) -> None:
        """Uses current context if no parent provided."""
        with new_trace("parent") as parent, inherit_context(span_name="child") as child:
            assert child.trace_id == parent.trace_id


class TestEnsureContext:
    """Tests for ensure_context decorator."""

    def setup_method(self) -> None:
        """Clear context before each test."""
        ContextManager.clear()

    def teardown_method(self) -> None:
        """Clear context after each test."""
        ContextManager.clear()

    def test_creates_context_when_missing(self) -> None:
        """Decorator creates context if missing."""

        @ensure_context
        def get_tid() -> str:
            return get_trace_id()

        tid = get_tid()
        assert tid.startswith("hp-auto-")

    def test_preserves_existing_context(self) -> None:
        """Decorator preserves existing context."""
        ContextManager.set_context(PolarisContext(trace_id="t1"))

        @ensure_context
        def get_tid() -> str:
            return get_trace_id()

        assert get_tid() == "t1"

    def test_preserves_function_result(self) -> None:
        """Function result is preserved."""

        @ensure_context
        def add(a: int, b: int) -> int:
            return a + b

        assert add(2, 3) == 5


class TestCurrentSpanStack:
    """Tests for _current_span_stack helper."""

    def setup_method(self) -> None:
        """Clear context before each test."""
        ContextManager.clear()

    def teardown_method(self) -> None:
        """Clear context after each test."""
        ContextManager.clear()

    def test_empty_returns_empty_list(self) -> None:
        """Returns empty list when no spans."""
        assert _current_span_stack() == []

    def test_returns_copy(self) -> None:
        """Returns a copy, not the original."""
        ctx = PolarisContext(trace_id="t1", span_stack=[{"span_id": "s1"}])
        ContextManager.set_context(ctx)
        stack = _current_span_stack()
        stack.append({"span_id": "s2"})
        assert len(_current_span_stack()) == 1


class TestCurrentMetadata:
    """Tests for _current_metadata helper."""

    def setup_method(self) -> None:
        """Clear context before each test."""
        ContextManager.clear()

    def teardown_method(self) -> None:
        """Clear context after each test."""
        ContextManager.clear()

    def test_empty_returns_empty_dict(self) -> None:
        """Returns empty dict when no metadata."""
        assert _current_metadata() == {}

    def test_returns_copy(self) -> None:
        """Returns a copy, not the original."""
        ctx = PolarisContext(trace_id="t1", metadata={"a": 1})
        ContextManager.set_context(ctx)
        meta = _current_metadata()
        meta["b"] = 2
        assert _current_metadata() == {"a": 1}


class TestModuleExports:
    """Tests for module public API."""

    def test_all_exports_present(self) -> None:
        """All expected names are importable."""
        from polaris.kernelone.trace import context

        assert hasattr(context, "PolarisContext")
        assert hasattr(context, "ContextManager")
        assert hasattr(context, "get_context")
        assert hasattr(context, "get_trace_id")
        assert hasattr(context, "new_trace")
        assert hasattr(context, "inherit_context")
        assert hasattr(context, "ensure_context")
