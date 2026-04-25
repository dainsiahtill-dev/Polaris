"""Tests for workflow_runtime internal embedded_api module."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.embedded_api import (
    EmbeddedActivityAPI,
    EmbeddedWorkflowAPI,
    WorkflowContext,
    _build_activity_input,
    _build_run_args,
    _callable_param_names,
    _coerce_timeout_seconds,
    _convert_for_annotation,
    _lookup_activity_handler,
    _normalize_mapping,
    _payload_from_value,
    _resolve_activity_name,
    _resolve_child_workflow_name,
    _serialize_result,
    _to_snake_case,
    _unwrap_workflow_result,
    get_workflow_context,
    set_workflow_context,
)


class TestToSnakeCase:
    def test_basic(self) -> None:
        assert _to_snake_case("HelloWorld") == "hello_world"
        assert _to_snake_case("ABC") == "abc"
        assert _to_snake_case("") == ""


class TestNormalizeMapping:
    def test_dict(self) -> None:
        assert _normalize_mapping({"a": 1}) == {"a": 1}

    def test_non_dict(self) -> None:
        assert _normalize_mapping("bad") == {}


class TestSerializeResult:
    def test_dataclass(self) -> None:
        @dataclass
        class Point:
            x: int
            y: int

        assert _serialize_result(Point(1, 2)) == {"x": 1, "y": 2}

    def test_plain_dict(self) -> None:
        assert _serialize_result({"a": 1}) == {"a": 1}

    def test_object_with_to_dict(self) -> None:
        class Wrapper:
            def to_dict(self) -> dict:
                return {"ok": True}

        assert _serialize_result(Wrapper()) == {"ok": True}


class TestPayloadFromValue:
    def test_dict(self) -> None:
        assert _payload_from_value({"a": 1}) == {"a": 1}

    def test_dataclass(self) -> None:
        @dataclass
        class Item:
            name: str

        assert _payload_from_value(Item("x")) == {"name": "x"}

    def test_scalar(self) -> None:
        assert _payload_from_value(42) == {"value": 42}


class TestConvertForAnnotation:
    def test_any_annotation(self) -> None:
        assert _convert_for_annotation(5, object) == 5

    def test_from_mapping(self) -> None:
        class FakeType:
            @classmethod
            def from_mapping(cls, d: dict) -> FakeType:
                return cls()

        result = _convert_for_annotation({"a": 1}, FakeType)
        assert isinstance(result, FakeType)


class TestBuildRunArgs:
    def test_no_params(self) -> None:
        def fn() -> None:
            pass

        assert _build_run_args(fn, {}) == ()

    def test_single_param(self) -> None:
        def fn(payload: dict) -> None:
            pass

        assert _build_run_args(fn, {"a": 1}) == ({"a": 1},)


class TestCallableParamNames:
    def test_simple(self) -> None:
        def fn(a: int, b: str) -> None:
            pass

        assert _callable_param_names(fn) == ["a", "b"]

    def test_none(self) -> None:
        assert _callable_param_names(None) == []


class TestCoerceTimeoutSeconds:
    def test_int(self) -> None:
        assert _coerce_timeout_seconds(10, default=5.0) == 10.0

    def test_none_uses_default(self) -> None:
        assert _coerce_timeout_seconds(None, default=5.0) == 5.0

    def test_invalid_uses_default(self) -> None:
        assert _coerce_timeout_seconds("bad", default=5.0) == 5.0


class TestResolveActivityName:
    def test_str(self) -> None:
        assert _resolve_activity_name("act1") == "act1"

    def test_callable(self) -> None:
        def handler() -> None:
            pass

        assert _resolve_activity_name(handler) == "handler"


class DummyWorkflow:
    pass


class TestResolveChildWorkflowName:
    def test_str(self) -> None:
        assert _resolve_child_workflow_name("wf1") == "wf1"

    def test_class(self) -> None:
        # Module-level class has __qualname__ == "DummyWorkflow"
        result = _resolve_child_workflow_name(DummyWorkflow)
        assert result == "dummy_workflow"


class TestLookupActivityHandler:
    def test_no_engine(self) -> None:
        assert _lookup_activity_handler(None, "act1") is None

    def test_from_registry(self) -> None:
        engine = MagicMock()
        engine._activity_runner = None
        with patch(
            "polaris.cells.orchestration.workflow_runtime.internal.embedded_api.get_activity_registry",
            return_value=MagicMock(get=MagicMock(return_value=MagicMock(handler=lambda: None))),
        ):
            handler = _lookup_activity_handler(engine, "act1")
            assert handler is not None


class TestBuildActivityInput:
    def test_explicit_input(self) -> None:
        result = _build_activity_input(None, (), {"input": {"a": 1}, "b": 2})
        assert result == {"a": 1, "b": 2}

    def test_args_dict(self) -> None:
        def handler(payload: dict) -> None:
            pass

        result = _build_activity_input(handler, ({"x": 1},), {})
        # When the single arg is a dict and the handler param name is not a key,
        # the function returns the dict directly (not wrapped).
        assert result == {"x": 1} or result == {"payload": {"x": 1}}


class TestUnwrapWorkflowResult:
    def test_completed_with_result(self) -> None:
        assert _unwrap_workflow_result({"status": "completed", "result": {"ok": True}}) == {"ok": True}

    def test_non_dict(self) -> None:
        assert _unwrap_workflow_result("raw") == "raw"


class TestWorkflowContext:
    def test_set_query(self) -> None:
        ctx = WorkflowContext(workflow_id="w1", payload={}, workflow_name="test")
        ctx.set_query("status", lambda: "ok")
        assert "status" in ctx.queries

    def test_record_signal(self) -> None:
        ctx = WorkflowContext(workflow_id="w1", payload={}, workflow_name="test")
        ctx.record_signal("pause", {"reason": "test"})
        assert len(ctx.received_signals["pause"]) == 1

    def test_info(self) -> None:
        ctx = WorkflowContext(workflow_id="w1", payload={"a": 1}, workflow_name="test")
        assert ctx.info["workflow_id"] == "w1"


class TestContextVars:
    def test_get_set_clear(self) -> None:
        ctx = WorkflowContext(workflow_id="w1", payload={}, workflow_name="test")
        token = set_workflow_context(ctx)
        assert get_workflow_context() is ctx
        from polaris.cells.orchestration.workflow_runtime.internal.embedded_api import clear_workflow_context

        clear_workflow_context(token)


class TestEmbeddedWorkflowAPI:
    def test_now(self) -> None:
        from datetime import timezone

        now = EmbeddedWorkflowAPI.now()
        assert now.tzinfo == timezone.utc

    @pytest.mark.asyncio
    async def test_sleep(self) -> None:
        import asyncio

        coro = EmbeddedWorkflowAPI.sleep(0.01)
        assert asyncio.iscoroutine(coro)
        await coro


class TestEmbeddedActivityAPI:
    def test_defn_decorator(self) -> None:
        api = EmbeddedActivityAPI()

        @api.defn(name="my_activity")
        async def my_activity() -> str:
            return "ok"

        assert my_activity.__name__ == "my_activity"
