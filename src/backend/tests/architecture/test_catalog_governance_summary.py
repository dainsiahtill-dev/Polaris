from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND_ROOT / "docs" / "governance" / "ci" / "scripts" / "summarize_catalog_governance_gate.py"


def test_catalog_governance_summary_renders_expected_sections(tmp_path: Path) -> None:
    payload = {
        "workspace": str(BACKEND_ROOT),
        "mode": "audit-only",
        "issue_count": 3,
        "blocker_count": 0,
        "high_count": 3,
        "new_issue_count": 1,
        "issues": [
            {
                "message": "roles.runtime imports roles.adapters but does not declare it in depends_on",
            },
            {
                "message": "roles.runtime imports roles.profile but does not declare it in depends_on",
            },
            {
                "message": "director.execution imports runtime.task_runtime but does not declare it in depends_on",
            },
        ],
        "manifest_catalog": {
            "mismatch_count": 2,
            "new_mismatch_count": 1,
            "mismatches": [
                {"cell_id": "roles.runtime", "field": "effects_allowed"},
                {"cell_id": "roles.runtime", "field": "depends_on"},
            ],
        },
    }
    report_path = tmp_path / "catalog_report.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--input", str(report_path), "--top", "5"],
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0
    assert "=== SUMMARY ===" in completed.stdout
    assert "issue_count: 3" in completed.stdout
    assert "mismatch_count: 2" in completed.stdout
    assert "=== depends_on_drift top cells ===" in completed.stdout
    assert "roles.runtime: 2" in completed.stdout
    assert "=== manifest_mismatch fields ===" in completed.stdout
    assert "effects_allowed: 1" in completed.stdout
    assert completed.stderr == ""
