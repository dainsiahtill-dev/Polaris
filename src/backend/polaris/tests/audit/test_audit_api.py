"""Tests for Audit API endpoints.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

import importlib.util, pytest
if importlib.util.find_spec("api") is None:
    pytest.skip("Legacy module not available: api.main", allow_module_level=True)

from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from api.main import app



@pytest.fixture
def client():
    """创建测试客户端"""
    return TestClient(app)


class TestAuditLogsEndpoint:
    """测试 /v2/audit/logs 端点"""

    def test_get_audit_logs_default(self, client):
        """测试默认查询"""
        response = client.get("/v2/audit/logs")
        assert response.status_code == 200
        data = response.json()
        assert "events" in data
        assert "pagination" in data

    def test_get_audit_logs_with_limit(self, client):
        """测试带 limit 参数"""
        response = client.get("/v2/audit/logs?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert data["pagination"]["limit"] == 10

    def test_get_audit_logs_with_task_id(self, client):
        """测试按 task_id 过滤"""
        response = client.get("/v2/audit/logs?task_id=test-task")
        assert response.status_code == 200


class TestAuditVerifyEndpoint:
    """测试 /v2/audit/verify 端点"""

    def test_verify_chain(self, client):
        """测试链验证"""
        response = client.get("/v2/audit/verify")
        assert response.status_code == 200
        data = response.json()
        assert "chain_valid" in data
        assert "total_events" in data


class TestAuditStatsEndpoint:
    """测试 /v2/audit/stats 端点"""

    def test_get_stats(self, client):
        """测试统计查询"""
        response = client.get("/v2/audit/stats")
        assert response.status_code == 200
        data = response.json()
        assert "stats" in data


class TestAuditExportEndpoint:
    """测试 /v2/audit/export 端点"""

    def test_export_json(self, client):
        """测试 JSON 导出"""
        response = client.get("/v2/audit/export?format=json")
        assert response.status_code == 200

    def test_export_csv(self, client):
        """测试 CSV 导出"""
        response = client.get("/v2/audit/export?format=csv")
        assert response.status_code == 200
        assert "text/csv" in response.headers.get("content-type", "")


class TestAuditTriageEndpoint:
    """测试 /v2/audit/triage 端点"""

    def test_triage_with_run_id(self, client):
        """测试按 run_id 排障"""
        response = client.post("/v2/audit/triage", json={"run_id": "test-run-20240310-abc12345"})
        assert response.status_code in [200, 404]  # 可能找不到

    def test_triage_no_params(self, client):
        """测试无参数排障"""
        response = client.post("/v2/audit/triage", json={})
        assert response.status_code == 400

    def test_triage_with_task_id(self, client):
        """测试按 task_id 排障"""
        response = client.post("/v2/audit/triage", json={"task_id": "test-task"})
        assert response.status_code in [200, 404]


class TestAuditFailureHopsEndpoint:
    """测试 /v2/audit/failures/{run_id}/hops 端点"""

    def test_failure_hops_not_found(self, client):
        """测试未找到（使用有效格式的 run_id）"""
        response = client.get("/v2/audit/failures/test-run-20240310-abc12345/hops")
        # 可能是 404（文件不存在）或 200（实时生成）
        assert response.status_code in [200, 404]


class TestAuditAnalyzeFailureEndpoint:
    """测试 /v2/audit/analyze-failure 端点"""

    def test_analyze_failure_requires_selector(self, client):
        """必须提供 run_id/task_id/error_message 之一"""
        response = client.post("/v2/audit/analyze-failure", json={})
        assert response.status_code == 400

    def test_analyze_failure_with_hint(self, client):
        """提供 error hint 时应返回诊断结构"""
        response = client.post(
            "/v2/audit/analyze-failure",
            json={"error_message": "timeout", "depth": 3},
        )
        assert response.status_code == 200
        data = response.json()
        assert "failure_hops" in data
        assert "recommended_action" in data


class TestAuditProjectScanEndpoint:
    """测试 /v2/audit/scan-project 端点"""

    def test_scan_project_default(self, client):
        response = client.post("/v2/audit/scan-project", json={})
        assert response.status_code == 200
        data = response.json()
        assert "summary" in data
        assert "findings" in data

    def test_scan_project_region_requires_focus(self, client):
        response = client.post("/v2/audit/scan-project", json={"scope": "region"})
        assert response.status_code == 400

    def test_scan_project_region_rejects_path_traversal(self, client):
        response = client.post(
            "/v2/audit/scan-project",
            json={"scope": "region", "focus": "../../etc/passwd"},
        )
        assert response.status_code == 400

    def test_scan_project_region_missing_file_returns_404(self, client):
        response = client.post(
            "/v2/audit/scan-project",
            json={"scope": "region", "focus": "this/file/does/not/exist.py"},
        )
        assert response.status_code == 404


class TestAuditCheckRegionEndpoint:
    """测试 /v2/audit/check-region 端点"""

    def test_check_region_requires_target(self, client):
        response = client.post("/v2/audit/check-region", json={})
        assert response.status_code == 400

    def test_check_region_with_file(self, client):
        response = client.post(
            "/v2/audit/check-region",
            json={"file_path": "src/backend/api/v2/audit.py", "lines": "1-50"},
        )
        assert response.status_code in [200, 404]


class TestAuditTraceEndpoint:
    """测试 /v2/audit/trace/{trace_id} 端点"""

    def test_trace_query(self, client):
        response = client.get("/v2/audit/trace/test-trace-id")
        assert response.status_code in [200, 404]


class TestAuditCorruptionEndpoint:
    """测试 /v2/audit/corruption 端点"""

    def test_corruption_default(self, client):
        """测试默认查询"""
        response = client.get("/v2/audit/corruption")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestAuditCleanupEndpoint:
    """测试 /v2/audit/cleanup 端点"""

    def test_cleanup_dry_run(self, client):
        """测试演练清理"""
        response = client.post("/v2/audit/cleanup", json={"dry_run": True})
        assert response.status_code == 200
        data = response.json()
        assert "would_delete" in data
        assert data["dry_run"] is True
