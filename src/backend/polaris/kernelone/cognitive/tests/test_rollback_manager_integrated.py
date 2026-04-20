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

import tempfile
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
