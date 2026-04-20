"""Run pytest with subprocess and correct path."""

import os
import subprocess
import sys

# Add the backend directory to the environment
env = os.environ.copy()
env["PYTHONPATH"] = r"C:\Users\dains\Documents\GitLab\polaris\src\backend"

result = subprocess.run(
    [
        r"C:\Users\dains\AppData\Local\Python\pythoncore-3.14-64\python.exe",
        "-m",
        "pytest",
        "test_message_bus.py",
        "-v",
        "--tb=short",
    ],
    cwd=r"C:\Users\dains\Documents\GitLab\polaris\src\backend\polaris\kernelone\events\tests",
    capture_output=True,
    text=True,
    env=env,
)

print(result.stdout)
print(result.stderr)
sys.exit(result.returncode)
