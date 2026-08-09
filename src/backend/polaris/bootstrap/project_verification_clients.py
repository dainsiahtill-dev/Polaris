"""Compose Director/QA verification ports onto runtime.execution_broker."""

from __future__ import annotations

from typing import Any

from polaris.cells.director.task_consumer.public.bootstrap import (
    bind_project_verification_client as bind_director_project_verification_client,
)
from polaris.cells.qa.audit_verdict.public.project_verification_bootstrap import (
    bind_project_verification_client as bind_qa_project_verification_client,
)
from polaris.cells.runtime.execution_broker.public import (
    QueryProjectVerificationReceiptV1,
    ResolveProjectVerificationAuthorityQueryV1,
    authorize_project_verification_command,
    query_project_verification_receipt,
    run_project_verification,
)


class _ExecutionBrokerProjectVerificationClientV1:
    @staticmethod
    def authorize_project_verification_command(query: Any) -> Any:
        return authorize_project_verification_command(
            ResolveProjectVerificationAuthorityQueryV1(
                workspace=query.workspace,
                project_id=query.project_id,
                run_id=query.run_id,
                completion_contract_hash=query.completion_contract_hash,
                obligation_id=query.obligation_id,
            )
        )

    @staticmethod
    def query_project_verification_receipt(query: Any) -> Any:
        return query_project_verification_receipt(
            QueryProjectVerificationReceiptV1(
                **{
                    name: getattr(query, name)
                    for name in QueryProjectVerificationReceiptV1.__dataclass_fields__
                }
            )
        )

    @staticmethod
    def run_project_verification(command: Any) -> Any:
        return run_project_verification(command)


_CLIENT = _ExecutionBrokerProjectVerificationClientV1()


def configure_project_verification_clients() -> None:
    """Bind one stateless execution-broker adapter to both consumer Cells."""

    bind_director_project_verification_client(_CLIENT)
    bind_qa_project_verification_client(_CLIENT)


__all__ = ["configure_project_verification_clients"]
