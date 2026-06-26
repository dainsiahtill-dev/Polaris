"""Tests for market_forensics replay safety gating."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import market_forensics  # noqa: E402
from polaris.kernelone.quality import step_verify  # noqa: E402


def _item(*, verify: str, target_file: str = "app.py") -> dict[str, Any]:
    return {
        "task_id": "step-1",
        "parent_task_id": "parent-1",
        "status": "resolved",
        "payload": json.dumps(
            {
                "construction_step": {
                    "target_file": target_file,
                    "verify": verify,
                },
                "scope_paths": [target_file],
            }
        ),
    }


def test_replay_runnable_rejects_unsafe_verify_without_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_run_step_verify(_verify: str, *, cwd: str) -> tuple[int, str]:
        raise AssertionError(f"run_step_verify should not run for unsafe verify in {cwd}")

    monkeypatch.setattr(step_verify, "run_step_verify", fail_run_step_verify)

    report = market_forensics.replay_runnable([_item(verify="rm -rf .")], str(tmp_path))

    assert report["coherent_steps"] == 0
    assert report["total_steps_with_verify"] == 1
    assert report["product_coherent"] is False
    row = report["steps"][0]
    assert row["verify_passes_vs_final"] is False
    assert row["verify_safety"]["allowed"] is False
    assert row["verify_safety"]["reason"] == "blocked_command:rm"
    assert "step verify command rejected by safety policy" in row["verify_failure_evidence"]
    assert "'rm -rf .'" in row["verify_failure_evidence"]


def test_replay_runnable_safe_verify_still_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    calls: list[tuple[str, str]] = []

    def fake_run_step_verify(verify: str, *, cwd: str) -> tuple[int, str]:
        calls.append((verify, cwd))
        return 0, ""

    monkeypatch.setattr(step_verify, "run_step_verify", fake_run_step_verify)

    report = market_forensics.replay_runnable([_item(verify="test -f app.py")], str(tmp_path))

    assert calls == [("test -f app.py", str(tmp_path))]
    assert report["coherent_steps"] == 1
    assert report["product_coherent"] is True
    row = report["steps"][0]
    assert row["verify_passes_vs_final"] is True
    assert row["verify_safety"]["allowed"] is True
    assert row["verify_failure_evidence"] == ""
