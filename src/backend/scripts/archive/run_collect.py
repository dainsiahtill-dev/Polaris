"""Run tests with custom collection."""

import os
import sys

import pytest

# Change to the backend directory and add to path
os.chdir(r"/")
sys.path.insert(0, r"/")

# Collect only specific test file
exit_code = pytest.main(
    [
        "polaris/kernelone/events/tests/test_message_bus.py",
        "--collect-only",
        "-q",
    ]
)

print(f"Collection exit code: {exit_code}")
