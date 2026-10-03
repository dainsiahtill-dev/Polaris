"""Final review regressions exercise installed tools and native verifier effects."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.native_validation_group import VerificationGroup
from polaris.kernelone.process import run_process_tree_safe


@pytest.mark.parametrize("selector", ["stable", "canonical", "unavailable-review-toolchain"])
def test_installed_rustup_selector_is_pinned_without_default_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: str
) -> None:
    rustup = shutil.which("rustup")
    assert rustup is not None, "native qualification requires installed Rustup"
    rust_home = Path(os.environ.get("RUSTUP_HOME") or Path.home() / ".rustup")
    environment = {"RUSTUP_HOME": str(rust_home), "RUSTUP_AUTO_INSTALL": "0", "PATH": "/usr/bin:/bin"}
    installed = run_process_tree_safe(
        [rustup, "which", "--toolchain", "stable", "rustc"],
        timeout=10,
        env=environment,
        encoding="utf-8",
    )
    assert installed.returncode == 0, installed.stderr
    canonical = Path(installed.stdout.strip()).parent.parent.name
    selected = canonical if selector == "canonical" else selector
    monkeypatch.setenv("RUSTUP_TOOLCHAIN", selected)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    content = "#[test] fn native_value() { assert_eq!(6 * 7, 42); }\n"
    (workspace / "main.rs").write_text(content, encoding="utf-8")
    cargo_home = tmp_path / "cargo-home"
    cargo_home.mkdir()

    def open_group() -> VerificationGroup:
        return VerificationGroup(
            workspace=workspace,
            candidate_id="final-selector",
            input_hashes={"main.rs": hashlib.sha256(content.encode("utf-8")).hexdigest()},
            dependency_roots={},
            toolchain_roots=(Path(rustup).parent,),
            staging_parent=None,
            rustup_home=rust_home,
            cargo_home=cargo_home,
        )

    if selector == "unavailable-review-toolchain":
        before = tuple(sorted((rust_home / "toolchains").iterdir()))
        with pytest.raises((OSError, RuntimeError), match="toolchain"):
            open_group()
        assert tuple(sorted((rust_home / "toolchains").iterdir())) == before
        assert list(cargo_home.iterdir()) == []
        return
    group = open_group()
    try:
        prepared = group.prepare_command([str(Path(rustup).parent / "rustc"), "--test", "main.rs", "-o", "verify"])
        assert dict(prepared.environment_policy)["RUSTUP_TOOLCHAIN"] == canonical
        result = group.execute_command(prepared, timeout=60)
        assert result.returncode == 0, result.stderr
        result = group.execute_command(group.prepare_command(["/bin/sh", "-c", "./verify"]), timeout=10)
        assert result.returncode == 0, result.stderr
        assert "1 passed; 0 failed" in result.stdout
        assert (workspace / "main.rs").read_text(encoding="utf-8") == content
        assert not (workspace / "verify").exists()
    finally:
        group.close()


@pytest.mark.parametrize("fallback", [False, True])
def test_verifier_toml_imports_use_real_compatible_parser(tmp_path: Path, fallback: bool) -> None:
    parser_root = os.environ.get("POLARIS_TEST_TOMLI_ROOT", "")
    script = """
import sys
import importlib
from pathlib import Path
from polaris.cells.factory.pipeline.internal import native_validation_group as group
from polaris.cells.factory.pipeline.internal import native_validation_sandbox as sandbox
if sys.argv[1] == 'True':
    sys.modules['tomllib'] = None
    if sys.argv[2]:
        sys.path.insert(0, sys.argv[2])
    importlib.reload(sandbox)
    importlib.reload(group)
assert group.tomllib is sandbox.tomllib
if sys.argv[1] == 'True':
    assert sandbox.tomllib.__name__ == 'tomli'
manifest = Path(sys.argv[3]) / 'Cargo.toml'
manifest.write_text('[package]\\nname="unicode_測試"\\nversion="0.1.0"\\n', encoding='utf-8')
assert sandbox._load_project_toml(manifest, label='fixture')['package']['name'] == 'unicode_測試'
assert group.tomllib.loads('default_toolchain="stable"')['default_toolchain'] == 'stable'
manifest.write_text('[malformed', encoding='utf-8')
try:
    sandbox._load_project_toml(manifest, label='fixture')
except sandbox.NativeValidationContractError:
    pass
else:
    raise AssertionError('malformed TOML accepted')
print('REAL_PARSER_OK')
"""
    result = run_process_tree_safe(
        [sys.executable, "-c", script, str(fallback), parser_root, str(tmp_path)],
        timeout=15,
        env={"PYTHONPATH": os.pathsep.join(sys.path), "PYTHONDONTWRITEBYTECODE": "1"},
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "REAL_PARSER_OK"


def test_rustup_metadata_resolver_cannot_write_host_or_return_foreign_compiler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("bound", encoding="utf-8")
    tools = tmp_path / "tools"
    tools.mkdir()
    marker = tmp_path / "host-write"
    resolver = tools / "rustup"
    resolver.write_text(f'#!/bin/sh\nprintf bad > "{marker}"\nprintf "/foreign/bin/rustc\\n"\n', encoding="utf-8")
    resolver.chmod(0o755)
    rust_home = Path(os.environ.get("RUSTUP_HOME") or Path.home() / ".rustup")
    cargo_home = tmp_path / "cargo"
    cargo_home.mkdir()
    monkeypatch.setenv("RUSTUP_TOOLCHAIN", "stable")
    with pytest.raises(RuntimeError, match="toolchain_unavailable"):
        VerificationGroup(
            workspace=source,
            candidate_id="malicious-resolver",
            input_hashes={"input.txt": hashlib.sha256(b"bound").hexdigest()},
            dependency_roots={},
            toolchain_roots=(tools,),
            staging_parent=None,
            rustup_home=rust_home,
            cargo_home=cargo_home,
        )
    assert not marker.exists()


def test_rustup_resolver_alias_retarget_during_native_query_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.factory.pipeline.internal import native_validation_group as module

    rustup = shutil.which("rustup")
    assert rustup is not None
    tools = tmp_path / "tools"
    tools.mkdir()
    shutil.copyfile(Path(rustup).resolve(), tools / "binary")
    (tools / "binary").chmod(0o755)
    alias = tools / "rustup"
    alias.symlink_to("binary")
    source = tmp_path / "source"
    source.mkdir()
    (source / "input.txt").write_text("bound", encoding="utf-8")
    cache = tmp_path / "cargo"
    cache.mkdir()
    original = module._owned_process_runner

    def retarget(*args, **kwargs):
        result = original(*args, **kwargs)
        alias.unlink()
        alias.symlink_to("./binary")
        return result

    monkeypatch.setattr(module, "_owned_process_runner", retarget)
    monkeypatch.setenv("RUSTUP_TOOLCHAIN", "stable")
    with pytest.raises(RuntimeError, match="resolver_drift"):
        VerificationGroup(
            workspace=source,
            candidate_id="alias-drift",
            input_hashes={"input.txt": hashlib.sha256(b"bound").hexdigest()},
            dependency_roots={},
            toolchain_roots=(tools,),
            staging_parent=None,
            rustup_home=Path(os.environ.get("RUSTUP_HOME") or Path.home() / ".rustup"),
            cargo_home=cache,
        )
