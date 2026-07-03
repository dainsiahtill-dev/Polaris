"""Tests for KernelOne LLM execution budget policy."""

from __future__ import annotations

import pytest
from polaris.kernelone.llm.budget_policy import (
    FORCED_WRITE_OUTPUT_TOKEN_ENV,
    FORCED_WRITE_RETRY_TIMEOUT_ENV,
    HARD_OUTPUT_TOKEN_CLAMP,
    TURN_KIND_FINALIZATION,
    TURN_KIND_FIRST_CALL,
    TURN_KIND_FORCED_WRITE_RETRY,
    TURN_KIND_ORDINARY_FOLLOWUP,
    TURN_KIND_REASONING_TRUNCATION_RETRY,
    TURN_KIND_REPAIR_SUBCALL,
    TURN_KIND_REQUIRED_TOOL_RETRY,
    ResolvedBudgetV1,
    clamp_output_tokens,
    classify_turn_kind,
    forced_write_output_token_ceiling,
    forced_write_retry_timeout_seconds,
)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, 7_000),
        ("", 7_000),
        ("not-an-int", 7_000),
        ("0", 7_000),
        ("64", 512),
        ("8192", 8_192),
        (str(HARD_OUTPUT_TOKEN_CLAMP + 1), HARD_OUTPUT_TOKEN_CLAMP),
    ],
)
def test_forced_write_output_token_ceiling_parses_single_env_flag(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str | None,
    expected: int,
) -> None:
    if raw_value is None:
        monkeypatch.delenv(FORCED_WRITE_OUTPUT_TOKEN_ENV, raising=False)
    else:
        monkeypatch.setenv(FORCED_WRITE_OUTPUT_TOKEN_ENV, raw_value)

    assert forced_write_output_token_ceiling() == expected


@pytest.mark.parametrize(
    ("raw_value", "upper", "expected"),
    [
        (None, 90.0, 90.0),
        ("", 180.0, 120.0),
        ("not-a-float", 180.0, 120.0),
        ("5", 180.0, 10.0),
        ("45", 180.0, 45.0),
        ("300", 180.0, 180.0),
    ],
)
def test_forced_write_retry_timeout_seconds_is_bounded_by_stage_timeout(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str | None,
    upper: float,
    expected: float,
) -> None:
    if raw_value is None:
        monkeypatch.delenv(FORCED_WRITE_RETRY_TIMEOUT_ENV, raising=False)
    else:
        monkeypatch.setenv(FORCED_WRITE_RETRY_TIMEOUT_ENV, raw_value)

    assert forced_write_retry_timeout_seconds(upper=upper) == expected


def test_clamp_output_tokens_applies_single_hard_cap() -> None:
    assert clamp_output_tokens(0, floor=512) == 512
    assert clamp_output_tokens(4_096, floor=512) == 4_096
    assert clamp_output_tokens(HARD_OUTPUT_TOKEN_CLAMP + 10, floor=512) == HARD_OUTPUT_TOKEN_CLAMP


@pytest.mark.parametrize(
    ("context", "options", "expected"),
    [
        (
            {"_transaction_kernel_forced_tool_definitions": [], "_transaction_kernel_forced_tool_choice": "none"},
            {},
            TURN_KIND_FINALIZATION,
        ),
        ({"required_tool_retry": True}, {}, TURN_KIND_REQUIRED_TOOL_RETRY),
        ({"reasoning_truncation_retry": True}, {}, TURN_KIND_REASONING_TRUNCATION_RETRY),
        ({"director_no_write_materialization_retry": True}, {}, TURN_KIND_FORCED_WRITE_RETRY),
        ({}, {"stage_label": "quality_repair.python"}, TURN_KIND_REPAIR_SUBCALL),
        ({"director_first_call_output_budget": {"max_output_tokens": 48_000}}, {}, TURN_KIND_FIRST_CALL),
        ({}, {}, TURN_KIND_ORDINARY_FOLLOWUP),
    ],
)
def test_classify_turn_kind_uses_single_priority_order(
    context: dict[str, object],
    options: dict[str, object],
    expected: str,
) -> None:
    assert classify_turn_kind(context, options) == expected


def test_resolved_budget_payload_is_json_ready_projection() -> None:
    payload = ResolvedBudgetV1(
        max_output_tokens=48_000,
        output_floor_tokens=7_000,
        llm_timeout_seconds=120.0,
        request_timeout_seconds=130.0,
        turn_kind=TURN_KIND_REQUIRED_TOOL_RETRY,
        provenance={"max_output_tokens": "context_override"},
    ).to_payload()

    assert payload == {
        "schema_version": "kernelone.execution_budget.v1",
        "max_output_tokens": 48_000,
        "output_floor_tokens": 7_000,
        "llm_timeout_seconds": 120.0,
        "request_timeout_seconds": 130.0,
        "turn_kind": TURN_KIND_REQUIRED_TOOL_RETRY,
        "provenance": {"max_output_tokens": "context_override"},
    }
