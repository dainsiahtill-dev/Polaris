"""A project may share a storage container without exposing its authority siblings."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.runtime.execution_broker.internal import project_verification_sandbox as sandbox
from polaris.cells.runtime.execution_broker.public.project_verification import ProjectVerificationArtifactInputV1


def _layout(monkeypatch: pytest.MonkeyPatch, home: Path, workspace: Path) -> None:
    monkeypatch.setattr(
        sandbox,
        "resolve_storage_roots",
        lambda _workspace: SimpleNamespace(
            config_root=str(home / "config"), runtime_root=str(workspace / ".polaris" / "runtime")
        ),
    )


def test_nested_workspace_runs_with_other_platform_state_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "platform"
    workspace = home / "projects" / "example"
    workspace.mkdir(parents=True)
    source = workspace / "source.txt"
    source.write_text("authoritative\n", encoding="utf-8")
    secret = home / "config" / "secret.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("private\n", encoding="utf-8")
    sibling = home / "projects" / "another" / "source.txt"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("other project\n", encoding="utf-8")
    local_secret = workspace / ".polaris" / "authority.txt"
    local_secret.parent.mkdir(parents=True)
    local_secret.write_text("authority\n", encoding="utf-8")
    _layout(monkeypatch, home, workspace)

    prepared = sandbox.prepare_project_verification_sandbox(
        workspace=str(workspace),
        inputs=(ProjectVerificationArtifactInputV1("source", "source.txt"),),
        request_hash="a" * 64,
    )
    try:
        prepared.release_after_fence()
        script = (
            "from pathlib import Path; "
            f"assert not Path({str(secret)!r}).exists(); "
            f"assert not Path({str(sibling)!r}).exists(); "
            "assert not Path('.polaris/authority.txt').exists(); "
            "assert Path('source.txt').read_text(encoding='utf-8') == 'authoritative\\n'; "
            "Path('build-result.txt').write_text('built\\n', encoding='utf-8')"
        )
        result = subprocess.run(
            prepared.wrap_command((sys.executable, "-c", script)),
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert (workspace / "build-result.txt").read_text(encoding="utf-8") == "built\n"
        prepared.assert_inputs_unchanged()
    finally:
        prepared.cleanup()
    assert secret.read_text(encoding="utf-8") == "private\n"
    assert sibling.read_text(encoding="utf-8") == "other project\n"
    assert local_secret.read_text(encoding="utf-8") == "authority\n"
    assert source.read_text(encoding="utf-8") == "authoritative\n"


@pytest.mark.parametrize("relative", [".", "config", "config/receipts"])
def test_platform_home_or_actual_config_is_not_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    home = tmp_path / "platform"
    workspace = home / relative
    workspace.mkdir(parents=True)
    _layout(monkeypatch, home, workspace)
    with pytest.raises(RuntimeError, match="protected Polaris authority"):
        sandbox._protected_platform_roots(workspace.resolve())
