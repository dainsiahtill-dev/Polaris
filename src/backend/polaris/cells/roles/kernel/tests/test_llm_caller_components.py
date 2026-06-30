"""Tests for LLM Caller sub-components without existing coverage.

验证：
1. DecisionCaller 的决策阶段调用
2. FinalizationCaller 的收口阶段调用
3. Error handling 的错误分类
4. StreamEngine 的流式处理
5. EventEmitter 的事件发射
6. ProviderFormatter 的格式化
"""

from __future__ import annotations

import asyncio
import gc
import time
import warnings
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
    build_final_provider_request_snapshot,
    build_final_request_context_audit,
    build_final_request_context_audit_for_request,
)
from polaris.cells.roles.kernel.internal.llm_caller.decision_caller import DecisionCaller
from polaris.cells.roles.kernel.internal.llm_caller.error_handling import (
    ERROR_CATEGORY_AUTH,
    ERROR_CATEGORY_CANCELLED,
    ERROR_CATEGORY_NETWORK,
    ERROR_CATEGORY_RATE_LIMIT,
    ERROR_CATEGORY_TIMEOUT,
    ERROR_CATEGORY_UNKNOWN,
    build_native_tool_unavailable_error,
    build_text_response_fallback_instruction,
    classify_error,
    is_native_tool_calling_unsupported,
    is_response_format_unsupported,
    is_retryable_error,
)
from polaris.cells.roles.kernel.internal.llm_caller.event_emitter import LLMEventEmitter
from polaris.cells.roles.kernel.internal.llm_caller.finalization_caller import FinalizationCaller
from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker
from polaris.cells.roles.kernel.internal.llm_caller.provider_formatter import (
    AnnotatedProviderFormatter,
    NativeProviderFormatter,
    create_formatter,
)
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import _ensure_current_user_message_final
from polaris.cells.roles.kernel.internal.llm_caller.response_types import (
    LLMResponse,
    PreparedLLMRequest,
)
from polaris.cells.roles.kernel.internal.llm_caller.stream_engine import (
    StreamEngine,
    _store_context_messages_accepts_provider_request,
)
from polaris.kernelone.audit.omniscient.dedup import LLMEventDeduplicator, set_global_llm_dedup


@pytest.fixture(autouse=True)
def reset_llm_event_dedup() -> None:
    """Keep global LLM event dedup state from leaking across component tests."""
    set_global_llm_dedup(LLMEventDeduplicator())


def test_final_request_context_audit_counts_tools_and_coverage() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    tool_schema = {
        "type": "function",
        "function": {
            "name": "write_file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "Chief Engineer blueprint with construction_plan and scope_for_apply. "
                "Resident AGI 决策交接 schema_version: resident.agi_decision_trace_signal.v1 "
                "source_of_truth: workspace/meta/resident/decision_trace.jsonl"
            ),
        },
        {
            "role": "user",
            "content": (
                "TASK-1 target_files src/index.ts tests/verify.test.ts retry after stderr exit_code failure "
                "Resident AGI 能力面 schema_version: resident.agi_capability_surface.v1 "
                "runtime_foundation: roles.runtime + ContextOS + TransactionKernel "
                "decision_boundary_schema: resident.agi_decision_boundary.v1 "
                "platform_hard_rule agi_decision_scope agi_governed_execution"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "resident_agi_audit_context": {
            "schema_version": "resident.agi_audit_context.v1",
            "enabled": True,
            "participation": {"final_request_audit": True},
            "participation_scopes": ["final_request_audit"],
        },
    }
    ai_request.options = {"tools": [tool_schema]}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": [tool_schema]},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["schema_version"] == "llm.final_request_context_audit.v1"
    assert audit["message_count"] == 2
    assert audit["tool_schema_count"] == 1
    assert audit["tool_schema_token_estimate"] > 0
    assert audit["final_request_token_estimate"] > audit["message_token_estimate"]
    assert audit["context_window_tokens"] == 32768
    assert audit["context_underutilized"] is True
    assert audit["coverage"]["has_chief_engineer_blueprint"] is True
    assert audit["coverage"]["has_pm_contract"] is True
    assert audit["coverage"]["has_target_files"] is True
    assert audit["coverage"]["has_failure_feedback"] is True
    assert audit["coverage"]["has_resident_agi_decision_trace"] is True
    assert audit["coverage"]["has_resident_agi_capability_surface"] is True
    assert audit["coverage"]["has_resident_agi_decision_boundary"] is True
    assert audit["available_token_headroom"] > 0
    assert "has_workspace_quality_evidence" in audit["context_quality"]["missing_coverage"]
    assert audit["context_quality"]["context_needs_review"] is True


def test_final_request_context_audit_does_not_count_degraded_blueprint_fallback() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "system",
            "content": (
                "【蓝图/技术架构（降级）】\n"
                "无 CE 蓝图可用。基于任务描述和项目结构推断。\n"
                "注意: 此为降级推断，非 CE 权威蓝图。"
            ),
        },
        {
            "role": "user",
            "content": (
                "TASK-1 target_files src/web.ts acceptance npm run build failed "
                "stderr src/web.ts(63,20): error TS2345 workspace quality"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {"chat_messages": messages}
    ai_request.options = {}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["coverage"]["has_pm_contract"] is True
    assert audit["coverage"]["has_chief_engineer_blueprint"] is False
    assert "has_chief_engineer_blueprint" in audit["context_quality"]["missing_coverage"]


def test_final_provider_request_snapshot_summarizes_tools_and_choice() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    profile.role_id = "director"
    profile.provider_id = "qwen-director"
    profile.model = "qwen3.6-27b-q6-code-gpu1"
    tool_schema = {
        "type": "function",
        "function": {
            "name": "repo_tree",
            "description": "List repository files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "depth": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    }
    messages = [{"role": "user", "content": "TASK-1 target_files src/index.ts Chief Engineer blueprint"}]
    ai_request = Mock()
    ai_request.role = "director"
    ai_request.provider_id = "qwen-director"
    ai_request.model = "qwen3.6-27b-q6-code-gpu1"
    ai_request.context = {
        "chat_messages": messages,
        "prompt_profile_audit": {
            "selected_prompt_profile_ids": ["builtin.language.typescript", "builtin.task.implement"],
            "inferred_language": "typescript",
            "inferred_task_type": "implement",
            "redline_clipped": [],
        },
        "selected_prompt_profile_ids": ["builtin.language.typescript", "builtin.task.implement"],
    }
    ai_request.options = {"tools": [tool_schema], "tool_choice": "auto"}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": [tool_schema], "tool_choice": "auto"},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    snapshot = build_final_provider_request_snapshot(ai_request=ai_request, prepared=prepared, profile=profile)

    assert snapshot["schema_version"] == "llm.provider_request_snapshot.v1"
    assert snapshot["source"] == "roles.kernel.llm_caller.context_audit"
    assert snapshot["role"] == "director"
    assert snapshot["provider_id"] == "qwen-director"
    assert snapshot["model"] == "qwen3.6-27b-q6-code-gpu1"
    assert snapshot["message_count"] == 1
    assert snapshot["tool_schema_count"] == 1
    assert snapshot["tool_choice"] == "auto"
    assert snapshot["selected_prompt_profile_ids"] == ["builtin.language.typescript", "builtin.task.implement"]
    assert snapshot["prompt_profile_selection"]["inferred_language"] == "typescript"
    assert snapshot["tools"] == [
        {
            "type": "function",
            "name": "repo_tree",
            "argument_keys": ["depth", "path"],
            "required": ["path"],
        }
    ]
    assert snapshot["final_request_context_audit"]["tool_schema_count"] == 1
    assert snapshot["final_request_context_audit"]["selected_prompt_profile_ids"] == [
        "builtin.language.typescript",
        "builtin.task.implement",
    ]


def test_final_request_context_audit_marks_complete_context_as_reasonable() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": (
                "TASK-1 acceptance criteria target_files src/index.ts "
                "Chief Engineer blueprint construction_plan scope_for_apply "
                "stderr exit_code failed retry factory_workspace_quality npm run build "
                "resident_agi_decision_trace resident.agi_decision_trace_signal.v1 "
                "workspace/meta/resident/decision_trace.jsonl "
                "resident_agi_capability_surface resident.agi_capability_surface.v1 "
                "runtime_foundation: roles.runtime + ContextOS + TransactionKernel "
                "resident.agi_decision_boundary.v1 decision_boundaries platform_hard_rule agi_decision_scope "
                "public_symbols: buildPlanetWeatherReport consumes_symbols: src/models/weather.ts"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {"chat_messages": messages}
    ai_request.options = {"tools": []}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": []},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["context_underutilized"] is True
    assert audit["context_quality"]["missing_coverage"] == []
    assert audit["context_quality"]["context_needs_review"] is False


def test_final_request_context_audit_skips_resident_agi_coverage_when_disabled() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": (
                "TASK-1 acceptance criteria target_files src/index.ts "
                "Chief Engineer blueprint construction_plan scope_for_apply "
                "stderr exit_code failed retry factory_workspace_quality npm run build"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {"chat_messages": messages}
    ai_request.options = {"tools": []}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": []},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert "has_resident_agi_decision_trace" not in audit["coverage"]
    assert "has_resident_agi_capability_surface" not in audit["coverage"]
    assert "has_resident_agi_decision_boundary" not in audit["coverage"]
    assert "has_resident_agi_decision_trace" not in audit["context_quality"]["missing_coverage"]
    assert "has_resident_agi_capability_surface" not in audit["context_quality"]["missing_coverage"]
    assert "has_resident_agi_decision_boundary" not in audit["context_quality"]["missing_coverage"]


def test_final_request_context_audit_reports_missing_resident_agi_when_participation_enabled() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": (
                "TASK-1 acceptance criteria target_files src/index.ts "
                "Chief Engineer blueprint construction_plan scope_for_apply "
                "stderr exit_code failed retry factory_workspace_quality npm run build"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "resident_agi_audit_context": {
            "schema_version": "resident.agi_audit_context.v1",
            "enabled": True,
            "participation": {"final_request_audit": True},
            "participation_scopes": ["final_request_audit"],
        },
    }
    ai_request.options = {"tools": []}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": []},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["coverage"]["has_resident_agi_decision_trace"] is False
    assert audit["coverage"]["has_resident_agi_capability_surface"] is False
    assert audit["coverage"]["has_resident_agi_decision_boundary"] is False
    assert "has_resident_agi_decision_trace" in audit["context_quality"]["missing_coverage"]
    assert "has_resident_agi_capability_surface" in audit["context_quality"]["missing_coverage"]
    assert "has_resident_agi_decision_boundary" in audit["context_quality"]["missing_coverage"]


def test_final_request_context_audit_reads_structured_resident_agi_context() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": (
                "TASK-1 acceptance criteria target_files src/index.ts "
                "Chief Engineer blueprint construction_plan scope_for_apply "
                "stderr exit_code failed retry factory_workspace_quality npm run build"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "resident_agi_audit_context": {
            "schema_version": "resident.agi_audit_context.v1",
            "enabled": True,
            "participation": {"final_request_audit": True},
            "participation_scopes": ["final_request_audit"],
            "audit_pack_schema_version": "resident.agi_audit_pack.v1",
            "decision_contract_schema_version": "resident.agi_decision_contract.v1",
            "capability_surface_schema_version": "resident.agi_capability_surface.v1",
            "decision_capability_registry_schema_version": "resident.agi_decision_capability_registry.v1",
            "decision_boundary_schema": "resident.agi_decision_boundary.v1",
            "decision_boundary_count": 3,
        },
    }
    ai_request.options = {"tools": []}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": []},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["coverage"]["has_resident_agi_decision_trace"] is True
    assert audit["coverage"]["has_resident_agi_capability_surface"] is True
    assert audit["coverage"]["has_resident_agi_decision_boundary"] is True
    assert audit["request_metadata_summary"]["has_resident_agi_audit_context"] is True
    assert audit["request_metadata_summary"]["resident_agi_audit_context"]["enabled"] is True


def test_llm_caller_keeps_current_user_instruction_as_final_message() -> None:
    messages = [
        {"role": "system", "content": "Role contract."},
        {"role": "user", "content": "Implement the task."},
        {"role": "system", "content": "Projected context appended late."},
    ]

    normalized = _ensure_current_user_message_final(messages, "Implement the task.")

    assert normalized[-1] == {"role": "user", "content": "Implement the task."}
    assert normalized[1]["role"] == "system"


def test_llm_caller_restores_missing_current_user_instruction_at_tail() -> None:
    messages = [{"role": "system", "content": "Projected context only."}]

    normalized = _ensure_current_user_message_final(messages, "Run quality repair.")

    assert normalized[-1] == {"role": "user", "content": "Run quality repair."}


def test_final_request_context_audit_recognizes_director_contract_and_blueprint_anchors() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": (
                "PM Task Contract / 任务合同:\n"
                "任务: Implement firefly garden simulator\n"
                "目标文件: src/engine/SimulationEngine.ts\n"
                "Acceptance criteria / 验收标准:\n"
                "- npm run build\n"
                "Chief Engineer Blueprint / CE 蓝图交接:\n"
                "- blueprint_id: bp-L1-01-4\n"
                "- construction target: src/engine/SimulationEngine.ts\n"
                "- construction signatures: class SimulationEngine\n"
                "- construction verify: npm run build\n"
            ),
        }
    ]
    ai_request = Mock()
    ai_request.context = {"chat_messages": messages}
    ai_request.options = {}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["coverage"]["has_pm_contract"] is True
    assert audit["coverage"]["has_chief_engineer_blueprint"] is True
    assert audit["coverage"]["has_target_files"] is True
    assert audit["coverage"]["has_workspace_quality_evidence"] is True


def test_final_request_context_audit_uses_active_fallback_request_options() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "user",
                "content": "TASK-1 target_files src/index.ts Chief Engineer blueprint",
            },
        ],
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={
            "tools": [{"type": "function", "function": {"name": "write_file"}}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "Plan"}},
        },
        ai_request=Mock(),
        native_tool_schemas=[{"type": "function", "function": {"name": "write_file"}}],
        native_response_format={"type": "json_schema", "json_schema": {"name": "Plan"}},
    )
    fallback_request = Mock()
    fallback_request.options = {}
    fallback_request.context = {
        "chat_messages": [
            {
                "role": "user",
                "content": "Fallback plain text request with TASK-1 target_files src/index.ts",
            }
        ]
    }
    fallback_request.input = ""

    audit = build_final_request_context_audit_for_request(
        ai_request=fallback_request,
        prepared=prepared,
        profile=profile,
    )

    assert audit["message_count"] == 1
    assert audit["tool_schema_count"] == 0
    assert audit["tool_schema_token_estimate"] == 0
    assert audit["response_format_token_estimate"] == 0
    assert audit["final_request_token_estimate"] == audit["message_token_estimate"]
    assert audit["coverage"]["has_pm_contract"] is True


def test_final_request_context_audit_reads_role_context_policy_window() -> None:
    profile = Mock()
    profile.max_context_tokens = None
    profile.context_policy = Mock(max_context_tokens=32768)
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "user",
                "content": "TASK-1 target_files src/index.ts Chief Engineer blueprint",
            },
        ],
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={},
        ai_request=Mock(),
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["context_window_tokens"] == 32768
    assert audit["context_window_utilization"] is not None


def test_final_request_context_audit_prefers_bound_model_window_over_role_default() -> None:
    profile = Mock()
    profile.max_context_tokens = 8000
    profile.context_policy = Mock(max_context_tokens=8000)
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "user",
                "content": "TASK-1 target_files src/index.ts Chief Engineer blueprint",
            },
        ],
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={},
        ai_request=Mock(),
        native_tool_schemas=[],
        capability_profile={"model_window_tokens": 24576},
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["context_window_tokens"] == 24576
    assert audit["available_token_headroom"] > 8000


# ============ DecisionCaller Tests ============


@pytest.mark.asyncio
class TestDecisionCaller:
    """测试 DecisionCaller."""

    async def test_call_returns_dict(self) -> None:
        """call 应返回兼容 TransactionKernel 的字典."""
        invoker = Mock()
        invoker.call = AsyncMock(
            return_value=LLMResponse(
                content="decision",
                tool_calls=[{"id": "call_1", "function": {"name": "read_file", "arguments": "{}"}}],
                metadata={"model": "claude"},
            )
        )
        caller = DecisionCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "read main.py"
        context.history = ()
        context.task_id = None
        context.context_override = None

        result = await caller.call(
            profile=profile,
            system_prompt="sys",
            context=context,
            tool_definitions=[{"name": "read_file"}],
        )

        assert result["content"] == "decision"
        assert len(result["tool_calls"]) == 1
        assert result["model"] == "unknown"

    async def test_call_raises_on_error(self) -> None:
        """LLM 返回 error 时应抛出 RuntimeError."""
        invoker = Mock()
        invoker.call = AsyncMock(return_value=LLMResponse(content="", error="LLM failed", error_category="provider"))
        caller = DecisionCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        with pytest.raises(RuntimeError, match="LLM failed"):
            await caller.call(profile=profile, system_prompt="sys", context=context)

    async def test_call_stream_delegates(self) -> None:
        """call_stream 应委托给 invoker.call_stream."""
        invoker = Mock()

        async def _mock_stream():
            yield {"chunk": "1"}

        invoker.call_stream = Mock(return_value=_mock_stream())
        caller = DecisionCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        stream = await caller.call_stream(profile=profile, system_prompt="sys", context=context)
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)

        assert len(chunks) == 1
        invoker.call_stream.assert_called_once()


async def async_generator(items: dict[str, Any]) -> Any:
    """Helper to create an async generator from a single item."""
    yield items


# ============ FinalizationCaller Tests ============


class TestFinalizationCaller:
    """测试 FinalizationCaller."""

    @pytest.mark.asyncio
    async def test_call_returns_dict(self) -> None:
        """call 应返回兼容 TransactionKernel 的字典."""
        invoker = Mock()
        invoker.call = AsyncMock(return_value=LLMResponse(content="final answer", metadata={"model": "claude"}))
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        result = await caller.call(profile=profile, system_prompt="sys", context=context)

        assert result["content"] == "final answer"
        assert result["tool_calls"] == []
        assert result["model"] == "unknown"

    @pytest.mark.asyncio
    async def test_call_raises_on_error(self) -> None:
        """LLM 返回 error 时应抛出 RuntimeError."""
        invoker = Mock()
        invoker.call = AsyncMock(return_value=LLMResponse(content="", error="finalization failed"))
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        with pytest.raises(RuntimeError, match="finalization failed"):
            await caller.call(profile=profile, system_prompt="sys", context=context)

    def test_override_prebuilt_system_prompt(self) -> None:
        """应替换 prebuilt messages 中的 system prompt."""
        invoker = Mock()
        caller = FinalizationCaller(invoker)

        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = {
            "_transaction_kernel_prebuilt_messages": [
                {"role": "system", "content": "old"},
                {"role": "user", "content": "hi"},
            ]
        }

        new_context = caller._override_prebuilt_system_prompt(context, "new prompt")

        override = new_context.context_override or {}
        messages = override["_transaction_kernel_prebuilt_messages"]
        assert messages[0]["content"] == "new prompt"
        assert messages[1]["content"] == "hi"

    def test_override_prebuilt_system_prompt_disables_transaction_tools(self) -> None:
        """Finalization must clear decision-phase forced tools from the final request."""
        invoker = Mock()
        caller = FinalizationCaller(invoker)

        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = {
            "_transaction_kernel_prebuilt_messages": [
                {"role": "system", "content": "old"},
                {"role": "user", "content": "hi"},
            ],
            "_transaction_kernel_forced_tool_definitions": [{"type": "function", "function": {"name": "read_file"}}],
            "_transaction_kernel_forced_tool_choice": "auto",
        }

        new_context = caller._override_prebuilt_system_prompt(context, "finalization prompt")

        override = new_context.context_override or {}
        assert override["_transaction_kernel_prebuilt_messages"][0]["content"] == "finalization prompt"
        assert override["_transaction_kernel_forced_tool_definitions"] == []
        assert override["_transaction_kernel_forced_tool_choice"] == "none"

    def test_build_finalization_prompt_for_execution(self) -> None:
        """执行类请求应生成执行型提示词."""
        invoker = Mock()
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "write a file"
        context.history = ()
        context.task_id = None
        context.context_override = {"domain": "code"}

        prompt = caller._build_finalization_system_prompt(profile=profile, context=context)
        assert "FINAL ANSWER" in prompt
        assert "落地" in prompt or "执行" in prompt

    def test_build_finalization_prompt_for_analysis(self) -> None:
        """分析类请求应生成分析型提示词."""
        invoker = Mock()
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "architect"
        context = Mock()
        context.message = "explain this code"
        context.history = ()
        context.task_id = None
        context.context_override = {"domain": "code"}

        prompt = caller._build_finalization_system_prompt(profile=profile, context=context)
        assert "FINAL ANSWER" in prompt


# ============ Error Handling Tests ============


class TestClassifyError:
    """测试 classify_error."""

    def test_timeout_classification(self) -> None:
        """超时错误应分类为 timeout."""
        assert classify_error("Request timeout") == ERROR_CATEGORY_TIMEOUT
        assert classify_error("timed out") == ERROR_CATEGORY_TIMEOUT

    def test_rate_limit_classification(self) -> None:
        """429 错误应分类为 rate_limit."""
        assert classify_error("429 Too Many Requests") == ERROR_CATEGORY_RATE_LIMIT
        assert classify_error("rate limit exceeded") == ERROR_CATEGORY_RATE_LIMIT

    def test_network_classification(self) -> None:
        """网络错误应分类为 network."""
        assert classify_error("Connection refused") == ERROR_CATEGORY_NETWORK
        assert classify_error("DNS resolution failed") == ERROR_CATEGORY_NETWORK

    def test_circuit_open_classification(self) -> None:
        """Provider circuit breaker open is a runtime/provider availability failure."""
        assert classify_error("circuit_open:57s_remaining") == ERROR_CATEGORY_NETWORK

    def test_cancelled_classification(self) -> None:
        """Cancellation must not fall through to unknown."""
        assert classify_error("call_cancelled") == ERROR_CATEGORY_CANCELLED

    def test_auth_classification(self) -> None:
        """认证错误应分类为 auth."""
        assert classify_error("Unauthorized: invalid api key") == ERROR_CATEGORY_AUTH

    def test_unknown_fallback(self) -> None:
        """未知错误应分类为 unknown."""
        assert classify_error("something weird") == ERROR_CATEGORY_UNKNOWN

    def test_empty_string(self) -> None:
        """空字符串应分类为 unknown."""
        assert classify_error("") == ERROR_CATEGORY_UNKNOWN


class TestIsRetryableError:
    """测试 is_retryable_error."""

    def test_timeout_is_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_TIMEOUT) is True

    def test_network_is_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_NETWORK) is True

    def test_rate_limit_is_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_RATE_LIMIT) is True

    def test_auth_is_not_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_AUTH) is False

    def test_unknown_is_not_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_UNKNOWN) is False


class TestIsNativeToolCallingUnsupported:
    """测试 is_native_tool_calling_unsupported."""

    def test_tools_not_allowed(self) -> None:
        """tools not allowed 应被识别."""
        assert is_native_tool_calling_unsupported("tools is not allowed") is True

    def test_unknown_field(self) -> None:
        """unknown field 应被识别."""
        assert is_native_tool_calling_unsupported("unknown field: tools") is True

    def test_function_calling_not_supported(self) -> None:
        """function calling not supported 应被识别."""
        assert is_native_tool_calling_unsupported("function calling not supported") is True

    def test_normal_error(self) -> None:
        """普通错误不应被识别."""
        assert is_native_tool_calling_unsupported("model overloaded") is False

    def test_empty_string(self) -> None:
        """空字符串不应被识别."""
        assert is_native_tool_calling_unsupported("") is False


class TestIsResponseFormatUnsupported:
    """测试 is_response_format_unsupported."""

    def test_response_format_keyword(self) -> None:
        """response_format 关键字应被识别."""
        assert is_response_format_unsupported("unsupported parameter: response_format") is True

    def test_json_schema_keyword(self) -> None:
        """json_schema 关键字应被识别."""
        assert is_response_format_unsupported("does not support json schema") is True

    def test_normal_error(self) -> None:
        """普通错误不应被识别."""
        assert is_response_format_unsupported("model overloaded") is False


class TestBuildNativeToolUnavailableError:
    """测试 build_native_tool_unavailable_error."""

    def test_builds_error_message(self) -> None:
        """应构建包含 provider/model/tools 信息的错误消息."""
        profile = Mock()
        profile.provider_id = "test-provider"
        profile.model = "test-model"
        tp = Mock()
        tp.whitelist = ["read_file", "write_file"]
        profile.tool_policy = tp

        msg = build_native_tool_unavailable_error(profile)
        assert "native_tool_calling_unavailable" in msg
        assert "test-provider" in msg
        assert "test-model" in msg
        assert "read_file" in msg

    def test_empty_whitelist(self) -> None:
        """空白名单时应使用默认文本."""
        profile = Mock()
        profile.provider_id = "p"
        profile.model = "m"
        tp = Mock()
        tp.whitelist = []
        profile.tool_policy = tp

        msg = build_native_tool_unavailable_error(profile)
        assert "authorized_tools" in msg


class TestBuildTextResponseFallbackInstruction:
    """测试 build_text_response_fallback_instruction."""

    def test_includes_schema_name(self) -> None:
        """应包含 schema 名称."""

        class FakeModel:
            __name__ = "TestSchema"

            @classmethod
            def model_json_schema(cls) -> dict[str, Any]:
                return {"type": "object"}

        instruction = build_text_response_fallback_instruction(FakeModel)
        assert "TestSchema" in instruction or "FakeModel" in instruction


# ============ LLMEventEmitter Tests ============


class TestLLMEventEmitterInit:
    """测试 LLMEventEmitter 初始化."""

    def test_init(self) -> None:
        """基本初始化."""
        emitter = LLMEventEmitter(workspace="/ws")
        assert emitter.workspace == "/ws"

    def test_publish_uep_lifecycle_event_without_loop_does_not_leak_warning(self) -> None:
        """无运行中 event loop 时不应创建未 await 的 coroutine."""
        emitter = LLMEventEmitter(workspace="/ws")

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            emitter.publish_uep_lifecycle_event(
                role="director",
                run_id="run_1",
                event_type="call_start",
            )
            gc.collect()

        warning_messages = [str(item.message) for item in captured]
        assert not any("was never awaited" in message for message in warning_messages)


class TestLLMEventEmitterEmitCallStartEvent:
    """测试 emit_call_start_event."""

    def test_emits_with_basic_params(self) -> None:
        """基本参数应能发射事件."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_start_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
            )
            mock_emit.assert_called_once()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["role"] == "director"
            assert kwargs["run_id"] == "run_1"

    def test_emits_with_event_emitter_override(self) -> None:
        """传入 event_emitter 时应使用其方法并补写 canonical 事件."""
        emitter = LLMEventEmitter(workspace="/ws")
        custom_emitter = Mock()
        custom_emitter._emit_call_start_event = Mock()

        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_start_event(
                event_emitter=custom_emitter,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
            )
        custom_emitter._emit_call_start_event.assert_called_once()
        mock_emit.assert_called_once()

    def test_canonical_event_emitter_override_does_not_double_emit(self) -> None:
        """已声明写 canonical 事件的 emitter 不应被重复补写."""
        emitter = LLMEventEmitter(workspace="/ws")
        custom_emitter = Mock()
        custom_emitter._emits_canonical_llm_events = True
        custom_emitter._emit_call_start_event = Mock()

        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_start_event(
                event_emitter=custom_emitter,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
            )
        custom_emitter._emit_call_start_event.assert_called_once()
        mock_emit.assert_not_called()


class TestLLMEventEmitterEmitCallErrorEvent:
    """测试 emit_call_error_event."""

    def test_emits_error_event(self) -> None:
        """错误事件应被发射."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_error_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                error_category="timeout",
                error_message="timed out",
                call_id="call_1",
                elapsed_ms=1000.0,
            )
            mock_emit.assert_called_once()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["error_category"] == "timeout"
            assert kwargs["error_message"] == "timed out"

    def test_custom_error_emitter_still_writes_canonical_event(self) -> None:
        """错误 override 不能吞掉 canonical 事件."""
        emitter = LLMEventEmitter(workspace="/ws")
        custom_emitter = Mock()
        custom_emitter._emit_call_error_event = Mock()

        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_error_event(
                event_emitter=custom_emitter,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                error_category="timeout",
                error_message="timed out",
                call_id="call_1",
                elapsed_ms=1000.0,
            )
            custom_emitter._emit_call_error_event.assert_called_once()
            mock_emit.assert_called_once()


class TestLLMEventEmitterEmitCallEndEvent:
    """测试 emit_call_end_event."""

    def test_emits_end_event(self) -> None:
        """结束事件应被发射."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_end_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                completion_tokens=50,
            )
            mock_emit.assert_called_once()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["completion_tokens"] == 50

    def test_response_content_emits_content_preview_before_end_event(self) -> None:
        """response_content 应进入实时内容预览，而不是只留在 call_end metadata."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_end_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                completion_tokens=50,
                response_content="公开模型输出片段",
            )

            event_types = [call.kwargs["event_type"] for call in mock_emit.call_args_list]
            assert event_types == ["content_preview", "llm_call_end"]
            preview_kwargs = mock_emit.call_args_list[0].kwargs
            assert preview_kwargs["metadata"]["content"] == "公开模型输出片段"
            assert preview_kwargs["metadata"]["call_id"] == "call_1"
            assert preview_kwargs["metadata"]["content_length"] == len("公开模型输出片段")
            assert preview_kwargs["metadata"]["truncated"] is False
            assert "response_content" not in preview_kwargs["metadata"]
            assert preview_kwargs["completion_tokens"] == 50
            end_kwargs = mock_emit.call_args_list[1].kwargs
            assert end_kwargs["metadata"]["response_content"] == "公开模型输出片段"

    def test_response_content_preview_is_truncated_without_full_response_duplication(self) -> None:
        """CONTENT_PREVIEW must be bounded and must not duplicate full response_content."""
        emitter = LLMEventEmitter(workspace="/ws")
        long_content = "x" * 2505
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_end_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                completion_tokens=50,
                response_content=long_content,
            )

            preview_metadata = mock_emit.call_args_list[0].kwargs["metadata"]
            assert len(preview_metadata["content"]) == 2000
            assert preview_metadata["content_length"] == len(long_content)
            assert preview_metadata["truncated"] is True
            assert "response_content" not in preview_metadata

    def test_custom_end_emitter_still_writes_canonical_event(self) -> None:
        """结束 override 不能吞掉 canonical 事件."""
        emitter = LLMEventEmitter(workspace="/ws")
        custom_emitter = Mock()
        custom_emitter._emit_call_end_event = Mock()

        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_end_event(
                event_emitter=custom_emitter,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                completion_tokens=50,
            )
            custom_emitter._emit_call_end_event.assert_called_once()
            mock_emit.assert_called_once()


class TestLLMEventEmitterEmitCallRetryEvent:
    """测试 emit_call_retry_event."""

    def test_emits_retry_event(self) -> None:
        """重试事件应被发射."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_retry_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=1,
                model="claude",
                call_id="call_1",
                retry_decision="backoff",
                backoff_seconds=2.0,
            )
            mock_emit.assert_called_once()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["retry_decision"] == "backoff"
            assert kwargs["backoff_seconds"] == 2.0

    def test_custom_retry_emitter_still_writes_canonical_event(self) -> None:
        """重试 override 不能吞掉 canonical 事件."""
        emitter = LLMEventEmitter(workspace="/ws")
        custom_emitter = Mock()
        custom_emitter._emit_call_retry_event = Mock()

        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_retry_event(
                event_emitter=custom_emitter,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=1,
                model="claude",
                call_id="call_1",
                retry_decision="backoff",
                backoff_seconds=2.0,
            )
            custom_emitter._emit_call_retry_event.assert_called_once()
            mock_emit.assert_called_once()


# ============ ProviderFormatter Tests ============


class TestCreateFormatter:
    """测试 create_formatter."""

    def test_openai_formatter(self) -> None:
        """openai 应返回 NativeProviderFormatter."""
        fmt = create_formatter("openai")
        assert isinstance(fmt, NativeProviderFormatter)

    def test_anthropic_formatter(self) -> None:
        """anthropic 应返回 NativeProviderFormatter."""
        fmt = create_formatter("anthropic")
        assert isinstance(fmt, NativeProviderFormatter)

    def test_annotated_formatter(self) -> None:
        """annotated 应返回 AnnotatedProviderFormatter."""
        fmt = create_formatter("annotated")
        assert isinstance(fmt, AnnotatedProviderFormatter)

    def test_unknown_defaults_to_annotated(self) -> None:
        """未知 provider 应默认返回 AnnotatedProviderFormatter."""
        fmt = create_formatter("unknown")
        assert isinstance(fmt, AnnotatedProviderFormatter)


class TestNativeProviderFormatter:
    """测试 NativeProviderFormatter."""

    def test_format_tools_passes_through(self) -> None:
        """原生格式化应直接透传."""
        fmt = NativeProviderFormatter()
        tools = [{"name": "read_file"}]
        assert fmt.format_tools(tools, "openai") == tools

    def test_format_messages_passes_through(self) -> None:
        """原生格式化应直接透传消息."""
        fmt = NativeProviderFormatter()
        from unittest.mock import Mock

        event = Mock()
        event.role = "user"
        event.content = "hello"
        messages: list[Any] = [event]
        assert fmt.format_messages(messages) == [{"role": "user", "content": "hello"}]


class TestAnnotatedProviderFormatter:
    """测试 AnnotatedProviderFormatter."""

    def test_format_tools_passes_through(self) -> None:
        """应直接透传工具 schema."""
        fmt = AnnotatedProviderFormatter()
        tools = [{"name": "read_file", "description": "Read a file"}]
        result = fmt.format_tools(tools, "openai")
        assert len(result) == 1
        assert result[0]["name"] == "read_file"

    def test_format_messages_passes_through(self) -> None:
        """应直接透传消息."""
        fmt = AnnotatedProviderFormatter()
        from unittest.mock import Mock

        event = Mock()
        event.role = "user"
        event.content = "hello"
        messages: list[Any] = [event]
        assert fmt.format_messages(messages) == [{"role": "user", "content": "hello"}]


# ============ StreamEngine Tests ============


class TestStreamEngineInit:
    """测试 StreamEngine 初始化."""

    def test_init(self) -> None:
        """基本初始化."""
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(),
            emit_call_start_event=Mock(),
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )
        assert engine.workspace == "/ws"

    def test_llm_invoker_stream_store_wiring_accepts_provider_request(self) -> None:
        """Default streamed context snapshots must preserve provider requests."""
        invoker = LLMInvoker(workspace="/ws")

        assert _store_context_messages_accepts_provider_request(invoker._stream_engine._store_context_messages)


@pytest.mark.asyncio
class TestStreamEngineRunStream:
    """测试 StreamEngine.run_stream."""

    async def test_cancel_before_invoke(self) -> None:
        """取消标志设置时应立即抛出 CancelledError."""
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(),
            emit_call_start_event=Mock(),
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )

        context = Mock()
        context.context_override = {"stream_cancelled": True}

        profile = Mock()
        profile.role_id = "director"

        prepared = Mock()
        prepared.messages = []
        prepared.ai_request = Mock()
        prepared.native_tool_mode = "disabled"
        prepared.response_format_mode = "none"
        prepared.context_result = None

        with pytest.raises(asyncio.CancelledError):
            async for _event in engine.run_stream(
                profile=profile,
                prepared=prepared,
                context=context,
                start_time=0.0,
                role_id="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                event_emitter=None,
                turn_round=0,
            ):
                pass

    async def test_empty_stream(self) -> None:
        """空流应正常完成."""
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=Mock(),
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )

        context = Mock()
        context.context_override = {}
        context.stream_cancelled = False

        profile = Mock()
        profile.role_id = "director"

        prepared = Mock()
        prepared.messages = []
        prepared.ai_request = Mock()
        prepared.native_tool_mode = "disabled"
        prepared.response_format_mode = "none"
        prepared.context_result = None

        # Mock executor to return empty stream
        mock_executor = Mock()

        async def _empty_stream(_request):
            return
            yield

        mock_executor.invoke_stream = _empty_stream
        engine._get_executor = lambda: mock_executor

        events = []
        async for event in engine.run_stream(
            profile=profile,
            prepared=prepared,
            context=context,
            start_time=0.0,
            role_id="director",
            run_id="run_1",
            task_id="task_1",
            attempt=0,
            model="claude",
            call_id="call_1",
            event_emitter=None,
            turn_round=0,
        ):
            events.append(event)

        # Should have at least context_metadata event
        assert any(e.get("type") == "context_metadata" for e in events)

    async def test_stream_call_start_emits_context_snapshot_ref(self) -> None:
        """Phase 1 critical fix: StreamEngine must call store_context_messages
        BEFORE the call_start event so the event metadata carries a non-empty
        context_snapshot_ref (Director multi-worker streams were producing
        empty refs and the per-LLM context viewer stayed blank).
        """
        emit_start = Mock()
        emit_end = Mock()
        captured_hash = "deadbeef" + "cafef00d" * 2  # 24-char hash sentinel

        # Performance hardening (HIGH #2): store_context_messages is awaited
        # so the disk write runs in a thread — the stub MUST be a coroutine
        # that returns the captured hash, mirroring the real
        # ``AIExecutor._store_context_messages`` signature.
        fake_store = AsyncMock(return_value=captured_hash)

        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=emit_start,
            emit_call_error_event=Mock(),
            emit_call_end_event=emit_end,
            emit_call_retry_event=Mock(),
            store_context_messages=fake_store,
        )

        context = Mock()
        context.context_override = {}
        context.stream_cancelled = False
        context.temperature = 0.2
        context.max_tokens = 256

        context_result = Mock()
        context_result.token_estimate = 12
        context_result.compression_strategy = "none"
        context_result.compression_applied = False

        prepared_messages = [
            {"role": "system", "content": "you are a director"},
            {"role": "user", "content": "build a thing"},
        ]
        prepared = Mock()
        prepared.messages = prepared_messages
        # Use a real dict-backed context so we can prove the hash is written
        # into prepared.ai_request.context AND read back via
        # _extract_context_snapshot_ref.
        prepared.ai_request = Mock()
        prepared.ai_request.context = {"mode": "chat"}
        prepared.native_tool_mode = "disabled"
        prepared.response_format_mode = "none"
        prepared.context_result = context_result
        prepared.context_os_audit = {}

        mock_executor = Mock()

        async def _empty_stream(_request):
            return
            yield  # pragma: no cover -- intentional empty generator

        mock_executor.invoke_stream = _empty_stream
        engine._get_executor = lambda: mock_executor

        events: list[dict[str, Any]] = []
        async for event in engine.run_stream(
            profile=Mock(role_id="director"),
            prepared=prepared,
            context=context,
            start_time=0.0,
            role_id="director",
            run_id="run_stream_42",
            task_id="task_42",
            attempt=0,
            model="claude",
            call_id="call_42",
            event_emitter=None,
            turn_round=0,
        ):
            events.append(event)

        # 1. The fake store was invoked with the prepared messages and the
        # same run_id/call_id we passed into run_stream.
        assert any(
            list(call_args.args[1]) == prepared_messages
            and call_args.args[2] == "run_stream_42"
            and call_args.args[3] == "call_42"
            and isinstance(call_args.args[4], dict)
            and call_args.args[4].get("source") == "roles.kernel.llm_caller.context_audit"
            for call_args in fake_store.call_args_list
        ), fake_store.call_args_list

        # 2. The hash was written into prepared.ai_request.context so the
        # sync-style extractor can read it back.
        assert prepared.ai_request.context["context_snapshot_ref"] == captured_hash

        # 3. The call_start event metadata carries a non-empty
        # context_snapshot_ref. THIS is what the per-LLM context viewer reads.
        start_metadata = emit_start.call_args.kwargs["metadata"]
        assert start_metadata["context_snapshot_ref"] == captured_hash

        # 4. The call_end event metadata also carries the same hash so
        # downstream consumers can correlate.
        end_metadata = emit_end.call_args.kwargs["metadata"]
        assert end_metadata["context_snapshot_ref"] == captured_hash

    async def test_stream_call_start_missing_store_does_not_block(self) -> None:
        """Failing-closed guarantee: if store_context_messages is None (legacy
        wiring) or raises, the stream must still emit a call_start with an
        empty context_snapshot_ref instead of crashing the LLM call.
        """
        emit_start = Mock()

        # Performance hardening (HIGH #2): the stream engine now awaits
        # ``store_context_messages`` so the disk write runs in a worker
        # thread. The store stub must be a coroutine that raises so the
        # except clause in ``run_stream`` sees a real exception and falls
        # through to the empty-ref path.
        async def _raising_store(
            workspace: str,
            messages: list[Any],
            trace_id: str,
            call_id_value: str,
        ) -> str:
            raise RuntimeError("disk_full_simulated")

        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=emit_start,
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
            store_context_messages=_raising_store,
        )

        context = Mock()
        context.context_override = {}
        context.stream_cancelled = False
        context.temperature = 0.2
        context.max_tokens = 256

        context_result = Mock()
        context_result.token_estimate = 4
        context_result.compression_strategy = "none"
        context_result.compression_applied = False

        prepared = Mock()
        prepared.messages = [{"role": "user", "content": "hi"}]
        prepared.ai_request = Mock()
        prepared.ai_request.context = {
            "mode": "chat",
            "context_snapshot_ref": "stale-ref-that-must-not-leak",
            "context_snapshot_degraded": {"code": "STALE"},
        }
        prepared.native_tool_mode = "disabled"
        prepared.response_format_mode = "none"
        prepared.context_result = context_result
        prepared.context_os_audit = {}

        async def _empty_stream(_request):
            return
            yield  # pragma: no cover -- intentional empty generator

        mock_executor = Mock()
        mock_executor.invoke_stream = _empty_stream
        engine._get_executor = lambda: mock_executor

        events: list[dict[str, Any]] = []
        async for event in engine.run_stream(
            profile=Mock(role_id="director"),
            prepared=prepared,
            context=context,
            start_time=0.0,
            role_id="director",
            run_id="run_x",
            task_id="task_x",
            attempt=0,
            model="claude",
            call_id="call_x",
            event_emitter=None,
            turn_round=0,
        ):
            events.append(event)

        # Stream completed without raising.
        assert any(e.get("type") == "context_metadata" for e in events)
        # No hash was injected because the store failed.
        start_metadata = emit_start.call_args.kwargs["metadata"]
        assert "context_snapshot_ref" not in start_metadata or not start_metadata["context_snapshot_ref"]
        assert prepared.ai_request.context.get("context_snapshot_ref") is None
        degraded = start_metadata["context_snapshot_degraded"]
        assert degraded["code"] == "CONTEXT_STORE_WRITE_FAILED"
        assert degraded["reason"] == "context_snapshot_store_failure"
        assert degraded["exception_type"] == "RuntimeError"
        assert start_metadata["context_snapshot_degraded_reason"] == "context_snapshot_store_failure"

        end_metadata = engine._emit_call_end.call_args.kwargs["metadata"]
        assert end_metadata["context_snapshot_degraded"]["exception_type"] == "RuntimeError"
        assert end_metadata["context_snapshot_degraded_reason"] == "context_snapshot_store_failure"

    async def test_context_os_audit_is_emitted_with_stream_metadata(self) -> None:
        """ContextOS audit should travel with stream lifecycle metadata."""
        emit_start = Mock()
        emit_end = Mock()
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=emit_start,
            emit_call_error_event=Mock(),
            emit_call_end_event=emit_end,
            emit_call_retry_event=Mock(),
        )

        context = Mock()
        context.context_override = {}
        context.stream_cancelled = False
        context.temperature = 0.2
        context.max_tokens = 256

        context_result = Mock()
        context_result.token_estimate = 12
        context_result.compression_strategy = "none"
        context_result.compression_applied = False

        audit = {"ok": True, "prompt_digest": "audit1234"}
        prepared = Mock()
        prepared.messages = [{"role": "user", "content": "hello"}]
        prepared.ai_request = Mock()
        prepared.native_tool_mode = "disabled"
        prepared.response_format_mode = "none"
        prepared.context_result = context_result
        prepared.context_os_audit = audit

        mock_executor = Mock()

        async def _empty_stream(_request):
            return
            yield

        mock_executor.invoke_stream = _empty_stream
        engine._get_executor = lambda: mock_executor

        events = []
        async for event in engine.run_stream(
            profile=Mock(role_id="director"),
            prepared=prepared,
            context=context,
            start_time=0.0,
            role_id="director",
            run_id="run_1",
            task_id="task_1",
            attempt=0,
            model="claude",
            call_id="call_1",
            event_emitter=None,
            turn_round=0,
        ):
            events.append(event)

        context_metadata = next(event for event in events if event.get("type") == "context_metadata")
        assert context_metadata["context_os_audit"] == audit
        assert emit_start.call_args.kwargs["metadata"]["context_os_audit"] == audit
        assert emit_end.call_args.kwargs["metadata"]["context_os_audit"] == audit

    async def test_native_tool_stream_unavailable_emits_final_request_audit(self) -> None:
        emit_error = Mock()
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(return_value=True),
            emit_call_start_event=Mock(),
            emit_call_error_event=emit_error,
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )

        context = Mock()
        context.context_override = {}
        context.stream_cancelled = False
        context.temperature = 0.2
        context.max_tokens = 256

        context_result = Mock()
        context_result.token_estimate = 24
        context_result.compression_strategy = "none"
        context_result.compression_applied = False

        tool_schema = {"type": "function", "function": {"name": "write_file"}}
        ai_request = Mock()
        ai_request.context = {"chat_messages": [{"role": "user", "content": "TASK-1 target_files src/app.ts"}]}
        ai_request.options = {"tools": [tool_schema]}
        ai_request.input = ""
        prepared = PreparedLLMRequest(
            messages=[{"role": "user", "content": "TASK-1 target_files src/app.ts"}],
            input_text="TASK-1 target_files src/app.ts",
            context_result=context_result,
            context_summary="summary",
            request_options={"tools": [tool_schema], "tool_choice": "auto"},
            ai_request=ai_request,
            native_tool_schemas=[tool_schema],
            native_tool_mode="native_tools_unavailable",
            response_format_mode="plain_text",
        )

        mock_executor = Mock()

        async def _fallback_stream(_request):
            yield {"type": "chunk", "content": "fallback ok"}
            yield {"type": "complete", "content": ""}

        mock_executor.invoke_stream = _fallback_stream
        engine._get_executor = lambda: mock_executor

        profile = Mock()
        profile.role_id = "director"
        profile.max_context_tokens = 32768
        profile.tool_policy.whitelist = []

        events = []
        async for event in engine.run_stream(
            profile=profile,
            prepared=prepared,
            context=context,
            start_time=time.monotonic(),
            role_id="director",
            run_id="run_1",
            task_id="task_1",
            attempt=0,
            model="claude",
            call_id="call_1",
            event_emitter=None,
            turn_round=0,
        ):
            events.append(event)

        error_event = next(event for event in events if event["type"] == "error")
        error_audit = error_event["metadata"]["final_request_context_audit"]
        assert error_audit["tool_schema_count"] == 1
        assert error_event["metadata"]["contextTokens"] == error_audit["final_request_token_estimate"]
        error_metadata = emit_error.call_args.kwargs["metadata"]
        assert error_metadata["native_tool_calling_fallback"] is False
        assert error_metadata["final_request_context_audit"]["tool_schema_count"] == 1


async def async_empty_generator() -> Any:
    """Helper: empty async generator."""
    if False:
        yield  # Make it a generator
