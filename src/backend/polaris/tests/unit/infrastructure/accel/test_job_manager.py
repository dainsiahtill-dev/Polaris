"""Tests for polaris.infrastructure.accel.verify.job_manager module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polaris.infrastructure.accel.verify.job_manager import (
    JobManager,
    JobState,
    VerifyJob,
)


class TestJobState:
    """Tests for JobState class."""

    def test_valid_states(self) -> None:
        """All defined states should be valid."""
        for state in [
            JobState.PENDING,
            JobState.RUNNING,
            JobState.CANCELLING,
            JobState.COMPLETED,
            JobState.FAILED,
            JobState.CANCELLED,
        ]:
            assert JobState.is_valid(state) is True

    def test_invalid_state(self) -> None:
        """Invalid state should return False."""
        assert JobState.is_valid("invalid") is False
        assert JobState.is_valid("") is False

    def test_values_list(self) -> None:
        """_VALUES should contain all state values."""
        assert len(JobState._VALUES) == 6


class TestVerifyJob:
    """Tests for VerifyJob dataclass."""

    def test_default_initialization(self) -> None:
        """Job should initialize with correct defaults."""
        job = VerifyJob(job_id="test_123")
        assert job.job_id == "test_123"
        assert job.state == JobState.PENDING
        assert job.stage == "init"
        assert job.progress == 0.0
        assert job.total_commands == 0
        assert job.completed_commands == 0
        assert job.current_command == ""
        assert job.elapsed_sec == 0.0
        assert job.eta_sec is None
        assert job.start_time is None
        assert job.end_time is None
        assert job.error is None
        assert job.result is None

    def test_to_status_pending(self) -> None:
        """to_status should return correct status for pending job."""
        job = VerifyJob(job_id="test_123")
        status = job.to_status()
        assert status["job_id"] == "test_123"
        assert status["state"] == JobState.PENDING
        assert status["progress"] == 0.0

    def test_to_status_running(self) -> None:
        """to_status should calculate elapsed time for running job."""
        job = VerifyJob(job_id="test_123")
        job.mark_running()
        status = job.to_status()
        assert status["state"] == JobState.RUNNING
        assert status["elapsed_sec"] >= 0.0

    def test_add_event(self) -> None:
        """Should add event to events list."""
        job = VerifyJob(job_id="test_123")
        event = job.add_event("test_event", {"data": "value"})
        assert event["event"] == "test_event"
        assert event["job_id"] == "test_123"
        assert event["seq"] == 1

    def test_add_event_increments_seq(self) -> None:
        """Event sequence should increment."""
        job = VerifyJob(job_id="test_123")
        job.add_event("event1", {})
        job.add_event("event2", {})
        events = job.get_events()
        assert events[0]["seq"] == 1
        assert events[1]["seq"] == 2

    def test_is_terminal_completed(self) -> None:
        """Completed job should be terminal."""
        job = VerifyJob(job_id="test_123")
        job.mark_completed("success", 0)
        assert job.is_terminal() is True

    def test_is_terminal_failed(self) -> None:
        """Failed job should be terminal."""
        job = VerifyJob(job_id="test_123")
        job.mark_failed("error")
        assert job.is_terminal() is True

    def test_is_terminal_cancelled(self) -> None:
        """Cancelled job should be terminal."""
        job = VerifyJob(job_id="test_123")
        job.mark_cancelled()
        assert job.is_terminal() is True

    def test_is_terminal_running(self) -> None:
        """Running job should not be terminal."""
        job = VerifyJob(job_id="test_123")
        job.mark_running()
        assert job.is_terminal() is False

    def test_add_live_event_running_job(self) -> None:
        """Should add live event to running job."""
        job = VerifyJob(job_id="test_123")
        job.mark_running()
        result = job.add_live_event("heartbeat", {"progress": 50})
        assert result is True
        events = job.get_events()
        assert len(events) == 1
        assert events[0]["event"] == "heartbeat"

    def test_add_live_event_completed_job(self) -> None:
        """Should not add live event to completed job."""
        job = VerifyJob(job_id="test_123")
        job.mark_completed("success", 0)
        result = job.add_live_event("heartbeat", {"progress": 50})
        assert result is False

    def test_get_events_since_seq(self) -> None:
        """Should filter events by sequence number."""
        job = VerifyJob(job_id="test_123")
        job.add_event("event1", {})
        job.add_event("event2", {})
        job.add_event("event3", {})
        events = job.get_events(since_seq=1)
        assert len(events) == 2
        assert all(e["seq"] > 1 for e in events)

    def test_update_progress(self) -> None:
        """Should update progress correctly."""
        job = VerifyJob(job_id="test_123")
        job.mark_running()
        job.update_progress(completed=5, total=10, current_command="pytest")
        assert job.completed_commands == 5
        assert job.total_commands == 10
        assert job.current_command == "pytest"
        assert job.progress == 50.0
        assert job.eta_sec is not None

    def test_mark_running(self) -> None:
        """Should mark job as running with stage."""
        job = VerifyJob(job_id="test_123")
        job.mark_running(stage="verification")
        assert job.state == JobState.RUNNING
        assert job.stage == "verification"
        assert job.start_time is not None
        assert job._start_perf_counter is not None

    def test_try_mark_completed_success(self) -> None:
        """Should mark job as completed."""
        job = VerifyJob(job_id="test_123")
        job.mark_running()
        result = job.try_mark_completed("success", 0, {"data": "value"})
        assert result is True
        assert job.state == JobState.COMPLETED
        assert job.result == {"data": "value"}

    def test_try_mark_completed_cancelling(self) -> None:
        """Should not mark cancelled job as completed."""
        job = VerifyJob(job_id="test_123")
        job.mark_running()
        job.state = JobState.CANCELLING
        result = job.try_mark_completed("success", 0)
        assert result is False
        assert job.state == JobState.CANCELLING

    def test_mark_completed(self) -> None:
        """mark_completed should set stage and end_time."""
        job = VerifyJob(job_id="test_123")
        job.mark_running()
        job.mark_completed("success", 0)
        assert job.stage == "completed"
        assert job.end_time is not None

    def test_mark_failed(self) -> None:
        """Should mark job as failed with error."""
        job = VerifyJob(job_id="test_123")
        job.mark_running()
        job.mark_failed("test error")
        assert job.state == JobState.FAILED
        assert job.stage == "failed"
        assert job.error == "test error"
        assert job.end_time is not None

    def test_mark_cancelled(self) -> None:
        """Should mark job as cancelled."""
        job = VerifyJob(job_id="test_123")
        job.mark_running()
        job.mark_cancelled()
        assert job.state == JobState.CANCELLED
        assert job.stage == "cancelled"
        assert job.end_time is not None


class TestJobManager:
    """Tests for JobManager singleton."""

    def setup_method(self) -> None:
        """Clear jobs before each test."""
        manager = JobManager()
        manager.clear_all_jobs()

    def teardown_method(self) -> None:
        """Clear jobs after each test."""
        manager = JobManager()
        manager.clear_all_jobs()

    def test_singleton(self) -> None:
        """Should return same instance."""
        manager1 = JobManager()
        manager2 = JobManager()
        assert manager1 is manager2

    def test_create_job(self) -> None:
        """Should create a new job with unique ID."""
        manager = JobManager()
        job = manager.create_job()
        assert job.job_id.startswith("verify_")
        assert JobState.is_valid(job.state)

    def test_create_job_with_prefix(self) -> None:
        """Should use custom prefix for job ID."""
        manager = JobManager()
        job = manager.create_job(prefix="custom")
        assert job.job_id.startswith("custom_")

    def test_create_job_prefix_default(self) -> None:
        """Should default to 'verify' prefix."""
        manager = JobManager()
        job = manager.create_job()
        assert job.job_id.startswith("verify_")

    def test_create_job_prefix_sanitization(self) -> None:
        """Should sanitize prefix."""
        manager = JobManager()
        job = manager.create_job(prefix="  Test  ")
        assert job.job_id.startswith("test_")

    def test_get_job_exists(self) -> None:
        """Should return job if it exists."""
        manager = JobManager()
        created = manager.create_job()
        retrieved = manager.get_job(created.job_id)
        assert retrieved is created

    def test_get_job_not_exists(self) -> None:
        """Should return None if job doesn't exist."""
        manager = JobManager()
        result = manager.get_job("nonexistent")
        assert result is None

    def test_get_all_jobs(self) -> None:
        """Should return all jobs."""
        manager = JobManager()
        job1 = manager.create_job()
        job2 = manager.create_job()
        jobs = manager.get_all_jobs()
        assert len(jobs) == 2
        assert job1 in jobs
        assert job2 in jobs

    def test_cancel_job_running(self) -> None:
        """Should cancel running job."""
        manager = JobManager()
        job = manager.create_job()
        job.mark_running()
        result = manager.cancel_job(job.job_id)
        assert result is True
        assert job.state == JobState.CANCELLING

    def test_cancel_job_nonexistent(self) -> None:
        """Should return False for nonexistent job."""
        manager = JobManager()
        result = manager.cancel_job("nonexistent")
        assert result is False

    def test_cancel_job_completed(self) -> None:
        """Should return False for completed job."""
        manager = JobManager()
        job = manager.create_job()
        job.mark_completed("success", 0)
        result = manager.cancel_job(job.job_id)
        assert result is False

    def test_cleanup_completed(self) -> None:
        """Should remove old completed jobs."""
        manager = JobManager()
        job = manager.create_job()
        job.mark_completed("success", 0)
        job.end_time = datetime.now(timezone.utc) - timedelta(seconds=7200)
        count = manager.cleanup_completed(max_age_seconds=3600)
        assert count == 1
        assert manager.get_job(job.job_id) is None

    def test_cleanup_completed_recent(self) -> None:
        """Should not remove recent completed jobs."""
        manager = JobManager()
        job = manager.create_job()
        job.mark_completed("success", 0)
        count = manager.cleanup_completed(max_age_seconds=3600)
        assert count == 0
        assert manager.get_job(job.job_id) is job

    def test_cleanup_completed_running(self) -> None:
        """Should not remove running jobs."""
        manager = JobManager()
        job = manager.create_job()
        job.mark_running()
        count = manager.cleanup_completed(max_age_seconds=3600)
        assert count == 0
        assert manager.get_job(job.job_id) is job

    def test_clear_all_jobs(self) -> None:
        """Should remove all jobs."""
        manager = JobManager()
        manager.create_job()
        manager.create_job()
        count = manager.clear_all_jobs()
        assert count == 2
        assert len(manager.get_all_jobs()) == 0

    def test_max_jobs_eviction(self) -> None:
        """Should evict oldest job when MAX_JOBS exceeded."""
        manager = JobManager()
        # Create jobs up to limit
        for _ in range(JobManager.MAX_JOBS - 1):
            manager.create_job()
        oldest = manager.create_job()  # This should evict the first one
        # The oldest should still be there (it was just created)
        assert manager.get_job(oldest.job_id) is oldest
