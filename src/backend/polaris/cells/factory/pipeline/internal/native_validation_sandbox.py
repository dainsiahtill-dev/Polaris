"""Isolated execution helpers for untrusted native validation commands.

Validation gates measure generated projects; they do not own mutation
authority over those projects. Native test binaries are therefore executed
against a disposable workspace copy inside a bubblewrap sandbox. The source
workspace is never mounted into the sandbox, and the child receives only the
Rust toolchain/cache paths required to compile the copy.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import tomllib

_CARGO_TEST_RUNNING_RE = re.compile(r"^\s*running\s+(?P<count>\d+)\s+tests?\s*$")
_CARGO_TEST_RESULT_RE = re.compile(
    r"^\s*test result:\s+ok\.\s+"
    r"(?P<passed>\d+)\s+passed;\s+"
    r"(?P<failed>\d+)\s+failed;\s+"
    r"(?P<ignored>\d+)\s+ignored;\s+"
    r"(?P<measured>\d+)\s+measured;\s+"
    r"(?P<filtered>\d+)\s+filtered out"
    r"(?:;\s+finished in .*)?\s*$"
)
_COPY_IGNORES = (
    ".git",
    ".polaris",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "__pycache__",
    "build",
    "cmake-build",
    "dist",
    "node_modules",
    "out",
    "target",
)


class NativeValidationSandboxError(RuntimeError):
    """Raised when native validation cannot be isolated safely."""


class NativeValidationContractError(NativeValidationSandboxError):
    """Raised when project configuration can forge validation evidence."""


@dataclass(frozen=True)
class SandboxedNativeCommand:
    """A command prepared for isolated execution."""

    command: list[str]
    backend: str


def is_cargo_test_command(command: Sequence[str]) -> bool:
    """Return whether ``command`` invokes Cargo's native test runner."""

    if len(command) < 2:
        return False
    return (
        Path(str(command[0])).name.lower()
        in {
            "cargo",
            "cargo.exe",
        }
        and str(command[1]).strip() == "test"
    )


def cargo_native_test_count(*outputs: object) -> int:
    """Count successful tests from paired standard-libtest stdout records."""

    text = "\n".join(str(output or "") for output in outputs)
    pending_count: int | None = None
    passed_count = 0
    for line in text.splitlines():
        running_match = _CARGO_TEST_RUNNING_RE.fullmatch(line)
        if running_match:
            pending_count = int(running_match.group("count"))
            continue
        result_match = _CARGO_TEST_RESULT_RE.fullmatch(line)
        if not result_match or pending_count is None:
            continue
        passed = int(result_match.group("passed"))
        failed = int(result_match.group("failed"))
        ignored = int(result_match.group("ignored"))
        measured = int(result_match.group("measured"))
        if failed == 0 and passed + failed + ignored + measured == pending_count:
            passed_count += passed
        pending_count = None
    return passed_count


def _load_project_toml(path: Path, *, label: str) -> dict[str, object]:
    if path.is_symlink():
        raise NativeValidationContractError(f"{label} must not be a symlink")
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise NativeValidationContractError(f"{label} is unreadable or invalid: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def _validate_cargo_manifest_targets(manifest: dict[str, object], *, label: str) -> None:
    for target_kind in ("lib", "bin", "example", "test", "bench"):
        targets = manifest.get(target_kind)
        if isinstance(targets, dict):
            targets = [targets]
        if not isinstance(targets, list):
            continue
        for target in targets:
            if not isinstance(target, dict) or target.get("harness") is not False:
                continue
            name = str(target.get("name") or target.get("path") or "<unnamed>")
            raise NativeValidationContractError(
                f"{label} custom cargo {target_kind} harness is not authoritative: {name}"
            )


def _expand_workspace_entries(
    workspace: Path,
    entries: object,
    *,
    label: str,
) -> set[Path]:
    if entries is None:
        return set()
    if not isinstance(entries, list):
        raise NativeValidationContractError(f"Cargo.toml workspace.{label} must be an array")
    expanded: set[Path] = set()
    for raw_entry in entries:
        entry = str(raw_entry or "").strip()
        entry_path = Path(entry)
        if not entry or entry_path.is_absolute() or ".." in entry_path.parts:
            raise NativeValidationContractError(f"Cargo.toml workspace.{label} entry is unsafe: {entry!r}")
        try:
            matches = tuple(workspace.glob(entry))
        except (OSError, ValueError) as exc:
            raise NativeValidationContractError(f"Cargo.toml workspace.{label} entry is invalid: {entry!r}") from exc
        for match in matches:
            resolved = match.resolve()
            if not resolved.is_relative_to(workspace):
                raise NativeValidationContractError(
                    f"Cargo.toml workspace.{label} escapes validation workspace: {entry!r}"
                )
            expanded.add(resolved)
    return expanded


_CARGO_DEPENDENCY_TABLE_KEYS = frozenset(
    {
        "dependencies",
        "dev-dependencies",
        "dev_dependencies",
        "build-dependencies",
        "build_dependencies",
    }
)


def _path_dependencies_from_table(table: object) -> Iterator[str]:
    if not isinstance(table, dict):
        return
    for specification in table.values():
        if not isinstance(specification, dict):
            continue
        dependency_path = specification.get("path")
        if isinstance(dependency_path, str) and dependency_path.strip():
            yield dependency_path.strip()


def _cargo_path_dependencies(manifest: dict[str, object]) -> Iterator[str]:
    for table_name in _CARGO_DEPENDENCY_TABLE_KEYS:
        yield from _path_dependencies_from_table(manifest.get(table_name))

    targets = manifest.get("target")
    if isinstance(targets, dict):
        for target_contract in targets.values():
            if not isinstance(target_contract, dict):
                continue
            for table_name in _CARGO_DEPENDENCY_TABLE_KEYS:
                yield from _path_dependencies_from_table(target_contract.get(table_name))

    workspace_contract = manifest.get("workspace")
    if isinstance(workspace_contract, dict):
        yield from _path_dependencies_from_table(workspace_contract.get("dependencies"))

    patches = manifest.get("patch")
    if isinstance(patches, dict):
        for patch_table in patches.values():
            yield from _path_dependencies_from_table(patch_table)
    yield from _path_dependencies_from_table(manifest.get("replace"))


def _resolve_dependency_manifest(
    workspace: Path,
    owner_manifest: Path,
    dependency_path: str,
) -> Path:
    candidate_root = (owner_manifest.parent / dependency_path).resolve()
    if not candidate_root.is_relative_to(workspace):
        owner_label = owner_manifest.relative_to(workspace)
        raise NativeValidationContractError(
            f"{owner_label} path dependency escapes validation workspace: {dependency_path!r}"
        )
    candidate = candidate_root if candidate_root.name == "Cargo.toml" else candidate_root / "Cargo.toml"
    if candidate.is_symlink():
        raise NativeValidationContractError(f"{candidate.relative_to(workspace)} must not be a symlink")
    if not candidate.is_file():
        owner_label = owner_manifest.relative_to(workspace)
        raise NativeValidationContractError(
            f"{owner_label} path dependency manifest is unavailable: {dependency_path!r}"
        )
    return candidate


def _workspace_member_manifest_paths(workspace: Path, manifest: dict[str, object]) -> tuple[Path, ...]:
    workspace_contract = manifest.get("workspace")
    if not isinstance(workspace_contract, dict):
        return ()
    excluded = _expand_workspace_entries(
        workspace,
        workspace_contract.get("exclude"),
        label="exclude",
    )
    members = _expand_workspace_entries(
        workspace,
        workspace_contract.get("members"),
        label="members",
    )
    root_manifest = workspace / "Cargo.toml"
    pending: list[Path] = [root_manifest]
    discovered: set[Path] = {root_manifest}
    for member in sorted(members):
        if member in excluded:
            continue
        candidate = member if member.name == "Cargo.toml" else member / "Cargo.toml"
        if candidate.is_symlink():
            raise NativeValidationContractError(f"{candidate.relative_to(workspace)} must not be a symlink")
        if candidate.is_file() and candidate not in discovered:
            discovered.add(candidate)
            pending.append(candidate)

    while pending:
        owner_manifest = pending.pop()
        owner_contract = (
            manifest
            if owner_manifest == root_manifest
            else _load_project_toml(
                owner_manifest,
                label=str(owner_manifest.relative_to(workspace)),
            )
        )
        for dependency_path in _cargo_path_dependencies(owner_contract):
            candidate = _resolve_dependency_manifest(workspace, owner_manifest, dependency_path)
            if candidate.parent in excluded or candidate in discovered:
                continue
            discovered.add(candidate)
            pending.append(candidate)

    discovered.remove(root_manifest)
    return tuple(sorted(discovered))


def _resolve_cargo_manifest_path(workspace: Path) -> Path | None:
    """Return the cargo manifest, accepting Linux-case ``cargo.toml``.

    Live L2-14: Director wrote ``cargo.toml``. Cargo's default lookup and the
    previous contract both required the exact basename ``Cargo.toml``, so
    official quality skipped rust and bench rustc-compiled ``main.rs`` alone.
    """

    canonical = workspace / "Cargo.toml"
    if canonical.is_file():
        return canonical
    try:
        for child in workspace.iterdir():
            if child.is_file() and child.name.lower() == "cargo.toml":
                return child
    except OSError:
        return None
    return None


def _ensure_sandbox_canonical_cargo_manifest(workspace: Path) -> None:
    """Give the sandbox copy the basename cargo itself looks up."""

    canonical = workspace / "Cargo.toml"
    if canonical.is_file():
        return
    source = _resolve_cargo_manifest_path(workspace)
    if source is None:
        return
    try:
        shutil.copyfile(source, canonical)
    except OSError as exc:
        raise NativeValidationSandboxError(f"sandbox could not canonicalize cargo manifest: {exc}") from exc


def _validate_cargo_project_contract(workspace: Path) -> None:
    manifest_path = _resolve_cargo_manifest_path(workspace)
    if manifest_path is None:
        raise NativeValidationContractError("Cargo.toml is required for cargo test")
    manifest = _load_project_toml(manifest_path, label="Cargo.toml")
    _validate_cargo_manifest_targets(manifest, label="Cargo.toml")
    for member_manifest_path in _workspace_member_manifest_paths(workspace, manifest):
        member_label = str(member_manifest_path.relative_to(workspace))
        member_manifest = _load_project_toml(member_manifest_path, label=member_label)
        _validate_cargo_manifest_targets(member_manifest, label=member_label)

    cargo_dir = workspace / ".cargo"
    if cargo_dir.is_symlink():
        raise NativeValidationContractError(".cargo must not be a symlink")
    for name in ("config.toml", "config"):
        config_path = cargo_dir / name
        if not config_path.exists():
            continue
        config = _load_project_toml(config_path, label=f".cargo/{name}")
        build = config.get("build")
        if isinstance(build, dict):
            for key in (
                "rustc",
                "rustc-wrapper",
                "rustc-workspace-wrapper",
                "rustdoc",
                "rustflags",
                "rustdocflags",
                "target",
            ):
                if build.get(key) not in (None, "", [], {}):
                    raise NativeValidationContractError(f".cargo/{name} build.{key} is not allowed")
        target = config.get("target")
        if isinstance(target, dict):
            for target_name, target_config in target.items():
                if not isinstance(target_config, dict):
                    continue
                for key in ("linker", "runner", "rustflags", "rustdocflags"):
                    if target_config.get(key) not in (None, "", [], {}):
                        raise NativeValidationContractError(f".cargo/{name} target.{target_name}.{key} is not allowed")
        env = config.get("env")
        if isinstance(env, dict):
            for key in env:
                normalized = str(key).strip().upper()
                if normalized in {
                    "CARGO_BUILD_RUSTC",
                    "CARGO_BUILD_RUSTC_WRAPPER",
                    "CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER",
                    "CARGO_BUILD_RUSTDOC",
                    "CARGO_BUILD_RUSTFLAGS",
                    "CARGO_BUILD_RUSTDOCFLAGS",
                    "CARGO_BUILD_TARGET",
                    "CARGO_ENCODED_RUSTFLAGS",
                    "CARGO_ENCODED_RUSTDOCFLAGS",
                    "DYLD_INSERT_LIBRARIES",
                    "DYLD_LIBRARY_PATH",
                    "LD_LIBRARY_PATH",
                    "LD_PRELOAD",
                    "PATH",
                    "RUSTC",
                    "RUSTC_WRAPPER",
                    "RUSTC_WORKSPACE_WRAPPER",
                    "RUSTDOC",
                    "RUSTDOCFLAGS",
                    "RUSTFLAGS",
                    "RUST_TEST_NOCAPTURE",
                } or (
                    normalized.startswith("CARGO_TARGET_")
                    and normalized.endswith(("_LINKER", "_RUNNER", "_RUSTFLAGS", "_RUSTDOCFLAGS"))
                ):
                    raise NativeValidationContractError(f".cargo/{name} env.{key} is not allowed")
        aliases = config.get("alias")
        if isinstance(aliases, dict) and "test" in aliases:
            raise NativeValidationContractError(f".cargo/{name} alias.test is not allowed")


def _copy_workspace(source: Path, destination: Path) -> None:
    try:
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns(*_COPY_IGNORES),
        )
    except (OSError, shutil.Error) as exc:
        raise NativeValidationSandboxError(f"validation workspace copy failed: {exc}") from exc


def _append_optional_ro_bind(arguments: list[str], source: Path, destination: str) -> None:
    if source.exists():
        arguments.extend(["--ro-bind", str(source), destination])


def _bubblewrap_command(
    *,
    bubblewrap: str,
    cargo_command: Sequence[str],
    sandbox_workspace: Path,
    sandbox_cargo_home: Path,
    sandbox_home: Path,
) -> list[str]:
    cargo_executable = Path(str(cargo_command[0])).expanduser()
    if not cargo_executable.is_absolute():
        resolved = shutil.which(str(cargo_executable))
        if not resolved:
            raise NativeValidationSandboxError(f"cargo executable unavailable: {cargo_executable}")
        cargo_executable = Path(resolved)
    tool_bin = cargo_executable.parent
    cargo_home = Path(os.environ.get("CARGO_HOME") or (Path.home() / ".cargo")).expanduser().resolve()
    rustup_home = Path(os.environ.get("RUSTUP_HOME") or (Path.home() / ".rustup")).expanduser().resolve()

    arguments = [
        bubblewrap,
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--unshare-ipc",
        "--unshare-net",
        "--unshare-pid",
        "--clearenv",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind-try",
        "/bin",
        "/bin",
        "--ro-bind-try",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind-try",
        "/sbin",
        "/sbin",
        "--ro-bind-try",
        "/etc/alternatives",
        "/etc/alternatives",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/workspace",
        "--bind",
        str(sandbox_workspace),
        "/workspace",
        "--dir",
        "/cargo-home",
        "--bind",
        str(sandbox_cargo_home),
        "/cargo-home",
        "--dir",
        "/sandbox-home",
        "--bind",
        str(sandbox_home),
        "/sandbox-home",
        "--ro-bind",
        str(tool_bin),
        "/tool-bin",
    ]
    _append_optional_ro_bind(arguments, rustup_home, "/rustup")
    _append_optional_ro_bind(arguments, cargo_home / "registry", "/cargo-home/registry")
    _append_optional_ro_bind(arguments, cargo_home / "git", "/cargo-home/git")
    arguments.extend(
        [
            "--chdir",
            "/workspace",
            "--setenv",
            "CARGO_HOME",
            "/cargo-home",
            "--setenv",
            "CARGO_NET_OFFLINE",
            "true",
            "--setenv",
            "CARGO_TARGET_DIR",
            "/workspace/target",
            "--setenv",
            "CI",
            "1",
            "--setenv",
            "HOME",
            "/sandbox-home",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--setenv",
            "LC_ALL",
            "C.UTF-8",
            "--setenv",
            "PATH",
            "/tool-bin:/usr/local/bin:/usr/bin:/bin",
            "--setenv",
            "KERNELONE_VALIDATION_SANDBOX",
            "1",
        ]
    )
    if rustup_home.exists():
        arguments.extend(["--setenv", "RUSTUP_HOME", "/rustup"])
    arguments.extend(
        [
            "--",
            f"/tool-bin/{cargo_executable.name}",
            *[str(part) for part in cargo_command[1:]],
        ]
    )
    return arguments


@contextmanager
def sandboxed_cargo_test_command(
    *,
    workspace: Path,
    command: Sequence[str],
) -> Iterator[SandboxedNativeCommand]:
    """Yield an isolated Cargo test invocation or fail closed."""

    if not is_cargo_test_command(command):
        raise NativeValidationSandboxError("only cargo test may use the native validation sandbox")
    bubblewrap = shutil.which("bwrap")
    if not bubblewrap:
        raise NativeValidationSandboxError("bubblewrap unavailable; refusing unsafe native test execution")
    source = Path(workspace).resolve()
    if not source.is_dir():
        raise NativeValidationSandboxError(f"validation workspace is not a directory: {source}")
    _validate_cargo_project_contract(source)

    with tempfile.TemporaryDirectory(prefix="polaris-native-validation-") as temp_root:
        sandbox_workspace = Path(temp_root) / "workspace"
        sandbox_cargo_home = Path(temp_root) / "cargo-home"
        sandbox_home = Path(temp_root) / "home"
        (sandbox_cargo_home / "registry").mkdir(parents=True)
        (sandbox_cargo_home / "git").mkdir()
        sandbox_home.mkdir()
        _copy_workspace(source, sandbox_workspace)
        _ensure_sandbox_canonical_cargo_manifest(sandbox_workspace)
        yield SandboxedNativeCommand(
            command=_bubblewrap_command(
                bubblewrap=bubblewrap,
                cargo_command=command,
                sandbox_workspace=sandbox_workspace,
                sandbox_cargo_home=sandbox_cargo_home,
                sandbox_home=sandbox_home,
            ),
            backend="bubblewrap_copy_v1",
        )
