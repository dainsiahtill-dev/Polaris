"""Cancellation must await the physical verifier, not only its asyncio wrapper."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.native_validation_execution import await_verification_worker
from polaris.kernelone.process import ProcessTreeDrainError, ProcessTreeRunControl, run_process_tree_safe


@pytest.mark.asyncio
async def test_repeated_cancellation_drains_real_descendant_before_return(tmp_path: Path) -> None:
    """Cancelling to_thread alone would leave this descendant writing later."""
    late_file = tmp_path / "late.txt"
    started = threading.Event()
    finished = threading.Event()
    child = "import pathlib,time,sys; time.sleep(0.8); pathlib.Path(sys.argv[1]).write_text('late',encoding='utf-8')"
    parent = (
        "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); time.sleep(5)"
    )

    def worker(control: ProcessTreeRunControl) -> subprocess.CompletedProcess[str]:
        started.set()
        try:
            return run_process_tree_safe(
                [sys.executable, "-c", parent, child, str(late_file)],
                timeout=6,
                cancel_control=control,
            )
        finally:
            finished.set()

    task = asyncio.create_task(await_verification_worker(worker))
    await asyncio.to_thread(started.wait, 2)
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    await asyncio.sleep(0.9)
    assert not late_file.exists()


@pytest.mark.asyncio
async def test_unproven_drain_is_preserved_after_outer_cancel() -> None:
    """A physical drain error must not be hidden behind caller cancellation."""
    started = threading.Event()

    def worker(control: ProcessTreeRunControl) -> None:
        started.set()
        while not control._cancelled():
            threading.Event().wait(0.005)
        raise ProcessTreeDrainError("real_owner_cannot_prove_terminal")

    task = asyncio.create_task(await_verification_worker(worker))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(ProcessTreeDrainError, match="cannot_prove_terminal"):
        await task


@pytest.mark.asyncio
async def test_normal_worker_preserves_physical_nonzero_exit() -> None:
    """Physical failure stays failed rather than being converted into success."""
    completed = await await_verification_worker(
        lambda control: run_process_tree_safe(
            [sys.executable, "-c", "raise SystemExit(7)"], timeout=2, cancel_control=control
        )
    )
    assert completed.returncode == 7
