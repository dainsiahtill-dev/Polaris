"""One consistent browser environment for the browser observation tests.

The browser capture runs in its own subprocess with an explicit interpreter
and an explicit package path. Both have to come from the same Python
environment: a package path from another Python version cannot load its
compiled extensions, and the capture then fails before it observes
anything. The three browser modules share this resolution so a test run
cannot depend on which interpreter happened to start pytest.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path


def browser_executable() -> Path:
    """Resolve the browser binary the installed Playwright expects."""
    configured = os.environ.get("POLARIS_TEST_BROWSER_EXECUTABLE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        return Path(playwright.chromium.executable_path).resolve()


def browser_python() -> Path:
    """Resolve the interpreter that runs the capture program."""
    configured = os.environ.get("POLARIS_TEST_BROWSER_PYTHON", "").strip()
    return Path(configured or sys.executable).expanduser().resolve()


def browser_packages() -> Path:
    """Resolve the package path of the same environment as the interpreter."""
    configured = os.environ.get("POLARIS_TEST_BROWSER_PACKAGES", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(sysconfig.get_paths()["purelib"]).resolve()


def browser_python_reads(python: Path) -> tuple[Path, ...]:
    """Resolve the interpreter roots the capture subprocess may read."""
    return () if python.is_relative_to("/usr") else (python.parent.parent,)
