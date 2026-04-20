"""Run all new tests with comprehensive reporting."""

import os
import sys

# Add to path
sys.path.insert(0, r"/")

import pytest

test_files = [
    r"C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\kernelone\events\tests\test_message_bus.py",
    r"C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\kernelone\llm\engine\tests\test_executor.py",
    r"C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\kernelone\context\engine\tests\test_engine.py",
    r"C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\kernelone\tests\test_concurrency_safety.py",
]

total_passed = 0
total_failed = 0
total_skipped = 0

for test_file in test_files:
    print(f"\n{'=' * 60}")
    print(f"Running: {os.path.basename(test_file)}")
    print("=" * 60)

    exit_code = pytest.main(
        [
            test_file,
            "-v",
            "--tb=short",
            "-x",
        ]
    )

    if exit_code == 0:
        total_passed += 1
        print(f"=== {os.path.basename(test_file)}: PASSED ===")
    else:
        total_failed += 1
        print(f"=== {os.path.basename(test_file)}: FAILED ===")

print(f"\n{'=' * 60}")
print("TOTAL RESULTS:")
print(f"  Passed: {total_passed}")
print(f"  Failed: {total_failed}")
print(f"{'=' * 60}")

sys.exit(0 if total_failed == 0 else 1)
