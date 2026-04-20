"""Timeout test for AsyncWorker."""

import asyncio
import sys
import time
from pathlib import Path
from uuid import uuid4

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
    print("Timeout Test")
    print("=" * 60)
    print(f"Python: {PYTHON_EXE}")

    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    tmp_dir = _TEST_TMP_ROOT / f"worker_timeout_{int(time.time() * 1000)}_{uuid4().hex[:8]}"
    tmp_dir.mkdir(parents=True, exist_ok=False)
    print(f"Work dir: {tmp_dir}")

    try:
        config = AsyncWorkerConfig(
            worker_id="timeout-worker",
            work_dir=tmp_dir,
            max_idle_time=10,
            poll_interval=1.0,
        )

        worker = AsyncWorker(config)
        print("Worker created")

        await worker.start()
        print("Worker started")

        # Long running task with short timeout
        task = WorkerTask(
            task_id=1,
            command=f'"{PYTHON_EXE}" -c "import time; time.sleep(60); print(\'done\')"',
            work_dir=tmp_dir,
            env={},
            timeout=2,  # 2 second timeout
            metadata={"test": "timeout"},
        )

        print(f"Executing: {task.command}")
        print("Waiting for result (should timeout)...")
        result = await asyncio.wait_for(worker._execute_task_async(task), timeout=15.0)
        print(f"Result: success={result.success}")
        print(f"Error: {result.error}")

        await worker.stop(graceful=True)
        print("Worker stopped")

        if not result.success and ("timeout" in result.error.lower() or "Timeout" in result.error):
            print("\nTIMEOUT TEST PASSED")
        else:
            print("\nTIMEOUT TEST FAILED - expected timeout error")
    except asyncio.TimeoutError:
        print("GLOBAL TIMEOUT - test took too long")
    except (RuntimeError, ValueError) as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
    finally:
        pass


if __name__ == "__main__":
    asyncio.run(main())
