from __future__ import annotations

import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
LOOP_CORE_ROOT = BACKEND_ROOT / "core" / "polaris_loop"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(LOOP_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_CORE_ROOT))

from core.polaris_loop.io_flags import (  # noqa: E402
    clear_director_stop_flag,
    director_stop_flag_path,
    director_stop_requested,
)


def test_director_stop_flag_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POLARIS_STATE_TO_RAMDISK", "0")
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    flag_path = Path(director_stop_flag_path(str(workspace)))
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text("stop\n", encoding="utf-8")

    assert director_stop_requested(str(workspace)) is True
    clear_director_stop_flag(str(workspace))
    assert director_stop_requested(str(workspace)) is False
