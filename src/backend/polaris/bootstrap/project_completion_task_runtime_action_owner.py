"""Production project-completion action owner backed by ``runtime.task_runtime``.

The durable workflow cursor owns dispatch claims.  TaskRuntime owns the exact
numeric Director task row and is therefore the only valid place to reopen it.
TaskMarket is deliberately absent: its Factory mainline migration is staged and
must not become a second execution authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, replace

from polaris.cells.orchestration.workflow_orchestration.public.project_completion import (
    ProjectCompletionActionCommandV1,
    ProjectCompletionActionPortV1,
    ProjectCompletionActionReceiptV1,
    ProjectCompletionDispatchClaimV1,
    project_completion_action_receipt_hash,
)
from polaris.cells.runtime.task_runtime.public import (
    SAME_TASK_LOCAL_REWORK_AUTHORIZATION_SCHEMA_V1,
    PrepareSameTaskLocalReworkCommandV1,
    QuerySameTaskLocalReworkAuthorizationV1,
    prepare_same_task_local_rework,
    query_observable_task_rows,
    query_same_task_local_rework_authorization,
)


def _effect_hash(command: ProjectCompletionActionCommandV1) -> str:
    payload = {
        "workspace": command.identity.workspace,
        "factory_run_id": command.identity.run_id,
        "external_task_id": command.owner_task_id,
        "completion_contract_hash": command.identity.completion_contract_hash,
        "action_id": command.action_id,
        "diagnostic_id": command.diagnostic_id,
        "obligation_id": command.obligation_id,
        "action_kind": command.action_kind,
        "owner_snapshot_hash": command.owner_snapshot_hash,
        "owner_bundle_hash": command.owner_bundle_hash,
        "diagnostic": asdict(command.diagnostic),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _external_aliases(row: Mapping[str, object]) -> set[str]:
    metadata_raw = row.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
    aliases: set[str] = set()
    for source in (row, metadata):
        for key in ("external_task_id", "source_task_id", "pm_task_id"):
            value = str(source.get(key) or "").strip()
            if value:
                aliases.add(value)
    return aliases


class TaskRuntimeProjectCompletionActionOwnerV1(ProjectCompletionActionPortV1):
    """Project one cursor-claimed action into its exact TaskRuntime owner row."""

    @staticmethod
    def _record(command: ProjectCompletionActionCommandV1) -> Mapping[str, object] | None:
        rows = query_observable_task_rows(command.identity.workspace).rows_for_factory_run(command.identity.run_id)
        matching = [row for row in rows if _external_aliases(row) == {command.owner_task_id}]
        if len(matching) == 1:
            metadata_raw = matching[0].get("metadata")
            metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
            records_raw = metadata.get("same_task_local_rework_authorizations")
            records = records_raw if isinstance(records_raw, list) else []
            matches = [
                item
                for item in records
                if isinstance(item, Mapping) and str(item.get("action_id") or "").strip() == command.action_id
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise RuntimeError("project_completion_task_runtime_receipt_ambiguous")
        elif len(matching) > 1:
            raise RuntimeError("project_completion_task_runtime_owner_identity_ambiguous")

        durable = query_same_task_local_rework_authorization(
            QuerySameTaskLocalReworkAuthorizationV1(
                workspace=command.identity.workspace,
                factory_run_id=command.identity.run_id,
                external_task_id=command.owner_task_id,
                action_id=command.action_id,
            )
        )
        if durable.code == "same_task_local_rework_authorization_not_found":
            return None
        if durable.code != "same_task_local_rework_authorization_found" or not durable.ok:
            raise RuntimeError(f"project_completion_task_runtime_{durable.code}")
        return durable.authorization

    @staticmethod
    def _receipt(
        command: ProjectCompletionActionCommandV1,
        record: Mapping[str, object],
        *,
        status: str,
    ) -> ProjectCompletionActionReceiptV1:
        effect_hash = str(record.get("effect_hash") or "").strip()
        claim_id = str(record.get("dispatch_claim_id") or "").strip()
        if effect_hash != _effect_hash(command) or len(claim_id) != 64:
            raise RuntimeError("project_completion_task_runtime_receipt_effect_mismatch")
        provisional = ProjectCompletionActionReceiptV1(
            identity=command.identity,
            action_id=command.action_id,
            handoff_id=command.action_id,
            diagnostic_id=command.diagnostic_id,
            owner_task_id=command.owner_task_id,
            status=status,
            lease_id=claim_id,
            settlement_id=effect_hash,
            effect_hash=effect_hash,
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
        record = await asyncio.to_thread(self._record, command)
        return None if record is None else self._receipt(command, record, status="accepted")

    async def dispatch_project_completion_action(
        self,
        command: ProjectCompletionActionCommandV1,
        claim: ProjectCompletionDispatchClaimV1,
    ) -> ProjectCompletionActionReceiptV1:
        if claim.identity != command.identity or claim.action_id != command.action_id:
            raise RuntimeError("project_completion_dispatch_claim_mismatch")
        result = await asyncio.to_thread(
            prepare_same_task_local_rework,
            PrepareSameTaskLocalReworkCommandV1(
                schema_version=SAME_TASK_LOCAL_REWORK_AUTHORIZATION_SCHEMA_V1,
                workspace=command.identity.workspace,
                factory_run_id=command.identity.run_id,
                external_task_id=command.owner_task_id,
                completion_contract_hash=command.identity.completion_contract_hash,
                action_id=command.action_id,
                diagnostic_id=command.diagnostic_id,
                obligation_id=command.obligation_id,
                action_kind=command.action_kind,
                owner_snapshot_hash=command.owner_snapshot_hash,
                owner_bundle_hash=command.owner_bundle_hash,
                dispatch_claim=asdict(claim),
                diagnostic=asdict(command.diagnostic),
            ),
        )
        if not result.ok:
            raise RuntimeError(f"project_completion_task_runtime_{result.code}")
        record = await asyncio.to_thread(self._record, command)
        if record is None:
            raise RuntimeError("project_completion_task_runtime_receipt_missing_after_commit")
        return self._receipt(command, record, status="accepted")


__all__ = ["TaskRuntimeProjectCompletionActionOwnerV1"]
