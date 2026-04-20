"""Run tests programmatically."""

import os
import sys

import pytest

# Change to the backend directory and add to path
os.chdir(r"/")
sys.path.insert(0, r"/")

print("Running message bus tests...")
exit_code = pytest.main(
    [
        "polaris/kernelone/events/tests/test_message_bus.py",
        "-v",
        "--tb=short",
        "-x",
    ]
)

if exit_code == 0:
    print("\n=== MESSAGE BUS TESTS PASSED ===")
else:
    print(f"\n=== MESSAGE BUS TESTS FAILED (exit code: {exit_code}) ===")

sys.exit(exit_code)
