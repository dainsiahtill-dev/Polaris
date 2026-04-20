"""Quick test script for AsyncWorker migration."""

import asyncio
import shutil
import sys
import time
from pathlib import Path
from uuid import uuid4

# Ensure polaris is importable
_test_file = Path(__file__).resolve()
_backend_path = _test_file.parent.parent.parent.parent.parent.parent
if str(_backend_path) not in sys.path:
    sys.path.insert(0, str(_backend_path))

from polaris.cells.roles.runtime.internal.worker_pool import (  # noqa: E402
    AsyncWorker,
    AsyncWorkerConfig,
    WorkerTask,
    create_async_worker_pool,
)

PYTHON_EXE = sys.executable
_TEST_TMP_ROOT = _backend_path / ".tmp_pytest_roles_runtime"


def make_temp_dir() -> Path:
    _TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = _TEST_TMP_ROOT / f"awt_{int(time.time() * 1000)}_{uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


async def test_basic() -> None:
    print("Test 1: Basic execution...", flush=True)
    work_dir = make_temp_dir()
    try:
        config = AsyncWorkerConfig(worker_id="t1", work_dir=work_dir, max_idle_time=10, poll_interval=1.0)
        worker = AsyncWorker(config)
        await worker.start()
        task = WorkerTask(
            task_id=1,
            command=f'"{PYTHON_EXE}" -c "print(1)"',
            work_dir=work_dir,
            env={},
            timeout=30,
            metadata={"test": "basic"},
        )
        result = await asyncio.wait_for(worker._execute_task_async(task), timeout=15.0)
        await worker.stop(graceful=True)
        assert result.success, f"Expected success=True, got {result.error}"
        assert result.metadata.get("cell") == "roles"
        print("  PASSED", flush=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def test_timeout() -> None:
    print("Test 2: Timeout...", flush=True)
    work_dir = make_temp_dir()
    try:
        config = AsyncWorkerConfig(worker_id="t2", work_dir=work_dir, max_idle_time=10, poll_interval=1.0)
        worker = AsyncWorker(config)
        await worker.start()
        task = WorkerTask(
            task_id=2,
            command=f'"{PYTHON_EXE}" -c "import time; time.sleep(60)"',
            work_dir=work_dir,
            env={},
            timeout=2,
            metadata={"test": "timeout"},
        )
        result = await asyncio.wait_for(worker._execute_task_async(task), timeout=15.0)
        await worker.stop(graceful=True)
        assert not result.success, "Expected failure"
        assert "timeout" in result.error.lower(), f"Expected timeout error, got {result.error}"
        assert result.metadata.get("cell") == "roles"
        print("  PASSED", flush=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def test_failure() -> None:
    print("Test 3: Command failure...", flush=True)
    work_dir = make_temp_dir()
    try:
        config = AsyncWorkerConfig(worker_id="t3", work_dir=work_dir, max_idle_time=10, poll_interval=1.0)
        worker = AsyncWorker(config)
        await worker.start()
        task = WorkerTask(
            task_id=3,
            command=f'"{PYTHON_EXE}" -c "import sys; sys.exit(99)"',
            work_dir=work_dir,
            env={},
            timeout=30,
            metadata={"test": "failure"},
        )
        result = await asyncio.wait_for(worker._execute_task_async(task), timeout=10.0)
        await worker.stop(graceful=True)
        assert not result.success, "Expected failure"
        assert "99" in result.error, f"Expected exit code 99, got {result.error}"
        print("  PASSED", flush=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def test_pool() -> None:
    print("Test 4: Worker pool...", flush=True)
    work_dir = make_temp_dir()
    try:
        pool = await create_async_worker_pool(work_base_dir=work_dir, max_workers=2)
        wid = await pool.spawn_worker("pool1")
        assert wid == "pool1"
        status = await pool.get_status()
        assert status["total_workers"] == 1
        task = WorkerTask(
            task_id=4,
            command=f'"{PYTHON_EXE}" -c "print(1)"',
            work_dir=work_dir,
            env={},
            timeout=30,
            metadata={"test": "pool"},
        )
        await pool.submit_task(task)
        await asyncio.sleep(2)
        await pool.shutdown_all(graceful=True)
        print("  PASSED", flush=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def main() -> int | None:
    print("=" * 50, flush=True)
    print("AsyncWorker Tests", flush=True)
    print("=" * 50, flush=True)
    try:
        await test_basic()
        await test_timeout()
        await test_failure()
        await test_pool()
        print("\nALL TESTS PASSED", flush=True)
        return 0
    except (RuntimeError, ValueError) as e:
        print(f"\nFAILED: {e}", flush=True)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
