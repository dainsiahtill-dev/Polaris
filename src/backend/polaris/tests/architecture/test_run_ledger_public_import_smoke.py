"""Cold-interpreter smoke for the canonical Run Ledger public surface."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_run_ledger_public_imports_in_fresh_interpreter() -> None:
    backend_root = Path(__file__).resolve().parents[3]
    environment = dict(os.environ)
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(token for token in (str(backend_root), existing_pythonpath) if token)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import polaris.cells.control_plane.run_ledger.public as public; "
            "assert public.__name__ == 'polaris.cells.control_plane.run_ledger.public'",
        ],
        cwd=backend_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
