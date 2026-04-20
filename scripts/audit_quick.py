#!/usr/bin/env python3
"""Compatibility entrypoint for backend audit_quick script.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

from __future__ import annotations

import runpy
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    target = repo_root / "src" / "backend" / "scripts" / "audit_quick.py"
    if not target.exists():
        raise FileNotFoundError(f"audit_quick backend entrypoint not found: {target}")
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
