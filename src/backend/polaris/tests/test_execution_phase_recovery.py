from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from domain.verification.progress_delta import ProgressTracker  # noqa: E402
from domain.verification.write_gate import WriteGate  # noqa: E402


def test_progress_tracker_recovers_after_improving_round() -> None:
    tracker = ProgressTracker(stall_threshold=2)

    round1 = tracker.update(
        files_created=0,
        missing_targets=["src/main.py"],
        errors=["Pattern not found"],
        unresolved_imports=[],
    )
    round2 = tracker.update(
        files_created=0,
        missing_targets=["src/main.py"],
        errors=["Pattern not found"],
        unresolved_imports=[],
    )
    round3 = tracker.update(
        files_created=1,
        missing_targets=[],
        errors=[],
        unresolved_imports=[],
    )

    assert round1.is_stalled is False
    assert round2.is_stalled is False
    assert round3.trend == "improving"
    assert round3.is_stalled is False


def test_write_gate_allows_no_change_when_not_required() -> None:
    result = WriteGate.validate(
        changed_files=[],
        act_files=["src/main.py"],
        pm_target_files=["src/main.py"],
        require_change=False,
    )
    assert result.allowed is True
    assert "validated" in result.reason.lower()
