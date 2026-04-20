"""Run pytest for all test files."""

import os
import subprocess
import sys

env = os.environ.copy()
env["PYTHONPATH"] = r"C:\Users\dains\Documents\GitLab\polaris\src\backend"

test_files = [
    (r"C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\kernelone\events\tests", "test_message_bus.py"),
    (r"C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\kernelone\llm\engine\tests", "test_executor.py"),
    (
        r"C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\kernelone\context\engine\tests",
        "test_engine.py",
    ),
    (r"C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\kernelone\tests", "test_concurrency_safety.py"),
]

total_passed = 0
total_failed = 0

for test_dir, test_file in test_files:
    print(f"\n{'=' * 60}")
    print(f"Running: {test_file}")
    print(f"{'=' * 60}")

    result = subprocess.run(
        [
            r"C:\Users\dains\AppData\Local\Python\pythoncore-3.14-64\python.exe",
            "-m",
            "pytest",
            test_file,
            "-v",
            "--tb=short",
        ],
        cwd=test_dir,
        capture_output=True,
        text=True,
        env=env,
    )

    if "passed" in result.stdout:
        print(result.stdout)
    else:
        print(result.stdout)
        print(result.stderr)

    if result.returncode == 0:
        total_passed += 1
        print(f"=== {test_file}: PASSED ===")
    else:
        total_failed += 1
        print(f"=== {test_file}: FAILED ===")

print(f"\n{'=' * 60}")
print("TOTAL RESULTS:")
print(f"  Files Passed: {total_passed}")
print(f"  Files Failed: {total_failed}")
print(f"{'=' * 60}")

sys.exit(0 if total_failed == 0 else 1)
