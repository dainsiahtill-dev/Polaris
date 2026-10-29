"""Read-only provenance for an existing attempt; evidence never grants execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, cast

from polaris.cells.runtime.task_runtime.public.contracts import (
    TaskRuntimeExecutionAttemptIdentityV1,
    ValidateTaskRuntimeExecutionAttemptQueryV1,
)

ExecutionClaimEvidencePurposeV1 = Literal["active_completion", "receipt_revalidation"]


@dataclass(frozen=True, slots=True)
class QueryExecutionClaimEvidenceV1:
    workspace: str
    identity: TaskRuntimeExecutionAttemptIdentityV1
    purpose: ExecutionClaimEvidencePurposeV1
    lock_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        validated = ValidateTaskRuntimeExecutionAttemptQueryV1(self.workspace, self.identity, self.lock_timeout_seconds)
        object.__setattr__(self, "workspace", validated.workspace)
        object.__setattr__(self, "lock_timeout_seconds", validated.lock_timeout_seconds)
        if self.purpose not in {"active_completion", "receipt_revalidation"}:
            raise ValueError("unsupported claim evidence purpose")


@dataclass(frozen=True, slots=True)
class ExecutionClaimEvidenceV1:
    """Immutable owner observation, re-queryable at a caller's decision frontier.

    The completed basis authenticates a receipt source only. It does not open
    an authority, renew a lease, or permit another execution attempt.
    JSON backing keeps nested metadata immutable while access returns copies.
    """

    identity: TaskRuntimeExecutionAttemptIdentityV1
    purpose: ExecutionClaimEvidencePurposeV1
    status: Literal["active", "completed"]
    acceptance_basis: Literal["active_unexpired_attempt", "completed_receipt_source"]
    claim_event_id: str
    claim_event_seq: int
    current_event_id: str
    current_event_seq: int
    task_content_hash: str
    session_content_hash: str
    claim_metadata_json: str
    current_metadata_json: str
    factory_run_id: str
    project_id: str

    @property
    def claim_metadata(self) -> dict[str, Any]:
        return dict(json.loads(self.claim_metadata_json))

    @property
    def current_metadata(self) -> dict[str, Any]:
        return dict(json.loads(self.current_metadata_json))


class ExecutionClaimEvidenceError(ValueError):
    """An explicit owner rejection, preserving the failing observation code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def query_execution_claim_evidence(query: QueryExecutionClaimEvidenceV1) -> ExecutionClaimEvidenceV1:
    """Authenticate original claim and durable current state without mutation."""
    from ..internal.execution_claim_evidence import query_execution_claim_evidence as owner_query

    return cast("ExecutionClaimEvidenceV1", (owner_query(query)))


__all__ = [
    "ExecutionClaimEvidenceError",
    "ExecutionClaimEvidencePurposeV1",
    "ExecutionClaimEvidenceV1",
    "QueryExecutionClaimEvidenceV1",
    "query_execution_claim_evidence",
]
