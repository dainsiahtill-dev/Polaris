"""Tests for chain_forensics wrapper.

Uses a synthetic chain.log fixture (built in tmp_path per test) so we never
depend on live L2 batch artifacts. Asserts:
  1. summarize() pulls project id, exit_code, chain_state, QA verdict
  2. step_counts correctly classifies write vs tool-fail turns
  3. top_errors categorizes signatures under the 5 attribution classes
  4. wall-clock parses LLM ENTRY timestamps end-to-end
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running the test directly without installing: insert scripts/factory_bench
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import chain_forensics  # noqa: E402
from polaris.kernelone.tools.tool_kinds import WRITE_TOOLS  # noqa: E402


def _build_log(tmp_path: Path, *, scenario: str) -> Path:
    """Construct a synthetic chain.log that exercises the parser.

    Scenarios:
      - "happy"      : 2 writes succeed, no errors, factory-bench PASS
      - "f16_wall"   : 1 write + 1 single_batch_contract_violation, FAIL
      - "empty_dir"  : 5th floor (output_length=0) on a Director turn
    """
    if scenario == "happy":
        body = "\n".join(
            [
                "[LLMInvoker.call] ENTRY POINT REACHED: profile=pm run_id=pm-00001",
                "[LLMInvoker.call] ENTRY: profile=director run_id=turn-A 2026-06-17 01:00:00",
                "[director] write_file -> main.py (ok)",
                "[LLMInvoker.call] ENTRY: profile=director run_id=turn-B 2026-06-17 01:00:10",
                "[director] write_file -> readme.md (ok)",
                "[market-chain] archived run evidence -> /tmp/L2-07_runs/run1 (run_id=pm-market-7)",
                '[market-chain] outcome: {"exit_code": 0, "director": {"status": "ok"}}',
                '[market-chain] inline detail: {"passed": true, "reason": "all clauses green"}',
                "[factory-bench] L2-07 PASS: chain=clean files=2 chain_exit=0 (300s)",
            ]
        )
    elif scenario == "f16_wall":
        body = "\n".join(
            [
                "[LLMInvoker.call] ENTRY: profile=director run_id=turn-X 2026-06-17 02:00:00",
                "[director] 工具执行返回失败结果: edit_blocks - line-range edit requires a 'file' argument.",
                "RuntimeError: single_batch_contract_violation: mutation requested but no write tool invocation",
                "RuntimeError: single_batch_contract_violation: circuit_breaker_triggered turn_id=turn-X batch_failures=1 consecutive_failures=3",
                "[LLMInvoker.call] ENTRY: profile=director run_id=turn-Y 2026-06-17 02:00:30",
                '[market-chain] outcome: {"exit_code": 5, "director": {"status": "fail"}}',
                "[factory-bench] L2-08 FAIL: chain=partial files=0 chain_exit=5 (240s)",
            ]
        )
    elif scenario == "empty_dir":
        body = "\n".join(
            [
                "[LLMInvoker.call] ENTRY: profile=director run_id=turn-Z 2026-06-17 03:00:00",
                "[invoker] reasoning-truncation re-ask: reserved output budget + minimal-reasoning directive",
                "[LLMInvoker] RAW_RESPONSE: model=qwen3.6-27b-int4 output_length=0 output_preview=''",
                "[LLMInvoker.call] ENTRY: profile=director run_id=turn-W 2026-06-17 03:00:05",
                "[PreWriteGuard] Blocked write to main.py due to syntax errors: main.py:3: SyntaxError",
                "[director] 工具执行返回失败结果: write_file - Code syntax validation failed",
                "RuntimeError: single_batch_contract_violation: mutation requested but no write tool invocation",
            ]
        )
    else:
        raise ValueError(f"unknown scenario: {scenario}")
    log = tmp_path / f"{scenario}.chain.log"
    log.write_text(body, encoding="utf-8")
    return log


def test_happy_path_extracts_all_fields(tmp_path: Path) -> None:
    log = _build_log(tmp_path, scenario="happy")
    report = chain_forensics.summarize(str(log))
    assert report["project"] == "happy"
    assert report["exit_code"] == 0
    assert report["chain_state"] == "clean"
    assert report["qa"]["passed"] is True
    assert report["qa"]["reason"] == "all clauses green"
    assert report["verdict"] == "PASS"
    assert report["file_count"] == 2
    assert report["step_counts"]["turns_with_write"] == 2
    assert report["step_counts"]["turns_failed"] == 0
    assert report["wall_seconds"] == 10.0
    assert len(report["archive_runs"]) == 1
    assert "single_batch_contract_violation" not in {e[0] for e in report["top_errors"]}


def test_write_detector_uses_canonical_write_tool_catalog() -> None:
    assert set(chain_forensics._FILE_WRITE_TOOL_NAMES) == set(WRITE_TOOLS)
    assert chain_forensics._FILE_WRITE_RE.search("[director] precision_edit -> main.py")


def test_f16_wall_categorizes_correctly(tmp_path: Path) -> None:
    log = _build_log(tmp_path, scenario="f16_wall")
    report = chain_forensics.summarize(str(log))
    assert report["exit_code"] == 5
    assert report["chain_state"] == "partial"  # factory-bench verdict wins over exit-code guess
    assert report["verdict"] == "FAIL"
    assert report["file_count"] == 0
    sigs = {e[0] for e in report["top_errors"]}
    assert "single_batch_contract_violation" in sigs
    assert "circuit_breaker" in sigs
    attrs = {e[0]: e[2] for e in report["top_errors"]}
    assert attrs["single_batch_contract_violation"] == "platform_fixable"
    assert attrs["circuit_breaker"] == "platform_fixable"


def test_empty_output_classified_as_non_authoritative_model_output_candidate(tmp_path: Path) -> None:
    log = _build_log(tmp_path, scenario="empty_dir")
    report = chain_forensics.summarize(str(log))
    sigs = {e[0]: e[2] for e in report["top_errors"]}
    assert sigs.get("reasoning-truncation re-ask") == "model_output_candidate"
    assert sigs.get("output_length=0 output_preview=''") == "model_output_candidate"
    assert sigs.get(r"\[PreWriteGuard\] Blocked write") == "working_as_intended"
    assert sigs.get("single_batch_contract_violation") == "platform_fixable"


def test_json_round_trip(tmp_path: Path) -> None:
    log = _build_log(tmp_path, scenario="happy")
    report = chain_forensics.summarize(str(log))
    blob = json.dumps(report, ensure_ascii=False, default=str)
    parsed = json.loads(blob)
    assert parsed["project"] == "happy"
    assert parsed["step_counts"]["turns_total"] >= 2
