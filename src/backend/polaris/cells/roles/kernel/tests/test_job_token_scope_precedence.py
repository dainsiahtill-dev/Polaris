"""Committed write authority is a ceiling, not a union of context hints."""

from __future__ import annotations

import json
from typing import Any

import pytest
from polaris.cells.director.tasking.public import TaskExecutionProfileV1, TaskExecutionStrategyV1
from polaris.cells.director.tasking.public.execution_guidance import apply_task_execution_strategy_overrides
from polaris.cells.roles.kernel.internal.kernel.tool_executor import (
    derive_role_turn_capability_scope,
    derive_role_turn_capability_token,
)
from polaris.cells.roles.profile.public.service import RoleTurnRequest


def _request(token, context=None):
    return RoleTurnRequest(
        message="Implement authorized files",
        metadata={"task_metadata": {"control_plane_job_token": token}},
        context_override=context or {},
    )


@pytest.mark.parametrize(
    "hint",
    [
        {"allowed_write_paths": ["owned.py", "foreign.py"]},
        {"target_files": ["owned.py", "foreign.py"]},
        {"allowed_paths": ["owned.py", "foreign.py"]},
        {"execution_envelope": {"authorization": {"allowed_write_paths": ["foreign.py"]}}},
    ],
)
def test_context_cannot_expand_committed_job_token(hint):
    request = _request(
        {"token_id": "committed", "allowed_write_paths": ["owned.py"], "allowed_read_paths": ["input.txt"]}, hint
    )
    scope = derive_role_turn_capability_scope(request)
    assert scope == ("owned.py",)
    evidence = derive_role_turn_capability_token(request, scope)
    assert evidence["allowed_scope"] == ["owned.py"]
    assert evidence["allowed_read_paths"] == ["input.txt"]


@pytest.mark.parametrize("writes", [[], None])
def test_explicit_empty_or_missing_token_writes_are_not_granted_by_required_targets(writes):
    token = {"token_id": "readonly", "target_files": ["owned.py"], "allowed_read_paths": ["owned.py"]}
    if writes is not None:
        token["allowed_write_paths"] = writes
    request = _request(token, {"target_files": ["owned.py"], "allowed_write_paths": ["owned.py"]})
    scope = derive_role_turn_capability_scope(request)
    assert scope == ("__polaris_no_authorized_paths__",)
    assert derive_role_turn_capability_token(request, scope)["allowed_read_paths"] == ["owned.py"]


@pytest.mark.parametrize(
    "context,expected",
    [
        ({"allowed_write_paths": ["one.py"]}, ("one.py",)),
        ({"allowed_write_paths": []}, ("__polaris_no_authorized_paths__",)),
        ({"allowed_write_paths": ["unowned.py"]}, ("__polaris_no_authorized_paths__",)),
        ({"target_files": ["one.py"]}, ("one.py", "two.py")),
    ],
)
def test_explicit_request_write_constraint_can_only_narrow(context, expected):
    request = _request({"token_id": "committed", "allowed_write_paths": ["one.py", "two.py"]}, context)
    assert derive_role_turn_capability_scope(request) == expected


@pytest.mark.parametrize(
    "token_paths,context_paths,expected",
    [
        (["src"], ["src/one.py"], ("src/one.py",)),
        (["src/one.py"], ["src"], ("src/one.py",)),
        (["src"], ["src2/evil.py"], ("__polaris_no_authorized_paths__",)),
    ],
)
def test_narrowing_uses_normalized_path_component_boundaries(token_paths, context_paths, expected):
    request = _request(
        {"token_id": "committed", "allowed_write_paths": token_paths}, {"allowed_write_paths": context_paths}
    )
    assert derive_role_turn_capability_scope(request) == expected


@pytest.mark.parametrize("writes", [[], ["owned.py"]])
def test_public_envelope_write_scope_survives_serialized_consumer_roundtrip(writes: list[str]) -> None:
    context: dict[str, Any] = {"workspace": "/workspace", "task_id": "TASK-1", "run_id": "run-1"}
    profile = TaskExecutionProfileV1(task_type="implement", target_files=("owned.py",))
    strategy = TaskExecutionStrategyV1(target_files=profile.target_files)
    apply_task_execution_strategy_overrides(
        context=context,
        metadata={
            "allowed_write_paths": writes,
            "allowed_read_paths": ["**"],
            "job_token": {"token_id": "envelope-source", "allowed_write_paths": writes},
        },
        profile=profile,
        strategy=strategy,
    )
    envelope = json.loads(json.dumps(context["task_execution_envelope"]))
    assert envelope["authorization"]["target_files"] == ["owned.py"]
    assert envelope["authorization"]["allowed_write_paths"] == writes
    # Only the serialized producer output reaches this compatibility consumer.
    # Keeping producer input constraints here would mask an expanded envelope.
    request = RoleTurnRequest(message="Implement owned.py", context_override={"task_execution_envelope": envelope})
    scope = derive_role_turn_capability_scope(request)
    assert scope == tuple(writes or ["__polaris_no_authorized_paths__"])
    evidence = derive_role_turn_capability_token(request, scope)
    assert evidence["allowed_scope"] == list(scope)
    assert evidence["allowed_read_paths"] == ["**"]
    assert evidence["execution_envelope_hash"] == envelope["envelope_hash"]


def test_legacy_envelope_missing_write_key_keeps_existing_target_compatibility() -> None:
    envelope = {
        "envelope_id": "legacy-envelope",
        "authorization": {"target_files": ["owned.py"], "allowed_read_paths": ["**"]},
    }
    request = RoleTurnRequest(message="Implement", context_override={"execution_envelope": envelope})
    assert derive_role_turn_capability_scope(request) == ("owned.py",)


@pytest.mark.parametrize("carrier", ["token", "envelope", "constraint"])
@pytest.mark.parametrize(
    "writes",
    [None, False, {}, {"allowed_read_paths": ["**"]}, [{"allowed_read_paths": ["**"]}], ["owned.py", {"path": "**"}]],
)
def test_invalid_write_scope_does_not_grant_targets_or_nested_read_paths(carrier: str, writes: Any) -> None:
    if carrier == "token":
        request = _request({"token_id": "invalid-scope", "allowed_write_paths": writes, "target_files": ["owned.py"]})
    elif carrier == "constraint":
        request = _request(
            {"token_id": "valid-grant", "allowed_write_paths": ["owned.py"]},
            {"allowed_write_paths": writes},
        )
    else:
        request = RoleTurnRequest(
            message="Implement",
            context_override={
                "execution_envelope": {
                    "envelope_id": "invalid-envelope",
                    "authorization": {"allowed_write_paths": writes, "target_files": ["owned.py"]},
                }
            },
        )
    scope = derive_role_turn_capability_scope(request)
    assert scope == ("__polaris_no_authorized_paths__",)
    assert derive_role_turn_capability_token(request, scope)["allowed_scope"] == list(scope)


@pytest.mark.parametrize("writes", ["owned.py", ["owned.py"], ("owned.py",), {"owned.py"}])
def test_flat_string_write_scopes_keep_existing_forms(writes: Any) -> None:
    assert derive_role_turn_capability_scope(_request({"token_id": "valid", "allowed_write_paths": writes})) == (
        "owned.py",
    )
