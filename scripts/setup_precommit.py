#!/usr/bin/env python3
"""Helper script to install and verify pre-commit hooks for Polaris.

Usage:
    python scripts/setup_precommit.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command and return the completed process."""
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, check=False)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    print("Polaris pre-commit setup helper")
    print("=" * 40)

    # Check if pre-commit is installed
    result = run([sys.executable, "-m", "pre_commit", "--version"])
    if result.returncode != 0:
        print("pre-commit not found. Installing...")
        install = run([sys.executable, "-m", "pip", "install", "pre-commit"])
        if install.returncode != 0:
            print("ERROR: Failed to install pre-commit")
            print(install.stderr)
            return 1
        print("pre-commit installed successfully.")
    else:
        print(f"Found: {result.stdout.strip()}")

    # Install hooks
    print("\nInstalling git hooks...")
    install_hooks = run([sys.executable, "-m", "pre_commit", "install"], cwd=repo_root)
    if install_hooks.returncode != 0:
        print("ERROR: Failed to install hooks")
        print(install_hooks.stderr)
        return 1
    print("Git hooks installed.")

    # Optionally run on all files
    print("\nRunning pre-commit on all files (this may take a while)...")
    check = run(
        [sys.executable, "-m", "pre_commit", "run", "--all-files"],
        cwd=repo_root,
    )
    print(check.stdout)
    if check.returncode != 0:
        print("WARNING: Some hooks reported issues (see above).")
        # pre-commit returns non-zero when it fixes things or finds issues
        # We don't treat this as fatal for setup
    else:
        print("All hooks passed.")

    print("\nSetup complete. pre-commit will now run automatically on every commit.")
    print("To run manually: pre-commit run --all-files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
