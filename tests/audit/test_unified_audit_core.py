"""Tests for UnifiedAuditCore.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.polaris_loop.unified_audit_core import (
    UnifiedAuditCore,
    emit_audit_event,
)
from infrastructure.persistence.audit_store import (
    AuditEventType,
    AuditRole,
)


class TestUnifiedAuditCore:
    """UnifiedAuditCore 测试类"""

    @pytest.fixture
    def core(self, tmp_path):
        """创建测试用的 core 实例"""
        # Reset for testing with specific path
        UnifiedAuditCore.reset_instance(tmp_path)
        return UnifiedAuditCore.get_instance(tmp_path)

    def test_instance_pool(self, tmp_path):
        """测试分区实例池模式"""
        path1 = tmp_path / "ws1"
        path2 = tmp_path / "ws2"

        UnifiedAuditCore.reset_instance(path1)
        UnifiedAuditCore.reset_instance(path2)

        core1 = UnifiedAuditCore.get_instance(path1)
        core2 = UnifiedAuditCore.get_instance(path1)  # 相同路径
        core3 = UnifiedAuditCore.get_instance(path2)  # 不同路径

        assert core1 is core2  # 相同路径返回相同实例
        assert core1 is not core3  # 不同路径返回不同实例

    def test_emit_event(self, core):
        """测试事件发射"""
        success = core.emit_v2(
            event_type=AuditEventType.TASK_START,
            role=AuditRole.PM,
            workspace="/tmp/test",
            task_id="task-123",
            run_id="run-456",
        )
        assert success is True

        # 验证事件被写入
        events = core.store.query(limit=10)
        assert len(events) >= 1
        found = False
        for event in events:
            task = event.task if isinstance(event.task, dict) else {}
            if task.get("task_id") == "task-123":
                found = True
                break
        assert found, "Task should be found in query results"

    def test_concurrent_emit(self, core):
        """测试并发写入"""
        errors = []

        def emit_batch(thread_id: int):
            try:
                for i in range(50):
                    core.emit_v2(
                        event_type=AuditEventType.TOOL_EXECUTION,
                        role=AuditRole.DIRECTOR,
                        workspace="/tmp/test",
                        task_id=f"task-{thread_id}-{i}",
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=emit_batch, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent emit errors: {errors}"

        # 验证所有事件都被写入
        events = core.store.query(limit=1000)
        assert len(events) == 250

    def test_corruption_handling(self, tmp_path):
        """测试损坏处理"""
        UnifiedAuditCore.reset_instance(tmp_path)
        core = UnifiedAuditCore.get_instance(tmp_path)

        # 手动触发损坏记录
        core._record_corruption(
            file_path="/test/file.jsonl",
            offset=100,
            error_type="json_decode_error",
            error_message="Test error",
            line_preview='{"bad": json}',
            source_op="test",
            schema_version="2.0",
        )

        # 验证损坏被记录且包含新字段
        corruption_log = core.get_corruption_log()
        assert len(corruption_log) == 1
        assert corruption_log[0]["schema_version"] == "2.0"
        assert corruption_log[0]["source_op"] == "test"

    def test_chain_integrity(self, core):
        """测试链完整性"""
        # 发射多个事件
        for i in range(10):
            core.emit_v2(
                event_type=AuditEventType.TASK_START,
                role=AuditRole.PM,
                workspace="/tmp/test",
                task_id=f"task-{i}",
            )

        # 验证链
        result = core.store.verify_chain()
        assert result.is_valid is True
        assert result.total_events == 10
        assert result.gap_count == 0

    def test_utf8_encoding(self, core):
        """测试 UTF-8 编码"""
        # 发射包含非 ASCII 字符的事件
        core.emit_v2(
            event_type=AuditEventType.LLM_CALL,
            role=AuditRole.PM,
            workspace="/tmp/test",
            data={"message": "中文测试 ñoño émojis 🎉"},
        )

        # 验证能正确读取
        events = core.store.query(limit=10)
        assert len(events) >= 1

    def test_index_flush(self, core):
        """测试索引刷写"""
        # 发射多个 run_id 的事件
        for i in range(5):
            core.emit_v2(
                event_type=AuditEventType.TASK_START,
                role=AuditRole.PM,
                workspace="/tmp/test",
                run_id="run-abc",
                task_id=f"task-{i}",
            )

        for i in range(3):
            core.emit_v2(
                event_type=AuditEventType.TASK_START,
                role=AuditRole.PM,
                workspace="/tmp/test",
                run_id="run-xyz",
                task_id=f"task-{i}",
            )

        # 等待索引刷写
        time.sleep(1)
        core._flush_indexes()

        # 验证索引文件存在
        index_file = core._audit_dir / "index.run_id.json"
        assert index_file.exists()

        with open(index_file, 'r', encoding='utf-8') as f:
            index = json.load(f)

        assert "run-abc" in index
        assert "run-xyz" in index


class TestPathSafety:
    """路径安全测试"""

    @pytest.fixture
    def core(self, tmp_path):
        """创建测试用的 core 实例"""
        UnifiedAuditCore.reset_instance(tmp_path)
        return UnifiedAuditCore.get_instance(tmp_path)

    def test_absolute_path_rejection(self, core):
        """测试绝对路径拒绝（可选的安全特性）"""
        # 绝对路径应该被允许，因为审计系统需要记录绝对路径
        # 这是一个占位测试，实际取决于安全策略


class TestEmitAuditEvent:
    """emit_audit_event 便捷函数测试"""

    @pytest.fixture(autouse=True)
    def reset_and_setup(self, tmp_path, monkeypatch):
        """重置并设置环境"""
        UnifiedAuditCore.reset_instance(tmp_path)
        monkeypatch.setenv("POLARIS_RUNTIME_BASE", str(tmp_path))

    def test_emit_audit_event(self, tmp_path):
        """测试便捷函数"""
        UnifiedAuditCore.reset_instance(tmp_path)
        success = emit_audit_event(
            event_type=AuditEventType.TASK_COMPLETE,
            role=AuditRole.PM,
            workspace="/tmp/test",
            task_id="task-test",
        )
        assert success is True
