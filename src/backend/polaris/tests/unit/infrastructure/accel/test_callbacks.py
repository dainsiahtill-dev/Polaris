"""Tests for polaris.infrastructure.accel.verify.callbacks module."""

from __future__ import annotations

from polaris.infrastructure.accel.verify.callbacks import (
    NoOpCallback,
    VerifyProgressCallback,
    VerifyStage,
)


class TestVerifyStage:
    """Tests for VerifyStage enum."""

    def test_all_stages_defined(self) -> None:
        """All expected stages should be defined."""
        expected = {
            "INIT",
            "LOAD_CACHE",
            "SELECT_CMDS",
            "RUNNING",
            "PARALLEL",
            "SEQUENTIAL",
            "COMPLETING",
            "CLEANUP",
        }
        assert {stage.name for stage in VerifyStage} == expected

    def test_stage_values(self) -> None:
        """Stages should have sequential auto values."""
        values = [stage.value for stage in VerifyStage]
        assert values == list(range(1, len(values) + 1))


class TestNoOpCallback:
    """Tests for NoOpCallback class."""

    def test_on_start(self) -> None:
        """on_start should not raise."""
        callback = NoOpCallback()
        callback.on_start("job_123", 10)

    def test_on_stage_change(self) -> None:
        """on_stage_change should not raise."""
        callback = NoOpCallback()
        callback.on_stage_change("job_123", VerifyStage.RUNNING)

    def test_on_command_start(self) -> None:
        """on_command_start should not raise."""
        callback = NoOpCallback()
        callback.on_command_start("job_123", "pytest", 0, 10)

    def test_on_command_complete(self) -> None:
        """on_command_complete should not raise."""
        callback = NoOpCallback()
        callback.on_command_complete(
            "job_123",
            "pytest",
            0,
            1.5,
            completed=1,
            total=10,
            stdout_tail="output",
            stderr_tail="",
        )

    def test_on_command_complete_with_defaults(self) -> None:
        """on_command_complete should work with minimal args."""
        callback = NoOpCallback()
        callback.on_command_complete("job_123", "pytest", 0, 1.5)

    def test_on_progress(self) -> None:
        """on_progress should not raise."""
        callback = NoOpCallback()
        callback.on_progress("job_123", 5, 10, "pytest tests/")

    def test_on_heartbeat(self) -> None:
        """on_heartbeat should not raise."""
        callback = NoOpCallback()
        callback.on_heartbeat(
            "job_123",
            5.0,
            10.0,
            "running",
            current_command="pytest",
            command_elapsed_sec=3.0,
            command_timeout_sec=60.0,
            command_progress_pct=5.0,
        )

    def test_on_heartbeat_with_stall(self) -> None:
        """on_heartbeat should handle stall detection."""
        callback = NoOpCallback()
        callback.on_heartbeat(
            "job_123",
            10.0,
            50.0,
            "running",
            stall_detected=True,
            stall_elapsed_sec=5.0,
        )

    def test_on_command_output(self) -> None:
        """on_command_output should not raise."""
        callback = NoOpCallback()
        callback.on_command_output(
            "job_123",
            "pytest",
            "stdout",
            "some output",
            truncated=False,
        )

    def test_on_cache_hit(self) -> None:
        """on_cache_hit should not raise."""
        callback = NoOpCallback()
        callback.on_cache_hit("job_123", "pytest")

    def test_on_skip(self) -> None:
        """on_skip should not raise."""
        callback = NoOpCallback()
        callback.on_skip("job_123", "pytest", "missing binary")

    def test_on_error(self) -> None:
        """on_error should not raise."""
        callback = NoOpCallback()
        callback.on_error("job_123", "pytest", "some error")

    def test_on_error_no_command(self) -> None:
        """on_error should handle None command."""
        callback = NoOpCallback()
        callback.on_error("job_123", None, "job error")

    def test_on_complete(self) -> None:
        """on_complete should not raise."""
        callback = NoOpCallback()
        callback.on_complete("job_123", "success", 0)


class TestVerifyProgressCallbackProtocol:
    """Tests to ensure VerifyProgressCallback protocol is implemented correctly."""

    def test_noop_is_valid_callback(self) -> None:
        """NoOpCallback should satisfy the VerifyProgressCallback protocol."""
        callback: VerifyProgressCallback = NoOpCallback()
        # All methods should exist and be callable
        assert callable(callback.on_start)
        assert callable(callback.on_stage_change)
        assert callable(callback.on_command_start)
        assert callable(callback.on_command_complete)
        assert callable(callback.on_progress)
        assert callable(callback.on_heartbeat)
        assert callable(callback.on_command_output)
        assert callable(callback.on_cache_hit)
        assert callable(callback.on_skip)
        assert callable(callback.on_error)
        assert callable(callback.on_complete)


class TestCustomCallback:
    """Tests for custom callback implementation."""

    def test_custom_callback_receives_events(self) -> None:
        """Custom callback should receive callback events."""
        received_events: list[tuple] = []

        class RecordingCallback(NoOpCallback):
            def on_start(self, job_id: str, total_commands: int) -> None:
                received_events.append(("start", job_id, total_commands))

            def on_command_complete(
                self,
                job_id: str,
                command: str,
                exit_code: int,
                duration: float,
                *,
                completed: int | None = None,
                total: int | None = None,
                stdout_tail: str = "",
                stderr_tail: str = "",
            ) -> None:
                received_events.append(("complete", job_id, command, exit_code))

        callback = RecordingCallback()
        callback.on_start("job_123", 5)
        callback.on_command_complete("job_123", "pytest", 0, 1.5, completed=1, total=5)

        assert len(received_events) == 2
        assert received_events[0] == ("start", "job_123", 5)
        assert received_events[1] == ("complete", "job_123", "pytest", 0)
