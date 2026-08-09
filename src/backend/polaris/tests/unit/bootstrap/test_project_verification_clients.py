from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import polaris.bootstrap.project_verification_clients as clients_module
from polaris.cells.director.task_consumer.public.project_verification import (
    ProjectVerificationClientPortV1 as DirectorProjectVerificationClientPortV1,
    QueryProjectVerificationReceiptV1,
    ResolveProjectVerificationAuthorityQueryV1,
)
from polaris.cells.qa.audit_verdict.public.project_verification import (
    ProjectVerificationClientPortV1 as QaProjectVerificationClientPortV1,
)
from polaris.cells.runtime.execution_broker.public import (
    ProjectVerificationArtifactInputV1,
    QueryProjectVerificationReceiptV1 as OwnerQueryProjectVerificationReceiptV1,
    ResolveProjectVerificationAuthorityQueryV1 as OwnerResolveProjectVerificationAuthorityQueryV1,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_execution_broker_adapter_implements_both_consumer_owned_ports() -> None:
    assert isinstance(clients_module._CLIENT, DirectorProjectVerificationClientPortV1)
    assert isinstance(clients_module._CLIENT, QaProjectVerificationClientPortV1)


def test_adapter_maps_consumer_queries_to_execution_owner_contracts(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured_authority: list[OwnerResolveProjectVerificationAuthorityQueryV1] = []
    captured_receipt: list[OwnerQueryProjectVerificationReceiptV1] = []

    def authorize(query: OwnerResolveProjectVerificationAuthorityQueryV1) -> object:
        captured_authority.append(query)
        return object()

    def query_receipt(query: OwnerQueryProjectVerificationReceiptV1) -> None:
        captured_receipt.append(query)
        return None

    monkeypatch.setattr(clients_module, "authorize_project_verification_command", authorize)
    monkeypatch.setattr(clients_module, "query_project_verification_receipt", query_receipt)

    contract_hash = _hash("contract")
    authority_query = ResolveProjectVerificationAuthorityQueryV1(
        workspace=str(workspace),
        project_id="project-1",
        run_id="run-1",
        completion_contract_hash=contract_hash,
        obligation_id="build-1",
    )
    clients_module._CLIENT.authorize_project_verification_command(authority_query)

    executable = str(Path(sys.executable).resolve())
    receipt_query = QueryProjectVerificationReceiptV1(
        workspace=str(workspace),
        project_id="project-1",
        run_id="run-1",
        completion_contract_hash=contract_hash,
        obligation_id="build-1",
        owner_task_id="task-1",
        modality="build",
        argv=(executable, "-V"),
        cwd=".",
        command_authority_hash=_hash("command"),
        input_artifacts=(ProjectVerificationArtifactInputV1(obligation_id="source-1", path="src/main.py"),),
        timeout_seconds=30.0,
        job_token_id="job-token-1",
        job_token_set_hash=_hash("job-token-set"),
        execution_policy_hash=_hash("execution-policy"),
        authority_revision=_hash("authority-revision"),
        policy_profile_id="profile-1",
        policy_decision_hash=_hash("policy-decision"),
        executable_path=executable,
        executable_realpath=executable,
        executable_hash=_hash("executable"),
    )
    clients_module._CLIENT.query_project_verification_receipt(receipt_query)

    assert captured_authority == [
        OwnerResolveProjectVerificationAuthorityQueryV1(
            workspace=str(workspace),
            project_id="project-1",
            run_id="run-1",
            completion_contract_hash=contract_hash,
            obligation_id="build-1",
        )
    ]
    assert len(captured_receipt) == 1
    assert captured_receipt[0].project_id == "project-1"
    assert captured_receipt[0].command_authority_hash == _hash("command")
    assert captured_receipt[0].executable_realpath == executable
