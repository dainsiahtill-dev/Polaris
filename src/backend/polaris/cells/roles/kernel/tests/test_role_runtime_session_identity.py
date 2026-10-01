"""Kernel-owner checks of public runtime request identity, never authority.

The private identity consumer belongs to roles.kernel; placing these tests
under roles.runtime crossed that Cell's public/internal import fence.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_id import (
    TransactionIdentityError,
    _derive_invocation_identity,
)
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1, RoleRuntimeError
from polaris.cells.roles.runtime.public.service import RoleRuntimeService


def command(workspace: Path, metadata: dict | None = None, **kwargs) -> ExecuteRoleSessionCommandV1:
    return ExecuteRoleSessionCommandV1(
        role="director",
        session_id="chat-session-1",
        workspace=str(workspace),
        user_message="List project files",
        run_id="observer-run-1",
        metadata=metadata or {},
        **kwargs,
    )


def identity(command: ExecuteRoleSessionCommandV1):
    request = RoleRuntimeService._build_session_request(command)
    return _derive_invocation_identity(
        request, metadata=request.metadata, role=command.role, workspace=command.workspace
    )


def test_public_session_can_enter_real_transaction_identity_gate(tmp_path: Path) -> None:
    item = command(tmp_path)
    derived = identity(item)
    assert derived.execution_scope_kind == "turn_request_id"
    assert derived.execution_scope_id == item.turn_request_id
    assert derived.execution_scope_id not in {"chat-session-1", "observer-run-1"}


def test_same_command_and_serialized_restore_preserve_invocation(tmp_path: Path) -> None:
    item = command(tmp_path)
    restored = ExecuteRoleSessionCommandV1(**json.loads(json.dumps(dataclasses.asdict(item))))
    assert identity(item) == identity(item)
    assert identity(restored) == identity(item)


def test_independent_same_session_requests_have_distinct_invocations(tmp_path: Path) -> None:
    assert identity(command(tmp_path)).invocation_id != identity(command(tmp_path)).invocation_id


@pytest.mark.parametrize("stream", [False, True])
def test_explicit_request_identity_is_retained_in_both_modes(tmp_path: Path, stream: bool) -> None:
    item = command(tmp_path, turn_request_id="request-unique-1", stream=stream)
    assert identity(item).execution_scope_id == "request-unique-1"


@pytest.mark.parametrize(
    "metadata,kind,expected",
    [
        ({"execution_attempt_id": "attempt-1"}, "execution_attempt_id", "attempt-1"),
        ({"execution_id": "execution-1"}, "execution_id", "execution-1"),
        ({"turn_request_id": "already-request-1"}, "turn_request_id", "already-request-1"),
        ({"task_runtime_session_id": "chat-session-1"}, "task_runtime_session_id", "chat-session-1"),
        ({"runtime_execution": {"execution_id": "execution-1"}}, "execution_id", "execution-1"),
        ({"runtime_execution": {"session_id": "chat-session-1"}}, "task_runtime_session_id", "chat-session-1"),
    ],
)
def test_existing_scope_is_not_overwritten(tmp_path: Path, metadata: dict, kind: str, expected: str) -> None:
    derived = identity(command(tmp_path, metadata=metadata))
    assert (derived.execution_scope_kind, derived.execution_scope_id) == (kind, expected)


@pytest.mark.parametrize(
    "metadata",
    [
        {"turn_request_id": ""},
        {"execution_id": ""},
        {"runtime_execution": {"session_id": ""}},
        {"turn_request_id": "request-1", "execution_id": "conflicting-execution"},
    ],
)
def test_invalid_explicit_scope_is_not_laundered_by_new_nonce(tmp_path: Path, metadata: dict) -> None:
    with pytest.raises(TransactionIdentityError) as caught:
        identity(command(tmp_path, metadata=metadata))
    assert caught.value.code in {"transaction_identity_unbound", "transaction_identity_mismatch"}


@pytest.mark.asyncio
async def test_request_identity_does_not_authorize_guarded_execution(tmp_path: Path) -> None:
    item = command(tmp_path, metadata={"task_runtime_guard": True}, turn_request_id="request-1")
    with pytest.raises(RoleRuntimeError) as caught:
        await RoleRuntimeService().execute_role_session(item)
    assert caught.value.code == "deo_execution_attempt_missing"
