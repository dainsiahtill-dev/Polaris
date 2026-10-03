"""Disposable, source-bound verifier groups; no execution authority or receipts.

Only explicitly declared files are staged. The caller owns command admission;
execute_command alone delegates physical execution/drain to the public process
owner through the fixed containment command. Legacy caller observations never
authorize disposal. This module grants no project authority or receipt seal.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.kernelone.fs import KernelFileSystem, get_default_adapter, read_guarded_regular_file_snapshot
from polaris.kernelone.process import (
    ProcessTreeCancelledError,
    ProcessTreeRunControl,
    run_process_tree_safe as _owned_process_runner,
)

from .native_validation_sandbox import NativeValidationSandboxError, _append_optional_ro_bind, tomllib

_MAX_INPUT_BYTES = 16 * 1024 * 1024
_MAX_TOOL_BYTES = 256 * 1024 * 1024
_SYSTEM_ROOTS = ("/usr", "/bin", "/lib", "/lib64", "/sbin", "/etc/alternatives")
_BLOCKED_PARTS = frozenset({".polaris", ".git"})
_TRUSTED_ISOLATION_PATH = Path("/usr/bin/bwrap")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_payload(value: Any) -> str:
    return _hash(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _relative(raw: str, *, allow_dot: bool = False) -> str:
    if type(raw) is not str or not raw or "\\" in raw or "\x00" in raw or ":" in raw:
        raise NativeValidationSandboxError("verification_input_path_unsafe")
    if allow_dot and raw == ".":
        return raw
    path = Path(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in raw.split("/")):
        raise NativeValidationSandboxError("verification_input_path_unsafe")
    if any(part in _BLOCKED_PARTS for part in path.parts):
        raise NativeValidationSandboxError("verification_platform_root_forbidden")
    return path.as_posix()


def _root(raw: Path) -> Path:
    path = Path(raw).absolute()
    if (
        path == Path("/")
        or any(part in _BLOCKED_PARTS for part in path.parts)
        or any(item.is_symlink() for item in (path, *path.parents))
    ):
        raise NativeValidationSandboxError("verification_root_unsafe")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise NativeValidationSandboxError("verification_workspace_not_directory")
    return resolved


def _read(fs: KernelFileSystem, path: str, *, max_bytes: int = _MAX_INPUT_BYTES) -> tuple[bytes, tuple[int, ...]]:
    snapshot = read_guarded_regular_file_snapshot(fs.workspace, path, max_bytes)
    data = fs.workspace_read_bytes(path)
    if data != snapshot.content:
        raise NativeValidationSandboxError(f"verification_input_drift:{path}")
    witness = (
        snapshot.root_device,
        snapshot.root_inode,
        snapshot.device,
        snapshot.inode,
        snapshot.mtime_ns,
        snapshot.ctime_ns,
        snapshot.size,
    )
    return data, witness


def _mount_hash(root: Path, cache: dict[str, tuple[str, tuple[int, ...]]] | None = None) -> str:
    """Seal actual root/leaf identity and bytes, not just a readonly mount label."""
    fs = KernelFileSystem(str(root), get_default_adapter())
    root_stat = root.stat()
    rows: dict[str, Any] = {"root_identity": (root_stat.st_dev, root_stat.st_ino)}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if any(part in _BLOCKED_PARTS for part in Path(relative).parts):
            raise NativeValidationSandboxError("verification_mount_platform_root_forbidden")
        if path.is_symlink():
            target = path.resolve(strict=True)
            if not target.is_relative_to(root):
                raise NativeValidationSandboxError("verification_dependency_symlink_escape")
            if any(part in _BLOCKED_PARTS for part in target.relative_to(root).parts):
                raise NativeValidationSandboxError("verification_mount_platform_root_forbidden")
            link = path.lstat()
            rows[relative] = (
                "link",
                target.relative_to(root).as_posix(),
                link.st_dev,
                link.st_ino,
                link.st_mtime_ns,
                link.st_ctime_ns,
            )
        elif path.is_file():
            leaf = path.stat(follow_symlinks=False)
            observed = (
                root_stat.st_dev,
                root_stat.st_ino,
                leaf.st_dev,
                leaf.st_ino,
                leaf.st_mtime_ns,
                leaf.st_ctime_ns,
                leaf.st_size,
            )
            sealed = cache.get(relative) if cache is not None else None
            if sealed is not None and sealed[1] == observed:
                digest, witness = sealed
            else:
                data, witness = _read(fs, relative, max_bytes=_MAX_TOOL_BYTES)
                digest = _hash(data)
                if cache is not None:
                    cache[relative] = (digest, witness)
            rows[relative] = (digest, witness)
        elif not path.is_dir():
            raise NativeValidationSandboxError("verification_dependency_not_regular")
        else:
            directory = path.stat(follow_symlinks=False)
            rows[relative] = (
                "directory",
                directory.st_dev,
                directory.st_ino,
                directory.st_mtime_ns,
                directory.st_ctime_ns,
            )
    return _hash_payload(rows)


def _isolation_binary_binding(path: Path) -> tuple[str, tuple[int, ...], tuple[int, int, int]]:
    """Canonical system installation, not executable flags or a PATH wrapper."""
    canonical = path.resolve(strict=True)
    metadata = canonical.stat(follow_symlinks=False)
    if (
        canonical != _TRUSTED_ISOLATION_PATH
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
    ):
        raise NativeValidationSandboxError("verification_isolation_binary_untrusted")
    data, witness = _read(
        KernelFileSystem(str(canonical.parent), get_default_adapter()),
        canonical.name,
        max_bytes=_MAX_TOOL_BYTES,
    )
    return _hash(data), witness, (metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode))


def _invocation_binding(path: Path) -> str:
    """Seal lexical invocation and every traversed alias, separately from bytes."""
    rows: list[Any] = []
    for item in (*reversed(path.parents), path):
        metadata = item.lstat()
        identity = (metadata.st_dev, metadata.st_ino, metadata.st_mode)
        if item.is_symlink():
            rows.append((str(item), identity, os.readlink(item), metadata.st_mtime_ns, metadata.st_ctime_ns))
        elif item == path:
            rows.append((str(item), identity, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns))
        else:
            rows.append((str(item), identity))
    return _hash_payload((str(path.resolve(strict=True)), rows))


@dataclass(frozen=True)
class PreparedVerificationCommand:
    command_id: int
    logical_argv: tuple[str, ...]
    argv: tuple[str, ...]
    cwd: str
    input_hash: str
    executable_hash: str
    environment_policy: tuple[tuple[str, str], ...]


class VerificationGroup:
    def __init__(
        self,
        *,
        workspace: Path,
        candidate_id: str,
        input_hashes: Mapping[str, str],
        dependency_roots: Mapping[str, Path],
        toolchain_roots: Sequence[Path],
        staging_parent: Path | None,
        rustup_home: Path | None = None,
        cargo_home: Path | None = None,
        toolchain_files: Sequence[Path] = (),
    ) -> None:
        self.workspace = _root(workspace)
        system_roots = tuple(Path(path).resolve() for path in _SYSTEM_ROOTS if Path(path).is_dir())
        if any(self.workspace.is_relative_to(root) or root.is_relative_to(self.workspace) for root in system_roots):
            raise NativeValidationSandboxError("verification_workspace_overlaps_system_mount")
        self.candidate_id = str(candidate_id or "").strip()
        if not self.candidate_id or not input_hashes:
            raise NativeValidationSandboxError("verification_group_identity_and_inputs_required")
        discovered_bwrap = shutil.which("bwrap")
        if os.name != "posix" or not discovered_bwrap:
            raise NativeValidationSandboxError("verification_isolation_unavailable")
        isolation_path = Path(discovered_bwrap).resolve(strict=True)
        self._isolation_hash, self._isolation_witness, self._isolation_permissions = _isolation_binary_binding(
            isolation_path
        )
        self._bwrap = str(isolation_path)
        self._source_fs = KernelFileSystem(str(self.workspace), get_default_adapter())
        self.input_hashes: dict[str, str] = {}
        self._witnesses: dict[str, tuple[int, ...]] = {}
        payloads: dict[str, bytes] = {}
        for raw, expected in input_hashes.items():
            path = _relative(raw)
            if type(expected) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise NativeValidationSandboxError("verification_expected_hash_invalid")
            data, witness = _read(self._source_fs, path)
            if _hash(data) != expected:
                raise NativeValidationSandboxError(f"verification_input_hash_mismatch:{path}")
            self.input_hashes[path] = expected
            self._witnesses[path] = witness
            payloads[path] = data
        self.input_hash = _hash_payload(self.input_hashes)
        self._mount_caches: dict[Path, dict[str, tuple[str, tuple[int, ...]]]] = {}
        self._dependencies: dict[str, tuple[Path, str]] = {}
        for raw, mount in dependency_roots.items():
            destination = _relative(raw)
            if any(path == destination or path.startswith(destination + "/") for path in self.input_hashes):
                raise NativeValidationSandboxError("verification_dependency_shadows_input")
            root = _root(mount)
            if root == self.workspace or root in self.workspace.parents:
                raise NativeValidationSandboxError("verification_dependency_overlaps_source_root")
            self._mount_caches[root] = {}
            self._dependencies[destination] = (root, _mount_hash(root, self._mount_caches[root]))
        self._tools = tuple(_root(path) for path in toolchain_roots)
        self._rust_mounts: dict[str, Path] = {}
        self._rust_policy: tuple[tuple[str, str], ...] = ()
        self._configuration_witnesses: dict[Path, tuple[str, tuple[int, ...]]] = {}
        if (rustup_home is None) != (cargo_home is None):
            raise NativeValidationSandboxError("verification_rust_homes_incomplete")
        if rustup_home is not None and cargo_home is not None:
            rust_root, cargo_root = _root(rustup_home), _root(cargo_home)
            for home in (rust_root, cargo_root):
                if home == self.workspace or home.is_relative_to(self.workspace) or home in self.workspace.parents:
                    raise NativeValidationSandboxError("verification_toolchain_overlaps_source")
            selected = os.environ.get("RUSTUP_TOOLCHAIN")
            if not selected:
                settings = rust_root / "settings.toml"
                data, witness = _read(KernelFileSystem(str(rust_root), get_default_adapter()), settings.name)
                self._configuration_witnesses[settings] = (_hash(data), witness)
                selected = tomllib.loads(data.decode("utf-8")).get("default_toolchain")
            if not isinstance(selected, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", selected):
                raise NativeValidationSandboxError("verification_rust_toolchain_selection_invalid")
            selected_path = rust_root / "toolchains" / selected
            if not selected_path.exists():
                # Rustup selectors are not necessarily physical directory names.
                # Ask the admitted local resolver without installation or default
                # substitution, then pin its checked installed identity.
                resolvers = [root / "rustup" for root in self._tools if (root / "rustup").is_file()]
                if len(resolvers) != 1:
                    raise NativeValidationSandboxError("verification_rust_toolchain_resolver_unavailable")
                resolver = resolvers[0]
                canonical_resolver = resolver.resolve(strict=True)
                if resolver.is_relative_to(self.workspace) or canonical_resolver.is_relative_to(self.workspace):
                    raise NativeValidationSandboxError("verification_toolchain_overlaps_source")
                if not canonical_resolver.is_relative_to(resolver.parent):
                    raise NativeValidationSandboxError("verification_rust_toolchain_resolver_alias_unsafe")
                binding = _invocation_binding(resolver)
                resolver_fs = KernelFileSystem(str(_root(canonical_resolver.parent)), get_default_adapter())
                resolver_name = canonical_resolver.name
                before = _read(resolver_fs, resolver_name, max_bytes=_MAX_TOOL_BYTES)
                query = [
                    self._bwrap,
                    "--die-with-parent",
                    "--new-session",
                    "--cap-drop",
                    "ALL",
                    "--unshare-all",
                    "--clearenv",
                ]
                for system in _SYSTEM_ROOTS:
                    _append_optional_ro_bind(query, Path(system), system)
                query.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/rustup/toolchains"])
                query.extend(["--ro-bind", str(canonical_resolver), "/resolver/rustup"])
                settings = rust_root / "settings.toml"
                settings_data, settings_witness = _read(
                    KernelFileSystem(str(rust_root), get_default_adapter()), settings.name
                )
                self._configuration_witnesses[settings] = (_hash(settings_data), settings_witness)
                query.extend(["--ro-bind", str(settings), "/rustup/settings.toml"])
                installed: dict[str, Path] = {}
                for candidate in sorted((rust_root / "toolchains").iterdir()):
                    if not candidate.name.startswith(selected + "-"):
                        continue
                    compiler_file = _root(candidate / "bin") / "rustc"
                    data, witness = _read(
                        KernelFileSystem(str(compiler_file.parent), get_default_adapter()),
                        "rustc",
                        max_bytes=_MAX_TOOL_BYTES,
                    )
                    self._configuration_witnesses[compiler_file] = (_hash(data), witness)
                    destination = "/rustup/toolchains/" + candidate.name + "/bin/rustc"
                    installed[destination] = compiler_file
                    query.extend(["--ro-bind", str(compiler_file), destination])
                if not installed:
                    raise NativeValidationSandboxError("verification_rust_toolchain_unavailable")
                for key, value in (
                    ("RUSTUP_HOME", "/rustup"),
                    ("CARGO_HOME", "/tmp/cargo"),
                    ("RUSTUP_AUTO_INSTALL", "0"),
                    ("PATH", "/usr/bin:/bin"),
                ):
                    query.extend(["--setenv", key, value])
                # Only namespace-local temporary files. Limit resolver output
                # before forwarding it to the parent's process-owner pipes.
                query.extend(
                    [
                        "--chdir",
                        "/tmp",
                        "--",
                        "/bin/sh",
                        "-c",
                        'ulimit -f 8; "$@" > /tmp/out 2>/tmp/err; status=$?; head -c 4096 /tmp/out; head -c 4096 /tmp/err >&2; exit "$status"',
                        "resolver-query",
                        "/resolver/rustup",
                        "which",
                        "--toolchain",
                        selected,
                        "rustc",
                    ]
                )
                for configuration, (digest, identity) in self._configuration_witnesses.items():
                    data, witness = _read(
                        KernelFileSystem(str(configuration.parent), get_default_adapter()),
                        configuration.name,
                        max_bytes=_MAX_TOOL_BYTES,
                    )
                    if (_hash(data), witness) != (digest, identity):
                        raise NativeValidationSandboxError("verification_toolchain_configuration_drift")
                if _isolation_binary_binding(Path(self._bwrap)) != (
                    self._isolation_hash,
                    self._isolation_witness,
                    self._isolation_permissions,
                ):
                    raise NativeValidationSandboxError("verification_isolation_binary_drift")
                resolved = _owned_process_runner(
                    query,
                    cwd="/",
                    timeout=10,
                    env={},
                    encoding="utf-8",
                    errors="replace",
                )
                if before != _read(
                    resolver_fs, resolver_name, max_bytes=_MAX_TOOL_BYTES
                ) or binding != _invocation_binding(resolver):
                    raise NativeValidationSandboxError("verification_rust_toolchain_resolver_drift")
                compiler = installed.get(resolved.stdout.strip())
                if resolved.returncode != 0 or compiler is None:
                    raise NativeValidationSandboxError("verification_rust_toolchain_unavailable")
                selected_path = compiler.parent.parent
                selected = selected_path.name
            selected_root = _root(selected_path)
            # Mount installed executable support, not every unrelated toolchain
            # or Rust's large documentation/source tree.
            for component in ("bin", "lib", "libexec", "etc"):
                if (selected_root / component).exists():
                    self._rust_mounts["/rustup/toolchains/" + selected + "/" + component] = _root(
                        selected_root / component
                    )
            for cache in ("registry", "git"):
                if (cargo_root / cache).exists():
                    self._rust_mounts["/cargo-home/" + cache] = _root(cargo_root / cache)
            self._rust_policy = (
                ("RUSTUP_HOME", "/rustup"),
                ("RUSTUP_TOOLCHAIN", selected),
                ("RUSTUP_AUTO_INSTALL", "0"),
                ("CARGO_HOME", "/cargo-home"),
                ("CARGO_NET_OFFLINE", "true"),
                ("CARGO_TARGET_DIR", "/workspace/target"),
            )
        mounted_tools = (*self._tools, *self._rust_mounts.values())
        for root in mounted_tools:
            if root == self.workspace or root.is_relative_to(self.workspace) or root in self.workspace.parents:
                raise NativeValidationSandboxError("verification_toolchain_overlaps_source")
            reserved = (*system_roots, Path("/workspace"), Path("/sandbox-home"), Path("/proc"), Path("/dev"))
            if any(root.is_relative_to(target) or target.is_relative_to(root) for target in reserved):
                raise NativeValidationSandboxError("verification_toolchain_shadows_mount")
            for link in root.rglob("*"):
                if link.is_symlink() and not link.resolve(strict=True).is_relative_to(root):
                    raise NativeValidationSandboxError("verification_toolchain_symlink_escape")
        for root in mounted_tools:
            self._mount_caches.setdefault(root, {})
        self._tool_hashes = {root: _mount_hash(root, self._mount_caches[root]) for root in mounted_tools}
        parent = _root(staging_parent) if staging_parent is not None else None
        if parent is not None and (parent == self.workspace or parent.is_relative_to(self.workspace)):
            raise NativeValidationSandboxError("verification_staging_overlaps_source")
        python_prefixes: set[tuple[Path, str]] = set()
        if toolchain_files:
            for root in self._tools:
                if root.name != "bin":
                    continue
                for interpreter in root.glob("python[0-9]*"):
                    match = re.fullmatch(r"python([0-9]+\.[0-9]+)", interpreter.name)
                    if match and interpreter.is_file() and interpreter.resolve(strict=True).is_relative_to(root):
                        python_prefixes.add((root.parent, match[1]))
        self._tool_file_witnesses: dict[Path, tuple[str, tuple[int, ...]]] = {}
        for runtime_raw in toolchain_files:
            runtime_file = Path(runtime_raw).absolute()
            file_parent = _root(runtime_file.parent)
            runtime_file = file_parent / runtime_file.name
            # A readonly runtime alias is still a source read. Prefix shape
            # must never admit workspace/staging bytes or overwrite mounts.
            if runtime_file.is_relative_to(self.workspace):
                raise NativeValidationSandboxError("verification_toolchain_overlaps_source")
            if parent is not None and runtime_file.is_relative_to(parent):
                raise NativeValidationSandboxError("verification_toolchain_overlaps_staging")
            reserved_files = (
                *system_roots,
                Path("/workspace"),
                Path("/sandbox-home"),
                Path("/proc"),
                Path("/dev"),
                Path("/rustup"),
                Path("/cargo-home"),
            )
            if any(
                runtime_file.is_relative_to(target) or target.is_relative_to(runtime_file) for target in reserved_files
            ):
                raise NativeValidationSandboxError("verification_toolchain_shadows_mount")
            allowed = any(
                (file_parent == prefix / "lib" / ("python" + version) and runtime_file.suffix == ".py")
                or (
                    file_parent == prefix / "lib"
                    and re.fullmatch(r"libpython" + re.escape(version) + r"\.so(?:\.[0-9]+)*", runtime_file.name)
                )
                for prefix, version in python_prefixes
            )
            if not allowed or runtime_file.is_symlink():
                raise NativeValidationSandboxError("verification_toolchain_file_undeclared")
            data, witness = _read(
                KernelFileSystem(str(file_parent), get_default_adapter()), runtime_file.name, max_bytes=_MAX_TOOL_BYTES
            )
            self._tool_file_witnesses[runtime_file] = (_hash(data), witness)
        self._executable_witnesses: dict[Path, tuple[str, tuple[int, ...]]] = {}
        self._invocation_witnesses: dict[Path, str] = {}
        self._stage_root = Path(tempfile.mkdtemp(prefix="polaris-verification-group-", dir=parent))
        self.staging_workspace = self._stage_root / "workspace"
        self._stage_fs = KernelFileSystem(str(self.staging_workspace), get_default_adapter())
        self._root_fs = KernelFileSystem(str(self._stage_root), get_default_adapter())
        self._issued: dict[int, PreparedVerificationCommand] = {}
        self._issued_fingerprints: dict[int, str] = {}
        self._owner_terminal: set[int] = set()
        self._records: list[dict[str, Any]] = []
        self._closed = False
        self._source_current = True
        self._backing_witnesses: dict[str, tuple[int, ...]] = {}
        try:
            self._root_fs.workspace_write_bytes("home/.keep", b"")
            if self._rust_policy:
                self._root_fs.workspace_write_bytes("cargo-home/.keep", b"")
            for path, data in payloads.items():
                self._stage_fs.workspace_write_bytes(path, data)
                self._root_fs.workspace_write_bytes("inputs/" + path, data)
                actual, witness = _read(self._root_fs, "inputs/" + path)
                if _hash(actual) != self.input_hashes[path]:
                    raise NativeValidationSandboxError(f"verification_backing_drift:{path}")
                self._backing_witnesses[path] = witness
            self.assert_inputs_current()
        except (OSError, RuntimeError, ValueError) as exc:
            shutil.rmtree(self._stage_root)
            raise NativeValidationSandboxError(f"verification_staging_failed:{exc}") from exc

    def assert_inputs_current(self) -> None:
        try:
            try:
                current = _isolation_binary_binding(Path(self._bwrap))
                if current != (self._isolation_hash, self._isolation_witness, self._isolation_permissions):
                    raise NativeValidationSandboxError("verification_isolation_binary_drift")
            except (OSError, RuntimeError, ValueError) as exc:
                raise NativeValidationSandboxError("verification_isolation_binary_drift") from exc
            for configuration, (expected, identity) in self._configuration_witnesses.items():
                data, witness = _read(
                    KernelFileSystem(str(configuration.parent), get_default_adapter()),
                    configuration.name,
                    max_bytes=_MAX_TOOL_BYTES,
                )
                if _hash(data) != expected or witness != identity:
                    raise NativeValidationSandboxError("verification_toolchain_configuration_drift")
            for executable, (expected, identity) in self._executable_witnesses.items():
                data, witness = _read(
                    KernelFileSystem(str(executable.parent), get_default_adapter()),
                    executable.name,
                    max_bytes=_MAX_TOOL_BYTES,
                )
                if _hash(data) != expected or witness != identity:
                    raise NativeValidationSandboxError("verification_executable_drift")
            for invocation, expected_binding in self._invocation_witnesses.items():
                if _invocation_binding(invocation) != expected_binding:
                    raise NativeValidationSandboxError("verification_invocation_drift")
            for runtime_file, (expected, identity) in self._tool_file_witnesses.items():
                data, witness = _read(
                    KernelFileSystem(str(runtime_file.parent), get_default_adapter()),
                    runtime_file.name,
                    max_bytes=_MAX_TOOL_BYTES,
                )
                if _hash(data) != expected or witness != identity:
                    raise NativeValidationSandboxError("verification_toolchain_file_drift")
            for path, expected in self.input_hashes.items():
                data, witness = _read(self._source_fs, path)
                if _hash(data) != expected or witness != self._witnesses[path]:
                    raise NativeValidationSandboxError(f"verification_input_drift:{path}")
                actual, backing = _read(self._root_fs, "inputs/" + path)
                if _hash(actual) != expected or backing != self._backing_witnesses[path]:
                    raise NativeValidationSandboxError(f"verification_backing_drift:{path}")
            for _destination, (root, expected) in self._dependencies.items():
                if _mount_hash(root, self._mount_caches[root]) != expected:
                    raise NativeValidationSandboxError("verification_dependency_drift")
            for root, expected in self._tool_hashes.items():
                if _mount_hash(root, self._mount_caches[root]) != expected:
                    raise NativeValidationSandboxError("verification_toolchain_drift")
        except (OSError, RuntimeError, ValueError):
            self._source_current = False
            raise

    def prepare_command(self, argv: Sequence[str], cwd: str = ".") -> PreparedVerificationCommand:
        if self._closed:
            raise NativeValidationSandboxError("verification_group_closed")
        if (
            not argv
            or isinstance(argv, (str, bytes))
            or any(type(item) is not str or not item or "\x00" in item for item in argv)
        ):
            raise NativeValidationSandboxError("verification_argv_invalid")
        self.assert_inputs_current()
        relative_cwd = _relative(cwd, allow_dot=True)
        host_cwd = self._stage_fs.resolve_workspace_path(relative_cwd)
        if not host_cwd.is_dir():
            raise NativeValidationSandboxError("verification_cwd_unavailable")
        resolved = shutil.which(argv[0]) if not Path(argv[0]).is_absolute() else argv[0]
        if not resolved:
            raise NativeValidationSandboxError("verification_executable_unavailable")
        invocation = Path(resolved).absolute()
        executable = invocation.resolve(strict=True)
        mounted = (*[Path(path).resolve() for path in _SYSTEM_ROOTS if Path(path).is_dir()], *self._tools)
        invocation_mounts = (*[Path(path) for path in _SYSTEM_ROOTS if Path(path).is_dir()], *self._tools)
        if not any(executable.is_relative_to(root) for root in mounted) or not any(
            invocation.is_relative_to(root) for root in invocation_mounts
        ):
            raise NativeValidationSandboxError("verification_toolchain_unmounted")
        executable_data, executable_witness = _read(
            KernelFileSystem(str(executable.parent), get_default_adapter()),
            executable.name,
            max_bytes=_MAX_TOOL_BYTES,
        )
        executable_hash = _hash(executable_data)
        self._executable_witnesses[executable] = (executable_hash, executable_witness)
        self._invocation_witnesses[invocation] = _invocation_binding(invocation)
        arguments = [
            self._bwrap or "",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--unshare-all",
            "--clearenv",
        ]
        for path in _SYSTEM_ROOTS:
            _append_optional_ro_bind(arguments, Path(path), path)
        arguments.extend(
            [
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--bind",
                str(self.staging_workspace),
                "/workspace",
                "--bind",
                str(self._stage_root / "home"),
                "/sandbox-home",
            ]
        )
        for root in self._tools:
            arguments.extend(["--ro-bind", str(root), str(root)])
        for runtime_file in self._tool_file_witnesses:
            arguments.extend(["--ro-bind", str(runtime_file), str(runtime_file)])
        if self._rust_policy:
            arguments.extend(
                [
                    "--dir",
                    "/rustup",
                    "--dir",
                    "/rustup/toolchains",
                    "--bind",
                    str(self._stage_root / "cargo-home"),
                    "/cargo-home",
                ]
            )
            for destination, root in self._rust_mounts.items():
                arguments.extend(["--ro-bind", str(root), destination])
        for destination, (root, _digest) in self._dependencies.items():
            arguments.extend(["--ro-bind", str(root), "/workspace/" + destination])
        for path in self.input_hashes:
            arguments.extend(["--ro-bind", str(self._stage_root / "inputs" / path), "/workspace/" + path])
        path_parts = [part for root in self._tools for part in (str(root / "bin"), str(root))]
        environment = ("PATH", ":".join([*path_parts, "/usr/local/bin", "/usr/bin", "/bin"]))
        policy = (
            environment,
            ("HOME", "/sandbox-home"),
            ("CI", "1"),
            ("LANG", "C.UTF-8"),
            ("LC_ALL", "C.UTF-8"),
            ("KERNELONE_VALIDATION_SANDBOX", "1"),
            ("PYTHONPATH", "/workspace"),
            ("PYTHONDONTWRITEBYTECODE", "1"),
            *self._rust_policy,
        )
        for name, value in policy:
            arguments.extend(["--setenv", name, value])
        arguments.extend(
            ["--chdir", "/workspace" if cwd == "." else "/workspace/" + relative_cwd, "--", str(invocation), *argv[1:]]
        )
        prepared = PreparedVerificationCommand(
            len(self._issued) + 1,
            tuple(argv),
            tuple(arguments),
            str(host_cwd),
            self.input_hash,
            executable_hash,
            policy,
        )
        self._issued[prepared.command_id] = prepared
        self._issued_fingerprints[prepared.command_id] = _hash_payload(prepared.__dict__)
        return prepared

    def execute_command(
        self,
        prepared: PreparedVerificationCommand,
        *,
        timeout: float,
        cancel_control: ProcessTreeRunControl | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Consume only this group's fixed containment command through the spawn owner.

        The public runner's normal return / cancellation / timeout paths prove
        terminal drain. Unknown/drain errors never authorize destruction. No
        caller result, PID, proof object or boolean can enter this transition.
        """
        if (
            self._closed
            or self._issued.get(prepared.command_id) is not prepared
            or self._issued_fingerprints.get(prepared.command_id) != _hash_payload(prepared.__dict__)
            or any(row["command_id"] == prepared.command_id for row in self._records)
            or prepared.argv[0] != self._bwrap
            or "--unshare-all" not in prepared.argv
            or "--die-with-parent" not in prepared.argv
        ):
            raise NativeValidationSandboxError("verification_execution_identity_invalid")
        if isinstance(timeout, bool) or not math.isfinite(float(timeout)) or timeout <= 0:
            raise NativeValidationSandboxError("verification_timeout_invalid")
        if env is not None and any(name.startswith(("LD_", "DYLD_")) for name in env):
            raise NativeValidationSandboxError("verification_host_loader_environment_forbidden")
        self.assert_inputs_current()
        try:
            result: subprocess.CompletedProcess[str] = _owned_process_runner(
                prepared.argv,
                cwd=prepared.cwd,
                timeout=timeout,
                cancel_control=cancel_control,
                env=dict(env) if env is not None else {},
                encoding="utf-8",
                errors="replace",
            )
        except (ProcessTreeCancelledError, subprocess.TimeoutExpired) as exc:
            self._owner_terminal.add(prepared.command_id)
            stdout = getattr(exc, "output", "") or ""
            stderr = getattr(exc, "stderr", "") or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            self._records.append(
                {
                    "command_id": prepared.command_id,
                    "argv": list(prepared.logical_argv),
                    "wrapped_argv": list(prepared.argv),
                    "exit_code": None,
                    "timed_out": isinstance(exc, subprocess.TimeoutExpired),
                    "cancelled": isinstance(exc, ProcessTreeCancelledError),
                    "error": str(exc),
                    "stdout": stdout,
                    "stderr": stderr,
                    "output_hash": _hash((stdout + "\0" + stderr).encode("utf-8")),
                    "input_hash": prepared.input_hash,
                    "executable_hash": prepared.executable_hash,
                    "isolation_binary": self._isolation_observation(),
                    "process_owner_terminal": True,
                    "authoritative": False,
                }
            )
            self.assert_inputs_current()
            raise
        self._owner_terminal.add(prepared.command_id)
        self.record_result(prepared, result)
        self._records[-1]["process_owner_terminal"] = True
        return result

    def record_result(
        self, prepared: PreparedVerificationCommand, result: subprocess.CompletedProcess[str], *, drained: bool = False
    ) -> dict[str, Any]:
        if self._issued.get(prepared.command_id) is not prepared or tuple(result.args) != prepared.argv:
            raise NativeValidationSandboxError("verification_result_command_mismatch")
        if any(row["command_id"] == prepared.command_id for row in self._records):
            raise NativeValidationSandboxError("verification_result_already_recorded")
        self.assert_inputs_current()
        for path, expected in self.input_hashes.items():
            if _hash(self._stage_fs.workspace_read_bytes(path)) != expected:
                raise NativeValidationSandboxError("verification_staged_input_drift")
        row: dict[str, Any] = {
            "command_id": prepared.command_id,
            "argv": list(prepared.logical_argv),
            "wrapped_argv": list(prepared.argv),
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "output_hash": _hash((result.stdout + "\0" + result.stderr).encode("utf-8")),
            "input_hash": prepared.input_hash,
            "executable_hash": prepared.executable_hash,
            "isolation_binary": self._isolation_observation(),
            "drained_observation": bool(drained),
            "authoritative": False,
        }
        self._records.append(row)
        return dict(row)

    def _isolation_observation(self) -> dict[str, Any]:
        return {
            "path": self._bwrap,
            "sha256": self._isolation_hash,
            "witness": list(self._isolation_witness),
            "uid": self._isolation_permissions[0],
            "gid": self._isolation_permissions[1],
            "mode": self._isolation_permissions[2],
            "authoritative": False,
        }

    def effects_summary(self) -> dict[str, Any]:
        outputs: dict[str, str] = {}
        if self.staging_workspace.is_dir():
            for path in sorted(self.staging_workspace.rglob("*")):
                relative = path.relative_to(self.staging_workspace).as_posix()
                if path.is_symlink():
                    continue
                if path.is_file() and relative not in self.input_hashes:
                    outputs[relative] = _hash(self._stage_fs.workspace_read_bytes(relative))
        return {
            "schema_version": "verification_group_observation.v1",
            "candidate_id": self.candidate_id,
            "input_hashes": dict(self.input_hashes),
            "input_hash": self.input_hash,
            "dependency_hashes": {path: digest for path, (_root, digest) in self._dependencies.items()},
            "commands": [dict(row) for row in self._records],
            "output_hashes": outputs,
            "source_binding_current": self._source_current,
            "authoritative": False,
            "receipt_sealed": False,
            "dispose_allowed": self._source_current and self._owner_terminal == set(self._issued),
            "drain_status": (
                "pending_process_owner"
                if self._owner_terminal != set(self._issued)
                else "owner_terminal"
                if self._issued
                else "no_command_issued"
            ),
            "staging_retained": self._stage_root.exists(),
        }

    def close(self) -> dict[str, Any]:
        if self._closed:
            return self.effects_summary()
        self._closed = True
        if self._source_current and self._owner_terminal == set(self._issued):
            shutil.rmtree(self._stage_root)
        # No TemporaryDirectory/finalizer: caller observations cannot authorize
        # destruction while an issued command may still have a writer.
        return self.effects_summary()


@contextmanager
def verification_group(
    *,
    workspace: Path,
    candidate_id: str,
    input_hashes: Mapping[str, str],
    dependency_roots: Mapping[str, Path] | None = None,
    toolchain_roots: Sequence[Path] = (),
    staging_parent: Path | None = None,
) -> Iterator[VerificationGroup]:
    try:
        group = VerificationGroup(
            workspace=workspace,
            candidate_id=candidate_id,
            input_hashes=input_hashes,
            dependency_roots=dependency_roots or {},
            toolchain_roots=toolchain_roots,
            staging_parent=staging_parent,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise NativeValidationSandboxError(f"verification_group_unavailable:{exc}") from exc
    try:
        yield group
        group.assert_inputs_current()
    finally:
        group.close()
