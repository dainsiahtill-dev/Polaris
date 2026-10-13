"""Read-only terminal effect observation; query keys never grant execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
class ExecutionEffectClosureObservationV1:
    status: Literal["closed", "active", "unknown"]
    reason: str
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    receipts: tuple[ExecutionEffectCommitObservationV1, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    observation_hash: str = ""
    execution_authority: Literal[False] = False

    def __post_init__(self) -> None:
        if self.execution_authority is not False:
            raise ValueError("observations cannot grant execution authority")


def query_execution_effect_closure(query: QueryExecutionEffectClosureV1) -> ExecutionEffectClosureObservationV1:
    """Observe one current or historical attempt without granting execution.

    The owner authenticates terminal history from its canonical FactStream;
    callers provide query identities only. A successor cannot erase an older
    attempt's effects. Unknown, active, or damaged owner evidence blocks
    resource release; observation never renews a lease or enrolls a stream.
    """
    from polaris.cells.runtime.task_runtime.internal.execution_effect_closure import observe_execution_effect_closure

    return observe_execution_effect_closure(query)
