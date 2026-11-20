"""The module runner consumes the settled internal bench result."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.platform_modules import run_module_gates as gates


@pytest.mark.parametrize(
    ("floor", "closed", "expected"),
    [("PASS", True, True), ("UNKNOWN", True, False), ("FAIL", True, False), ("PASS", False, False)],
)
def test_bench_requires_platform_floor_and_cleanup(tmp_path: Path, floor: str, closed: bool, expected: bool):
    project = "L1-04"
    report = tmp_path / "reports" / ("a" * 32) / "summary.json"
    report.parent.mkdir(parents=True)
    record = {
        "project_id": project,
        "exit_code": 0,
        "catalog_quality": floor,
        "resources_closed": closed,
        "observation": {"harness_status": "OBSERVED_ACCEPTED"},
    }
    report.write_text(json.dumps({"selected_project_ids": [project], "records": [record]}), encoding="utf-8")
    proc = subprocess.CompletedProcess([], 0, str(report) + "\n", "")
    with patch.object(gates.subprocess, "run", return_value=proc):
        result = gates.run_bench_gate(
            repo=tmp_path,
            work_dir=tmp_path,
            host_config=report,
            llm_config=report,
            registry_home=tmp_path,
            project_id=project,
        )
    assert result["ok"] is expected
    assert "--real-run-timeout" not in result["command"]
