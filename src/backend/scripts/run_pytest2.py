"""Run tests programmatically with limited collection."""

import os
import sys

import pytest

# Change to the backend directory and add to path
os.chdir(r"/")
sys.path.insert(0, r"/")

if __name__ == "__main__":
    # Run tests with verbose output
    exit_code = pytest.main(
        [
            "polaris/kernelone/events/tests/test_message_bus.py",
            "-v",
            "--tb=short",
            "-p",
            "no:xdist",
            "--no-header",
            "-x",  # Stop on first failure
        ]
    )

    if exit_code == 0:
        print("\n=== ALL TESTS PASSED ===")
    else:
        print(f"\n=== TESTS FAILED (exit code: {exit_code}) ===")

    sys.exit(exit_code)
