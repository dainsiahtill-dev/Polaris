"""Run tests and collect results."""

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
        ]
    )
    sys.exit(exit_code)
