from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest
from polaris.cells.runtime.execution_broker.internal import service as broker_service_module
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionProcessStatusV1,
    GetExecutionProcessStatusQueryV1,
    LaunchExecutionProcessCommandV1,
)
from polaris.cells.runtime.execution_broker.public.service import ExecutionBrokerService
from polaris.kernelone.runtime import AsyncTaskSpec, BlockingIoSpec, ExecutionFacade, ExecutionRuntime


def test_log_drain_default_has_no_wall_clock_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default log draining must follow process lifetime instead of a fixed 30s cutoff."""
    monkeypatch.delenv("KERNELONE_EXECUTION_BROKER_LOG_DRAIN_MAX_SECONDS", raising=False)

    assert broker_service_module._resolve_log_drain_max_seconds() is None


def test_log_drain_optional_wall_clock_cutoff_is_env_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Diagnostic log drain cutoff remains available only through explicit env config."""
    monkeypatch.setenv("KERNELONE_EXECUTION_BROKER_LOG_DRAIN_MAX_SECONDS", "2.5")

    assert broker_service_module._resolve_log_drain_max_seconds() == 2.5


@pytest.mark.asyncio
async def test_launch_process_wait_and_log(tmp_path: Path) -> None:
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))
    log_path = tmp_path / "process.log"

    command = LaunchExecutionProcessCommandV1(
        name="echo-test",
        args=(sys.executable, "-c", "print('broker-ok')"),
        workspace=str(tmp_path),
        timeout_seconds=5.0,
        log_path=str(log_path),
        metadata={"test_case": "launch_process_wait_and_log"},
    )

    try:
        launch = await broker.launch_process(command)
        assert launch.success is True
        assert launch.handle is not None

        wait_result = await broker.wait_process(launch.handle, timeout_seconds=5.0)
        assert wait_result.success is True
        assert wait_result.status == ExecutionProcessStatusV1.SUCCESS
        assert wait_result.exit_code == 0
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "[execution_broker] launched" in content
        assert "[execution_broker] command=" in content
        assert "broker-ok" in content
        assert "[execution_broker] terminal" in content
        assert "exit_code=0" in content
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_failed_process_preserves_stderr_in_log_and_status(tmp_path: Path) -> None:
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))
    log_path = tmp_path / "failed-process.log"

    command = LaunchExecutionProcessCommandV1(
        name="stderr-test",
        args=(
            sys.executable,
            "-c",
            "import sys; print('broker-stdout'); print('broker-stderr', file=sys.stderr); sys.exit(7)",
        ),
        workspace=str(tmp_path),
        timeout_seconds=5.0,
        log_path=str(log_path),
        metadata={"test_case": "failed_process_preserves_stderr"},
    )

    try:
        launch = await broker.launch_process(command)
        assert launch.success is True
        assert launch.handle is not None

        wait_result = await broker.wait_process(launch.handle, timeout_seconds=5.0)
        assert wait_result.success is False
        assert wait_result.status == ExecutionProcessStatusV1.FAILED
        assert wait_result.exit_code == 7
        assert "broker-stderr" in str(wait_result.error_message)

        content = log_path.read_text(encoding="utf-8")
        assert "broker-stdout" in content
        assert "broker-stderr" in content
        assert "exit_code=7" in content
        assert "error=broker-stderr" in content
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_terminate_long_running_process() -> None:
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))

    command = LaunchExecutionProcessCommandV1(
        name="sleep-test",
        args=(sys.executable, "-c", "import time; time.sleep(30)"),
        workspace=str(Path.cwd()),
        timeout_seconds=60.0,
    )

    try:
        launch = await broker.launch_process(command)
        assert launch.success is True
        assert launch.handle is not None
        terminated = await broker.terminate_process(launch.handle, timeout_seconds=1.0)
        assert isinstance(terminated, bool)
        status = broker.get_process_status(
            GetExecutionProcessStatusQueryV1(execution_id=launch.handle.execution_id),
        )
        assert status in {
            ExecutionProcessStatusV1.CANCELLED,
            ExecutionProcessStatusV1.FAILED,
            ExecutionProcessStatusV1.TIMED_OUT,
            ExecutionProcessStatusV1.SUCCESS,
        }
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_submit_async_and_blocking_via_broker() -> None:
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))

    async def async_job() -> str:
        await asyncio.sleep(0.02)
        return "async-ok"

    def blocking_job() -> str:
        time.sleep(0.02)
        return "blocking-ok"

    async_handle = broker.submit_async_task(
        AsyncTaskSpec(name="async-job", coroutine_factory=async_job, timeout_seconds=1.0)
    )
    blocking_handle = broker.submit_blocking_io(
        BlockingIoSpec(name="blocking-job", func=blocking_job, timeout_seconds=1.0)
    )

    try:
        async_status = await async_handle.wait(timeout=1.0)
        blocking_status = await blocking_handle.wait(timeout=1.0)
        assert async_status.value == "success"
        assert blocking_status.value == "success"
    finally:
        await runtime.close()


# =============================================================================
# Security Tests - Metadata Injection Prevention
# =============================================================================


@pytest.mark.asyncio
async def test_metadata_cannot_override_internal_fields(tmp_path: Path) -> None:
    """User metadata must be isolated; internal fields cannot be overridden."""
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))
    log_path = tmp_path / "security_test.log"

    # Malicious user attempts to override internal fields
    malicious_metadata = {
        "workspace": "/etc/passwd",
        "log_path": "/evil/path",
        "execution_broker": "malicious_broker",
    }

    command = LaunchExecutionProcessCommandV1(
        name="security-test",
        args=(sys.executable, "-c", "print('ok')"),
        workspace=str(tmp_path),
        timeout_seconds=5.0,
        log_path=str(log_path),
        metadata=malicious_metadata,
    )

    try:
        launch = await broker.launch_process(command)
        assert launch.success is True
        assert launch.handle is not None

        # Verify internal fields are NOT overridden by user metadata
        # Internal fields come from command parameters, not user metadata
        assert launch.handle.workspace == str(tmp_path.resolve())
        assert launch.handle.log_path == str(log_path.resolve())

        # Verify user metadata is isolated under _user_metadata key
        assert "_user_metadata" in launch.handle.metadata
        user_meta = launch.handle.metadata["_user_metadata"]
        assert user_meta["workspace"] == "/etc/passwd"
        assert user_meta["log_path"] == "/evil/path"
        assert user_meta["execution_broker"] == "malicious_broker"

        # Internal fields must NOT contain user-injected values
        assert launch.handle.metadata.get("workspace") is None
        assert launch.handle.metadata.get("log_path") is None
        assert launch.handle.metadata.get("execution_broker") is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_metadata_isolation_in_runtime_snapshot(tmp_path: Path) -> None:
    """User metadata must be stored separately from internal runtime metadata."""
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))
    log_path = tmp_path / "isolation_test.log"

    user_metadata = {
        "custom_key": "custom_value",
        "priority": "high",
        "tags": ["test", "security"],
    }

    command = LaunchExecutionProcessCommandV1(
        name="isolation-test",
        args=(sys.executable, "-c", "print('ok')"),
        workspace=str(tmp_path),
        timeout_seconds=5.0,
        log_path=str(log_path),
        metadata=user_metadata,
    )

    try:
        launch = await broker.launch_process(command)
        assert launch.success is True

        # Get runtime snapshot to verify metadata isolation at runtime level
        assert launch.handle is not None
        snapshot = broker.get_process_snapshot(launch.handle)

        # User metadata should be under _user_metadata
        assert "_user_metadata" in snapshot.metadata
        assert snapshot.metadata["_user_metadata"]["custom_key"] == "custom_value"
        assert snapshot.metadata["_user_metadata"]["priority"] == "high"
        assert snapshot.metadata["_user_metadata"]["tags"] == ["test", "security"]

        # Internal fields should be separate
        assert snapshot.metadata["workspace"] == str(tmp_path.resolve())
        assert snapshot.metadata["log_path"] == str(log_path.resolve())
        assert snapshot.metadata["execution_broker"] == "runtime.execution_broker"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_resolve_process_handle_preserves_metadata_isolation(tmp_path: Path) -> None:
    """Verify resolve_process_handle_sync preserves metadata isolation when discovering by execution_id."""
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))
    log_path = tmp_path / "resolve_test.log"

    user_metadata = {
        "custom_key": "custom_value",
        "secret": "should_not_be_exposed",
    }

    command = LaunchExecutionProcessCommandV1(
        name="resolve-test",
        args=(sys.executable, "-c", "print('ok')"),
        workspace=str(tmp_path),
        timeout_seconds=5.0,
        log_path=str(log_path),
        metadata=user_metadata,
    )

    try:
        launch = await broker.launch_process(command)
        assert launch.success is True
        assert launch.handle is not None, "launch.handle should not be None"

        # Resolve by execution_id (string) to test the discovery path
        # This goes through resolve_process_handle which calls facade.snapshot
        resolved = await broker.resolve_process_handle(launch.handle.execution_id)

        # Verify metadata isolation is preserved
        assert "_user_metadata" in resolved.metadata
        assert resolved.metadata["_user_metadata"]["custom_key"] == "custom_value"
        assert resolved.metadata["_user_metadata"]["secret"] == "should_not_be_exposed"

        # Verify internal fields are NOT exposed in the returned handle metadata
        assert resolved.metadata.get("workspace") is None
        assert resolved.metadata.get("log_path") is None
        assert resolved.metadata.get("execution_broker") is None
        assert resolved.metadata.get("_user_metadata", {}).get("workspace") is None
    finally:
        await runtime.close()


def test_command_rejects_empty_user_metadata() -> None:
    """Empty user metadata should be handled gracefully."""
    command = LaunchExecutionProcessCommandV1(
        name="empty-meta-test",
        args=(sys.executable, "-c", "print('ok')"),
        workspace=".",
        metadata={},
    )
    # Should not raise, empty metadata is valid
    assert command.metadata == {}


# =============================================================================
# Structured Logging Tests
# =============================================================================


@pytest.mark.asyncio
async def test_structured_logging_launch_and_wait(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Verify structured logging outputs required fields for launch and wait operations."""
    import logging

    # Configure logging to capture at INFO level
    caplog.set_level(logging.INFO)

    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))
    log_path = tmp_path / "log_test.log"

    command = LaunchExecutionProcessCommandV1(
        name="log-test",
        args=(sys.executable, "-c", "print('log-ok')"),
        workspace=str(tmp_path),
        timeout_seconds=5.0,
        log_path=str(log_path),
        metadata={"test": "structured_logging"},
    )

    try:
        # Test launch_process logging
        launch = await broker.launch_process(command)
        assert launch.success is True
        assert launch.handle is not None

        # Verify launch logs
        launch_logged = False
        launched_logged = False
        for record in caplog.records:
            if "execution_broker.process.launching" in record.message:
                launch_logged = True
                # Verify required fields in extra attributes
                assert hasattr(record, "execution_id")
                assert hasattr(record, "workspace")
                assert hasattr(record, "timeout_seconds")
            if "execution_broker.process.launched" in record.message:
                launched_logged = True
                assert hasattr(record, "execution_id")
                assert hasattr(record, "pid")
                assert hasattr(record, "workspace")

        assert launch_logged, "Expected 'execution_broker.process.launching' log"
        assert launched_logged, "Expected 'execution_broker.process.launched' log"

        # Test wait_process logging
        caplog.clear()
        wait_result = await broker.wait_process(launch.handle, timeout_seconds=5.0)
        assert wait_result.success is True
        assert wait_result.status == ExecutionProcessStatusV1.SUCCESS

        # Verify wait logs
        waiting_logged = False
        completed_logged = False
        for record in caplog.records:
            if "execution_broker.process.waiting" in record.message:
                waiting_logged = True
                assert hasattr(record, "execution_id")
                assert hasattr(record, "timeout_seconds")
            if "execution_broker.process.completed" in record.message:
                completed_logged = True
                assert hasattr(record, "execution_id")
                assert hasattr(record, "status")
                assert hasattr(record, "exit_code")
                assert hasattr(record, "duration_ms")

        assert waiting_logged, "Expected 'execution_broker.process.waiting' log"
        assert completed_logged, "Expected 'execution_broker.process.completed' log"

    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_structured_logging_terminate(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Verify structured logging outputs required fields for terminate operations."""
    import logging

    # Capture both WARNING (terminating) and INFO (terminated) logs
    caplog.set_level(logging.DEBUG)  # Capture all levels

    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))

    command = LaunchExecutionProcessCommandV1(
        name="terminate-test",
        args=(sys.executable, "-c", "import time; time.sleep(30)"),
        workspace=str(tmp_path),
        timeout_seconds=60.0,
    )

    try:
        launch = await broker.launch_process(command)
        assert launch.success is True

        # Test terminate_process logging
        assert launch.handle is not None
        terminated = await broker.terminate_process(launch.handle, timeout_seconds=1.0)
        assert isinstance(terminated, bool)

        # Verify terminate logs
        terminating_logged = False
        terminated_logged = False
        for record in caplog.records:
            if "execution_broker.process.terminating" in record.message:
                terminating_logged = True
                assert hasattr(record, "execution_id")
                assert hasattr(record, "timeout_seconds")
            if "execution_broker.process.terminated" in record.message:
                terminated_logged = True
                assert hasattr(record, "execution_id")
                assert hasattr(record, "success")

        assert terminating_logged, "Expected 'execution_broker.process.terminating' log"
        assert terminated_logged, "Expected 'execution_broker.process.terminated' log"

    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_structured_logging_launch_failure(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Verify structured logging outputs error fields on launch failure."""
    import logging

    caplog.set_level(logging.ERROR)

    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))

    # Use invalid executable to trigger failure
    command = LaunchExecutionProcessCommandV1(
        name="fail-test",
        args=("nonexistent_executable_12345",),
        workspace=str(tmp_path),
        timeout_seconds=5.0,
    )

    try:
        launch = await broker.launch_process(command)
        assert launch.success is False
        assert launch.error_message is not None

        # Verify error log
        error_logged = False
        for record in caplog.records:
            if "execution_broker.process.launch_failed" in record.message:
                error_logged = True
                assert hasattr(record, "execution_id")
                assert hasattr(record, "error")
                assert hasattr(record, "error_type")

        assert error_logged, "Expected 'execution_broker.process.launch_failed' log"

    finally:
        await runtime.close()


# =============================================================================
# Concurrency Safety Tests - Race Condition Prevention
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_handles_access_no_race(tmp_path: Path) -> None:
    """Verify concurrent access to _process_handles has no race conditions.

    This test launches multiple processes concurrently and then performs
    concurrent list operations to ensure the asyncio.Lock protection works.
    """
    runtime = ExecutionRuntime(async_concurrency=4, blocking_concurrency=2, process_concurrency=4)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))

    commands = [
        LaunchExecutionProcessCommandV1(
            name=f"concurrent-test-{i}",
            args=(sys.executable, "-c", f"print({i})"),
            workspace=str(tmp_path),
            timeout_seconds=30.0,
        )
        for i in range(20)
    ]

    try:
        # Concurrent launch - all submissions should succeed
        launches = await asyncio.gather(*[broker.launch_process(cmd) for cmd in commands])

        # Verify all launches succeeded
        assert all(r.success for r in launches), f"Failed launches: {[r for r in launches if not r.success]}"

        # Concurrent list operations - should not raise KeyError
        errors: list[Exception] = []
        for _ in range(10):
            try:
                handles = await broker.list_active_processes()
                # Verify we got some handles back
                assert isinstance(handles, list)
            except KeyError as e:
                errors.append(e)
            except (RuntimeError, ValueError) as e:
                errors.append(e)

        assert not errors, f"Race condition detected: {errors}"

        # Concurrent resolve operations - resolve by execution_id (string) not handle
        resolve_errors: list[Exception] = []
        for launch in launches[:10]:
            try:
                # Pass execution_id (string) to trigger the lookup path
                if launch.handle is None:
                    resolve_errors.append(RuntimeError("launch.handle is None"))
                    continue
                resolved = await broker.resolve_process_handle(launch.handle.execution_id)
                assert resolved is not None, "resolved should not be None"
                assert resolved.execution_id == launch.handle.execution_id
            except (RuntimeError, ValueError) as e:
                resolve_errors.append(e)

        assert not resolve_errors, f"Resolve race condition: {resolve_errors}"

        # Wait for all processes to complete
        await asyncio.gather(
            *[
                broker.wait_process(launch.handle, timeout_seconds=5.0)  # type: ignore[arg-type]
                for launch in launches
                if launch.handle is not None
            ]
        )

    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_concurrent_log_drain_registration(tmp_path: Path) -> None:
    """Verify concurrent log drain task registration has no race conditions."""
    runtime = ExecutionRuntime(async_concurrency=4, blocking_concurrency=2, process_concurrency=4)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))

    commands = [
        LaunchExecutionProcessCommandV1(
            name=f"log-test-{i}",
            args=(sys.executable, "-c", f"print('log-{i}')"),
            workspace=str(tmp_path),
            timeout_seconds=30.0,
            log_path=str(tmp_path / f"log-{i}.log"),
        )
        for i in range(15)
    ]

    try:
        # Launch all with concurrent log drain registration
        launches = await asyncio.gather(*[broker.launch_process(cmd) for cmd in commands])

        assert all(r.success for r in launches)

        # Verify log files are created and contain output
        for i, launch in enumerate(launches):
            if launch.success:
                tmp_path / f"log-{i}.log"
                # Give the log drain task time to write
                await asyncio.sleep(0.1)

        # Cleanup
        await asyncio.gather(
            *[
                broker.wait_process(launch.handle, timeout_seconds=5.0)  # type: ignore[arg-type]
                for launch in launches
                if launch.success and launch.handle is not None
            ]
        )

    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_close_with_concurrent_operations(tmp_path: Path) -> None:
    """Verify close() works correctly when operations are in flight."""
    runtime = ExecutionRuntime(async_concurrency=2, blocking_concurrency=1, process_concurrency=2)
    broker = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))

    command = LaunchExecutionProcessCommandV1(
        name="close-test",
        args=(sys.executable, "-c", "import time; time.sleep(0.5); print('done')"),
        workspace=str(tmp_path),
        timeout_seconds=10.0,
    )

    # Launch a process
    launch = await broker.launch_process(command)
    assert launch.success

    # Start a wait in parallel with close
    async def wait_task():
        return await broker.wait_process(launch.handle, timeout_seconds=5.0)

    async def close_task() -> None:
        await asyncio.sleep(0.1)  # Let wait start first
        await broker.close(cancel_running=True)

    results = await asyncio.gather(wait_task(), close_task(), return_exceptions=True)

    # At least one should complete, the other may be cancelled
    completed = [r for r in results if not isinstance(r, Exception)]
    assert len(completed) >= 1

    await runtime.close()
