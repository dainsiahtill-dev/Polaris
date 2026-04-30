"""Tests for polaris.delivery.cli.visualization.contracts."""

from __future__ import annotations

from polaris.delivery.cli.visualization.contracts import (
    RenderMode,
    RenderResult,
    VisualizationContext,
)


class TestRenderMode:
    def test_values(self) -> None:
        assert RenderMode.COLLAPSED.value == "collapsed"
        assert RenderMode.EXPANDED.value == "expanded"
        assert RenderMode.INTERACTIVE.value == "interactive"
        assert RenderMode.SUMMARY.value == "summary"


class TestVisualizationContext:
    def test_defaults(self) -> None:
        ctx = VisualizationContext()
        assert ctx.mode == RenderMode.INTERACTIVE
        assert ctx.show_metadata is True
        assert ctx.show_timestamps is False
        assert ctx.max_content_length == 500
        assert ctx.theme_name == "default"
        assert ctx.collapse_states == {}

    def test_get_fold_state_existing(self) -> None:
        ctx = VisualizationContext(collapse_states={"item1": True})
        assert ctx.get_fold_state("item1") is True

    def test_get_fold_state_default_true(self) -> None:
        ctx = VisualizationContext()
        assert ctx.get_fold_state("missing", default=True) is True

    def test_get_fold_state_default_false(self) -> None:
        ctx = VisualizationContext()
        assert ctx.get_fold_state("missing") is False

    def test_set_fold_state(self) -> None:
        ctx = VisualizationContext()
        ctx.set_fold_state("item1", True)
        assert ctx.collapse_states["item1"] is True

    def test_toggle_fold_state(self) -> None:
        ctx = VisualizationContext(collapse_states={"item1": False})
        result = ctx.toggle_fold_state("item1")
        assert result is True
        assert ctx.collapse_states["item1"] is True

    def test_toggle_fold_state_new(self) -> None:
        ctx = VisualizationContext()
        result = ctx.toggle_fold_state("item1")
        assert result is True

    def test_expand_all(self) -> None:
        ctx = VisualizationContext(collapse_states={"a": True, "b": True})
        ctx.expand_all()
        assert ctx.collapse_states["a"] is False
        assert ctx.collapse_states["b"] is False

    def test_collapse_all(self) -> None:
        ctx = VisualizationContext(collapse_states={"a": False, "b": False})
        ctx.collapse_all()
        assert ctx.collapse_states["a"] is True
        assert ctx.collapse_states["b"] is True


class TestRenderResult:
    def test_defaults(self) -> None:
        result = RenderResult(content="hello")
        assert result.content == "hello"
        assert result.metadata == {}
        assert result.errors == []
        assert result.is_success is True

    def test_is_success_with_errors(self) -> None:
        result = RenderResult(content="hello", errors=["error1"])
        assert result.is_success is False

    def test_add_error(self) -> None:
        result = RenderResult(content="hello")
        result.add_error("error1")
        assert result.errors == ["error1"]
        assert result.is_success is False

    def test_add_multiple_errors(self) -> None:
        result = RenderResult(content="hello")
        result.add_error("error1")
        result.add_error("error2")
        assert len(result.errors) == 2
