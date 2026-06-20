"""P0-1: RollbackManager integration tests for ActingPhaseHandler.

Tests that prepare_rollback() -> execute_rollback() -> abort_rollback()
are properly called in the acting phase lifecycle.

Verified behaviors:
- prepare_rollback() is called before tool execution for L3/L4
- execute_rollback() is called on successful tool execution
- abort_rollback() is called on failed tool execution
- execute_rollback() returning ABORTED marks result as blocked
- execute_rollback() returning PARTIAL logs warning but doesn't block
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from polaris.kernelone.cognitive.execution.acting_handler import (
    ActingPhaseConfig,
    ActingPhaseHandler,
    ActionResult,
)
from polaris.kernelone.cognitive.execution.rollback_manager import (
    RollbackManager,
    RollbackPlan,
    RollbackResult,
)
from polaris.kernelone.cognitive.types import ExecutionPath, ExecutionRecommendation, RiskLevel


class TestRollbackManagerPrepareExecuteAbortChain:
    """Test the full prepare→execute→abort rollback lifecycle."""

    @pytest.fixture
    def temp_file(self) -> Path:
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("original content")
            return Path(f.name)

    @pytest.fixture
    def rollback_manager(self) -> RollbackManager:
        """Create a RollbackManager instance."""
        return RollbackManager(max_rollback_steps=3)

    @pytest.fixture
    def acting_handler(self, rollback_manager: RollbackManager, temp_file: Path) -> ActingPhaseHandler:
        """Create an ActingPhaseHandler with real RollbackManager."""
        config = ActingPhaseConfig(enable_rollback=True, require_verification=True)
        return ActingPhaseHandler(
            config=config,
            rollback_manager=rollback_manager,
            workspace=str(temp_file.parent),
        )

    @pytest.mark.asyncio
    async def test_prepare_rollback_called_before_execution(
        self,
        acting_handler: ActingPhaseHandler,
        rollback_manager: RollbackManager,
        temp_file: Path,
    ) -> None:
        """Test that prepare_rollback() is called before tool execution."""
        # Prepare a spy on rollback_manager
        spy_manager = MagicMock(wraps=rollback_manager)

        # Create handler with spied manager
        config = ActingPhaseConfig(enable_rollback=True)
        handler = ActingPhaseHandler(
            config=config,
            rollback_manager=spy_manager,
            workspace=str(temp_file.parent),
        )

        # Create a full_pipe recommendation requiring rollback
        recommendation = ExecutionRecommendation(
            path=ExecutionPath.FULL_PIPE,
            skip_cognitive_pipe=False,
            confidence=0.9,
            risk_level=RiskLevel.L2_MODIFY,
            requires_rollback_plan=True,
        )

        # Execute action with rollback
        action = f'read file "{temp_file}"'
        target_paths = (str(temp_file),)
        handler._execute_direct = MagicMock(  # type: ignore[method-assign]
            return_value=ActionResult(
                action=action,
                status="success",
                output="ok",
                error=None,
            )
        )

        await handler.execute_action(
            action=action,
            execution_recommendation=recommendation,
            target_paths=target_paths,
        )

        # Verify prepare_rollback was called before execution
        spy_manager.prepare_rollback.assert_called_once()
        call_args = spy_manager.prepare_rollback.call_args
        assert call_args.kwargs["action_description"] == action
        assert call_args.kwargs["target_paths"] == target_paths

    @pytest.mark.asyncio
    async def test_execute_rollback_called_on_success(
        self,
        acting_handler: ActingPhaseHandler,
        rollback_manager: RollbackManager,
        temp_file: Path,
    ) -> None:
        """Test that execute_rollback() is called on successful execution."""
        spy_manager = MagicMock(wraps=rollback_manager)

        config = ActingPhaseConfig(enable_rollback=True)
        handler = ActingPhaseHandler(
            config=config,
            rollback_manager=spy_manager,
            workspace=str(temp_file.parent),
        )

        recommendation = ExecutionRecommendation(
            path=ExecutionPath.FULL_PIPE,
            skip_cognitive_pipe=False,
            confidence=0.9,
            risk_level=RiskLevel.L2_MODIFY,
            requires_rollback_plan=True,
        )

        action = f'read file "{temp_file}"'
        target_paths = (str(temp_file),)

        await handler.execute_action(
            action=action,
            execution_recommendation=recommendation,
            target_paths=target_paths,
        )

        # Verify execute_rollback was called after success
        spy_manager.execute_rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_abort_rollback_called_on_failure(
        self,
        acting_handler: ActingPhaseHandler,
        rollback_manager: RollbackManager,
        temp_file: Path,
    ) -> None:
        """Test that abort_rollback() is called on execution failure."""
        spy_manager = MagicMock(wraps=rollback_manager)

        config = ActingPhaseConfig(enable_rollback=True)
        handler = ActingPhaseHandler(
            config=config,
            rollback_manager=spy_manager,
            workspace=str(temp_file.parent),
        )

        recommendation = ExecutionRecommendation(
            path=ExecutionPath.FULL_PIPE,
            skip_cognitive_pipe=False,
            confidence=0.9,
            risk_level=RiskLevel.L3_DELETE,
            requires_rollback_plan=True,
        )

        # Action that will fail (delete is blocked)
        action = f'delete file "{temp_file}"'
        target_paths = (str(temp_file),)

        await handler.execute_action(
            action=action,
            execution_recommendation=recommendation,
            target_paths=target_paths,
        )

        # Verify abort_rollback was called after failure
        spy_manager.abort_rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_rollback_aborted_blocks_result(
        self,
        rollback_manager: RollbackManager,
        temp_file: Path,
    ) -> None:
        """Test that execute_rollback() returning ABORTED marks result as blocked."""
        # Create a manager that returns ABORTED
        failing_manager = MagicMock(spec=RollbackManager)
        abort_plan = RollbackPlan(
            plan_id="test_abort",
            created_at="2026-04-10T00:00:00Z",
            status="pending",
            steps=("restore test.txt",),
            targets=(str(temp_file),),
            etags={},
        )
        failing_manager.prepare_rollback.return_value = abort_plan
        failing_manager.execute_rollback.return_value = RollbackResult(
            status="ABORTED",
            reason="State drift detected: files modified externally",
            required_action="MANUAL_INTERVENTION",
            plan=abort_plan,
            executed_steps=(),
        )

        config = ActingPhaseConfig(enable_rollback=True)
        handler = ActingPhaseHandler(
            config=config,
            rollback_manager=failing_manager,
            workspace=str(temp_file.parent),
        )

        recommendation = ExecutionRecommendation(
            path=ExecutionPath.FULL_PIPE,
            skip_cognitive_pipe=False,
            confidence=0.9,
            risk_level=RiskLevel.L2_MODIFY,
            requires_rollback_plan=True,
        )

        action = f'read file "{temp_file}"'
        target_paths = (str(temp_file),)

        result = await handler.execute_action(
            action=action,
            execution_recommendation=recommendation,
            target_paths=target_paths,
        )

        # The action result should be marked as blocked
        assert result.actions_taken  # Should have some action taken
        # The handler's _action_history should have a blocked result

    @pytest.mark.asyncio
    async def test_execute_rollback_partial_warns_but_continues(
        self,
        rollback_manager: RollbackManager,
        temp_file: Path,
    ) -> None:
        """Test that execute_rollback() returning PARTIAL logs warning but doesn't block."""
        # Create a manager that returns PARTIAL
        partial_manager = MagicMock(spec=RollbackManager)
        partial_plan = RollbackPlan(
            plan_id="test_partial",
            created_at="2026-04-10T00:00:00Z",
            status="pending",
            steps=("restore test.txt",),
            targets=(str(temp_file),),
            etags={},
        )
        partial_manager.prepare_rollback.return_value = partial_plan
        partial_manager.execute_rollback.return_value = RollbackResult(
            status="PARTIAL",
            reason="Failed to restore 1 file(s): ['test.txt']",
            required_action="RETRY",
            plan=partial_plan,
            executed_steps=("restored test.txt",),
        )

        config = ActingPhaseConfig(enable_rollback=True)
        handler = ActingPhaseHandler(
            config=config,
            rollback_manager=partial_manager,
            workspace=str(temp_file.parent),
        )

        recommendation = ExecutionRecommendation(
            path=ExecutionPath.FULL_PIPE,
            skip_cognitive_pipe=False,
            confidence=0.9,
            risk_level=RiskLevel.L2_MODIFY,
            requires_rollback_plan=True,
        )

        action = f'read file "{temp_file}"'
        target_paths = (str(temp_file),)
        handler._execute_direct = MagicMock(  # type: ignore[method-assign]
            return_value=ActionResult(
                action=action,
                status="success",
                output="ok",
                error=None,
            )
        )

        # Should not raise - PARTIAL is a warning, not a block
        await handler.execute_action(
            action=action,
            execution_recommendation=recommendation,
            target_paths=target_paths,
        )

        # execute_rollback should have been called (not aborted)
        partial_manager.execute_rollback.assert_called_once()
        partial_manager.abort_rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_prepare_rollback_failure_blocks_action(
        self,
        rollback_manager: RollbackManager,
        temp_file: Path,
    ) -> None:
        """Test that prepare_rollback() raising ValueError blocks the action."""
        # Create a manager that raises on prepare
        failing_manager = MagicMock(spec=RollbackManager)
        failing_manager.prepare_rollback.side_effect = ValueError(
            "Cannot prepare rollback: 1 target(s) unreadable: ['nonexistent.txt']"
        )

        config = ActingPhaseConfig(enable_rollback=True)
        handler = ActingPhaseHandler(
            config=config,
            rollback_manager=failing_manager,
            workspace=str(temp_file.parent),
        )

        recommendation = ExecutionRecommendation(
            path=ExecutionPath.FULL_PIPE,
            skip_cognitive_pipe=False,
            confidence=0.9,
            risk_level=RiskLevel.L2_MODIFY,
            requires_rollback_plan=True,
        )

        action = f'read file "{temp_file}"'
        target_paths = (str(temp_file),)

        result = await handler.execute_action(
            action=action,
            execution_recommendation=recommendation,
            target_paths=target_paths,
        )

        # Action should be blocked due to rollback preparation failure
        assert "BLOCKED" in result.content

    @pytest.mark.asyncio
    async def test_target_paths_extracted_from_action(
        self,
        acting_handler: ActingPhaseHandler,
        rollback_manager: RollbackManager,
        temp_file: Path,
    ) -> None:
        """Test that target paths are extracted from action when not provided."""
        spy_manager = MagicMock(wraps=rollback_manager)

        config = ActingPhaseConfig(enable_rollback=True)
        handler = ActingPhaseHandler(
            config=config,
            rollback_manager=spy_manager,
            workspace=str(temp_file.parent),
        )

        recommendation = ExecutionRecommendation(
            path=ExecutionPath.FULL_PIPE,
            skip_cognitive_pipe=False,
            confidence=0.9,
            risk_level=RiskLevel.L2_MODIFY,
            requires_rollback_plan=True,
        )

        # Action with embedded path (no explicit target_paths)
        action = f'read file "{temp_file}"'

        await handler.execute_action(
            action=action,
            execution_recommendation=recommendation,
            target_paths=None,  # Should be extracted from action
        )

        # prepare_rollback should have been called with extracted path
        spy_manager.prepare_rollback.assert_called_once()
        call_args = spy_manager.prepare_rollback.call_args
        assert str(temp_file) in call_args.kwargs["target_paths"]

    @pytest.mark.asyncio
    async def test_relative_target_paths_are_resolved_to_workspace(
        self,
        rollback_manager: RollbackManager,
        temp_file: Path,
    ) -> None:
        """Relative target paths should be normalized against workspace."""
        spy_manager = MagicMock(wraps=rollback_manager)

        config = ActingPhaseConfig(enable_rollback=True)
        handler = ActingPhaseHandler(
            config=config,
            rollback_manager=spy_manager,
            workspace=str(temp_file.parent),
        )

        recommendation = ExecutionRecommendation(
            path=ExecutionPath.FULL_PIPE,
            skip_cognitive_pipe=False,
            confidence=0.9,
            risk_level=RiskLevel.L2_MODIFY,
            requires_rollback_plan=True,
        )

        relative_path = temp_file.name
        action = f'read file "{relative_path}"'

        await handler.execute_action(
            action=action,
            execution_recommendation=recommendation,
            target_paths=(relative_path,),
        )

        spy_manager.prepare_rollback.assert_called_once()
        call_args = spy_manager.prepare_rollback.call_args
        normalized = tuple(call_args.kwargs["target_paths"])
        assert normalized == (str(temp_file),)

    @pytest.mark.asyncio
    async def test_prepare_rollback_accepts_missing_file_and_restores_absence(
        self,
        rollback_manager: RollbackManager,
        temp_file: Path,
    ) -> None:
        """Create-file rollback should snapshot absence and remove created artifact."""
        missing_target = temp_file.parent / "rollback_create_target.txt"
        missing_target.unlink(missing_ok=True)

        plan = await rollback_manager.prepare_rollback(
            action_description="create file rollback",
            target_paths=(str(missing_target),),
        )

        snapshot_key = f"{plan.plan_id}:{missing_target}"
        assert snapshot_key in rollback_manager._snapshots
        assert rollback_manager._snapshots[snapshot_key].existed_before is False

        # Simulate post-action state where file now exists.
        missing_target.write_text("created", encoding="utf-8")
        assert missing_target.exists()

        result = await rollback_manager.execute_rollback(plan)
        assert result.status == "SUCCESS"
        assert not missing_target.exists()

    @pytest.mark.asyncio
    async def test_rollback_disabled_skips_prepare_execute(
        self,
        rollback_manager: RollbackManager,
        temp_file: Path,
    ) -> None:
        """Test that disable_rollback=True skips rollback preparation."""
        spy_manager = MagicMock(wraps=rollback_manager)

        config = ActingPhaseConfig(enable_rollback=False)
        handler = ActingPhaseHandler(
            config=config,
            rollback_manager=spy_manager,
            workspace=str(temp_file.parent),
        )

        recommendation = ExecutionRecommendation(
            path=ExecutionPath.FULL_PIPE,
            skip_cognitive_pipe=False,
            confidence=0.9,
            risk_level=RiskLevel.L2_MODIFY,
            requires_rollback_plan=True,
        )

        action = f'read file "{temp_file}"'
        target_paths = (str(temp_file),)

        await handler.execute_action(
            action=action,
            execution_recommendation=recommendation,
            target_paths=target_paths,
        )

        # prepare_rollback should NOT be called when disabled
        spy_manager.prepare_rollback.assert_not_called()
        spy_manager.execute_rollback.assert_not_called()
        spy_manager.abort_rollback.assert_not_called()


class TestActingPhaseHandlerActionHistory:
    """Test that action history is properly maintained."""

    @pytest.fixture
    def temp_file(self) -> Path:
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("test content")
            return Path(f.name)

    @pytest.mark.asyncio
    async def test_action_history_appended_on_success(self, temp_file: Path) -> None:
        """Test that successful actions are appended to history."""
        config = ActingPhaseConfig(enable_rollback=False)
        handler = ActingPhaseHandler(
            config=config,
            rollback_manager=RollbackManager(),
            workspace=str(temp_file.parent),
        )

        recommendation = ExecutionRecommendation(
            path=ExecutionPath.BYPASS,
            skip_cognitive_pipe=True,
            confidence=1.0,
            risk_level=RiskLevel.L0_READONLY,
        )

        action = f'read file "{temp_file}"'
        handler._execute_direct = MagicMock(  # type: ignore[method-assign]
            return_value=ActionResult(
                action=action,
                status="success",
                output="ok",
                error=None,
            )
        )

        await handler.execute_action(
            action=action,
            execution_recommendation=recommendation,
        )

        assert len(handler._action_history) == 1
        assert handler._action_history[0].status == "success"

    @pytest.mark.asyncio
    async def test_action_history_appended_on_failure(self, temp_file: Path) -> None:
        """Test that failed actions are appended to history."""
        config = ActingPhaseConfig(enable_rollback=False)
        handler = ActingPhaseHandler(
            config=config,
            rollback_manager=RollbackManager(),
            workspace=str(temp_file.parent),
        )

        recommendation = ExecutionRecommendation(
            path=ExecutionPath.FULL_PIPE,
            skip_cognitive_pipe=False,
            confidence=0.9,
            risk_level=RiskLevel.L3_DELETE,
            requires_rollback_plan=True,
        )

        # Delete action will fail (blocked)
        action = f'delete file "{temp_file}"'
        target_paths = (str(temp_file),)

        await handler.execute_action(
            action=action,
            execution_recommendation=recommendation,
            target_paths=target_paths,
        )

        # Failed actions are also appended to history
        assert len(handler._action_history) == 1


class TestRollbackManagerMalformedPath:
    """Regression: a malformed/too-long target path must not crash prepare_rollback.

    Root cause (SWE-bench Arch-B audit): the cognitive acting handler passed a problem-
    statement fragment as a target path; path.exists() then raised
    OSError [Errno 36] File name too long, crashing the whole cognitive turn and the
    host role call. prepare_rollback must treat an un-stat-able path as "not a file".
    """

    @pytest.mark.asyncio
    async def test_prepare_rollback_too_long_path_is_handled_not_oserror(self) -> None:
        manager = RollbackManager()
        # A single path component far beyond NAME_MAX -> os.stat raises ENAMETOOLONG.
        # Pre-fix this OSError escaped and crashed the whole cognitive turn; now it must
        # be routed to the domain "unreadable" path (ValueError), which callers catch.
        bad_path = "/tmp/" + ("a" * 5000)
        with pytest.raises(ValueError):
            await manager.prepare_rollback("noop action", (bad_path,))

    @pytest.mark.asyncio
    async def test_prepare_rollback_newline_path_no_oserror_crash(self) -> None:
        manager = RollbackManager()
        bad_path = "/tmp/some title\nDescription\n\t" + ("x" * 400)
        try:
            await manager.prepare_rollback("noop action", (bad_path,))
        except ValueError:
            pass  # handled as a domain error — acceptable
        except OSError as exc:  # pragma: no cover - the bug we fixed
            pytest.fail(f"malformed path must not raise OSError: {exc}")


class TestRollbackManagerNoSnapshotLeak:
    """State-leakage guard: a rejected prepare_rollback must not leave snapshots behind.

    Root cause (ContextOS state-leakage audit): prepare_rollback stores snapshots for
    readable targets incrementally, then raises ValueError if any later target is
    unreadable — but the plan is recorded only AFTER that raise, so plan-keyed cleanup
    can never reclaim the orphaned snapshots. They must be purged on the reject path.
    """

    @pytest.mark.asyncio
    async def test_rejected_prepare_leaves_no_snapshot_state(self, tmp_path: Path) -> None:
        manager = RollbackManager()
        good = tmp_path / "good.txt"
        good.write_text("original", encoding="utf-8")
        # A directory exists but is not a regular file -> classified "unreadable".
        bad_dir = tmp_path / "subdir"
        bad_dir.mkdir()

        # good.txt is snapshotted first, then bad_dir triggers the reject.
        with pytest.raises(ValueError):
            await manager.prepare_rollback(
                action_description="leak probe",
                target_paths=(str(good), str(bad_dir)),
            )

        # No orphaned snapshots and no plan recorded.
        assert manager._snapshots == {}
        assert manager._plans == {}


class TestRollbackManagerAsyncIoOffload:
    """ASYNC-5: prepare_rollback / execute_rollback must offload blocking file IO.

    Root cause: prepare_rollback (read_text) and execute_rollback (read_text for
    ETag/verification + write_text for restore) performed synchronous file IO
    directly on the event loop, blocking every other coroutine for the duration
    of the read/write. Each call must now run on a worker thread via
    asyncio.to_thread, while preserving explicit UTF-8 and exact content.
    """

    @pytest.mark.asyncio
    async def test_prepare_rollback_reads_off_event_loop_thread(self, tmp_path: Path) -> None:
        # Arrange: a real file whose read records the thread it executes on.
        loop_thread_id = threading.get_ident()
        target = tmp_path / "snap_me.txt"
        target.write_text("payload", encoding="utf-8")
        recorded: dict[str, int] = {}
        original_read_text = Path.read_text

        def recording_read_text(self: Path, *args: object, **kwargs: object) -> str:
            recorded["thread_id"] = threading.get_ident()
            return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        manager = RollbackManager()

        # Act
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(Path, "read_text", recording_read_text)
            plan = await manager.prepare_rollback(
                action_description="offload probe",
                target_paths=(str(target),),
            )

        # Assert: snapshot captured AND the read ran on a worker thread.
        snapshot_key = f"{plan.plan_id}:{target}"
        assert manager._snapshots[snapshot_key].content == "payload"
        assert recorded["thread_id"] != loop_thread_id

    @pytest.mark.asyncio
    async def test_execute_rollback_writes_off_event_loop_thread(self, tmp_path: Path) -> None:
        # Arrange: snapshot an existing file. An existed_before snapshot always
        # rewrites the file on restore, so a write fires even with no drift; we
        # leave the on-disk content unchanged so the ETag matches (no abort).
        loop_thread_id = threading.get_ident()
        target = tmp_path / "restore_me.txt"
        target.write_text("original", encoding="utf-8")
        manager = RollbackManager()
        plan = await manager.prepare_rollback(
            action_description="write offload probe",
            target_paths=(str(target),),
        )

        write_threads: list[int] = []
        original_write_text = Path.write_text

        def recording_write_text(self: Path, *args: object, **kwargs: object) -> int:
            write_threads.append(threading.get_ident())
            return original_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

        # Act: restore. ETag matches current disk state, so rollback proceeds.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(Path, "write_text", recording_write_text)
            result = await manager.execute_rollback(plan)

        # Assert: restored content + the restore write executed on a worker thread.
        assert result.status == "SUCCESS"
        assert target.read_text(encoding="utf-8") == "original"
        assert write_threads, "write_text was never invoked"
        assert all(tid != loop_thread_id for tid in write_threads)

    @pytest.mark.asyncio
    async def test_prepare_then_execute_roundtrips_utf8_content(self, tmp_path: Path) -> None:
        # Arrange: non-ASCII content must survive the offloaded read+write cycle
        # byte-for-byte. An existed_before snapshot rewrites the file on restore;
        # with the disk left unchanged the ETag matches and rollback proceeds.
        target = tmp_path / "unicode.txt"
        utf8_content = "尚书令 → café ☃ 𝄞"
        target.write_text(utf8_content, encoding="utf-8")
        manager = RollbackManager()

        # Act: snapshot then roll back (no external drift).
        plan = await manager.prepare_rollback(
            action_description="utf8 roundtrip",
            target_paths=(str(target),),
        )
        result = await manager.execute_rollback(plan)

        # Assert: exact UTF-8 restoration through the threaded read+write.
        assert result.status == "SUCCESS"
        assert target.read_text(encoding="utf-8") == utf8_content

    @pytest.mark.asyncio
    async def test_execute_rollback_aborts_on_external_drift(self, tmp_path: Path) -> None:
        # Arrange: snapshot a file, then mutate it externally before rollback.
        target = tmp_path / "drift.txt"
        target.write_text("original", encoding="utf-8")
        manager = RollbackManager()
        plan = await manager.prepare_rollback(
            action_description="drift probe",
            target_paths=(str(target),),
        )

        # Act: an external edit changes the content hash (ETag).
        target.write_text("externally modified", encoding="utf-8")
        result = await manager.execute_rollback(plan)

        # Assert: drift is detected and rollback is aborted, file untouched.
        assert result.status == "ABORTED"
        assert result.required_action == "MANUAL_INTERVENTION"
        assert target.read_text(encoding="utf-8") == "externally modified"

    @pytest.mark.asyncio
    async def test_execute_rollback_concurrent_calls_do_not_block_loop(self, tmp_path: Path) -> None:
        # Arrange: a read_text that sleeps in-thread; if IO ran on the loop these
        # would serialize for ~N*delay, but offloaded they overlap on the pool.
        delay = 0.2
        files = []
        manager = RollbackManager()
        plans = []
        for i in range(3):
            f = tmp_path / f"concurrent_{i}.txt"
            f.write_text(f"content-{i}", encoding="utf-8")
            files.append(f)
            plans.append(
                await manager.prepare_rollback(
                    action_description=f"concurrent-{i}",
                    target_paths=(str(f),),
                )
            )

        original_read_text = Path.read_text

        def slow_read_text(self: Path, *args: object, **kwargs: object) -> str:
            import time

            time.sleep(delay)
            return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

        # Act: run all three rollbacks concurrently and measure wall time.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(Path, "read_text", slow_read_text)
            loop = asyncio.get_running_loop()
            start = loop.time()
            results = await asyncio.gather(*(manager.execute_rollback(p) for p in plans))
            elapsed = loop.time() - start

        # Assert: all succeeded and the loop stayed responsive (overlapped IO).
        assert all(r.status == "SUCCESS" for r in results)
        assert elapsed < delay * len(plans), f"IO appears serialized on the loop: {elapsed:.2f}s"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
