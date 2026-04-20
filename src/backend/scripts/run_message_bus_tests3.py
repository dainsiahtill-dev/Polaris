"""Run tests with no config file."""

import sys

# Add to path
sys.path.insert(0, r"/")

import pytest

print("Running message bus tests...")
exit_code = pytest.main(
    [
        r"C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\kernelone\events\tests\test_message_bus.py",
        "-v",
        "--tb=short",
        "-x",
        "-p",
        "no:xdist",
        "--ignore=pytest.ini",
    ]
)

if exit_code == 0:
    print("\n=== MESSAGE BUS TESTS PASSED ===")
else:
    print(f"\n=== MESSAGE BUS TESTS FAILED (exit code: {exit_code}) ===")

sys.exit(exit_code)
