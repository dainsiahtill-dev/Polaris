"""Process-level proof that backend startup resumes persisted Factory work."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_models import (
    FactoryConfig,
    FactoryRunStatus,
)
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryRunService

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_STARTUP_TIMEOUT_SECONDS = 60.0
_RECOVERY_TIMEOUT_SECONDS = 60.0


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


async def _seed_interrupted_run(workspace: Path) -> tuple[str, Path]:
    service = FactoryRunService(workspace=workspace)
    run = await service.create_run(
        FactoryConfig(
            name="process-recovery-proof",
            description="Verify backend startup resumes persisted Factory work.",
            stages=["quality_gate"],
        )
    )
    # Model the durable bytes left behind by a hard process crash.  The new
    # backend must consume this RUNNING snapshot without a new HTTP start call.
    run.status = FactoryRunStatus.RUNNING
    run.metadata["factory_start_request"] = {
        "workspace": str(workspace),
        "start_from": "pm",
        "run_director": False,
        "director_iterations": 0,
        "loop": False,
    }
    await service.store.save_run(run)
    return run.id, service.store.get_run_dir(run.id) / "run.json"


def _read_status(run_file: Path) -> str:
    payload = json.loads(run_file.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return str(payload.get("status") or "").strip().lower()


def _wait_for_identity(port: int, token: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v2/resident/identity",
        headers={"Authorization": f"Bearer {token}"},
    )
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"backend exited before identity became ready: exit={process.returncode}")
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                if response.status == 200:
                    return
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise AssertionError(f"backend identity did not become ready: {last_error}")


def _wait_for_recovery_effect(run_file: Path) -> str:
    deadline = time.monotonic() + _RECOVERY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = _read_status(run_file)
        if status not in {
            FactoryRunStatus.RUNNING.value,
            FactoryRunStatus.RECOVERING.value,
        }:
            return status
        time.sleep(0.1)
    raise AssertionError("persisted Factory run did not reach a terminal state after backend startup")


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def test_backend_startup_recovers_persisted_running_factory_run_without_http_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    ramdisk_root = tmp_path / "ramdisk"
    workspace.mkdir(parents=True)
    ramdisk_root.mkdir(parents=True)
    monkeypatch.setenv("KERNELONE_RAMDISK_ROOT", str(ramdisk_root))
    run_id, run_file = asyncio.run(_seed_interrupted_run(workspace))
    assert _read_status(run_file) == FactoryRunStatus.RUNNING.value

    port = _unused_loopback_port()
    token = "factory-driver-process-recovery-token"
    env = os.environ.copy()
    env.update(
        {
            "KERNELONE_TOKEN": token,
            "KERNELONE_NATS_ENABLED": "0",
            "KERNELONE_NATS_REQUIRED": "0",
            "KERNELONE_JETSTREAM_PUBLISH": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    log_file = tmp_path / "backend.log"
    with log_file.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "polaris.delivery.server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workspace",
                str(workspace),
                "--token",
                token,
                "--ramdisk-root",
                str(ramdisk_root),
                "--log-level",
                "warning",
                "--no-self-upgrade-mode",
            ],
            cwd=_BACKEND_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        try:
            _wait_for_identity(port, token, process)
            recovered_status = _wait_for_recovery_effect(run_file)
        except (AssertionError, OSError, ValueError):
            log.flush()
            evidence = log_file.read_text(encoding="utf-8")[-12000:]
            raise AssertionError(f"backend recovery failed for {run_id}:\n{evidence}") from None
        finally:
            _stop_process(process)

    # The empty fixture is expected to fail its physical quality gate.  The
    # proof is that startup owned and executed the persisted run at all; it no
    # longer remains orphaned in RUNNING waiting for another HTTP request.
    assert recovered_status == FactoryRunStatus.FAILED.value
    with suppress(OSError):
        assert process.poll() is not None
