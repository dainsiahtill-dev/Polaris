from __future__ import annotations

import pytest
from polaris.cells.control_plane.run_ledger.public.failure_evidence import (
    FailureClassV1,
    normalize_failure_class,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnFailureClass,
    TurnId,
    TurnResult,
)
from pydantic import ValidationError


class TestToolInvocationV2:
    def test_infers_effect_type_and_execution_mode(self) -> None:
        tool = ToolInvocation(
            call_id=ToolCallId("call_read"),
            tool_name="read_file",
            arguments={"path": "main.py"},
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )

        assert tool["effect_type"] == ToolEffectType.READ
        assert tool["execution_mode"] == ToolExecutionMode.READONLY_PARALLEL

    def test_forbids_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            ToolInvocation(
                call_id=ToolCallId("call_bad"),
                tool_name="read_file",
                arguments={},
                effect_type=ToolEffectType.READ,
                execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                unknown_field=True,
            )

    def test_is_frozen(self) -> None:
        tool = ToolInvocation(
            call_id=ToolCallId("call_write"),
            tool_name="write_file",
            arguments={"path": "main.py", "content": "print(1)"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )

        with pytest.raises(ValidationError):
            tool.tool_name = "edit_file"  # type: ignore[misc]


class TestTurnResultV2:
    def test_turn_result_defaults_protocol_version(self) -> None:
        decision = TurnDecision(
            turn_id=TurnId("turn_1"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="done",
            tool_batch=None,
            finalize_mode=FinalizeMode.NONE,
            domain="document",
        )

        result = TurnResult(
            turn_id=TurnId("turn_1"),
            kind="final_answer",
            visible_content="done",
            decision=decision,
        )

        assert result["protocol_version"] == "2.2"

    def test_tool_batch_supports_readonly_serial(self) -> None:
        serial_tool = ToolInvocation(
            call_id=ToolCallId("call_serial"),
            tool_name="read_file",
            arguments={"path": "ordered.py"},
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_SERIAL,
        )

        batch = ToolBatch(batch_id=BatchId("batch_1"), readonly_serial=[serial_tool], invocations=[serial_tool])

        assert batch["readonly_serial"][0]["execution_mode"] == ToolExecutionMode.READONLY_SERIAL


class TestTurnFailureClassBoundary:
    def test_turn_failure_classes_do_not_alias_run_ledger_taxonomy(self) -> None:
        run_ledger_values = {item.value for item in FailureClassV1}

        for failure_class in TurnFailureClass:
            assert failure_class.value not in run_ledger_values
            assert normalize_failure_class(failure_class.value) == failure_class.value
