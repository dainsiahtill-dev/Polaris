"""Tests for architecture improvements: state bridge and error classifier.

This module tests the P0 improvements:
1. State bridge between TaskBoard and Workflow Runtime
2. Error classification and recovery strategies
3. Circuit breaker pattern
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from polaris.cells.orchestration.pm_dispatch.internal.error_classifier import (
    CircuitBreaker,
    ErrorCategory,
    ErrorClassifier,
    ExponentialBackoff,
)
from polaris.cells.orchestration.pm_dispatch.internal.state_bridge import (
    StateConsistencyChecker,
    TaskBoardStateBridge,
)
from polaris.cells.orchestration.workflow_runtime.internal.models import TaskFailureRecord
from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime.embedded.store_sqlite import (
    SqliteRuntimeStore,
)
from polaris.cells.runtime.task_runtime.public.task_board_contract import TaskBoard


class TestErrorClassifier:
    """Test error classification and recovery."""

    def test_classify_network_error(self) -> None:
        """Network errors should be classified as transient."""
        error = ConnectionRefusedError("connection refused")
        category = ErrorClassifier.classify(error)
        assert category == ErrorCategory.TRANSIENT_NETWORK

    def test_classify_timeout_error(self) -> None:
        """Timeout errors should be classified as system timeout."""
        error = TimeoutError("operation timed out")
        category = ErrorClassifier.classify(error)
        assert category == ErrorCategory.SYSTEM_TIMEOUT

    def test_classify_auth_error(self) -> None:
        """Permission errors should be classified as permanent auth."""
        error = PermissionError("access denied")
        category = ErrorClassifier.classify(error)
        assert category == ErrorCategory.PERMANENT_AUTH

    def test_classify_validation_error(self) -> None:
        """Value errors should be classified as permanent validation."""
        error = ValueError("invalid argument")
        category = ErrorClassifier.classify(error)
        assert category == ErrorCategory.PERMANENT_VALIDATION

    def test_classify_from_message(self) -> None:
        """Should classify from message string."""
        category, recommendation = ErrorClassifier.classify_from_message("connection refused")
        assert category == ErrorCategory.TRANSIENT_NETWORK
        assert recommendation.can_retry is True

    def test_recovery_recommendation_for_transient(self) -> None:
        """Transient errors should be retryable."""
        category = ErrorCategory.TRANSIENT_NETWORK
        rec = ErrorClassifier.get_recovery_recommendation(category)
        assert rec.can_retry is True
        assert rec.strategy == "backoff"
        assert rec.max_retries > 0

    def test_recovery_recommendation_for_permanent(self) -> None:
        """Permanent errors should not be retryable."""
        category = ErrorCategory.PERMANENT_AUTH
        rec = ErrorClassifier.get_recovery_recommendation(category)
        assert rec.can_retry is False
        assert rec.strategy in ("manual", "abort")


class TestExponentialBackoff:
    """Test exponential backoff calculations."""

    def test_backoff_increases_with_attempts(self) -> None:
        """Delay should increase with attempt number."""
        backoff = ExponentialBackoff(base_delay=1.0, jitter=False)

        delay_0 = backoff.calculate_delay(0)
        delay_1 = backoff.calculate_delay(1)
        delay_2 = backoff.calculate_delay(2)

        assert delay_0 < delay_1 < delay_2

    def test_backoff_respects_max_delay(self) -> None:
        """Delay should not exceed max_delay."""
        backoff = ExponentialBackoff(base_delay=1.0, max_delay=5.0, jitter=False)

        delay = backoff.calculate_delay(10)  # High attempt number

        assert delay <= 5.0

    def test_backoff_with_jitter(self) -> None:
        """Jitter should add randomness."""
        backoff = ExponentialBackoff(base_delay=1.0, jitter=True)

        delays = [backoff.calculate_delay(1) for _ in range(10)]

        # All delays should be different (very unlikely to be same with jitter)
        assert len(set(delays)) > 1


class TestCircuitBreaker:
    """Test circuit breaker pattern."""

    def test_initial_state_is_closed(self) -> None:
        """Circuit should start closed."""
        cb = CircuitBreaker("test")
        assert cb.state == CircuitBreaker.State.CLOSED
        assert cb.can_execute() is True

    def test_opens_after_failures(self) -> None:
        """Circuit should open after threshold failures."""
        cb = CircuitBreaker("test", failure_threshold=3)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.State.CLOSED  # Still closed

        cb.record_failure()
        assert cb.state == CircuitBreaker.State.OPEN
        assert cb.can_execute() is False

    def test_records_success(self) -> None:
        """Success should be recorded."""
        cb = CircuitBreaker("test")

        cb.record_success()
        # No state change, just internal tracking
        assert cb.state == CircuitBreaker.State.CLOSED

    def test_half_open_after_timeout(self) -> None:
        """Should transition to half-open after recovery timeout."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)

        cb.record_failure()
        assert cb.state == CircuitBreaker.State.OPEN
        assert cb.can_execute() is False

        # Wait for recovery timeout
        import time
        time.sleep(0.15)

        # Now should be HALF_OPEN
        assert cb.state == CircuitBreaker.State.HALF_OPEN
        assert cb.can_execute() is True

    def test_closes_after_successes_in_half_open(self) -> None:
        """Should close after successes in half-open state."""
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1, half_open_max_calls=2)

        cb.record_failure()
        import time
        time.sleep(0.15)

        assert cb.state == CircuitBreaker.State.HALF_OPEN

        cb.record_success()
        cb.record_success()

        assert cb.state == CircuitBreaker.State.CLOSED


class TestTaskBoardStateBridge:
    """Test TaskBoard state bridge."""

    @pytest.fixture
    def temp_workspace(self, tmp_path: Path) -> str:
        """Create a temporary workspace."""
        return str(tmp_path / "workspace")

    @pytest.mark.asyncio
    async def test_bridge_initialization_without_store(self, temp_workspace: str) -> None:
        """Bridge should work without workflow store."""
        task_board = TaskBoard(temp_workspace)
        bridge = TaskBoardStateBridge(task_board, workflow_store=None)

        # Should not raise
        await bridge.start()
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_bridge_with_workflow_store(self, temp_workspace: str) -> None:
        """Bridge should sync to workflow store."""
        task_board = TaskBoard(temp_workspace)
        store = SqliteRuntimeStore(":memory:")
        bridge = TaskBoardStateBridge(task_board, workflow_store=store)

        await bridge.start()

        # Create a task in TaskBoard
        task = task_board.create("Test task")

        # Notify bridge
        bridge.notify_task_created(task.id, subject="Test task")

        # Give time for async processing
        await asyncio.sleep(0.1)

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_unified_task_status(self, temp_workspace: str) -> None:
        """Should provide unified task status from both sources."""
        task_board = TaskBoard(temp_workspace)
        store = SqliteRuntimeStore(":memory:")
        bridge = TaskBoardStateBridge(task_board, workflow_store=store)

        # Create a task
        task = task_board.create("Test task")

        # Get unified status
        status = await bridge.get_unified_task_status(str(task.id))

        assert status is not None
        assert status["task_id"] == str(task.id)
        assert "sources" in status
        assert "task_board" in status["sources"]


class TestStateConsistencyChecker:
    """Test state consistency checker."""

    @pytest.fixture
    def temp_workspace(self, tmp_path: Path) -> str:
        """Create a temporary workspace."""
        return str(tmp_path / "workspace")

    @pytest.fixture
    async def temp_store(self, tmp_path: Path):
        """Create a temporary SQLite store."""
        db_path = str(tmp_path / "test.db")
        store = SqliteRuntimeStore(db_path)
        yield store

    @pytest.mark.asyncio
    async def test_empty_check_passes(self, temp_workspace: str, tmp_path: Path) -> None:
        """Empty task board should be consistent."""
        task_board = TaskBoard(temp_workspace)
        db_path = str(tmp_path / "test.db")
        store = SqliteRuntimeStore(db_path)
        # Initialize the workflow execution table by creating an execution
        await store.create_execution("test-workflow", "test", {})
        checker = StateConsistencyChecker(task_board, workflow_store=store)

        report = await checker.check_consistency("test-workflow")

        assert report["consistent"] is True
        assert report["summary"]["checked"] == 0
        assert len(report["inconsistencies"]) == 0

    @pytest.mark.asyncio
    async def test_detects_missing_in_workflow(self, temp_workspace: str, tmp_path: Path) -> None:
        """Should detect task in TaskBoard but not in workflow."""
        task_board = TaskBoard(temp_workspace)
        db_path = str(tmp_path / "test.db")
        store = SqliteRuntimeStore(db_path)
        # Initialize the workflow execution table
        await store.create_execution("test-workflow", "test", {})
        checker = StateConsistencyChecker(task_board, workflow_store=store)

        # Create task only in TaskBoard
        task = task_board.create("Test task")

        report = await checker.check_consistency("test-workflow")

        assert report["summary"]["missing_in_workflow"] == 1
        assert len(report["inconsistencies"]) == 1
        assert report["inconsistencies"][0]["type"] == "missing_in_workflow"


class TestTaskFailureRecord:
    """Test TaskFailureRecord model."""

    def test_to_dict(self) -> None:
        """Should serialize to dict."""
        record = TaskFailureRecord(
            task_id="task-1",
            error_message="Connection refused",
            error_category="transient_network",
            retryable=True,
            max_retries=3,
            recovery_strategy="backoff",
        )

        d = record.to_dict()

        assert d["task_id"] == "task-1"
        assert d["error_category"] == "transient_network"
        assert d["retryable"] is True
        assert "timestamp" in d
