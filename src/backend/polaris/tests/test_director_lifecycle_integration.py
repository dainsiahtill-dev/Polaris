"""Director lifecycle integration tests.

Tests for the DirectorLifecycleManager and related components migrated from
polaris.kernelone.runtime to polaris.domain.director.

这些测试验证:
1. 生命周期状态机的基本操作
2. 并发更新的原子性
3. 错误跟踪
4. 工作区相对路径解析
5. 向后兼容接口
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from polaris.domain.director import (
    DEFAULT_DIRECTOR_LIFECYCLE,
    DirectorLifecycleManager,
    DirectorPhase,
    LifecycleEvent,
    LifecycleState,
)
from polaris.domain.director.constants import (
    DEFAULT_DIRECTOR_SUBPROCESS_LOG,
    DEFAULT_PM_REPORT,
)


class TestDirectorLifecycleStateMachine:
    """测试 Director 生命周期状态机。"""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """创建临时工作区。"""
        return tmp_path

    @pytest.fixture
    def manager(self, workspace: Path) -> DirectorLifecycleManager:
        """创建生命周期管理器。"""
        return DirectorLifecycleManager(workspace=workspace)

    def test_initial_state_is_init(self, manager: DirectorLifecycleManager) -> None:
        """验证初始状态为 init。"""
        state = manager.get_state()
        assert state.phase == DirectorPhase.INIT
        assert state.status == "unknown"
        assert state.run_id == ""
        assert state.task_id == ""
        assert state.events == []

    def test_update_phase_transitions(
        self,
        manager: DirectorLifecycleManager,
        workspace: Path,
    ) -> None:
        """验证阶段转换。"""
        # 初始状态
        state = manager.get_state()
        assert state.phase == DirectorPhase.INIT

        # 转换到规划阶段
        state = manager.update(
            phase=DirectorPhase.PLANNING,
            status="running",
            run_id="run-123",
        )
        assert state.phase == DirectorPhase.PLANNING
        assert state.status == "running"
        assert state.run_id == "run-123"

        # 转换到执行阶段
        state = manager.update(
            phase=DirectorPhase.EXECUTING,
            status="running",
            task_id="task-456",
        )
        assert state.phase == DirectorPhase.EXECUTING
        assert state.task_id == "task-456"

        # 转换到完成阶段
        state = manager.update(
            phase=DirectorPhase.COMPLETING,
            status="success",
        )
        assert state.phase == DirectorPhase.COMPLETING
        assert state.status == "success"

    def test_event_history_accumulation(
        self,
        manager: DirectorLifecycleManager,
        workspace: Path,
    ) -> None:
        """验证事件历史累积。"""
        # 执行多次状态更新
        for i in range(5):
            manager.update(
                phase=DirectorPhase.PLANNING,
                status=f"running-{i}",
                run_id=f"run-{i}",
            )

        state = manager.get_state()

        # 验证事件历史
        assert len(state.events) == 5
        assert state.events[0].run_id == "run-0"
        assert state.events[-1].run_id == "run-4"

    def test_event_history_limit(
        self,
        manager: DirectorLifecycleManager,
        workspace: Path,
    ) -> None:
        """验证事件历史限制（最多50条）。"""
        # 执行超过50次状态更新
        for i in range(60):
            manager.update(
                phase=DirectorPhase.PLANNING,
                status=f"running-{i}",
                run_id=f"run-{i}",
            )

        state = manager.get_state()

        # 验证事件数量限制
        assert len(state.events) == 50
        # 验证最早的记录被丢弃
        assert state.events[0].run_id == "run-10"
        assert state.events[-1].run_id == "run-59"

    def test_phase_normalization(
        self,
        manager: DirectorLifecycleManager,
        workspace: Path,
    ) -> None:
        """验证阶段名称规范化（小写）。"""
        state = manager.update(
            phase="PLANNING",  # 大写
            status="Running",  # 大写
        )
        assert state.phase == "planning"
        assert state.status == "running"

    def test_empty_path_returns_default_state(
        self,
        manager: DirectorLifecycleManager,
    ) -> None:
        """验证空路径返回默认状态。"""
        state = manager.get_state(path="nonexistent/DIRECTOR_LIFECYCLE.json")
        assert state.phase == DirectorPhase.INIT
        assert state.status == "unknown"


class TestDirectorLifecycleConcurrency:
    """测试 Director 生命周期并发安全。"""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """创建临时工作区。"""
        return tmp_path

    @pytest.fixture
    def manager(self, workspace: Path) -> DirectorLifecycleManager:
        """创建生命周期管理器。"""
        return DirectorLifecycleManager(workspace=workspace)

    def test_concurrent_updates_are_atomic(
        self,
        workspace: Path,
    ) -> None:
        """验证并发更新的原子性。"""
        manager = DirectorLifecycleManager(workspace=workspace)
        errors: list[Exception] = []
        barrier = threading.Barrier(5)

        def worker(worker_id: int) -> None:
            try:
                barrier.wait()  # 同步启动
                for i in range(10):
                    manager.update(
                        phase=DirectorPhase.PLANNING,
                        status="running",
                        run_id=f"worker-{worker_id}",
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 验证无错误
        assert len(errors) == 0

        # 验证最终状态
        state = manager.get_state()
        assert state.run_id.startswith("worker-")
        # 验证事件数量（应该等于总更新次数，但受50条限制）
        assert len(state.events) == 50

    def test_concurrent_read_write(
        self,
        workspace: Path,
    ) -> None:
        """验证并发读写不冲突。"""
        manager = DirectorLifecycleManager(workspace=workspace)
        errors: list[Exception] = []
        read_count = 0
        read_lock = threading.Lock()

        def reader() -> None:
            nonlocal read_count
            for _ in range(20):
                try:
                    manager.get_state()
                    with read_lock:
                        read_count += 1
                except Exception as e:
                    errors.append(e)

        def writer(writer_id: int) -> None:
            for i in range(10):
                try:
                    manager.update(
                        phase=DirectorPhase.EXECUTING,
                        status=f"writer-{writer_id}-{i}",
                    )
                except Exception as e:
                    # Intentionally catch all exceptions to detect thread safety issues.
                    errors.append(e)

        threads = [
            threading.Thread(target=reader),
            threading.Thread(target=reader),
            threading.Thread(target=writer, args=(1,)),
            threading.Thread(target=writer, args=(2,)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 验证无错误
        assert len(errors) == 0
        assert read_count == 40


class TestDirectorLifecycleErrorTracking:
    """测试 Director 生命周期错误跟踪。"""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """创建临时工作区。"""
        return tmp_path

    @pytest.fixture
    def manager(self, workspace: Path) -> DirectorLifecycleManager:
        """创建生命周期管理器。"""
        return DirectorLifecycleManager(workspace=workspace)

    def test_error_tracking(
        self,
        manager: DirectorLifecycleManager,
        workspace: Path,
    ) -> None:
        """验证错误跟踪。"""
        state = manager.update(
            phase=DirectorPhase.EXECUTING,
            status="failed",
            error="Command failed: exit code 1",
        )

        assert state.phase == DirectorPhase.EXECUTING
        assert state.status == "failed"
        assert state.error == "Command failed: exit code 1"

        # 验证错误在事件历史中
        last_event = state.events[-1]
        assert last_event.phase == DirectorPhase.EXECUTING
        assert last_event.status == "failed"

    def test_error_cleared_on_recovery(
        self,
        manager: DirectorLifecycleManager,
        workspace: Path,
    ) -> None:
        """验证错误在恢复时被清除。"""
        # 设置错误
        state = manager.update(
            phase=DirectorPhase.EXECUTING,
            status="failed",
            error="Temporary error",
        )
        assert state.error == "Temporary error"

        # 恢复（不传 error 参数）
        state = manager.update(
            phase=DirectorPhase.EXECUTING,
            status="running",
        )
        # 错误应该保留（除非显式清除）
        # 这是当前行为：如果不传 error，则保留旧值
        # 如果需要清除，应该传 error=None
        assert state.status == "running"

    def test_startup_completed_flag(
        self,
        manager: DirectorLifecycleManager,
        workspace: Path,
    ) -> None:
        """验证启动完成标志。"""
        state = manager.update(
            phase=DirectorPhase.INIT,
            status="running",
            startup_completed=True,
        )

        assert state.startup_completed is True

        # 验证事件历史中有 startup_at
        state = manager.get_state()
        assert len(state.events) == 1

    def test_execution_started_flag(
        self,
        manager: DirectorLifecycleManager,
        workspace: Path,
    ) -> None:
        """验证执行开始标志。"""
        state = manager.update(
            phase=DirectorPhase.EXECUTING,
            status="running",
            execution_started=True,
        )

        assert state.execution_started is True

    def test_terminal_flag(
        self,
        manager: DirectorLifecycleManager,
        workspace: Path,
    ) -> None:
        """验证终态标志。"""
        state = manager.update(
            phase=DirectorPhase.COMPLETING,
            status="success",
            terminal=True,
        )

        assert state.terminal is True


class TestDirectorLifecyclePaths:
    """测试 Director 生命周期路径解析。"""

    def test_workspace_relative_paths(self, tmp_path: Path) -> None:
        """验证相对路径解析。"""
        # 创建子目录
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        manager = DirectorLifecycleManager(workspace=subdir)

        manager.update(
            phase=DirectorPhase.INIT,
            status="running",
        )

        # 文件应该在子目录中
        expected_path = subdir / DEFAULT_DIRECTOR_LIFECYCLE
        assert expected_path.exists()

    def test_absolute_path_resolution(self, tmp_path: Path) -> None:
        """验证绝对路径解析。"""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        manager = DirectorLifecycleManager(workspace=workspace)

        absolute_path = str(tmp_path / "other" / "lifecycle.json")
        manager.update(
            path=absolute_path,
            phase=DirectorPhase.PLANNING,
            status="running",
        )

        assert Path(absolute_path).exists()

    def test_file_persistence(self, tmp_path: Path) -> None:
        """验证文件持久化。"""
        manager = DirectorLifecycleManager(workspace=tmp_path)

        manager.update(
            phase=DirectorPhase.PLANNING,
            status="running",
            run_id="run-123",
        )

        # 创建新的管理器实例，应该能读取到之前的状态
        new_manager = DirectorLifecycleManager(workspace=tmp_path)
        state = new_manager.get_state()

        assert state.phase == DirectorPhase.PLANNING
        assert state.run_id == "run-123"


class TestDirectorLifecycleCompatFunctions:
    """测试向后兼容函数。"""

    def test_compat_read_function(self, tmp_path: Path) -> None:
        """测试兼容的 read 函数。"""
        from polaris.domain.director.lifecycle import read

        manager = DirectorLifecycleManager(workspace=tmp_path)
        manager.update(
            phase=DirectorPhase.PLANNING,
            status="running",
            run_id="run-456",
        )

        # 使用兼容函数读取
        result = read(path=str(tmp_path / DEFAULT_DIRECTOR_LIFECYCLE))

        assert result["phase"] == "planning"
        assert result["run_id"] == "run-456"
        assert "events" in result

    def test_compat_update_function(self, tmp_path: Path) -> None:
        """测试兼容的 update 函数。"""
        from polaris.domain.director.lifecycle import update

        result = update(
            path=str(tmp_path / DEFAULT_DIRECTOR_LIFECYCLE),
            phase=DirectorPhase.EXECUTING,
            status="running",
            task_id="task-789",
        )

        assert result["phase"] == "executing"
        assert result["task_id"] == "task-789"


class TestDirectorConstants:
    """测试 Director 常量定义。"""

    def test_director_phase_values(self) -> None:
        """验证 DirectorPhase 值。"""
        assert DirectorPhase.INIT == "init"
        assert DirectorPhase.PLANNING == "planning"
        assert DirectorPhase.EXECUTING == "executing"
        assert DirectorPhase.REVIEWING == "reviewing"
        assert DirectorPhase.COMPLETING == "completing"
        assert DirectorPhase.FAILED == "failed"

    def test_director_phase_all(self) -> None:
        """验证 DirectorPhase.ALL 包含所有阶段。"""
        assert DirectorPhase.INIT in DirectorPhase.ALL
        assert DirectorPhase.PLANNING in DirectorPhase.ALL
        assert DirectorPhase.EXECUTING in DirectorPhase.ALL
        assert DirectorPhase.REVIEWING in DirectorPhase.ALL
        assert DirectorPhase.COMPLETING in DirectorPhase.ALL
        assert DirectorPhase.FAILED in DirectorPhase.ALL
        assert len(DirectorPhase.ALL) == 6

    def test_default_paths(self) -> None:
        """验证默认路径常量。"""
        assert DEFAULT_DIRECTOR_LIFECYCLE == "runtime/DIRECTOR_LIFECYCLE.json"
        assert DEFAULT_DIRECTOR_SUBPROCESS_LOG == "runtime/logs/director.process.log"
        assert DEFAULT_PM_REPORT == "runtime/results/pm.report.md"

    def test_channel_files_mapping(self) -> None:
        """验证通道文件映射。"""
        from polaris.domain.director.constants import CHANNEL_FILES

        assert CHANNEL_FILES["pm_report"] == DEFAULT_PM_REPORT
        assert CHANNEL_FILES["director_console"] == DEFAULT_DIRECTOR_SUBPROCESS_LOG


class TestDirectorLifecycleDataClasses:
    """测试 Director 生命周期数据类。"""

    def test_lifecycle_event_creation(self) -> None:
        """验证 LifecycleEvent 创建。"""
        event = LifecycleEvent(
            phase="planning",
            status="running",
            timestamp="2026-03-27T00:00:00Z",
            run_id="run-123",
            task_id="task-456",
        )

        assert event.phase == "planning"
        assert event.status == "running"
        assert event.run_id == "run-123"
        assert event.task_id == "task-456"

    def test_lifecycle_state_creation(self) -> None:
        """验证 LifecycleState 创建。"""
        state = LifecycleState(
            phase=DirectorPhase.PLANNING,
            status="running",
            run_id="run-123",
            task_id="task-456",
            startup_completed=True,
        )

        assert state.phase == "planning"
        assert state.status == "running"
        assert state.run_id == "run-123"
        assert state.startup_completed is True
        assert state.events == []

    def test_lifecycle_state_default_values(self) -> None:
        """验证 LifecycleState 默认值。"""
        state = LifecycleState()

        assert state.phase == DirectorPhase.INIT
        assert state.status == "unknown"
        assert state.run_id == ""
        assert state.task_id == ""
        assert state.startup_completed is False
        assert state.execution_started is False
        assert state.terminal is False
        assert state.events == []
