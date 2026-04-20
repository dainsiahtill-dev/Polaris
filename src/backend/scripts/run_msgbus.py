"""Run message bus tests only."""

import os
import sys

import pytest

# Setup path and change directory
sys.path.insert(0, r"/")
os.chdir(r"/polaris/kernelone/events/tests")

exit_code = pytest.main(
    [
        "test_message_bus.py",
        "-v",
        "--tb=short",
    ]
)

print(f"\nExit code: {exit_code}")
sys.exit(exit_code)
