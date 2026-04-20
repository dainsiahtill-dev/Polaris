#!/usr/bin/env python3
"""Test runner for Claude-style Agent TUI.

Usage:
    python run_tests.py                    # Run all tests
    python run_tests.py -v                 # Run with verbose output
    python run_tests.py --cov              # Run with coverage
    python run_tests.py -k test_name       # Run specific test
"""

import subprocess
import sys
from pathlib import Path


def main():
    """Run the test suite."""
    # Get the test file path
    test_file = Path(__file__).parent / "polaris" / "delivery" / "cli" / "textual" / "tests" / "test_textual_console.py"

    if not test_file.exists():
        print(f"Error: Test file not found at {test_file}")
        sys.exit(1)

    # Build pytest command
    cmd = ["python", "-m", "pytest", str(test_file)]

    # Add any additional arguments passed to this script
    cmd.extend(sys.argv[1:])

    print(f"Running: {' '.join(cmd)}")
    print("=" * 60)

    # Run tests
    result = subprocess.run(cmd, cwd=Path(__file__).parent)

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
