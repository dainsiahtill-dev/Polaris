from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    resolve_mutation_target_guard_violation,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import requires_mutation_intent
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.retry_context_builders import (
    build_retry_write_after_bootstrap_context,
)
from polaris.cells.roles.kernel.internal.transaction.retry_escalation_policy import resolve_retry_model_override
from polaris.cells.roles.kernel.internal.transaction.retry_tool_definitions import (
    bootstrap_receipt_contains_whole_file_replacement_marker,
    build_forced_write_only_retry_tool_definitions,
    build_retry_tool_definitions_for_mutation,
    select_bootstrap_followup_write_tool_name,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    build_single_batch_task_contract_hint,
    extract_allowed_tool_names_from_definitions,
)
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    fill_content_only_write_file_from_remaining_targets,
    fill_single_target_line_range_edit_blocks,
    rewrite_existing_file_paths_in_invocations,
)
from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnStateMachine
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TurnTransactionController
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    RawLLMResponse,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
    classify_tool_invocation,
)

_WRITE_TOOL_NAMES = frozenset({"edit_blocks", "edit_file", "write_file"})


def _canonical_decision(
    *,
    turn_id: str,
    kind: TurnDecisionKind,
    invocations: list[Mapping[str, Any]] | None = None,
    visible_message: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> TurnDecision:
    """Build a complete public turn decision for orchestration-focused tests."""

    normalized_invocations: list[ToolInvocation] = []
    for index, invocation in enumerate(invocations or []):
        tool_name = str(invocation.get("tool_name") or "")
        classification = classify_tool_invocation(tool_name)
        normalized_invocations.append(
            ToolInvocation(
                call_id=ToolCallId(str(invocation.get("call_id") or f"{turn_id}_call_{index + 1}")),
                tool_name=tool_name,
                arguments=dict(cast(Mapping[str, Any], invocation.get("arguments") or {})),
                effect_type=classification.effect_type,
                execution_mode=classification.execution_mode,
            )
        )

    tool_batch = None
    if kind == TurnDecisionKind.TOOL_BATCH:
        tool_batch = ToolBatch(
            batch_id=BatchId(f"{turn_id}_batch"),
            invocations=normalized_invocations,
        )

    return TurnDecision(
        turn_id=TurnId(turn_id),
        kind=kind,
        visible_message=visible_message,
        tool_batch=tool_batch,
        finalize_mode=FinalizeMode.NONE,
        domain="code",
        metadata=dict(metadata or {}),
    )


def _successful_write_tool_result(file_path: str, *, bytes_written: int = 1) -> dict[str, Any]:
    """Return the production write-tool result shape with effect evidence."""

    return {
        "ok": True,
        "file": file_path,
        "bytes_written": bytes_written,
        "effect_receipt": {
            "file": file_path,
            "bytes_written": bytes_written,
            "operation": "write_file",
        },
    }


def _successful_write_batch_result(
    file_path: str,
    *,
    tool_name: str = "write_file",
    visible_content: str = "",
) -> dict[str, Any]:
    """Return a successful batch carrying authoritative write-effect evidence."""

    effect_receipt = {
        "file": file_path,
        "bytes_written": 1,
        "operation": tool_name,
    }
    return {
        "kind": "tool_batch_with_receipt",
        "visible_content": visible_content,
        "batch_receipt": {
            "batch_id": f"batch_{tool_name}",
            "results": [
                {
                    "call_id": f"call_{tool_name}",
                    "tool_name": tool_name,
                    "status": "success",
                    "result": {"file": file_path},
                    "effect_receipt": effect_receipt,
                }
            ],
            "success_count": 1,
            "failure_count": 0,
            "effect_receipts": [effect_receipt],
        },
    }


@pytest.mark.asyncio
async def test_transaction_kernel_executes_single_transaction_turn() -> None:
    llm = AsyncMock(
        return_value={
            "content": "Final answer.",
            "model": "test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
    )
    tool_runtime = AsyncMock()
    kernel = TransactionKernel(llm_provider=llm, tool_runtime=tool_runtime, config=TransactionConfig(domain="code"))

    result = await kernel.execute(
        turn_id="turn_tx",
        context=[{"role": "user", "content": "say hi"}],
        tool_definitions=[],
    )

    assert result["turn_id"] == "turn_tx"
    assert result["kind"] == "final_answer"
    assert result["metrics"]["llm_calls"] == 1
    assert result["metrics"]["tool_calls"] == 0


@pytest.mark.asyncio
async def test_transaction_kernel_execute_forwards_tool_choice_override_to_provider_request() -> None:
    llm = AsyncMock(
        return_value={
            "content": "Final answer.",
            "model": "test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
    )
    tool_runtime = AsyncMock()
    kernel = TransactionKernel(llm_provider=llm, tool_runtime=tool_runtime, config=TransactionConfig(domain="code"))
    forced_choice = {"type": "function", "function": {"name": "write_file"}}
    tool_definitions = [{"type": "function", "function": {"name": "write_file"}}]

    result = await kernel.execute(
        turn_id="turn_tx",
        context=[{"role": "user", "content": "say hi"}],
        tool_definitions=tool_definitions,
        tool_choice_override=forced_choice,
    )

    assert result["kind"] == "final_answer"
    assert llm.await_args is not None
    assert llm.await_args.args[0]["tools"] == tool_definitions
    assert llm.await_args.args[0]["tool_choice"] == forced_choice


async def test_transaction_kernel_executes_single_transaction_turn() -> None:
    llm = AsyncMock(
        return_value={
            "content": "Final answer.",
            "model": "test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
    )
    tool_runtime = AsyncMock()
    kernel = TransactionKernel(llm_provider=llm, tool_runtime=tool_runtime, config=TransactionConfig(domain="code"))

    result = await kernel.execute(
        turn_id="turn_tx",
        context=[{"role": "user", "content": "say hi"}],
        tool_definitions=[],
    )

    assert result["turn_id"] == "turn_tx"
    assert result["kind"] == "final_answer"
    assert result["metrics"]["llm_calls"] == 1
    assert result["metrics"]["tool_calls"] == 0


@pytest.mark.asyncio
async def test_transaction_kernel_execute_forwards_tool_choice_override_to_provider_request() -> None:
    llm = AsyncMock(
        return_value={
            "content": "Final answer.",
            "model": "test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
    )
    tool_runtime = AsyncMock()
    kernel = TransactionKernel(llm_provider=llm, tool_runtime=tool_runtime, config=TransactionConfig(domain="code"))
    forced_choice = {"type": "function", "function": {"name": "write_file"}}
    tool_definitions = [{"type": "function", "function": {"name": "write_file"}}]

    result = await kernel.execute(
        turn_id="turn_tx",
        context=[{"role": "user", "content": "say hi"}],
        tool_definitions=tool_definitions,
        tool_choice_override=forced_choice,
    )

    assert result["kind"] == "final_answer"
    assert llm.await_args is not None
    assert llm.await_args.args[0]["tools"] == tool_definitions
    assert llm.await_args.args[0]["tool_choice"] == forced_choice


def test_build_decision_messages_adds_write_verify_contract_hint() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    context = [
        {
            "role": "user",
            "content": "在 tests/ 目录下创建 test_new.py, 并运行 pytest 验证.",
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "edit_blocks"}},
        {"type": "function", "function": {"name": "execute_command"}},
    ]
    messages = controller._build_decision_messages(context, tool_definitions)

    system_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "system"]
    assert any("SYSTEM CONSTRAINT (Execution)" in text for text in system_messages)
    assert any("TASK CONTRACT (single-batch planning)" in text for text in system_messages)
    assert any("edit_blocks" in text for text in system_messages)
    assert any("execute_command" in text for text in system_messages)
    assert any("HARD GATE: if your tool batch contains no write tool call" in text for text in system_messages)
    assert any("Mutation target files detected from user request" in text for text in system_messages)


def test_edit_preferred_single_target_quality_repair_drops_verify_required_tool() -> None:
    context = [
        {
            "role": "user",
            "content": (
                "[mode:materialize]\n"
                "[director_quality_repair:edit_preferred_single_target]\n"
                "- Target path: src/main.ts\n"
                "Quality errors:\n"
                "- TypeScript project typecheck failed: src/main.ts(10,10): unused import.\n"
                "Return tool calls only for the minimal files needed."
            ),
            "metadata": {
                "tool_contract": {
                    "single_batch": True,
                    "required_tools": ["execute_command"],
                    "min_tool_calls": 1,
                }
            },
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "edit_file"}},
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "execute_command"}},
    ]

    contract_text, metadata = build_single_batch_task_contract_hint(context, tool_definitions)

    assert "Single-target quality repair is active" in contract_text
    assert "prefer edit_file" in contract_text
    assert "Contract-required tools are mandatory" not in contract_text
    assert "execute_command" not in contract_text
    assert metadata["expected_read_count"] == 0


def test_build_decision_messages_treats_xinzeng_gengxin_as_mutation() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    context = [
        {
            "role": "user",
            "content": "请新增暂停功能，并更新 README.md 的操作说明。",
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "edit_blocks"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]

    messages = controller._build_decision_messages(context, tool_definitions)
    system_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "system"]

    assert any("This request requires mutation." in text for text in system_messages)
    assert any("HARD GATE: if your tool batch contains no write tool call" in text for text in system_messages)
    assert any("README.md" in text for text in system_messages)


def test_fill_content_only_write_file_uses_remaining_structured_target() -> None:
    invocations = [
        {
            "call_id": "write_pkg",
            "tool_name": "write_file",
            "arguments": {"file": "package.json", "content": "{}"},
        },
        {
            "call_id": "write_test",
            "tool_name": "write_file",
            "arguments": {"file": "tests/product.test.js", "content": "import test from 'node:test';\n"},
        },
        {
            "call_id": "write_pytest",
            "tool_name": "write_file",
            "arguments": {"file": "tests/test_product.py", "content": "import unittest\n"},
        },
        {
            "call_id": "write_readme",
            "tool_name": "write_file",
            "arguments": {"content": "# Project\n"},
        },
    ]

    filled = fill_content_only_write_file_from_remaining_targets(
        invocations,
        target_files=("package.json", "tests/product.test.js", "tests/test_product.py", "README.md"),
    )

    assert filled[3]["arguments"]["file"] == "README.md"
    assert filled[3]["arguments"]["content"] == "# Project\n"


def test_fill_content_only_write_file_stays_fail_closed_when_ambiguous() -> None:
    invocations = [
        {
            "call_id": "write_unknown",
            "tool_name": "write_file",
            "arguments": {"content": "# Project\n"},
        }
    ]

    filled = fill_content_only_write_file_from_remaining_targets(
        invocations,
        target_files=("README.md", "CHANGELOG.md"),
    )

    assert "file" not in filled[0]["arguments"]


def test_requires_mutation_intent_detects_cn_execute_verbs() -> None:
    assert requires_mutation_intent("请新增接口并更新 README") is True
    assert requires_mutation_intent("请解释当前架构") is False


def test_requires_mutation_intent_rejects_adjective_usage_in_analysis_context() -> None:
    """'完善' as an adjective in analysis-only requests must NOT trigger mutation.

    Regression guard: "总结项目代码并给我进一步完善的建议" contains '完善'
    but is purely advisory/analysis; it must not enter mutation-contract retry.
    """
    assert requires_mutation_intent("总结项目代码并给我进一步完善的建议") is False
    assert requires_mutation_intent("请给出完善的分析报告") is False
    assert requires_mutation_intent("完善这个函数") is True
    assert requires_mutation_intent("帮我完善代码") is True


def test_build_retry_tool_definitions_for_mutation_keeps_context_reads_with_write_tools() -> None:
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "list_directory"}},
        {"type": "function", "function": {"name": "repo_read_slice"}},
        {"type": "function", "function": {"name": "repo_read_around"}},
        {"type": "function", "function": {"name": "glob"}},
        {"type": "function", "function": {"name": "edit_file"}},
        {"type": "function", "function": {"name": "execute_command"}},
    ]

    retry_definitions = build_retry_tool_definitions_for_mutation(
        latest_user_request="请修改 README.md 并补充操作说明",
        tool_definitions=tool_definitions,
    )
    retry_names = extract_allowed_tool_names_from_definitions(retry_definitions)

    assert retry_names == {
        "read_file",
        "list_directory",
        "repo_read_slice",
        "repo_read_around",
        "glob",
        "edit_file",
        "execute_command",
    }


def test_build_forced_write_only_retry_tool_definitions_keeps_execute_command_when_verify_required() -> None:
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "execute_command"}},
    ]

    strict_definitions = build_forced_write_only_retry_tool_definitions(
        tool_definitions,
        "write_file",
        include_verification_tools=True,
    )
    strict_names = extract_allowed_tool_names_from_definitions(strict_definitions)

    assert strict_names == {"write_file", "execute_command"}


def test_build_forced_write_only_retry_tool_definitions_keeps_mutation_companions_for_multi_target_create() -> None:
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "edit_file"}},
        {"type": "function", "function": {"name": "execute_command"}},
    ]

    strict_definitions = build_forced_write_only_retry_tool_definitions(
        tool_definitions,
        "write_file",
        include_verification_tools=False,
        include_mutation_companion_tools=True,
    )
    strict_names = extract_allowed_tool_names_from_definitions(strict_definitions)

    assert strict_names == {"write_file", "edit_file"}


def test_forced_edit_blocks_retry_adds_scoped_write_file_companion() -> None:
    tool_definitions = [
        {
            "type": "function",
            "function": {
                "name": "edit_blocks",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "enum": ["calculator.py"]},
                        "blocks": {"type": "string"},
                    },
                    "required": ["file", "blocks"],
                },
            },
        },
        {"type": "function", "function": {"name": "execute_command"}},
    ]

    strict_definitions = build_forced_write_only_retry_tool_definitions(
        tool_definitions,
        "edit_blocks",
        include_verification_tools=True,
    )
    strict_names = extract_allowed_tool_names_from_definitions(strict_definitions)
    write_file_definition = next(
        item for item in strict_definitions if item.get("function", {}).get("name") == "write_file"
    )

    assert strict_names == {"edit_blocks", "write_file", "execute_command"}
    file_schema = write_file_definition["function"]["parameters"]["properties"]["file"]
    assert file_schema["enum"] == ["calculator.py"]


def test_scoped_write_file_companion_stays_registry_faithful_except_path_enums() -> None:
    """Live L1-08: invented slim write_file blocked quality-repair qualification.

    MiniMax omitted package.json. The repair turn forced write_file+edit_file, but
    `_build_scoped_write_file_tool_definition` rewrote description and dropped
    aliases. Final-provider qualification raised tool_registry_function_contract_drift
    and the turn never called the provider (llm_calls=0).
    """

    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    tool_definitions = [
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "parameters": {
                    "type": "object",
                    "properties": {"file": {"type": "string", "enum": ["package.json"]}},
                    "required": ["file"],
                },
            },
        }
    ]
    strict_definitions = build_forced_write_only_retry_tool_definitions(
        tool_definitions,
        "edit_file",
        include_verification_tools=False,
        include_mutation_companion_tools=True,
    )
    write_file_definition = next(
        item for item in strict_definitions if item.get("function", {}).get("name") == "write_file"
    )
    expected = ToolSpecRegistry.get_llm_schema(
        "write_file",
        include_arg_aliases=True,
        deterministic=True,
    )
    assert expected is not None
    assert write_file_definition["function"]["name"] == expected["function"]["name"]
    assert write_file_definition["function"]["description"] == expected["function"]["description"]
    actual_props = write_file_definition["function"]["parameters"]["properties"]
    expected_props = expected["function"]["parameters"]["properties"]
    assert set(actual_props) == set(expected_props)
    assert actual_props["file"]["enum"] == ["package.json"]
    assert actual_props["path"]["enum"] == ["package.json"]
    assert "enum" not in expected_props["file"]


def test_forced_edit_blocks_retry_can_disable_write_file_companion_for_scoped_repair() -> None:
    tool_definitions = [
        {"type": "function", "function": {"name": "edit_blocks"}},
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "execute_command"}},
    ]

    strict_definitions = build_forced_write_only_retry_tool_definitions(
        tool_definitions,
        "edit_blocks",
        include_verification_tools=True,
        allow_write_file_companion_for_edit_blocks=False,
    )
    strict_names = extract_allowed_tool_names_from_definitions(strict_definitions)

    assert strict_names == {"edit_blocks", "execute_command"}


def test_bootstrap_followup_prefers_write_file_for_deterministic_scaffold() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {
                    "file": "src/game/rules-engine.ts",
                    "content": 'tags: ["audit-seed"]; title: "planning scenario 1";',
                },
            }
        ]
    }

    assert bootstrap_receipt_contains_whole_file_replacement_marker(receipt) is True
    selected = select_bootstrap_followup_write_tool_name(
        allowed_tool_names={"read_file", "edit_blocks", "write_file", "edit_file"},
        default_write_tool_name="edit_blocks",
        bootstrap_receipt=receipt,
        failed_bootstrap_files=[],
    )

    assert selected == "write_file"


def test_bootstrap_followup_switches_whole_file_edit_blocks_error_to_write_file() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "edit_blocks",
                "status": "error",
                "result": {
                    "ok": False,
                    "error_type": "new_file_via_edit_blocks",
                    "error": (
                        "edit_blocks received a filename plus full file content for "
                        "services/order_service/__init__.py. That is a whole-file write, "
                        "not an edit."
                    ),
                },
            }
        ]
    }

    selected = select_bootstrap_followup_write_tool_name(
        allowed_tool_names={"read_file", "edit_blocks", "write_file", "edit_file"},
        default_write_tool_name="edit_blocks",
        bootstrap_receipt=receipt,
        failed_bootstrap_files=[],
    )

    assert selected == "write_file"


def test_bootstrap_followup_does_not_force_create_tool_for_opaque_read_failure() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "error",
                "arguments": {"file": "src/main/java/app/Service.java"},
                "result": {},
            }
        ]
    }

    selected = select_bootstrap_followup_write_tool_name(
        allowed_tool_names={"read_file", "edit_blocks", "write_file", "edit_file"},
        default_write_tool_name="write_file",
        bootstrap_receipt=receipt,
        failed_bootstrap_files=["src/main/java/app/Service.java"],
    )

    assert selected is None


def test_bootstrap_followup_missing_file_failure_can_create() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "error",
                "arguments": {"file": "src/main/java/app/NewType.java"},
                "result": {"error": "file not found: src/main/java/app/NewType.java"},
            }
        ]
    }

    selected = select_bootstrap_followup_write_tool_name(
        allowed_tool_names={"read_file", "edit_blocks", "write_file", "edit_file"},
        default_write_tool_name="edit_blocks",
        bootstrap_receipt=receipt,
        failed_bootstrap_files=["src/main/java/app/NewType.java"],
    )

    assert selected == "write_file"


def test_bootstrap_followup_successful_existing_content_prefers_edit_tool_over_create_default() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {
                    "file": "src/test/java/app/ServiceTest.java",
                    "content": "class ServiceTest { void verifiesBehavior() {} }\n",
                },
            }
        ]
    }

    selected = select_bootstrap_followup_write_tool_name(
        allowed_tool_names={"read_file", "edit_blocks", "write_file", "edit_file"},
        default_write_tool_name="write_file",
        bootstrap_receipt=receipt,
        failed_bootstrap_files=[],
    )

    assert selected == "edit_blocks"


def test_bootstrap_followup_context_keeps_medium_file_tail() -> None:
    content = "\n".join(f"{line:03d}: value" for line in range(1, 700))
    content += "\nTAIL_COMPILER_ERROR_CONTEXT\n"
    receipt = {
        "results": [
            {
                "tool_name": "read_file",
                "status": "success",
                "result": {"file": "src/test/java/app/ServiceTest.java", "content": content},
            }
        ]
    }

    retry_context = build_retry_write_after_bootstrap_context(
        original_context=[{"role": "user", "content": "Fix src/test/java/app/ServiceTest.java"}],
        bootstrap_receipt=receipt,
        forced_write_tool_name="edit_blocks",
    )
    system_text = str(retry_context[0]["content"])

    assert "TAIL_COMPILER_ERROR_CONTEXT" in system_text
    assert "content truncated" not in system_text


@pytest.mark.asyncio
async def test_execute_tool_batch_rejects_readonly_batch_for_mutation_request() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code", mutation_guard_mode="strict"),
    )
    decision = {
        "turn_id": "turn_contract_guard",
        "metadata": {"workspace": "."},
        "tool_batch": {
            "batch_id": "batch_contract_guard",
            "invocations": [
                {
                    "call_id": "call_readme",
                    "tool_name": "read_file",
                    "arguments": {"file": "README.md"},
                }
            ],
        },
    }
    state_machine = TurnStateMachine(turn_id="turn_contract_guard")
    ledger = TurnLedger(turn_id="turn_contract_guard")
    context = [{"role": "user", "content": "请更新 README.md 并写入新说明"}]

    with pytest.raises(RuntimeError, match="single_batch_contract_violation"):
        await controller._tool_batch_executor.execute_tool_batch(
            cast(TurnDecision, decision), state_machine, ledger, context
        )


@pytest.mark.asyncio
async def test_execute_tool_batch_rejects_write_target_drift_for_explicit_mutation_request() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code", mutation_guard_mode="strict"),
    )
    decision = {
        "turn_id": "turn_target_guard",
        "metadata": {"workspace": "."},
        "tool_batch": {
            "batch_id": "batch_target_guard",
            "invocations": [
                {
                    "call_id": "call_write",
                    "tool_name": "write_file",
                    "arguments": {"file": "game.py", "content": "print('x')"},
                    "effect_type": ToolEffectType.WRITE,
                    "execution_mode": ToolExecutionMode.WRITE_SERIAL,
                }
            ],
        },
    }
    state_machine = TurnStateMachine(turn_id="turn_target_guard")
    ledger = TurnLedger(turn_id="turn_target_guard")
    context = [{"role": "user", "content": "请更新 README.md 和 highscore.json，然后继续落地"}]

    with pytest.raises(RuntimeError, match="mutation write target drift"):
        await controller._tool_batch_executor.execute_tool_batch(
            cast(TurnDecision, decision), state_machine, ledger, context
        )


def test_resolve_mutation_target_guard_violation_allows_matching_write_target() -> None:
    context_message = "请更新 README.md 和 highscore.json，然后继续落地"
    invocations = [
        {
            "tool_name": "write_file",
            "arguments": {"file": "README.md", "content": "updated"},
            "effect_type": ToolEffectType.WRITE,
            "execution_mode": ToolExecutionMode.WRITE_SERIAL,
        }
    ]

    violation = resolve_mutation_target_guard_violation(context_message, invocations)

    assert violation is None


def test_resolve_mutation_target_guard_violation_allows_go_write_targets() -> None:
    context_message = "目标文件: go.mod, src/models/pet.go, src/engine/renderer.go, main.go"
    invocations = [
        {
            "tool_name": "write_file",
            "arguments": {"file": "go.mod", "content": "module ascii-magic-pet\n"},
            "effect_type": ToolEffectType.WRITE,
            "execution_mode": ToolExecutionMode.WRITE_SERIAL,
        },
        {
            "tool_name": "write_file",
            "arguments": {"file": "src/models/pet.go", "content": "package models\n"},
            "effect_type": ToolEffectType.WRITE,
            "execution_mode": ToolExecutionMode.WRITE_SERIAL,
        },
        {
            "tool_name": "write_file",
            "arguments": {"file": "main.go", "content": "package main\n"},
            "effect_type": ToolEffectType.WRITE,
            "execution_mode": ToolExecutionMode.WRITE_SERIAL,
        },
    ]

    violation = resolve_mutation_target_guard_violation(context_message, invocations)

    assert violation is None


def test_single_batch_task_contract_hint_detects_go_target_files() -> None:
    context = [
        {
            "role": "user",
            "content": (
                "请实现 Go CLI，目标文件: go.mod, src/models/pet.go, src/engine/spell_engine.go, main.go。"
                "必须写入代码。"
            ),
        }
    ]
    tool_definitions = [
        {
            "type": "function",
            "function": {"name": "write_file"},
        }
    ]

    contract_text, metadata = build_single_batch_task_contract_hint(context, tool_definitions)

    assert "expected_read_count" in metadata
    assert "go.mod" in contract_text
    assert "src/models/pet.go" in contract_text
    assert "src/engine/spell_engine.go" in contract_text
    assert "main.go" in contract_text


def test_single_batch_task_contract_hint_uses_quality_repair_targets_not_evidence_refs() -> None:
    context = [
        {
            "role": "user",
            "content": (
                "[mode:materialize]\n"
                "- Chief Engineer blueprint evidence:\n"
                "- artifact: runtime/state/blueprints/factory_abc.review.json\n"
                '- "blueprint_path": "runtime/blueprints/ce_TASK-1-foundation.json"\n\n'
                "MISSING TARGET FILES — create these exact paths NOW, one write_file call per path:\n"
                "- src/meteor.js\n"
                "- src/wish.js\n"
                "EXISTING FAILED TARGET FILES — repair these exact paths NOW:\n"
                "- package.json\n"
                "- src/engine/rules.js\n"
                "Quality errors:\n"
                "- Artifact quality scan failed: unresolved relative import in src/engine/rules.js; "
                "create missing module target src/meteor.js and export the imported symbols.\n"
            ),
        }
    ]
    tool_definitions = [{"type": "function", "function": {"name": "write_file"}}]

    contract_text, _metadata = build_single_batch_task_contract_hint(context, tool_definitions)

    assert "src/meteor.js" in contract_text
    assert "src/wish.js" in contract_text
    assert "package.json" in contract_text
    assert "src/engine/rules.js" in contract_text
    assert "runtime/state/blueprints" not in contract_text
    assert "runtime/blueprints" not in contract_text


def test_resolve_mutation_target_guard_violation_rejects_extra_out_of_scope_write_target() -> None:
    context_message = "请仅修改 snake_game/input_handler.py 和 snake_game/game_loop.py，并继续落地"
    invocations = [
        {
            "tool_name": "write_file",
            "arguments": {"file": "snake_game/input_handler.py", "content": "updated"},
            "effect_type": ToolEffectType.WRITE,
            "execution_mode": ToolExecutionMode.WRITE_SERIAL,
        },
        {
            "tool_name": "write_file",
            "arguments": {"file": "snake_game/test_game.py", "content": "unexpected"},
            "effect_type": ToolEffectType.WRITE,
            "execution_mode": ToolExecutionMode.WRITE_SERIAL,
        },
    ]

    violation = resolve_mutation_target_guard_violation(context_message, invocations)

    assert isinstance(violation, str)
    assert "mutation write target drift" in violation
    assert "snake_game/test_game.py" in violation


def test_resolve_mutation_target_guard_violation_rejects_bare_basename_for_scoped_target() -> None:
    context_message = "请仅修改 src/main.py，并继续落地"
    invocations = [
        {
            "tool_name": "write_file",
            "arguments": {"file": "main.py", "content": "print('wrong path')"},
            "effect_type": ToolEffectType.WRITE,
            "execution_mode": ToolExecutionMode.WRITE_SERIAL,
        }
    ]

    violation = resolve_mutation_target_guard_violation(context_message, invocations)

    assert isinstance(violation, str)
    assert "mutation write target drift" in violation
    assert "main.py" in violation
    assert "src/main.py" in violation


@pytest.mark.asyncio
async def test_execute_turn_forces_retry_on_non_tool_decision_for_mutation(monkeypatch) -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code", mutation_guard_mode="strict"),
    )
    state_machine = TurnStateMachine(turn_id="turn_force_retry")
    ledger = TurnLedger(turn_id="turn_force_retry")
    context = [{"role": "user", "content": "请修改 README.md 并落地代码"}]
    tool_definitions = [{"type": "function", "function": {"name": "edit_file"}}]
    captured: dict[str, Any] = {}

    async def _fake_call_llm_for_decision(
        _ctx,
        _tool_definitions,
        _llm_ledger,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        return RawLLMResponse(content="我先总结思路", native_tool_calls=[])

    def _fake_decode(_response, _turn_id):
        return _canonical_decision(
            turn_id="turn_force_retry",
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="仅总结",
        )

    async def _fake_retry(*, turn_id, context, tool_definitions, **_kwargs):
        captured["turn_id"] = turn_id
        captured["context"] = context
        captured["tool_definitions"] = tool_definitions
        return _successful_write_batch_result(
            "README.md",
            tool_name="edit_file",
            visible_content="已执行写入",
        )

    monkeypatch.setattr(controller, "_call_llm_for_decision", _fake_call_llm_for_decision)
    monkeypatch.setattr(controller.decoder, "decode", _fake_decode)
    monkeypatch.setattr(
        controller._retry_orchestrator,
        "retry_tool_batch_after_contract_violation",
        _fake_retry,
    )

    result = await controller._execute_turn(
        "turn_force_retry",
        context,
        tool_definitions,
        state_machine,
        ledger,
        stream=False,
    )

    assert result["kind"] == "tool_batch_with_receipt"
    assert captured["turn_id"] == "turn_force_retry"
    assert captured["context"] == context


@pytest.mark.asyncio
async def test_execute_turn_passes_narrowed_tool_names_to_direct_batch_executor(monkeypatch) -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    state_machine = TurnStateMachine(turn_id="turn_allowed_tools")
    ledger = TurnLedger(turn_id="turn_allowed_tools")
    context = [{"role": "user", "content": "请只读取 README.md 的必要片段"}]
    tool_definitions = [{"type": "function", "function": {"name": "repo_read_slice"}}]
    captured: dict[str, Any] = {}

    async def _fake_call_llm_for_decision(
        _ctx,
        _tool_definitions,
        _llm_ledger,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        return RawLLMResponse(content="", native_tool_calls=[])

    def _fake_decode(_response, _turn_id):
        return _canonical_decision(
            turn_id="turn_allowed_tools",
            kind=TurnDecisionKind.TOOL_BATCH,
            invocations=[
                {
                    "tool_name": "repo_read_slice",
                    "arguments": {"file": "README.md", "start": 1, "end": 20},
                }
            ],
        )

    async def _fake_execute_tool_batch(
        decision,
        state_machine,
        ledger,
        context,
        *,
        stream=False,
        shadow_engine=None,
        allowed_tool_names=None,
        **_kwargs,
    ):
        captured["decision"] = decision
        captured["stream"] = stream
        captured["allowed_tool_names"] = allowed_tool_names
        return {
            "turn_id": "turn_allowed_tools",
            "kind": "tool_batch_with_receipt",
            "visible_content": "已读取",
        }

    monkeypatch.setattr(controller, "_call_llm_for_decision", _fake_call_llm_for_decision)
    monkeypatch.setattr(controller.decoder, "decode", _fake_decode)
    monkeypatch.setattr(controller._tool_batch_executor, "execute_tool_batch", _fake_execute_tool_batch)

    result = await controller._execute_turn(
        "turn_allowed_tools",
        context,
        tool_definitions,
        state_machine,
        ledger,
        stream=False,
    )

    assert result["kind"] == "tool_batch_with_receipt"
    assert captured["stream"] is False
    assert captured["allowed_tool_names"] == {"repo_read_slice"}


def test_rewrite_existing_file_paths_in_invocations_drops_nonexistent_prefix(tmp_path) -> None:
    # 前缀剥离回退已删除：当子目录路径不存在时，系统不应静默猜测根目录同名文件。
    index_file = tmp_path / "index.html"
    index_file.write_text("<html></html>", encoding="utf-8")
    invocations = [
        {
            "call_id": "call_read",
            "tool_name": "read_file",
            "arguments": {"file": "snake_game/index.html"},
        }
    ]

    rewritten = rewrite_existing_file_paths_in_invocations(
        turn_id="turn_path_rewrite",
        workspace=str(tmp_path),
        invocations=invocations,
    )

    assert rewritten
    first = rewritten[0]
    assert isinstance(first, dict)
    # 路径不存在时不应回退到根目录同名文件
    assert first["arguments"]["file"] == "snake_game/index.html"


def test_fill_single_target_line_range_edit_blocks_adds_missing_file() -> None:
    invocations = [
        {
            "tool_name": "edit_blocks",
            "arguments": {
                "start": 2,
                "end": 4,
                "replace": "def create_app():\n    return app\n",
            },
        }
    ]

    filled = fill_single_target_line_range_edit_blocks(
        invocations,
        target_files=("services/__init__.py",),
    )

    assert filled[0]["arguments"] == {
        "file": "services/__init__.py",
        "start": 2,
        "end": 4,
        "replace": "def create_app():\n    return app\n",
    }


def test_fill_single_target_line_range_edit_blocks_handles_tool_invocation_model() -> None:
    invocation = ToolInvocation(
        call_id=ToolCallId("call_edit"),
        tool_name="edit_blocks",
        arguments={"start": 1, "end": 3, "replace": "value = 1\n"},
        effect_type=ToolEffectType.WRITE,
        execution_mode=ToolExecutionMode.WRITE_SERIAL,
    )

    filled = fill_single_target_line_range_edit_blocks(
        [invocation],
        target_files=("services/__init__.py",),
    )

    assert isinstance(filled[0], ToolInvocation)
    assert filled[0].arguments == {
        "file": "services/__init__.py",
        "start": 1,
        "end": 3,
        "replace": "value = 1\n",
    }
    assert invocation.arguments == {"start": 1, "end": 3, "replace": "value = 1\n"}


def test_fill_single_target_line_range_edit_blocks_refuses_multi_target() -> None:
    invocations = [
        {
            "tool_name": "edit_blocks",
            "arguments": {"start": 1, "end": 1, "replace": "x = 1\n"},
        }
    ]

    filled = fill_single_target_line_range_edit_blocks(
        invocations,
        target_files=("a.py", "b.py"),
    )

    assert filled == invocations


def test_resolve_retry_model_override_uses_env_sequence(monkeypatch) -> None:
    monkeypatch.setenv("KERNELONE_TRANSACTION_KERNEL_RETRY_MODELS", "model-alpha, model-beta")
    monkeypatch.setenv("KERNELONE_TRANSACTION_KERNEL_RETRY_MODEL_START", "2")

    assert resolve_retry_model_override(1) is None
    assert resolve_retry_model_override(2) == "model-alpha"
    assert resolve_retry_model_override(3) == "model-beta"
    assert resolve_retry_model_override(4) == "model-beta"


@pytest.mark.asyncio
async def test_retry_tool_batch_after_contract_violation_appends_retry_contract_hint(monkeypatch) -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    state_machine = TurnStateMachine(turn_id="turn_retry_contract")
    ledger = TurnLedger(turn_id="turn_retry_contract")
    context = [{"role": "user", "content": "请直接修改 README.md 并写入操作说明"}]
    captured: dict[str, object] = {}

    async def _fake_call_llm_for_decision(
        ctx,
        tool_definitions,
        llm_ledger,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        captured["retry_context"] = ctx
        captured["retry_tool_definitions"] = list(tool_definitions)
        captured["retry_ledger"] = llm_ledger
        captured["retry_tool_choice_override"] = tool_choice_override
        return RawLLMResponse(content="", native_tool_calls=[])

    def _fake_decode(_response, _turn_id):
        return {
            "kind": TurnDecisionKind.TOOL_BATCH,
            "turn_id": "turn_retry_contract",
            "visible_message": "retry write",
            "tool_batch": {
                "batch_id": "turn_retry_contract_batch",
                "invocations": [],
                "parallel_readonly": [],
                "readonly_serial": [],
                "serial_writes": [],
                "async_receipts": [],
            },
            "finalize_mode": "none",
            "domain": "code",
        }

    async def _fake_execute_tool_batch(
        decision,
        sm,
        lg,
        exec_context,
        *,
        stream,
        shadow_engine,
        allowed_tool_names=None,
        count_towards_batch_limit=True,
    ):
        captured["execute_context"] = exec_context
        captured["stream"] = stream
        captured["shadow_engine"] = shadow_engine
        captured["allowed_tool_names"] = allowed_tool_names
        return {"kind": "tool_batch_with_receipt", "batch_receipt": None}

    monkeypatch.setattr(controller, "_call_llm_for_decision", _fake_call_llm_for_decision)
    monkeypatch.setattr(controller.decoder, "decode", _fake_decode)
    monkeypatch.setattr(controller._retry_orchestrator, "execute_tool_batch", _fake_execute_tool_batch)

    result = await controller._retry_orchestrator.retry_tool_batch_after_contract_violation(
        turn_id="turn_retry_contract",
        context=context,
        tool_definitions=[{"type": "function", "function": {"name": "edit_file"}}],
        state_machine=state_machine,
        ledger=ledger,
        stream=False,
        shadow_engine=None,
        initial_failure_reason=(
            "single_batch_contract_violation: write tool batch produced no effects and requires "
            "a new invocation within the authorized target scope; "
            "error_types=director_write_policy_denied"
        ),
    )

    assert result["kind"] == "tool_batch_with_receipt"
    retry_context = captured["retry_context"]
    assert isinstance(retry_context, list)
    assert len(retry_context) == len(context) + 2
    assert retry_context[0]["role"] == "system"
    assert "RETRY CONTRACT" in str(retry_context[0]["content"])
    assert "Allowed write tools" in str(retry_context[0]["content"])
    assert "HARD GATE: never return plain-text-only completion" in str(retry_context[0]["content"])
    assert "MANDATORY:" not in str(retry_context[0]["content"])
    assert "Do not guess exact-match edit search text" in str(retry_context[0]["content"])
    assert retry_context[-2]["role"] == "user"
    assert retry_context[-1]["role"] == "system"
    assert "RETRY ENFORCEMENT" in str(retry_context[-1]["content"])
    assert "director_write_policy_denied" in str(retry_context[-1]["content"])
    execute_context = captured["execute_context"]
    assert execute_context == retry_context
    assert captured["stream"] is False
    assert captured["allowed_tool_names"] == {"edit_file"}
    assert captured["retry_tool_choice_override"] is None


@pytest.mark.asyncio
async def test_retry_enforcement_after_invalid_batch_does_not_pin_edit_blocks(monkeypatch) -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    state_machine = TurnStateMachine(turn_id="turn_retry_no_edit_blocks_pin")
    ledger = TurnLedger(turn_id="turn_retry_no_edit_blocks_pin")
    context = [{"role": "user", "content": "请实现 src/game/rules-engine.ts 并写入文件"}]
    captured: dict[str, object] = {"execute_calls": 0, "retry_contexts": []}
    decode_calls = 0

    async def _fake_call_llm_for_decision(
        ctx,
        tool_definitions,
        llm_ledger,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        del tool_definitions, llm_ledger, tool_choice_override, model_override
        cast(list[list[dict]], captured["retry_contexts"]).append(ctx)
        return RawLLMResponse(content="", native_tool_calls=[])

    def _fake_decode(_response, _turn_id):
        nonlocal decode_calls
        decode_calls += 1
        if decode_calls == 1:
            return _canonical_decision(
                turn_id="turn_retry_no_edit_blocks_pin",
                kind=TurnDecisionKind.TOOL_BATCH,
                invocations=[{"tool_name": "read_file", "arguments": {"file": "README.md"}}],
            )
        return _canonical_decision(
            turn_id="turn_retry_no_edit_blocks_pin",
            kind=TurnDecisionKind.TOOL_BATCH,
            invocations=[
                {
                    "tool_name": "write_file",
                    "arguments": {"file": "src/game/rules-engine.ts", "content": "export {};\n"},
                }
            ],
        )

    async def _fake_execute_tool_batch(
        decision,
        sm,
        lg,
        exec_context,
        *,
        stream,
        shadow_engine,
        allowed_tool_names=None,
        count_towards_batch_limit=True,
    ):
        del decision, sm, lg, exec_context, stream, shadow_engine, allowed_tool_names, count_towards_batch_limit
        execute_calls = int(captured["execute_calls"]) + 1
        captured["execute_calls"] = execute_calls
        if execute_calls == 1:
            raise RuntimeError(
                "single_batch_contract_violation: mutation requested but no write tool invocation in decision batch"
            )
        return _successful_write_batch_result("src/game/rules-engine.ts")

    monkeypatch.setattr(controller, "_call_llm_for_decision", _fake_call_llm_for_decision)
    monkeypatch.setattr(controller.decoder, "decode", _fake_decode)
    monkeypatch.setattr(controller._retry_orchestrator, "execute_tool_batch", _fake_execute_tool_batch)

    result = await controller._retry_orchestrator.retry_tool_batch_after_contract_violation(
        turn_id="turn_retry_no_edit_blocks_pin",
        context=context,
        tool_definitions=[
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "edit_blocks"}},
            {"type": "function", "function": {"name": "write_file"}},
        ],
        state_machine=state_machine,
        ledger=ledger,
        stream=False,
        shadow_engine=None,
    )

    assert result["kind"] == "tool_batch_with_receipt"
    retry_contexts = cast(list[list[dict]], captured["retry_contexts"])
    assert len(retry_contexts) == 2
    second_context_text = "\n".join(str(item.get("content") or "") for item in retry_contexts[1])
    assert "MANDATORY write tool for this retry: edit_blocks" not in second_context_text
    assert "MANDATORY: your batch must include write tool `edit_blocks`" not in second_context_text


@pytest.mark.asyncio
async def test_retry_tool_batch_after_contract_violation_uses_stream_materialization(monkeypatch) -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
        llm_provider_stream=AsyncMock(),
    )
    state_machine = TurnStateMachine(turn_id="turn_retry_stream")
    ledger = TurnLedger(turn_id="turn_retry_stream")
    context = [{"role": "user", "content": "请直接修改 README.md 并写入操作说明"}]
    captured: dict[str, object] = {}

    async def _fake_call_llm_for_decision_stream(
        ctx,
        tool_definitions,
        llm_ledger,
        shadow_engine=None,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        captured["retry_context"] = ctx
        captured["retry_tool_definitions"] = list(tool_definitions)
        captured["retry_ledger"] = llm_ledger
        captured["retry_shadow_engine"] = shadow_engine
        captured["retry_tool_choice_override"] = tool_choice_override
        yield {"type": "_internal_materialize", "response": RawLLMResponse(content="", native_tool_calls=[])}

    def _fake_decode(_response, _turn_id):
        return _canonical_decision(
            turn_id="turn_retry_stream",
            kind=TurnDecisionKind.TOOL_BATCH,
            invocations=[
                {
                    "tool_name": "edit_file",
                    "arguments": {"file": "README.md", "search": "old", "replace": "new"},
                }
            ],
        )

    async def _fake_execute_tool_batch(
        decision,
        sm,
        lg,
        exec_context,
        *,
        stream,
        shadow_engine,
        allowed_tool_names=None,
        count_towards_batch_limit=True,
    ):
        captured["execute_context"] = exec_context
        captured["stream"] = stream
        captured["shadow_engine"] = shadow_engine
        captured["allowed_tool_names"] = allowed_tool_names
        return _successful_write_batch_result("README.md", tool_name="edit_file")

    monkeypatch.setattr(
        controller._stream_orchestrator,
        "_call_llm_for_decision_stream_impl",
        _fake_call_llm_for_decision_stream,
    )
    monkeypatch.setattr(controller.decoder, "decode", _fake_decode)
    monkeypatch.setattr(controller._retry_orchestrator, "execute_tool_batch", _fake_execute_tool_batch)

    result = await controller._retry_orchestrator.retry_tool_batch_after_contract_violation(
        turn_id="turn_retry_stream",
        context=context,
        tool_definitions=[{"type": "function", "function": {"name": "edit_file"}}],
        state_machine=state_machine,
        ledger=ledger,
        stream=True,
        shadow_engine=None,
    )

    assert result["kind"] == "tool_batch_with_receipt"
    retry_context = captured["retry_context"]
    assert isinstance(retry_context, list)
    assert retry_context[0]["role"] == "system"
    assert "RETRY CONTRACT" in str(retry_context[0]["content"])
    assert retry_context[-1]["role"] == "user"
    assert captured["stream"] is True
    assert captured["allowed_tool_names"] == {"edit_file"}
    assert captured["retry_tool_choice_override"] is None


@pytest.mark.asyncio
async def test_stream_provider_tool_events_materialize_native_tool_calls(monkeypatch) -> None:
    async def _fake_llm_provider_stream(_payload):
        yield {
            "type": "tool_call",
            "tool": "write_file",
            "args": {"file": "README.md", "content": "done\n"},
            "call_id": "call_write_readme",
        }

    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
        llm_provider_stream=_fake_llm_provider_stream,
    )
    monkeypatch.setattr(controller._stream_orchestrator, "build_stream_shadow_engine", lambda **_kwargs: None)
    ledger = TurnLedger(turn_id="turn_stream_provider_tool_materialize")
    events: list[object] = []

    async for event in controller._stream_orchestrator._call_llm_for_decision_stream_impl(
        [{"role": "user", "content": "请更新 README.md 并写入文件"}],
        [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ],
        ledger,
        shadow_engine=None,
    ):
        events.append(event)

    materialized = [
        event for event in events if isinstance(event, dict) and event.get("type") == "_internal_materialize"
    ]
    assert materialized
    response = materialized[-1]["response"]
    assert isinstance(response, RawLLMResponse)
    assert response.native_tool_calls == [
        {
            "id": "call_write_readme",
            "type": "function",
            "function": {
                "name": "write_file",
                "arguments": {"file": "README.md", "content": "done\n"},
            },
        }
    ]

    decision = controller.decoder.decode(response, TurnId("turn_stream_provider_tool_materialize"))
    tool_batch = decision.get("tool_batch")
    assert tool_batch is not None
    invocations = list(tool_batch.get("invocations", []) or [])
    assert invocations
    assert invocations[0].get("tool_name") == "write_file"
    assert invocations[0].get("arguments") == {"file": "README.md", "content": "done\n"}


@pytest.mark.asyncio
async def test_stream_provider_error_is_not_followed_by_generic_materialization_error(monkeypatch) -> None:
    from polaris.cells.roles.kernel.public.turn_events import ErrorEvent

    async def _fake_llm_provider_stream(_payload):
        yield {"type": "error", "error": "provider_stream_timeout:125s"}

    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
        llm_provider_stream=_fake_llm_provider_stream,
    )
    monkeypatch.setattr(controller._stream_orchestrator, "build_stream_shadow_engine", lambda **_kwargs: None)

    events = [
        event
        async for event in controller.execute_stream(
            "turn_stream_provider_timeout",
            [{"role": "user", "content": "review this plan"}],
            [],
        )
    ]

    errors = [event for event in events if isinstance(event, ErrorEvent)]
    assert [event.message for event in errors] == ["provider_stream_timeout:125s"]


@pytest.mark.asyncio
async def test_stream_final_answer_has_one_terminal_truth_and_preserves_provider_audit(monkeypatch) -> None:
    from polaris.cells.roles.kernel.public.turn_events import CompletionEvent

    final_request_audit = {
        "schema_version": "llm.final_request_context_audit.v1",
        "final_request_token_estimate": 987,
    }
    context_snapshot_ref = "abcdef123456abcdef123456"

    async def _fake_llm_provider_stream(_payload):
        yield {"type": "chunk", "content": "Complete CE blueprint."}
        yield {
            "type": "complete",
            "content": "Complete CE blueprint.",
            "metadata": {
                "final_request_context_audit": final_request_audit,
                "context_snapshot_ref": context_snapshot_ref,
            },
        }

    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
        llm_provider_stream=_fake_llm_provider_stream,
    )
    monkeypatch.setattr(controller._stream_orchestrator, "build_stream_shadow_engine", lambda **_kwargs: None)
    phase_events = MagicMock()
    monkeypatch.setattr(controller, "_emit_phase_event", phase_events)

    events = [
        event
        async for event in controller.execute_stream(
            "turn_stream_ce_blueprint",
            [
                {
                    "role": "user",
                    "content": "[mode:analyze_only]\nProduce the implementation blueprint.",
                }
            ],
            [],
        )
    ]

    completions = [event for event in events if isinstance(event, CompletionEvent)]
    assert len(completions) == 1
    assert completions[0].status == "success"
    assert completions[0].monitoring["final_request_context_audit"] == final_request_audit
    assert completions[0].monitoring["context_snapshot_ref"] == context_snapshot_ref
    assert not any(call.args and isinstance(call.args[0], CompletionEvent) for call in phase_events.call_args_list)


@pytest.mark.asyncio
async def test_stream_materialize_bypass_emits_one_failed_terminal(monkeypatch) -> None:
    from polaris.cells.roles.kernel.public.turn_events import CompletionEvent

    async def _fake_llm_provider_stream(_payload):
        yield {"type": "chunk", "content": "I would implement it later."}

    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
        llm_provider_stream=_fake_llm_provider_stream,
    )
    monkeypatch.setattr(controller._stream_orchestrator, "build_stream_shadow_engine", lambda **_kwargs: None)
    phase_events = MagicMock()
    monkeypatch.setattr(controller, "_emit_phase_event", phase_events)

    events = [
        event
        async for event in controller.execute_stream(
            "turn_stream_materialize_bypass",
            [{"role": "user", "content": "[mode:materialize]\nImplement the requested files."}],
            [],
        )
    ]

    completions = [event for event in events if isinstance(event, CompletionEvent)]
    assert len(completions) == 1
    assert completions[0].status == "failed"
    assert completions[0].turn_kind == "mutation_bypass_blocked"
    assert completions[0].error == "MUTATION_BYPASS_BLOCKED"
    assert not any(call.args and isinstance(call.args[0], CompletionEvent) for call in phase_events.call_args_list)


@pytest.mark.asyncio
async def test_retry_tool_batch_stream_escalates_without_single_tool_lock(monkeypatch, tmp_path) -> None:
    # The task EDITS an existing README.md, so the graduated ladder applies (the
    # next attempt must NOT lock to a single tool). The file must physically
    # exist in the workspace, otherwise F16 correctly classifies it as a
    # from-scratch create and forces the write early (covered separately by
    # test_retry_api_escalation.TestF16CreationModeEscalation).
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code", workspace=str(tmp_path)),
        llm_provider_stream=AsyncMock(),
    )
    state_machine = TurnStateMachine(turn_id="turn_retry_escalation")
    ledger = TurnLedger(turn_id="turn_retry_escalation")
    context = [{"role": "user", "content": "请直接修改 README.md 并写入操作说明"}]
    stream_calls = 0
    non_stream_calls = 0
    decode_calls = 0
    execute_allowed_names: list[set[str]] = []

    async def _fake_call_llm_for_decision_stream(
        ctx,
        tool_definitions,
        llm_ledger,
        shadow_engine=None,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        nonlocal stream_calls
        stream_calls += 1
        yield {"type": "_internal_materialize", "response": RawLLMResponse(content="", native_tool_calls=[])}

    async def _fake_call_llm_for_decision(
        ctx,
        tool_definitions,
        llm_ledger,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        nonlocal non_stream_calls
        non_stream_calls += 1
        return RawLLMResponse(content="", native_tool_calls=[])

    def _fake_decode(_response, _turn_id):
        nonlocal decode_calls
        decode_calls += 1
        if decode_calls == 1:
            # NOTE: a pure READ-ONLY first attempt (e.g. read_file) now ignites the
            # bootstrap read path immediately (Phase 2 weak-model ignition, covered by
            # test_readonly_retry_batch_ignites_bootstrap_immediately). Use a
            # non-bootstrap-safe tool here so this test keeps pinning the blind-retry
            # escalation property: the next attempt must NOT lock to a single tool.
            return _canonical_decision(
                turn_id="turn_retry_escalation",
                kind=TurnDecisionKind.TOOL_BATCH,
                invocations=[
                    {"tool_name": "execute_command", "arguments": {"command": "cat README.md"}},
                ],
            )
        return _canonical_decision(
            turn_id="turn_retry_escalation",
            kind=TurnDecisionKind.TOOL_BATCH,
            invocations=[
                {"tool_name": "edit_file", "arguments": {"file": "README.md", "old": "", "new": "x"}},
            ],
        )

    async def _fake_execute_tool_batch(
        decision,
        sm,
        lg,
        exec_context,
        *,
        stream,
        shadow_engine,
        allowed_tool_names=None,
        count_towards_batch_limit=True,
    ):
        allowed = set(allowed_tool_names or set())
        execute_allowed_names.append(allowed)
        invocations = decision.get("tool_batch", {}).get("invocations", [])
        has_write = any(str(item.get("tool_name") or "").strip() == "edit_file" for item in invocations)
        if not has_write:
            raise RuntimeError(
                "single_batch_contract_violation: mutation requested but no write tool invocation in decision batch"
            )
        return _successful_write_batch_result("README.md", tool_name="edit_file")

    monkeypatch.setattr(
        controller._stream_orchestrator,
        "_call_llm_for_decision_stream_impl",
        _fake_call_llm_for_decision_stream,
    )
    monkeypatch.setattr(controller, "_call_llm_for_decision", _fake_call_llm_for_decision)
    monkeypatch.setattr(controller.decoder, "decode", _fake_decode)
    monkeypatch.setattr(controller._retry_orchestrator, "execute_tool_batch", _fake_execute_tool_batch)

    result = await controller._retry_orchestrator.retry_tool_batch_after_contract_violation(
        turn_id="turn_retry_escalation",
        context=context,
        tool_definitions=[
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "list_directory"}},
            {"type": "function", "function": {"name": "edit_file"}},
        ],
        state_machine=state_machine,
        ledger=ledger,
        stream=True,
        shadow_engine=None,
    )

    assert result["kind"] == "tool_batch_with_receipt"
    assert stream_calls == 2
    assert non_stream_calls == 0
    assert execute_allowed_names[0] == {"read_file", "list_directory", "edit_file"}
    assert execute_allowed_names[1] == {"read_file", "list_directory", "edit_file"}


@pytest.mark.asyncio
async def test_retry_stale_edit_violation_switches_to_bootstrap_read_path(monkeypatch) -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    state_machine = TurnStateMachine(turn_id="turn_retry_stale_bootstrap")
    ledger = TurnLedger(turn_id="turn_retry_stale_bootstrap")
    context = [{"role": "user", "content": "请直接修改 README.md 并写入操作说明"}]
    captured: dict[str, object] = {"execute_calls": 0, "executed_batch_ids": []}

    async def _fake_call_llm_for_decision(
        ctx,
        tool_definitions,
        llm_ledger,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        captured["retry_context"] = ctx
        captured["retry_tool_definitions"] = list(tool_definitions)
        captured["retry_ledger"] = llm_ledger
        captured["retry_tool_choice_override"] = tool_choice_override
        return RawLLMResponse(content="", native_tool_calls=[])

    def _fake_decode(_response, _turn_id):
        return _canonical_decision(
            turn_id="turn_retry_stale_bootstrap",
            kind=TurnDecisionKind.TOOL_BATCH,
            metadata={"workspace": "."},
            invocations=[
                {
                    "tool_name": "edit_file",
                    "arguments": {"file": "README.md", "search": "old", "replace": "new"},
                }
            ],
        )

    async def _fake_execute_tool_batch(
        decision,
        sm,
        lg,
        exec_context,
        *,
        stream,
        shadow_engine,
        allowed_tool_names=None,
        count_towards_batch_limit=True,
    ):
        execute_calls = int(captured["execute_calls"]) + 1
        captured["execute_calls"] = execute_calls
        captured.setdefault("allowed_tool_names", []).append(set(allowed_tool_names or set()))
        tool_batch = decision.get("tool_batch") or {}
        cast(list[str], captured["executed_batch_ids"]).append(str(tool_batch.get("batch_id") or ""))
        if execute_calls == 1:
            raise RuntimeError(
                "single_batch_contract_violation: stale_edit blocked write invocation; requires_bootstrap_read"
            )
        return _successful_write_batch_result("README.md", tool_name="edit_file")

    async def _fake_execute_read_bootstrap_batch(
        *,
        turn_id,
        workspace,
        tool_batch,
        ledger,
    ):
        captured["bootstrap_turn_id"] = turn_id
        captured["bootstrap_workspace"] = workspace
        captured["bootstrap_tool_batch"] = tool_batch
        return {
            "results": [
                {
                    "tool_name": "read_file",
                    "status": "success",
                    "result": {"file": "README.md", "content": "# README"},
                }
            ]
        }

    monkeypatch.setattr(controller, "_call_llm_for_decision", _fake_call_llm_for_decision)
    monkeypatch.setattr(controller.decoder, "decode", _fake_decode)
    monkeypatch.setattr(controller._retry_orchestrator, "execute_tool_batch", _fake_execute_tool_batch)
    monkeypatch.setattr(
        controller._retry_orchestrator, "execute_read_bootstrap_batch", _fake_execute_read_bootstrap_batch
    )

    result = await controller._retry_orchestrator.retry_tool_batch_after_contract_violation(
        turn_id="turn_retry_stale_bootstrap",
        context=context,
        tool_definitions=[
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "edit_file"}},
        ],
        state_machine=state_machine,
        ledger=ledger,
        stream=False,
        shadow_engine=None,
    )

    assert result["kind"] == "tool_batch_with_receipt"
    assert captured["execute_calls"] == 2
    assert captured["executed_batch_ids"] == [
        "turn_retry_stale_bootstrap:mutation-retry:1",
        "turn_retry_stale_bootstrap:bootstrap-followup:2",
    ]
    bootstrap_batch = captured["bootstrap_tool_batch"]
    if isinstance(bootstrap_batch, dict):
        bootstrap_invocations = list(bootstrap_batch.get("invocations", []))
    else:
        bootstrap_invocations = list(getattr(bootstrap_batch, "invocations", []) or [])
    assert bootstrap_invocations
    first_bootstrap = bootstrap_invocations[0]
    if isinstance(first_bootstrap, dict):
        first_tool_name = first_bootstrap.get("tool_name")
        first_file = (first_bootstrap.get("arguments") or {}).get("file")
    else:
        first_tool_name = getattr(first_bootstrap, "tool_name", "")
        first_file = getattr(first_bootstrap, "arguments", {}).get("file")
    assert first_tool_name == "read_file"
    assert first_file == "README.md"


@pytest.mark.asyncio
async def test_retry_bootstrap_scaffold_followup_forces_write_file(monkeypatch) -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    state_machine = TurnStateMachine(turn_id="turn_retry_scaffold_bootstrap")
    ledger = TurnLedger(turn_id="turn_retry_scaffold_bootstrap")
    context = [
        {
            "role": "user",
            "content": "请实现 src/game/rules-engine.ts，替换当前脚手架代码。",
        }
    ]
    captured: dict[str, object] = {
        "execute_calls": 0,
        "tool_choice_overrides": [],
        "max_tokens_floors": [],
        "llm_tool_definition_names": [],
        "execute_allowed_names": [],
    }
    decode_calls = 0

    async def _fake_call_llm_for_decision(
        ctx,
        tool_definitions,
        llm_ledger,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        del ctx, llm_ledger, model_override
        cast(list[object], captured["tool_choice_overrides"]).append(tool_choice_override)
        cast(list[object], captured["max_tokens_floors"]).append(max_tokens_floor)
        cast(list[set[str]], captured["llm_tool_definition_names"]).append(
            extract_allowed_tool_names_from_definitions(list(tool_definitions))
        )
        return RawLLMResponse(content="", native_tool_calls=[])

    def _fake_decode(_response, _turn_id):
        nonlocal decode_calls
        decode_calls += 1
        if decode_calls == 1:
            return _canonical_decision(
                turn_id="turn_retry_scaffold_bootstrap",
                kind=TurnDecisionKind.TOOL_BATCH,
                metadata={"workspace": "."},
                invocations=[
                    {
                        "tool_name": "edit_blocks",
                        "arguments": {
                            "blocks": (
                                "<<<< SEARCH[:src/game/rules-engine.ts]\n"
                                "audit-seed\n"
                                "====\n"
                                "real implementation\n"
                                ">>>> REPLACE"
                            )
                        },
                    }
                ],
            )
        return _canonical_decision(
            turn_id="turn_retry_scaffold_bootstrap",
            kind=TurnDecisionKind.TOOL_BATCH,
            metadata={"workspace": "."},
            invocations=[
                {
                    "tool_name": "write_file",
                    "arguments": {
                        "file": "src/game/rules-engine.ts",
                        "content": "export const implemented = true;\n",
                    },
                }
            ],
        )

    async def _fake_execute_tool_batch(
        decision,
        sm,
        lg,
        exec_context,
        *,
        stream,
        shadow_engine,
        allowed_tool_names=None,
        count_towards_batch_limit=True,
    ):
        del sm, lg, exec_context, stream, shadow_engine, count_towards_batch_limit
        execute_calls = int(captured["execute_calls"]) + 1
        captured["execute_calls"] = execute_calls
        cast(list[set[str]], captured["execute_allowed_names"]).append(set(allowed_tool_names or set()))
        if execute_calls == 1:
            raise RuntimeError(
                "single_batch_contract_violation: stale_edit blocked write invocation; requires_bootstrap_read"
            )
        tool_batch = cast(Mapping[str, object], decision.get("tool_batch") or {})
        invocations = cast(list[Mapping[str, object]], tool_batch.get("invocations") or [])
        assert invocations
        assert invocations[0].get("tool_name") == "write_file"
        return _successful_write_batch_result("src/game/rules-engine.ts")

    async def _fake_execute_read_bootstrap_batch(
        *,
        turn_id,
        workspace,
        tool_batch,
        ledger,
    ):
        del turn_id, workspace, tool_batch, ledger
        return {
            "results": [
                {
                    "tool_name": "read_file",
                    "status": "success",
                    "result": {
                        "file": "src/game/rules-engine.ts",
                        "content": 'export const cardRulesEngineScenario1 = { tags: ["audit-seed"] };',
                    },
                }
            ]
        }

    monkeypatch.setattr(controller, "_call_llm_for_decision", _fake_call_llm_for_decision)
    monkeypatch.setattr(controller.decoder, "decode", _fake_decode)
    monkeypatch.setattr(controller._retry_orchestrator, "execute_tool_batch", _fake_execute_tool_batch)
    monkeypatch.setattr(
        controller._retry_orchestrator, "execute_read_bootstrap_batch", _fake_execute_read_bootstrap_batch
    )

    result = await controller._retry_orchestrator.retry_tool_batch_after_contract_violation(
        turn_id="turn_retry_scaffold_bootstrap",
        context=context,
        tool_definitions=[
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "edit_blocks"}},
            {"type": "function", "function": {"name": "write_file"}},
            {"type": "function", "function": {"name": "edit_file"}},
        ],
        state_machine=state_machine,
        ledger=ledger,
        stream=False,
        shadow_engine=None,
    )

    assert result["kind"] == "tool_batch_with_receipt"
    assert captured["execute_calls"] == 2
    overrides = cast(list[object], captured["tool_choice_overrides"])
    assert overrides[0] == {"type": "function", "function": {"name": "write_file"}}
    assert overrides[1] == {"type": "function", "function": {"name": "write_file"}}
    assert cast(list[object], captured["max_tokens_floors"]) == [7000, 7000]
    assert cast(list[set[str]], captured["llm_tool_definition_names"])[0] == {"write_file"}
    assert cast(list[set[str]], captured["llm_tool_definition_names"])[1] == {"write_file"}
    assert cast(list[set[str]], captured["execute_allowed_names"])[0] == {"write_file"}
    assert cast(list[set[str]], captured["execute_allowed_names"])[1] == {"write_file"}


@pytest.mark.asyncio
async def test_retry_bootstrap_existing_edit_blocks_followup_keeps_write_file_companion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    workspace = tmp_path
    (workspace / "src").mkdir()
    (workspace / "src/existing.ts").write_text("export const oldValue = true;\n", encoding="utf-8")
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(workspace))
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    state_machine = TurnStateMachine(turn_id="turn_retry_existing_edit_blocks")
    ledger = TurnLedger(turn_id="turn_retry_existing_edit_blocks")
    context = [
        {
            "role": "user",
            "content": "请修复 src/existing.ts，必须修改该文件并验证。",
        }
    ]
    captured: dict[str, object] = {
        "tool_choice_overrides": [],
        "llm_tool_definition_names": [],
        "execute_allowed_names": [],
    }

    async def _fake_call_llm_for_decision(
        ctx: Any,
        tool_definitions: Any,
        llm_ledger: Any,
        *,
        tool_choice_override: Any = None,
        model_override: Any = None,
        temperature_override: Any = None,
        max_tokens_floor: Any = None,
    ) -> RawLLMResponse:
        del ctx, llm_ledger, model_override, temperature_override, max_tokens_floor
        cast(list[object], captured["tool_choice_overrides"]).append(tool_choice_override)
        cast(list[set[str]], captured["llm_tool_definition_names"]).append(
            extract_allowed_tool_names_from_definitions(list(tool_definitions))
        )
        return RawLLMResponse(content="", native_tool_calls=[])

    def _fake_decode(_response: Any, _turn_id: Any) -> TurnDecision:
        return _canonical_decision(
            turn_id="turn_retry_existing_edit_blocks",
            kind=TurnDecisionKind.TOOL_BATCH,
            metadata={"workspace": str(workspace)},
            invocations=[
                {
                    "tool_name": "write_file",
                    "arguments": {
                        "file": "src/existing.ts",
                        "content": "export const oldValue = false;\n",
                    },
                }
            ],
        )

    async def _fake_execute_tool_batch(
        decision: Mapping[str, object],
        sm: Any,
        lg: Any,
        exec_context: Any,
        *,
        stream: bool,
        shadow_engine: Any,
        allowed_tool_names: set[str] | None = None,
        count_towards_batch_limit: bool = True,
    ) -> dict[str, object]:
        del sm, lg, exec_context, stream, shadow_engine, count_towards_batch_limit
        cast(list[set[str]], captured["execute_allowed_names"]).append(set(allowed_tool_names or set()))
        tool_batch = cast(Mapping[str, object], decision.get("tool_batch") or {})
        invocations = cast(list[Mapping[str, object]], tool_batch.get("invocations") or [])
        assert invocations
        assert invocations[0].get("tool_name") == "write_file"
        return _successful_write_batch_result("src/existing.ts")

    async def _fake_execute_read_bootstrap_batch(
        *,
        turn_id: str,
        workspace: str,
        tool_batch: Any,
        ledger: Any,
    ) -> dict[str, object]:
        del turn_id, workspace, tool_batch, ledger
        return {
            "results": [
                {
                    "tool_name": "read_file",
                    "status": "success",
                    "result": {
                        "file": "src/existing.ts",
                        "content": "export const oldValue = true;\n",
                    },
                }
            ]
        }

    original_decision = _canonical_decision(
        turn_id="turn_retry_existing_edit_blocks",
        kind=TurnDecisionKind.TOOL_BATCH,
        metadata={"workspace": str(workspace)},
        invocations=[
            {
                "tool_name": "read_file",
                "arguments": {"file": "src/existing.ts"},
            }
        ],
    )
    monkeypatch.setattr(controller, "_call_llm_for_decision", _fake_call_llm_for_decision)
    monkeypatch.setattr(controller.decoder, "decode", _fake_decode)
    monkeypatch.setattr(controller._retry_orchestrator, "execute_tool_batch", _fake_execute_tool_batch)
    monkeypatch.setattr(
        controller._retry_orchestrator,
        "execute_read_bootstrap_batch",
        _fake_execute_read_bootstrap_batch,
    )

    result = await controller._retry_orchestrator.retry_tool_batch_after_contract_violation(
        turn_id="turn_retry_existing_edit_blocks",
        context=context,
        tool_definitions=[
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "edit_blocks"}},
            {"type": "function", "function": {"name": "write_file"}},
            {"type": "function", "function": {"name": "edit_file"}},
            {"type": "function", "function": {"name": "execute_command"}},
        ],
        state_machine=state_machine,
        ledger=ledger,
        stream=False,
        shadow_engine=None,
        original_decision=original_decision,
    )

    assert result["kind"] == "tool_batch_with_receipt"
    assert cast(list[object], captured["tool_choice_overrides"]) == [None]
    assert cast(list[set[str]], captured["llm_tool_definition_names"]) == [
        {"edit_blocks", "write_file", "execute_command"}
    ]
    assert cast(list[set[str]], captured["execute_allowed_names"]) == [{"edit_blocks", "write_file", "execute_command"}]


@pytest.mark.asyncio
async def test_retry_known_target_requires_read_switches_to_context_bootstrap(monkeypatch) -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    state_machine = TurnStateMachine(turn_id="turn_retry_context_bootstrap")
    ledger = TurnLedger(turn_id="turn_retry_context_bootstrap")
    context = [
        {
            "role": "user",
            "content": (
                "请继续完善 polaris/cells/roles/runtime/internal/session_orchestrator.py。"
                " 当前必须先 read_file 再修改。"
            ),
        }
    ]
    captured: dict[str, object] = {"execute_calls": 0}
    decode_calls = 0

    async def _fake_call_llm_for_decision(
        ctx,
        tool_definitions,
        llm_ledger,
        *,
        tool_choice_override=None,
        model_override=None,
        temperature_override=None,
        max_tokens_floor=None,
    ):
        captured["retry_context"] = ctx
        captured["retry_tool_definitions"] = list(tool_definitions)
        captured["retry_ledger"] = llm_ledger
        captured["retry_tool_choice_override"] = tool_choice_override
        return RawLLMResponse(content="", native_tool_calls=[])

    def _fake_decode(_response, _turn_id):
        nonlocal decode_calls
        decode_calls += 1
        if decode_calls == 1:
            return _canonical_decision(
                turn_id="turn_retry_context_bootstrap",
                kind=TurnDecisionKind.TOOL_BATCH,
                metadata={"workspace": "."},
                invocations=[
                    {
                        "tool_name": "glob",
                        "arguments": {"pattern": "**/*session_orchestrator*"},
                    }
                ],
            )
        return _canonical_decision(
            turn_id="turn_retry_context_bootstrap",
            kind=TurnDecisionKind.TOOL_BATCH,
            metadata={"workspace": "."},
            invocations=[
                {
                    "tool_name": "edit_file",
                    "arguments": {
                        "file": "polaris/cells/roles/runtime/internal/session_orchestrator.py",
                        "search": "class RoleSessionOrchestrator:",
                        "replace": 'class RoleSessionOrchestrator:\n    """Coordinate role session lifecycle."""',
                    },
                }
            ],
        )

    async def _fake_execute_tool_batch(
        decision,
        sm,
        lg,
        exec_context,
        *,
        stream,
        shadow_engine,
        allowed_tool_names=None,
        count_towards_batch_limit=True,
    ):
        execute_calls = int(captured["execute_calls"]) + 1
        captured["execute_calls"] = execute_calls
        if execute_calls == 1:
            raise RuntimeError(
                "single_batch_contract_violation: target_files_known_without_read_evidence; requires_bootstrap_read"
            )
        return _successful_write_batch_result(
            "polaris/cells/roles/runtime/internal/session_orchestrator.py",
            tool_name="edit_file",
        )

    async def _fake_execute_read_bootstrap_batch(
        *,
        turn_id,
        workspace,
        tool_batch,
        ledger,
    ):
        captured["bootstrap_turn_id"] = turn_id
        captured["bootstrap_workspace"] = workspace
        captured["bootstrap_tool_batch"] = tool_batch
        return {
            "results": [
                {
                    "tool_name": "read_file",
                    "status": "success",
                    "result": {
                        "file": "polaris/cells/roles/runtime/internal/session_orchestrator.py",
                        "content": "class RoleSessionOrchestrator:",
                    },
                }
            ]
        }

    monkeypatch.setattr(controller, "_call_llm_for_decision", _fake_call_llm_for_decision)
    monkeypatch.setattr(controller.decoder, "decode", _fake_decode)
    monkeypatch.setattr(controller._retry_orchestrator, "execute_tool_batch", _fake_execute_tool_batch)
    monkeypatch.setattr(
        controller._retry_orchestrator, "execute_read_bootstrap_batch", _fake_execute_read_bootstrap_batch
    )

    result = await controller._retry_orchestrator.retry_tool_batch_after_contract_violation(
        turn_id="turn_retry_context_bootstrap",
        context=context,
        tool_definitions=[
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "glob"}},
            {"type": "function", "function": {"name": "edit_file"}},
        ],
        state_machine=state_machine,
        ledger=ledger,
        stream=False,
        shadow_engine=None,
    )

    assert result["kind"] == "tool_batch_with_receipt"
    assert captured["execute_calls"] == 2
    bootstrap_batch = captured["bootstrap_tool_batch"]
    if isinstance(bootstrap_batch, dict):
        bootstrap_invocations = list(bootstrap_batch.get("invocations", []))
    else:
        bootstrap_invocations = list(getattr(bootstrap_batch, "invocations", []) or [])
    assert bootstrap_invocations
    first_bootstrap = bootstrap_invocations[0]
    if isinstance(first_bootstrap, dict):
        first_tool_name = first_bootstrap.get("tool_name")
        first_file = (first_bootstrap.get("arguments") or {}).get("file")
    else:
        first_tool_name = getattr(first_bootstrap, "tool_name", "")
        first_file = getattr(first_bootstrap, "arguments", {}).get("file")
    assert first_tool_name == "read_file"
    assert first_file == "polaris/cells/roles/runtime/internal/session_orchestrator.py"


def test_build_decision_messages_omits_task_contract_for_read_only_request() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    context = [{"role": "user", "content": "读取 README.md 的前 20 行并总结"}]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "repo_read_head"}},
    ]
    messages = controller._build_decision_messages(context, tool_definitions)

    system_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "system"]
    assert any("SYSTEM CONSTRAINT (Execution)" in text for text in system_messages)
    assert not any("TASK CONTRACT (single-batch planning)" in text for text in system_messages)


def test_build_decision_messages_omits_mutation_contract_for_readonly_qa_judgement() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    context = [
        {
            "role": "system",
            "content": (
                "<role_definition>\n"
                "你是 Polaris 体系中的 **QA**。\n"
                "</role_definition>\n"
                "Polaris QA output contract:\n"
                "Return exactly one JSON object and nothing else.\n"
                "Do not inspect the workspace. Do not call tools.\n"
                "代码写入: 禁止"
            ),
        },
        {
            "role": "user",
            "content": (
                "Review the QA target using only the deterministic evidence already collected by Polaris.\n"
                "src/main.ts(29,18): error TS2339\n"
                "src/models/Inventory.ts(74,10): error TS2540\n"
                "tsconfig.json is present."
            ),
        },
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "execute_command"}},
    ]
    ledger = TurnLedger(turn_id="turn_qa_readonly")
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))

    messages = controller._build_decision_messages(context, tool_definitions, ledger)

    system_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "system"]
    assert any("SYSTEM CONSTRAINT (QA Readonly)" in text for text in system_messages)
    assert not any("SINGLE-BATCH execution" in text for text in system_messages)
    assert not any("TASK CONTRACT (single-batch planning)" in text for text in system_messages)
    assert not any("Mutation target files detected from user request" in text for text in system_messages)
    assert not any("write/edit tools" in text for text in system_messages)


def test_build_decision_messages_uses_single_batch_guard_for_materialize_contract() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    context = [{"role": "user", "content": "请实现 src/app.ts 并写入文件。"}]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]
    ledger = TurnLedger(turn_id="turn_materialize_single_batch")
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))

    messages = controller._build_decision_messages(context, tool_definitions, ledger)

    system_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "system"]
    assert any("SINGLE-BATCH execution" in text for text in system_messages)
    assert not any("This turn supports multi-turn workflow" in text for text in system_messages)
    assert not any("Subsequent turns: You MUST call write/edit tools" in text for text in system_messages)


def test_build_decision_messages_keeps_bootstrap_write_retry_user_final() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    user_request = "Implement index.html and styles.css for the static resume page."
    context = [
        {
            "role": "system",
            "content": (
                "WRITE RETRY MODE: bootstrap read context has been collected.\nMandatory write tool: write_file."
            ),
        },
        {"role": "user", "content": user_request},
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]
    ledger = TurnLedger(turn_id="turn_bootstrap_write_retry")
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))

    messages = controller._build_decision_messages(context, tool_definitions, ledger)

    system_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "system"]
    assert any("WRITE RETRY MODE" in text for text in system_messages)
    assert any("SINGLE-BATCH execution" in text for text in system_messages)
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == user_request


def test_build_decision_messages_omits_write_contract_for_toolless_proposal() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    context = [
        {
            "role": "user",
            "content": (
                "[mode:propose] Non-interactive batch worker. Return only fenced "
                "file sections for: src/app.py. The first non-whitespace text "
                "must be ```file:. No progress notes. Do not call tools."
            ),
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "edit_file"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]
    messages = controller._build_decision_messages(context, tool_definitions)

    system_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "system"]
    assert any("SYSTEM CONSTRAINT (Proposal)" in text for text in system_messages)
    assert not any("SYSTEM CONSTRAINT (Execution)" in text for text in system_messages)
    assert not any("TASK CONTRACT (single-batch planning)" in text for text in system_messages)
    assert not any("This request requires mutation." in text for text in system_messages)
    assert not any("write/edit tools" in text for text in system_messages)


def test_build_decision_messages_omits_mutation_contract_for_super_readonly_stage() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    context = [
        {
            "role": "user",
            "content": (
                "[mode:analyze]\n"
                "[SUPER_MODE_READONLY_STAGE]\n"
                "stage_role: pm\n"
                "stage_type: readonly_planning\n"
                "original_user_request:\n"
                "进一步完善 session_orchestrator.py\n"
                "[/SUPER_MODE_READONLY_STAGE]"
            ),
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "glob"}},
        {"type": "function", "function": {"name": "edit_file"}},
    ]

    messages = controller._build_decision_messages(context, tool_definitions)
    system_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "system"]

    assert any("SUPER readonly planning stage" in text for text in system_messages)
    assert not any("TASK CONTRACT (single-batch planning)" in text for text in system_messages)
    assert not any("This request requires mutation." in text for text in system_messages)
    assert not any("subsequent turns: You MUST call write/edit tools" in text for text in system_messages)


def test_build_decision_messages_does_not_infer_verify_from_runtime_word() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    context = [
        {
            "role": "user",
            "content": (
                "请把 server.py 里的 localhost 替换为 0.0.0.0。\n"
                "Runtime constraint: one decision + one tool-call batch."
            ),
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "repo_rg"}},
        {"type": "function", "function": {"name": "search_replace"}},
        {"type": "function", "function": {"name": "execute_command"}},
    ]
    messages = controller._build_decision_messages(context, tool_definitions)
    system_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "system"]

    assert any("TASK CONTRACT (single-batch planning)" in text for text in system_messages)
    assert not any("Verification is required by the user." in text for text in system_messages)


def test_single_target_quality_repair_contract_suppresses_generic_verify_and_templates() -> None:
    context = [
        {
            "role": "user",
            "content": (
                "[mode:materialize]\n"
                "任务要求补齐 requirements.txt, src/__init__.py, src/models/weather.py。\n"
                "Verification is required by the user. Include an available verification step.\n"
                "SINGLE MISSING TARGET REPAIR:\n"
                "[director_quality_repair:write_only_single_target]\n"
                "- Target path: src/models/weather.py\n"
                "- Emit exactly one write_file tool call for that target path.\n"
                "- Do not read files first. Do not list directories. Do not explore. Do not explain.\n"
            ),
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "repo_tree"}},
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "execute_command"}},
    ]

    hint, _metadata = build_single_batch_task_contract_hint(context, tool_definitions)

    assert "Single-target quality repair is active" in hint
    assert "src/models/weather.py" in hint
    assert "exactly one write/edit tool call" in hint
    assert "Verification is required by the user" not in hint
    assert "Include verification tools" not in hint
    assert "POSITIVE TOOL SEQUENCE TEMPLATES" not in hint
    assert "requirements.txt, src/__init__.py" not in hint
    assert "VALID pattern: emit [search/read tool]" not in hint
    assert "Mutation target files detected from user request" not in hint
    assert "TOOL FAILURE RECOVERY PROTOCOL" not in hint


def test_single_target_quality_repair_drops_stale_verify_contract_requirements() -> None:
    context = [
        {
            "role": "user",
            "content": (
                "[mode:materialize]\n"
                "MATERIALIZATION QUALITY REPAIR MODE:\n"
                "EXISTING FAILED TARGET FILES — repair these exact paths NOW.\n"
                "- src/main.ts\n"
                "SINGLE FAILED TARGET REPAIR:\n"
                "[director_quality_repair:edit_preferred_single_target]\n"
                "- Target path: src/main.ts\n"
                "- For edit_file, use an exact SEARCH string from the current content.\n"
                "Do not list directories. Do not explore. Do not explain.\n"
                "Quality errors:\n"
                "- Artifact quality scan failed: TypeScript project typecheck failed: src/main.ts(10,10): "
                "error TS6133: 'Market' is declared but its value is never read.\n"
            ),
            "metadata": {
                "tool_contract": {
                    "single_batch": True,
                    "required_tools": ["execute_command"],
                    "min_tool_calls": 3,
                }
            },
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "edit_file"}},
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "execute_command"}},
    ]

    hint, _metadata = build_single_batch_task_contract_hint(context, tool_definitions)

    assert "Single-target quality repair is active" in hint
    assert "`src/main.ts`" in hint
    assert "Contract-required tools are mandatory" not in hint
    assert "execute_command" not in hint
    assert "Contract minimum tool-call count for this batch: >= 1." in hint
    assert ">= 3" not in hint
    assert "Mutation target files detected from user request" not in hint
    assert "TOOL FAILURE RECOVERY PROTOCOL" not in hint


def test_single_batch_hint_does_not_suggest_mixed_read_write_without_bypass() -> None:
    context = [
        {
            "role": "user",
            "content": "Read src/app.ts and update src/app.ts in this single-batch task.",
            "metadata": {
                "tool_contract": {
                    "single_batch": True,
                    "required_tools": ["read_file", "edit_file"],
                    "min_tool_calls": 2,
                }
            },
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "edit_file"}},
    ]

    hint, _metadata = build_single_batch_task_contract_hint(context, tool_definitions)

    assert "VALID pattern: emit [search/read tool] then [write tool] in the same batch" not in hint
    assert "Do not mix read/search tools with write/edit tools in one parallel batch" in hint


def test_single_batch_hint_allows_mixed_read_write_when_contract_bypasses_barrier() -> None:
    context = [
        {
            "role": "user",
            "content": "Read src/app.ts and update src/app.ts in this single-batch task.",
            "metadata": {
                "tool_contract": {
                    "single_batch": True,
                    "required_tools": ["read_file", "edit_file"],
                    "min_tool_calls": 2,
                    "allow_mixed_read_write_batch": True,
                }
            },
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "edit_file"}},
    ]

    hint, _metadata = build_single_batch_task_contract_hint(context, tool_definitions)

    assert "VALID pattern: emit [search/read tool] then [write tool] in the same batch" in hint
    assert "Do not mix read/search tools with write/edit tools in one parallel batch" not in hint


def test_single_batch_required_groups_prioritize_active_edit_blocks() -> None:
    context = [
        {
            "role": "user",
            "content": "Update src/app.ts in one batch.",
            "metadata": {
                "tool_contract": {
                    "single_batch": True,
                    "required_tool_groups": [["write_file", "edit_file", "repo_apply_diff", "edit_blocks"]],
                    "allow_mixed_read_write_batch": True,
                }
            },
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "edit_file"}},
        {"type": "function", "function": {"name": "repo_apply_diff"}},
        {"type": "function", "function": {"name": "edit_blocks"}},
    ]

    hint, _metadata = build_single_batch_task_contract_hint(context, tool_definitions)

    assert (
        "Use available tools to satisfy each group in order: [edit_blocks, repo_apply_diff, edit_file, write_file]."
        in hint
    )


def test_build_decision_messages_includes_benchmark_required_tools_hint() -> None:
    controller = TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )
    context = [
        {
            "role": "user",
            "content": "在 server.py 中查找并替换 localhost。",
            "metadata": {
                "tool_contract": {
                    "single_batch": True,
                    "required_tools": ["repo_rg", "search_replace"],
                    "min_tool_calls": 2,
                    "allow_mixed_read_write_batch": True,
                }
            },
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "repo_rg"}},
        {"type": "function", "function": {"name": "search_replace"}},
    ]
    messages = controller._build_decision_messages(context, tool_definitions)
    system_messages = [str(item.get("content") or "") for item in messages if item.get("role") == "system"]

    assert any("Contract-required tools are mandatory in this single batch" in text for text in system_messages)
    assert any("repo_rg, search_replace" in text for text in system_messages)
