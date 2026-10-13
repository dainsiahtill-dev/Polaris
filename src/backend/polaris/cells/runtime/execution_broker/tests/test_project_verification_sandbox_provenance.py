"""A physical verifier must execute the platform isolation binary, not a PATH shim."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.runtime.execution_broker.internal import project_verification_sandbox as sandbox
from polaris.cells.runtime.execution_broker.public.project_verification import ProjectVerificationArtifactInputV1
from polaris.kernelone.fs import GuardedRegularFileSnapshotV1


def test_caller_path_shim_is_rejected_before_preparing_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("original\n", encoding="utf-8")
    shim = tmp_path / "caller-tools" / "bwrap"
    shim.parent.mkdir()
    shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(shim.parent) + os.pathsep + os.environ.get("PATH", ""))
    prepared = None
    try:
        with pytest.raises(RuntimeError, match="verification_isolation_binary_untrusted"):
            prepared = sandbox.prepare_project_verification_sandbox(
                workspace=str(tmp_path),
                inputs=(ProjectVerificationArtifactInputV1("source", "source.txt"),),
                request_hash="a" * 64,
            )
    finally:
        if prepared is not None:
            prepared.cleanup()
    assert source.read_text(encoding="utf-8") == "original\n"


def test_binary_witness_is_rechecked_before_physical_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("original\n", encoding="utf-8")
    prepared = sandbox.prepare_project_verification_sandbox(
        workspace=str(tmp_path),
        inputs=(ProjectVerificationArtifactInputV1("source", "source.txt"),),
        request_hash="b" * 64,
    )
    try:
        snapshot = getattr(prepared, "bwrap_snapshot", None)
        assert isinstance(snapshot, GuardedRegularFileSnapshotV1)
        prepared.bwrap_snapshot = replace(snapshot, ctime_ns=snapshot.ctime_ns + 1)
        with pytest.raises(RuntimeError, match="verification_isolation_binary_drift"):
            prepared.wrap_command(("python", "-V"))
        with pytest.raises(RuntimeError, match="verification_isolation_binary_drift"):
            prepared.assert_inputs_unchanged()
    finally:
        prepared.cleanup()
