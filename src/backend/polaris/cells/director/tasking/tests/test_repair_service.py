"""Tests for repair_service module."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    pass


class TestRepairResult:
    """Tests for RepairResult dataclass."""

    def test_repair_result_creation(self) -> None:
        """Test basic RepairResult creation."""
        from polaris.cells.director.tasking.internal.repair_service import RepairResult

        result = RepairResult(
            success=True,
            iteration=1,
            changes_made=["file1.py", "file2.py"],
        )
        assert result.success is True
        assert result.iteration == 1
        assert result.changes_made == ["file1.py", "file2.py"]
        assert result.error_message == ""

    def test_repair_result_failure(self) -> None:
        """Test RepairResult with failure."""
        from polaris.cells.director.tasking.internal.repair_service import RepairResult

        result = RepairResult(
            success=False,
            iteration=2,
            error_message="Syntax error in file1.py",
        )
        assert result.success is False
        assert result.iteration == 2
        assert result.error_message == "Syntax error in file1.py"

    def test_repair_result_to_dict(self) -> None:
        """Test RepairResult.to_dict() method."""
        from polaris.cells.director.tasking.internal.repair_service import RepairResult

        result = RepairResult(
            success=True,
            iteration=1,
            changes_made=["file.py"],
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["iteration"] == 1
        assert d["changes_made"] == ["file.py"]
        assert d["error_message"] == ""
        assert d["has_evidence"] is False


class TestRepairContext:
    """Tests for RepairContext dataclass."""

    def test_repair_context_defaults(self) -> None:
        """Test RepairContext default values."""
        from polaris.cells.director.tasking.internal.repair_service import RepairContext

        ctx = RepairContext(task_id="task-1")
        assert ctx.task_id == "task-1"
        assert ctx.build_round == 0
        assert ctx.max_build_rounds == 4
        assert ctx.stall_rounds == 0
        assert ctx.stall_threshold == 2
        assert ctx.previous_missing_targets == []
        assert ctx.previous_unresolved_imports == []

    def test_repair_context_custom_values(self) -> None:
        """Test RepairContext with custom values."""
        from polaris.cells.director.tasking.internal.repair_service import RepairContext

        ctx = RepairContext(
            task_id="task-2",
            build_round=2,
            max_build_rounds=6,
            stall_rounds=3,
            target_files=["a.py", "b.py"],
            original_plan="Fix the bug",
        )
        assert ctx.task_id == "task-2"
        assert ctx.build_round == 2
        assert ctx.max_build_rounds == 6
        assert ctx.stall_rounds == 3
        assert ctx.target_files == ["a.py", "b.py"]


class TestRepairService:
    """Tests for RepairService class."""

    def test_repair_service_initialization(self) -> None:
        """Test RepairService initialization."""
        from polaris.cells.director.tasking.internal.repair_service import RepairService

        service = RepairService()
        assert service._repair_executor is None
        assert service._repair_history == []

    def test_repair_service_with_executor(self) -> None:
        """Test RepairService with custom executor."""
        from polaris.cells.director.tasking.internal.repair_service import RepairService

        def executor(brief: str, files: list[str]) -> tuple[list[str], str | None]:
            return ["fixed.py"], None

        service = RepairService(repair_executor=executor)
        assert service._repair_executor is not None

    def test_should_attempt_repair_audit_passed(self) -> None:
        """Test should_attempt_repair when audit passed."""
        from polaris.cells.director.tasking.internal.repair_service import RepairContext, RepairService

        service = RepairService()
        mock_soft_check = MagicMock()
        mock_soft_check.missing_targets = []
        mock_soft_check.unresolved_imports = []
        mock_progress = MagicMock()
        mock_progress.is_stalled = False
        ctx = RepairContext(task_id="task-1")

        should_repair, reason = service.should_attempt_repair(
            audit_accepted=True,
            soft_check=mock_soft_check,
            progress=mock_progress,
            context=ctx,
        )

        assert should_repair is False
        assert "no repair needed" in reason.lower()

    def test_should_attempt_repair_budget_exhausted(self) -> None:
        """Test should_attempt_repair when build budget exhausted."""
        from polaris.cells.director.tasking.internal.repair_service import RepairContext, RepairService

        service = RepairService()
        mock_soft_check = MagicMock()
        mock_soft_check.missing_targets = []
        mock_soft_check.unresolved_imports = []
        mock_progress = MagicMock()
        ctx = RepairContext(task_id="task-1", build_round=4, max_build_rounds=4)

        should_repair, reason = service.should_attempt_repair(
            audit_accepted=False,
            soft_check=mock_soft_check,
            progress=mock_progress,
            context=ctx,
        )

        assert should_repair is False
        assert "budget exhausted" in reason.lower()

    def test_should_attempt_repair_stalled(self) -> None:
        """Test should_attempt_repair when progress is stalled."""
        from polaris.cells.director.tasking.internal.repair_service import RepairContext, RepairService

        service = RepairService()
        mock_soft_check = MagicMock()
        mock_soft_check.missing_targets = []
        mock_soft_check.unresolved_imports = []
        mock_progress = MagicMock()
        mock_progress.is_stalled = True
        ctx = RepairContext(task_id="task-1", build_round=3, stall_rounds=3, stall_threshold=2)

        should_repair, reason = service.should_attempt_repair(
            audit_accepted=False,
            soft_check=mock_soft_check,
            progress=mock_progress,
            context=ctx,
        )

        assert should_repair is False
        assert "stalled" in reason.lower()

    def test_should_attempt_repair_missing_targets(self) -> None:
        """Test should_attempt_repair when there are missing targets."""
        from polaris.cells.director.tasking.internal.repair_service import RepairContext, RepairService

        service = RepairService()
        mock_soft_check = MagicMock()
        mock_soft_check.missing_targets = ["missing.py"]
        mock_soft_check.unresolved_imports = []
        mock_progress = MagicMock()
        mock_progress.is_stalled = False
        ctx = RepairContext(task_id="task-1")

        should_repair, reason = service.should_attempt_repair(
            audit_accepted=False,
            soft_check=mock_soft_check,
            progress=mock_progress,
            context=ctx,
        )

        assert should_repair is True
        assert "missing" in reason.lower()

    def test_should_attempt_repair_unresolved_imports(self) -> None:
        """Test should_attempt_repair when there are unresolved imports."""
        from polaris.cells.director.tasking.internal.repair_service import RepairContext, RepairService

        service = RepairService()
        mock_soft_check = MagicMock()
        mock_soft_check.missing_targets = []
        mock_soft_check.unresolved_imports = ["numpy", "pandas"]
        mock_progress = MagicMock()
        mock_progress.is_stalled = False
        ctx = RepairContext(task_id="task-1")

        should_repair, reason = service.should_attempt_repair(
            audit_accepted=False,
            soft_check=mock_soft_check,
            progress=mock_progress,
            context=ctx,
        )

        assert should_repair is True
        assert "import" in reason.lower()

    @pytest.mark.asyncio
    async def test_run_repair_no_executor(self) -> None:
        """Test run_repair when no executor is configured."""
        from polaris.cells.director.tasking.internal.repair_service import RepairContext, RepairService

        service = RepairService()
        ctx = RepairContext(task_id="task-1")

        result = await service.run_repair(
            qa_feedback="Fix the bug",
            context=ctx,
            iteration=1,
        )

        assert result.success is False
        assert "no repair executor configured" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_run_repair_with_executor_success(self) -> None:
        """Test run_repair with successful executor."""
        from polaris.cells.director.tasking.internal.repair_service import RepairContext, RepairService

        def executor(brief: str, files: list[str]) -> tuple[list[str], str | None]:
            return ["file1.py", "file2.py"], None

        service = RepairService(repair_executor=executor)
        ctx = RepairContext(task_id="task-1", target_files=["file1.py", "file2.py"])

        result = await service.run_repair(
            qa_feedback="Fix import errors",
            context=ctx,
            iteration=1,
        )

        assert result.success is True
        assert result.changes_made == ["file1.py", "file2.py"]
        assert result.iteration == 1

    @pytest.mark.asyncio
    async def test_run_repair_with_executor_failure(self) -> None:
        """Test run_repair with failing executor."""
        from polaris.cells.director.tasking.internal.repair_service import RepairContext, RepairService

        def executor(brief: str, files: list[str]) -> tuple[list[str], str | None]:
            return [], "Syntax error"

        service = RepairService(repair_executor=executor)
        ctx = RepairContext(task_id="task-1", target_files=["file.py"])

        result = await service.run_repair(
            qa_feedback="Fix syntax",
            context=ctx,
            iteration=1,
        )

        assert result.success is False
        assert "Syntax error" in result.error_message

    @pytest.mark.asyncio
    async def test_run_repair_loop_success_first_attempt(self) -> None:
        """Test run_repair_loop succeeds on first attempt."""
        from polaris.cells.director.tasking.internal.repair_service import RepairContext, RepairService

        def executor(brief: str, files: list[str]) -> tuple[list[str], str | None]:
            return ["fixed.py"], None

        service = RepairService(repair_executor=executor)
        # Context needs target_files for repair scope to be non-empty
        ctx = RepairContext(task_id="task-1", target_files=["file.py"])

        success, results, message = await service.run_repair_loop(
            qa_feedback="Fix it",
            context=ctx,
            max_repair_rounds=3,
        )

        assert success is True
        assert len(results) == 1
        assert "succeeded after 1" in message

    @pytest.mark.asyncio
    async def test_run_repair_loop_fails_all_attempts(self) -> None:
        """Test run_repair_loop fails all attempts."""
        from polaris.cells.director.tasking.internal.repair_service import RepairContext, RepairService

        def executor(brief: str, files: list[str]) -> tuple[list[str], str | None]:
            return [], "Still broken"

        service = RepairService(repair_executor=executor)
        ctx = RepairContext(task_id="task-1")

        success, results, message = await service.run_repair_loop(
            qa_feedback="Fix it",
            context=ctx,
            max_repair_rounds=2,
        )

        assert success is False
        assert len(results) == 2
        assert "failed after 2" in message

    def test_extract_missing_files(self) -> None:
        """Test _extract_missing_files method."""
        from polaris.cells.director.tasking.internal.repair_service import RepairService

        service = RepairService()
        qa_output = """
        The following files are missing:
        - src/utils.py
        - tests/test_main.py

        Check imports in:
        - src/main.py
        - src/app.ts
        """

        files = service._extract_missing_files(qa_output)
        # The regex matches .py and .ts files
        assert "src/utils.py" in files
        assert "tests/test_main.py" in files
        assert "src/main.py" in files

    def test_extract_missing_files_empty(self) -> None:
        """Test _extract_missing_files with empty input."""
        from polaris.cells.director.tasking.internal.repair_service import RepairService

        service = RepairService()
        files = service._extract_missing_files("")
        assert files == []

    def test_compute_repair_scope(self) -> None:
        """Test _compute_repair_scope method."""
        from polaris.cells.director.tasking.internal.repair_service import RepairService

        service = RepairService()
        scope = service._compute_repair_scope(
            target_files=["a.py", "b.py"],
            missing_files=["c.py", "a.py"],  # a.py is duplicate
        )

        assert "a.py" in scope
        assert "b.py" in scope
        assert "c.py" in scope

    def test_build_repair_brief(self) -> None:
        """Test _build_repair_brief method."""
        from polaris.cells.director.tasking.internal.repair_service import RepairService

        service = RepairService()
        brief = service._build_repair_brief(
            original_plan="Implement feature X",
            qa_feedback="Missing function",
            repair_scope=["a.py", "b.py"],
        )

        assert "Implement feature X" in brief
        assert "Missing function" in brief
        assert "a.py" in brief
        assert "b.py" in brief

    def test_get_repair_history(self) -> None:
        """Test get_repair_history method."""
        from polaris.cells.director.tasking.internal.repair_service import RepairService

        service = RepairService()
        service._repair_history = [
            MagicMock(success=True, iteration=1),
            MagicMock(success=False, iteration=2),
        ]

        history = service.get_repair_history()
        assert len(history) == 2

    def test_get_stats_empty(self) -> None:
        """Test get_stats with no history."""
        from polaris.cells.director.tasking.internal.repair_service import RepairService

        service = RepairService()
        stats = service.get_stats()

        assert stats["total"] == 0
        assert stats["successful"] == 0
        assert stats["failed"] == 0
        assert stats["success_rate"] == 0.0

    def test_get_stats_with_history(self) -> None:
        """Test get_stats with repair history."""
        from polaris.cells.director.tasking.internal.repair_service import RepairResult, RepairService

        service = RepairService()
        service._repair_history = [
            RepairResult(success=True, iteration=1),
            RepairResult(success=True, iteration=2),
            RepairResult(success=False, iteration=3),
        ]

        stats = service.get_stats()

        assert stats["total"] == 3
        assert stats["successful"] == 2
        assert stats["failed"] == 1
        assert stats["success_rate"] == pytest.approx(2 / 3)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
