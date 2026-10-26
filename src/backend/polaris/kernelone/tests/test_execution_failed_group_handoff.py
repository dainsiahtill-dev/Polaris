"""An official failed spawn retains one in-process owner, never fake authority."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.kernelone.fs import get_default_adapter
from polaris.kernelone.process import ProcessTreeDrainError, async_contracts as process_owner, process_tree
from polaris.kernelone.process.tests.test_popen_timeout_lifecycle import _birth, _release, _spawn
from polaris.kernelone.runtime.execution_runtime import ExecutionRuntime, ExecutionStatus


@pytest.mark.asyncio
async def test_failed_official_group_binding_hands_same_resource_to_runtime_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fs = get_default_adapter()
    script = tmp_path / "blocked.py"
    fs.write_text(str(script), "import os\nos.read(0,1)\n", encoding="utf-8")
    home = str(tmp_path / "kernel-home")
    argv = [sys.executable, "-I", "-S", "-B", str(script)]
    observed = []
    runtime = ExecutionRuntime(process_concurrency=1)
    original_bind = process_owner._bind_spawned_process_group
    try:
        with monkeypatch.context() as patch:

            def unavailable(proc):
                observed.append((proc, _birth(proc.pid)))
                raise PermissionError("controlled group binding unavailable")

            patch.setattr(process_owner, "_bind_spawned_process_group", unavailable)
            with pytest.raises(ProcessTreeDrainError, match=r"binding.*unproven") as failure:
                await runtime.submit_process(
                    name="failed-binding",
                    args=argv,
                    cwd=tmp_path,
                    env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "KERNELONE_HOME": home},
                    timeout_seconds=5,
                )
            snapshots = runtime.list_snapshots()
            assert len(snapshots) == 1
            snapshot = snapshots[0]
            assert snapshot.status is ExecutionStatus.FAILED and snapshot.ok is False
            assert snapshot.metadata["process_cleanup_status"] == "unknown"
            assert snapshot.metadata["process_slot_retained"] is True
            assert runtime._require_state(snapshot.execution_id).process_handle is failure.value.owned_resource
            assert snapshot.pid == observed[0][0].pid
            json.dumps(snapshot.to_dict())  # Ephemeral owned_resource is not serialized.
            assert "owned_resource" not in snapshot.metadata
        assert process_owner._bind_spawned_process_group is original_bind
        assert await runtime.terminate(snapshot.execution_id, timeout=0.02) is True
        final = runtime.get_snapshot(snapshot.execution_id)
        assert final.status is ExecutionStatus.FAILED
        assert final.metadata["process_cleanup_status"] == "confirmed"
        assert final.result.exit_code == observed[0][0].returncode
    finally:
        for proc, token in observed:
            if proc.poll() is None:
                assert _birth(proc.pid) == token
                assert [v.decode("utf-8") for v in fs.read_bytes(f"/proc/{proc.pid}/cmdline").split(b"\0") if v] == argv
                assert ("KERNELONE_HOME=" + home).encode("utf-8") in fs.read_bytes(f"/proc/{proc.pid}/environ").split(
                    b"\0"
                )
                proc.kill()
            await asyncio.to_thread(proc.wait, 5.0)
            for pipe in (proc.stdout, proc.stderr, proc.stdin):
                pipe.close()
            try:
                remaining_birth = _birth(proc.pid)
            except (FileNotFoundError, ProcessLookupError):
                remaining_birth = None
            assert remaining_birth != token
            fs.write_text(
                str(tmp_path / "owned-handoff-cleanup.json"),
                json.dumps(
                    {
                        "pid": proc.pid,
                        "start_token": token,
                        "argv": argv,
                        "private_home": home,
                        "remaining_birth": remaining_birth,
                        "returncode": proc.returncode,
                        "pipes_closed": all(pipe.closed for pipe in (proc.stdout, proc.stderr, proc.stdin)),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        await runtime.close()


@pytest.mark.asyncio
async def test_forged_owned_resource_is_not_registered_as_a_process_owner(tmp_path: Path) -> None:
    fake = SimpleNamespace(pid=12345)
    failure = ProcessTreeDrainError("forged resource")
    failure.owned_resource = fake

    class FakeRunner:
        async def spawn(self, **kwargs):
            raise failure

    runtime = ExecutionRuntime(process_runner_factory=FakeRunner, process_concurrency=1)
    try:
        with pytest.raises(ProcessTreeDrainError, match="forged resource"):
            await runtime.submit_process(name="forged", args=["does-not-run"], cwd=tmp_path)
        state = next(iter(runtime._states.values()))
        assert state.process_handle is None and state.pid is None
        assert runtime._process_semaphore.locked() is False
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_mismatched_group_identity_is_rejected_before_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = await _spawn(tmp_path)
    handle = child[0]
    identity = handle._group_identity
    seen = []
    try:
        handle._group_identity = replace(identity, group_id=os.getpgrp())

        def forbidden(*args, **kwargs):
            seen.append(True)
            raise AssertionError("invalid group reached OS signal")

        with monkeypatch.context() as patch:
            patch.setattr(process_tree.os, "killpg", forbidden)
            with pytest.raises(ProcessTreeDrainError, match=r"group.*bound"):
                await handle.kill()
        assert seen == [] and handle.process.poll() is None
    finally:
        handle._group_identity = identity
        await _release(child)
