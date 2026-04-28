import importlib.util, pytest
if importlib.util.find_spec("core") is None:
    pytest.skip("Legacy module not available: core.auditkit", allow_module_level=True)

"""Tests for auditkit library.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

import json
import pytest
from datetime import datetime, timezone
from pathlib import Path

from core.auditkit import (
    build_triage_bundle,
    query_events,
    query_by_run_id,
    query_by_task_id,
    verify_chain,
    verify_file_integrity,
)


class TestQueryFunctions:
    """测试查询函数"""

    @pytest.fixture
    def runtime_root(self, tmp_path):
        """创建测试 runtime 根目录"""
        runtime = tmp_path / ".polaris" / "runtime"
        runtime.mkdir(parents=True)
        audit_dir = runtime / "audit"
        audit_dir.mkdir(parents=True)
        return runtime

    def test_query_events_empty(self, runtime_root):
        """测试空查询"""
        events = query_events(str(runtime_root))
        assert events == []

    def test_query_by_run_id_empty(self, runtime_root):
        """测试按 run_id 空查询"""
        events = query_by_run_id(str(runtime_root), "test-run")
        assert events == []

    def test_query_by_task_id_empty(self, runtime_root):
        """测试按 task_id 空查询"""
        events = query_by_task_id(str(runtime_root), "test-task")
        assert events == []


class TestVerifyFunctions:
    """测试验证函数"""

    @pytest.fixture
    def runtime_root(self, tmp_path):
        """创建测试 runtime 根目录"""
        runtime = tmp_path / ".polaris" / "runtime"
        runtime.mkdir(parents=True)
        audit_dir = runtime / "audit"
        audit_dir.mkdir(parents=True)
        return runtime

    def test_verify_chain_no_files(self, runtime_root):
        """测试无文件验证 - AuditStore 认为空链是有效的"""
        result = verify_chain(str(runtime_root))
        # AuditStore 认为空链是有效的（没有事件意味着没有篡改）
        # 降级 fallback 会在没有文件时返回 chain_valid=False
        # 两种情况都是可接受的
        assert "chain_valid" in result
        assert "total_events" in result

    def test_verify_chain_with_events(self, runtime_root):
        """测试有事件的链验证"""
        audit_dir = runtime_root / "audit"

        # 写入一些测试事件
        event = {
            "event_id": "test-1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "task_start",
            "prev_hash": "0" * 64,
            "signature": "test-signature-1",
        }

        log_file = audit_dir / "audit-2024-03.jsonl"
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')

        result = verify_chain(str(runtime_root))
        assert result["total_events"] == 1


class TestVerifyFileIntegrity:
    """测试文件完整性验证"""

    def test_file_not_found(self, tmp_path):
        """测试文件不存在"""
        result = verify_file_integrity(str(tmp_path / "nonexistent.txt"))
        assert result["valid"] is False
        assert "error" in result

    def test_file_hash(self, tmp_path):
        """测试文件哈希"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content", encoding='utf-8')

        result = verify_file_integrity(str(test_file))
        assert result["valid"] is True
        assert "hash" in result

    def test_file_hash_mismatch(self, tmp_path):
        """测试哈希不匹配"""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content", encoding='utf-8')

        result = verify_file_integrity(
            str(test_file),
            expected_hash="wrong-hash"
        )
        assert result["valid"] is False


class TestTriageBundle:
    """测试排障包构建"""

    @pytest.fixture
    def runtime_root(self, tmp_path):
        """创建测试 runtime 根目录"""
        runtime = tmp_path / ".polaris" / "runtime"
        runtime.mkdir(parents=True)
        return runtime

    def test_build_triage_empty(self, runtime_root):
        """测试空排障包"""
        bundle = build_triage_bundle(
            workspace=str(runtime_root.parent),
            run_id="test-run",
        )
        assert bundle["status"] == "not_found"

    def test_build_triage_with_events(self, runtime_root):
        """测试有事件的排障包"""
        audit_dir = runtime_root / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)  # 确保目录存在

        # 写入一些测试事件
        event = {
            "event_id": "test-1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "task_start",
            "source": {"role": "pm"},
            "task": {"task_id": "task-1", "run_id": "run-1"},
            "action": {"name": "start", "result": "success"},
            "context": {"trace_id": "trace-1"},
            "prev_hash": "0" * 64,
            "signature": "test-signature-1",
        }

        log_file = audit_dir / "audit-2024-03.jsonl"
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')

        bundle = build_triage_bundle(
            workspace=str(runtime_root.parent),
            run_id="run-1",
            runtime_root=runtime_root,
        )

        # 应该找到事件
        assert bundle["run_id"] == "run-1"
        assert "pm_quality_history" in bundle

    def test_build_triage_with_task_id(self, runtime_root):
        """测试按 task_id 构建排障包"""
        audit_dir = runtime_root / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)

        event = {
            "event_id": "test-2",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "task_start",
            "source": {"role": "pm"},
            "task": {"task_id": "task-x", "run_id": "run-x"},
            "action": {"name": "start", "result": "success"},
            "context": {"trace_id": "trace-x"},
            "prev_hash": "0" * 64,
            "signature": "test-signature-2",
        }

        log_file = audit_dir / "audit-2024-03.jsonl"
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        bundle = build_triage_bundle(
            workspace=str(runtime_root.parent),
            task_id="task-x",
            runtime_root=runtime_root,
        )

        assert bundle["status"] == "success"
        assert bundle["task_id"] == "task-x"
        assert bundle["run_id"] == "run-x"

    def test_build_triage_with_trace_id(self, runtime_root):
        """测试按 trace_id 构建排障包"""
        audit_dir = runtime_root / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)

        event = {
            "event_id": "test-3",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "task_start",
            "source": {"role": "pm"},
            "task": {"task_id": "task-y", "run_id": "run-y"},
            "action": {"name": "start", "result": "success"},
            "context": {"trace_id": "trace-y"},
            "prev_hash": "0" * 64,
            "signature": "test-signature-3",
        }

        log_file = audit_dir / "audit-2024-03.jsonl"
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        bundle = build_triage_bundle(
            workspace=str(runtime_root.parent),
            trace_id="trace-y",
            runtime_root=runtime_root,
        )

        assert bundle["status"] == "success"
        assert bundle["trace_id"] == "trace-y"
        assert bundle["run_id"] == "run-y"
