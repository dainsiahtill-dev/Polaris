"""Real-process regression tests for whole-tree timeout containment."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from polaris.kernelone.process import run_process_tree_safe


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        try:
            fields = proc_stat.read_text(encoding="utf-8").split()
        except OSError:
            return False
        return len(fields) > 2 and fields[2] != "Z"
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group regression")
def test_timeout_terminates_descendant_process_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_process_tree_safe(
            [sys.executable, str(parent), str(pid_file)],
            cwd=tmp_path,
            timeout=0.4,
        )

    child_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2.0
    while _pid_is_running(child_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert _pid_is_running(child_pid) is False
