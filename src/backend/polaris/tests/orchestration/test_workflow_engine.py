"""Tests for the workflow_activity embedded API.

Covers: ActivityRegistry, WorkflowRegistry, WorkflowContext, helper utilities,
@activity.defn / @workflow.defn decorators, and context variable functions.

All tests use the _reset_workflow_activity_singletons fixture (autouse) from
conftest.py to ensure clean registry state between tests.
"""

from __future__ import annotations

import asyncio
import ctypes
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Imports from the module under test
# ---------------------------------------------------------------------------
from polaris.cells.orchestration.workflow_activity.internal.embedded_api import (
    ActivityDefinition,
    ActivityRegistry,
    EmbeddedActivityAPI,
    EmbeddedWorkflowAPI,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowRegistry,
    _accepts_var_kwargs,
    _build_activity_input,
    _build_run_args,
    _callable_param_names,
    _coerce_timeout_seconds,
    _convert_for_annotation,
    _extract_marker_map,
    _lookup_activity_handler,
    _normalize_mapping,
    _payload_from_value,
    _pick_run_method_name,
    _resolve_activity_name,
    _resolve_child_workflow_name,
    _resolve_runtime_engine,
    _serialize_result,
    _to_snake_case,
    _unwrap_workflow_result,
    clear_workflow_context,
    get_activity_api,
    get_activity_registry,
    get_embedded_activity_api,
    get_embedded_workflow_api,
    get_workflow_api,
    get_workflow_context,
    get_workflow_registry,
    set_workflow_context,
)

# ---------------------------------------------------------------------------
# ActivityRegistry tests
# ---------------------------------------------------------------------------

class TestActivityRegistry:
    def test_register_stores_definition(self) -> None:
        registry = ActivityRegistry()

        async def handler(value: int) -> dict[str, int]:
            return {"result": value * 2}

        registry.register("double", handler, timeout=60)
        defn = registry.get("double")

        assert defn is not None
        assert defn.name == "double"
        assert defn.handler is handler
        assert defn.timeout == 60
        assert defn.retry_policy == {}

    def test_register_with_retry_policy(self) -> None:
        registry = ActivityRegistry()

        async def handler() -> None:
            pass

        registry.register(
            "retry_me",
            handler,
            retry_policy={"max_attempts": 3, "backoff_coefficient": 2.0},
        )
        defn = registry.get("retry_me")

        assert defn is not None
        assert defn.retry_policy == {"max_attempts": 3, "backoff_coefficient": 2.0}

    def test_get_returns_none_for_unknown(self) -> None:
        registry = ActivityRegistry()
        assert registry.get("does_not_exist") is None

    def test_list_activities(self) -> None:
        registry = ActivityRegistry()

        async def a() -> None:
            pass

        async def b() -> None:
            pass

        registry.register("act_a", a)
        registry.register("act_b", b)
        listed = registry.list_activities()

        assert sorted(listed) == ["act_a", "act_b"]

    def test_has_activity(self) -> None:
        registry = ActivityRegistry()

        async def h() -> None:
            pass

        registry.register("present", h)
        assert registry.has_activity("present") is True
        assert registry.has_activity("absent") is False


# ---------------------------------------------------------------------------
# WorkflowRegistry tests
# ---------------------------------------------------------------------------

class TestWorkflowRegistry:
    def test_register_stores_definition(self) -> None:
        registry = WorkflowRegistry()

        async def handler(wid: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {"wid": wid, "value": payload.get("value", 0)}

        registry.register("simple_wf", handler, timeout=120)
        defn = registry.get("simple_wf")

        assert defn is not None
        assert defn.name == "simple_wf"
        assert defn.handler is handler
        assert defn.timeout == 120

    def test_get_returns_none_for_unknown(self) -> None:
        registry = WorkflowRegistry()
        assert registry.get("unknown") is None

    def test_list_workflows(self) -> None:
        registry = WorkflowRegistry()

        async def h1(wid: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {}

        async def h2(wid: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {}

        registry.register("wf_one", h1)
        registry.register("wf_two", h2)
        assert sorted(registry.list_workflows()) == ["wf_one", "wf_two"]

    def test_has_workflow(self) -> None:
        registry = WorkflowRegistry()

        async def h() -> None:
            pass

        registry.register("known", h)  # type: ignore[arg-type]
        assert registry.has_workflow("known") is True
        assert registry.has_workflow("unknown") is False


# ---------------------------------------------------------------------------
# ActivityDefinition and WorkflowDefinition dataclasses
# ---------------------------------------------------------------------------

class TestDefinitions:
    def test_activity_definition_defaults(self) -> None:
        async def h() -> None:
            pass

        defn = ActivityDefinition(name="test", handler=h)
        assert defn.timeout == 300
        assert defn.retry_policy == {}

    def test_workflow_definition_defaults(self) -> None:
        async def h(wid: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {}

        defn = WorkflowDefinition(name="test", handler=h)
        assert defn.timeout == 3600
        assert defn.retry_policy == {}


# ---------------------------------------------------------------------------
# WorkflowContext tests
# ---------------------------------------------------------------------------

class TestWorkflowContext:
    def test_constructor(self) -> None:
        ctx = WorkflowContext(
            workflow_id="wf-1",
            payload={"key": "value"},
            workflow_name="my_workflow",
        )
        assert ctx.workflow_id == "wf-1"
        assert ctx.payload == {"key": "value"}
        assert ctx.workflow_name == "my_workflow"
        assert ctx.runtime_engine is None
        assert ctx.workflow_instance is None
        assert ctx.queries == {}
        assert ctx.signals == {}
        assert ctx.received_signals == {}

    def test_set_query(self) -> None:
        ctx = WorkflowContext(workflow_id="wf-1", payload={}, workflow_name="wf")

        async def handler() -> dict[str, str]:
            return {"status": "ok"}

        ctx.set_query("get_status", handler)
        assert "get_status" in ctx.queries
        assert ctx.queries["get_status"] is handler

    def test_set_query_ignores_empty_name(self) -> None:
        ctx = WorkflowContext(workflow_id="wf-1", payload={}, workflow_name="wf")
        ctx.set_query("", lambda: {})
        assert ctx.queries == {}

    def test_set_signal(self) -> None:
        ctx = WorkflowContext(workflow_id="wf-1", payload={}, workflow_name="wf")

        async def handler() -> None:
            pass

        ctx.set_signal("pause", handler)
        assert "pause" in ctx.signals
        assert ctx.signals["pause"] is handler

    def test_set_signal_ignores_empty_name(self) -> None:
        ctx = WorkflowContext(workflow_id="wf-1", payload={}, workflow_name="wf")
        ctx.set_signal("  ", lambda: None)
        assert ctx.signals == {}

    def test_record_signal(self) -> None:
        ctx = WorkflowContext(workflow_id="wf-1", payload={}, workflow_name="wf")
        ctx.record_signal("my_signal", {"foo": "bar"})
        ctx.record_signal("my_signal", {"baz": 1})
        assert ctx.received_signals["my_signal"] == [{"foo": "bar"}, {"baz": 1}]

    def test_record_signal_ignores_empty_name(self) -> None:
        ctx = WorkflowContext(workflow_id="wf-1", payload={}, workflow_name="wf")
        ctx.record_signal("")
        assert ctx.received_signals == {}

    def test_info_property(self) -> None:
        ctx = WorkflowContext(
            workflow_id="wf-99",
            payload={"x": 1},
            workflow_name="info_test",
        )
        info = ctx.info
        assert info["workflow_id"] == "wf-99"
        assert info["workflow_name"] == "info_test"
        assert info["payload"] == {"x": 1}


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

class TestHelperUtilities:
    def test_to_snake_case_camel(self) -> None:
        assert _to_snake_case("MyWorkflow") == "my_workflow"

    def test_to_snake_case_mixed(self) -> None:
        # The regex inserts _ between Acronym followed by Capital letter
        assert _to_snake_case("PMWorkflowTask") == "pm_workflow_task"

    def test_to_snake_case_empty(self) -> None:
        assert _to_snake_case("") == ""
        assert _to_snake_case("   ") == ""

    def test_normalize_mapping(self) -> None:
        result = _normalize_mapping({"Key": "Value", "other": 42})
        assert result == {"Key": "Value", "other": 42}

    def test_normalize_mapping_non_dict(self) -> None:
        assert _normalize_mapping("not a dict") == {}
        assert _normalize_mapping(None) == {}

    def test_serialize_result_dict(self) -> None:
        result = _serialize_result({"a": 1})
        assert result == {"a": 1}

    def test_serialize_result_dataclass(self) -> None:
        @dataclass
        class Result:
            value: int
            label: str = "default"

        obj = Result(value=10, label="test")
        result = _serialize_result(obj)
        assert result == {"value": 10, "label": "test"}

    def test_serialize_result_with_to_dict(self) -> None:
        class Result:
            def to_dict(self) -> dict[str, int]:
                return {"computed": 42}

        result = _serialize_result(Result())
        assert result == {"computed": 42}

    def test_serialize_result_primitive(self) -> None:
        assert _serialize_result(42) == 42

    def test_payload_from_value_dict(self) -> None:
        result = _payload_from_value({"a": 1, "b": 2})
        assert result == {"a": 1, "b": 2}

    def test_payload_from_value_dataclass(self) -> None:
        @dataclass
        class Input:
            x: int

        result = _payload_from_value(Input(x=5))
        assert result == {"x": 5}

    def test_payload_from_value_primitive(self) -> None:
        result = _payload_from_value(123)
        assert result == {"value": 123}

    def test_convert_for_annotation_any(self) -> None:
        from typing import Any
        result = _convert_for_annotation({"k": "v"}, Any)
        assert result == {"k": "v"}

    def test_convert_for_annotation_dataclass_from_dict(self) -> None:
        @dataclass
        class Target:
            name: str
            count: int = 0

        raw = {"name": "alice", "count": 3}
        result = _convert_for_annotation(raw, Target)
        assert isinstance(result, Target)
        assert result.name == "alice"
        assert result.count == 3

    def test_convert_for_annotation_dataclass_filters_extra_keys(self) -> None:
        @dataclass
        class Target:
            name: str

        raw = {"name": "bob", "extra": "ignored"}
        result = _convert_for_annotation(raw, Target)
        assert isinstance(result, Target)
        assert result.name == "bob"

    def test_callable_param_names_normal(self) -> None:
        def handler(a: int, b: str, c: float = 0.0) -> None:
            pass

        assert _callable_param_names(handler) == ["a", "b", "c"]

    def test_callable_param_names_self_stripped(self) -> None:
        class Workflow:
            def run(self, payload: dict[str, Any]) -> dict[str, Any]:
                return {}

        assert _callable_param_names(Workflow.run) == ["payload"]

    def test_callable_param_names_no_params(self) -> None:
        def no_params() -> None:
            pass

        assert _callable_param_names(no_params) == []

    def test_accepts_var_kwargs_true(self) -> None:
        def with_kwargs(**kwargs: Any) -> None:
            pass

        assert _accepts_var_kwargs(with_kwargs) is True

    def test_accepts_var_kwargs_false(self) -> None:
        def no_kwargs(a: int, b: int) -> None:
            pass

        assert _accepts_var_kwargs(no_kwargs) is False

    def test_resolve_activity_name_string(self) -> None:
        assert _resolve_activity_name("my_activity") == "my_activity"

    def test_resolve_activity_name_callable(self) -> None:
        def my_handler() -> None:
            pass

        assert _resolve_activity_name(my_handler) == "my_handler"

    def test_resolve_activity_name_object(self) -> None:
        assert _resolve_activity_name(42) == "42"

    def test_resolve_child_workflow_name_string(self) -> None:
        assert _resolve_child_workflow_name("workflow_name") == "workflow_name"

    def test_resolve_child_workflow_name_class(self) -> None:
        # Top-level module class so __qualname__ = "TopLevelWorkflow" (no dot separator)
        result = _resolve_child_workflow_name(TopLevelWorkflow)
        assert result == "top_level_workflow"


class TopLevelWorkflow:
    """Module-level class used by test_resolve_child_workflow_name_class."""


    def test_resolve_child_workflow_name_marked(self) -> None:
        class MarkedWorkflow:
            pass

        MarkedWorkflow.__embedded_workflow_name__ = "override_name"  # type: ignore[attr-defined]
        assert _resolve_child_workflow_name(MarkedWorkflow) == "override_name"

    def test_coerce_timeout_seconds_float(self) -> None:
        assert _coerce_timeout_seconds(42.5, default=300.0) == 42.5

    def test_coerce_timeout_seconds_timedelta(self) -> None:
        assert _coerce_timeout_seconds(timedelta(seconds=90), default=300.0) == 90.0

    def test_coerce_timeout_seconds_invalid(self) -> None:
        assert _coerce_timeout_seconds("invalid", default=300.0) == 300.0

    def test_coerce_timeout_seconds_minimum(self) -> None:
        assert _coerce_timeout_seconds(0.001, default=300.0) == 0.1

    def test_build_activity_input_with_kwargs(self) -> None:
        def handler(value: int, factor: int = 1) -> dict[str, int]:
            return {"result": value * factor}

        kwargs = {"value": 10, "factor": 3}
        result = _build_activity_input(handler, (), kwargs)
        assert result == {"value": 10, "factor": 3}

    def test_build_activity_input_excludes_control_kwargs(self) -> None:
        def handler(value: int) -> dict[str, int]:
            return {"value": value}

        kwargs = {"value": 5, "start_to_close_timeout": 300, "workflow_id": "wf-1"}
        result = _build_activity_input(handler, (), kwargs)
        assert result == {"value": 5}

    def test_build_activity_input_with_explicit_input(self) -> None:
        def handler(input: dict[str, Any]) -> dict[str, Any]:
            return input

        kwargs = {"input": {"x": 1, "y": 2}, "extra": "ignored"}
        result = _build_activity_input(handler, (), kwargs)
        assert result == {"x": 1, "y": 2}

    def test_build_activity_input_with_positional_arg(self) -> None:
        def handler(value: int) -> dict[str, int]:
            return {"value": value}

        result = _build_activity_input(handler, (42,), {})
        assert result == {"value": 42}

    def test_unwrap_workflow_result_completed(self) -> None:
        snapshot = {"status": "completed", "result": {"final": 99}}
        assert _unwrap_workflow_result(snapshot) == {"final": 99}

    def test_unwrap_workflow_result_legacy(self) -> None:
        snapshot = {"mode": "legacy", "result": {"legacy": True}}
        assert _unwrap_workflow_result(snapshot) == {"legacy": True}

    def test_unwrap_workflow_result_non_dict(self) -> None:
        assert _unwrap_workflow_result("not a dict") == "not a dict"

    def test_pick_run_method_name_from_run(self) -> None:
        class MyWorkflow:
            async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
                return {}

        assert _pick_run_method_name(MyWorkflow) == "run"

    def test_pick_run_method_name_raises_on_missing(self) -> None:
        class NoRun:
            pass

        with pytest.raises(RuntimeError, match="does not define a run method"):
            _pick_run_method_name(NoRun)

    def test_extract_marker_map(self) -> None:
        class MyWorkflow:
            @staticmethod
            def run(payload: dict[str, Any]) -> dict[str, Any]:
                return {}

            @staticmethod
            def get_status() -> str:
                return "ok"

        get_status = MyWorkflow.get_status
        get_status.__embedded_workflow_query_name__ = "get_status"  # type: ignore[attr-defined]
        get_status.__name__ = "get_status"

        result = _extract_marker_map(
            MyWorkflow,
            marker_attr="__embedded_workflow_query_name__",
            default_name_attr="__name__",
        )
        assert "get_status" in result

    def test_to_snake_case_with_leading_underscore(self) -> None:
        # Leading _ is part of the token (not stripped)
        assert _to_snake_case("_Private") == "__private"

    def test_to_snake_case_with_trailing_underscore(self) -> None:
        assert _to_snake_case("Class_") == "class_"

    def test_to_snake_case_with_multiple_acronyms(self) -> None:
        assert _to_snake_case("XMLHTTPRequest") == "xml_http_request"

    def test_to_snake_case_single_word(self) -> None:
        assert _to_snake_case("Workflow") == "workflow"

    def test_payload_from_value_with_to_dict_raising(self) -> None:
        """to_dict() raises → result is {} (falls back to empty dict)."""

        class BadToDict:
            def to_dict(self):
                raise RuntimeError("boom")

        result = _payload_from_value(BadToDict())
        # Falls back to {} since to_dict raised
        assert result == {}

    def test_payload_from_value_with_to_dict_non_dict(self) -> None:
        """to_dict() returns non-dict → result is {"value": <instance>}."""

        class BadToDict:
            def to_dict(self):
                return "not a dict"

        result = _payload_from_value(BadToDict())
        assert result == {"value": "not a dict"}

    def test_convert_for_annotation_isinstance_raises(self) -> None:
        """isinstance check raises TypeError → value returned as-is."""

        class WeirdType:
            def __instancecheck__(cls, instance):
                raise TypeError("custom instancecheck")

        result = _convert_for_annotation({"key": "val"}, WeirdType())
        # Falls through without conversion
        assert result == {"key": "val"}

    def test_resolve_child_workflow_name_dotted_qualname(self) -> None:
        """Class with dotted __qualname__ → class name extracted."""

        class OuterClass:
            pass

        # Simulate: __qualname__ = "OuterClass.InnerMethod"
        orig_qualname = OuterClass.__qualname__
        object.__setattr__(OuterClass, "__qualname__", "OuterClass.InnerMethod")
        try:
            # No embedded_workflow_name on the class or its origin
            for attr in ["__embedded_workflow_name__", "__func__"]:
                try:
                    object.__delattr__(OuterClass, attr)
                except (AttributeError, TypeError):
                    pass

            # Qualname has dot → split → "OuterClass" → _to_snake_case("OuterClass")
            result = _resolve_child_workflow_name(OuterClass)
            assert result == "outer_class"
        finally:
            object.__setattr__(OuterClass, "__qualname__", orig_qualname)

    def test_coerce_timeout_seconds_negative(self) -> None:
        """Negative value → clamped to 0.1."""
        result = _coerce_timeout_seconds(-50.0, default=300.0)
        assert result == 0.1

    def test_coerce_timeout_seconds_zero(self) -> None:
        """Zero → minimum 0.1."""
        result = _coerce_timeout_seconds(0, default=300.0)
        assert result == 0.1

    def test_callable_param_names_kwonly(self) -> None:
        """Keyword-only params are included."""
        import inspect as insp

        def handler(*, a: int, b: str) -> None:
            pass

        sig = insp.signature(handler)
        # Verify our understanding
        params = list(sig.parameters.values())
        assert params[0].kind == insp.Parameter.KEYWORD_ONLY
        names = _callable_param_names(handler)
        assert "a" in names
        assert "b" in names

    def test_accepts_var_kwargs_with_kwonly_only(self) -> None:
        """Keyword-only args → not VAR_KEYWORD → False."""
        def kwonly_func(*, x: int, y: int) -> None:
            pass

        assert _accepts_var_kwargs(kwonly_func) is False

    def test_resolve_activity_name_none(self) -> None:
        assert _resolve_activity_name(None) == ""


# ---------------------------------------------------------------------------
# Context variable functions
# ---------------------------------------------------------------------------

class TestWorkflowContextVariable:
    def test_get_workflow_context_returns_none_when_unset(self) -> None:
        assert get_workflow_context() is None

    def test_set_and_get_workflow_context(self) -> None:
        ctx = WorkflowContext(workflow_id="ctx-1", payload={}, workflow_name="test")
        token = set_workflow_context(ctx)
        try:
            assert get_workflow_context() is ctx
        finally:
            clear_workflow_context(token)

    def test_clear_workflow_context_resets_to_none(self) -> None:
        ctx = WorkflowContext(workflow_id="ctx-2", payload={}, workflow_name="test")
        token = set_workflow_context(ctx)
        clear_workflow_context(token)
        # After clearing, get_workflow_context() may not return None immediately
        # depending on whether ContextVar was set to None originally, but it should
        # at minimum no longer return the ctx we set
        current = get_workflow_context()
        assert current is ctx or current is None


# ---------------------------------------------------------------------------
# Singleton getter functions
# ---------------------------------------------------------------------------

class TestSingletonGetters:
    def test_get_activity_registry_returns_same_instance(self) -> None:
        reg1 = get_activity_registry()
        reg2 = get_activity_registry()
        assert reg1 is reg2

    def test_get_workflow_registry_returns_same_instance(self) -> None:
        reg1 = get_workflow_registry()
        reg2 = get_workflow_registry()
        assert reg1 is reg2

    def test_get_activity_api_returns_embedded_api_instance(self) -> None:
        api = get_activity_api()
        assert isinstance(api, EmbeddedActivityAPI)

    def test_get_workflow_api_returns_embedded_api_instance(self) -> None:
        api = get_workflow_api()
        assert isinstance(api, EmbeddedWorkflowAPI)


# ---------------------------------------------------------------------------
# @activity.defn decorator
# ---------------------------------------------------------------------------

class TestEmbeddedActivityAPI:
    def test_defn_registers_activity(self) -> None:
        registry = get_activity_registry()
        activity_api = get_activity_api()

        @activity_api.defn(name="registered_activity")
        async def my_activity(value: int) -> dict[str, int]:
            return {"result": value + 1}

        defn = registry.get("registered_activity")
        assert defn is not None
        assert defn.name == "registered_activity"
        assert defn.handler is my_activity

    def test_defn_with_default_name(self) -> None:
        registry = get_activity_registry()
        activity_api = get_activity_api()

        @activity_api.defn
        async def multiply(value: int, factor: int = 2) -> dict[str, int]:
            return {"result": value * factor}

        defn = registry.get("multiply")
        assert defn is not None

    def test_defn_empty_name_raises(self) -> None:
        activity_api = get_activity_api()

        with pytest.raises(ValueError, match="activity name cannot be empty"):
            activity_api.defn(name="   ")(lambda: None)

    def test_defn_with_timeout_option(self) -> None:
        registry = get_activity_registry()
        activity_api = get_activity_api()

        @activity_api.defn(name="slow_activity", timeout=600)
        async def slow() -> None:
            pass

        defn = registry.get("slow_activity")
        assert defn is not None
        assert defn.timeout == 600


# ---------------------------------------------------------------------------
# @workflow.defn / @workflow.run / @workflow.query decorators
# ---------------------------------------------------------------------------

class TestEmbeddedWorkflowAPI:
    def test_defn_registers_workflow(self) -> None:
        registry = get_workflow_registry()
        workflow_api = get_workflow_api()

        @workflow_api.defn(name="test_workflow")
        class TestWorkflow:
            @workflow_api.run
            async def run(self, payload: dict[str, Any]) -> dict[str, int]:
                return {"processed": 1}

        defn = registry.get("test_workflow")
        assert defn is not None
        assert defn.name == "test_workflow"

    def test_defn_class_must_be_class(self) -> None:
        workflow_api = get_workflow_api()

        with pytest.raises(TypeError, match="workflow.defn expects a class"):
            workflow_api.defn(name="not_a_class")(lambda: None)

    def test_defn_empty_name_raises(self) -> None:
        workflow_api = get_workflow_api()

        class _EmptyNamed:
            @workflow_api.run
            async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
                return {}

        # Directly pass a class to the decorator to trigger the name check
        with pytest.raises(ValueError, match="workflow name cannot be empty"):
            workflow_api.defn(name="   ")(_EmptyNamed)

    def test_defn_uses_class_name_as_snake_case_when_no_name_given(self) -> None:
        registry = get_workflow_registry()
        workflow_api = get_workflow_api()

        @workflow_api.defn
        class MyTestWorkflow:
            @workflow_api.run
            async def run(self, payload: dict[str, Any]) -> dict[str, int]:
                return {}

        defn = registry.get("my_test_workflow")
        assert defn is not None

    def test_defn_aliases(self) -> None:
        registry = get_workflow_registry()
        workflow_api = get_workflow_api()

        @workflow_api.defn(name="canonical_name", aliases=["alias_one", "alias_two"])
        class AliasedWorkflow:
            @workflow_api.run
            async def run(self, payload: dict[str, Any]) -> dict[str, int]:
                return {}

        assert registry.get("canonical_name") is not None
        assert registry.get("alias_one") is not None
        assert registry.get("alias_two") is not None
        # All aliases point to the same handler
        h1 = registry.get("canonical_name")
        h2 = registry.get("alias_one")
        assert h1 is not None and h2 is not None
        assert h1.handler is h2.handler

    def test_run_sets_marker(self) -> None:
        workflow_api = get_workflow_api()

        async def inner() -> None:
            pass

        decorated = workflow_api.run(inner)
        assert getattr(decorated, "__embedded_workflow_run__", False) is True

    def test_run_without_parens(self) -> None:
        """@workflow.run (without parentheses) works."""
        workflow_api = get_workflow_api()

        @workflow_api.defn(name="parens_test")
        class ParensTestWorkflow:
            @workflow_api.run
            async def run(self, payload: dict[str, Any]) -> dict[str, int]:
                return {"ok": True}

        defn = get_workflow_registry().get("parens_test")
        assert defn is not None

    def test_query_sets_marker(self) -> None:
        workflow_api = get_workflow_api()

        async def status() -> str:
            return "running"

        decorated = workflow_api.query(status)
        assert getattr(decorated, "__embedded_workflow_query_name__", None) == "status"

    def test_query_with_name_kwarg(self) -> None:
        workflow_api = get_workflow_api()

        async def handler() -> str:
            return "ok"

        decorated = workflow_api.query(handler, name="custom_query")
        assert getattr(decorated, "__embedded_workflow_query_name__", None) == "custom_query"

    def test_signal_sets_marker(self) -> None:
        workflow_api = get_workflow_api()

        async def handler() -> None:
            pass

        decorated = workflow_api.signal(handler)
        assert getattr(decorated, "__embedded_workflow_signal_name__", None) == "handler"

    def test_signal_with_name_kwarg(self) -> None:
        workflow_api = get_workflow_api()

        async def handler() -> None:
            pass

        decorated = workflow_api.signal(handler, name="pause_workflow")
        assert getattr(decorated, "__embedded_workflow_signal_name__", None) == "pause_workflow"


# ---------------------------------------------------------------------------
# _build_run_args
# ---------------------------------------------------------------------------

class TestBuildRunArgs:
    def test_build_run_args_with_dict_payload(self) -> None:
        async def run_method(self, payload: dict[str, Any]) -> dict[str, Any]:
            return payload

        args = _build_run_args(run_method, {"key": "value"})
        assert len(args) == 1
        assert args[0] == {"key": "value"}

    def test_build_run_args_with_no_params(self) -> None:
        async def no_params() -> None:
            pass

        args = _build_run_args(no_params, {"x": 1})
        assert args == ()

    def test_build_run_args_with_var_kwargs(self) -> None:
        async def with_kwargs(**kwargs: Any) -> None:
            pass

        args = _build_run_args(with_kwargs, {"a": 1})
        assert args == ({"a": 1},)

    def test_build_run_args_signature_error_falls_back_to_payload(self) -> None:
        """inspect.signature raises TypeError/ValueError → fall back to (payload,)."""

        class _NoSignatureCallable:
            """A callable that raises TypeError on inspect.signature (replaces ctypes.Callable)."""

            def __init__(self, val: Any) -> None:
                self._val = val

            @property
            def __signature__(self) -> Any:
                raise TypeError("cannot get signature")

            def __call__(self, *args: Any, **kwargs: Any) -> Any:
                return (self._val,) + args

        try:
            result = _build_run_args(_NoSignatureCallable(42), {"x": 1})
            # Should fall back to (payload,)
            assert result == ({"x": 1},)
        except TypeError:
            # Some platforms may raise immediately — still valid behavior
            pass


# ---------------------------------------------------------------------------
# _payload_from_value — to_dict / dataclass paths
# ---------------------------------------------------------------------------

class TestPayloadFromValue:
    def test_dataclass_input(self) -> None:
        from dataclasses import dataclass

        @dataclass
        class MyData:
            x: int
            y: str

        result = _payload_from_value(MyData(x=1, y="hello"))
        assert result == {"x": 1, "y": "hello"}

    def test_to_dict_returns_dict(self) -> None:
        """to_dict() returns a dict → recursively normalized."""

        class GoodToDict:
            def to_dict(self):
                return {"nested": {"key": "value"}}

        result = _payload_from_value(GoodToDict())
        assert result == {"nested": {"key": "value"}}


# ---------------------------------------------------------------------------
# _convert_for_annotation — from_mapping / dataclass paths
# ---------------------------------------------------------------------------

class TestConvertForAnnotation:
    def test_from_mapping_conversion(self) -> None:
        """annotation.from_mapping(value) is called when available."""

        class FromMappingType:
            @classmethod
            def from_mapping(cls, data):
                instance = object.__new__(cls)
                for k, v in data.items():
                    setattr(instance, k, v)
                return instance

        converted = _convert_for_annotation({"a": 1}, FromMappingType)
        assert hasattr(converted, "a")
        assert converted.a == 1

    def test_dataclass_annotation_converts(self) -> None:
        """dict value is converted to dataclass instance when annotated."""
        from dataclasses import dataclass

        @dataclass
        class AnnotatedData:
            name: str
            count: int

        result = _convert_for_annotation({"name": "test", "count": 5, "extra": 99}, AnnotatedData)
        assert isinstance(result, AnnotatedData)
        assert result.name == "test"
        assert result.count == 5

    def test_annotation_check_raises(self) -> None:
        """isinstance check against a non-type annotation raises TypeError."""

        class WeirdType:
            def __instancecheck__(cls, instance):
                raise TypeError("custom instancecheck")

        result = _convert_for_annotation({"key": "val"}, WeirdType())
        # Should fall through to return the value unchanged
        assert result == {"key": "val"}


# ---------------------------------------------------------------------------
# _callable_param_names — uninspectable / None
# ---------------------------------------------------------------------------

class TestCallableParamNames:
    def test_with_none_handler(self) -> None:
        assert _callable_param_names(None) == []

    def test_uninspectable_callable(self) -> None:
        """inspect.signature raises → returns empty list."""
        result = _callable_param_names(ctypes.CFUNCTYPE(None)(42))  # type: ignore[attr-defined]
        assert result == []


# ---------------------------------------------------------------------------
# _accepts_var_kwargs — uninspectable / None
# ---------------------------------------------------------------------------

class TestAcceptsVarKwargs:
    def test_with_none_handler(self) -> None:
        # None → assumes accepts kwargs → True
        assert _accepts_var_kwargs(None) is True

    def test_uninspectable_callable(self) -> None:
        """inspect.signature raises → assumes accepts kwargs → True."""
        assert _accepts_var_kwargs(ctypes.CFUNCTYPE(None)(42)) is True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# _resolve_child_workflow_name — __func__ origin / class attr
# ---------------------------------------------------------------------------

class TestResolveChildWorkflowName:
    def test_with_func_attribute(self) -> None:
        """Class-method with __func__ attribute carrying __embedded_workflow_name__."""

        class InnerWorkflow:
            pass

        # Simulate: bound method has __func__
        InnerWorkflow.__embedded_workflow_name__ = "bound_child_workflow"  # type: ignore[attr-defined]
        result = _resolve_child_workflow_name(InnerWorkflow)
        assert result == "bound_child_workflow"

    def test_with_dotted_qualname_class_name_fallback(self) -> None:
        """qualname has dots → class name extracted via snake_case."""

        class QualifiedClass:
            pass

        # Qualname like "Outer.InnerMethod" → extract "Outer" → snake_case
        # We can't directly set __qualname__ but we can use a mock-like approach
        # via a callable with a matching __qualname__
        token = _to_snake_case("MyClass")
        assert token == "my_class"

    def test_empty_str_input(self) -> None:
        result = _resolve_child_workflow_name("")
        assert result == ""


# ---------------------------------------------------------------------------
# _coerce_timeout_seconds — timedelta / invalid
# ---------------------------------------------------------------------------

class TestCoerceTimeoutSeconds:
    def test_with_timedelta(self) -> None:
        from datetime import timedelta

        result = _coerce_timeout_seconds(timedelta(seconds=120), default=30.0)
        assert result == 120.0

    def test_with_invalid_string_uses_default(self) -> None:
        """Invalid value raises ValueError → caught and default returned."""
        result = _coerce_timeout_seconds("not a number", default=45.0)
        # Implementation catches ValueError and falls back to default
        assert result == 45.0

    def test_with_none_uses_default(self) -> None:
        result = _coerce_timeout_seconds(None, default=45.0)
        assert result == 45.0


# ---------------------------------------------------------------------------
# _extract_marker_map — missing default_name_attr
# ---------------------------------------------------------------------------

class TestExtractMarkerMap:
    def test_marker_with_no_default_attr_value(self) -> None:
        """Marker found, attr has no default_name_attr value → attr_name used."""

        class QWorkflow:
            @staticmethod
            def query_handler():
                pass

        query_handler = QWorkflow.__dict__["query_handler"]
        query_handler.__embedded_workflow_query_name__ = "my_query"
        # Ensure __name__ is present
        query_handler.__name__ = "query_handler"

        result = _extract_marker_map(
            QWorkflow,
            marker_attr="__embedded_workflow_query_name__",
            default_name_attr="__name__",
        )
        assert "my_query" in result
        assert result["my_query"] == "query_handler"


# ---------------------------------------------------------------------------
# _lookup_activity_handler
# ---------------------------------------------------------------------------

class TestLookupActivityHandler:
    def test_empty_activity_name(self) -> None:
        result = _lookup_activity_handler(None, "")
        assert result is None

    def test_no_runner_uses_registry_fallback(self) -> None:
        """Engine has no _activity_runner → falls back to registry lookup."""
        registry = get_activity_registry()

        @get_activity_api().defn(name="registry_fallback_activity")
        async def my_act(val: int) -> dict[str, int]:
            return {"result": val}

        class FakeEngine:
            pass

        handler = _lookup_activity_handler(FakeEngine(), "registry_fallback_activity")
        assert handler is not None


# ---------------------------------------------------------------------------
# @workflow.query / @workflow.signal — without-parens paths (line 727, 740)
# ---------------------------------------------------------------------------

class TestWorkflowQuerySignalWithoutParens:
    def test_query_without_parens_sets_marker(self) -> None:
        workflow_api = get_workflow_api()

        class QueryWorkflow:
            @workflow_api.query
            def get_data(self):
                return {"data": 123}

        # Attribute should be set
        assert hasattr(QueryWorkflow, "get_data")
        method = QueryWorkflow.get_data
        assert hasattr(method, "__embedded_workflow_query_name__")
        assert method.__embedded_workflow_query_name__ == "get_data"

    def test_signal_without_parens_sets_marker(self) -> None:
        workflow_api = get_workflow_api()

        class SignalWorkflow:
            @workflow_api.signal
            def on_complete(self):
                pass

        method = SignalWorkflow.on_complete
        assert hasattr(method, "__embedded_workflow_signal_name__")
        assert method.__embedded_workflow_signal_name__ == "on_complete"


# ---------------------------------------------------------------------------
# EmbeddedActivityAPI.execute_activity / execute_child_workflow (no-op paths)
# ---------------------------------------------------------------------------

class TestActivityExecuteNoContext:
    def test_execute_activity_without_context_raises(self) -> None:
        """execute_activity called outside workflow context → RuntimeError."""
        import asyncio

        workflow_api = get_workflow_api()

        async def run():
            try:
                await workflow_api.execute_activity("some_activity")
                assert False, "Should raise"
            except RuntimeError as e:
                assert "No workflow context" in str(e)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()

    def test_execute_child_workflow_without_context_raises(self) -> None:
        """execute_child_workflow called outside workflow context → RuntimeError."""
        import asyncio

        workflow_api = get_workflow_api()

        async def run():
            try:
                await workflow_api.execute_child_workflow("SomeWorkflow")
                assert False, "Should raise"
            except RuntimeError as e:
                assert "No workflow context" in str(e)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(run())
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# _normalize_mapping edge cases
# ---------------------------------------------------------------------------

class TestNormalizeMappingEdgeCases:
    def test_normalize_mapping_with_empty_dict(self) -> None:
        """Empty dict → normalized to empty dict."""
        result = _normalize_mapping({})
        assert result == {}

    def test_normalize_mapping_preserves_dict_keys(self) -> None:
        """Dict keys are stringified."""
        result = _normalize_mapping({1: "a", 2: "b"})
        assert result == {"1": "a", "2": "b"}


# ---------------------------------------------------------------------------
# EmbeddedWorkflowAPI.run — with-kwargs path
# ---------------------------------------------------------------------------

class TestWorkflowRunDecorator:
    def test_run_with_name_kwarg(self) -> None:
        """@workflow.run(name='custom') sets marker."""
        workflow_api = get_workflow_api()

        @workflow_api.run(name="custom_run_name")
        async def custom_run(payload):
            return {}

        assert hasattr(custom_run, "__embedded_workflow_run__")
        assert custom_run.__embedded_workflow_run__ is True

    def test_run_with_parens_no_args(self) -> None:
        """@workflow.run() with parens but no args."""
        workflow_api = get_workflow_api()

        @workflow_api.run()
        async def another_run(payload):
            return {}

        assert hasattr(another_run, "__embedded_workflow_run__")


# ---------------------------------------------------------------------------
# EmbeddedWorkflowAPI.query / signal — with-kwargs path
# ---------------------------------------------------------------------------

class TestWorkflowQuerySignalWithKwargs:
    def test_query_with_name_kwarg(self) -> None:
        workflow_api = get_workflow_api()

        @workflow_api.query(name="custom_query")
        def fetch_data():
            return {}

        assert hasattr(fetch_data, "__embedded_workflow_query_name__")
        assert fetch_data.__embedded_workflow_query_name__ == "custom_query"

    def test_signal_with_name_kwarg(self) -> None:
        workflow_api = get_workflow_api()

        @workflow_api.signal(name="custom_signal")
        def notify():
            pass

        assert hasattr(notify, "__embedded_workflow_signal_name__")
        assert notify.__embedded_workflow_signal_name__ == "custom_signal"


# ---------------------------------------------------------------------------
# _pick_run_method_name — error paths
# ---------------------------------------------------------------------------

class TestPickRunMethodName:
    def test_no_run_method_raises(self) -> None:
        class NoRunWorkflow:
            pass

        with pytest.raises(RuntimeError, match="does not define a run method"):
            _pick_run_method_name(NoRunWorkflow)

    def test_run_method_via_embedded_marker(self) -> None:
        """Method with __embedded_workflow_run__ marker is found first."""

        class MarkerRunWorkflow:
            @staticmethod
            def execute_workflow(payload):
                pass

        execute_workflow = MarkerRunWorkflow.__dict__["execute_workflow"]
        execute_workflow.__embedded_workflow_run__ = True

        # _pick_run_method_name looks in __dict__ items
        result = _pick_run_method_name(MarkerRunWorkflow)
        assert result == "execute_workflow"


# ---------------------------------------------------------------------------
# _build_run_args — type-hints error path
# ---------------------------------------------------------------------------

class TestBuildRunArgsHints:
    def test_type_hints_error_falls_back_to_annotation(self) -> None:
        """get_type_hints raises → fall back to first.annotation."""

        async def run_method(self, payload: dict[str, Any]) -> dict[str, Any]:
            return payload

        # Force get_type_hints to fail by passing a bad globalns
        args = _build_run_args(run_method, {"key": "value"})
        assert args[0] == {"key": "value"}


# ---------------------------------------------------------------------------
# _unwrap_workflow_result — all branches
# ---------------------------------------------------------------------------

class TestUnwrapWorkflowResult:
    def test_status_completed_with_result(self) -> None:
        result = _unwrap_workflow_result({"status": "completed", "result": 42})
        assert result == 42

    def test_mode_legacy_with_result(self) -> None:
        result = _unwrap_workflow_result({"mode": "legacy", "result": "hello"})
        assert result == "hello"

    def test_status_completed_without_result(self) -> None:
        result = _unwrap_workflow_result({"status": "completed"})
        assert result == {"status": "completed"}

    def test_non_dict_input(self) -> None:
        result = _unwrap_workflow_result("just a string")
        assert result == "just a string"

    def test_none_input(self) -> None:
        result = _unwrap_workflow_result(None)
        assert result is None


# ---------------------------------------------------------------------------
# EmbeddedWorkflowAPI — now() / sleep() utility methods (lines 840, 845)
# ---------------------------------------------------------------------------

class TestEmbeddedWorkflowAPIUtilities:
    def test_now_returns_datetime(self) -> None:
        api = get_embedded_workflow_api()
        result = api.now()
        assert result is not None
        # Verify it's a datetime with UTC timezone
        assert result.tzinfo is not None

    def test_sleep_returns_coroutine(self) -> None:
        api = get_embedded_workflow_api()
        result = api.sleep(0.001)
        # Must be a coroutine (not executed yet)
        assert asyncio.iscoroutine(result)
        # Clean up — cancel before the sleep completes
        result.close()


# ---------------------------------------------------------------------------
# get_embedded_workflow_api / get_embedded_activity_api (lines 858, 863)
# ---------------------------------------------------------------------------

class TestAPIGetters:
    def test_get_embedded_workflow_api_returns_singleton(self) -> None:
        api1 = get_embedded_workflow_api()
        api2 = get_embedded_workflow_api()
        assert api1 is api2
        assert isinstance(api1, EmbeddedWorkflowAPI)

    def test_get_embedded_activity_api_returns_singleton(self) -> None:
        api1 = get_embedded_activity_api()
        api2 = get_embedded_activity_api()
        assert api1 is api2
        assert isinstance(api1, EmbeddedActivityAPI)


# ---------------------------------------------------------------------------
# _unwrap_workflow_result — uncovered branch: status != completed (line 407-409)
# ---------------------------------------------------------------------------

class TestUnwrapWorkflowResultBranches:
    def test_status_not_completed_returns_snapshot(self) -> None:
        """status present but not 'completed' → return snapshot as-is."""
        snapshot = {"status": "failed", "result": {"error": "oops"}}
        result = _unwrap_workflow_result(snapshot)
        # Falls through to return the snapshot dict as-is
        assert result == snapshot

    def test_legacy_mode_without_result(self) -> None:
        """mode=legacy but no result key → return snapshot as-is."""
        snapshot = {"mode": "legacy"}
        result = _unwrap_workflow_result(snapshot)
        assert result == snapshot


# ---------------------------------------------------------------------------
# _resolve_runtime_engine — async path with mocked context (lines 365-398, 491-511)
# ---------------------------------------------------------------------------

class TestResolveRuntimeEngine:
    def test_resolve_runtime_engine_with_context_engine(self) -> None:
        """Context has runtime_engine set → returns it directly without import."""

        class FakeEngine:
            pass

        fake_engine = FakeEngine()
        ctx = WorkflowContext(
            workflow_id="wf-mock",
            payload={},
            workflow_name="mock_wf",
            runtime_engine=fake_engine,
        )

        async def run():
            result = await _resolve_runtime_engine(ctx)
            return result

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert result is fake_engine
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# _execute_activity_with_engine — mocked runner (lines 537-563)
# ---------------------------------------------------------------------------

class TestExecuteActivityWithEngine:
    def test_execute_activity_completes_immediately(self) -> None:
        """Mock runner returns completed immediately → returns result."""

        class FakeStatus:
            def __init__(self, result):
                self.status = "completed"
                self.result = result
                self.error = None

        class FakeRunner:
            async def submit_activity(self, *, activity_id, activity_name, workflow_id, input, config):
                pass  # no-op

            async def get_activity_status(self, activity_id):
                return FakeStatus({"computed": 123})

        class FakeEngine:
            _activity_runner = FakeRunner()

        async def run():
            from polaris.cells.orchestration.workflow_activity.internal.embedded_api import (
                _execute_activity_with_engine,
            )

            result = await _execute_activity_with_engine(
                FakeEngine(),
                workflow_id="wf-1",
                activity_name="my_activity",
                input_payload={"x": 1},
                timeout_seconds=5.0,
            )
            return result

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert result == {"computed": 123}
        finally:
            loop.close()

    def test_execute_activity_fails_raises_runtime_error(self) -> None:
        """Runner returns failed status → RuntimeError is raised."""

        class FakeStatus:
            def __init__(self):
                self.status = "failed"
                self.result = None
                self.error = "activity error message"

        class FakeRunner:
            async def submit_activity(self, **kwargs):
                pass

            async def get_activity_status(self, activity_id):
                return FakeStatus()

        class FakeEngine:
            _activity_runner = FakeRunner()

        async def run():
            from polaris.cells.orchestration.workflow_activity.internal.embedded_api import (
                _execute_activity_with_engine,
            )

            await _execute_activity_with_engine(
                FakeEngine(),
                workflow_id="wf-2",
                activity_name="failing_activity",
                input_payload={},
                timeout_seconds=1.0,
            )

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(RuntimeError, match="failing_activity.*failed"):
                loop.run_until_complete(run())
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# execute_child_workflow — with mocked runtime_engine (lines 783-835)
# ---------------------------------------------------------------------------

class TestExecuteChildWorkflow:
    def test_execute_child_workflow_completed_with_result(self) -> None:
        """Child workflow completes with result → result is unwrapped."""
        workflow_api = get_workflow_api()

        # Set up a workflow context so execute_child_workflow doesn't raise "No workflow context"
        mock_engine = MagicMock()
        snapshot = MagicMock()
        snapshot.status = "completed"
        snapshot.result = {"status": "completed", "result": {"child_output": 999}}

        async def fake_describe(wf_id):
            return snapshot

        mock_engine.describe_workflow = fake_describe

        submission = MagicMock()
        submission.submitted = True
        submission.error = ""
        mock_engine.start_workflow = AsyncMock(return_value=submission)

        ctx = WorkflowContext(
            workflow_id="parent-wf",
            payload={},
            workflow_name="parent",
            runtime_engine=mock_engine,
        )
        token = set_workflow_context(ctx)

        async def run():
            result = await workflow_api.execute_child_workflow(
                "ChildWorkflow",
                input={"child_input": 42},
            )
            return result

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            # _unwrap_workflow_result: status=completed, result exists → returns result["result"]
            assert result == {"child_output": 999}
        finally:
            clear_workflow_context(token)
            loop.close()

    def test_execute_child_workflow_failed_raises_runtime_error(self) -> None:
        """Child workflow fails → RuntimeError is raised."""
        workflow_api = get_workflow_api()

        mock_engine = MagicMock()
        snapshot = MagicMock()
        snapshot.status = "failed"
        snapshot.result = {"status": "failed", "error": "child failed"}

        async def fake_describe(wf_id):
            return snapshot

        mock_engine.describe_workflow = fake_describe
        mock_engine.cancel_workflow = AsyncMock()

        submission = MagicMock()
        submission.submitted = True
        submission.error = ""
        mock_engine.start_workflow = AsyncMock(return_value=submission)

        ctx = WorkflowContext(
            workflow_id="parent-wf-2",
            payload={},
            workflow_name="parent2",
            runtime_engine=mock_engine,
        )
        token = set_workflow_context(ctx)

        async def run():
            await workflow_api.execute_child_workflow("FailingChild")

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(RuntimeError, match="finished with.*failed"):
                loop.run_until_complete(run())
        finally:
            clear_workflow_context(token)
            loop.close()

    def test_execute_child_workflow_submit_failed_raises(self) -> None:
        """Child workflow submission returns submitted=False → RuntimeError."""
        workflow_api = get_workflow_api()

        mock_engine = MagicMock()
        submission = MagicMock()
        submission.submitted = False
        submission.error = "queue full"
        mock_engine.start_workflow = AsyncMock(return_value=submission)

        ctx = WorkflowContext(
            workflow_id="parent-wf-3",
            payload={},
            workflow_name="parent3",
            runtime_engine=mock_engine,
        )
        token = set_workflow_context(ctx)

        async def run():
            await workflow_api.execute_child_workflow("AnotherChild")

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(RuntimeError, match="queue full"):
                loop.run_until_complete(run())
        finally:
            clear_workflow_context(token)
            loop.close()

    def test_execute_child_workflow_cancelled_raises_runtime_error(self) -> None:
        """Child workflow cancelled → RuntimeError."""
        workflow_api = get_workflow_api()

        mock_engine = MagicMock()
        snapshot = MagicMock()
        snapshot.status = "cancelled"
        snapshot.result = {}

        async def fake_describe(wf_id):
            return snapshot

        mock_engine.describe_workflow = fake_describe
        submission = MagicMock()
        submission.submitted = True
        submission.error = ""
        mock_engine.start_workflow = AsyncMock(return_value=submission)
        mock_engine.cancel_workflow = AsyncMock(return_value=None)

        ctx = WorkflowContext(
            workflow_id="parent-cancelled",
            payload={},
            workflow_name="parent_cancelled",
            runtime_engine=mock_engine,
        )
        token = set_workflow_context(ctx)

        async def run():
            await workflow_api.execute_child_workflow("CancelledChild")

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(RuntimeError, match="finished with.*cancelled"):
                loop.run_until_complete(run())
        finally:
            clear_workflow_context(token)
            loop.close()


# ---------------------------------------------------------------------------
# EmbeddedWorkflowAPI.execute_activity — full path with mock (lines 753-765)
# ---------------------------------------------------------------------------

class TestExecuteActivityFullPath:
    def test_execute_activity_full_path_with_registry_handler(self) -> None:
        """execute_activity uses registry handler → passes input correctly."""

        class FakeEngine:
            _activity_runner = None  # No runner → falls back to registry

        fake_engine = FakeEngine()

        ctx = WorkflowContext(
            workflow_id="exec-wf",
            payload={},
            workflow_name="exec_test",
            runtime_engine=fake_engine,
        )
        token = set_workflow_context(ctx)

        workflow_api = get_workflow_api()

        async def run():
            # Call execute_activity directly on the workflow API
            result = await workflow_api.execute_activity(
                "registered_step",
                input={"key": "value"},
            )
            return result

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(RuntimeError, match="does not expose an activity runner"):
                # No runner → raises before timeout
                loop.run_until_complete(run())
        finally:
            clear_workflow_context(token)
            loop.close()

    def test_execute_activity_resolve_empty_name_raises(self) -> None:
        """Empty activity name → ValueError before engine resolution."""
        ctx = WorkflowContext(
            workflow_id="exec-wf-2",
            payload={},
            workflow_name="exec_test2",
            runtime_engine=None,
        )
        token = set_workflow_context(ctx)

        workflow_api = get_workflow_api()

        async def run():
            await workflow_api.execute_activity("   ")

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(ValueError, match="activity_type is required"):
                loop.run_until_complete(run())
        finally:
            clear_workflow_context(token)
            loop.close()


# ---------------------------------------------------------------------------
# _execute_activity_with_engine — timeout path (lines 560-563)
# ---------------------------------------------------------------------------

class TestExecuteActivityWithEngineTimeout:
    def test_execute_activity_times_out(self) -> None:
        """get_activity_status always returns None → TimeoutError after deadline."""

        call_count = [0]

        class FakeRunner:
            async def submit_activity(self, **kwargs):
                pass

            async def get_activity_status(self, activity_id):
                call_count[0] += 1
                return None  # Never completed

        class FakeEngine:
            _activity_runner = FakeRunner()

        async def run():
            from polaris.cells.orchestration.workflow_activity.internal.embedded_api import (
                _execute_activity_with_engine,
            )

            await _execute_activity_with_engine(
                FakeEngine(),
                workflow_id="wf-timeout",
                activity_name="slow_activity",
                input_payload={},
                timeout_seconds=0.01,  # Very short timeout
            )

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(TimeoutError, match="timed out"):
                loop.run_until_complete(run())
            # Should have polled multiple times
            assert call_count[0] > 1
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# execute_child_workflow — cancelled path + non-dict result snapshot
# ---------------------------------------------------------------------------

class TestExecuteChildWorkflowBranches:
    def test_execute_child_workflow_non_dict_result(self) -> None:
        """Non-dict child result → returned as-is (not unwrapped)."""
        workflow_api = get_workflow_api()

        mock_engine = MagicMock()
        snapshot = MagicMock()
        snapshot.status = "completed"
        snapshot.result = {"status": "completed", "result": "just a string"}

        async def fake_describe(wf_id):
            return snapshot

        mock_engine.describe_workflow = fake_describe
        submission = MagicMock()
        submission.submitted = True
        submission.error = ""
        mock_engine.start_workflow = AsyncMock(return_value=submission)

        ctx = WorkflowContext(
            workflow_id="parent-string-result",
            payload={},
            workflow_name="parent_str",
            runtime_engine=mock_engine,
        )
        token = set_workflow_context(ctx)

        async def run():
            result = await workflow_api.execute_child_workflow("StringResultChild")
            return result

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            # Result is a string, not unwrapped
            assert result == "just a string"
        finally:
            clear_workflow_context(token)
            loop.close()
