from __future__ import annotations

import runpy
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    current = start
    for _ in range(12):
        if (current / ".polaris").exists() or (current / ".polaris").exists() or (current / ".git").exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    return start


def load_root_test(module_globals: dict, relative_path: str) -> None:
    root = _find_repo_root(Path(__file__).resolve())
    target = root / relative_path
    if not target.is_file():
        raise FileNotFoundError(f"Missing root test file: {relative_path}")
    module_globals.update(runpy.run_path(str(target)))
