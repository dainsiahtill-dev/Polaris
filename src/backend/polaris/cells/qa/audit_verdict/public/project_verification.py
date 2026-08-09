"""QA-owned project-verification port; execution owner binds in bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ResolveProjectVerificationAuthorityQueryV1:
    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str


@dataclass(frozen=True, slots=True)
class QueryProjectVerificationReceiptV1:
    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    modality: str
    argv: tuple[str, ...]
    cwd: str
    command_authority_hash: str
    input_artifacts: tuple[Any, ...]
    timeout_seconds: float
    job_token_id: str
    job_token_set_hash: str
    execution_policy_hash: str
    authority_revision: str
    policy_profile_id: str
    policy_decision_hash: str
    executable_path: str
    executable_realpath: str
    executable_hash: str


@runtime_checkable
class ProjectVerificationReceiptV1(Protocol):
    project_id: str
    run_id: str
    completion_contract_hash: str
    obligation_id: str
    owner_task_id: str
    modality: str
    argv: tuple[str, ...]
    cwd: str
    command_authority_hash: str
    executable_path: str
    executable_realpath: str
    executable_hash: str
    input_artifact_hash: str
    exit_code: int | None
    timed_out: bool
    output_hash: str
    proof_satisfied: bool
    proof_evidence_hash: str
    process_pid: int | None
    process_start_token: str
    readiness_probe_kind: str
    readiness_satisfied: bool
    controlled_termination: bool
    receipt_hash: str
    receipt_ref: str
    succeeded: bool


@runtime_checkable
class ProjectVerificationClientPortV1(Protocol):
    def authorize_project_verification_command(
        self,
        query: ResolveProjectVerificationAuthorityQueryV1,
    ) -> Any: ...

    def query_project_verification_receipt(
        self,
        query: QueryProjectVerificationReceiptV1,
    ) -> ProjectVerificationReceiptV1 | None: ...

    def run_project_verification(self, command: Any) -> Any: ...


_client: ProjectVerificationClientPortV1 | None = None
_client_lock = Lock()


def _bind_project_verification_client(port: ProjectVerificationClientPortV1) -> None:
    if not isinstance(port, ProjectVerificationClientPortV1):
        raise TypeError("port must implement ProjectVerificationClientPortV1")
    global _client
    with _client_lock:
        if _client is None:
            _client = port
        elif _client is not port:
            raise RuntimeError("qa_project_verification_client_conflicting_rebind")


def _client_port() -> ProjectVerificationClientPortV1:
    with _client_lock:
        port = _client
    if port is None:
        raise RuntimeError("qa_project_verification_client_unbound")
    return port


def authorize_project_verification_command(query: ResolveProjectVerificationAuthorityQueryV1) -> Any:
    return _client_port().authorize_project_verification_command(query)


def query_project_verification_receipt(
    query: QueryProjectVerificationReceiptV1,
) -> ProjectVerificationReceiptV1 | None:
    return _client_port().query_project_verification_receipt(query)


def run_project_verification(command: Any) -> Any:
    return _client_port().run_project_verification(command)


__all__ = [
    "ProjectVerificationClientPortV1",
    "ProjectVerificationReceiptV1",
    "QueryProjectVerificationReceiptV1",
    "ResolveProjectVerificationAuthorityQueryV1",
    "authorize_project_verification_command",
    "query_project_verification_receipt",
    "run_project_verification",
]
