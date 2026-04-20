"""Durable consumer loop manager for background CE/Director/QA daemon threads."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _Consumer(Protocol):
    """Minimal protocol for consumers managed by ConsumerLoopManager."""

    def run(self) -> None: ...
    def stop(self) -> None: ...


class _Service(Protocol):
    """Minimal protocol for the outbox relay target."""

    def relay_outbox_messages(self, workspace: str, *, limit: int = 200) -> dict[str, Any]: ...


def _read_positive_int_env(name: str, *, default: int, minimum: int = 1, maximum: int = 3600) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def _read_float_env(name: str, *, default: float, minimum: float = 0.01, maximum: float = 600.0) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


class ConsumerLoopManager:
    """Manages durable consumer daemon threads for a single workspace.

    Spawns one daemon thread per consumer role (CE, Director, QA) plus an
    outbox relay thread.  Each thread runs until the shared ``_stop_event``
    is set.  Exceptions in one consumer do not crash the others.

    Thread naming convention:
        ``task-market-{role}-consumer-{workspace_hash[:8]}``
    """

    def __init__(
        self,
        workspace: str,
        *,
        poll_interval: float | None = None,
        design_visibility_timeout: int | None = None,
        exec_visibility_timeout: int | None = None,
        qa_visibility_timeout: int | None = None,
        enable_safe_parallel: bool = False,
        outbox_relay_interval: float | None = None,
    ) -> None:
        ws_token = str(workspace or "").strip()
        if not ws_token:
            raise ValueError("workspace must be a non-empty string")
        self._workspace = ws_token
        self._poll_interval = (
            poll_interval
            if poll_interval is not None
            else _read_float_env(
                "POLARIS_TASK_MARKET_DURABLE_POLL_INTERVAL",
                default=5.0,
                minimum=0.05,
                maximum=300.0,
            )
        )
        self._design_timeout = (
            design_visibility_timeout
            if design_visibility_timeout is not None
            else _read_positive_int_env(
                "POLARIS_TASK_MARKET_DESIGN_VISIBILITY_TIMEOUT_SECONDS",
                default=900,
                minimum=30,
                maximum=7200,
            )
        )
        self._exec_timeout = (
            exec_visibility_timeout
            if exec_visibility_timeout is not None
            else _read_positive_int_env(
                "POLARIS_TASK_MARKET_EXEC_VISIBILITY_TIMEOUT_SECONDS",
                default=1800,
                minimum=30,
                maximum=7200,
            )
        )
        self._qa_timeout = (
            qa_visibility_timeout
            if qa_visibility_timeout is not None
            else _read_positive_int_env(
                "POLARIS_TASK_MARKET_QA_VISIBILITY_TIMEOUT_SECONDS",
                default=900,
                minimum=30,
                maximum=7200,
            )
        )
        self._enable_safe_parallel = enable_safe_parallel
        self._outbox_relay_interval = (
            outbox_relay_interval
            if outbox_relay_interval is not None
            else _read_float_env(
                "POLARIS_TASK_MARKET_OUTBOX_RELAY_INTERVAL",
                default=2.0,
                minimum=0.05,
                maximum=60.0,
            )
        )
        self._stop_event = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self._consumers: dict[str, Any] = {}
        self._outbox_relay_thread: threading.Thread | None = None
        self._started = False
        self._ws_hash = hashlib.sha256(ws_token.encode("utf-8")).hexdigest()[:8]

    @property
    def workspace(self) -> str:
        return self._workspace

    def start(
        self,
        *,
        consumer_types: dict[str, type] | None = None,
        service: _Service | None = None,
    ) -> bool:
        """Start CE, Director, QA consumer threads and the outbox relay.

        Args:
            consumer_types: Mapping of role name to consumer class.  If
                *None*, the default CE/Director/QA consumers are lazily
                imported.
            service: Service used for outbox relay.  If *None*, the default
                ``get_task_market_service()`` singleton is used.

        Returns:
            ``True`` if threads were started, ``False`` if already running.
        """
        if self._started:
            return False

        # Resolve consumer types (lazy import to avoid circular deps).
        if consumer_types is None:
            consumer_types = self._default_consumer_types()

        if service is None:
            from polaris.cells.runtime.task_market.internal.service import get_task_market_service

            service = get_task_market_service()

        self._stop_event.clear()

        # Build consumer instances.
        consumer_configs: dict[str, dict[str, Any]] = {
            "chief_engineer": {
                "visibility_timeout_seconds": self._design_timeout,
            },
            "director": {
                "visibility_timeout_seconds": self._exec_timeout,
                "enable_safe_parallel": self._enable_safe_parallel,
            },
            "qa": {
                "visibility_timeout_seconds": self._qa_timeout,
            },
        }

        for role, cfg in consumer_configs.items():
            consumer_cls = consumer_types.get(role)
            if consumer_cls is None:
                logger.warning("ConsumerLoopManager: no consumer class for role=%s, skipping", role)
                continue
            worker_id = f"durable_{role}_{self._ws_hash}"
            try:
                consumer = consumer_cls(
                    workspace=self._workspace,
                    worker_id=worker_id,
                    poll_interval=self._poll_interval,
                    **cfg,
                )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.exception("ConsumerLoopManager: failed to init consumer for role=%s: %s", role, exc)
                continue
            self._consumers[role] = consumer
            thread_name = f"task-market-{role}-consumer-{self._ws_hash}"
            thread = threading.Thread(
                target=self._run_consumer,
                args=(role, consumer),
                name=thread_name,
                daemon=True,
            )
            self._threads[role] = thread

        # Outbox relay thread.
        self._outbox_relay_thread = threading.Thread(
            target=self._run_outbox_relay,
            args=(service,),
            name=f"task-market-outbox-relay-{self._ws_hash}",
            daemon=True,
        )

        # Start all threads.
        for thread in self._threads.values():
            thread.start()
        if self._outbox_relay_thread is not None:
            self._outbox_relay_thread.start()

        self._started = True
        logger.info(
            "ConsumerLoopManager started: workspace=%s roles=%s",
            self._workspace,
            tuple(self._consumers.keys()),
        )
        return True

    def stop(self, *, join_timeout: float = 10.0) -> None:
        """Signal all consumers to stop and join their threads."""
        if not self._started:
            return
        self._stop_event.set()

        # Signal stop on each consumer.
        for consumer in self._consumers.values():
            try:
                consumer.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("ConsumerLoopManager: error stopping consumer: %s", exc)

        # Join consumer threads.
        timeout = max(0.1, float(join_timeout))
        for role, thread in self._threads.items():
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning("ConsumerLoopManager: thread for role=%s did not exit within timeout", role)

        # Join outbox relay.
        if self._outbox_relay_thread is not None:
            self._outbox_relay_thread.join(timeout=timeout)
            if self._outbox_relay_thread.is_alive():
                logger.warning("ConsumerLoopManager: outbox relay thread did not exit within timeout")

        self._threads.clear()
        self._consumers.clear()
        self._outbox_relay_thread = None
        self._started = False
        logger.info("ConsumerLoopManager stopped: workspace=%s", self._workspace)

    def is_running(self) -> bool:
        return self._started and not self._stop_event.is_set()

    def status(self) -> dict[str, Any]:
        """Return running state per consumer role and outbox relay."""
        roles: dict[str, dict[str, Any]] = {}
        for role, _consumer in self._consumers.items():
            thread = self._threads.get(role)
            roles[role] = {
                "running": thread is not None and thread.is_alive(),
                "thread_name": getattr(thread, "name", "") if thread else "",
            }
        return {
            "workspace": self._workspace,
            "started": self._started,
            "is_running": self.is_running(),
            "poll_interval": self._poll_interval,
            "roles": roles,
            "outbox_relay_running": (self._outbox_relay_thread is not None and self._outbox_relay_thread.is_alive()),
        }

    # ---- Internal -----------------------------------------------------------

    def _run_consumer(self, role: str, consumer: _Consumer) -> None:
        """Thread target wrapping consumer.run() with exception isolation."""
        t0 = time.monotonic()
        try:
            consumer.run()
        except Exception as exc:
            logger.exception(
                "ConsumerLoopManager: consumer role=%s crashed: %s",
                role,
                exc,
            )
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            self._record_consumer_metrics(role, elapsed_ms)

    def _run_outbox_relay(self, service: _Service) -> None:
        """Thread target for periodic outbox relay."""
        while not self._stop_event.is_set():
            t0 = time.monotonic()
            try:
                service.relay_outbox_messages(self._workspace)
            except Exception as exc:
                logger.exception(
                    "ConsumerLoopManager: outbox relay failed: %s",
                    exc,
                )
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            self._record_consumer_metrics("outbox_relay", elapsed_ms)
            self._stop_event.wait(self._outbox_relay_interval)

    @staticmethod
    def _record_consumer_metrics(role: str, duration_ms: float) -> None:
        """Record consumer poll metrics via TaskMarketMetrics."""
        try:
            from .metrics import get_task_market_metrics

            metrics = get_task_market_metrics()
            metrics.record_consumer_poll(role, duration_ms)
        except (ImportError, OSError, RuntimeError):
            pass

    @staticmethod
    def _default_consumer_types() -> dict[str, type]:
        """Lazy-import default consumer classes."""
        from polaris.cells.chief_engineer.blueprint.public.service import CEConsumer
        from polaris.cells.director.task_consumer import DirectorExecutionConsumer
        from polaris.cells.qa.audit_verdict.public.service import QAConsumer

        return {
            "chief_engineer": CEConsumer,
            "director": DirectorExecutionConsumer,
            "qa": QAConsumer,
        }


__all__ = ["ConsumerLoopManager"]
