"""Factory Run Service - formal service for unattended development with persistence.

This module is the durable-lifecycle orchestrator (``FactoryRunService``) plus a
thin re-export shim. The data-contracts and shared cancel-registry foundation now
live in :mod:`factory_run_models`, and the production stage executor god-class
lives in :mod:`factory_stage_executor`. Both are re-exported here so the original
import path resolves identically for every existing caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os  # re-exported for lossless surface + test monkeypatch of ``os.name``
import re  # re-exported for lossless surface compatibility
import shutil  # re-exported for lossless surface + test monkeypatch of ``shutil.which``
import subprocess  # re-exported for lossless surface compatibility
import threading  # re-exported for lossless surface compatibility
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field  # re-exported for lossless surface
from datetime import datetime, timezone  # re-exported for lossless surface
from enum import Enum  # re-exported for lossless surface
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol  # Protocol re-exported for lossless surface

from polaris.cells.chief_engineer.blueprint.public import GenerateTaskBlueprintCommandV1, generate_task_blueprint
from polaris.cells.control_plane.run_ledger.public import (
    FactorySettlementBarrierResultV1,
    query_factory_settlement_barrier,
)
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.cells.runtime.task_runtime.public.contracts import (
    FenceExpiredFactoryRunSessionsCommandV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
    fence_expired_factory_run_sessions,
    query_factory_run_settlement,
)
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.storage import resolve_logical_path, resolve_storage_roots
from polaris.kernelone.utils import utc_now_iso

from .factory_run_admission import FactoryWorkspaceRunAdmission
from .factory_run_models import (
    _FACTORY_CANCEL_EVENTS,
    _FACTORY_CANCEL_EVENTS_GUARD,
    _FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS,
    _PM_ARCHITECT_DOC_MAX_CHARS,
    _PM_DIRECTIVE_MAX_CHARS,
    _PM_DIRECTIVE_META_LINE_PATTERN,
    _PM_ORIGINAL_DIRECTIVE_MAX_CHARS,
    _PM_PLAN_META_DIAGNOSTIC_MARKERS,
    _QA_LLM_JUDGEMENT_UNAVAILABLE_WARNING,
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    _WORKSPACE_VALIDATION_TIMEOUT_SECONDS,
    DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS,
    SUPPORTED_FACTORY_STAGES,
    TERMINAL_RUN_STATUSES,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    FactoryStageExecutor,
    StageResult,
    _factory_cancel_key,
    _register_factory_cancel_event,
    _signal_factory_cancel_event,
    _unregister_factory_cancel_event,
)
from .factory_stage_executor import OrchestrationStageExecutor

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.factory.pipeline.public.contracts import (
        FactoryWorkspaceReleaseEvidenceV1,
        FactoryWorkspaceRunLeaseV1,
    )

logger = logging.getLogger(__name__)

_WORKSPACE_LEASE_METADATA_KEY = "factory_workspace_run_lease"
_STAGE_IN_FLIGHT_METADATA_KEY = "factory_stage_in_flight"
_CHILD_SESSIONS_SETTLED_METADATA_KEY = "factory_child_sessions_settled"
_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY = "factory_child_session_settlement_evidence"


def _factory_jetstream_fanout_timeout_seconds() -> float:
    """Resolve the JetStream fanout timeout for ``_append_event``.

    Defined here (not imported from ``factory_run_models``) so it reads the
    module-level ``_FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS`` bound in THIS
    module. This preserves the original single-file behavior where the helper
    and constant were co-located, keeping the constant monkeypatch-able via
    ``factory_run_service._FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS``.
    """
    raw = os.getenv("KERNELONE_FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS")
    if raw is None:
        return _FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS
    try:
        return max(float(raw), 0.05)
    except ValueError:
        return _FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS


# Realtime fanout payload bound: must stay under the JetStream 256KB max-message
# limit AND the WS 1MiB frame limit. Large director stage outputs (full
# ``task_results`` reach ~2.7MB on big projects) remain durable on disk via
# ``store.append_event``; the best-effort realtime fanout only needs control
# fields plus a bounded preview. Headroom kept below 256KB for envelope fields.
_FACTORY_FANOUT_MAX_PAYLOAD_BYTES = 200_000


# NOTE: ``__all__`` intentionally re-exports the symbols, stdlib modules, and
# private constants/helpers that the original single-file module bound at module
# scope. Keeping them here preserves the historical public+private import surface
# (callers / tests import these from ``factory_run_service``) and keeps the names
# from being stripped by ruff as "unused" — they are deliberate re-exports.
__all__ = [
    "DEFAULT_DIRECTOR_MAX_PARALLELISM",
    "DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS",
    "SUPPORTED_FACTORY_STAGES",
    "TERMINAL_RUN_STATUSES",
    "_FACTORY_CANCEL_EVENTS",
    "_FACTORY_CANCEL_EVENTS_GUARD",
    "_FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS",
    "_PM_ARCHITECT_DOC_MAX_CHARS",
    "_PM_DIRECTIVE_MAX_CHARS",
    "_PM_DIRECTIVE_META_LINE_PATTERN",
    "_PM_ORIGINAL_DIRECTIVE_MAX_CHARS",
    "_PM_PLAN_META_DIAGNOSTIC_MARKERS",
    "_QA_LLM_JUDGEMENT_UNAVAILABLE_WARNING",
    "_WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS",
    "_WORKSPACE_VALIDATION_TIMEOUT_SECONDS",
    "CommandResult",
    "Enum",
    "FactoryConfig",
    "FactoryRun",
    "FactoryRunService",
    "FactoryRunStatus",
    "FactoryStageExecutor",
    "GenerateTaskBlueprintCommandV1",
    "KernelFileSystem",
    "OrchestrationStageExecutor",
    "Protocol",
    "StageResult",
    "TaskRuntimeService",
    "_factory_cancel_key",
    "_factory_jetstream_fanout_timeout_seconds",
    "_register_factory_cancel_event",
    "_signal_factory_cancel_event",
    "_unregister_factory_cancel_event",
    "asdict",
    "dataclass",
    "datetime",
    "field",
    "generate_task_blueprint",
    "get_default_adapter",
    "os",
    "re",
    "resolve_logical_path",
    "resolve_storage_roots",
    "shutil",
    "subprocess",
    "threading",
    "timezone",
    "utc_now_iso",
]


class FactoryRunService:
    """Formal service for Factory runs with persistence and recovery."""

    # 细粒度锁桶数量 - 减少跨 run 的竞争
    _LOCK_BUCKETS = 64

    def __init__(
        self,
        workspace: Path,
        cache_root: Path | None = None,
        executor: FactoryStageExecutor | None = None,
        admission: FactoryWorkspaceRunAdmission | None = None,
        settlement_barrier_query: Callable[
            [str | Path, str], FactorySettlementBarrierResultV1
        ] = query_factory_settlement_barrier,
    ) -> None:
        from .factory_store import FactoryStore

        self.workspace = Path(workspace)
        self.cache_root = (
            Path(cache_root)
            if cache_root is not None
            else Path(resolve_storage_roots(str(self.workspace)).runtime_root)
        )
        self.store = FactoryStore(self.cache_root / "factory")
        self._admission = admission or FactoryWorkspaceRunAdmission(
            self.workspace,
            state_root=self.cache_root / "factory",
        )
        self._settlement_barrier_query = settlement_barrier_query
        # 细粒度锁: 按 run_id 哈希分片，减少竞争
        self._run_locks: list[asyncio.Lock] = [asyncio.Lock() for _ in range(self._LOCK_BUCKETS)]
        self._executor: FactoryStageExecutor = executor or OrchestrationStageExecutor(self.workspace)

    def _get_run_lock(self, run_id: str) -> asyncio.Lock:
        """获取 run_id 对应的细粒度锁。

        使用哈希分片确保同一 run 的操作串行化，不同 run 可并行。
        """
        bucket = hash(run_id) % self._LOCK_BUCKETS
        return self._run_locks[bucket]

    @staticmethod
    def _attach_workspace_lease(run: FactoryRun, lease: FactoryWorkspaceRunLeaseV1) -> None:
        run.metadata[_WORKSPACE_LEASE_METADATA_KEY] = lease.to_dict()

    @staticmethod
    def _workspace_lease_fencing_token(run: FactoryRun) -> int:
        payload = run.metadata.get(_WORKSPACE_LEASE_METADATA_KEY)
        lease_data = payload if isinstance(payload, Mapping) else {}
        try:
            fencing_token = int(lease_data.get("fencing_token") or 0)
        except (TypeError, ValueError) as exc:
            fencing_token = 0
            token_error: Exception | None = exc
        else:
            token_error = None
        if fencing_token > 0:
            return fencing_token

        from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

        raise FactoryPipelineError(
            "Factory run has no valid workspace fencing authority",
            code="factory_workspace_run_lease_authority_missing",
            details={"run_id": run.id, "error": str(token_error or "missing fencing_token")},
        )

    @classmethod
    def _expected_lifecycle_fencing_token(cls, run: FactoryRun) -> int | None:
        """Return authority proof only while the run projects an owned lease.

        A missing or released projection requests a fresh atomic acquisition.
        ACTIVE and DRAINING projections must carry their original fencing token;
        the admission ledger never supplies or upgrades that proof for callers.
        """

        payload = run.metadata.get(_WORKSPACE_LEASE_METADATA_KEY)
        lease_data = payload if isinstance(payload, Mapping) else {}
        state = str(lease_data.get("state") or "").strip().lower()
        if not state or state == "released":
            return None
        if state in {"active", "draining"}:
            return cls._workspace_lease_fencing_token(run)

        from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

        raise FactoryPipelineError(
            "Factory run has an invalid workspace lease projection",
            code="factory_workspace_run_lease_projection_invalid",
            details={"run_id": run.id, "state": state},
        )

    def _acquire_workspace_lease(self, run: FactoryRun) -> FactoryWorkspaceRunLeaseV1:
        lease = self._admission.acquire(run.id)
        self._attach_workspace_lease(run, lease)
        return lease

    def _claim_lifecycle_operation(
        self,
        run: FactoryRun,
        *,
        operation: str,
        nonce: str,
        acquire_if_available: bool,
        expected_fencing_token: int | None = None,
    ) -> FactoryWorkspaceRunLeaseV1:
        expected_token = (
            self._expected_lifecycle_fencing_token(run)
            if expected_fencing_token is None
            else expected_fencing_token
        )
        lease = self._admission.claim_lifecycle_operation(
            run.id,
            operation=operation,
            nonce=nonce,
            acquire_if_available=acquire_if_available,
            expected_fencing_token=expected_token,
        )
        self._attach_workspace_lease(run, lease)
        return lease

    async def _release_lifecycle_operation(
        self,
        run: FactoryRun,
        *,
        operation: str,
        nonce: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        lease = self._admission.release_lifecycle_operation(
            run.id,
            fencing_token=self._workspace_lease_fencing_token(run),
            operation=operation,
            nonce=nonce,
        )
        self._attach_workspace_lease(run, lease)
        run.updated_at = self._now()
        await self.store.save_run(run)
        return lease

    async def _rollback_lifecycle_operation(
        self,
        run: FactoryRun,
        *,
        operation: str,
        nonce: str,
        reason: str,
        persist_run: bool = True,
    ) -> None:
        """Best-effort authority rollback while preserving the original error."""

        try:
            lease = self._admission.rollback_lifecycle_operation(
                run.id,
                fencing_token=self._workspace_lease_fencing_token(run),
                operation=operation,
                nonce=nonce,
                reason=reason,
            )
            self._attach_workspace_lease(run, lease)
            if persist_run:
                run.updated_at = self._now()
                await self.store.save_run(run)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.error(
                "Factory lifecycle rollback failed run_id=%s operation=%s: %s",
                run.id,
                operation,
                exc,
            )

    @staticmethod
    def _workspace_release_evidence(
        run_id: str,
        settlement: Mapping[str, object],
        *,
        source: str,
        observed_at: str,
        fenced_session_ids: tuple[str, ...] = (),
        details: Mapping[str, Any] | None = None,
    ) -> FactoryWorkspaceReleaseEvidenceV1:
        from polaris.cells.factory.pipeline.public.contracts import (
            FactoryWorkspaceReleaseEvidenceV1,
        )

        return FactoryWorkspaceReleaseEvidenceV1(
            factory_run_id=run_id,
            source=source,
            observed_at=observed_at,
            active_session_count=int(str(settlement.get("active_session_count") or 0)),
            conflict_count=int(str(settlement.get("conflict_count") or 0)),
            fenced_session_ids=fenced_session_ids,
            details={"task_runtime_settlement": dict(settlement), **dict(details or {})},
        )

    def _renew_workspace_lease(
        self,
        run: FactoryRun,
        *,
        require_active: bool,
    ) -> FactoryWorkspaceRunLeaseV1:
        fencing_token = self._workspace_lease_fencing_token(run)
        lease = self._admission.renew(run.id, fencing_token=fencing_token)
        if require_active and lease.state.value != "active":
            from polaris.cells.factory.pipeline.public.contracts import (
                FactoryWorkspaceRunLeaseConflictError,
            )

            raise FactoryWorkspaceRunLeaseConflictError(
                "Factory stage execution requires an ACTIVE workspace lease",
                code="factory_workspace_run_not_active",
                requested_run_id=run.id,
                current_lease=lease,
            )
        self._attach_workspace_lease(run, lease)
        return lease

    def _assert_active_workspace_lease(self, run: FactoryRun) -> FactoryWorkspaceRunLeaseV1:
        fencing_token = self._workspace_lease_fencing_token(run)
        lease = self._admission.assert_active(run.id, fencing_token=fencing_token)
        self._attach_workspace_lease(run, lease)
        return lease

    def _claim_stage_execution(
        self,
        run: FactoryRun,
        *,
        stage: str,
        nonce: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        lease = self._admission.claim_stage(
            run.id,
            fencing_token=self._workspace_lease_fencing_token(run),
            stage=stage,
            nonce=nonce,
        )
        self._attach_workspace_lease(run, lease)
        return lease

    async def _release_stage_execution(
        self,
        run: FactoryRun,
        *,
        stage: str,
        nonce: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        lease = self._admission.release_stage(
            run.id,
            fencing_token=self._workspace_lease_fencing_token(run),
            stage=stage,
            nonce=nonce,
        )
        self._attach_workspace_lease(run, lease)
        latest = await self.store.get_run(run.id)
        target_run = latest or run
        self._attach_workspace_lease(target_run, lease)
        target_run.updated_at = self._now()
        await self.store.save_run(target_run)
        return lease

    @staticmethod
    def _stage_result_releases_execution_claim(result: StageResult) -> bool:
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        if metadata.get("inflight_run_continues") is True:
            return False
        return metadata.get("child_sessions_settled") is not False

    async def _reconcile_stage_execution_claim(
        self,
        run: FactoryRun,
        *,
        settlement: Mapping[str, object] | None = None,
    ) -> FactoryWorkspaceRunLeaseV1 | None:
        current = self._admission.current()
        if current is None or current.run_id != run.id:
            return current
        claim = current.stage_execution_claim
        if claim is None or run.metadata.get(_STAGE_IN_FLIGHT_METADATA_KEY) is not False:
            self._attach_workspace_lease(run, current)
            return current

        evidence = dict(settlement or self._query_child_session_settlement(run.id))
        run.metadata[_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY] = evidence
        run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = evidence.get("settled") is True
        if evidence.get("settled") is not True:
            self._attach_workspace_lease(run, current)
            return current

        self._attach_workspace_lease(run, current)
        return await self._release_stage_execution(
            run,
            stage=claim.stage,
            nonce=claim.nonce,
        )

    async def _begin_terminal_drain(
        self,
        run: FactoryRun,
        *,
        reason: str,
        operation_nonce: str,
    ) -> FactoryWorkspaceRunLeaseV1 | None:
        payload = run.metadata.get(_WORKSPACE_LEASE_METADATA_KEY)
        if not isinstance(payload, Mapping):
            if run.status == FactoryRunStatus.PENDING:
                return None
            current = self._admission.current()
            if current is None or current.state.value == "released":
                return None
            from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

            raise FactoryPipelineError(
                "Factory terminal transition lacks persisted fencing authority",
                code="factory_workspace_run_lease_authority_missing",
                details={"run_id": run.id, "current_lease": current.to_dict()},
            )
        lease = self._admission.begin_draining(
            run.id,
            fencing_token=self._workspace_lease_fencing_token(run),
            reason=reason,
            operation_nonce=operation_nonce,
        )
        self._attach_workspace_lease(run, lease)
        await self.store.save_run(run)
        return lease

    async def _record_drain_conflict(
        self,
        run: FactoryRun,
        lease: FactoryWorkspaceRunLeaseV1,
        *,
        code: str,
        message: str,
        details: Mapping[str, Any],
    ) -> FactoryRun:
        observed_at = self._now()
        conflict: dict[str, Any] = {
            "code": code,
            "message": message,
            "details": dict(details),
            "observed_at": observed_at,
        }
        run.metadata["factory_workspace_run_drain_conflict"] = conflict
        self._attach_workspace_lease(run, lease)
        run.updated_at = observed_at
        await self.store.save_run(run)
        return run

    def _query_child_session_settlement(self, run_id: str) -> dict[str, object]:
        return query_factory_run_settlement(
            str(self.workspace),
            factory_run_id=run_id,
        )

    async def _require_child_session_settlement_for_reentry(
        self,
        run: FactoryRun,
        *,
        operation: str,
    ) -> dict[str, object]:
        evidence = self._query_child_session_settlement(run.id)
        settled = evidence.get("settled") is True
        run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = settled
        run.metadata[_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY] = evidence
        if settled:
            run.metadata.pop("factory_child_session_conflict", None)
            return evidence

        observed_at = self._now()
        conflict: dict[str, Any] = {
            "code": "factory_workspace_run_child_session_inflight",
            "message": "Factory run cannot re-enter while a child execution session is active",
            "operation": operation,
            "settlement": evidence,
            "observed_at": observed_at,
        }
        run.metadata["factory_child_session_conflict"] = conflict
        run.updated_at = observed_at
        await self.store.save_run(run)
        from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

        raise FactoryPipelineError(
            conflict["message"],
            code=conflict["code"],
            details={
                "run_id": run.id,
                "operation": operation,
                "settlement": evidence,
            },
        )

    async def _finalize_terminal_drain(
        self,
        run: FactoryRun,
        lease: FactoryWorkspaceRunLeaseV1 | None,
        *,
        operation_nonce: str,
    ) -> FactoryRun:
        if lease is None or lease.state.value == "released":
            return run

        latest = await self.store.get_run(run.id)
        target_run = latest or run
        stage_in_flight = target_run.metadata.get(_STAGE_IN_FLIGHT_METADATA_KEY)
        if stage_in_flight is not False:
            return await self._record_drain_conflict(
                target_run,
                lease,
                code="factory_workspace_run_drain_unproven",
                message="Factory workspace drain cannot prove stage settlement",
                details={
                    "stage_in_flight": stage_in_flight,
                },
            )

        settlement = self._query_child_session_settlement(target_run.id)
        target_run.metadata[_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY] = settlement
        if settlement.get("settled") is not True:
            target_run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = False
            return await self._record_drain_conflict(
                target_run,
                lease,
                code="factory_workspace_run_child_session_inflight",
                message="Factory workspace drain found an active or foreign child session",
                details={"settlement": settlement},
            )

        reconciled_lease = await self._reconcile_stage_execution_claim(
            target_run,
            settlement=settlement,
        )
        if reconciled_lease is not None:
            lease = reconciled_lease

        task_count = int(str(settlement.get("observable_row_count") or 0))
        barrier_evidence: dict[str, Any] = {
            "required": task_count > 0,
            "factory_run_id": target_run.id,
        }
        if task_count > 0:
            barrier = self._settlement_barrier_query(self.workspace, target_run.id)
            barrier_evidence.update(
                {
                    "schema_version": barrier.schema_version,
                    "barrier_hash": barrier.barrier_hash,
                    "closed": barrier.closed,
                    "passed": barrier.passed,
                    "release_allowed": barrier.release_allowed,
                    "blocking_reasons": list(barrier.blocking_reasons),
                    "evidence_refs": list(barrier.evidence_refs),
                    "consumed_run_ids": list(barrier.consumed_run_ids),
                }
            )
            target_run.metadata["factory_run_ledger_settlement_barrier"] = barrier_evidence
            if not barrier.release_allowed:
                return await self._record_drain_conflict(
                    target_run,
                    lease,
                    code="factory_run_ledger_settlement_barrier_open",
                    message="Factory workspace drain found open Run Ledger obligations",
                    details={"settlement_barrier": barrier_evidence},
                )
        else:
            target_run.metadata["factory_run_ledger_settlement_barrier"] = barrier_evidence

        reset_summary = TaskRuntimeService(str(self.workspace)).reset_records(
            keep_plan=True,
            factory_run_id=target_run.id,
        )
        if reset_summary.get("ok") is not True or int(str(reset_summary.get("failed_count") or 0)) > 0:
            target_run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = False
            return await self._record_drain_conflict(
                target_run,
                lease,
                code="factory_workspace_run_task_runtime_drain_conflict",
                message="TaskRuntime records did not settle under the Factory run authority",
                details={"task_runtime_reset": reset_summary},
            )

        target_run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = True
        release_evidence = self._workspace_release_evidence(
            target_run.id,
            settlement,
            source="factory_terminal_drain",
            observed_at=self._now(),
            details={
                "task_runtime_reset": reset_summary,
                "settlement_barrier": barrier_evidence,
            },
        )
        released = self._admission.release(
            target_run.id,
            fencing_token=lease.fencing_token,
            settlement_evidence=release_evidence,
            operation_nonce=operation_nonce,
        )
        self._attach_workspace_lease(target_run, released)
        target_run.metadata.pop("factory_workspace_run_drain_conflict", None)
        target_run.updated_at = self._now()
        await self.store.save_run(target_run)
        await self._append_event(
            target_run.id,
            {
                "type": "workspace_run_lease_released",
                "message": "Factory workspace run lease released after draining",
                "lease": released.to_dict(),
                "timestamp": target_run.updated_at,
            },
        )
        return target_run

    async def create_run(self, config: FactoryConfig) -> FactoryRun:
        """Create a new factory run with directory structure."""
        run = FactoryRun(
            id=f"factory_{uuid.uuid4().hex[:12]}",
            config=config,
            status=FactoryRunStatus.PENDING,
            created_at=self._now(),
            metadata={
                "current_stage": None,
                "last_stage": None,
                "last_successful_stage": None,
                "last_failed_stage": None,
                _STAGE_IN_FLIGHT_METADATA_KEY: False,
                _CHILD_SESSIONS_SETTLED_METADATA_KEY: True,
            },
        )

        run_dir = self.store.get_run_dir(run.id)
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (run_dir / "events").mkdir(parents=True, exist_ok=True)
        (run_dir / "checkpoints").mkdir(parents=True, exist_ok=True)

        await self.store.save_run(run)
        logger.info("Created factory run %s", run.id)
        return run

    async def execute_stage(
        self,
        run_id: str,
        stage: str,
        context: dict[str, Any] | None = None,
    ) -> StageResult:
        """Execute a single stage with durable lifecycle updates."""
        normalized_context = dict(context or {})
        normalized_context["_factory_abort_checker"] = self._build_abort_checker(run_id)
        cancel_event = _register_factory_cancel_event(self.workspace, run_id)
        normalized_context["_factory_cancel_event"] = cancel_event
        heartbeat_interval = self._resolve_heartbeat_interval_seconds(normalized_context)
        stage_claim_nonce = f"stage_{uuid.uuid4().hex}"

        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            if run.status not in {FactoryRunStatus.RUNNING, FactoryRunStatus.RECOVERING}:
                current_lease = self._admission.current()
                if (
                    current_lease is not None
                    and current_lease.run_id == run.id
                    and current_lease.stage_execution_claim is not None
                ):
                    self._attach_workspace_lease(run, current_lease)
                    self._claim_stage_execution(
                        run,
                        stage=stage,
                        nonce=stage_claim_nonce,
                    )
                raise ValueError(f"Run {run_id} is not executable in status {run.status.value}")
            self._renew_workspace_lease(run, require_active=True)
            self._claim_stage_execution(
                run,
                stage=stage,
                nonce=stage_claim_nonce,
            )
            started_at = self._now()
            await self._mark_stage_started(run, stage, started_at)

        heartbeat_task: asyncio.Task[None] | None = None
        if heartbeat_interval > 0:
            heartbeat_task = asyncio.create_task(
                self._run_stage_heartbeat(run_id, stage, heartbeat_interval),
                name=f"factory_stage_heartbeat:{run_id}:{stage}",
            )

        try:
            result = await self._execute_stage_logic(run, stage, normalized_context)
        except (
            AttributeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            asyncio.TimeoutError,
        ) as exc:
            result = StageResult(
                stage=stage,
                status="failed",
                output=f"{stage} failed: {exc}",
                artifacts=[],
                started_at=started_at,
                completed_at=self._now(),
                metadata={
                    "child_sessions_settled": False,
                    "inflight_run_continues": True,
                    "settlement_source": "factory_stage_wrapper_exception",
                },
            )
            async with run_lock:
                self._renew_workspace_lease(run, require_active=False)
                await self._mark_stage_finished(run, result, error=exc)
            logger.error("Stage %s failed for run %s: %s", stage, run_id, exc)
            raise
        finally:
            _unregister_factory_cancel_event(self.workspace, run_id, cancel_event)
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    try:
                        await heartbeat_task
                    except (
                        AttributeError,
                        OSError,
                        RuntimeError,
                        TypeError,
                        ValueError,
                        asyncio.TimeoutError,
                    ) as heartbeat_exc:
                        logger.warning(
                            "Factory heartbeat task failed for run %s stage %s: %s",
                            run_id,
                            stage,
                            heartbeat_exc,
                        )

        result.started_at = result.started_at or started_at
        result.completed_at = result.completed_at or self._now()
        terminal_after_stage = False
        async with run_lock:
            self._renew_workspace_lease(run, require_active=False)
            await self._mark_stage_finished(run, result)
            if self._stage_result_releases_execution_claim(result):
                await self._release_stage_execution(
                    run,
                    stage=stage,
                    nonce=stage_claim_nonce,
                )
            latest = await self.store.get_run(run_id)
            current_lease = self._admission.current()
            terminal_after_stage = (
                latest is not None
                and latest.status in TERMINAL_RUN_STATUSES
                and current_lease is not None
                and current_lease.run_id == run_id
                and current_lease.state.value in {"active", "draining"}
            )
        if terminal_after_stage and self._stage_result_releases_execution_claim(result):
            await self.settle_terminal_run(run_id)
        return result

    async def _run_stage_heartbeat(
        self,
        run_id: str,
        stage: str,
        interval_seconds: float,
    ) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            await self._emit_stage_heartbeat(run_id, stage)

    async def _emit_stage_heartbeat(self, run_id: str, stage: str) -> None:
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                return
            current_stage = str(run.metadata.get("current_stage") or "").strip()
            if current_stage != stage:
                return

            self._renew_workspace_lease(run, require_active=False)
            timestamp = self._now()
            run.updated_at = timestamp
            run.metadata["last_stage_heartbeat_at"] = timestamp
            await self.store.save_run(run)
            await self._append_event(
                run_id,
                {
                    "type": "stage_heartbeat",
                    "stage": stage,
                    "message": f"Stage {stage} is still running",
                    "timestamp": timestamp,
                },
            )

    def _build_abort_checker(self, run_id: str) -> Callable[[], Awaitable[str | None]]:
        async def _checker() -> str | None:
            current_run = await self.store.get_run(run_id)
            if current_run is None:
                return "run_not_found"
            if current_run.status == FactoryRunStatus.CANCELLED:
                return str(current_run.metadata.get("cancel_reason") or "run_cancelled")
            return None

        return _checker

    @staticmethod
    def _resolve_heartbeat_interval_seconds(context: dict[str, Any]) -> float:
        raw_value = context.get("heartbeat_interval_seconds")
        if raw_value is None:
            return DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS
        if value <= 0:
            return 0.0
        return max(0.05, min(value, 300.0))

    async def recover_run(self, run_id: str) -> FactoryRun:
        """Recover a run from durable storage."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status in TERMINAL_RUN_STATUSES:
                return run

            operation = "recover_run"
            nonce = f"lifecycle_{uuid.uuid4().hex}"
            claimed = False
            try:
                self._claim_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=nonce,
                    acquire_if_available=True,
                )
                claimed = True
                settlement = await self._require_child_session_settlement_for_reentry(
                    run,
                    operation=operation,
                )
                run.metadata[_STAGE_IN_FLIGHT_METADATA_KEY] = False
                run.updated_at = self._now()
                await self.store.save_run(run)
                await self._reconcile_stage_execution_claim(run, settlement=settlement)
                last_successful_stage = (
                    str(run.metadata.get("last_successful_stage") or "").strip()
                    or str(run.recovery_point or "").strip()
                    or str(await self._find_last_successful_stage(run_id) or "").strip()
                    or None
                )
                run.recovery_point = last_successful_stage
                run.status = FactoryRunStatus.RECOVERING
                run.updated_at = self._now()
                run.metadata["current_stage"] = last_successful_stage
                run.metadata["last_stage"] = last_successful_stage
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "recovered",
                        "stage": last_successful_stage,
                        "message": f"Recovered run at {last_successful_stage or 'start'}",
                        "timestamp": run.updated_at,
                    },
                )
                await self._release_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=nonce,
                )
                claimed = False
                logger.info("Run %s recovered at stage %s", run_id, last_successful_stage)
                return run
            except Exception:
                if claimed:
                    await self._rollback_lifecycle_operation(
                        run,
                        operation=operation,
                        nonce=nonce,
                        reason="recover_run_failed",
                    )
                raise

    async def retry_run_from_stage(
        self,
        run_id: str,
        target_stage: str | None = None,
        reason: str | None = None,
    ) -> FactoryRun:
        """Move a run into recovery from a checkpoint or configured stage."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status in {FactoryRunStatus.COMPLETED, FactoryRunStatus.CANCELLED}:
                return run
            if run.status != FactoryRunStatus.FAILED:
                raise ValueError(f"Run {run_id} cannot be retried in status {run.status.value}")

            self._acquire_workspace_lease(run)
            settlement = await self._require_child_session_settlement_for_reentry(
                run,
                operation="retry_run_from_stage",
            )

            configured_stages = [str(stage).strip() for stage in run.config.stages if str(stage).strip()]
            requested_stage = str(target_stage or "").strip()
            if requested_stage and requested_stage not in configured_stages:
                raise ValueError(f"Stage {requested_stage} is not configured for run {run_id}")

            retry_stage = (
                requested_stage
                or str(run.metadata.get("last_successful_stage") or "").strip()
                or str(run.recovery_point or "").strip()
                or str(await self._find_last_successful_stage(run_id) or "").strip()
                or None
            )
            retry_start_policy = "rerun_stage" if requested_stage else "after_checkpoint"
            retry_execution_stage = retry_stage
            if retry_stage and retry_stage in configured_stages:
                stage_index = configured_stages.index(retry_stage)
                rerun_start_index = stage_index if requested_stage else stage_index + 1
            else:
                rerun_start_index = 0
            stages_to_rerun = set(configured_stages[rerun_start_index:])
            if stages_to_rerun:
                run.stages_completed = [stage for stage in run.stages_completed if stage not in stages_to_rerun]
                run.stages_failed = [stage for stage in run.stages_failed if stage not in stages_to_rerun]
                retry_execution_stage = (
                    configured_stages[rerun_start_index] if rerun_start_index < len(configured_stages) else retry_stage
                )

            timestamp = self._now()
            previous_status = run.status.value
            previous_failure = run.metadata.get("failure")
            run.recovery_point = retry_stage
            run.status = FactoryRunStatus.RECOVERING
            run.completed_at = None
            run.updated_at = timestamp
            run.metadata["current_stage"] = retry_execution_stage
            run.metadata["last_stage"] = retry_stage
            run.metadata["retry_from_status"] = previous_status
            run.metadata["retry_start_policy"] = retry_start_policy
            run.metadata["retry_requested_stage"] = requested_stage or None
            run.metadata["retry_execution_stage"] = retry_execution_stage
            run.metadata[_STAGE_IN_FLIGHT_METADATA_KEY] = False
            if previous_failure:
                run.metadata["retry_previous_failure"] = previous_failure
            run.metadata["failure"] = None
            run.metadata["last_failed_stage"] = None
            if reason:
                run.metadata["retry_reason"] = reason
            await self.store.save_run(run)
            await self._reconcile_stage_execution_claim(run, settlement=settlement)
            await self._append_event(
                run_id,
                {
                    "type": "retry_requested",
                    "stage": retry_stage,
                    "message": f"Retry requested from {retry_stage or 'start'}",
                    "reason": reason,
                    "previous_status": previous_status,
                    "timestamp": timestamp,
                },
            )
            logger.info("Run %s retry requested from stage %s", run_id, retry_stage)
            return run

    async def execute_pause(self, run_id: str) -> FactoryRun:
        """Pause a running factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status == FactoryRunStatus.RUNNING:
                self._renew_workspace_lease(run, require_active=True)
                run.status = FactoryRunStatus.PAUSED
                run.updated_at = self._now()
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "paused",
                        "message": "Run paused",
                        "timestamp": run.updated_at,
                    },
                )
                logger.info("Run %s paused", run_id)
            return run

    async def execute_resume(self, run_id: str) -> FactoryRun:
        """Resume a paused factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status == FactoryRunStatus.PAUSED:
                self._acquire_workspace_lease(run)
                run.status = FactoryRunStatus.RUNNING
                run.updated_at = self._now()
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "resumed",
                        "message": "Run resumed",
                        "timestamp": run.updated_at,
                    },
                )
                logger.info("Run %s resumed", run_id)
            return run

    async def update_run_metadata(self, run_id: str, metadata: dict[str, Any]) -> FactoryRun:
        """Persist metadata updates for an existing factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            run.metadata.update(dict(metadata))
            run.updated_at = self._now()
            await self.store.save_run(run)
            await self._append_event(
                run_id,
                {
                    "type": "metadata_updated",
                    "message": "Run metadata updated",
                    "metadata_keys": sorted(str(key) for key in metadata),
                    "timestamp": run.updated_at,
                },
            )
            logger.info("Run %s metadata updated: keys=%s", run_id, sorted(str(key) for key in metadata))
            return run

    async def start_run(self, run_id: str) -> FactoryRun:
        """Start a run only after durable workspace admission succeeds."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status in TERMINAL_RUN_STATUSES:
                return run

            operation = "start_run"
            nonce = f"lifecycle_{uuid.uuid4().hex}"
            claimed = False
            try:
                self._claim_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=nonce,
                    acquire_if_available=True,
                )
                claimed = True
                started_now = run.status == FactoryRunStatus.PENDING
                if started_now:
                    started_at = self._now()
                    run.status = FactoryRunStatus.RUNNING
                    run.started_at = started_at
                    run.updated_at = started_at
                else:
                    run.updated_at = self._now()
                await self.store.save_run(run)
                if started_now:
                    await self._append_event(
                        run_id,
                        {
                            "type": "started",
                            "message": "Run started",
                            "timestamp": run.updated_at,
                        },
                    )
                    logger.info("Run %s started", run_id)
                await self._release_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=nonce,
                )
                claimed = False
                return run
            except Exception:
                if claimed:
                    await self._rollback_lifecycle_operation(
                        run,
                        operation=operation,
                        nonce=nonce,
                        reason="start_run_failed",
                        persist_run=False,
                    )
                raise

    async def cancel_run(self, run_id: str, reason: str | None = None) -> FactoryRun:
        """Cancel a factory run and keep a distinct terminal status."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status not in TERMINAL_RUN_STATUSES:
                operation = "cancel_run"
                nonce = f"lifecycle_{uuid.uuid4().hex}"
                claimed = False
                try:
                    if run.status != FactoryRunStatus.PENDING:
                        self._claim_lifecycle_operation(
                            run,
                            operation=operation,
                            nonce=nonce,
                            acquire_if_available=False,
                        )
                        claimed = True
                        await self._begin_terminal_drain(
                            run,
                            reason=reason or "factory_run_cancelled",
                            operation_nonce=nonce,
                        )
                    timestamp = self._now()
                    run.status = FactoryRunStatus.CANCELLED
                    run.completed_at = timestamp
                    run.updated_at = timestamp
                    if reason:
                        run.metadata["cancel_reason"] = reason
                    await self.store.save_run(run)
                    await self._append_event(
                        run_id,
                        {
                            "type": "cancelled",
                            "message": reason or "Run cancelled",
                            "reason": reason,
                            "timestamp": timestamp,
                        },
                    )
                    if claimed:
                        await self._release_lifecycle_operation(
                            run,
                            operation=operation,
                            nonce=nonce,
                        )
                        claimed = False
                    logger.info("Run %s cancelled", run_id)
                    _signal_factory_cancel_event(self.workspace, run_id)
                except Exception:
                    if claimed:
                        await self._rollback_lifecycle_operation(
                            run,
                            operation=operation,
                            nonce=nonce,
                            reason="cancel_run_failed",
                        )
                    raise

        run = await self.settle_terminal_run(run_id)
        self._trigger_archive(run_id, "cancelled")
        return run

    async def complete_run(self, run_id: str, success: bool = True) -> FactoryRun:
        """Close the Factory orchestration session without granting verification.

        ``FactoryRun.status`` is an operational lifecycle projection used by
        the HTTP control surface. Verified delivery authority belongs to the
        canonical Run Ledger / QA projection and is intentionally not inferred
        from the caller-provided ``success`` flag.
        """
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            if run.status not in TERMINAL_RUN_STATUSES:
                operation = "complete_run"
                nonce = f"lifecycle_{uuid.uuid4().hex}"
                claimed = False
                try:
                    if run.status != FactoryRunStatus.PENDING:
                        self._claim_lifecycle_operation(
                            run,
                            operation=operation,
                            nonce=nonce,
                            acquire_if_available=False,
                        )
                        claimed = True
                        await self._begin_terminal_drain(
                            run,
                            reason="factory_run_completed" if success else "factory_run_failed",
                            operation_nonce=nonce,
                        )
                    timestamp = self._now()
                    run.status = FactoryRunStatus.COMPLETED if success else FactoryRunStatus.FAILED
                    run.completed_at = timestamp
                    run.updated_at = timestamp
                    run.metadata["completion_authority"] = "orchestration_session_lifecycle"
                    run.metadata["verified"] = False
                    run.metadata["verification_authority"] = "execution_ledger_projection"
                    await self.store.save_run(run)
                    await self._append_event(
                        run_id,
                        {
                            "type": "completed" if success else "failed",
                            "message": "Run completed" if success else "Run failed",
                            "timestamp": timestamp,
                            "success": success,
                            "authoritative": False,
                            "verified": False,
                            "authority_scope": "orchestration_session_lifecycle",
                        },
                    )
                    if claimed:
                        await self._release_lifecycle_operation(
                            run,
                            operation=operation,
                            nonce=nonce,
                        )
                        claimed = False
                    logger.info(
                        "Factory orchestration session %s closed with success=%s",
                        run_id,
                        success,
                    )
                except Exception:
                    if claimed:
                        await self._rollback_lifecycle_operation(
                            run,
                            operation=operation,
                            nonce=nonce,
                            reason="complete_run_failed",
                        )
                    raise

        run = await self.settle_terminal_run(run_id)
        self._trigger_archive(run_id, "completed" if success else "failed")
        return run

    async def settle_terminal_run(
        self,
        run_id: str,
        *,
        expected_fencing_token: int | None = None,
    ) -> FactoryRun:
        """Explicitly settle a terminal run; observation APIs never call this.

        A supplied fencing token is verified by the admission claim while its
        exclusive lock is held.  The legacy run-id-only path keeps its existing
        behavior for direct callers.
        """

        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            if run.status not in TERMINAL_RUN_STATUSES:
                return run
            current = self._admission.current()
            if current is None or current.run_id != run.id:
                if expected_fencing_token is None:
                    return run
            elif current.state.value == "released":
                if expected_fencing_token is None or current.fencing_token == expected_fencing_token:
                    return run
            elif expected_fencing_token is None:
                self._attach_workspace_lease(run, current)

            operation = "settle_terminal_run"
            nonce = f"lifecycle_{uuid.uuid4().hex}"
            claimed = False
            try:
                lease = self._claim_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=nonce,
                    acquire_if_available=False,
                    expected_fencing_token=expected_fencing_token,
                )
                claimed = True
                if lease.state.value == "active":
                    lease = await self._begin_terminal_drain(
                        run,
                        reason=f"terminal_{run.status.value}",
                        operation_nonce=nonce,
                    )
                run = await self._finalize_terminal_drain(
                    run,
                    lease,
                    operation_nonce=nonce,
                )
                current = self._admission.current()
                if (
                    current is not None
                    and current.run_id == run.id
                    and current.lifecycle_operation_claim is not None
                    and current.lifecycle_operation_claim.nonce == nonce
                ):
                    await self._release_lifecycle_operation(
                        run,
                        operation=operation,
                        nonce=nonce,
                    )
                claimed = False
                return run
            except Exception:
                if claimed:
                    await self._rollback_lifecycle_operation(
                        run,
                        operation=operation,
                        nonce=nonce,
                        reason="settle_terminal_run_failed",
                    )
                raise

    async def recover_stale_workspace_owner(
        self,
        run_id: str,
        *,
        expected_fencing_token: int,
        reason: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        """Explicitly fence expired child sessions and release one stale owner."""

        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            stale = self._admission.assert_stale_owner(
                run_id,
                fencing_token=expected_fencing_token,
            )
            fence_result = fence_expired_factory_run_sessions(
                FenceExpiredFactoryRunSessionsCommandV1(
                    workspace=str(self.workspace),
                    factory_run_id=run_id,
                    reason=reason,
                )
            )
            if not fence_result.ok:
                from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

                raise FactoryPipelineError(
                    "Factory stale-owner recovery could not fence child sessions",
                    code="factory_workspace_stale_owner_fence_failed",
                    details=fence_result.to_record(),
                )
            settlement = self._query_child_session_settlement(run_id)
            if settlement.get("settled") is not True:
                from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

                raise FactoryPipelineError(
                    "Factory stale-owner recovery lacks child settlement proof",
                    code="factory_workspace_run_child_session_inflight",
                    details={"settlement": settlement, "fence_result": fence_result.to_record()},
                )

            run = await self.store.get_run(run_id)
            observed_at = self._now()
            if run is not None:
                self._attach_workspace_lease(run, stale)
                run.metadata[_STAGE_IN_FLIGHT_METADATA_KEY] = False
                run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = True
                run.metadata[_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY] = settlement
                run.updated_at = observed_at
                await self.store.save_run(run)
            release_evidence = self._workspace_release_evidence(
                run_id,
                settlement,
                source="factory_stale_owner_recovery",
                observed_at=observed_at,
                fenced_session_ids=fence_result.fenced_session_ids,
                details={"reason": reason, "session_fence": fence_result.to_record()},
            )
            released = self._admission.recover_stale_owner(
                run_id,
                fencing_token=expected_fencing_token,
                settlement_evidence=release_evidence,
                reason=reason,
            )
            if run is not None:
                self._attach_workspace_lease(run, released)
                run.updated_at = self._now()
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "workspace_stale_owner_recovered",
                        "message": "Expired Factory workspace owner was explicitly fenced and released",
                        "lease": released.to_dict(),
                        "settlement": settlement,
                        "timestamp": run.updated_at,
                    },
                )
            return released

    async def list_runs(self) -> list[dict[str, Any]]:
        """List all factory runs with basic info."""
        run_ids = self.store.list_runs()
        runs: list[dict[str, Any]] = []
        for run_id in run_ids:
            run = await self.store.get_run(run_id)
            if run is None:
                continue
            runs.append(
                {
                    "id": run.id,
                    "name": run.config.name,
                    "status": run.status.value,
                    "created_at": run.created_at,
                    "updated_at": run.updated_at,
                    "current_stage": run.metadata.get("current_stage"),
                    "last_successful_stage": run.metadata.get("last_successful_stage"),
                    "stages_completed": len(run.stages_completed),
                    "stages_failed": len(run.stages_failed),
                }
            )
        return runs

    async def get_run(self, run_id: str) -> FactoryRun | None:
        """Return one persisted run without mutation, cleanup, or events."""

        return await self.store.get_run(run_id)

    async def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        """Get all events for a run."""
        return await self.store.get_events(run_id)

    async def _execute_stage_logic(
        self,
        run: FactoryRun,
        stage: str,
        context: dict[str, Any],
    ) -> StageResult:
        if stage not in SUPPORTED_FACTORY_STAGES:
            return StageResult(stage=stage, status="skipped", output="No handler for this stage")
        return await self._executor.execute(stage, run, context)

    async def _find_last_successful_stage(self, run_id: str) -> str | None:
        """Find the last successful stage from events."""
        events = await self.store.get_events(run_id)
        for event in reversed(events):
            if event.get("type") != "stage_completed":
                continue
            result = event.get("result", {})
            if result.get("status") == "success":
                return result.get("stage")
        return None

    async def _mark_stage_started(self, run: FactoryRun, stage: str, started_at: str) -> None:
        run.metadata["current_stage"] = stage
        run.metadata["current_stage_started_at"] = started_at
        run.metadata["last_stage"] = stage
        run.metadata[_STAGE_IN_FLIGHT_METADATA_KEY] = True
        run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = False
        run.updated_at = started_at
        await self.store.save_run(run)
        await self._append_event(
            run.id,
            {
                "type": "stage_started",
                "stage": stage,
                "message": f"Started stage {stage}",
                "timestamp": started_at,
            },
        )

    async def _mark_stage_finished(
        self,
        run: FactoryRun,
        result: StageResult,
        error: Exception | None = None,
    ) -> None:
        completed_at = result.completed_at or self._now()
        result.completed_at = completed_at
        latest_run = await self.store.get_run(run.id)
        target_run = latest_run or run

        lease_payload = run.metadata.get(_WORKSPACE_LEASE_METADATA_KEY)
        if isinstance(lease_payload, Mapping):
            target_run.metadata[_WORKSPACE_LEASE_METADATA_KEY] = dict(lease_payload)
        target_run.metadata["last_stage"] = result.stage
        target_run.metadata["current_stage_completed_at"] = completed_at
        target_run.metadata[_STAGE_IN_FLIGHT_METADATA_KEY] = False
        result_metadata = result.metadata if isinstance(result.metadata, dict) else {}
        inflight_run_continues = result_metadata.get("inflight_run_continues") is True
        child_sessions_settled = result_metadata.get("child_sessions_settled") is True and not inflight_run_continues
        target_run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = child_sessions_settled
        target_run.metadata[_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY] = {
            "schema_version": "factory-stage.child-session-settlement/1",
            "stage": result.stage,
            "child_sessions_settled": child_sessions_settled,
            "inflight_run_continues": inflight_run_continues,
            "source": str(result_metadata.get("settlement_source") or "stage_result.metadata"),
        }
        stage_results = target_run.metadata.get("stage_results")
        if not isinstance(stage_results, dict):
            stage_results = {}
        stage_results[result.stage] = result.to_dict()
        target_run.metadata["stage_results"] = stage_results

        cancelled_externally = (
            target_run.status == FactoryRunStatus.CANCELLED or str(result.status or "").strip().lower() == "cancelled"
        )
        if cancelled_externally:
            result.status = "cancelled"
            if not str(result.output or "").strip():
                reason = str(target_run.metadata.get("cancel_reason") or "Run cancelled").strip()
                result.output = f"Stage {result.stage} cancelled: {reason}"
            target_run.status = FactoryRunStatus.CANCELLED
            target_run.metadata["last_cancelled_stage"] = result.stage
        elif result.status == "success":
            self._append_unique(target_run.stages_completed, result.stage)
            target_run.recovery_point = result.stage
            target_run.metadata["last_successful_stage"] = result.stage
        elif result.status == "failed":
            self._append_unique(target_run.stages_failed, result.stage)
            target_run.status = FactoryRunStatus.FAILED
            target_run.metadata["last_failed_stage"] = result.stage
            target_run.metadata["failure"] = {
                "stage": result.stage,
                "code": "FACTORY_STAGE_FAILED",
                "detail": result.output or str(error or "Stage failed"),
                "recoverable": True,
                "timestamp": completed_at,
            }

        target_run.updated_at = completed_at
        await self.store.save_run(target_run)
        await self._append_event(
            target_run.id,
            {
                "type": "stage_completed",
                "stage": result.stage,
                "message": result.output or f"Completed stage {result.stage}",
                "result": result.to_dict(),
                "timestamp": completed_at,
            },
        )
        await self.store.checkpoint(target_run)

    async def _append_event(self, run_id: str, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("run_id", run_id)
        payload.setdefault("event_id", f"evt_{uuid.uuid4().hex[:12]}")
        payload.setdefault("timestamp", self._now())
        await self.store.append_event(run_id, payload)
        # Best-effort NAT JetStream fanout so the unified WebSocket pipeline
        # (``event.factory:<run_id>`` channel) can stream these events to
        # subscribers. The factory run stays the source of truth (durable on
        # disk); JetStream is the best-effort realtime fanout.
        try:
            from polaris.delivery.http.routers.jetstream_utils import (
                publish_to_jetstream,
            )

            workspace_key = ""
            if self.workspace:
                try:
                    roots = resolve_storage_roots(str(self.workspace))
                    workspace_key = str(getattr(roots, "workspace_key", "") or "").strip()
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.debug("factory workspace key resolution failed for %s: %s", self.workspace, exc)
                    workspace_key = self.workspace.name
            if not workspace_key:
                return
            subject = f"hp.runtime.{workspace_key}.event.factory.{run_id}"
            channel = f"event.factory:{run_id}"
            # Bound the realtime fanout payload to the JetStream 256KB max-message
            # limit and the WS 1MiB frame limit. A large director ``stage_completed``
            # payload (full ``task_results`` reaches ~2.7MB on big projects) is durable
            # on disk via ``store.append_event`` above; the fanout only carries control
            # fields plus a bounded preview. Without this bound the oversized frame is
            # dropped (WS close 1009), the bench loses the socket, times out after the
            # full deadline, and cancels the chain before QA runs (factory_bench L1-08).
            from polaris.delivery.ws.endpoints.json_utils import elide_oversized_frame

            envelope = {
                "schema_version": "runtime.v2",
                "event_id": payload.get("event_id"),
                "workspace_key": workspace_key,
                "run_id": run_id,
                "channel": channel,
                "kind": str(payload.get("type") or payload.get("kind") or "factory.event"),
                "ts": payload.get("timestamp"),
                "cursor": 0,
                "trace_id": None,
                "payload": elide_oversized_frame(payload, _FACTORY_FANOUT_MAX_PAYLOAD_BYTES),
                "meta": {"source": "factory_run_service"},
            }
            await asyncio.wait_for(
                publish_to_jetstream(
                    subject=subject,
                    payload=envelope,
                ),
                timeout=_factory_jetstream_fanout_timeout_seconds(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("factory JetStream fanout failed for run %s: %s", run_id, exc)

    def _trigger_archive(self, run_id: str, reason: str) -> None:
        """Trigger async archiving of factory run to history.

        This is non-blocking - archiving happens in background.
        """
        try:
            from polaris.cells.archive.factory_archive.public.service import trigger_factory_archive

            workspace = str(self.workspace) if hasattr(self, "workspace") else ""
            if workspace:
                trigger_factory_archive(
                    workspace=workspace,
                    factory_run_id=run_id,
                    reason=reason,
                )
                logger.debug("Triggered archive for factory run %s", run_id)
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            # Log error but don't block the main flow
            logger.warning("Failed to trigger archive for factory run %s: %s", run_id, exc)

    @staticmethod
    def _append_unique(target: list[str], value: str) -> None:
        if value and value not in target:
            target.append(value)

    @staticmethod
    def _now() -> str:
        return utc_now_iso()
