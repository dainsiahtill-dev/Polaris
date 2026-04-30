"""Tests for polaris.delivery.cli.visualization.render_context."""

from __future__ import annotations

from polaris.delivery.cli.visualization.contracts import RenderMode
from polaris.delivery.cli.visualization.render_context import RenderContext, StreamContext


class TestRenderContext:
    def test_defaults(self) -> None:
        ctx = RenderContext()
        assert ctx.mode == RenderMode.INTERACTIVE
        assert ctx.indent == 0
        assert ctx.max_depth == 10
        assert ctx.show_collapsed is True
        assert ctx.collapse_states == {}

    def test_copy(self) -> None:
        ctx = RenderContext(indent=4, collapse_states={"a": True})
        copied = ctx.copy()
        assert copied.indent == 4
        assert copied.collapse_states == {"a": True}
        # Mutating copy doesn't affect original
        copied.collapse_states["b"] = False
        assert "b" not in ctx.collapse_states

    def test_with_indent(self) -> None:
        ctx = RenderContext(indent=2)
        new_ctx = ctx.with_indent(4)
        assert new_ctx.indent == 6
        assert ctx.indent == 2

    def test_with_mode(self) -> None:
        ctx = RenderContext()
        new_ctx = ctx.with_mode(RenderMode.COLLAPSED)
        assert new_ctx.mode == RenderMode.COLLAPSED
        assert ctx.mode == RenderMode.INTERACTIVE

    def test_get_collapse_state_existing(self) -> None:
        ctx = RenderContext(collapse_states={"item1": True})
        assert ctx.get_collapse_state("item1") is True

    def test_get_collapse_state_default(self) -> None:
        ctx = RenderContext()
        assert ctx.get_collapse_state("missing") is False
        assert ctx.get_collapse_state("missing", default=True) is True

    def test_set_collapse_state(self) -> None:
        ctx = RenderContext()
        ctx.set_collapse_state("item1", True)
        assert ctx.collapse_states["item1"] is True

    def test_toggle_collapse_state(self) -> None:
        ctx = RenderContext(collapse_states={"item1": True})
        result = ctx.toggle_collapse_state("item1")
        assert result is False
        assert ctx.collapse_states["item1"] is False

    def test_toggle_collapse_state_new(self) -> None:
        ctx = RenderContext()
        result = ctx.toggle_collapse_state("item1")
        assert result is True

    def test_collapse_by_type(self) -> None:
        ctx = RenderContext(collapse_states={"DEBUG:a": False, "DEBUG:b": False, "USER:a": False})
        ctx.collapse_by_type("DEBUG")
        assert ctx.collapse_states["DEBUG:a"] is True
        assert ctx.collapse_states["DEBUG:b"] is True
        assert ctx.collapse_states["USER:a"] is False

    def test_expand_by_type(self) -> None:
        ctx = RenderContext(collapse_states={"DEBUG:a": True, "USER:a": True})
        ctx.expand_by_type("DEBUG")
        assert ctx.collapse_states["DEBUG:a"] is False
        assert ctx.collapse_states["USER:a"] is True

    def test_collapse_all(self) -> None:
        ctx = RenderContext(collapse_states={"a": False, "b": False})
        ctx.collapse_all()
        assert ctx.collapse_states["a"] is True
        assert ctx.collapse_states["b"] is True

    def test_expand_all(self) -> None:
        ctx = RenderContext(collapse_states={"a": True, "b": True})
        ctx.expand_all()
        assert ctx.collapse_states["a"] is False
        assert ctx.collapse_states["b"] is False

    def test_get_indent_str(self) -> None:
        ctx = RenderContext(indent=4)
        assert ctx.get_indent_str() == "    "


class TestStreamContext:
    def test_defaults(self) -> None:
        ctx = StreamContext()
        assert ctx.buffer == []
        assert ctx.flush_callback is None

    def test_append(self) -> None:
        ctx = StreamContext()
        ctx.append("hello")
        ctx.append(" world")
        assert ctx.buffer == ["hello", " world"]

    def test_flush(self) -> None:
        ctx = StreamContext()
        ctx.append("hello")
        result = ctx.flush()
        assert result == "hello"
        assert ctx.buffer == []

    def test_flush_with_callback(self) -> None:
        calls = []
        ctx = StreamContext(flush_callback=lambda x: calls.append(x))
        ctx.append("hello")
        result = ctx.flush()
        assert result == "hello"
        assert calls == ["hello"]

    def test_context_manager(self) -> None:
        with StreamContext() as ctx:
            ctx.append("hello")
        # flush is called on exit
        assert ctx.buffer == []
