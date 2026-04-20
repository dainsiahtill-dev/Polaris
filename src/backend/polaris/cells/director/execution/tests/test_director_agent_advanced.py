"""Tests for director_agent module."""

from __future__ import annotations

from unittest.mock import Mock

from polaris.cells.director.execution.internal.director_agent import (
    ExecutionRecord,
    QualityTracker,
    RiskRegistry,
)


class TestExecutionRecord:
    def test_init_basic(self):
        record = ExecutionRecord("exec-123", "task-456")
        assert record.execution_id == "exec-123"
        assert record.task_id == "task-456"
        assert record.status == "pending"
        assert record.risk_score == 0.0

    def test_to_dict(self):
        record = ExecutionRecord("exec-123", "task-456")
        result = record.to_dict()
        assert result["execution_id"] == "exec-123"
        assert result["task_id"] == "task-456"

    def test_from_dict(self):
        data = {
            "execution_id": "exec-123",
            "task_id": "task-456",
            "status": "completed",
            "risk_score": 0.5,
        }
        record = ExecutionRecord.from_dict(data)
        assert record.execution_id == "exec-123"
        assert record.status == "completed"

    def test_complete(self):
        record = ExecutionRecord("exec-123", "task-456")
        record.complete("completed", result={"ok": True})
        assert record.status == "completed"

    def test_assess_risk_many_files(self):
        record = ExecutionRecord("exec-123", "task-456")
        files = [f"file{i}.py" for i in range(15)]
        record.assess_risk(files, 100, 50)
        assert record.risk_score >= 0.3
        assert "many_files_changed" in record.risk_factors

    def test_assess_risk_auth_files(self):
        record = ExecutionRecord("exec-123", "task-456")
        record.assess_risk(["auth.py", "security.py"], 10, 5)
        assert "touches_auth" in record.risk_factors

    def test_assess_risk_max_score(self):
        record = ExecutionRecord("exec-123", "task-456")
        files = ["auth.py", "config.json"] + [f"file{i}.py" for i in range(15)]
        record.assess_risk(files, 600, 100)
        assert record.risk_score == 1.0


class TestRiskRegistry:
    def test_init(self, tmp_path):
        registry = RiskRegistry(str(tmp_path))
        assert registry.workspace == str(tmp_path)

    def test_record_risk(self, tmp_path):
        registry = RiskRegistry(str(tmp_path))
        risk_id = registry.record_risk("exec-123", "test_risk", "Test risk", "medium")
        assert risk_id.startswith("risk-")

    def test_get_open_risks_empty(self, tmp_path):
        registry = RiskRegistry(str(tmp_path))
        assert registry.get_open_risks() == []


class TestQualityTracker:
    def test_init(self, tmp_path):
        tracker = QualityTracker(str(tmp_path))
        assert tracker.workspace == str(tmp_path)

    def test_record_qa_result(self, tmp_path):
        tracker = QualityTracker(str(tmp_path))
        tracker._fs = Mock()
        tracker.record_qa_result("exec-123", "task-456", {"passed": True})
        tracker._fs.write_json.assert_called_once()

    def test_get_qa_history_empty(self, tmp_path):
        tracker = QualityTracker(str(tmp_path))
        assert tracker.get_qa_history(limit=10) == []


class TestDirectorAgentSnapshot:
    def test_save_snapshot(self):
        from polaris.cells.director.execution.internal.director_agent import DirectorAgent, ExecutionRecord

        agent = DirectorAgent.__new__(DirectorAgent)
        agent.workspace = "/tmp/test"
        agent._current_execution = ExecutionRecord("exec-123", "task-456")
        agent._execution_history = [{"execution_id": "exec-123"}]
        snapshot = agent.save_snapshot()
        assert "execution_history" in snapshot
        assert "saved_at" in snapshot

    def test_load_snapshot(self):
        from polaris.cells.director.execution.internal.director_agent import DirectorAgent

        agent = DirectorAgent.__new__(DirectorAgent)
        agent.workspace = "/tmp/test"
        agent._execution_history = []
        agent._load_snapshot({"execution_history": [{"execution_id": "exec-123"}]})
        assert len(agent._execution_history) == 1
