from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = (
    BACKEND_ROOT
    / "docs"
    / "governance"
    / "ci"
    / "scripts"
    / "run_tool_calling_canonical_gate.py"
)
FITNESS_RULES_PATH = BACKEND_ROOT / "docs" / "governance" / "ci" / "fitness-rules.yaml"
PIPELINE_TEMPLATE_PATH = BACKEND_ROOT / "docs" / "governance" / "ci" / "pipeline.template.yaml"


def _build_utf8_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


def _local_tmp_dir(label: str) -> Path:
    path = BACKEND_ROOT / ".tmp_pytest_canonical_gate" / f"{label}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _matrix_report_payload(*, raw_tool: str, observed_tool: str) -> dict[str, Any]:
    return {
        "suite": "tool_calling_matrix",
        "cases": [
            {
                "case": {
                    "case_id": "l1_single_tool_accuracy",
                    "role": "director",
                    "judge": {
                        "stream": {
                            "required_tools": ["repo_read_head"],
                        }
                    },
                },
                "stream_observed": {
                    "tool_calls": [
                        {
                            "tool": observed_tool,
                            "args": {"file": "src/utils/helpers.py", "n": 50},
                        }
                    ]
                },
                "raw_events": [
                    {
                        "type": "tool_call",
                        "tool": raw_tool,
                        "args": {"file": "src/utils/helpers.py", "n": 50},
                    }
                ],
            }
        ],
    }


def _run_gate(report_path: Path, *, mode: str = "hard-fail") -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(GATE_SCRIPT),
        "--workspace",
        str(BACKEND_ROOT),
        "--input-report",
        str(report_path),
        "--role",
        "director",
        "--mode",
        mode,
    ]
    return subprocess.run(
        command,
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_build_utf8_env(),
        timeout=120,
        check=False,
    )


def test_tool_calling_canonical_gate_passes_when_raw_is_canonical() -> None:
    assert GATE_SCRIPT.is_file(), f"missing gate script: {GATE_SCRIPT}"
    tmp_path = _local_tmp_dir("canonical-pass")
    report_path = tmp_path / "TOOL_CALLING_MATRIX_REPORT.json"
    report_path.write_text(
        json.dumps(
            _matrix_report_payload(raw_tool="repo_read_head", observed_tool="repo_read_head"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = _run_gate(report_path)
    assert completed.returncode == 0, (
        "canonical gate should pass when raw tool names are canonical.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout or "{}")
    assert payload.get("issue_count") == 0


def test_tool_calling_canonical_gate_fails_on_alias_mapping_drift() -> None:
    tmp_path = _local_tmp_dir("canonical-fail")
    report_path = tmp_path / "TOOL_CALLING_MATRIX_REPORT.json"
    report_path.write_text(
        json.dumps(
            _matrix_report_payload(raw_tool="read_file", observed_tool="repo_read_head"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = _run_gate(report_path)
    assert completed.returncode == 1, (
        "canonical gate must fail when raw tool uses alias but observed tool is canonical.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout or "{}")
    assert int(payload.get("issue_count") or 0) > 0
    categories = {
        str(item.get("category") or "")
        for item in (payload.get("issues") or [])
        if isinstance(item, dict)
    }
    assert "alias_tool_name_used" in categories or "raw_observed_name_drift" in categories


def test_tool_calling_canonical_rule_and_stage_declared() -> None:
    rules_payload = yaml.safe_load(FITNESS_RULES_PATH.read_text(encoding="utf-8")) or {}
    pipeline_payload = yaml.safe_load(PIPELINE_TEMPLATE_PATH.read_text(encoding="utf-8")) or {}

    rules = rules_payload.get("rules")
    assert isinstance(rules, list), "fitness-rules.yaml must define a rules list"
    rule_ids = {
        str(item.get("id") or "").strip()
        for item in rules
        if isinstance(item, dict)
    }
    assert "tool_calling_canonical_identity_non_regressive" in rule_ids

    stages = pipeline_payload.get("stages")
    assert isinstance(stages, list), "pipeline.template.yaml must define stages"
    stage_ids = {
        str(item.get("id") or "").strip()
        for item in stages
        if isinstance(item, dict)
    }
    assert "tool_calling_canonical_gate" in stage_ids


def _multi_tool_payload(
    *,
    raw_tools: list[str],
    observed_tools: list[str],
    required_tools: list[str],
    case_id: str = "l3_parallel_tool_accuracy",
    role: str = "director",
) -> dict[str, Any]:
    return {
        "suite": "tool_calling_matrix",
        "cases": [
            {
                "case": {
                    "case_id": case_id,
                    "role": role,
                    "judge": {
                        "stream": {
                            "required_tools": required_tools,
                        }
                    },
                },
                "stream_observed": {
                    "tool_calls": [
                        {"tool": tool, "args": {}}
                        for tool in observed_tools
                    ]
                },
                "raw_events": [
                    {"type": "tool_call", "tool": tool, "args": {}}
                    for tool in raw_tools
                ],
            }
        ],
    }


def test_tool_calling_canonical_gate_fails_on_missing_required_raw_tool() -> None:
    """Verify gate fails when a required tool is absent from raw_events."""
    tmp_path = _local_tmp_dir("missing-required")
    report_path = tmp_path / "TOOL_CALLING_MATRIX_REPORT.json"
    report_path.write_text(
        json.dumps(
            _matrix_report_payload(raw_tool="repo_tree", observed_tool="repo_tree"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Override required_tools to include a tool not in raw_events
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["cases"][0]["case"]["judge"]["stream"]["required_tools"] = [
        "repo_read_head",
        "repo_tree",
    ]
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    completed = _run_gate(report_path)
    assert completed.returncode == 1, (
        "canonical gate must fail when required tool is missing from raw_events.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout or "{}")
    categories = {
        str(item.get("category") or "")
        for item in (payload.get("issues") or [])
        if isinstance(item, dict)
    }
    assert "missing_required_raw_tool" in categories


def test_tool_calling_canonical_gate_fails_on_count_mismatch() -> None:
    """Verify gate fails when raw tool count differs from observed count."""
    tmp_path = _local_tmp_dir("count-mismatch")
    report_path = tmp_path / "TOOL_CALLING_MATRIX_REPORT.json"
    report_path.write_text(
        json.dumps(
            _multi_tool_payload(
                raw_tools=["repo_read_head", "repo_tree"],
                observed_tools=["repo_read_head"],
                required_tools=["repo_read_head"],
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = _run_gate(report_path)
    assert completed.returncode == 1, (
        "canonical gate must fail when raw/observed tool counts differ.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout or "{}")
    categories = {
        str(item.get("category") or "")
        for item in (payload.get("issues") or [])
        if isinstance(item, dict)
    }
    assert "raw_observed_count_mismatch" in categories


def test_tool_calling_canonical_gate_passes_with_multiple_canonical_tools() -> None:
    """Verify gate passes when all tools are canonical and counts match."""
    tmp_path = _local_tmp_dir("multi-canonical")
    report_path = tmp_path / "TOOL_CALLING_MATRIX_REPORT.json"
    report_path.write_text(
        json.dumps(
            _multi_tool_payload(
                raw_tools=["repo_read_head", "repo_tree", "repo_rg"],
                observed_tools=["repo_read_head", "repo_tree", "repo_rg"],
                required_tools=["repo_read_head", "repo_tree", "repo_rg"],
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = _run_gate(report_path)
    assert completed.returncode == 0, (
        "canonical gate should pass when all tools are canonical and counts match.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout or "{}")
    assert payload.get("issue_count") == 0


def test_tool_calling_canonical_gate_fails_on_name_drift() -> None:
    """Verify gate fails when raw and observed names differ (non-alias drift)."""
    tmp_path = _local_tmp_dir("name-drift")
    report_path = tmp_path / "TOOL_CALLING_MATRIX_REPORT.json"
    report_path.write_text(
        json.dumps(
            _matrix_report_payload(raw_tool="repo_read_head", observed_tool="repo_tree"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = _run_gate(report_path)
    assert completed.returncode == 1, (
        "canonical gate must fail when raw/observed names differ.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout or "{}")
    categories = {
        str(item.get("category") or "")
        for item in (payload.get("issues") or [])
        if isinstance(item, dict)
    }
    assert "raw_observed_name_drift" in categories


def test_tool_calling_canonical_gate_audit_mode_never_fails() -> None:
    """Verify audit-only mode never returns non-zero exit code."""
    tmp_path = _local_tmp_dir("audit-mode")
    report_path = tmp_path / "TOOL_CALLING_MATRIX_REPORT.json"
    report_path.write_text(
        json.dumps(
            _matrix_report_payload(raw_tool="read_file", observed_tool="repo_read_head"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = _run_gate(report_path, mode="audit-only")
    assert completed.returncode == 0, (
        "audit-only mode should never fail.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout or "{}")
    assert payload.get("mode") == "audit-only"
    assert int(payload.get("issue_count") or 0) > 0  # Issues still reported
