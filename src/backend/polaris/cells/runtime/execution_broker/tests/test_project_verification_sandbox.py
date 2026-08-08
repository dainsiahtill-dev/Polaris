"""Adversarial tests for physical verifier isolation and immutable inputs."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from polaris.cells.runtime.execution_broker.public.project_verification import (
    ProjectVerificationArtifactInputV1,
)


@pytest.mark.skipif(os.name != "posix", reason="bubblewrap verifier sandbox is Linux-only")
def test_verifier_sandbox_hides_platform_authority_and_pins_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.runtime.execution_broker.internal import project_verification_authority
    from polaris.cells.runtime.execution_broker.internal.project_verification_sandbox import (
        prepare_project_verification_sandbox,
    )

    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("ORIGINAL\n", encoding="utf-8")
    monkeypatch.setenv("POLARIS_F3C_AUDIT_SECRET", "secret-sentinel")
    workspace_authority = tmp_path / ".polaris" / "authority.json"
    workspace_authority.parent.mkdir(parents=True)
    workspace_authority.write_text("ORIGINAL\n", encoding="utf-8")
    key_path = project_verification_authority._auth_key_path(str(tmp_path))
    database_path = project_verification_authority._db_path(str(tmp_path))
    project_verification_authority._receipt_auth_key(str(tmp_path))
    original_key = key_path.read_bytes()
    assert not database_path.exists()
    prepared = prepare_project_verification_sandbox(
        workspace=str(tmp_path),
        inputs=(ProjectVerificationArtifactInputV1("source", "src/main.py"),),
        request_hash="a" * 64,
    )
    try:
        prepared.release_after_fence()
        script = (
            "import os,pathlib; "
            "assert os.environ['HOME'] == '/tmp/polaris-verifier-home'; "
            "assert 'POLARIS_F3C_AUDIT_SECRET' not in os.environ; "
            f"key=pathlib.Path({str(key_path)!r}); db=pathlib.Path({str(database_path)!r}); "
            "assert not key.exists() and not db.exists(); "
            "key.parent.mkdir(parents=True,exist_ok=True); key.write_text('FORGED'); "
            "db.parent.mkdir(parents=True,exist_ok=True); db.write_text('FORGED'); "
            "local=pathlib.Path('.polaris/authority.json'); "
            "assert not local.exists(); local.parent.mkdir(parents=True,exist_ok=True); local.write_text('FORGED'); "
            "p=pathlib.Path('src/main.py'); "
            "assert p.read_text() == 'ORIGINAL\\n'; "
            "p.write_text('FORGED\\n')"
        )
        completed = subprocess.run(
            prepared.wrap_command((os.environ.get("PYTHON", "python"), "-c", script)),
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode != 0
        assert source.read_text(encoding="utf-8") == "ORIGINAL\n"
        assert workspace_authority.read_text(encoding="utf-8") == "ORIGINAL\n"
        assert key_path.read_bytes() == original_key
        assert not database_path.exists()
        prepared.assert_inputs_unchanged()
    finally:
        prepared.cleanup()


@pytest.mark.skipif(os.name != "posix", reason="bubblewrap verifier sandbox is Linux-only")
def test_verifier_command_cannot_execute_before_durable_fence_release(tmp_path: Path) -> None:
    from polaris.cells.runtime.execution_broker.internal.project_verification_sandbox import (
        prepare_project_verification_sandbox,
    )

    source = tmp_path / "main.py"
    sentinel = tmp_path / "executed.txt"
    source.write_text("print('ok')\n", encoding="utf-8")
    prepared = prepare_project_verification_sandbox(
        workspace=str(tmp_path),
        inputs=(ProjectVerificationArtifactInputV1("source", "main.py"),),
        request_hash="d" * 64,
    )
    process = subprocess.Popen(
        prepared.wrap_command(
            (
                os.environ.get("PYTHON", "python"),
                "-c",
                "from pathlib import Path; Path('executed.txt').write_text('yes')",
            )
        ),
        cwd=tmp_path,
    )
    try:
        time.sleep(0.1)
        assert not sentinel.exists()
        prepared.release_after_fence()
        assert process.wait(timeout=5.0) == 0
        assert sentinel.read_text(encoding="utf-8") == "yes"
    finally:
        if process.poll() is None:
            process.kill()
        prepared.cleanup()


@pytest.mark.skipif(os.name != "posix", reason="bubblewrap verifier sandbox is Linux-only")
def test_verifier_sandbox_uses_exact_contained_cwd(tmp_path: Path) -> None:
    from polaris.cells.runtime.execution_broker.internal.project_verification_sandbox import (
        prepare_project_verification_sandbox,
    )

    source = tmp_path / "sub" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    prepared = prepare_project_verification_sandbox(
        workspace=str(tmp_path),
        inputs=(ProjectVerificationArtifactInputV1("source", "sub/main.py"),),
        request_hash="e" * 64,
        cwd="sub",
    )
    try:
        prepared.release_after_fence()
        completed = subprocess.run(
            prepared.wrap_command((os.environ.get("PYTHON", "python"), "-c", "import os; print(os.getcwd())")),
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == str(tmp_path / "sub")
    finally:
        prepared.cleanup()


def test_verifier_sandbox_rejects_authority_cwd_and_input(tmp_path: Path) -> None:
    from polaris.cells.runtime.execution_broker.internal.project_verification_sandbox import (
        prepare_project_verification_sandbox,
    )

    authority = tmp_path / ".polaris" / "authority.json"
    authority.parent.mkdir(parents=True)
    authority.write_text("secret\n", encoding="utf-8")
    with pytest.raises(ValueError, match="authority storage"):
        prepare_project_verification_sandbox(
            workspace=str(tmp_path),
            inputs=(ProjectVerificationArtifactInputV1("authority", ".polaris/authority.json"),),
            request_hash="f" * 64,
        )
    with pytest.raises(ValueError, match="authority storage"):
        prepare_project_verification_sandbox(
            workspace=str(tmp_path),
            inputs=(ProjectVerificationArtifactInputV1("authority", ".polaris/authority.json"),),
            request_hash="0" * 64,
            cwd=".polaris",
        )


def test_verifier_sandbox_fails_closed_without_bubblewrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.runtime.execution_broker.internal import project_verification_sandbox

    source = tmp_path / "main.py"
    source.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(project_verification_sandbox.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="bubblewrap"):
        project_verification_sandbox.prepare_project_verification_sandbox(
            workspace=str(tmp_path),
            inputs=(ProjectVerificationArtifactInputV1("source", "main.py"),),
            request_hash="b" * 64,
        )


def test_verifier_sandbox_rejects_snapshot_copy_divergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.runtime.execution_broker.internal import project_verification_sandbox

    source = tmp_path / "main.py"
    source.write_text("ORIGINAL\n", encoding="utf-8")

    def _forge_copy(_source: Path, target: Path) -> None:
        target.write_text("FORGED\n", encoding="utf-8")

    monkeypatch.setattr(project_verification_sandbox.shutil, "copy2", _forge_copy)

    with pytest.raises(ValueError, match="immutable snapshot diverged"):
        project_verification_sandbox.prepare_project_verification_sandbox(
            workspace=str(tmp_path),
            inputs=(ProjectVerificationArtifactInputV1("source", "main.py"),),
            request_hash="c" * 64,
        )
