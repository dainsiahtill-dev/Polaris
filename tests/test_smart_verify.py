from __future__ import annotations

import sys
from pathlib import Path
import pytest

# Skip this test - domain.* modules have been migrated to polaris
try:
    from domain.verification.existence_gate import check_mode  # noqa: E402
    from domain.verification.progress_delta import (  # noqa: E402
        ProgressTracker,
        detect_stall,
    )
    from domain.verification.soft_check import (  # noqa: E402
        check_missing_targets,
        detect_unresolved_imports,
    )
except ImportError:
    pytest.importorskip("polaris.domain")

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def test_check_mode_detects_mixed_create_modify(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")

    result = check_mode(["src/main.py", "src/new.py"], str(tmp_path))
    assert result.mode == "mixed"
    assert result.existing == ["src/main.py"]
    assert result.missing == ["src/new.py"]


def test_soft_check_missing_targets(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    missing = check_missing_targets(["app.py", "missing.py"], str(tmp_path))
    assert missing == ["missing.py"]


def test_soft_check_detects_unresolved_python_relative_import(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "module.py").write_text("from .missing import X\n", encoding="utf-8")
    unresolved = detect_unresolved_imports("pkg/module.py", str(tmp_path))
    assert unresolved
    assert unresolved[0].startswith("pkg/module.py:")


def test_progress_tracker_marks_stall_after_threshold() -> None:
    tracker = ProgressTracker(stall_threshold=2)
    first = tracker.update(files_created=0, missing_targets=["a.py"], errors=["E1"], unresolved_imports=[])
    second = tracker.update(files_created=0, missing_targets=["a.py"], errors=["E1"], unresolved_imports=[])
    third = tracker.update(files_created=0, missing_targets=["a.py"], errors=["E1"], unresolved_imports=[])

    assert first.is_stalled is False
    assert second.is_stalled is False
    assert third.is_stalled is True
    assert detect_stall([first, second, third], threshold=2) is True
