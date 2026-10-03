"""Real private Python/Rust source executes through CE-bound native groups."""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.bootstrap.project_completion_diagnostics_owner import (
    PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER,
    configure_project_completion_diagnostics_owner,
)
from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    build_chief_engineer_blueprint_portfolio,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_public_contractsa import (
    _library_completion_requirements,
    _portfolio_command_authority,
)
from polaris.cells.factory.pipeline.internal.native_validation_group import verification_group
from polaris.cells.factory.pipeline.internal.native_validation_session import NativeValidationSession
from polaris.cells.runtime.execution_broker.public import (
    ProjectArtifactExecutionAuthorityV1,
    RecordProjectArtifactCommandV1,
    record_project_artifact,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


def test_unmounted_invocation_alias_is_not_admitted_by_mounted_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("private input", encoding="utf-8")
    alias = tmp_path / "unmounted-alias"
    alias.symlink_to("/usr/bin/true")
    with (
        verification_group(
            workspace=source,
            candidate_id="alias",
            input_hashes={
                "input.txt": hashlib.sha256(b"private input").hexdigest(),
            },
        ) as group,
        pytest.raises(RuntimeError, match="toolchain_unmounted"),
    ):
        group.prepare_command([str(alias)])


def test_multicall_alias_dispatch_and_retarget_are_independently_bound(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("private input", encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    target = tools / "multicall"
    target.write_text('#!/bin/sh\nprintf "%s\\n" "${0##*/}"\n', encoding="utf-8")
    target.chmod(0o755)
    alias = tools / "cargo"
    alias.symlink_to("multicall")
    with (
        pytest.raises(RuntimeError, match="invocation_drift"),
        verification_group(
            workspace=source,
            candidate_id="multicall",
            toolchain_roots=(tools,),
            input_hashes={"input.txt": hashlib.sha256(b"private input").hexdigest()},
        ) as group,
    ):
        result = group.execute_command(group.prepare_command([str(alias)]), timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip() == "cargo"
        target_bytes = target.read_bytes()
        prepared = group.prepare_command([str(alias)])
        alias.unlink()
        alias.symlink_to("./multicall")
        with pytest.raises(RuntimeError, match="invocation_drift"):
            group.execute_command(prepared, timeout=10)
        assert target.read_bytes() == target_bytes
        assert group.effects_summary()["source_binding_current"] is False
    assert group.staging_workspace.exists()


def test_rust_configuration_drift_after_freeze_rejects_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.cells.factory.pipeline.internal.native_validation_group import VerificationGroup

    monkeypatch.delenv("RUSTUP_TOOLCHAIN", raising=False)

    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("private input", encoding="utf-8")
    rust = tmp_path / "rust-home"
    (rust / "toolchains/stable/bin").mkdir(parents=True)
    (rust / "settings.toml").write_text('default_toolchain="stable"\n', encoding="utf-8")
    (rust / "toolchains/stable/bin/rustc").write_text("private toolchain fixture", encoding="utf-8")
    cargo = tmp_path / "cargo-home"
    cargo.mkdir()
    group = VerificationGroup(
        workspace=source,
        candidate_id="config",
        input_hashes={
            "input.txt": hashlib.sha256(b"private input").hexdigest(),
        },
        toolchain_roots=(),
        dependency_roots={},
        staging_parent=None,
        rustup_home=rust,
        cargo_home=cargo,
    )
    try:
        prepared = group.prepare_command(["/usr/bin/true"])
        (rust / "settings.toml").write_text('default_toolchain="other"\n', encoding="utf-8")
        with pytest.raises(RuntimeError, match="toolchain_configuration_drift"):
            group.execute_command(prepared, timeout=10)
    finally:
        group.close()
    assert group.staging_workspace.exists()


@pytest.mark.parametrize("foreign", ["foreign", "source", "site-packages", "symlink"])
def test_runtime_files_reject_undeclared_source_host_or_alias_paths(tmp_path: Path, foreign: str) -> None:
    from polaris.cells.factory.pipeline.internal.native_validation_group import VerificationGroup

    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("private input", encoding="utf-8")
    prefix = tmp_path / "python-runtime"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python3.12").write_text("private installed runtime", encoding="utf-8")
    if foreign == "source":
        selected = source / "foreign.py"
    elif foreign == "site-packages":
        selected = prefix / "lib/python3.12/site-packages/foreign.py"
    elif foreign == "symlink":
        selected = prefix / "lib/python3.12/foreign.py"
    else:
        selected = tmp_path / "foreign.py"
    selected.parent.mkdir(parents=True, exist_ok=True)
    if foreign == "symlink":
        selected.symlink_to(source / "input.txt")
    else:
        selected.write_text("unrelated host source", encoding="utf-8")
    denial = (
        "verification_toolchain_overlaps_source" if foreign == "source" else "verification_toolchain_file_undeclared"
    )
    with pytest.raises(RuntimeError, match=denial):
        VerificationGroup(
            workspace=source,
            candidate_id="foreign",
            input_hashes={
                "input.txt": hashlib.sha256(b"private input").hexdigest(),
            },
            dependency_roots={},
            toolchain_roots=(prefix / "bin",),
            staging_parent=None,
            toolchain_files=(selected,),
        )


def test_declared_runtime_file_is_readonly_and_replacement_blocks_launch(tmp_path: Path) -> None:
    from polaris.cells.factory.pipeline.internal.native_validation_group import VerificationGroup

    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("private input", encoding="utf-8")
    prefix = tmp_path / "python-runtime"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python3.12").write_text("private installed runtime", encoding="utf-8")
    library = prefix / "lib/python3.12/library.py"
    library.parent.mkdir(parents=True)
    library.write_text("LITERAL_RUNTIME\n", encoding="utf-8")
    group = VerificationGroup(
        workspace=source,
        candidate_id="runtime-file",
        input_hashes={
            "input.txt": hashlib.sha256(b"private input").hexdigest(),
        },
        dependency_roots={},
        toolchain_roots=(prefix / "bin",),
        staging_parent=None,
        toolchain_files=(library,),
    )
    try:
        first = group.execute_command(group.prepare_command(["/usr/bin/cat", str(library)]), timeout=10)
        assert first.returncode == 0 and first.stdout == "LITERAL_RUNTIME\n"
        denied = group.execute_command(
            group.prepare_command(["/bin/sh", "-c", f"printf polluted > '{library}'"]), timeout=10
        )
        assert denied.returncode != 0
        assert library.read_text(encoding="utf-8") == "LITERAL_RUNTIME\n"
        prepared = group.prepare_command(["/usr/bin/cat", str(library)])
        replacement = library.with_name("replacement.py")
        replacement.write_text("LITERAL_RUNTIME\n", encoding="utf-8")
        replacement.replace(library)
        with pytest.raises(RuntimeError, match="toolchain_file_drift"):
            group.execute_command(prepared, timeout=10)
    finally:
        group.close()
    assert group.staging_workspace.exists()


@pytest.mark.parametrize(
    "overlap", ["workspace", "workspace_canonical", "workspace_parent", "staging", "reserved_mount"]
)
def test_prefix_shaped_source_or_mount_runtime_file_rejected_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overlap: str
) -> None:
    from polaris.cells.factory.pipeline.internal import native_validation_group as module

    prefix = tmp_path / "runtime"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python3.12").write_text("private installed runtime", encoding="utf-8")
    stdlib = prefix / "lib/python3.12"
    stdlib.mkdir(parents=True)
    secret = stdlib / "excluded.py"
    secret.write_text("UNDECLARED_SOURCE_VISIBLE", encoding="utf-8")
    workspace = (
        stdlib
        if overlap in {"workspace", "workspace_canonical"}
        else prefix / "lib"
        if overlap == "workspace_parent"
        else tmp_path / "source"
    )
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "input.txt").write_text("admitted", encoding="utf-8")
    staging = stdlib if overlap == "staging" else None
    runtime_file = secret
    if overlap == "workspace_canonical":
        (prefix / "lib/unrelated").mkdir()
        runtime_file = prefix / "lib/unrelated/../python3.12/excluded.py"
    if overlap == "reserved_mount":
        monkeypatch.setattr(module, "_SYSTEM_ROOTS", (*module._SYSTEM_ROOTS, str(stdlib)))
    read = module._read
    reads: list[str] = []

    def observed_read(
        fs: KernelFileSystem, path: str, *, max_bytes: int = 16 * 1024 * 1024
    ) -> tuple[bytes, tuple[int, ...]]:
        if Path(fs.workspace) / path == secret:
            reads.append(path)
        return read(fs, path, max_bytes=max_bytes)

    monkeypatch.setattr(module, "_read", observed_read)
    paths_before = set(prefix.rglob("*"))
    with pytest.raises(RuntimeError, match=r"verification_toolchain_(overlaps_source|shadows_mount|overlaps_staging)"):
        module.VerificationGroup(
            workspace=workspace,
            candidate_id="review-overlap",
            input_hashes={"input.txt": hashlib.sha256(b"admitted").hexdigest()},
            dependency_roots={},
            toolchain_roots=(prefix / "bin",),
            staging_parent=staging,
            toolchain_files=(runtime_file,),
        )
    assert reads == []
    assert secret.read_text(encoding="utf-8") == "UNDECLARED_SOURCE_VISIBLE"
    assert set(prefix.rglob("*")) == paths_before
    assert not list(workspace.rglob("__pycache__"))


def _session(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str], command: list[str]
) -> NativeValidationSession:
    workspace.mkdir()
    names = tuple(files)
    tasks = (ChiefEngineerPortfolioTaskV1(task_id="LANGUAGE", objective="Author library", target_files=names),)
    portfolio = build_chief_engineer_blueprint_portfolio(
        BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(workspace),
            run_id="language-run",
            tasks=tasks,
            **_portfolio_command_authority(
                tasks=tasks, project_kind="library", workspace=workspace, run_id="language-run"
            ),
            llm_blueprint={
                "construction_plan": {"project_interface_contract": {}},
                "project_completion_contract": _library_completion_requirements(
                    names[0],
                    names[1],
                    owner_task_ids=("LANGUAGE", "LANGUAGE"),
                    test_path=names[2],
                    test_owner_task_id="LANGUAGE",
                ),
                "risk_flags": [],
            },
        )
    )
    contract = portfolio.project_completion_contract
    assert contract is not None

    def authority(query: Any) -> ProjectArtifactExecutionAuthorityV1:
        artifact = next(item for item in contract.obligations.artifacts if item.obligation_id == query.obligation_id)
        return ProjectArtifactExecutionAuthorityV1(
            workspace=query.workspace,
            project_id=contract.project_id,
            run_id=contract.run_id,
            completion_contract_hash=contract.contract_hash,
            obligation_id=artifact.obligation_id,
            owner_task_id=artifact.owner_task_id,
            path=artifact.path,
            job_token_id="fixture-language-owner",
            job_token_set_hash="c" * 64,
            execution_policy_hash="d" * 64,
            authority_revision="e" * 64,
        )

    configure_project_completion_diagnostics_owner()
    monkeypatch.setattr(PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER, "resolve_project_artifact_authority", authority)
    fs = KernelFileSystem(str(workspace), get_default_adapter())
    for artifact in contract.obligations.artifacts:
        fs.workspace_write_text(artifact.path, files[artifact.path], encoding="utf-8")
        record_project_artifact(
            RecordProjectArtifactCommandV1(
                workspace=str(workspace),
                project_id=contract.project_id,
                run_id=contract.run_id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=artifact.obligation_id,
                owner_task_id=artifact.owner_task_id,
                path=artifact.path,
            )
        )
    run = SimpleNamespace(
        id=contract.run_id,
        metadata={
            "stage_results": {
                "chief_engineer_review": {
                    "status": "success",
                    "artifacts": [portfolio.portfolio_path],
                }
            }
        },
    )
    executor = SimpleNamespace(
        workspace=workspace,
        _canonical_project_id=lambda _context: contract.project_id,
        _read_json_artifact_payload=lambda path: fs.read_json(path),
    )
    return NativeValidationSession.from_factory(executor, run, {}, commands=(command,))


@pytest.mark.asyncio
@pytest.mark.parametrize("python", [sys.executable, "/usr/bin/python3"])
async def test_native_python_entrypoint_imports_only_sandbox_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, python: str
) -> None:
    # A missing sandbox PYTHONPATH fails the import; inherited host paths leak env.
    monkeypatch.setenv("PYTHONPATH", "/host/shadow")
    monkeypatch.setenv("HOST_LANGUAGE_SECRET", "never-visible")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    files = {
        "src/main.py": "from src.engine import VALUE\nimport os, pathlib, json, unittest, socket, sys\n"
        "assert os.environ['PYTHONPATH'] == '/workspace'\n"
        "assert 'HOST_LANGUAGE_SECRET' not in os.environ\n"
        "if sys.prefix not in {'/usr', '/usr/local'}:\n"
        "    assert not pathlib.Path(sys.prefix, 'lib', 'python' + sys.version[:4], 'site-packages').exists()\n"
        "print(VALUE)\n",
        "src/engine.py": "VALUE = 'NATIVE_WORKSPACE_42'\n",
        "tests/test_engine.py": "from src.engine import VALUE\nassert VALUE == 'NATIVE_WORKSPACE_42'\n",
    }
    session = _session(tmp_path / "python-source", monkeypatch, files, [python, "src/main.py"])
    try:
        result = await session.run_command([python, "src/main.py"], 10)
        assert result["exit_code"] == 0, result.get("stderr_tail")
        assert result["stdout_tail"].strip() == "NATIVE_WORKSPACE_42"
        assert {name: (session.workspace / name).read_text(encoding="utf-8") for name in files} == files
        assert not list(session.workspace.rglob("__pycache__"))
    finally:
        session.close()


@pytest.mark.asyncio
async def test_native_rustup_cargo_test_runs_installed_toolchain_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Canonicalizing cargo to rustup loses proxy dispatch; absent home loses compiler.
    cargo = shutil.which("cargo")
    assert cargo is not None, "native acceptance requires installed Cargo"
    assert Path(cargo).is_symlink() and Path(cargo).resolve().name == "rustup"
    cache = tmp_path / "cargo-cache"
    for name in ("registry", "git"):
        (cache / name).mkdir(parents=True)
        (cache / name / "sentinel").write_text("host-cache", encoding="utf-8")
    monkeypatch.setenv("CARGO_HOME", str(cache))
    monkeypatch.setenv("RUSTFLAGS", "this-host-flag-must-not-leak")
    files = {
        "Cargo.toml": '[package]\nname="native_language_fixture"\nversion="0.1.0"\nedition="2021"\n',
        "src/lib.rs": "pub fn value() -> u32 { 42 }\n#[test]\nfn native_value() { assert_eq!(value(), 42); }\n",
        "tests/verify.rs": "#[test]\nfn native_public_value() {\n"
        "assert_eq!(native_language_fixture::value(), 42);\n"
        'assert_eq!(std::env::var("CARGO_NET_OFFLINE").unwrap(), "true");\n'
        'assert_eq!(std::env::var("RUSTUP_AUTO_INSTALL").unwrap(), "0");\n'
        'assert!(std::env::var("RUSTFLAGS").is_err());\n'
        'assert!(std::fs::write("src/lib.rs", "pollution").is_err());\n'
        'assert!(std::fs::write("/cargo-home/registry/sentinel", "pollution").is_err());\n'
        'assert!(std::fs::write("/cargo-home/git/sentinel", "pollution").is_err());\n'
        'let roots = std::fs::read_to_string("/proc/self/mountinfo").unwrap();\n'
        'let toolchain = std::env::var("RUSTUP_TOOLCHAIN").unwrap();\n'
        'assert!(roots.lines().any(|line| line.contains(&format!("/rustup/toolchains/{}/bin ro,", toolchain))));\n'
        'assert!(std::net::TcpStream::connect_timeout(&"1.1.1.1:80".parse().unwrap(), '
        "std::time::Duration::from_millis(100)).is_err());\n}\n",
    }
    session = _session(tmp_path / "rust-source", monkeypatch, files, ["cargo", "test", "--quiet"])
    try:
        result = await session.run_command(["cargo", "test", "--quiet"], 60)
        assert result["exit_code"] == 0, result.get("stderr_tail")
        assert result["stdout_tail"].count("1 passed; 0 failed") == 2
        assert {name: (session.workspace / name).read_text(encoding="utf-8") for name in files} == files
        assert not (session.workspace / "target").exists()
        assert not (session.workspace / "Cargo.lock").exists()
        assert (session.group.staging_workspace / "target").is_dir()
        for name in ("registry", "git"):
            assert (cache / name / "sentinel").read_text(encoding="utf-8") == "host-cache"
    finally:
        session.close()
