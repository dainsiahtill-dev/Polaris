"""Audit Metrics Benchmark — Performance benchmarks for Omniscient Audit System.

This module provides benchmark cases and validators to measure the audit
system's performance under various load conditions.

Features:
- Event throughput benchmark (events/second)
- Storm detection latency benchmark
- Alert firing accuracy benchmark
- Storage tier rotation benchmark

Usage:
    # Run audit benchmarks
    runner = UnifiedBenchmarkRunner()
    cases = get_audit_benchmark_cases()
    result = await runner.run_suite(cases, workspace=".", mode="agentic")

    # Or run standalone
    benchmarker = AuditMetricsBenchmarker()
    metrics = await benchmarker.run_throughput_benchmark(event_count=10000)
    print(f"Throughput: {metrics.events_per_second} events/sec")
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.audit.alerting import AlertingEngine
from polaris.kernelone.audit.omniscient.bus import OmniscientAuditBus
from polaris.kernelone.audit.omniscient.storm_detector import (
    AuditStormDetector,
    StormLevel,
)
from polaris.kernelone.benchmark.unified_models import (
    BudgetConditions,
    JudgeConfig,
    UnifiedBenchmarkCase,
)

# =============================================================================
# Audit Benchmark Cases
# =============================================================================


def get_audit_benchmark_cases() -> list[UnifiedBenchmarkCase]:
    """Get the standard set of audit metric benchmark cases.

    Returns:
        List of UnifiedBenchmarkCase instances for audit metrics.
    """
    return [
        UnifiedBenchmarkCase(
            case_id="audit_throughput_baseline",
            role="director",
            title="Audit Throughput Baseline",
            prompt="Run audit throughput test: emit 1000 events as fast as possible and report events_per_second",
            description="Measures baseline event throughput of the audit bus",
            judge=JudgeConfig(
                score_threshold=0.7,
                mode="agentic",
                validators=("audit_throughput",),
            ),
            budget_conditions=BudgetConditions(
                max_tokens=5000,
                max_turns=3,
                max_wall_time_seconds=60.0,
            ),
        ),
        UnifiedBenchmarkCase(
            case_id="audit_storm_detection",
            role="director",
            title="Audit Storm Detection",
            prompt="Run storm detection test: emit 6000 events in 1 second window and verify storm level detection",
            description="Tests storm detection accuracy at high event rates",
            judge=JudgeConfig(
                score_threshold=0.8,
                mode="agentic",
                validators=("audit_storm_detection",),
            ),
            budget_conditions=BudgetConditions(
                max_tokens=5000,
                max_turns=3,
                max_wall_time_seconds=60.0,
            ),
        ),
        UnifiedBenchmarkCase(
            case_id="audit_alert_firing",
            role="director",
            title="Audit Alert Firing",
            prompt="Run alert firing test: trigger 5 task_failed events and verify alerts fire correctly",
            description="Tests that alerting engine fires alerts at correct thresholds",
            judge=JudgeConfig(
                score_threshold=0.75,
                mode="agentic",
                validators=("audit_alert_firing",),
            ),
            budget_conditions=BudgetConditions(
                max_tokens=5000,
                max_turns=3,
                max_wall_time_seconds=60.0,
            ),
        ),
        UnifiedBenchmarkCase(
            case_id="audit_storage_tier_rotation",
            role="director",
            title="Audit Storage Tier Rotation",
            prompt="Run storage tier test: emit events and verify hot/cold tier classification works",
            description="Tests storage tier adapter hot/cold classification",
            judge=JudgeConfig(
                score_threshold=0.7,
                mode="agentic",
                validators=("audit_storage_tier",),
            ),
            budget_conditions=BudgetConditions(
                max_tokens=5000,
                max_turns=3,
                max_wall_time_seconds=60.0,
            ),
        ),
    ]


# =============================================================================
# Audit Validators
# =============================================================================


class AuditThroughputValidator:
    """Validator that checks audit throughput metrics.

    Checks that the observed run produced audit metrics within expected ranges.
    """

    name: str = "audit_throughput"
    category: str = "performance"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: Any,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check throughput from observed fingerprint metadata.

        Returns:
            Tuple of (passed, message).
        """
        fingerprint = getattr(observed, "fingerprint", {}) or {}
        events_per_sec = fingerprint.get("events_per_second", 0)
        total_events = fingerprint.get("total_events", 0)

        if total_events < 100:
            return False, f"insufficient events emitted: {total_events}"

        # Baseline: expect at least 100 events/sec
        if events_per_sec < 100:
            return False, f"throughput too low: {events_per_sec} events/sec (expected >= 100)"

        return True, f"throughput acceptable: {events_per_sec} events/sec ({total_events} total)"


class AuditStormDetectionValidator:
    """Validator that checks storm detection accuracy.

    Verifies that the storm detector correctly identifies storm levels
    based on event rate.
    """

    name: str = "audit_storm_detection"
    category: str = "performance"
    critical: bool = True

    def validate(
        self,
        output_text: str,
        observed: Any,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check storm detection from observed fingerprint metadata.

        Returns:
            Tuple of (passed, message).
        """
        fingerprint = getattr(observed, "fingerprint", {}) or {}
        detected_level = fingerprint.get("storm_level", "normal")
        peak_count = fingerprint.get("peak_event_count", 0)

        # At 6000 events/sec we expect at least WARNING level
        expected_minimum = StormLevel.WARNING.value

        level_order = [
            StormLevel.NORMAL,
            StormLevel.ELEVATED,
            StormLevel.WARNING,
            StormLevel.CRITICAL,
            StormLevel.EMERGENCY,
        ]

        try:
            detected_idx = level_order.index(StormLevel(detected_level))
            expected_idx = level_order.index(StormLevel(expected_minimum))
        except ValueError:
            return False, f"invalid storm level detected: {detected_level}"

        if detected_idx < expected_idx:
            return (
                False,
                f"storm level too low: {detected_level} (expected >= {expected_minimum}) at {peak_count} events",
            )

        return True, f"storm detection accurate: {detected_level} at {peak_count} events"


class AuditAlertFiringValidator:
    """Validator that checks alert firing accuracy.

    Verifies that alerts fire at the correct thresholds.
    """

    name: str = "audit_alert_firing"
    category: str = "contract"
    critical: bool = True

    def validate(
        self,
        output_text: str,
        observed: Any,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check alert firing from observed fingerprint metadata.

        Returns:
            Tuple of (passed, message).
        """
        fingerprint = getattr(observed, "fingerprint", {}) or {}
        alerts_fired = fingerprint.get("alerts_fired", 0)
        expected_min = 1

        if alerts_fired < expected_min:
            return False, f"insufficient alerts fired: {alerts_fired} (expected >= {expected_min})"

        return True, f"alert firing correct: {alerts_fired} alerts fired"


class AuditStorageTierValidator:
    """Validator that checks storage tier classification.

    Verifies that hot/cold tier classification works correctly.
    """

    name: str = "audit_storage_tier"
    category: str = "contract"
    critical: bool = False

    def validate(
        self,
        output_text: str,
        observed: Any,
        known_paths: list[str],
    ) -> tuple[bool, str]:
        """Check storage tier from observed fingerprint metadata.

        Returns:
            Tuple of (passed, message).
        """
        fingerprint = getattr(observed, "fingerprint", {}) or {}
        hot_events = fingerprint.get("hot_events", 0)
        cold_events = fingerprint.get("cold_events", 0)
        total = hot_events + cold_events

        if total == 0:
            return False, "no events recorded in storage tiers"

        # All events should be hot in a fresh benchmark
        if hot_events == 0:
            return False, "no hot events recorded"

        return True, f"storage tier classification works: {hot_events} hot, {cold_events} cold"


# Registry of audit validators
AUDIT_VALIDATORS: dict[str, type] = {
    "audit_throughput": AuditThroughputValidator,
    "audit_storm_detection": AuditStormDetectionValidator,
    "audit_alert_firing": AuditAlertFiringValidator,
    "audit_storage_tier": AuditStorageTierValidator,
}


# =============================================================================
# Audit Metrics Benchmarker (Standalone)
# =============================================================================


@dataclass
class ThroughputMetrics:
    """Metrics from a throughput benchmark run."""

    total_events: int
    duration_seconds: float
    events_per_second: float
    peak_concurrent: int = 0
    dropped_events: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_events": self.total_events,
            "duration_seconds": round(self.duration_seconds, 3),
            "events_per_second": round(self.events_per_second, 2),
            "peak_concurrent": self.peak_concurrent,
            "dropped_events": self.dropped_events,
        }


@dataclass
class StormDetectionMetrics:
    """Metrics from a storm detection benchmark run."""

    target_event_count: int
    window_seconds: float
    detected_level: str
    peak_event_count: int
    expected_minimum_level: str
    passed: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_event_count": self.target_event_count,
            "window_seconds": self.window_seconds,
            "detected_level": self.detected_level,
            "peak_event_count": self.peak_event_count,
            "expected_minimum_level": self.expected_minimum_level,
            "passed": self.passed,
            "message": self.message,
        }


@dataclass
class AlertMetrics:
    """Metrics from an alert firing benchmark run."""

    events_emitted: int
    alerts_fired: int
    expected_alert_count: int
    passed: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "events_emitted": self.events_emitted,
            "alerts_fired": self.alerts_fired,
            "expected_alert_count": self.expected_alert_count,
            "passed": self.passed,
            "message": self.message,
        }


class AuditMetricsBenchmarker:
    """Standalone benchmarker for audit system metrics.

    This class provides direct benchmark methods without going through
    the full benchmark runner framework. Useful for quick performance
    verification and regression testing.

    Usage:
        benchmarker = AuditMetricsBenchmarker()
        metrics = await benchmarker.run_throughput_benchmark(event_count=1000)
        print(f"Throughput: {metrics.events_per_second} events/sec")
    """

    def __init__(
        self,
        storm_detector: AuditStormDetector | None = None,
        alerting_engine: AlertingEngine | None = None,
    ) -> None:
        """Initialize the benchmarker.

        Args:
            storm_detector: Optional storm detector. Creates default if None.
            alerting_engine: Optional alerting engine. Creates default if None.
        """
        self._storm_detector = storm_detector or AuditStormDetector()
        self._alerting_engine = alerting_engine or AlertingEngine()

    async def run_throughput_benchmark(
        self,
        event_count: int = 1000,
        concurrency: int = 10,
    ) -> ThroughputMetrics:
        """Run throughput benchmark by emitting events as fast as possible.

        Args:
            event_count: Number of events to emit.
            concurrency: Number of concurrent emit tasks.

        Returns:
            ThroughputMetrics with performance results.
        """
        bus = await self._create_test_bus()

        emitted = 0
        start_time = time.monotonic()
        peak_concurrent = 0
        dropped = 0

        async def emit_batch(batch_size: int) -> None:
            nonlocal emitted, peak_concurrent, dropped
            tasks = []
            for _ in range(batch_size):
                event = self._create_test_event(f"throughput_test_{emitted}")
                task = asyncio.create_task(bus.emit(event))
                tasks.append(task)
                emitted += 1

            # Track peak concurrent operations
            peak_concurrent = max(peak_concurrent, len(tasks))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            dropped += sum(1 for r in results if isinstance(r, Exception))

        # Emit in batches
        batch_size = event_count // concurrency
        for _ in range(concurrency):
            await emit_batch(batch_size)

        # Account for remainder
        remainder = event_count % concurrency
        if remainder:
            await emit_batch(remainder)

        # Allow events to be processed
        await asyncio.sleep(0.5)

        duration = time.monotonic() - start_time

        # Get final stats
        stats = bus.get_stats()
        dropped = stats.get("events_dropped", 0)

        await bus.stop()

        return ThroughputMetrics(
            total_events=event_count,
            duration_seconds=duration,
            events_per_second=event_count / duration if duration > 0 else 0,
            peak_concurrent=peak_concurrent,
            dropped_events=dropped,
        )

    async def run_storm_detection_benchmark(
        self,
        target_events: int = 6000,
        window_seconds: float = 1.0,
    ) -> StormDetectionMetrics:
        """Run storm detection benchmark by emitting events at high rate.

        Args:
            target_events: Number of events to emit in the window.
            window_seconds: The storm detector window size.

        Returns:
            StormDetectionMetrics with detection results.
        """
        detector = AuditStormDetector(window_seconds=window_seconds)

        emitted = 0
        start_time = time.monotonic()

        async def emit_rapid() -> None:
            nonlocal emitted
            while time.monotonic() - start_time < window_seconds:
                detector.record_event("test_event")
                emitted += 1
                if emitted >= target_events:
                    break
                # Minimal yield to allow other tasks
                if emitted % 100 == 0:
                    await asyncio.sleep(0)

        # Run rapid emission
        await emit_rapid()

        # Get detected level
        detected_level = detector.get_level()
        stats = detector.get_stats()
        peak_count = stats.get("total_count", 0)

        # Expected: at WARNING level with 6000 events in 1 second window
        # Adjust expectation based on target events
        detector_thresholds = detector._elevated_threshold  # 500 by default
        if target_events < detector_thresholds:
            expected_minimum = StormLevel.NORMAL
        elif target_events < detector_thresholds * 4:
            expected_minimum = StormLevel.ELEVATED
        elif target_events < detector_thresholds * 10:
            expected_minimum = StormLevel.WARNING
        else:
            expected_minimum = StormLevel.CRITICAL

        level_order = [
            StormLevel.NORMAL,
            StormLevel.ELEVATED,
            StormLevel.WARNING,
            StormLevel.CRITICAL,
            StormLevel.EMERGENCY,
        ]

        try:
            detected_idx = level_order.index(detected_level)
            expected_idx = level_order.index(expected_minimum)
            passed = detected_idx >= expected_idx
            message = f"{detected_level.value} at {peak_count} events (expected >= {expected_minimum.value})"
        except ValueError:
            passed = False
            message = f"invalid level: {detected_level}"

        return StormDetectionMetrics(
            target_event_count=target_events,
            window_seconds=window_seconds,
            detected_level=detected_level.value,
            peak_event_count=peak_count,
            expected_minimum_level=expected_minimum.value,
            passed=passed,
            message=message,
        )

    async def run_alert_firing_benchmark(
        self,
        failure_count: int = 5,
    ) -> AlertMetrics:
        """Run alert firing benchmark by emitting failure events.

        Args:
            failure_count: Number of task_failed events to emit.

        Returns:
            AlertMetrics with alert firing results.
        """
        engine = AlertingEngine()

        # Emit failure events
        from polaris.kernelone.audit.contracts import KernelAuditEvent, KernelAuditEventType

        for i in range(failure_count):
            event = KernelAuditEvent(
                event_id=f"alert_test_{i}",
                timestamp=datetime.now(timezone.utc),
                event_type=KernelAuditEventType.TASK_FAILED,
                task={"task_id": f"task_{i}"},
                action={"success": False, "error": "test error"},
                data={"event_type": "task_failed"},
            )
            engine.evaluate(event)

        # Check active alerts
        active_alerts = engine.get_active_alerts()
        alerts_fired = len(active_alerts)

        expected_min = 1  # At least 1 alert should fire

        passed = alerts_fired >= expected_min
        message = f"{alerts_fired} alerts fired (expected >= {expected_min})"

        return AlertMetrics(
            events_emitted=failure_count,
            alerts_fired=alerts_fired,
            expected_alert_count=expected_min,
            passed=passed,
            message=message,
        )

    async def _create_test_bus(self) -> OmniscientAuditBus:
        """Create a test audit bus.

        Returns:
            A configured OmniscientAuditBus instance.
        """
        bus = OmniscientAuditBus(
            name=f"audit_benchmark_{uuid.uuid4().hex[:8]}",
            storm_detector=self._storm_detector,
        )
        await bus.start()
        return bus

    def _create_test_event(self, event_id: str) -> dict[str, Any]:
        """Create a test event dict.

        Args:
            event_id: Unique identifier for the event.

        Returns:
            Event dict.
        """
        return {
            "event_id": event_id,
            "type": "llm_interaction",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "priority": "info",
            "task_id": f"task_{uuid.uuid4().hex[:8]}",
            "data": {
                "model": "test-model",
                "duration_ms": 100,
            },
        }


def get_validator(name: str) -> Any:
    """Get an audit validator by name.

    Args:
        name: Validator name (e.g., "audit_throughput").

    Returns:
        Validator instance.

    Raises:
        KeyError: If validator not found.
    """
    validator_class = AUDIT_VALIDATORS[name]
    return validator_class()


__all__ = [
    "AlertMetrics",
    "AuditAlertFiringValidator",
    "AuditMetricsBenchmarker",
    "AuditStorageTierValidator",
    "AuditStormDetectionValidator",
    "AuditThroughputValidator",
    "StormDetectionMetrics",
    "ThroughputMetrics",
    "get_audit_benchmark_cases",
    "get_validator",
]
