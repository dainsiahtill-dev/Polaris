"""Tests for KernelOne LLM execution budget policy."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.kernelone.llm import budget_policy
from polaris.kernelone.llm.budget_policy import (
    CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKEN_ENV,
    DEFAULT_CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKENS,
    DEFAULT_DIRECTOR_DISPATCH_TIMEOUT_SECONDS,
    DIRECTOR_DISPATCH_TIMEOUT_ENV_KEYS,
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
    chief_engineer_portfolio_generation_floor_seconds,
    chief_engineer_portfolio_output_tokens,
    chief_engineer_structured_output_tokens,
    clamp_output_tokens,
    classify_turn_kind,
    forced_write_output_token_ceiling,
    forced_write_retry_timeout_seconds,
    resolve_director_dispatch_timeout_seconds,
    resolve_execution_budget,
)


@pytest.mark.parametrize("key", ["llm_max_tokens", "max_output_tokens", "max_tokens"])
def test_foundation_output_budget_top_level_aliases(key: str) -> None:
    resolver = getattr(budget_policy, "resolve_output_budget_tokens", None)
    assert callable(resolver), "Pure output resolution must be available without importing a Cell"
    assert resolver(60_000, {key: "64000"}) == 64_000


@pytest.mark.parametrize(
    "payload_key",
    [
        "task_execution_contract",
        "director_execution_contract",
        "task_execution_strategy",
        "director_execution_strategy",
        "execution_strategy",
    ],
)
@pytest.mark.parametrize("key", ["output_budget_tokens", "llm_max_tokens", "max_output_tokens", "max_tokens"])
@pytest.mark.parametrize("nested", [False, True])
def test_foundation_output_budget_contract_aliases(payload_key: str, key: str, nested: bool) -> None:
    resolver = getattr(budget_policy, "resolve_output_budget_tokens", None)
    assert callable(resolver), "Pure contract scanning must belong to KernelOne"
    payload = {"context_budget": {key: "20000"}} if nested else {key: "20000"}
    assert resolver(60_000, {payload_key: payload}) == 20_000


@pytest.mark.parametrize(
    ("requested", "context", "expected"),
    [
        (60_000, None, 60_000),
        (None, {}, 4_000),
        ("bad", {}, 4_000),
        (0, {}, 1),
        (-2, {}, 1),
        (128_001, {}, 128_000),
        ("64000", {}, 64_000),
        (2.9, {}, 2),
        (True, {}, 1),
        (60_000, {"llm_max_tokens": True}, 1),
        (60_000, {"llm_max_tokens": 2.9}, 2),
        (60_000, {"llm_max_tokens": 128_001}, 128_000),
        (60_000, {"llm_max_tokens": 0, "max_output_tokens": -2, "max_tokens": "bad"}, 60_000),
        (60_000, {"llm_max_tokens": "bad", "max_tokens": 20_000}, 20_000),
        (60_000, {"llm_max_tokens": 1_024, "max_output_tokens": 2_048, "max_tokens": 3_072}, 1_024),
        (60_000, {"max_tokens_floor": 96_000}, 60_000),
        (60_000, {"task_execution_strategy": {"budget_policy": {"max_tokens": 20_000}}}, 60_000),
        (60_000, {"task_execution_strategy": {"llm_sampling": {"max_tokens": 20_000}}}, 60_000),
        (
            60_000,
            {"task_execution_contract": {"context_budget": {"output_budget_tokens": 20_000}, "llm_max_tokens": 64_000}},
            20_000,
        ),
        (
            60_000,
            {
                "task_execution_contract": {
                    "output_budget_tokens": 64_000,
                    "context_budget": {"output_budget_tokens": 20_000},
                }
            },
            64_000,
        ),
        (
            60_000,
            {
                "task_execution_contract": {"max_tokens": 20_000},
                "director_execution_contract": {"output_budget_tokens": 64_000},
            },
            20_000,
        ),
        (
            60_000,
            {"task_execution_contract": {"max_tokens": "bad"}, "director_execution_strategy": {"max_tokens": 64_000}},
            64_000,
        ),
        (60_000, {"task_execution_contract": [], "execution_strategy": {"max_tokens": 20_000}}, 20_000),
        (60_000, [], 60_000),
    ],
)
def test_foundation_output_budget_preserves_coercion_precedence_and_defaults(
    requested: Any,
    context: Any,
    expected: int,
) -> None:
    resolver = getattr(budget_policy, "resolve_output_budget_tokens", None)
    assert callable(resolver), "Canonical output resolution must be available in KernelOne"
    assert resolver(requested, context) == expected


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (None, DEFAULT_CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKENS),
        ("", DEFAULT_CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKENS),
        ("invalid", DEFAULT_CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKENS),
        ("0", DEFAULT_CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKENS),
        ("20000", 20_000),
        (str(HARD_OUTPUT_TOKEN_CLAMP + 1), HARD_OUTPUT_TOKEN_CLAMP),
    ],
)
def test_chief_engineer_structured_output_budget_has_one_policy(
    raw_value: str | None,
    expected: int,
) -> None:
    environ = {} if raw_value is None else {CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKEN_ENV: raw_value}

    assert chief_engineer_structured_output_tokens(environ) == expected


@pytest.mark.parametrize(
    ("task_count", "raw_cap", "expected"),
    [
        (1, None, 60_000),
        (8, None, 60_000),
        (20, None, 60_000),
        (32, None, 60_000),
        (64, None, 60_000),
        (10, "20000", 20_000),
    ],
)
def test_chief_engineer_portfolio_budget_preserves_planning_role_policy(
    task_count: int,
    raw_cap: str | None,
    expected: int,
) -> None:
    environ = {} if raw_cap is None else {CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKEN_ENV: raw_cap}

    assert chief_engineer_portfolio_output_tokens(task_count, environ) == expected


@pytest.mark.parametrize(
    ("task_count", "expected_floor"),
    [
        (1, 750.0),
        (8, 750.0),
        (20, 750.0),
    ],
)
def test_chief_engineer_portfolio_generation_floor_scales_with_output_budget(
    task_count: int,
    expected_floor: float,
) -> None:
    assert chief_engineer_portfolio_generation_floor_seconds(task_count) == pytest.approx(expected_floor)


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


def test_director_dispatch_timeout_resolver_owns_env_key_order() -> None:
    first_key, second_key, *_ = DIRECTOR_DISPATCH_TIMEOUT_ENV_KEYS

    assert resolve_director_dispatch_timeout_seconds({}) == DEFAULT_DIRECTOR_DISPATCH_TIMEOUT_SECONDS
    assert (
        resolve_director_dispatch_timeout_seconds(
            {
                first_key: "not-a-number",
                second_key: "2100",
            }
        )
        == 2100
    )
    assert resolve_director_dispatch_timeout_seconds({first_key: "10"}) == DEFAULT_DIRECTOR_DISPATCH_TIMEOUT_SECONDS


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


def test_resolve_execution_budget_freezes_actual_request_values() -> None:
    budget = resolve_execution_budget(
        role_id="director",
        context={"required_tool_retry": True},
        request_options={"tool_choice": "auto"},
        max_output_tokens=123_456,
        llm_timeout_seconds=77.0,
        request_timeout_seconds=88.0,
        context_max_tokens_present=True,
        context_timeout_present=True,
        output_floor_tokens=7_000,
        output_floor_provenance="transaction_kernel_retry_output_budget_bounded",
    )

    assert budget.max_output_tokens == 123_456
    assert budget.output_floor_tokens == 7_000
    assert budget.llm_timeout_seconds == 77.0
    assert budget.request_timeout_seconds == 88.0
    assert budget.turn_kind == TURN_KIND_REQUIRED_TOOL_RETRY
    assert budget.provenance == {
        "max_output_tokens": "context_override",
        "output_floor_tokens": "transaction_kernel_retry_output_budget_bounded",
        "llm_timeout_seconds": "director_timeout_policy",
        "request_timeout_seconds": "same_funnel_as_llm_timeout",
        "turn_kind": "classify_turn_kind",
    }
