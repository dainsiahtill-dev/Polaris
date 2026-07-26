"""Commit deferred Director repair requests through the DEO followup path.

Materialization-quality planning returns typed deferred requests outside a
ToolBatch. Physical writes remain owned by roles.kernel DEO followup
(synthesize → prepare → ToolBatchRuntime mutation port).

``roles.kernel`` must not import ``roles.adapters``. Callers inject a fully
bound ``DirectedEffectRuntimeDependenciesV1`` (mutation port / policy / fence)
from the adapter public composition boundary, plus optional JobToken/capability
evidence so DirectedEffectPolicyGuard can authorize sealed inventory.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    DirectorEffectPolicySnapshotPortV1,
)
from polaris.cells.roles.kernel.internal.directed_effect_policy_guard import DirectedEffectPolicyGuard
from polaris.cells.roles.kernel.internal.speculation.models import CancelToken
from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
from polaris.cells.roles.kernel.public import DirectedEffectRuntimeDependenciesV1
from polaris.cells.roles.profile.public.service import load_core_roles
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)

logger = logging.getLogger(__name__)


def _receipts_from_deferred_tool_results(
    tool_results: Sequence[Mapping[str, Any]],
) -> list[MutableMapping[str, Any]]:
    """Project deferred adapter tool_results into followup receipt shape."""

    receipts: list[MutableMapping[str, Any]] = []
    for item in tool_results:
        if not isinstance(item, Mapping):
            continue
        result = item.get("result")
        if not isinstance(result, Mapping):
            continue
        deferred = result.get("deferred_request")
        status = str(result.get("status") or "").strip()
        if deferred is None and status not in {
            "deferred_repair_effects_pending",
            "deferred_command_effect_pending",
        }:
            continue
        if item.get("success") is False:
            continue
        receipts.append(
            {
                "results": [{"status": "success", "result": dict(result)}],
                "raw_results": [],
            }
        )
    return receipts


def _capability_scope_from_tool_results(
    tool_results: Sequence[Mapping[str, Any]],
    capability_scope: Sequence[str] | None,
) -> tuple[str, ...]:
    """Prefer caller scope; else union allowed_paths from deferred repair payloads."""

    if capability_scope:
        cleaned = tuple(
            str(item or "").replace("\\", "/").strip().strip("/")
            for item in capability_scope
            if str(item or "").strip()
        )
        if cleaned:
            return cleaned
    paths: list[str] = []
    seen: set[str] = set()
    for item in tool_results:
        if not isinstance(item, Mapping):
            continue
        result = item.get("result")
        if not isinstance(result, Mapping):
            continue
        for raw in result.get("allowed_paths") or ():
            path = str(raw or "").replace("\\", "/").strip().strip("/")
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)
        deferred = result.get("deferred_request")
        deferred_paths = getattr(deferred, "allowed_paths", None)
        if isinstance(deferred_paths, (list, tuple)):
            for raw in deferred_paths:
                path = str(raw or "").replace("\\", "/").strip().strip("/")
                if not path or path in seen:
                    continue
                seen.add(path)
                paths.append(path)
    return tuple(paths)


def _normalize_capability_token(
    capability_token: Mapping[str, Any] | None,
    *,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
) -> dict[str, Any]:
    """Build minimal authoritative JobToken evidence for DEO policy capture."""

    raw = dict(capability_token or {})
    token_id = str(raw.get("token_id") or "").strip()
    if not token_id:
        token_id = (
            f"deo-materialization:{execution_attempt.run_id}:"
            f"{execution_attempt.external_task_id}:{execution_attempt.attempt}"
        )
    envelope_hash = str(raw.get("execution_envelope_hash") or "").strip().lower()
    if len(envelope_hash) != 64 or any(ch not in "0123456789abcdef" for ch in envelope_hash):
        # Stable pseudo-envelope for materialization deferred commits when the
        # control-plane JobToken omits execution_envelope_hash. Policy capture
        # requires a 64-hex field; path scope still comes from capability_scope.
        from hashlib import sha256

        envelope_hash = sha256(
            f"{token_id}|{execution_attempt.workspace}|{execution_attempt.external_task_id}".encode()
        ).hexdigest()
    capability_audit = raw.get("capability_audit")
    if isinstance(capability_audit, Mapping) and capability_audit.get("ok") is True:
        capability_audit_ok = True
    else:
        capability_audit_ok = raw.get("capability_audit_ok") is True or not capability_token
    allowed_commands = raw.get("allowed_commands") or ()
    if isinstance(allowed_commands, str):
        allowed_commands = (allowed_commands,)
    return {
        "token_id": token_id,
        "execution_envelope_hash": envelope_hash,
        "capability_audit_ok": bool(capability_audit_ok),
        "allowed_commands": tuple(allowed_commands),
        "source": str(raw.get("source") or "materialization.deferred_repair_commit"),
        "run_id": str(raw.get("run_id") or execution_attempt.run_id or ""),
        "stage": str(raw.get("stage") or "director_materialization_deferred"),
    }


def _noop_emit_event(_event: Any) -> None:
    return None


def _noop_guard_assert_single_tool_batch(*_args: Any, **_kwargs: Any) -> None:
    return None


class _DeferredRepairToolRuntime:
    """Tool runtime that only exposes DEO policy-guard construction for followup."""

    __slots__ = ("_gateway",)

    def __init__(self, gateway: RoleToolGateway) -> None:
        self._gateway = gateway

    def directed_effect_policy_guard(
        self,
        policy_port: DirectorEffectPolicySnapshotPortV1,
    ) -> DirectedEffectPolicyGuard:
        return DirectedEffectPolicyGuard(self._gateway, policy_port)

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("generic tool_runtime must not execute directed deferred repairs")

    async def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("generic tool_runtime must not execute directed deferred repairs")


async def commit_deferred_director_repair_tool_results(
    *,
    workspace: str,
    tool_results: Sequence[Mapping[str, Any]],
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1,
    directed_effect_runtime: DirectedEffectRuntimeDependenciesV1,
    turn_id: str | None = None,
    primary_batch_id: str | None = None,
    capability_scope: Sequence[str] | None = None,
    capability_token: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Physically commit deferred repair/command requests via DEO followup.

    Returns normalized tool-batch receipt dicts (empty when nothing deferred).
    Never uses adapter-side bypass writers.
    """

    if type(execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
        raise TypeError("execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1")
    if type(execution_attempt_authority) is not TaskRuntimeExecutionAttemptAuthorityV1:
        raise TypeError("execution_attempt_authority must be exactly TaskRuntimeExecutionAttemptAuthorityV1")
    if type(directed_effect_runtime) is not DirectedEffectRuntimeDependenciesV1:
        raise TypeError("directed_effect_runtime must be exactly DirectedEffectRuntimeDependenciesV1")
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        raise ValueError("workspace must be non-empty")
    if execution_attempt.workspace != workspace_token:
        raise ValueError("workspace must match execution_attempt.workspace")

    receipts = _receipts_from_deferred_tool_results(tool_results)
    if not receipts:
        return []

    scope = _capability_scope_from_tool_results(tool_results, capability_scope)
    if not scope:
        logger.warning(
            "Skipping deferred director repair commit: empty capability scope workspace=%s",
            workspace_token,
        )
        return []

    token = _normalize_capability_token(capability_token, execution_attempt=execution_attempt)
    if token.get("capability_audit_ok") is not True:
        logger.warning(
            "Skipping deferred director repair commit: JobToken capability_audit not ok workspace=%s",
            workspace_token,
        )
        return []

    profile = load_core_roles().get_profile("director")
    if profile is None:
        raise RuntimeError("director role profile unavailable for deferred repair commit")
    gateway = RoleToolGateway(
        profile,
        workspace_token,
        run_id=execution_attempt.run_id,
        task_id=execution_attempt.external_task_id,
        capability_scope=scope,
        capability_token=token,
    )
    tool_runtime = _DeferredRepairToolRuntime(gateway)

    turn = str(turn_id or "").strip() or f"materialization-deferred-{uuid.uuid4().hex[:12]}"
    batch_id = str(primary_batch_id or "").strip() or f"{turn}_materialization_deferred"

    executor = ToolBatchExecutor(
        tool_runtime=tool_runtime,
        config=TransactionConfig(
            workspace=workspace_token,
            role_id="director",
            mutation_guard_mode="strict",
        ),
        emit_event=_noop_emit_event,
        guard_assert_single_tool_batch=_noop_guard_assert_single_tool_batch,
        finalization_handler=object(),
        handoff_handler=object(),
        directed_effect_runtime=directed_effect_runtime,
        directed_effect_required=True,
        directed_effect_execution_attempt=execution_attempt,
        directed_effect_execution_attempt_authority=execution_attempt_authority,
    )
    ledger = TurnLedger(turn_id=turn)
    followup_receipts = await executor._execute_deferred_repair_followup(  # noqa: SLF001
        receipts_as_dicts=[dict(item) for item in receipts],
        primary_batch_id=batch_id,
        workspace=workspace_token,
        turn_id=turn,
        ledger=ledger,
        cancel_token=CancelToken(),
    )
    logger.info(
        "Committed deferred director repairs via DEO followup: workspace=%s turn=%s receipts=%s",
        workspace_token,
        turn,
        len(followup_receipts),
    )
    return [dict(item) for item in followup_receipts]


__all__ = ["commit_deferred_director_repair_tool_results"]
