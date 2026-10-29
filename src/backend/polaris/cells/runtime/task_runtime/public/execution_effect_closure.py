"""Read-only terminal effect observation; query keys never grant execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from polaris.cells.runtime.task_runtime.public.contracts import TaskRuntimeExecutionAttemptIdentityV1


@dataclass(frozen=True, slots=True)
class QueryExecutionEffectClosureV1:
    workspace: str
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1

    def __post_init__(self) -> None:
        if not isinstance(self.workspace, str) or not self.workspace.strip():
            raise ValueError("workspace must be non-empty")
        if type(self.execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")


@dataclass(frozen=True, slots=True)
class QueryExecutionEffectClosuresV1:
    """Identity-only batch query; owner snapshots cannot come from callers."""

    workspace: str
    execution_attempts: tuple[TaskRuntimeExecutionAttemptIdentityV1, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.workspace, str) or not self.workspace.strip():
            raise ValueError("workspace must be non-empty")
        if type(self.execution_attempts) is not tuple or any(
            type(attempt) is not TaskRuntimeExecutionAttemptIdentityV1 for attempt in self.execution_attempts
        ):
            raise TypeError("execution_attempts must be a tuple of TaskRuntimeExecutionAttemptIdentityV1")


@dataclass(frozen=True, slots=True)
class ExecutionEffectCommitObservationV1:
    operation_id: str
    batch_id: str
    tool_call_id: str
    normalized_tool_name: str
    receipt_ref: str
    receipt_hash: str
    receipt_binding_hash: str
    receipt_outcome: str
    commit_event_id: str
    commit_version: int


@dataclass(frozen=True, slots=True)
class ExecutionEffectTerminalFailureObservationV1:
    """Owner-authenticated negative effect closure, never a success receipt."""

    operation_id: str
    parent_binding_id: str
    batch_id: str
    turn_id: str
    tool_call_id: str
    normalized_tool_name: str
    expected_receipt_binding_hash: str
    context_id: str
    resolution_evidence_ref: str
    resolution_evidence_hash: str
    dead_letter_event_id: str
    dead_letter_version: int
    exit_code: int | None
    physical_failure_hash: str
    owner_result_hash: str
    owner_raw_result_hash: str
    owner_turn_event_id: str
    owner_turn_event_seq: int
    parent_close_event_id: str
    parent_close_version: int
    owner_terminal_event_id: str
    owner_terminal_event_seq: int
    failure_kind: str = "physical_result_failed"


@dataclass(frozen=True, slots=True)
class ExecutionEffectClosureObservationV1:
    status: Literal["closed", "active", "unknown"]
    reason: str
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    receipts: tuple[ExecutionEffectCommitObservationV1, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    observation_hash: str = ""
    execution_authority: Literal[False] = False
    terminal_failures: tuple[ExecutionEffectTerminalFailureObservationV1, ...] = ()

    def __post_init__(self) -> None:
        if self.execution_authority is not False:
            raise ValueError("observations cannot grant execution authority")


def query_execution_effect_closure(query: QueryExecutionEffectClosureV1) -> ExecutionEffectClosureObservationV1:
    """Observe one current or historical attempt without granting execution.

    Closed means this exact attempt has no unresolved effects. A genuinely
    settled suspended attempt can be closed while its task remains resumable;
    this grants neither task completion nor successor execution authority.
    The owner authenticates terminal history from its canonical FactStream;
    callers provide query identities only. A successor cannot erase an older
    attempt's effects. Unknown, active, or damaged owner evidence blocks
    resource release; observation never renews a lease or enrolls a stream.
    """
    from polaris.cells.runtime.task_runtime.internal.execution_effect_closure import observe_execution_effect_closure

    return cast("ExecutionEffectClosureObservationV1", (observe_execution_effect_closure(query)))


def query_execution_effect_closures(
    query: QueryExecutionEffectClosuresV1,
) -> tuple[ExecutionEffectClosureObservationV1, ...]:
    """Observe each requested owner against one strict before/after history.

    Results retain input order. Session, registry, operation and negative-effect
    checks remain independent. Unknown or changed evidence grants no closure.
    This query has no execution authority, snapshot input or cross-query cache.
    """
    from polaris.cells.runtime.task_runtime.internal.execution_effect_closure import observe_execution_effect_closures

    return cast("tuple[ExecutionEffectClosureObservationV1, ...]", (observe_execution_effect_closures(query)))
