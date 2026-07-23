"""Production adapters and lifecycle owner for durable Factory settlement."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final, Protocol, cast

from nats.errors import Error as NatsError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, ReplayPolicy
from nats.js.errors import Error as JetStreamError
from polaris.cells.control_plane.run_ledger.public import (
    FACTORY_SETTLEMENT_BARRIER_SCHEMA_V1,
    AppendRunLedgerEventCommandV1,
    FactorySettlementBarrierResultV1,
    RunLedgerAppendResultV1,
    append_run_ledger_event,
    query_factory_settlement_barrier,
)
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    FactEventAppendedV1,
    FactStreamError,
    FactStreamQueryResultV1,
    QueryFactEventsV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
    query_fact_events,
)
from polaris.cells.runtime.task_runtime.public import (
    DirectedEffectRecoverySweepResultV1,
    ReconcileAmbiguousDirectedEffectsCommandV1,
    reconcile_ambiguous_directed_effects,
)
from polaris.cells.storage.layout.public import (
    ResolveStorageLayoutQueryV1,
    resolve_storage_layout,
)
from polaris.infrastructure.messaging.nats.nats_types import JetStreamConstants

from ..public.contracts import (
    FactoryPipelineError,
    FactoryWorkspaceRunLeaseConflictError,
    FactoryWorkspaceRunLeaseStorageError,
    FactoryWorkspaceRunLeaseV1,
)
from .factory_run_admission import FactoryWorkspaceRunAdmission
from .factory_run_service import FactoryRunService
from .factory_settlement_consumer import (
    FactorySettlementBarrierQuery,
    FactorySettlementBarrierSnapshot,
    FactorySettlementConsumer,
    FactorySettlementConsumerError,
    FactorySettlementFencedError,
    FactorySettlementPermanentError,
    FactorySettlementRecoveryRequiredError,
    FactorySettlementRetryableError,
    SettlementReplayReport,
)
from .factory_settlement_journal import FactorySettlementJournal

logger = logging.getLogger(__name__)

_WAKE_SUBJECT_PREFIX: Final[str] = "hp.runtime"
_WAKE_DURABLE_PREFIX: Final[str] = "factory-settlement"
# Bump only for an intentionally incompatible consumer configuration change;
# otherwise the workspace-derived durable remains stable across process restarts.
_WAKE_DURABLE_VERSION: Final[str] = "v1"
_WAKE_DRAIN_TIMEOUT_SECONDS: Final[float] = 30.0
_WAKE_CONSUMER_SAFETY_FIELDS: Final[tuple[str, ...]] = (
    "durable_name",
    "ack_policy",
    "ack_wait",
    "filter_subject",
    "filter_subjects",
    "max_deliver",
    "max_ack_pending",
    "backoff",
    "deliver_policy",
    "replay_policy",
    "deliver_group",
    "flow_control",
    "headers_only",
)
_WAKE_FALSE_EQUIVALENT_BOOLEAN_FIELDS: Final[frozenset[str]] = frozenset({"flow_control", "headers_only"})
_RECOVERY_REQUIRED_CODES: Final[frozenset[str]] = frozenset({"factory_workspace_run_lease_expired"})
_FENCED_CODES: Final[frozenset[str]] = frozenset(
    {
        "factory_lifecycle_operation_fenced",
        "factory_stage_execution_fenced",
        "factory_workspace_run_fenced",
        "factory_workspace_run_lease_missing",
        "factory_workspace_run_released",
    }
)

FactQueryHandler = Callable[[QueryFactEventsV1], FactStreamQueryResultV1]
FactAppendHandler = Callable[[AppendFactEventCommandV1], FactEventAppendedV1]
BarrierQueryHandler = Callable[[str | Path, str], FactorySettlementBarrierResultV1]
DirectedEffectRecoveryHandler = Callable[
    [ReconcileAmbiguousDirectedEffectsCommandV1],
    DirectedEffectRecoverySweepResultV1,
]
RunLedgerAppendHandler = Callable[[AppendRunLedgerEventCommandV1], RunLedgerAppendResultV1]
LeaseReader = Callable[[], FactoryWorkspaceRunLeaseV1 | None]
WakeCallback = Callable[[], Coroutine[Any, Any, SettlementReplayReport]]


def _canonical_workspace(value: str | Path) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("workspace must be a non-empty path")
    return os.path.normcase(str(Path(normalized).expanduser().resolve(strict=False)))


def _consumer_safety_evidence(config: ConsumerConfig) -> dict[str, object]:
    """Project public nats-py config fields into stable drift evidence."""

    raw = config.as_dict()
    evidence: dict[str, object] = {}
    for field in _WAKE_CONSUMER_SAFETY_FIELDS:
        value = raw.get(field)
        if field in _WAKE_FALSE_EQUIVALENT_BOOLEAN_FIELDS and value is None:
            value = False
        evidence[field] = value.value if isinstance(value, Enum) else value
    return evidence


def _consumer_safety_values_match(
    field: str,
    expected: object,
    actual: object,
) -> bool:
    if field in _WAKE_FALSE_EQUIVALENT_BOOLEAN_FIELDS:
        return type(expected) is bool and type(actual) is bool and expected is actual
    return expected == actual


class FactoryRunServicePort(Protocol):
    """Narrow Factory service surface required by settlement."""

    workspace: Path

    async def settle_terminal_run(
        self,
        run_id: str,
        *,
        expected_fencing_token: int | None = None,
    ) -> object:
        """Settle one terminal run."""

    async def recover_stale_workspace_owner(
        self,
        run_id: str,
        *,
        expected_fencing_token: int,
        reason: str,
    ) -> object:
        """Recover one expired workspace owner."""


FactoryRunServiceFactory = Callable[[str], FactoryRunServicePort]


class SettlementWakeMessage(Protocol):
    """One JetStream delivery whose ACK follows durable FactStream progress."""

    async def ack(self) -> None:
        """Acknowledge delivery after an ACK-safe settlement replay."""


class SettlementWakeSubscription(Protocol):
    """Manual-ACK JetStream subscription used by Factory settlement."""

    async def consumer_info(self) -> SettlementConsumerInfo:
        """Return the server-authoritative configuration of this consumer."""

    async def next_msg(self, timeout: float | None = None) -> SettlementWakeMessage:
        """Wait for one persisted runtime wake."""

    async def unsubscribe(self) -> None:
        """Stop delivery without deleting durable consumer state."""


class SettlementConsumerInfo(Protocol):
    """Public consumer-info projection returned by nats-py subscriptions."""

    config: ConsumerConfig


class SettlementJetStreamContext(Protocol):
    """Minimal JetStream context required to establish a durable consumer."""

    async def subscribe(
        self,
        subject: str,
        *,
        durable: str,
        config: ConsumerConfig,
        manual_ack: bool,
    ) -> SettlementWakeSubscription:
        """Create or bind the durable explicit-ACK subscription."""


class SettlementWakeClient(Protocol):
    """Connected NATS client exposing its JetStream context."""

    @property
    def jetstream(self) -> SettlementJetStreamContext | None:
        """Return the active JetStream context."""


WakeClientFactory = Callable[[], Awaitable[SettlementWakeClient]]


class SettlementAuthoritySink(Protocol):
    """Bind source-fact authority before a Factory service mutation."""

    def bind(self, query: FactorySettlementBarrierQuery) -> int:
        """Bind authority and return the current Factory fencing token."""


class FactStreamPublicServiceAdapter:
    """Adapt the FactStream public service to the settlement core port."""

    def __init__(
        self,
        *,
        query_handler: FactQueryHandler = query_fact_events,
        append_handler: FactAppendHandler = append_fact_event,
    ) -> None:
        self._query_handler = query_handler
        self._append_handler = append_handler

    def query(self, query: QueryFactEventsV1, /) -> FactStreamQueryResultV1:
        return self._query_handler(query)

    def append(self, command: AppendFactEventCommandV1, /) -> FactEventAppendedV1:
        return self._append_handler(command)


@dataclass(frozen=True, slots=True)
class _SettlementAuthority:
    workspace: str
    factory_run_id: str
    workspace_fencing_token: int


class FactoryRunServiceSettlementAdapter:
    """Fence and translate Factory service calls for the settlement consumer."""

    def __init__(
        self,
        *,
        workspace: str,
        service: FactoryRunServicePort,
        lease_reader: LeaseReader,
    ) -> None:
        self._workspace = _canonical_workspace(workspace)
        if _canonical_workspace(service.workspace) != self._workspace:
            raise ValueError("FactoryRunService workspace must match settlement workspace")
        self._service = service
        self._lease_reader = lease_reader
        self._authority: ContextVar[_SettlementAuthority | None] = ContextVar(
            f"factory_settlement_authority_{id(self)}",
            default=None,
        )

    def bind(self, query: FactorySettlementBarrierQuery) -> int:
        """Bind source authority and return current Factory fencing token.

        The source token is intentionally not compared here.  The consumer
        compares it with this current token and records a typed fenced verdict;
        every mutation repeats the comparison through
        :meth:`_assert_current_authority` to close the TOCTOU window.
        """

        workspace = _canonical_workspace(query.workspace)
        if workspace != self._workspace:
            raise FactorySettlementFencedError(
                "Settlement barrier authority belongs to another workspace",
                code="factory_settlement_workspace_fenced",
            )
        lease = self._lease_reader()
        if lease is None:
            raise FactorySettlementFencedError(
                "Factory settlement workspace lease does not exist",
                code="factory_workspace_run_lease_missing",
            )
        if _canonical_workspace(lease.workspace) != workspace or lease.run_id != query.factory_run_id:
            raise FactorySettlementFencedError(
                "Factory settlement source authority belongs to another run",
                code="factory_workspace_run_fenced",
            )
        self._authority.set(
            _SettlementAuthority(
                workspace=workspace,
                factory_run_id=query.factory_run_id,
                workspace_fencing_token=query.workspace_fencing_token,
            )
        )
        return lease.fencing_token

    async def settle_terminal_run(self, run_id: str) -> object:
        authority = self._assert_current_authority(run_id)
        try:
            return await self._service.settle_terminal_run(
                run_id,
                expected_fencing_token=authority.workspace_fencing_token,
            )
        except (
            FactoryPipelineError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            raise _translate_factory_service_error(exc) from exc

    async def recover_stale_workspace_owner(
        self,
        run_id: str,
        *,
        expected_fencing_token: int,
        reason: str,
    ) -> object:
        authority = self._assert_current_authority(run_id)
        if authority.workspace_fencing_token != expected_fencing_token:
            raise FactorySettlementFencedError(
                "Stale-owner recovery supplied a different fencing token",
                code="factory_settlement_recovery_token_fenced",
            )
        try:
            return await self._service.recover_stale_workspace_owner(
                run_id,
                expected_fencing_token=expected_fencing_token,
                reason=reason,
            )
        except (
            FactoryPipelineError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            raise _translate_factory_service_error(exc) from exc

    def _assert_current_authority(self, run_id: str) -> _SettlementAuthority:
        normalized_run_id = str(run_id or "").strip()
        authority = self._authority.get()
        if authority is None or authority.factory_run_id != normalized_run_id:
            raise FactorySettlementFencedError(
                "Factory settlement service call lacks matching source-fact authority",
                code="factory_settlement_authority_missing",
            )
        lease = self._lease_reader()
        if lease is None:
            raise FactorySettlementFencedError(
                "Factory settlement workspace lease does not exist",
                code="factory_workspace_run_lease_missing",
            )
        if (
            _canonical_workspace(lease.workspace) != authority.workspace
            or lease.run_id != authority.factory_run_id
            or lease.fencing_token != authority.workspace_fencing_token
        ):
            raise FactorySettlementFencedError(
                "Factory settlement source authority has been fenced",
                code="factory_workspace_run_fenced",
            )
        return authority


def _translate_factory_service_error(
    exc: FactoryPipelineError | OSError | RuntimeError | TypeError | ValueError,
) -> FactorySettlementRetryableError | FactorySettlementPermanentError:
    """Translate Factory service failures without masking their machine code."""

    if isinstance(exc, FactoryWorkspaceRunLeaseStorageError):
        return FactorySettlementRetryableError(str(exc), code=exc.code)
    if isinstance(exc, FactoryWorkspaceRunLeaseConflictError):
        if exc.code in _RECOVERY_REQUIRED_CODES:
            return FactorySettlementRecoveryRequiredError(str(exc), code=exc.code)
        if exc.code in _FENCED_CODES:
            return FactorySettlementFencedError(str(exc), code=exc.code)
        return FactorySettlementRetryableError(str(exc), code=exc.code)
    if isinstance(exc, FactoryPipelineError):
        return FactorySettlementRetryableError(str(exc), code=exc.code)
    if isinstance(exc, (TypeError, ValueError)):
        return FactorySettlementPermanentError(
            str(exc),
            code="factory_settlement_invalid_service_request",
        )
    return FactorySettlementRetryableError(
        str(exc),
        code="factory_settlement_service_unavailable",
    )


class RunLedgerFactorySettlementBarrierAdapter:
    """Map the Run Ledger public no-wait query to the settlement core port."""

    def __init__(
        self,
        *,
        authority_sink: SettlementAuthoritySink,
        query_handler: BarrierQueryHandler = query_factory_settlement_barrier,
    ) -> None:
        self._authority_sink = authority_sink
        self._query_handler = query_handler

    def query(self, query: FactorySettlementBarrierQuery, /) -> FactorySettlementBarrierSnapshot:
        current_fencing_token = self._authority_sink.bind(query)
        result = self._query_handler(query.workspace, query.factory_run_id)
        if result.schema_version != FACTORY_SETTLEMENT_BARRIER_SCHEMA_V1:
            raise FactorySettlementRetryableError(
                "Run Ledger returned an unsupported Factory settlement barrier schema",
                code="unsupported_factory_settlement_barrier_schema",
            )
        if result.release_allowed != result.closed:
            raise FactorySettlementRetryableError(
                "Run Ledger settlement barrier violates release invariants",
                code="invalid_factory_settlement_barrier",
            )
        source_fact_visible = bool(query.source_run_id and query.source_run_id in set(result.consumed_run_ids))
        evidence = {
            "schema_version": result.schema_version,
            "passed": result.passed,
            "missing_required_modalities": result.missing_required_modalities,
            "failed_required_modalities": result.failed_required_modalities,
            "task_lifecycle_count": result.task_lifecycle_count,
            "tool_lifecycle_count": result.tool_lifecycle_count,
            "active_lifecycle_count": result.active_lifecycle_count,
            "open_lifecycle_count": result.open_lifecycle_count,
            "failed_lifecycle_count": result.failed_lifecycle_count,
            "expected_effect_count": result.expected_effect_count,
            "effect_receipt_count": result.effect_receipt_count,
            "open_effect_count": result.open_effect_count,
            "evidence_refs": result.evidence_refs,
            "consumed_run_ids": result.consumed_run_ids,
        }
        return FactorySettlementBarrierSnapshot(
            workspace=result.workspace,
            factory_run_id=result.factory_run_id,
            source_fact_visible=source_fact_visible,
            closed=result.closed,
            release_allowed=result.release_allowed,
            workspace_fencing_token=current_fencing_token,
            barrier_hash=result.barrier_hash,
            blocking_reasons=result.blocking_reasons,
            evidence=evidence,
        )


class DurableJetStreamSettlementWakeBridge:
    """Durable explicit-ACK wake bridge for Factory settlement.

    JetStream delivery is notification only.  Every message triggers a finite
    re-read of canonical FactStream state; transport ACK occurs only after that
    replay persisted an ACK-safe checkpoint.  Open barriers remain unacked and
    later TaskRuntime or Run Ledger events trigger another replay.

    Complexity:
        O(1) retained delivery state.  Each wake delegates FactStream traversal
        to ``FactorySettlementConsumer`` and therefore inherits its bounded-page
        O(n) replay cost for n unseen source facts.
    """

    delivery_mode: Final[str] = "jetstream_durable_explicit_ack"
    durable_ack_supported: Final[bool] = True

    def __init__(
        self,
        *,
        client: SettlementWakeClient,
        subject: str,
        durable_name: str,
        wake: WakeCallback,
        drain_timeout_seconds: float = _WAKE_DRAIN_TIMEOUT_SECONDS,
    ) -> None:
        normalized_subject = str(subject or "").strip()
        normalized_durable_name = str(durable_name or "").strip()
        if not normalized_subject or not normalized_durable_name:
            raise ValueError("wake bridge subject and durable_name must be non-empty")
        if drain_timeout_seconds <= 0:
            raise ValueError("drain_timeout_seconds must be > 0")
        self._client = client
        self._subject = normalized_subject
        self._durable_name = normalized_durable_name
        self._wake = wake
        self._drain_timeout_seconds = float(drain_timeout_seconds)
        self._subscription: SettlementWakeSubscription | None = None
        self._task: asyncio.Task[None] | None = None
        self._delivery_lock = asyncio.Lock()
        self._active_replay: asyncio.Task[SettlementReplayReport] | None = None
        self._stopping = False
        self._last_report: SettlementReplayReport | None = None
        self._failure: FactorySettlementWakeBridgeError | None = None

    @property
    def subject(self) -> str:
        return self._subject

    @property
    def durable_name(self) -> str:
        return self._durable_name

    @property
    def last_report(self) -> SettlementReplayReport | None:
        return self._last_report

    @property
    def failure(self) -> FactorySettlementWakeBridgeError | None:
        """Return the terminal transport failure, if delivery has stopped."""

        return self._failure

    @property
    def is_healthy(self) -> bool:
        """Return whether the subscribed delivery task remains live."""

        task = self._task
        return not self._stopping and self._failure is None and task is not None and not task.done()

    async def start(self) -> bool:
        task = self._task
        if task is not None and not task.done():
            return False
        jetstream = self._client.jetstream
        if jetstream is None:
            raise RuntimeError("JetStream is unavailable for Factory settlement")
        consumer_config = self._consumer_config()
        try:
            subscription = await jetstream.subscribe(
                self._subject,
                durable=self._durable_name,
                config=consumer_config,
                manual_ack=True,
            )
        except (NatsError, JetStreamError, OSError, RuntimeError, TypeError, ValueError) as exc:
            failure = FactorySettlementWakeBridgeError(
                "Factory settlement JetStream durable subscription failed",
                code="factory_settlement_wake_subscription_failed",
                details={
                    "subject": self._subject,
                    "durable_name": self._durable_name,
                    "error": str(exc),
                },
            )
            self._failure = failure
            raise failure from exc

        try:
            consumer_info = await subscription.consumer_info()
        except (NatsError, JetStreamError, OSError, RuntimeError, TypeError, ValueError) as exc:
            failure = FactorySettlementWakeBridgeError(
                "Factory settlement JetStream consumer configuration could not be read",
                code="factory_settlement_wake_consumer_info_failed",
                details={
                    "subject": self._subject,
                    "durable_name": self._durable_name,
                    "error": str(exc),
                },
            )
            await self._abort_start(subscription, failure)
            raise failure from exc

        try:
            self._assert_consumer_config_matches(
                expected=consumer_config,
                actual=consumer_info.config,
            )
        except FactorySettlementWakeBridgeError as failure:
            await self._abort_start(subscription, failure)
            raise

        self._subscription = subscription
        self._stopping = False
        self._failure = None
        task = asyncio.create_task(
            self._consume_wakes(),
            name=f"factory-settlement-wake:{self._durable_name}",
        )
        task.add_done_callback(self._observe_completion)
        self._task = task
        return True

    def _consumer_config(self) -> ConsumerConfig:
        """Build the versioned durable's complete server-side safety contract."""

        return ConsumerConfig(
            durable_name=self._durable_name,
            deliver_policy=DeliverPolicy.NEW,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=JetStreamConstants.CONSUMER_ACK_WAIT_SECONDS,
            max_deliver=-1,
            max_ack_pending=JetStreamConstants.CONSUMER_MAX_ACK_PENDING,
            filter_subject=self._subject,
            replay_policy=ReplayPolicy.INSTANT,
            flow_control=False,
            headers_only=False,
        )

    def _assert_consumer_config_matches(
        self,
        *,
        expected: ConsumerConfig,
        actual: ConsumerConfig,
    ) -> None:
        expected_evidence = _consumer_safety_evidence(expected)
        actual_evidence = _consumer_safety_evidence(actual)
        mismatches = {
            field: {
                "expected": expected_evidence[field],
                "actual": actual_evidence[field],
            }
            for field in _WAKE_CONSUMER_SAFETY_FIELDS
            if not _consumer_safety_values_match(
                field,
                expected_evidence[field],
                actual_evidence[field],
            )
        }
        if mismatches:
            raise FactorySettlementWakeBridgeError(
                "Factory settlement JetStream durable consumer configuration drifted",
                code="factory_settlement_wake_consumer_config_drift",
                details={
                    "subject": self._subject,
                    "durable_name": self._durable_name,
                    "expected": expected_evidence,
                    "actual": actual_evidence,
                    "mismatches": mismatches,
                },
            )

    async def _abort_start(
        self,
        subscription: SettlementWakeSubscription,
        failure: FactorySettlementWakeBridgeError,
    ) -> None:
        """Release the local binding after a failed verification, never the durable."""

        self._subscription = None
        self._failure = failure
        try:
            await subscription.unsubscribe()
        except (NatsError, JetStreamError, OSError, RuntimeError, ValueError) as exc:
            failure.details["unsubscribe_error"] = str(exc)
            logger.error(
                "Factory settlement JetStream verification cleanup failed for %s: %s",
                self._subject,
                exc,
                exc_info=True,
            )

    async def stop(self) -> bool:
        task = self._task
        subscription = self._subscription
        if task is None and subscription is None:
            return False
        self._stopping = True

        drain_error: TimeoutError | None = None
        try:
            async with asyncio.timeout(self._drain_timeout_seconds):
                async with self._delivery_lock:
                    pass
        except TimeoutError as exc:
            drain_error = exc

        unsubscribe_error: OSError | RuntimeError | ValueError | NatsError | JetStreamError | None = None
        if subscription is not None:
            try:
                await subscription.unsubscribe()
            except (NatsError, JetStreamError, OSError, RuntimeError, ValueError) as exc:
                unsubscribe_error = exc
                logger.error(
                    "Factory settlement JetStream unsubscribe failed for %s: %s",
                    self._subject,
                    exc,
                    exc_info=True,
                )
        self._subscription = None

        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except FactorySettlementWakeBridgeError as exc:
                logger.error(
                    "Factory settlement wake bridge was already terminal during stop for %s: %s",
                    self._subject,
                    exc,
                )
        self._task = None
        self._active_replay = None
        if drain_error is not None:
            raise RuntimeError("Factory settlement wake drain timed out with an active replay") from drain_error
        if unsubscribe_error is not None:
            raise RuntimeError("Factory settlement wake subscription could not unsubscribe") from unsubscribe_error
        return True

    async def _consume_wakes(self) -> None:
        subscription = self._subscription
        if subscription is None:
            raise RuntimeError("Factory settlement wake subscription is not ready")
        while not self._stopping:
            try:
                message = await subscription.next_msg(timeout=None)
            except asyncio.CancelledError:
                raise
            except (NatsError, JetStreamError, OSError, RuntimeError, ValueError) as exc:
                if not self._stopping:
                    failure = FactorySettlementWakeBridgeError(
                        "Factory settlement JetStream delivery failed",
                        code="factory_settlement_wake_delivery_failed",
                        details={"subject": self._subject, "error": str(exc)},
                    )
                    self._failure = failure
                    raise failure from exc
                return
            async with self._delivery_lock:
                if self._stopping:
                    return
                replay = asyncio.create_task(
                    self._wake(),
                    name=f"factory-settlement-replay:{self._durable_name}",
                )
                self._active_replay = replay
                try:
                    report = await replay
                except (FactorySettlementConsumerError, FactStreamError) as exc:
                    logger.error(
                        "Factory settlement wake replay failed for %s: %s",
                        self._subject,
                        exc,
                        exc_info=True,
                    )
                    continue
                finally:
                    self._active_replay = None
                self._last_report = report
                if report.ack_safe:
                    try:
                        await message.ack()
                    except (NatsError, JetStreamError, OSError, RuntimeError, ValueError) as exc:
                        failure = FactorySettlementWakeBridgeError(
                            "Factory settlement JetStream ACK failed",
                            code="factory_settlement_wake_ack_failed",
                            details={"subject": self._subject, "error": str(exc)},
                        )
                        self._failure = failure
                        raise failure from exc
                logger.debug(
                    "Factory settlement wake replay complete subject=%s decisions=%d ack_safe=%s durable_ack=%s",
                    self._subject,
                    len(report.decisions),
                    report.ack_safe,
                    report.ack_safe,
                )

    def _observe_completion(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            if self._failure is None:
                self._failure = FactorySettlementWakeBridgeError(
                    "Factory settlement wake bridge stopped unexpectedly",
                    code="factory_settlement_wake_bridge_stopped",
                    details={"subject": self._subject, "error": str(error)},
                )
            logger.error(
                "Factory settlement wake bridge stopped unexpectedly for %s: %s",
                self._subject,
                error,
                exc_info=(type(error), error, error.__traceback__),
            )


class FactorySettlementRuntime:
    """Own one workspace consumer and its optional wake subscription."""

    def __init__(
        self,
        *,
        consumer: FactorySettlementConsumer,
        wake_bridge: DurableJetStreamSettlementWakeBridge | None,
    ) -> None:
        self._consumer = consumer
        self._wake_bridge = wake_bridge
        self._lifecycle_lock = asyncio.Lock()
        self._running = False

    @property
    def workspace(self) -> str:
        return self._consumer.workspace

    @property
    def wake_bridge(self) -> DurableJetStreamSettlementWakeBridge | None:
        return self._wake_bridge

    @property
    def is_running(self) -> bool:
        """Return false when the required durable transport has failed."""

        bridge = self._wake_bridge
        return self._running and (bridge is None or bridge.is_healthy)

    async def start(self) -> SettlementReplayReport:
        """Start replay and close the replay-to-subscription race window."""

        async with self._lifecycle_lock:
            if self._running:
                self._assert_wake_bridge_healthy()
                return SettlementReplayReport(decisions=(), already_started=True)
            report = await self._consumer.start()
            bridge = self._wake_bridge
            if bridge is not None:
                try:
                    await bridge.start()
                    catch_up = await self._consumer.wake()
                    self._assert_wake_bridge_healthy()
                except (NatsError, JetStreamError, OSError, RuntimeError, ValueError) as exc:
                    await bridge.stop()
                    await self._consumer.stop()
                    details: dict[str, Any] = {
                        "workspace": self.workspace,
                        "error": str(exc),
                    }
                    if isinstance(exc, FactorySettlementWakeBridgeError):
                        details["wake_bridge_code"] = exc.code
                        details["wake_bridge_evidence"] = dict(exc.details)
                    raise FactorySettlementRuntimeError(
                        "Factory settlement wake bridge could not start",
                        code="factory_settlement_wake_bridge_start_failed",
                        details=details,
                    ) from exc
                report = SettlementReplayReport(
                    decisions=(*report.decisions, *catch_up.decisions),
                    started_now=report.started_now,
                    already_started=report.already_started,
                )
            self._running = True
            return report

    async def wake(self) -> SettlementReplayReport:
        """Replay unseen FactStream state; never trust the wake payload."""

        self._assert_wake_bridge_healthy()
        return await self._consumer.wake()

    def _assert_wake_bridge_healthy(self) -> None:
        bridge = self._wake_bridge
        if bridge is None or bridge.is_healthy:
            return
        failure = bridge.failure
        raise FactorySettlementRuntimeError(
            "Factory settlement wake bridge is not healthy",
            code="factory_settlement_wake_bridge_unhealthy",
            details={
                "workspace": self.workspace,
                "error": str(failure) if failure is not None else "bridge is not running",
            },
        )

    async def stop(self) -> None:
        """Stop delivery before stopping the durable consumer."""

        async with self._lifecycle_lock:
            if not self._running:
                await self._consumer.stop()
                return
            bridge_error: OSError | RuntimeError | ValueError | NatsError | JetStreamError | None = None
            bridge = self._wake_bridge
            if bridge is not None:
                try:
                    await bridge.stop()
                except (NatsError, JetStreamError, OSError, RuntimeError, ValueError) as exc:
                    bridge_error = exc
            await self._consumer.stop()
            self._running = False
            if bridge_error is not None:
                raise FactorySettlementRuntimeError(
                    "Factory settlement wake bridge could not stop cleanly",
                    code="factory_settlement_wake_bridge_stop_failed",
                    details={"workspace": self.workspace, "error": str(bridge_error)},
                ) from bridge_error


class FactorySettlementRuntimeError(RuntimeError):
    """Typed production-runtime lifecycle failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_message = str(message or "").strip()
        normalized_code = str(code or "").strip()
        if not normalized_message or not normalized_code:
            raise ValueError("runtime errors require non-empty message and code")
        super().__init__(normalized_message)
        self.code = normalized_code
        self.details = dict(details or {})


class FactorySettlementWakeBridgeError(RuntimeError):
    """Terminal JetStream wake-transport failure with structured evidence."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_message = str(message or "").strip()
        normalized_code = str(code or "").strip()
        if not normalized_message or not normalized_code:
            raise ValueError("wake bridge errors require non-empty message and code")
        super().__init__(normalized_message)
        self.code = normalized_code
        self.details = dict(details or {})


RuntimeFactory = Callable[[str], Awaitable[FactorySettlementRuntime]]


class FactorySettlementRuntimeRegistry:
    """Process-local workspace index for one durable consumer per backend."""

    def __init__(self) -> None:
        self._runtimes: dict[str, FactorySettlementRuntime] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        workspace: str,
        *,
        runtime_factory: RuntimeFactory,
    ) -> SettlementReplayReport:
        key = _canonical_workspace(workspace)
        async with self._lock:
            current = self._runtimes.get(key)
            if current is not None:
                return await current.start()
            runtime = await runtime_factory(key)
            if _canonical_workspace(runtime.workspace) != key:
                raise FactorySettlementRuntimeError(
                    "Factory settlement runtime factory returned another workspace",
                    code="factory_settlement_runtime_workspace_mismatch",
                    details={"requested_workspace": key, "runtime_workspace": runtime.workspace},
                )
            report = await runtime.start()
            self._runtimes[key] = runtime
            return report

    async def wake(self, workspace: str) -> SettlementReplayReport:
        key = _canonical_workspace(workspace)
        async with self._lock:
            runtime = self._runtimes.get(key)
        if runtime is None:
            raise FactorySettlementRuntimeError(
                "Factory settlement runtime is not started for this workspace",
                code="factory_settlement_runtime_not_started",
                details={"workspace": key},
            )
        return await runtime.wake()

    async def stop(self, workspace: str) -> bool:
        key = _canonical_workspace(workspace)
        async with self._lock:
            runtime = self._runtimes.get(key)
            if runtime is None:
                return False
            await runtime.stop()
            del self._runtimes[key]
            return True

    async def stop_all(self) -> int:
        async with self._lock:
            runtimes = tuple(self._runtimes.values())
            for runtime in reversed(runtimes):
                await runtime.stop()
            self._runtimes.clear()
            return len(runtimes)


def _create_factory_run_service(workspace: str) -> FactoryRunServicePort:
    return FactoryRunService(workspace=Path(workspace))


async def _get_default_wake_client() -> SettlementWakeClient:
    from polaris.infrastructure.messaging import get_default_client

    return cast(SettlementWakeClient, await get_default_client())


def _wake_binding(workspace: str) -> tuple[str, str]:
    layout = resolve_storage_layout(ResolveStorageLayoutQueryV1(workspace=workspace))
    extras = layout.extras if isinstance(layout.extras, Mapping) else {}
    workspace_key = str(extras.get("workspace_key") or "").strip()
    if not workspace_key:
        raise FactorySettlementRuntimeError(
            "Storage layout did not provide a workspace key",
            code="factory_settlement_workspace_key_missing",
            details={"workspace": workspace},
        )
    return (
        f"{_WAKE_SUBJECT_PREFIX}.{workspace_key}.>",
        f"{_WAKE_DURABLE_PREFIX}-{_WAKE_DURABLE_VERSION}-{workspace_key}",
    )


async def create_factory_settlement_runtime(
    workspace: str,
    *,
    enable_wake_bridge: bool = True,
    wake_bridge_required: bool = True,
    wake_client: SettlementWakeClient | None = None,
    wake_client_factory: WakeClientFactory = _get_default_wake_client,
    factory_service_factory: FactoryRunServiceFactory = _create_factory_run_service,
    fact_query_handler: FactQueryHandler = query_fact_events,
    fact_append_handler: FactAppendHandler = append_fact_event,
    barrier_query_handler: BarrierQueryHandler = query_factory_settlement_barrier,
    directed_effect_recovery_handler: DirectedEffectRecoveryHandler = reconcile_ambiguous_directed_effects,
    run_ledger_append_handler: RunLedgerAppendHandler = append_run_ledger_event,
) -> FactorySettlementRuntime:
    """Assemble production settlement ports for one canonical workspace."""

    canonical_workspace = _canonical_workspace(workspace)
    if wake_bridge_required and not enable_wake_bridge:
        raise ValueError("wake_bridge_required requires enable_wake_bridge")

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=canonical_workspace,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_settlement_runtime_startup",
        )
    )

    recovery = directed_effect_recovery_handler(
        ReconcileAmbiguousDirectedEffectsCommandV1(
            workspace=canonical_workspace,
            reason="factory settlement startup recovery",
        )
    )
    if not recovery.ok:
        raise FactorySettlementRuntimeError(
            "Factory settlement directed-effect recovery failed",
            code="factory_settlement_directed_effect_recovery_failed",
            details={
                "workspace": canonical_workspace,
                "scanned_session_count": recovery.scanned_session_count,
                "failures": tuple(dict(item) for item in recovery.failures),
            },
        )
    for item in recovery.items:
        if not item.factory_run_id:
            raise FactorySettlementRuntimeError(
                "Factory settlement recovery fact is missing Factory authority",
                code="factory_settlement_directed_effect_recovery_projection_authority_missing",
                details={
                    "workspace": canonical_workspace,
                    "task_id": item.task_id,
                    "session_id": item.session_id,
                    "operation_id": item.operation_id,
                    "event_id": item.event_id,
                },
            )
        try:
            run_ledger_append_handler(
                AppendRunLedgerEventCommandV1(
                    workspace=canonical_workspace,
                    run_id=item.factory_run_id,
                    event={
                        "schema_version": "factory.directed_effect_recovery_projection.v1",
                        "event_type": "directed_effect_recovery",
                        "stage": "director_mutation",
                        "run_id": item.factory_run_id,
                        "task_id": str(item.task_id),
                        "operation_id": item.operation_id,
                        "ok": False,
                        "physical_evidence": {"effect_recovery": item.to_record()},
                    },
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise FactorySettlementRuntimeError(
                "Factory settlement recovery projection failed",
                code="factory_settlement_directed_effect_recovery_projection_failed",
                details={
                    "workspace": canonical_workspace,
                    "factory_run_id": item.factory_run_id,
                    "task_id": item.task_id,
                    "operation_id": item.operation_id,
                    "event_id": item.event_id,
                    "error": str(exc),
                },
            ) from exc

    fact_stream = FactStreamPublicServiceAdapter(
        query_handler=fact_query_handler,
        append_handler=fact_append_handler,
    )
    journal = FactorySettlementJournal(
        workspace=canonical_workspace,
        fact_stream=fact_stream,
    )
    service = factory_service_factory(canonical_workspace)
    admission = FactoryWorkspaceRunAdmission(canonical_workspace)
    factory_runs = FactoryRunServiceSettlementAdapter(
        workspace=canonical_workspace,
        service=service,
        lease_reader=admission.current,
    )
    barrier = RunLedgerFactorySettlementBarrierAdapter(
        authority_sink=factory_runs,
        query_handler=barrier_query_handler,
    )
    consumer = FactorySettlementConsumer(
        workspace=canonical_workspace,
        fact_stream=fact_stream,
        journal=journal,
        barrier=barrier,
        factory_runs=factory_runs,
    )

    bridge: DurableJetStreamSettlementWakeBridge | None = None
    if enable_wake_bridge:
        try:
            client = wake_client if wake_client is not None else await wake_client_factory()
            subject, durable_name = _wake_binding(canonical_workspace)
            bridge = DurableJetStreamSettlementWakeBridge(
                client=client,
                subject=subject,
                durable_name=durable_name,
                wake=consumer.wake,
            )
        except (NatsError, JetStreamError, OSError, RuntimeError, ValueError) as exc:
            if wake_bridge_required:
                raise FactorySettlementRuntimeError(
                    "Factory settlement wake bridge is unavailable",
                    code="factory_settlement_wake_bridge_unavailable",
                    details={"workspace": canonical_workspace, "error": str(exc)},
                ) from exc
            logger.warning(
                "Factory settlement starts in replay-only mode for workspace=%s: %s",
                canonical_workspace,
                exc,
            )

    return FactorySettlementRuntime(consumer=consumer, wake_bridge=bridge)


_RUNTIME_REGISTRY = FactorySettlementRuntimeRegistry()


async def start_factory_settlement_runtime(
    workspace: str,
    *,
    enable_wake_bridge: bool = True,
    wake_bridge_required: bool = True,
    runtime_factory: RuntimeFactory | None = None,
) -> SettlementReplayReport:
    """Start one workspace singleton and run startup replay."""

    canonical_workspace = _canonical_workspace(workspace)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=canonical_workspace,
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_settlement_runtime_registry_startup",
        )
    )

    async def configured_factory(canonical_workspace: str) -> FactorySettlementRuntime:
        if runtime_factory is not None:
            return await runtime_factory(canonical_workspace)
        return await create_factory_settlement_runtime(
            canonical_workspace,
            enable_wake_bridge=enable_wake_bridge,
            wake_bridge_required=wake_bridge_required,
        )

    return await _RUNTIME_REGISTRY.start(
        canonical_workspace,
        runtime_factory=configured_factory,
    )


async def wake_factory_settlement_runtime(workspace: str) -> SettlementReplayReport:
    """Wake the workspace singleton by replaying durable FactStream state."""

    return await _RUNTIME_REGISTRY.wake(workspace)


async def stop_factory_settlement_runtime(workspace: str) -> bool:
    """Stop and remove one workspace singleton."""

    return await _RUNTIME_REGISTRY.stop(workspace)


async def stop_all_factory_settlement_runtimes() -> int:
    """Stop every registered runtime, primarily for process shutdown."""

    return await _RUNTIME_REGISTRY.stop_all()


__all__ = [
    "DurableJetStreamSettlementWakeBridge",
    "FactStreamPublicServiceAdapter",
    "FactoryRunServiceSettlementAdapter",
    "FactorySettlementRuntime",
    "FactorySettlementRuntimeError",
    "FactorySettlementRuntimeRegistry",
    "FactorySettlementWakeBridgeError",
    "RunLedgerFactorySettlementBarrierAdapter",
    "SettlementAuthoritySink",
    "SettlementWakeClient",
    "create_factory_settlement_runtime",
    "start_factory_settlement_runtime",
    "stop_all_factory_settlement_runtimes",
    "stop_factory_settlement_runtime",
    "wake_factory_settlement_runtime",
]
