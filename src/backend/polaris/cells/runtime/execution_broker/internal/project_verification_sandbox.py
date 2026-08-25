"""Fail-closed Linux sandbox for untrusted physical verifier commands."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from polaris.cells.runtime.execution_broker.public.project_verification import (
    ProjectVerificationArtifactInputV1,
)
from polaris.kernelone.storage import resolve_storage_roots

_SANDBOX_GATE_ROOT = "/run/polaris-verifier-gate"
_SANDBOX_GATE_RELEASE = f"{_SANDBOX_GATE_ROOT}/launch.release"
_EPHEMERAL_EXECUTION_ROOTS = tuple(Path(item).resolve() for item in ("/tmp", "/var/tmp", "/dev/shm"))
_ENV_ALLOWLIST = ("CI", "LANG", "LC_ALL", "LC_CTYPE", "NO_COLOR", "TERM", "TZ")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash_tree(path: Path) -> str:
    if path.is_file():
        return _hash_file(path)
    rows: list[str] = []
    for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        if child.is_symlink():
            raise ValueError(f"verification input contains symlink: {child}")
        relative = child.relative_to(path).as_posix()
        rows.append(f"d:{relative}" if child.is_dir() else f"f:{relative}:{_hash_file(child)}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def _protected_platform_roots(workspace: Path) -> tuple[Path, ...]:
    roots = resolve_storage_roots(str(workspace))
    config_root = Path(roots.config_root).resolve()
    runtime_root = Path(roots.runtime_root).resolve()
    polaris_home = config_root.parent
    runtime_authority_root = next(
        (candidate for candidate in (runtime_root, *runtime_root.parents) if candidate.name == ".polaris"),
        runtime_root,
    )
    workspace_authority_root = (workspace / ".polaris").resolve()
    protected: list[Path] = []
    for root in dict.fromkeys((polaris_home, runtime_authority_root)):
        # Project-local authority is the canonical runtime layout.  It is
        # already hidden by the dedicated workspace ``.polaris`` tmpfs below
        # and rejected as verifier cwd/input, so treating it as an external
        # overlapping root would make every valid project unverifiable.
        if root == workspace_authority_root:
            continue
        if root == workspace or root in workspace.parents or workspace in root.parents:
            raise RuntimeError("workspace overlaps protected Polaris authority storage")
        protected.append(root)
    return tuple(protected)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _safe_path_value(workspace: Path) -> str:
    """Return an absolute, non-ephemeral PATH without target-controlled entries."""

    rows: list[str] = []
    for raw in (os.environ.get("PATH") or os.defpath).split(os.pathsep):
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            continue
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_dir() or _is_within(resolved, workspace):
            continue
        if any(_is_within(resolved, root) for root in _EPHEMERAL_EXECUTION_ROOTS):
            continue
        token = str(candidate.resolve())
        if token not in rows:
            rows.append(token)
    for fallback in ("/usr/local/bin", "/usr/bin", "/bin"):
        if fallback not in rows and Path(fallback).is_dir():
            rows.append(fallback)
    if not rows:
        raise RuntimeError("physical verifier has no trusted executable PATH")
    return os.pathsep.join(rows)


def _sanitized_environment(workspace: Path) -> tuple[tuple[str, str], ...]:
    environment: list[tuple[str, str]] = [("PATH", _safe_path_value(workspace))]
    for name in _ENV_ALLOWLIST:
        value = os.environ.get(name)
        if value is not None and "\x00" not in value:
            environment.append((name, value))
    return tuple(environment)


def _resolved_execution_cwd(workspace: Path, cwd: str) -> Path:
    relative = Path(cwd)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("project verifier cwd must be workspace-relative")
    candidate = workspace if cwd == "." else workspace / relative
    current = workspace
    for part in (() if cwd == "." else relative.parts):
        current /= part
        if current.is_symlink():
            raise ValueError("project verifier cwd must not traverse symlinks")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir() or not _is_within(resolved, workspace):
        raise ValueError("project verifier cwd must resolve to a workspace directory")
    if _is_within(resolved, workspace / ".polaris"):
        raise ValueError("project verifier cwd must not enter workspace authority storage")
    return resolved


@dataclass(slots=True)
class PreparedProjectVerificationSandbox:
    workspace: Path
    execution_cwd: Path
    snapshot_root: Path
    snapshots: tuple[tuple[Path, Path, str], ...]
    bwrap_path: str
    protected_roots: tuple[Path, ...]
    gate_release_path: Path
    environment: tuple[tuple[str, str], ...]

    def wrap_command(self, argv: tuple[str, ...], *, executable_path: str | None = None) -> tuple[str, ...]:
        if not argv:
            raise ValueError("verifier argv must not be empty")
        effective_argv = ((executable_path or argv[0]), *argv[1:])
        args: list[str] = [
            self.bwrap_path,
            "--die-with-parent",
            "--new-session",
            "--unshare-user-try",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
            "--clearenv",
            "--ro-bind",
            "/",
            "/",
            "--tmpfs",
            "/run",
            "--dir",
            _SANDBOX_GATE_ROOT,
        ]
        args.extend(("--tmpfs", "/tmp"))
        try:
            temporary_relative = self.workspace.relative_to("/tmp")
        except ValueError:
            pass
        else:
            current = Path("/tmp")
            for part in temporary_relative.parts:
                current /= part
                args.extend(("--dir", str(current)))
        args.extend(("--bind", str(self.workspace), str(self.workspace)))
        # Target code must never observe or mutate workspace-local platform
        # authority/runtime state.  An empty tmpfs keeps ordinary tools that
        # probe for ``.polaris`` working without exposing the real bytes.
        args.extend(("--tmpfs", str(self.workspace / ".polaris")))
        for original, snapshot, _digest in self.snapshots:
            args.extend(("--ro-bind", str(snapshot), str(original)))
        args.extend(("--ro-bind", str(self.snapshot_root), _SANDBOX_GATE_ROOT))
        for protected in self.protected_roots:
            if protected.exists():
                args.extend(("--tmpfs", str(protected)))
        for name, value in self.environment:
            args.extend(("--setenv", name, value))
        args.extend(
            (
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--dir",
                "/tmp/polaris-verifier-home",
                "--setenv",
                "HOME",
                "/tmp/polaris-verifier-home",
                "--setenv",
                "KERNELONE_VERIFIER_SANDBOX",
                "1",
                "--chdir",
                str(self.execution_cwd),
                "--",
                "/bin/sh",
                "-c",
                f'while [ ! -f "{_SANDBOX_GATE_RELEASE}" ]; do sleep 0.01; done; exec "$@"',
                "polaris-verifier-gate",
                *effective_argv,
            )
        )
        return tuple(args)

    def release_after_fence(self) -> None:
        """Release the child only after its durable process identity is committed."""

        descriptor = os.open(self.gate_release_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        try:
            os.write(descriptor, b"released\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def assert_inputs_unchanged(self) -> None:
        for original, _snapshot, expected_hash in self.snapshots:
            if _hash_tree(original) != expected_hash:
                raise ValueError(f"project verification input changed during execution: {original}")

    def cleanup(self) -> None:
        shutil.rmtree(self.snapshot_root, ignore_errors=True)


def prepare_project_verification_sandbox(
    *,
    workspace: str,
    inputs: tuple[ProjectVerificationArtifactInputV1, ...],
    request_hash: str,
    cwd: str = ".",
) -> PreparedProjectVerificationSandbox:
    """Copy authoritative inputs, then mount those copies read-only over workspace paths."""

    bwrap_path = shutil.which("bwrap")
    if os.name != "posix" or not bwrap_path:
        raise RuntimeError("physical verifier requires Linux bubblewrap sandbox")
    root = Path(workspace).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project verification workspace must be a directory")
    execution_cwd = _resolved_execution_cwd(root, cwd)
    protected_roots = _protected_platform_roots(root)
    snapshot_parent = Path(resolve_storage_roots(str(root)).config_root) / "execution_broker" / "verifier_snapshots"
    snapshot_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    snapshot_root = Path(tempfile.mkdtemp(prefix=f"{request_hash[:16]}-", dir=snapshot_parent))
    snapshot_root.chmod(0o700)
    snapshots: list[tuple[Path, Path, str]] = []
    try:
        for index, item in enumerate(inputs):
            relative = Path(item.path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"verification input path escapes workspace: {item.path}")
            original = (root / relative).resolve(strict=True)
            original.relative_to(root)
            if _is_within(original, root / ".polaris"):
                raise ValueError("verification input must not include workspace authority storage")
            if original.is_symlink():
                raise ValueError(f"verification input must not be a symlink: {item.path}")
            snapshot = snapshot_root / f"{index:04d}"
            before_digest = _hash_tree(original)
            if original.is_dir():
                shutil.copytree(original, snapshot, symlinks=False)
            elif original.is_file():
                shutil.copy2(original, snapshot)
            else:
                raise ValueError(f"verification input must be a file or directory: {item.path}")
            snapshot_digest = _hash_tree(snapshot)
            after_digest = _hash_tree(original)
            if before_digest != snapshot_digest or snapshot_digest != after_digest:
                raise ValueError(f"immutable snapshot diverged while copying verifier input: {item.path}")
            snapshots.append((original, snapshot, snapshot_digest))
        return PreparedProjectVerificationSandbox(
            workspace=root,
            execution_cwd=execution_cwd,
            snapshot_root=snapshot_root,
            snapshots=tuple(snapshots),
            bwrap_path=bwrap_path,
            protected_roots=protected_roots,
            gate_release_path=snapshot_root / "launch.release",
            environment=_sanitized_environment(root),
        )
    except Exception:
        shutil.rmtree(snapshot_root, ignore_errors=True)
        raise


__all__ = ["PreparedProjectVerificationSandbox", "prepare_project_verification_sandbox"]
