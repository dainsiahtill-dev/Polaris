"""Behavior and fork-safety tests for the DEO-2B process-local fence."""

from __future__ import annotations

import os
import select
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from typing import Any, cast

from polaris.cells.roles.kernel.public.directed_effect_contracts import (
    DirectedEffectExecutionContextV1,
)
from polaris.cells.roles.kernel.public.directed_effect_service import (
    create_directed_effect_fence_ports,
)
from polaris.cells.roles.kernel.tests.test_directed_effect_contracts import (
    _attempt,
    _claim_grant,
    _current_policy_evidence,
    _prepared_batch,
)


def _context(
    *,
    context_id: str = "context-1",
    batch_id: str = "batch-1",
    run_id: str = "run-1",
) -> DirectedEffectExecutionContextV1:
    batch = _prepared_batch(execution_attempt=_attempt(run_id=run_id))
    member = batch.prepared_members[0].member
    evidence = batch.authorization_evidence_by_call_id[0][1]
    claim_grant = _claim_grant(batch.execution_attempt, batch.parent_binding, member)
    return DirectedEffectExecutionContextV1(
        context_id=context_id,
        batch_id=batch_id,
        creator_pid=os.getpid(),
        tool_call_id=member.tool_call_id,
        normalized_tool_name=member.normalized_tool_name,
        arguments_hash=evidence.arguments_hash,
        authorization_evidence=evidence,
        claim_grant=claim_grant,
        bound_snapshot=batch.prepared_members[0].policy_binding.bound_snapshot,
        current_policy_evidence=_current_policy_evidence(batch, member, claim_grant),
        current_job_token_restriction_evidence=(),
    )


def _forged_context(
    context: DirectedEffectExecutionContextV1,
    **changes: object,
) -> DirectedEffectExecutionContextV1:
    forged = object.__new__(DirectedEffectExecutionContextV1)
    for field in fields(context):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(context, field.name)))
    return forged


def _forged_dataclass(instance: object, **changes: object) -> object:
    forged = object.__new__(type(instance))
    for field in fields(instance):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(instance, field.name)))
    return forged


def test_fence_capacity_is_exactly_64() -> None:
    ports = create_directed_effect_fence_ports()
    contexts = tuple(_context(context_id=f"context-{index}") for index in range(65))

    for context in contexts[:64]:
        assert ports.admin.register(context).status == "registered"

    denied = ports.admin.register(contexts[64])
    assert denied.status == "denied"
    assert denied.error_code == "deo_fence_capacity_exceeded"


def test_admin_and_consume_views_expose_disjoint_capabilities() -> None:
    ports = create_directed_effect_fence_ports()

    assert not hasattr(ports.consume, "register")
    assert not hasattr(ports.consume, "release_batch")
    assert not hasattr(ports.admin, "consume")


def test_reconstructed_equal_context_and_identity_drift_are_denied() -> None:
    ports = create_directed_effect_fence_ports()
    context = _context()
    assert ports.admin.register(context).ok

    reconstructed = replace(context)
    equal_denial = ports.consume.consume(reconstructed)
    assert equal_denial.error_code == "deo_context_reconstructed"

    drifted = _forged_context(context, normalized_tool_name="execute_command")
    drift_denial = ports.consume.consume(drifted)
    assert drift_denial.error_code == "deo_context_identity_mismatch"


def test_forged_context_and_stale_nested_authorization_hash_are_denied() -> None:
    ports = create_directed_effect_fence_ports()
    context = _context()
    forged_authorization = object.__new__(type(context.authorization_evidence))
    for field in fields(context.authorization_evidence):
        value = getattr(context.authorization_evidence, field.name)
        if field.name == "capability_scope":
            value = ("forged/",)
        object.__setattr__(forged_authorization, field.name, value)
    forged_context = _forged_context(context, authorization_evidence=forged_authorization)

    registration = ports.admin.register(forged_context)
    assert registration.error_code == "deo_context_identity_mismatch"

    assert ports.admin.register(context).ok
    object.__setattr__(context.authorization_evidence, "capability_scope", ("mutated/",))
    consume = ports.consume.consume(context)
    assert consume.error_code == "deo_context_identity_mismatch"


def test_same_object_with_rehashed_nested_grant_drift_is_denied() -> None:
    ports = create_directed_effect_fence_ports()
    context = _context()
    assert ports.admin.register(context).ok
    grant = context.claim_grant
    changed_binding = replace(grant.parent_binding, source_event_id="parent-event-2")
    changed_grant = _claim_grant(grant.execution_attempt, changed_binding, grant.member)
    object.__setattr__(context, "claim_grant", changed_grant)

    result = ports.consume.consume(context)
    assert result.error_code == "deo_context_identity_mismatch"


def test_same_object_can_be_consumed_exactly_once_and_cleanup_is_idempotent() -> None:
    ports = create_directed_effect_fence_ports()
    context = _context()
    attempt = context.claim_grant.execution_attempt
    assert ports.admin.register(context).ok
    assert ports.consume.consume(context).status == "consumed"

    replay = ports.consume.consume(context)
    assert replay.error_code == "deo_context_replayed"

    released = ports.admin.release_batch(context.batch_id, attempt)
    assert released.status == "released"
    assert released.released_count == 1
    absent = ports.admin.release_batch(context.batch_id, attempt)
    assert absent.status == "absent"
    assert absent.released_count == 0


def test_release_is_scoped_to_batch_and_exact_attempt_identity() -> None:
    ports = create_directed_effect_fence_ports()
    first = _context(context_id="context-1", run_id="run-1")
    second = _context(context_id="context-2", run_id="run-2")
    assert ports.admin.register(first).ok
    assert ports.admin.register(second).ok

    first_release = ports.admin.release_batch(
        "batch-1",
        first.claim_grant.execution_attempt,
    )
    assert first_release.status == "released"
    assert first_release.released_count == 1
    assert ports.consume.consume(second).status == "consumed"

    wrong_attempt = ports.admin.release_batch(
        "batch-1",
        first.claim_grant.execution_attempt,
    )
    assert wrong_attempt.status == "denied"
    assert wrong_attempt.error_code == "deo_context_release_failed"

    second_release = ports.admin.release_batch(
        "batch-1",
        second.claim_grant.execution_attempt,
    )
    assert second_release.status == "released"
    assert second_release.released_count == 1


def test_release_rejects_noncanonical_attempt_without_deleting_entry() -> None:
    ports = create_directed_effect_fence_ports()
    context = _context()
    attempt = context.claim_grant.execution_attempt
    assert ports.admin.register(context).ok
    forged_attempt = _forged_dataclass(attempt, run_id="")

    denied = ports.admin.release_batch(context.batch_id, forged_attempt)  # type: ignore[arg-type]
    assert denied.status == "denied"
    assert denied.error_code == "deo_context_release_failed"
    assert ports.consume.consume(context).status == "consumed"


def test_concurrent_consume_allows_exactly_one() -> None:
    ports = create_directed_effect_fence_ports()
    context = _context()
    assert ports.admin.register(context).ok

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = tuple(pool.map(lambda _: ports.consume.consume(context), range(32)))

    assert sum(result.ok for result in results) == 1
    assert {result.error_code for result in results if not result.ok} == {"deo_context_replayed"}


def test_fork_child_cannot_consume_parent_context() -> None:
    ports = create_directed_effect_fence_ports()
    context = _context()
    assert ports.admin.register(context).ok
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        try:
            os.close(read_fd)
            result = ports.consume.consume(context)
            os.write(write_fd, str(result.error_code or "").encode("utf-8"))
        finally:
            os.close(write_fd)
            os._exit(0)

    os.close(write_fd)
    payload = os.read(read_fd, 128).decode("utf-8")
    os.close(read_fd)
    _, status = os.waitpid(child_pid, 0)

    assert os.waitstatus_to_exitcode(status) == 0
    assert payload == "deo_fence_pid_mismatch"
    assert ports.consume.consume(context).status == "consumed"


def test_fork_child_pid_denial_does_not_wait_on_inherited_locked_fence() -> None:
    ports = create_directed_effect_fence_ports()
    context = _context()
    assert ports.admin.register(context).ok
    state = cast(Any, ports.admin)._state
    lock_held = threading.Event()
    release_lock = threading.Event()

    def _hold_fence_lock() -> None:
        with state._lock:
            lock_held.set()
            assert release_lock.wait(timeout=5)

    holder = threading.Thread(target=_hold_fence_lock, daemon=True)
    holder.start()
    assert lock_held.wait(timeout=2)
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        try:
            os.close(read_fd)
            result = ports.consume.consume(context)
            os.write(write_fd, str(result.error_code or "").encode("utf-8"))
        finally:
            os.close(write_fd)
            os._exit(0)

    os.close(write_fd)
    try:
        readable, _, _ = select.select((read_fd,), (), (), 2.0)
        if not readable:
            os.kill(child_pid, signal.SIGKILL)
            raise AssertionError("fork child deadlocked before PID denial")
        payload = os.read(read_fd, 128).decode("utf-8")
        _, status = os.waitpid(child_pid, 0)
    finally:
        os.close(read_fd)
        release_lock.set()
        holder.join(timeout=2)

    assert not holder.is_alive()
    assert os.waitstatus_to_exitcode(status) == 0
    assert payload == "deo_fence_pid_mismatch"
