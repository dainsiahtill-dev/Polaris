"""Exercise the native entry and writer exclusion using real, provider-free child processes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from scripts.factory_bench import source_freeze

ORIGINAL = Path(__file__).resolve().parent
HOLD = """
import os, sys
from pathlib import Path
Path(os.environ["READY_FILE"]).touch()
sys.stdin.read()
"""


def checkout(root: Path) -> Path:
    folder = root / "scripts" / "factory_bench"
    (folder / "_bench_lib").mkdir(parents=True)
    (folder / "_bench_lib/__init__.py").write_text("", encoding="utf-8")
    (root / "polaris").mkdir()
    (root / "polaris/__init__.py").write_text("", encoding="utf-8")
    for name in ("run_factory_bench.py", "backend_fingerprint.py", "source_freeze.py"):
        shutil.copyfile(ORIGINAL / name, folder / name)
    # The normal entry and source lock stay intact. Only the execution adapter is local.
    (folder / "_bench_lib/impl.py").write_text(
        "def main(argv=None):\n"
        + "".join("    " + s + "\n" for s in HOLD.splitlines())
        + "    return 0\n"
        + "def __getattr__(name): return None\n",
        encoding="utf-8",
    )
    shutil.copyfile(folder / "_bench_lib/impl.py", folder / "_bench_lib/cli.py")
    return root


def guard(root: Path, mode: str, *command: str) -> list[str]:
    return [sys.executable, str(ORIGINAL / "source_freeze.py"), "--backend-root", str(root), mode, *command]


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, encoding="utf-8", capture_output=True, timeout=10, check=False)


@contextmanager
def held(
    args: list[str], ready: Path, *, cwd: Path | None = None, module_root: Path | None = None
) -> Iterator[subprocess.Popen[str]]:
    env = {**os.environ, "READY_FILE": str(ready), "PYTHONDONTWRITEBYTECODE": "1"}
    if module_root:
        env["PYTHONPATH"] = str(module_root)
    proc = subprocess.Popen(
        args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=env, cwd=cwd
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.is_file() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.is_file(), proc.communicate(timeout=5)
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=5)


def test_live_native_run_blocks_editors_on_physical_root_but_not_other_roots(tmp_path: Path) -> None:
    root = checkout(tmp_path / "source")
    other = checkout(tmp_path / "other")
    alias = tmp_path / "source-alias"
    alias.symlink_to(root, target_is_directory=True)
    script = root / "scripts/factory_bench/run_factory_bench.py"
    with held([sys.executable, str(script), "--case", "case"], tmp_path / "ready") as proc:
        with source_freeze.source_lock(root, exclusive=False):
            assert run(*guard(alias, "status")).returncode == 75
            assert run(*guard(alias, "edit", "--", sys.executable, "-c", "raise AssertionError()")).returncode == 75
            assert run(*guard(other, "edit", "--", sys.executable, "-c", "pass")).returncode == 0
        proc.kill()
        proc.wait(timeout=5)
    assert json.loads(run(*guard(root, "status")).stdout)["state"] == "editable"


def test_native_entry_refuses_before_import_during_edit_and_dry_run_can_proceed(tmp_path: Path) -> None:
    root = checkout(tmp_path / "source")
    script = root / "scripts/factory_bench/run_factory_bench.py"
    with held(guard(root, "edit", "--", sys.executable, "-c", HOLD), tmp_path / "editor-ready"):
        blocked = run(sys.executable, str(script), "--case", "any")
        assert blocked.returncode == 75 and "source" in blocked.stderr
        with held([sys.executable, str(script), "--dry-run"], tmp_path / "dry-ready"):
            assert run(*guard(root, "edit", "--", sys.executable, "-c", "pass")).returncode == 75
    with held([sys.executable, str(script), "--dry-run"], tmp_path / "other-dry"):
        assert run(*guard(root, "edit", "--", sys.executable, "-c", "pass")).returncode == 0


@pytest.mark.parametrize("launcher", ["relative", "module", "wrapper"])
def test_pre_upgrade_runner_detected_by_exact_source_root(tmp_path: Path, launcher: str) -> None:
    root = checkout(tmp_path / "legacy")
    other = checkout(tmp_path / "other")
    script = root / "scripts/factory_bench/run_factory_bench.py"
    script.write_text(HOLD, encoding="utf-8")
    cwd = tmp_path
    if launcher == "module":
        args = [sys.executable, "-m", "scripts.factory_bench.run_factory_bench"]
    elif launcher == "wrapper":
        if os.name != "posix":
            pytest.skip("shell wrapper is POSIX")
        wrapper = tmp_path / "run_one.sh"
        # Keep only the legacy shell alive, before its native child begins.
        wrapper.write_text(f'cd "{root}"\n: > "$READY_FILE"\nread -r input\n', encoding="utf-8")
        args = ["/bin/sh", str(wrapper)]
    else:
        cwd = root
        args = [sys.executable, "-u", "scripts/factory_bench/run_factory_bench.py"]
    with held(args, tmp_path / "legacy-ready", cwd=cwd, module_root=root):
        state = run(*guard(root, "status"))
        assert state.returncode == 75 and json.loads(state.stdout)["state"] == "legacy_runner"
        assert run(*guard(other, "edit", "--", sys.executable, "-c", "pass")).returncode == 0
    assert run(*guard(root, "edit", "--", sys.executable, "-c", "pass")).returncode == 0


def test_editor_return_status_and_read_only_mode_classification(tmp_path: Path) -> None:
    root = checkout(tmp_path / "source")
    assert run(*guard(root, "edit", "--", sys.executable, "-c", "raise SystemExit(23)")).returncode == 23
    for args in (
        ["--help"],
        ["repeatability", "x"],
        ["compare"],
        ["artifact-fingerprint", "a", "b"],
        ["arm-identity"],
        ["evidence-fingerprint", "x"],
    ):
        assert source_freeze.read_only_invocation(args)
    assert not source_freeze.read_only_invocation(["--case", "L1-01"])


def test_portable_sqlite_reader_writer_and_crash_release(tmp_path: Path) -> None:
    root = checkout(tmp_path / "portable")
    module = "from pathlib import Path; from scripts.factory_bench.source_freeze import _sqlite_lock, SourceBusyError"
    importer = Path(__file__).resolve().parents[2]
    setup = f"import sys; sys.path.insert(0,{str(importer)!r}); " + module
    reader = (
        setup
        + "\nwith _sqlite_lock(Path(sys.argv[1]),False):\n"
        + "".join("    " + s + "\n" for s in HOLD.splitlines())
    )
    probe = (
        setup
        + "\ntry:\n    with _sqlite_lock(Path(sys.argv[1]),sys.argv[2]=='write'): pass\nexcept SourceBusyError: raise SystemExit(75)"
    )
    with held([sys.executable, "-c", reader, str(root)], tmp_path / "sqlite-ready") as proc:
        assert run(sys.executable, "-c", probe, str(root), "read").returncode == 0
        assert run(sys.executable, "-c", probe, str(root), "write").returncode == 75
        proc.kill()
        proc.wait(timeout=5)
    assert run(sys.executable, "-c", probe, str(root), "write").returncode == 0


def test_imported_normal_main_checks_the_same_source_guard(tmp_path: Path) -> None:
    root = checkout(tmp_path / "source")
    adapter = "import sys; sys.path.insert(0,sys.argv[1]); from scripts.factory_bench.run_factory_bench import main; raise SystemExit(main(['--case','local']))"
    with held(guard(root, "edit", "--", sys.executable, "-c", HOLD), tmp_path / "import-editor"):
        result = run(sys.executable, "-c", adapter, str(root))
    assert result.returncode == 75, result.stderr
