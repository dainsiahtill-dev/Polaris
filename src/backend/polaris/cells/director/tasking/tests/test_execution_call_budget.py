"""Effective call budgets are derived, never promoted into task authority."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.cells.director.tasking.public.contracts import TaskExecutionStrategyV1
from polaris.cells.director.tasking.public.execution_guidance import (
    apply_task_execution_strategy_overrides,
    resolve_task_execution_profile,
    resolve_task_execution_strategy,
)


def test_call_strategy_retains_task_origin_and_rebuilds_bound_hashes() -> None:
    """Catch task defaults leaking into call hashes or erasing task provenance."""
    profile = resolve_task_execution_profile(subject="Implement service", target_files=["src/service.py"])
    strategy = resolve_task_execution_strategy(profile)
    original = deepcopy(strategy.to_dict())
    context: dict[str, Any] = {"llm_max_tokens": 3000}
    metadata: dict[str, Any] = {"execution_envelope_created_at": "2026-10-01T00:00:00Z"}
    apply_task_execution_strategy_overrides(context=context, metadata=metadata, profile=profile, strategy=strategy)
    effective = context["task_execution_strategy"]
    assert effective["output_budget_tokens"] == 3000
    derivation = effective["signal_evidence"]["call_output_budget_derivation"]
    assert derivation["task_strategy"] == original
    assert derivation["effective_output_budget_tokens"] == 3000
    assert derivation["ceiling_sources"]["context.llm_max_tokens"] == 3000
    assert derivation["observation_only"] is True
    assert strategy.to_dict() == original
    assert strategy.output_budget_tokens == 128000
    for key in (
        "input_budget_tokens",
        "prompt_max_chars",
        "temperature",
        "evidence_requirements",
        "target_files",
        "scope_paths",
        "context_budget_policy",
    ):
        assert effective[key] == original[key]
    contract = context["task_execution_contract"]
    assert contract["audit_contract"]["strategy_hash"] == stable_hash(effective)
    assert metadata["task_execution_contract"] == contract
    envelope = context["task_execution_envelope"]
    assert envelope["budget_policy"]["output_budget_tokens"] == 3000
    assert envelope["model_policy"]["max_tokens"] == 3000
    assert envelope["authorization"]["allowed_write_paths"] == ["src/service.py"]
    assert envelope["audit_policy"]["final_provider_request_required"] is True
    assert envelope["audit_policy"]["receipt_required"] is True
    assert envelope["audit_policy"]["provenance_bundle_required"] is True
    assert context["execution_envelope_hash"] == envelope["envelope_hash"]
    assert metadata["execution_envelope_hash"] == envelope["envelope_hash"]
    # Change only the call limit; regenerated envelope and strategy hashes must change.
    other: dict[str, Any] = {"llm_max_tokens": 4000}
    apply_task_execution_strategy_overrides(context=other, metadata=dict(metadata), profile=profile, strategy=strategy)
    # Inherited metadata is still a 3000 ceiling and must not be enlarged to 4000.
    assert other["llm_max_tokens"] == 3000
    fresh: dict[str, Any] = {"llm_max_tokens": 4000}
    apply_task_execution_strategy_overrides(
        context=fresh,
        metadata={"execution_envelope_created_at": "2026-10-01T00:00:00Z"},
        profile=profile,
        strategy=strategy,
    )
    assert fresh["task_execution_envelope"]["envelope_hash"] != envelope["envelope_hash"]
    assert (
        fresh["task_execution_contract"]["audit_contract"]["strategy_hash"]
        != contract["audit_contract"]["strategy_hash"]
    )


@pytest.mark.parametrize("key", ["task_execution_envelope", "director_execution_envelope", "execution_envelope"])
def test_existing_envelope_budget_is_a_ceiling_not_a_new_authority(key: str) -> None:
    """A lower model-policy ceiling cannot be lost during envelope reconstruction."""
    profile = resolve_task_execution_profile(subject="Implement service", target_files=["src/service.py"])
    strategy = resolve_task_execution_strategy(profile)
    admission: dict[str, Any] = {"model_policy": {"max_tokens": 1100}}
    if key == "execution_envelope":
        admission["authorization"] = {"allowed_write_paths": ["src/service.py"]}
    context: dict[str, Any] = {key: admission}
    inherited = deepcopy(admission)
    metadata: dict[str, Any] = {}
    apply_task_execution_strategy_overrides(context=context, metadata=metadata, profile=profile, strategy=strategy)
    assert context["llm_max_tokens"] == 1100
    assert context["task_execution_strategy"]["output_budget_tokens"] == 1100
    assert context["task_execution_envelope"]["model_policy"]["max_tokens"] == 1100
    assert admission == inherited
    assert inherited["model_policy"]["max_tokens"] == 1100


@pytest.mark.parametrize("location", ["context", "metadata", "nested_metadata"])
def test_canonical_admission_ceiling_and_explicit_denial_survive_public_projection(location: str) -> None:
    """Canonical envelope facts must neither vanish nor become profile write grants."""
    profile = resolve_task_execution_profile(subject="Implement service", target_files=["src/service.py"])
    strategy = resolve_task_execution_strategy(profile)
    admission = {
        "model_policy": {"max_tokens": 1100},
        "budget_policy": {"output_budget_tokens": 1100},
        "authorization": {"allowed_write_paths": [], "allowed_read_paths": [], "allowed_commands": []},
    }
    context: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    if location == "context":
        context["execution_envelope"] = admission
    elif location == "metadata":
        metadata["execution_envelope"] = admission
    else:
        context["metadata"] = {"execution_envelope": admission}
    before = deepcopy(admission)
    apply_task_execution_strategy_overrides(context=context, metadata=metadata, profile=profile, strategy=strategy)
    assert context["llm_max_tokens"] == 1100
    assert context["task_execution_contract"]["context_budget"]["output_budget_tokens"] == 1100
    for facts in (context, metadata):
        for key in ("task_execution_envelope", "director_execution_envelope"):
            projected = facts[key]
            assert projected["model_policy"]["max_tokens"] == 1100
            assert projected["budget_policy"]["output_budget_tokens"] == 1100
            assert projected["authorization"]["allowed_write_paths"] == []
            assert projected["authorization"]["allowed_read_paths"] == []
            assert projected["authorization"]["allowed_commands"] == []
        if "execution_envelope" in facts:
            assert facts["execution_envelope"] == facts["task_execution_envelope"]
    if location == "nested_metadata":
        assert context["metadata"]["execution_envelope"] == context["task_execution_envelope"]
    assert admission == before


def test_context_denial_cannot_be_shadowed_by_metadata_admission_or_scope_inventory() -> None:
    profile = resolve_task_execution_profile(subject="Implement service", target_files=["src/service.py"])
    strategy = resolve_task_execution_strategy(profile)
    context: dict[str, Any] = {
        "execution_envelope": {"authorization": {"allowed_write_paths": []}},
    }
    metadata: dict[str, Any] = {
        "execution_envelope": {"authorization": {"allowed_write_paths": ["src/service.py"]}},
    }
    apply_task_execution_strategy_overrides(context=context, metadata=metadata, profile=profile, strategy=strategy)
    assert context["task_execution_envelope"]["authorization"]["allowed_write_paths"] == []
    assert context["execution_envelope"]["authorization"]["allowed_write_paths"] == []
    assert metadata["execution_envelope"]["authorization"]["allowed_write_paths"] == []


@pytest.mark.parametrize(
    "authorization", [{}, {"allowed_write_paths": "src/service.py"}, {"allowed_write_paths": ["../escape.py"]}]
)
def test_invalid_admitted_authorization_fails_before_caller_projection(authorization: dict[str, Any]) -> None:
    profile = resolve_task_execution_profile(subject="Implement service", target_files=["src/service.py"])
    strategy = resolve_task_execution_strategy(profile)
    context: dict[str, Any] = {"execution_envelope": {"authorization": authorization}}
    metadata: dict[str, Any] = {}
    before = deepcopy(context)
    with pytest.raises(ValueError):
        apply_task_execution_strategy_overrides(context=context, metadata=metadata, profile=profile, strategy=strategy)
    assert context == before
    assert metadata == {}


def test_repeated_call_narrowing_retains_one_flat_immutable_task_origin() -> None:
    """Hydrating the previous call must not make it the next call's task origin."""
    profile = resolve_task_execution_profile(subject="Implement service", target_files=["src/service.py"])
    task_strategy = resolve_task_execution_strategy(profile)
    original = deepcopy(task_strategy.to_dict())
    incoming = task_strategy
    for limit in (7000, 6000, 5000, 4000):
        context: dict[str, Any] = {"llm_max_tokens": limit}
        metadata: dict[str, Any] = {}
        before = deepcopy(incoming.to_dict())
        apply_task_execution_strategy_overrides(context=context, metadata=metadata, profile=profile, strategy=incoming)
        current = context["task_execution_strategy"]
        assert current["output_budget_tokens"] == limit
        observation = current["signal_evidence"]["call_output_budget_derivation"]
        assert observation["task_strategy"] == original
        assert "call_output_budget_derivation" not in observation["task_strategy"]["signal_evidence"]
        assert incoming.to_dict() == before
        incoming = TaskExecutionStrategyV1(**current)
    assert task_strategy.to_dict() == original


def test_empty_canonical_admission_is_not_replaced_by_profile_write_authority() -> None:
    profile = resolve_task_execution_profile(subject="Implement service", target_files=["src/service.py"])
    strategy = resolve_task_execution_strategy(profile)
    context: dict[str, Any] = {"execution_envelope": {}}
    metadata: dict[str, Any] = {}
    with pytest.raises(ValueError, match="director_execution_envelope_admission_invalid"):
        apply_task_execution_strategy_overrides(context=context, metadata=metadata, profile=profile, strategy=strategy)
    assert context == {"execution_envelope": {}}
    assert metadata == {}


@pytest.mark.parametrize(
    "admission",
    [
        {"model_policy": {"max_tokens": 1100}},
        {
            "schema_version": "polaris.execution_envelope.v1",
            "model_policy": {"max_tokens": 1100},
            "budget_policy": {"output_budget_tokens": 1100},
        },
    ],
)
def test_canonical_budget_hints_without_authorization_fail_before_projection(admission: dict[str, Any]) -> None:
    profile = resolve_task_execution_profile(subject="Implement service", target_files=["src/service.py"])
    strategy = resolve_task_execution_strategy(profile)
    context: dict[str, Any] = {"execution_envelope": admission}
    before = deepcopy(context)
    metadata: dict[str, Any] = {}
    with pytest.raises(ValueError, match="director_execution_envelope_admission_invalid"):
        apply_task_execution_strategy_overrides(context=context, metadata=metadata, profile=profile, strategy=strategy)
    assert context == before
    assert metadata == {}
