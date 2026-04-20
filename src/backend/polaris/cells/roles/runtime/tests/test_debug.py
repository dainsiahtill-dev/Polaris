"""Simplified debug test for AsyncWorker."""

import asyncio
import sys
import time
from pathlib import Path
from uuid import uuid4

# Ensure polaris is importable
sys.path.insert(0, ".")

from polaris.cells.roles.runtime.internal.worker_pool import (
    AsyncWorker,
    AsyncWorkerConfig,
    WorkerTask,
)

PYTHON_EXE = sys.executable
_TEST_TMP_ROOT = Path(__file__).resolve().parents[5] / ".tmp_pytest_roles_runtime"


async def main() -> None:
    print("=" * 60)
    print("AsyncWorker Debug Test")
    print("=" * 60)
    print(f"Python: {PYTHON_EXE}")

    # Create temp dir manually
    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    tmp_dir = _TEST_TMP_ROOT / f"worker_debug_{int(time.time() * 1000)}_{uuid4().hex[:8]}"
    tmp_dir.mkdir(parents=True, exist_ok=False)
    print(f"Work dir: {tmp_dir}")

    try:
        config = AsyncWorkerConfig(
            worker_id="debug-worker",
            work_dir=tmp_dir,
            max_idle_time=10,
            poll_interval=1.0,
        )

        worker = AsyncWorker(config)
        print("Worker created")

        await worker.start()
        print("Worker started")

        task = WorkerTask(
            task_id=1,
            command=f'"{PYTHON_EXE}" -c "print(\'hello\')"',
            work_dir=tmp_dir,
            env={},
            timeout=10,
            metadata={"test": "debug"},
        )

        print(f"Executing: {task.command}")
        result = await worker._execute_task_async(task)
        print(f"Result: success={result.success}, error={result.error}")

        await worker.stop(graceful=True)
        print("Worker stopped")

        print("\nSUCCESS" if result.success else "\nFAILED")
    finally:
        # Don't cleanup - may be locked on Windows
        pass


if __name__ == "__main__":
    asyncio.run(main())
