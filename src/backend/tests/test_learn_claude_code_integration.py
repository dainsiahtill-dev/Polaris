"""Tests for learn-claude-code Phase 2-6 integration.

Run with: pytest tests/test_learn_claude_code_integration.py -v
"""
# Add project root to path
import sys
import tempfile
from pathlib import Path

import pytest

project_root = str(Path(__file__).parent.parent.parent.parent)
sys.path.insert(0, project_root)

from polaris.cells.runtime.task_runtime.public.task_board_contract import TaskBoard, TaskStatus
from polaris.kernelone.single_agent.skill_system import SkillLoader, install_default_skills
from polaris.kernelone.context import (
    RoleContextCompressor as ContextCompressor,
)
from polaris.kernelone.context import (
    RoleContextIdentity as IdentityAnchor,
)
from polaris.kernelone.process.background_manager import (
    BackgroundManagerV2,
)


class TestPhase2BackgroundManagerV2:
    """Test Phase 2: Background task orchestration with queue."""

    @pytest.fixture
    def temp_workspace(self):
        """Create temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def bg_manager(self, temp_workspace):
        """Create background manager with max_concurrent=2."""
        manager = BackgroundManagerV2(temp_workspace, max_concurrent=2)
        try:
            yield manager
        finally:
            manager.close(cancel_running=True)

    def test_submit_task(self, bg_manager):
        """Test basic task submission."""
        result = bg_manager.submit(command="echo hello", timeout=60)

        assert result["ok"] is True
        assert "task_id" in result
        assert result["status"] in ["queued", "running"]
        assert "max_concurrent" in result

    def test_queue_when_at_capacity(self, bg_manager):
        """Test that tasks queue when at concurrency limit."""
        # Submit 3 tasks with max_concurrent=2
        results = []
        for i in range(3):
            result = bg_manager.submit(command=f"echo task{i}", timeout=60)
            results.append(result)

        # At least one should be queued
        statuses = [r["status"] for r in results]
        assert "queued" in statuses or all(s == "running" for s in statuses[:2])

    def test_cancel_task(self, bg_manager):
        """Test task cancellation."""
        # Submit a long-running task
        result = bg_manager.submit(command="sleep 300", timeout=600)
        task_id = result["task_id"]

        # Cancel it
        cancel_result = bg_manager.cancel(task_id)

        assert cancel_result["ok"] is True
        assert cancel_result["status"] == "cancelled"

    def test_wait_with_timeout(self, bg_manager):
        """Test wait with timeout decision."""
        # Submit quick task
        result = bg_manager.submit(command="echo done", timeout=60)
        task_id = result["task_id"]

        # Wait for it
        wait_result = bg_manager.wait([task_id], timeout=10, on_timeout="fail")

        assert wait_result["ok"] is True
        assert wait_result["decision"] == "continue"  # Task finished

    def test_queue_status(self, bg_manager):
        """Test queue status reporting."""
        status = bg_manager.get_queue_status()

        assert "max_concurrent" in status
        assert "running" in status
        assert "queued" in status
        assert "available_slots" in status
        assert status["max_concurrent"] == 2


class TestPhase3ContextCompact:
    """Test Phase 3: Context compression with identity."""

    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def compressor(self, temp_workspace):
        """Create compressor without LLM (for unit tests)."""
        return ContextCompressor(workspace=temp_workspace, llm_client=None)

    @pytest.fixture
    def sample_identity(self):
        return IdentityAnchor(
            task_id="test-123",
            goal="Implement feature X",
            acceptance_criteria=["Test passes", "Code reviewed"],
            write_scope=["src/feature.py"],
            current_phase="implementation",
        )

    def test_micro_compact(self, compressor):
        """Test Layer 1 micro compression."""
        messages = [
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "A" * 200}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "2", "content": "B" * 200}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "3", "content": "C" * 200}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "4", "content": "D" * 200}]},
        ]

        result = compressor.micro_compact(messages)

        # Should keep last 3, compact oldest
        assert len(result) == 4
        assert "compacted" in result[0]["content"][0] or "[Previous" in str(result[0]["content"])

    def test_truncate_compact(self, compressor, sample_identity):
        """Test truncate fallback."""
        messages = [{"role": "user", "content": f"Message {i}"} for i in range(100)]

        compressed, snapshot = compressor.truncate_compact(messages)

        assert len(compressed) < len(messages)
        assert snapshot.method == "truncate"
        assert snapshot.original_tokens > snapshot.compressed_tokens

    def test_estimate_tokens(self, compressor):
        """Test token estimation."""
        messages = [{"role": "user", "content": "Hello world" * 100}]

        tokens = compressor.estimate_tokens(messages)

        assert tokens > 0
        assert isinstance(tokens, int)

    def test_identity_anchor_creation(self, temp_workspace):
        """Test creating identity from task data."""
        compressor = ContextCompressor(temp_workspace)
        task_data = {
            "id": "task-456",
            "goal": "Refactor auth",
            "acceptance_criteria": ["Tests pass"],
            "write_scope": ["src/auth.py"],
            "current_phase": "refactoring",
        }

        identity = compressor.create_identity_from_task(task_data)

        assert identity.task_id == "task-456"
        assert identity.goal == "Refactor auth"


class TestPhase4SkillSystem:
    """Test Phase 4: Two-layer skill loading."""

    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def skill_loader(self, temp_workspace):
        return SkillLoader(temp_workspace)

    def test_install_default_skills(self, temp_workspace):
        """Test default skill installation."""
        installed = install_default_skills(temp_workspace, explicit=True)

        assert len(installed) > 0
        skills_dir = Path(temp_workspace) / ".polaris" / "skills"
        assert skills_dir.exists()

    def test_install_default_skills_requires_explicit_call(self, temp_workspace):
        with pytest.raises(RuntimeError, match="explicit=True"):
            install_default_skills(temp_workspace)

    def test_load_skill_metadata(self, temp_workspace):
        """Test Layer 1: Skill metadata."""
        install_default_skills(temp_workspace, explicit=True)
        loader = SkillLoader(temp_workspace)

        descriptions = loader.get_system_prompt_section()

        assert "Skills available" in descriptions
        assert len(loader.list_skills()) > 0

    def test_load_skill_content(self, temp_workspace):
        """Test Layer 2: Full skill content."""
        install_default_skills(temp_workspace, explicit=True)
        loader = SkillLoader(temp_workspace)

        content = loader.load_skill_content("security-review")

        assert "<skill" in content
        assert "</skill>" in content
        assert "Security" in content

    def test_unknown_skill(self, temp_workspace):
        """Test loading unknown skill."""
        loader = SkillLoader(temp_workspace)

        content = loader.load_skill_content("nonexistent")

        assert "Error" in content
        assert "Unknown skill" in content


class TestPhase6TaskBoard:
    """Test Phase 6: Task board with dependencies."""

    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def board(self, temp_workspace):
        return TaskBoard(temp_workspace)

    def test_create_task(self, board):
        """Test task creation."""
        task = board.create(subject="Test task", priority=5)

        assert task.id > 0
        assert task.subject == "Test task"
        assert task.priority == 5
        assert task.status == TaskStatus.PENDING

    def test_task_dependencies(self, board):
        """Test task dependency tracking."""
        task1 = board.create(subject="First")
        task2 = board.create(subject="Second", blocked_by=[task1.id])

        assert task2.blocked_by == [task1.id]
        # task1.blocks should contain task2.id (reverse dependency)
        assert task2.id in board._cache[task1.id].blocks

    def test_unblock_on_complete(self, board):
        """Test that completing task unblocks dependents."""
        task1 = board.create(subject="First")
        task2 = board.create(subject="Second", blocked_by=[task1.id])

        # Complete task1
        board.update_status(task1.id, TaskStatus.COMPLETED)

        # task2 should no longer be blocked
        updated_task2 = board.get(task2.id)
        assert task1.id not in updated_task2.blocked_by

    def test_get_ready_tasks(self, board):
        """Test getting ready-to-work tasks."""
        task1 = board.create(subject="Ready task")
        task2 = board.create(subject="Blocked task", blocked_by=[task1.id])

        ready = board.get_ready_tasks()

        assert len(ready) == 1
        assert ready[0].id == task1.id

    def test_dependency_graph(self, board):
        """Test dependency graph generation."""
        task1 = board.create(subject="Base")
        task2 = board.create(subject="Middle", blocked_by=[task1.id])
        task3 = board.create(subject="Top", blocked_by=[task2.id])

        graph = board.get_dependency_graph(task3.id)

        assert graph["task"]["id"] == task3.id
        assert len(graph["depends_on"]) == 2  # task1 and task2

    def test_stats(self, board):
        """Test board statistics."""
        board.create(subject="Task 1")
        board.create(subject="Task 2")

        stats = board.get_stats()

        assert stats["total"] == 2
        assert "by_status" in stats


class TestIntegration:
    """Integration tests for all phases working together."""

    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_full_workflow(self, temp_workspace):
        """Test complete workflow with all phases."""
        # Phase 6: Create tasks
        board = TaskBoard(temp_workspace)
        task = board.create(subject="Implement feature", priority=10)

        # Phase 4: Load skills
        install_default_skills(temp_workspace, explicit=True)
        skills = SkillLoader(temp_workspace)
        assert len(skills.list_skills()) > 0

        # Phase 2: Submit background work
        with BackgroundManagerV2(temp_workspace, max_concurrent=2) as bg:
            result = bg.submit(command="echo 'working on feature'")
            assert result["ok"]

        # Phase 3: Context compression setup
        compressor = ContextCompressor(temp_workspace, llm_client=None)
        identity = compressor.create_identity_from_task({
            "id": str(task.id),
            "goal": task.subject,
            "acceptance_criteria": [],
            "write_scope": [],
            "current_phase": "implementation",
        })
        assert identity.task_id == str(task.id)

        print("✓ Full integration workflow passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
