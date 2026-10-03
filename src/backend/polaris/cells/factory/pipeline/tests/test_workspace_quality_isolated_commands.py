"""Factory verifiers share disposable outputs, never host environment or source writes."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.factory_workspace_quality import WorkspaceQualityRunner
from polaris.cells.factory.pipeline.internal.native_validation_group import verification_group
from polaris.kernelone.process import ProcessTreeRunControl

pytestmark = pytest.mark.skipif(shutil.which("bwrap") is None or os.name != "posix", reason="real bwrap required")


def test_isolated_runner_build_test_start_share_outputs_without_live_writes(tmp_path: Path) -> None:
    """Independent per-command copies would lose the build output before test/start."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("authored", encoding="utf-8")
    runner = WorkspaceQualityRunner(source)
    with verification_group(
        workspace=source,
        candidate_id="candidate-closed-inputs",
        input_hashes={"input.txt": hashlib.sha256(b"authored").hexdigest()},
    ) as group:
        build = runner.run_isolated_command(
            ["/bin/sh", "-c", "mkdir -p dist; cp input.txt dist/result.txt"], 3, group, ProcessTreeRunControl()
        )
        test = runner.run_isolated_command(
            ["/bin/sh", "-c", "test $(cat dist/result.txt) = authored"], 3, group, ProcessTreeRunControl()
        )
        start = runner.run_isolated_command(["/bin/cat", "dist/result.txt"], 3, group, ProcessTreeRunControl())
        assert [row["exit_code"] for row in (build, test, start)] == [0, 0, 0]
        assert start["stdout_tail"] == "authored"
        assert start["verification_group"]["input_hash"] == build["verification_group"]["input_hash"]
        assert start["verification_group"]["source_binding_current"] is True
        assert start["verification_group"]["receipt_sealed"] is False
    assert not (source / "dist").exists()
    assert {p.name for p in source.iterdir()} == {"input.txt"}


def test_isolated_runner_does_not_inherit_provider_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing os.environ into the verifier would disclose these credentials."""
    (tmp_path / "input.txt").write_text("input", encoding="utf-8")
    monkeypatch.setenv("TEST_PROVIDER_SECRET", "synthetic-secret-never-expose")
    with verification_group(
        workspace=tmp_path,
        candidate_id="environment-boundary",
        input_hashes={"input.txt": hashlib.sha256(b"input").hexdigest()},
    ) as group:
        result = WorkspaceQualityRunner(tmp_path).run_isolated_command(
            ["/bin/sh", "-c", 'test -z "$TEST_PROVIDER_SECRET"'], 3, group, ProcessTreeRunControl()
        )
    assert result["passed"] is True


def test_isolated_runner_preserves_compiler_diagnostic_and_failure(tmp_path: Path) -> None:
    """A nonzero physical result must remain a failed verifier with exact diagnostics."""
    (tmp_path / "input.txt").write_text("input", encoding="utf-8")
    with verification_group(
        workspace=tmp_path,
        candidate_id="failed-verifier",
        input_hashes={"input.txt": hashlib.sha256(b"input").hexdigest()},
    ) as group:
        result = WorkspaceQualityRunner(tmp_path).run_isolated_command(
            ["/bin/sh", "-c", "echo 'src/code.ts(1,1): error TS6059: outside rootDir' >&2; exit 2"],
            3,
            group,
            ProcessTreeRunControl(),
        )
    assert result["passed"] is False
    assert result["exit_code"] == 2
    assert "TS6059" in result["diagnostic_excerpt"]
    assert result["verification_group"]["source_binding_current"] is True


@pytest.mark.skipif(shutil.which("cargo") is None, reason="Cargo resolution required")
def test_isolated_cargo_preserves_native_contract_preflight(tmp_path: Path) -> None:
    """A disposable copy must not bypass the existing native-test contract."""
    files = {
        "Cargo.toml": '[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n'
        '[[test]]\nname="custom"\npath="tests/custom.rs"\nharness=false\n',
        "src/main.rs": "fn main() {}\n",
        "tests/custom.rs": 'fn main() { println!("1 test passed"); }\n',
    }
    for path, text in files.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    with verification_group(
        workspace=tmp_path,
        candidate_id="cargo-preflight",
        input_hashes={path: hashlib.sha256(text.encode("utf-8")).hexdigest() for path, text in files.items()},
    ) as group:
        result = WorkspaceQualityRunner(tmp_path).run_isolated_command(
            ["cargo", "test"], 3, group, ProcessTreeRunControl()
        )
        assert result["passed"] is False
        assert "native_validation_contract_invalid" in result["error"]
        assert result["native_test_count"] == 0
        assert group.effects_summary()["commands"] == []
