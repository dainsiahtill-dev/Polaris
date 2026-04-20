"""Tests for AuditContext."""

from __future__ import annotations

import pytest
from polaris.kernelone.audit.omniscient.context import (
    AuditContext,
    ThreadAuditContextManager,
    audit_context_manager,
    clear_audit_context,
    clear_thread_audit_context,
    get_current_audit_context,
    get_thread_audit_context,
    set_audit_context,
    set_thread_audit_context,
)


class TestAuditContext:
    """Tests for AuditContext dataclass."""

    def test_create_empty_context(self) -> None:
        """Test creating an empty context."""
        ctx = AuditContext()
        assert ctx.run_id == ""
        assert ctx.turn_id == ""
        assert ctx.task_id == ""
        assert ctx.instance_id == ""
        assert ctx.span_id == ""
        assert ctx.parent_span_id == ""
        assert ctx.user_id == ""
        assert ctx.workspace == ""
        assert ctx.metadata == {}

    def test_create_full_context(self) -> None:
        """Test creating a context with all fields."""
        ctx = AuditContext(
            run_id="run_123",
            turn_id="turn_1",
            task_id="task_456",
            instance_id="agent_789",
            span_id="span_abc",
            parent_span_id="span_parent",
            user_id="user_test",
            workspace="/tmp/test",
            metadata={"key": "value"},
        )
        assert ctx.run_id == "run_123"
        assert ctx.turn_id == "turn_1"
        assert ctx.task_id == "task_456"
        assert ctx.instance_id == "agent_789"
        assert ctx.span_id == "span_abc"
        assert ctx.parent_span_id == "span_parent"
        assert ctx.user_id == "user_test"
        assert ctx.workspace == "/tmp/test"
        assert ctx.metadata == {"key": "value"}

    def test_context_is_immutable(self) -> None:
        """Test that AuditContext is immutable (frozen)."""
        ctx = AuditContext(run_id="run_123")
        with pytest.raises(AttributeError):
            ctx.run_id = "other"  # type: ignore[misc]

    def test_with_span(self) -> None:
        """Test creating new context with span chaining."""
        ctx = AuditContext(
            run_id="run_1",
            turn_id="turn_1",
            task_id="task_1",
            span_id="span_parent",
        )
        new_ctx = ctx.with_span("span_child")

        # New context has new span_id
        assert new_ctx.span_id == "span_child"
        # Parent is preserved
        assert new_ctx.parent_span_id == "span_parent"
        # Other fields preserved
        assert new_ctx.run_id == "run_1"
        assert new_ctx.turn_id == "turn_1"

    def test_with_metadata(self) -> None:
        """Test adding metadata to context."""
        ctx = AuditContext()
        new_ctx = ctx.with_metadata("foo", "bar")
        assert new_ctx.metadata == {"foo": "bar"}
        # Original unchanged
        assert ctx.metadata == {}

    def test_to_dict(self) -> None:
        """Test serialization to dict."""
        ctx = AuditContext(
            run_id="run_1",
            turn_id="turn_1",
            task_id="task_1",
            span_id="span_1",
            workspace="/tmp",
        )
        d = ctx.to_dict()
        assert d["run_id"] == "run_1"
        assert d["turn_id"] == "turn_1"
        assert d["task_id"] == "task_1"
        assert d["span_id"] == "span_1"
        assert d["workspace"] == "/tmp"
        assert isinstance(d["metadata"], dict)


class TestAuditContextManager:
    """Tests for async context manager."""

    @pytest.mark.asyncio
    async def test_context_manager_sets_and_clears(self) -> None:
        """Test async context manager sets and clears context."""
        assert get_current_audit_context() is None

        async with audit_context_manager(run_id="run_1", turn_id="turn_1") as ctx:
            assert ctx is not None
            assert ctx.run_id == "run_1"
            assert ctx.turn_id == "turn_1"
            # Also accessible via getter
            assert get_current_audit_context() is ctx

        # Cleared after exit
        assert get_current_audit_context() is None

    @pytest.mark.asyncio
    async def test_context_manager_restores_previous(self) -> None:
        """Test async context manager restores previous context."""
        outer = AuditContext(run_id="outer_run")
        set_audit_context(outer)

        try:
            async with audit_context_manager(run_id="inner_run") as inner:
                assert get_current_audit_context() is inner
                assert inner.run_id == "inner_run"

            # Restored to outer
            assert get_current_audit_context() is outer
        finally:
            clear_audit_context()

    @pytest.mark.asyncio
    async def test_context_manager_propagates_in_async_tasks(self) -> None:
        """Test that context propagates to child async tasks."""
        context_values: list[str | None] = []

        async with audit_context_manager(run_id="parent_run") as parent_ctx:
            assert parent_ctx.run_id == "parent_run"

            async def child_task() -> None:
                ctx = get_current_audit_context()
                if ctx is not None:
                    context_values.append(ctx.run_id)
                else:
                    context_values.append(None)

            # Run child task
            await child_task()
            # Context should be visible in child
            assert context_values[-1] == "parent_run"

    @pytest.mark.asyncio
    async def test_context_manager_with_metadata(self) -> None:
        """Test context manager with metadata kwargs."""
        async with audit_context_manager(run_id="run_1", custom_field="value") as ctx:
            assert ctx.metadata.get("custom_field") == "value"

    @pytest.mark.asyncio
    async def test_context_manager_nested(self) -> None:
        """Test nested context managers."""
        async with audit_context_manager(run_id="outer", turn_id="1") as outer:
            assert outer.run_id == "outer"

            async with audit_context_manager(run_id="inner", turn_id="2") as inner:
                assert inner.run_id == "inner"
                # Can still access outer via contextvars
                outer_ctx = get_current_audit_context()
                assert outer_ctx is inner

            # Back to outer
            assert get_current_audit_context() is outer


class TestThreadAuditContextManager:
    """Tests for sync thread context manager."""

    def test_sync_context_manager(self) -> None:
        """Test sync context manager sets and clears."""
        assert get_thread_audit_context() is None

        with ThreadAuditContextManager(run_id="sync_run") as ctx:
            assert ctx.run_id == "sync_run"
            assert get_thread_audit_context() is ctx

        assert get_thread_audit_context() is None

    def test_sync_context_manager_restores_previous(self) -> None:
        """Test sync context manager restores previous context."""
        outer = AuditContext(run_id="sync_outer")
        set_thread_audit_context(outer)

        try:
            with ThreadAuditContextManager(run_id="sync_inner") as inner:
                assert get_thread_audit_context() is inner
            assert get_thread_audit_context() is outer
        finally:
            clear_thread_audit_context()


class TestContextVars:
    """Tests for context variable helpers."""

    @pytest.mark.asyncio
    async def test_clear_audit_context(self) -> None:
        """Test clearing context."""
        set_audit_context(AuditContext(run_id="temp"))
        assert get_current_audit_context() is not None
        clear_audit_context()
        assert get_current_audit_context() is None

    def test_clear_thread_audit_context(self) -> None:
        """Test clearing thread context."""
        set_thread_audit_context(AuditContext(run_id="temp"))
        assert get_thread_audit_context() is not None
        clear_thread_audit_context()
        assert get_thread_audit_context() is None
