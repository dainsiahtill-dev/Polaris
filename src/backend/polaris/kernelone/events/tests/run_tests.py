"""Run tests from events/tests directory."""

import os
import sys

# Add to path
sys.path.insert(0, r"C:\Users\dains\Documents\GitLab\polaris\src\backend")

# Change to events/tests directory
os.chdir(r"C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\kernelone\events\tests")

import pytest  # noqa: E402

print("Running message bus tests from test directory...")
exit_code = pytest.main(
    [
        "test_message_bus.py",
        "-v",
        "--tb=short",
        "-x",
        "-p",
        "no:xdist",
    ]
)

if exit_code == 0:
    print("\n=== MESSAGE BUS TESTS PASSED ===")
else:
    print(f"\n=== MESSAGE BUS TESTS FAILED (exit code: {exit_code}) ===")

sys.exit(exit_code)
