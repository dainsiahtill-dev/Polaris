"""Development-only reader/writer exclusion for the physical backend used by a catalog run."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import shlex
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

import psutil

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.factory_bench.backend_fingerprint import resolve_backend_source_root

_RUNNER = Path("scripts/factory_bench/run_factory_bench.py")
_READ_COMMANDS = {"repeatability", "compare", "artifact-fingerprint", "arm-identity", "evidence-fingerprint"}
_WRAPPERS = {"run_one.sh", "run_march.sh"}
_BUSY = 75


class SourceBusyError(RuntimeError):
    """The physical source is held by a conflicting operation."""


def read_only_invocation(argv: list[str]) -> bool:
    return bool(set(argv) & {"--dry-run", "--help", "-h"}) or bool(argv and argv[0] in _READ_COMMANDS)


@contextmanager
def _sqlite_lock(root: Path, exclusive: bool) -> Iterator[None]:
    # Native Windows has no POSIX directory flock. A rollback-mode SQLite pager
    # provides process-owned shared reads and exclusive writes without a TTL.
    checkout = root.parent.parent if root.name == "backend" and root.parent.name == "src" else root.parent
    directory = checkout / ".polaris" / "internal-bench-source-locks"
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(os.fsencode(os.path.normcase(str(root)))).hexdigest()
    with closing(sqlite3.connect(directory / f"{key}.sqlite3", timeout=0, isolation_level=None)) as db:
        try:
            db.execute("BEGIN EXCLUSIVE" if exclusive else "BEGIN")
            db.execute("SELECT count(*) FROM sqlite_schema").fetchone()
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise OSError("source freeze requires a rollback-mode local SQLite file")
        except sqlite3.OperationalError as error:
            if getattr(error, "sqlite_errorcode", 0) & 255 in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise SourceBusyError(f"source is locked: {root}") from None
            raise
        yield


@contextmanager
def source_lock(root: Path, *, exclusive: bool) -> Iterator[int | None]:
    root = resolve_backend_source_root(root)
    if os.name == "nt":
        with _sqlite_lock(root, exclusive):
            yield None
        return
    if os.name != "posix":
        raise OSError("source freeze has no tested backend on this operating system")
    import fcntl

    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EAGAIN, errno.EACCES}:
                raise SourceBusyError(f"source is locked: {root}") from None
            raise
        if not os.path.samestat(os.fstat(fd), root.stat()):
            raise OSError("source directory identity changed while acquiring its lock")
        yield fd
    finally:
        # Closing the last copy releases flock, including after SIGKILL or exec.
        os.close(fd)


def _entry(argv: list[str]) -> tuple[str, list[str]]:
    """Extract the interpreter's entry operand, ignoring text inside -c and tool arguments."""
    args = iter(argv)
    for item in args:
        if item in {"-c", "/c", "-"} or (item.startswith("-") and not item.startswith("--") and "c" in item[1:]):
            break
        if item in {"-X", "-W"}:
            next(args, None)
        elif item == "-m":
            return "-m " + next(args, ""), list(args)
        elif item == "--":
            return next(args, ""), list(args)
        elif not item.startswith("-"):
            return item, list(args)
    return "", []


def _legacy_source(proc: psutil.Process, entry: str, root: Path) -> bool:
    if entry.startswith("-m "):
        target = Path(*entry[3:].split(".")).with_suffix(".py")
        if target.name != _RUNNER.name:
            return False
        cwd = Path(proc.cwd())
        search_path: str = proc.environ().get("PYTHONPATH", "")
        for directory in [str(cwd), *search_path.split(os.pathsep)]:
            candidate = (cwd / directory / target).resolve()
            if candidate.is_file():
                return candidate == root / _RUNNER
        # Module search may also use site paths unavailable to another interpreter.
        return True
    raw = Path(entry)
    if raw.name not in {_RUNNER.name, *_WRAPPERS}:
        return False
    cwd = Path(proc.cwd())
    script = (cwd / raw).resolve()
    if raw.name == _RUNNER.name:
        # A relative pre-upgrade entry may have changed cwd after startup.
        return script == root / _RUNNER or (not raw.is_absolute() and not script.is_file())
    if cwd.is_relative_to(root):
        return True
    # A pre-upgrade wrapper may still be outside the backend before its 'cd'.
    # Resolve its literal declared backend without interpreting the shell script.
    if script.is_file() and script.stat().st_size <= 64 * 1024:
        for line in script.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("cd "):
                fields = shlex.split(line, comments=True)
                if len(fields) == 2 and Path(fields[1]).is_absolute():
                    return Path(fields[1]).resolve() == root
    return True


def legacy_runners(root: Path) -> list[int]:
    matches = []
    for proc in psutil.process_iter(["cmdline"]):
        argv = proc.info["cmdline"] or []
        if proc.pid == os.getpid() or len(argv) < 2:
            continue
        interpreter = Path(argv[0]).stem.lower()
        if not (
            interpreter.startswith(("python", "pypy")) or interpreter in {"sh", "bash", "dash", "zsh", "ksh", "py"}
        ):
            continue
        entry, options = _entry(argv[1:])
        if not entry or read_only_invocation(options):
            continue
        try:
            if _legacy_source(proc, entry, root):
                matches.append(proc.pid)
        except (psutil.NoSuchProcess, FileNotFoundError):
            continue
        except (psutil.AccessDenied, OSError, ValueError):
            # Exact runner signature with an uninspectable source cannot certify an edit.
            matches.append(proc.pid)
    return sorted(matches)


def native_main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    with ExitStack() as handles:
        if not read_only_invocation(argv):
            try:
                handles.enter_context(source_lock(resolve_backend_source_root(), exclusive=False))
            except SourceBusyError as error:
                print(str(error), file=sys.stderr)
                return _BUSY
            except (OSError, sqlite3.Error, ValueError) as error:
                print(f"could not freeze bench source: {error}", file=sys.stderr)
                return 2
        from scripts.factory_bench._bench_lib.cli import main

        return main(argv)


def _status(root: Path) -> tuple[str, list[int]]:
    try:
        with source_lock(root, exclusive=True):
            pids = legacy_runners(root)
            return ("legacy_runner" if pids else "editable"), pids
    except SourceBusyError:
        try:
            with source_lock(root, exclusive=False):
                return "bench_frozen", legacy_runners(root)
        except SourceBusyError:
            return "editor_active", []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-root", type=Path)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("status", help="inspect the current source freeze")
    modes.add_parser("edit", help="hold the source while a foreground command edits it").add_argument(
        "command", nargs=argparse.REMAINDER
    )
    args = parser.parse_args(argv)
    try:
        root = resolve_backend_source_root(args.backend_root)
        if args.mode == "status":
            state, pids = _status(root)
            print(json.dumps({"state": state, "backend_root": str(root), "runner_pids": pids}, ensure_ascii=False))
            return 0 if state == "editable" else _BUSY
        command = list(args.command)
        if command[:1] == ["--"]:
            command = command[1:]
        if not command:
            parser.error("edit requires a foreground command")
        with source_lock(root, exclusive=True) as fd:
            pids = legacy_runners(root)
            if pids:
                raise SourceBusyError(f"source has pre-existing runner PIDs {pids}: {root}")
            if fd is not None:
                os.set_inheritable(fd, True)
                os.execvp(command[0], command)
            return subprocess.run(command, check=False).returncode
    except SourceBusyError as error:
        print(str(error), file=sys.stderr)
        return _BUSY
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"could not guard bench source: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
