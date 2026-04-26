from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
if str(BACKEND_ROOT) in sys.path:
    sys.path.remove(str(BACKEND_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

from application.audit_service import AuditContext, IndependentAuditService  # noqa: E402


def test_mentions_missing_evidence_detects_unknown_file_state_when_evidence_exists() -> None:
    service = IndependentAuditService()
    context = AuditContext(
        task_id="TASK-001",
        planner_output="non-empty planner output",
        executor_output="",
    )
    assert service._mentions_missing_evidence("实际文件状态未知，无法确认文件是否真实创建", context)


def test_mentions_missing_evidence_false_for_regular_contract_fail() -> None:
    service = IndependentAuditService()
    context = AuditContext(task_id="TASK-002", planner_output="", executor_output="")
    assert service._mentions_missing_evidence(
        "当前实现与验收标准不符：minutes 范围校验缺失",
        context,
    ) is False
