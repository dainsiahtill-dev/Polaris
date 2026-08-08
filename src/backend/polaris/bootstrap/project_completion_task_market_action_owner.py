"""Production ProjectCompletion action owner backed by ``runtime.task_market``.

The adapter lives in composition because it joins two public Cells.  The
TaskMarket requeue receipt is the durable effect receipt; the convergence
receipt preserves its original dispatch claim, settlement id and effect hash.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from polaris.cells.orchestration.workflow_orchestration.public.project_completion import (
    ProjectCompletionActionCommandV1,
    ProjectCompletionActionPortV1,
    ProjectCompletionActionReceiptV1,
    ProjectCompletionDispatchClaimV1,
    project_completion_action_receipt_hash,
)
from polaris.cells.runtime.task_market.public import (
    QueryTaskRequeueReceiptV1,
    RequeueTaskCommandV1,
    TaskMarketService,
    TaskRequeueReceiptV1,
    get_task_market_service,
)

_ACTION_STAGE = {
    "publish_owner_rework": "pending_exec",
    "refresh_owner_evidence": "pending_qa",
    "run_deterministic_repair": "pending_exec",
    "run_required_verifier": "pending_qa",
    "wait_for_dependencies": "pending_design",
}


class TaskMarketProjectCompletionActionOwnerV1(ProjectCompletionActionPortV1):
    """Translate completion actions into atomic, idempotent TaskMarket requeues."""

    def __init__(self, service: TaskMarketService | None = None) -> None:
        self._service = service or get_task_market_service()

    @staticmethod
    def _requeue_command(
        command: ProjectCompletionActionCommandV1,
        *,
        claim_id: str,
    ) -> RequeueTaskCommandV1:
        target_stage = _ACTION_STAGE[command.action_kind]
        return RequeueTaskCommandV1(
            workspace=command.identity.workspace,
            task_id=command.owner_task_id,
            target_stage=target_stage,
            reason=f"project completion residual: {command.action_kind}",
            metadata={
                "source": "orchestration.workflow_orchestration.project_completion",
                "schema_version": "project_completion.task_market_handoff.v1",
                "project_id": command.identity.project_id,
                "factory_run_id": command.identity.run_id,
                "completion_contract_hash": command.identity.completion_contract_hash,
                "diagnostic_id": command.diagnostic_id,
                "obligation_id": command.obligation_id,
                "action_kind": command.action_kind,
                "owner_snapshot_hash": command.owner_snapshot_hash,
                "owner_bundle_hash": command.owner_bundle_hash,
                "last_failure": {
                    "error_code": "PROJECT_COMPLETION_RESIDUAL",
                    "error_message": command.diagnostic_id,
                    "source": "project_completion.convergence",
                },
                "verification_failure_report": {
                    "diagnostic_id": command.diagnostic_id,
                    "obligation_id": command.obligation_id,
                    "action_kind": command.action_kind,
                },
            },
            reopen_policy={
                "allowed_source_prefixes": ["orchestration.workflow_orchestration."],
                "max_reopen_count": 8,
                "requires_failure_report": True,
            },
            idempotency_key=command.action_id,
            idempotency_fingerprint=claim_id,
        )

    @staticmethod
    def _project_receipt(
        command: ProjectCompletionActionCommandV1,
        receipt: TaskRequeueReceiptV1,
    ) -> ProjectCompletionActionReceiptV1:
        expected = TaskMarketProjectCompletionActionOwnerV1._requeue_command(
            command,
            claim_id=receipt.idempotency_fingerprint,
        )
        if (
            receipt.workspace != command.identity.workspace
            or receipt.task_id != command.owner_task_id
            or receipt.idempotency_key != command.action_id
            or receipt.effect_hash != expected.effect_hash
            or receipt.target_stage != expected.target_stage
            or receipt.reason != expected.reason
        ):
            raise RuntimeError("project_completion_task_market_receipt_effect_mismatch")
        provisional = ProjectCompletionActionReceiptV1(
            identity=command.identity,
            action_id=command.action_id,
            handoff_id=command.action_id,
            diagnostic_id=command.diagnostic_id,
            owner_task_id=command.owner_task_id,
            status="accepted",
            lease_id=receipt.idempotency_fingerprint,
            settlement_id=receipt.receipt_hash,
            effect_hash=receipt.effect_hash,
            receipt_hash="0" * 64,
        )
        return replace(
            provisional,
            receipt_hash=project_completion_action_receipt_hash(
                identity=provisional.identity,
                action_id=provisional.action_id,
                handoff_id=provisional.handoff_id,
                diagnostic_id=provisional.diagnostic_id,
                owner_task_id=provisional.owner_task_id,
                status=provisional.status,
                lease_id=provisional.lease_id,
                settlement_id=provisional.settlement_id,
                effect_hash=provisional.effect_hash,
            ),
        )

    async def query_project_completion_action_receipt(
        self,
        command: ProjectCompletionActionCommandV1,
    ) -> ProjectCompletionActionReceiptV1 | None:
        receipt = await asyncio.to_thread(
            self._service.query_task_requeue_receipt,
            QueryTaskRequeueReceiptV1(
                workspace=command.identity.workspace,
                task_id=command.owner_task_id,
                idempotency_key=command.action_id,
            ),
        )
        if receipt is None:
            return None
        return self._project_receipt(command, receipt)

    async def dispatch_project_completion_action(
        self,
        command: ProjectCompletionActionCommandV1,
        claim: ProjectCompletionDispatchClaimV1,
    ) -> ProjectCompletionActionReceiptV1:
        requeue = self._requeue_command(command, claim_id=claim.claim_id)
        try:
            result = await asyncio.to_thread(self._service.requeue_task, requeue)
        except RuntimeError:
            # A competing process may win the SQLite CAS between our lookup
            # and write.  Only the exact durable receipt converts that race to
            # success; every other failure remains fail-closed.
            raced_receipt = await self.query_project_completion_action_receipt(command)
            if raced_receipt is not None and raced_receipt.lease_id == claim.claim_id:
                return raced_receipt
            raise
        if not result.ok or result.reason not in {"requeued", "already_requeued"}:
            raise RuntimeError(f"project_completion_task_market_requeue_failed:{result.reason}")
        receipt = await self.query_project_completion_action_receipt(command)
        if receipt is None:
            raise RuntimeError("project_completion_task_market_receipt_missing_after_commit")
        return receipt


__all__ = ["TaskMarketProjectCompletionActionOwnerV1"]
