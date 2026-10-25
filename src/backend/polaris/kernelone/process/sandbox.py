"""Linux workspace containment for commands executed by a physical owner.

This builds a mandatory bubblewrap mount/user/PID namespace boundary. Execute
its argv using ``run_process_tree_safe`` and a fresh ``ProcessTreeRunControl``;
async cancellation must still await that physical execution owner. Process
groups alone cannot contain descendants which call setsid().
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

from .command_cache import COMMAND_CACHE_ALIAS, CommandCacheMount


class WorkspaceSandboxUnavailableError(RuntimeError):
    """The platform cannot provide the required OS containment."""


_TOOLCHAIN_ROOTS = ("/usr", "/bin", "/sbin", "/lib", "/lib64")
_PRIVATE_ROOTS = ("/tmp", "/sandbox-home", "/sandbox-cache", "/sandbox-output")
_RESERVED_ROOTS = ("/proc", "/dev", "/etc", COMMAND_CACHE_ALIAS, *_PRIVATE_ROOTS)
_SYSTEM_JVM_ROOT = Path("/usr/lib/jvm")
_SYSTEM_ETC_ROOT = Path("/etc")


def _trusted_system_java_path(path: Path) -> bool:
    """System runtime policy may expose only real administrator-owned paths."""
    try:
        if path.resolve(strict=True) != path:
            return False
        for candidate in (path, *path.parents):
            information = candidate.lstat()
            if (
                information.st_uid != 0
                or information.st_mode & 0o022
                or not (stat.S_ISREG(information.st_mode) or stat.S_ISDIR(information.st_mode))
            ):
                return False
    except (OSError, RuntimeError):
        return False
    return True


def _system_java_config_roots() -> tuple[Path, ...]:
    """Complete trusted Debian/Ubuntu modular JVMs' existing system read surface.

    These exact readonly configuration roots are built-in runtime policy, not
    project grants. Never infer mounts from JAVA_HOME or arbitrary conf links.
    Other children share the administrator trust boundary of the existing /usr
    mount; their symlinks do not authorize additional destination mounts.
    """
    if not _trusted_system_java_path(_SYSTEM_JVM_ROOT):
        return ()
    try:
        entries = sorted(_SYSTEM_JVM_ROOT.iterdir())
    except OSError:
        return ()
    roots: set[Path] = set()
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
            home = entry.resolve(strict=True)
            if not home.is_relative_to(_SYSTEM_JVM_ROOT):
                continue
            release, executable = home / "release", home / "bin/java"
            security = home / "conf/security/java.security"
            if not release.is_file() or not executable.is_file() or not security.is_symlink():
                continue
            if not all(_trusted_system_java_path(path) for path in (release, executable, security.parent)):
                continue
            if security.lstat().st_uid != 0:
                continue
            match = re.search(
                r'^JAVA_VERSION="([1-9][0-9]*)(?:[.\-+_][^"\r\n]*)?"$',
                release.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
            if match is None or int(match.group(1)) < 9:
                continue
            config = _SYSTEM_ETC_ROOT / f"java-{match.group(1)}-openjdk"
            target = config / "security/java.security"
            if security.resolve(strict=True) == target and _trusted_system_java_path(target):
                roots.add(config)
        except (OSError, RuntimeError, UnicodeError):
            # Optional discovery grants nothing for unsupported/broken entries;
            # unrelated commands must retain their existing contained execution.
            continue
    return tuple(sorted(roots))


def _existing_path(value: str | os.PathLike[str], *, label: str) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"sandbox {label} must exist: {value}") from exc
    return path


def _overlaps(path: Path, other: Path) -> bool:
    return path.is_relative_to(other) or other.is_relative_to(path)


def _reject_external_hardlinks(roots: set[Path], *, control: Path) -> None:
    """A shared inode is writable only when all its aliases are in write scope."""
    aliases: dict[tuple[int, int], set[Path]] = {}
    link_counts: dict[tuple[int, int], int] = {}

    def inspect(candidate: Path) -> None:
        try:
            information = candidate.lstat()
        except OSError as exc:
            raise ValueError(f"sandbox cannot inspect writable path: {candidate}") from exc
        if stat.S_ISREG(information.st_mode) and information.st_nlink > 1:
            identity = (information.st_dev, information.st_ino)
            aliases.setdefault(identity, set()).add(candidate)
            link_counts[identity] = max(link_counts.get(identity, 0), information.st_nlink)

    def fail_scan(error: OSError) -> None:
        raise ValueError(f"sandbox cannot inspect write root: {error}") from error

    # Parent grants cover nested grants; enumerate each physical name once.
    outer_roots = {root for root in roots if not any(root != other and root.is_relative_to(other) for other in roots)}
    for root in outer_roots:
        if root.is_file():
            inspect(root)
            continue
        for current, directories, files in os.walk(root, followlinks=False, onerror=fail_scan):
            directories[:] = [name for name in directories if Path(current, name) != control]
            for name in files:
                inspect(Path(current, name))
    if any(len(paths) != link_counts[identity] for identity, paths in aliases.items()):
        raise ValueError("sandbox writable hardlink has an external, protected or unknown alias")


def resolve_toolchain_bin_dirs(
    toolchain_bin_dirs: Sequence[str | os.PathLike[str]],
    *,
    workspace: str | os.PathLike[str],
    read_roots: Sequence[str | os.PathLike[str]],
    write_roots: Sequence[str | os.PathLike[str]],
) -> tuple[Path, ...]:
    """Resolve explicit host search directories without adding filesystem grants.

    Only existing directory read mounts and the existing system mounts qualify.
    This validates the additional search paths, not the complete mount scope;
    the sandbox builder must still apply its normal grant checks. Empty input
    preserves the original command environment and never infers paths.
    """
    if isinstance(toolchain_bin_dirs, (str, bytes, os.PathLike)) or not isinstance(toolchain_bin_dirs, Sequence):
        raise ValueError("sandbox toolchain_bin_dirs must be a sequence of absolute directories")
    if not toolchain_bin_dirs:
        return ()
    project = _existing_path(workspace, label="workspace")
    control = project / ".polaris"
    reads = tuple(_existing_path(root, label="read root") for root in read_roots)
    writes = tuple(_existing_path(root, label="write root") for root in write_roots)
    system_reads = tuple(Path(root).resolve() for root in _TOOLCHAIN_ROOTS if Path(root).exists())
    result: list[Path] = []
    for value in toolchain_bin_dirs:
        try:
            raw = os.fspath(value)
        except TypeError as exc:
            raise ValueError("sandbox toolchain bin directory must be an absolute path") from exc
        if not isinstance(raw, str) or not raw or "\0" in raw or ":" in raw or not Path(raw).is_absolute():
            raise ValueError("sandbox toolchain bin directory must be absolute without NUL or PATH separators")
        directory = _existing_path(raw, label="toolchain bin directory")
        if ":" in str(directory):
            raise ValueError("sandbox canonical toolchain bin directory contains a PATH separator")
        if not directory.is_dir():
            raise ValueError("sandbox toolchain bin directory must be a directory")
        if directory.is_relative_to(control) or any(_overlaps(directory, Path(root)) for root in _RESERVED_ROOTS):
            raise ValueError("sandbox toolchain bin directory overlaps a control or reserved mount")
        if not any(root.is_dir() and directory.is_relative_to(root) for root in (*reads, *system_reads)):
            raise ValueError("sandbox toolchain bin directory is outside existing read mounts")
        if any(_overlaps(directory, root) for root in writes):
            raise ValueError("sandbox toolchain bin directory overlaps a writable mount")
        if directory not in result:
            result.append(directory)
    return tuple(result)


def build_workspace_sandbox_argv(
    argv: Sequence[str],
    *,
    workspace: str | os.PathLike[str],
    cwd: str | os.PathLike[str],
    read_roots: Sequence[str | os.PathLike[str]],
    write_roots: Sequence[str | os.PathLike[str]],
    network_allowed: bool,
    toolchain_bin_dirs: Sequence[str | os.PathLike[str]] = (),
    capture_output_root: Path | None = None,
    command_cache: CommandCacheMount | None = None,
) -> list[str]:
    """Build a fail-closed Linux command with explicit filesystem grants.

    Read grants may include specific external dependency/toolchain directories;
    write grants must stay inside the workspace. The complete workspace
    ``.polaris`` subtree is hidden read-only, including its mountpoint, so a
    source writer cannot replace platform databases, locks or snapshots.
    Home, cache, output and temporary files use distinct private tmpfs mounts.
    No host environment or home directory is inherited. Hardlinks are writable
    only when every alias is within the authorized writable roots.

    Linux user namespaces and bubblewrap are required. A namespace setup error
    remains a nonzero bubblewrap result; callers must report its stderr, never
    retry the original command outside the sandbox.
    """
    if sys.platform != "linux":
        raise WorkspaceSandboxUnavailableError("workspace sandbox requires Linux/WSL2 user and PID namespaces")
    bubblewrap = shutil.which("bwrap")
    if not bubblewrap:
        raise WorkspaceSandboxUnavailableError(
            "workspace sandbox requires bubblewrap (bwrap); install it and enable unprivileged user namespaces"
        )
    command = list(argv)
    if not command or any(not isinstance(part, str) or "\0" in part for part in command):
        raise ValueError("sandbox argv must contain non-NUL strings")
    if type(network_allowed) is not bool:
        raise ValueError("sandbox network_allowed must be a boolean")
    project = _existing_path(workspace, label="workspace")
    directory = _existing_path(cwd, label="cwd")
    if not project.is_dir() or not directory.is_dir() or not directory.is_relative_to(project):
        raise ValueError("sandbox cwd must be a directory inside workspace")
    if any(_overlaps(project, Path(root)) for root in (*_TOOLCHAIN_ROOTS, *_RESERVED_ROOTS)):
        raise ValueError("sandbox workspace overlaps a reserved mount")
    control = project / ".polaris"
    if control.is_symlink() or (control.exists() and not control.is_dir()):
        raise ValueError("sandbox control root must be a real directory")
    if directory.is_relative_to(control):
        raise ValueError("sandbox cwd cannot enter the control root")
    reads = {_existing_path(root, label="read root") for root in read_roots}
    writes = {_existing_path(root, label="write root") for root in write_roots}
    home = Path.home().resolve()
    for root in reads | writes:
        if root.is_relative_to(control):
            raise ValueError("sandbox grants cannot expose the control root")
        if home.is_relative_to(root) or (project.is_relative_to(root) and root != project):
            raise ValueError("sandbox grants cannot expose broad host or home directories")
        if any(_overlaps(root, Path(reserved)) for reserved in _RESERVED_ROOTS):
            raise ValueError("sandbox grants overlap a reserved mount")
    if any(not root.is_relative_to(project) for root in writes):
        raise ValueError("sandbox write roots must stay inside workspace")
    extra_bins = resolve_toolchain_bin_dirs(
        toolchain_bin_dirs, workspace=project, read_roots=tuple(reads), write_roots=tuple(writes)
    )
    _reject_external_hardlinks(writes, control=control)
    if capture_output_root is not None:
        from .output_capture import validate_command_output_root

        validate_command_output_root(project, capture_output_root, empty=True)
    if command_cache is not None:
        if not isinstance(command_cache, CommandCacheMount) or command_cache.workspace != project:
            raise ValueError("sandbox command cache must belong to this workspace")
        command_cache.validate()
    arguments = [
        bubblewrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--hostname",
        "polaris-sandbox",
        "--disable-userns",
        "--cap-drop",
        "ALL",
        "--clearenv",
    ]
    if not network_allowed:
        arguments.append("--unshare-net")
    for system_root in _TOOLCHAIN_ROOTS:
        if Path(system_root).exists():
            arguments.extend(("--ro-bind", system_root, system_root))
    for filename in ("/etc/alternatives", "/etc/ssl/certs", "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf"):
        if Path(filename).exists():
            arguments.extend(("--ro-bind", filename, filename))
    for java_config in _system_java_config_roots():
        arguments.extend(("--ro-bind", str(java_config), str(java_config)))
    arguments.extend(("--proc", "/proc", "--dev", "/dev"))
    for private_root in _PRIVATE_ROOTS:
        if private_root == "/sandbox-output" and capture_output_root is not None:
            arguments.extend(("--bind", str(capture_output_root), private_root))
        else:
            arguments.extend(("--tmpfs", private_root))
    arguments.extend(("--dir", str(project)))
    for root in sorted(reads, key=lambda path: (len(path.parts), str(path))):
        arguments.extend(("--ro-bind", str(root), str(root)))
    for root in sorted(writes, key=lambda path: (len(path.parts), str(path))):
        arguments.extend(("--bind", str(root), str(root)))
    arguments.extend(("--tmpfs", str(control), "--remount-ro", str(control)))
    if command_cache is not None:
        arguments.extend(
            (
                "--bind" if command_cache.mode == "read_write" else "--ro-bind",
                str(command_cache.root),
                COMMAND_CACHE_ALIAS,
            )
        )
    arguments.extend(("--remount-ro", "/"))
    environment = {
        "HOME": "/sandbox-home",
        "XDG_CACHE_HOME": "/sandbox-cache",
        "PYTHONPYCACHEPREFIX": "/sandbox-cache/python",
        "POLARIS_SANDBOX_OUTPUT": "/sandbox-output",
        "TMPDIR": "/tmp",
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    if extra_bins:
        environment["PATH"] = ":".join(
            dict.fromkeys([*environment["PATH"].split(":"), *(str(path) for path in extra_bins)])
        )
    if command_cache is not None:
        environment["POLARIS_COMMAND_CACHE"] = COMMAND_CACHE_ALIAS
    for key, value in environment.items():
        arguments.extend(("--setenv", key, value))
    arguments.extend(("--chdir", str(directory), "--", *command))
    return arguments


__all__ = ["WorkspaceSandboxUnavailableError", "build_workspace_sandbox_argv", "resolve_toolchain_bin_dirs"]
