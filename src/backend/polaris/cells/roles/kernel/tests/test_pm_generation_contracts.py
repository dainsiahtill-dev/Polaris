"""Atomic native outcome, public budget and exact visible-response binding."""

from hashlib import sha256
from types import SimpleNamespace

import pytest
from polaris.cells.roles.kernel.internal.kernel.role_result_projection import role_result_metadata_from_profile
from polaris.cells.roles.kernel.internal.transaction.llm_response_metadata import llm_response_metadata_from_usage


def outcome(text="same", call="a" * 32, reason="max_tokens", budget=4000):
    return {
        "schema_version": "roles.kernel.nonstream_generation_outcome.v1",
        "run_id": "pm-child",
        "call_id": call,
        "native_finish_reason": reason,
        "max_tokens": budget,
        "response_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "request_freeze_id": None,
        "final_semantic_request_hash": None,
    }


def test_atomic_outcome_survives_both_real_filters():
    payload = outcome()
    first = llm_response_metadata_from_usage({"generation_outcome": payload})
    assert first["generation_outcome"] == payload
    second = role_result_metadata_from_profile(profile=SimpleNamespace(), llm_response_metadata=first)
    assert second["generation_outcome"] == payload
    first["generation_outcome"]["max_tokens"] = 3
    assert second["generation_outcome"] == payload


def test_canonical_budget_default_precedence_and_clamp():
    from polaris.cells.roles.kernel.internal.llm_caller.helpers import resolve_max_tokens
    from polaris.cells.roles.kernel.public.llm_caller_contracts import resolve_role_output_budget
    from polaris.kernelone.llm.budget_policy import resolve_output_budget_tokens

    contexts = [
        None,
        {"llm_max_tokens": 3072, "max_output_tokens": 2048, "max_tokens": 1024, "contract": {"max_tokens": 1536}},
        {"llm_max_tokens": "bad", "max_tokens": 2048},
        {"task_execution_contract": {"context_budget": {"max_tokens": 1536}}},
        {"max_tokens": 1000000},
    ]
    assert resolve_role_output_budget() == 4000
    assert resolve_role_output_budget(context=contexts[1]) == 3072
    for context in contexts:
        assert resolve_role_output_budget(context=context) == resolve_max_tokens(4000, context)
        assert resolve_role_output_budget(context=context) == resolve_output_budget_tokens(4000, context)


@pytest.mark.parametrize("key", ["llm_max_tokens", "max_output_tokens", "max_tokens"])
@pytest.mark.parametrize("budget", [20_000, 64_000])
def test_role_budget_facades_preserve_foundation_top_level_overrides(key: str, budget: int) -> None:
    from polaris.cells.roles.kernel.internal.llm_caller.helpers import resolve_max_tokens
    from polaris.cells.roles.kernel.public.llm_caller_contracts import resolve_role_output_budget
    from polaris.kernelone.llm.budget_policy import resolve_output_budget_tokens

    context = {key: str(budget)}
    assert resolve_role_output_budget(context=context, requested=60_000) == budget
    assert resolve_max_tokens(60_000, context) == budget
    assert resolve_output_budget_tokens(60_000, context) == budget


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
def test_role_budget_facades_preserve_foundation_nested_overrides(payload_key: str, key: str, nested: bool) -> None:
    from polaris.cells.roles.kernel.internal.llm_caller.helpers import (
        resolve_context_output_budget_tokens,
        resolve_max_tokens,
    )
    from polaris.cells.roles.kernel.public.llm_caller_contracts import resolve_role_output_budget
    from polaris.kernelone.llm.budget_policy import resolve_output_budget_tokens

    payload = {"context_budget": {key: "64000"}} if nested else {key: "64000"}
    context = {payload_key: payload}
    assert resolve_context_output_budget_tokens(context) == 64_000
    assert resolve_role_output_budget(context=context, requested=60_000) == 64_000
    assert resolve_max_tokens(60_000, context) == 64_000
    assert resolve_output_budget_tokens(60_000, context) == 64_000


def test_role_budget_facade_keeps_mapping_boundary_without_widening_helper_scan() -> None:
    from types import MappingProxyType

    from polaris.cells.roles.kernel.internal.llm_caller.helpers import resolve_max_tokens
    from polaris.cells.roles.kernel.public.llm_caller_contracts import resolve_role_output_budget
    from polaris.kernelone.llm.budget_policy import resolve_output_budget_tokens

    context = MappingProxyType({"max_tokens": 20_000})
    assert resolve_role_output_budget(context=context, requested=60_000) == 20_000
    assert resolve_max_tokens(60_000, context) == 60_000
    assert resolve_output_budget_tokens(60_000, context) == 60_000


@pytest.mark.parametrize(
    "mutations,reason",
    [
        ({}, None),
        ({"run_id": "foreign"}, "generation_outcome_run_mismatch"),
        ({"response_sha256": "f" * 64}, "generation_outcome_response_binding_unavailable"),
        ({"call_id": "bad"}, "generation_outcome_malformed"),
        ({"max_tokens": True}, "generation_outcome_malformed"),
        ({"max_tokens": 128001}, "generation_outcome_malformed"),
        ({"native_finish_reason": "RAW BODY"}, "generation_outcome_malformed"),
        ({"request_freeze_id": "b" * 32}, "generation_outcome_malformed"),
        ({"raw": "secret"}, "generation_outcome_malformed"),
    ],
)
def test_strict_binding(mutations, reason):
    from polaris.cells.roles.kernel.public.generation_outcome import bind_role_generation_outcome

    item, unavailable = bind_role_generation_outcome(
        {"generation_outcome": {**outcome(), **mutations}}, response_text="same", expected_run_id="pm-child"
    )
    assert unavailable == reason
    assert (item is not None) is (reason is None)


def test_missing_and_unknown_are_not_inferred():
    from polaris.cells.roles.kernel.public.generation_outcome import bind_role_generation_outcome

    assert bind_role_generation_outcome({}, response_text="same", expected_run_id="pm-child") == (
        None,
        "generation_outcome_missing",
    )
    item, reason = bind_role_generation_outcome(
        {"generation_outcome": outcome(reason=None, budget=None)}, response_text="same", expected_run_id="pm-child"
    )
    assert reason is None and item.native_finish_reason is None and item.max_tokens is None
