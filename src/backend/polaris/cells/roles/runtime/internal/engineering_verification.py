"""Final commands over the existing command owner, bound to observed inputs.

The host owns output grants. Source is read-only in each command sandbox; the
single engineering journal retains observations and their historical binding.
Metadata and content comparisons detect ordinary outside edits, including an
edit followed by restoration of the original bytes. They are not an immutable
filesystem snapshot or a proof about arbitrary external/network effects.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import platform
import re
import stat
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_io import run_owned_io
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools, bounded_output
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits, ToolObservation
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.fs import GuardedMetadataError, read_guarded_regular_file_snapshot
from polaris.kernelone.fs.runtime import KernelFileSystem
from polaris.kernelone.process.command_cache import resolve_command_cache
from polaris.kernelone.process.process_tree import ProcessTreeDrainError
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

from .engineering_roles import _VERIFICATION_GUIDANCE

# Captured when the verifier module loads. A cold reader can distinguish
# observations produced by a previous implementation without touching the candidate.
_VERIFIER_DIGEST = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class CandidateObservationUnavailableError(ValueError):
    """Current input observation is incomplete; this grants no verification authority."""


# A granted read root larger than this is an environment/toolchain mount, not a
# declared candidate input; it is excluded from capture identity (see
# EngineeringVerification._capture_roots).
_MAX_CAPTURE_ROOT_ENTRIES = 10_000

_EROFS_PATH = re.compile(r"(?:Could not write file|cannot create directory|mkdir)\D{0,40}?'([^']+)'")
_EROFS_WRITE_PATH = re.compile(
    r"(?:open|cannot create regular file)\s+'?([^':\n]+?)'?\s*:\s*(?:read-only file system|EROFS)",
    re.IGNORECASE,
)
_EROFS_CN_PATH = re.compile(r"(?:无法写入临时文件|无法创建输出目录)[：:]\s*([^（(]+)")
# A failed check tried to create/write inside the read-only verification scope.
# Coreutils prints "Read-only file system"; tsc prints "EROFS: read-only file
# system"; npm reports a failed mkdir as "npm ERR! code ENOENT ... syscall
# mkdir". All three mean the same thing: the check must be prepared first.
READ_ONLY_MARKERS = (
    "read-only file system",
    "erofs",
    "syscall mkdir",
    "无法写入临时文件",
    "无法创建输出目录",
    "只读文件系统",
)


def is_read_only_failure(content: str) -> bool:
    """True when a failed check's output says it could not write/create."""
    lowered = content.lower()
    return any(marker in lowered for marker in READ_ONLY_MARKERS)


def read_only_write_targets(content: str) -> list[str]:
    """Name the workspace paths a failed check tried to write.

    Single source for both the dispatch feedback and the read-only corrective
    note: the message must describe the actual failure (a build output the check
    tried to create), not a generic dependency-install hint, so a weak model can
    act on it instead of repeating the same write.
    """
    if not is_read_only_failure(content):
        return []
    targets: list[str] = []
    for pattern in (_EROFS_PATH, _EROFS_WRITE_PATH, _EROFS_CN_PATH):
        for match in pattern.finditer(content):
            path = match.group(1).strip()
            if path not in targets:
                targets.append(path)
    return targets[:3]


def verification_observation(call_id: str, payload: dict[str, Any], max_output_bytes: int) -> ToolObservation:
    """Project a durable Verifier completion, never a model's claimed verdict."""
    status = payload.get("verification", "unknown")
    binding = payload.get("input_binding", {})
    commands = binding.get("commands", [])
    checks = payload.get("checks", [])
    if status not in {"passed", "failed", "not_run", "unknown"}:
        status = "unknown"
    if status == "passed" and (
        not commands
        or not isinstance(checks, list)
        or len(checks) != len(commands)
        or any(
            not isinstance(check, dict)
            or check.get("ok") is not True
            or check.get("exit_code") != 0
            or check.get("interrupted")
            or check.get("timed_out")
            for check in checks
        )
    ):
        status = "unknown"
    if max_output_bytes <= 0:
        raise ValueError("verification output limit must be positive")
    summary = {
        "verification": status,
        # Reflect the completed attempt's actual binding, never original
        # Director write permissions or a guessed historical profile.
        "execution_scope": binding.get("scope") if isinstance(binding.get("scope"), dict) else None,
        "verification_output_policy": binding.get("verification_output_policy"),
        **({"execution_policy": binding["execution_policy"]} if "execution_policy" in binding else {}),
        # Keep scope and a legal next step ahead of potentially long outputs.
        "detail": f"{_VERIFICATION_GUIDANCE}\n{payload.get('detail', '')}",
        "verification_commands": commands,
        "checks": checks,
        "workspace_revision": payload.get("workspace_revision"),
        "attempt_seq": binding.get("attempt_seq"),
    }
    return ToolObservation(
        call_id,
        "verify_project",
        status == "passed",
        bounded_output(json.dumps(summary, ensure_ascii=False), max_output_bytes),
        None,
        status == "unknown",
    )


class EngineeringVerification:
    """Use while the service owns its workspace lock and has stopped source writes."""

    def __init__(
        self,
        workspace: Path,
        *,
        store: EngineeringSessionStore,
        scope: PermissionScope,
        limits: RunLimits,
        cancel: asyncio.Event,
        writable_output_roots: tuple[Path, ...] = (),
        retain_verified_candidate: bool = False,
        authorize: Callable[[], None] | None = None,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        if store.workspace != self.workspace:
            raise ValueError("verification store workspace mismatch")
        self.store = store
        self.scope = scope
        self.cancel = cancel
        self._retain_verified_candidate = retain_verified_candidate
        # Only a host query may install an actual capture, never a caller's
        # binding. Context-local lifetime also isolates concurrent readers.
        self._observed_inputs: ContextVar[dict[str, object] | None] = ContextVar(
            "engineering_current_inputs", default=None
        )
        # Declarations remain the original authority, including future paths.
        # This attempt's command mounts never expand after construction.
        self._declared_output_roots = writable_output_roots
        self.output_roots = self._validate_outputs(self._declared_output_roots)
        # No file tools and no wholesale Director write grant in final checks.
        executable = any(ToolSpecRegistry.get_canonical(name) == "execute_command" for name in scope.enabled_tools)
        self.tools = EngineeringTools(
            self.workspace,
            PermissionScope(
                scope.read_roots,
                self.output_roots,
                frozenset({"execute_command"}) if executable else frozenset(),
                scope.network_allowed,
                scope.browser,
                toolchain_bin_dirs=scope.toolchain_bin_dirs,
                command_cache="disabled" if scope.command_cache == "disabled" else "read_only",
            ),
            limits,
            cancel,
            authorize=authorize,
        )

    def _validate_outputs(self, roots: tuple[Path, ...]) -> tuple[Path, ...]:
        declarations = self.validate_declarations(self.workspace, roots, write_roots=self.scope.write_roots)
        filesystem = KernelFileSystem(str(self.workspace), LocalFileSystemAdapter())
        effective: list[Path] = []
        for root in declarations:
            try:
                metadata = filesystem.workspace_lstat(str(root))
            except GuardedMetadataError as error:
                if error.code == "guarded_metadata_missing":
                    continue
                raise ValueError(f"verification output is unavailable: {root}") from error
            if not stat.S_ISDIR(metadata.mode):
                raise ValueError("verification output must be a real workspace-local directory below source root")
            effective.append(root)
        return tuple(effective)

    @staticmethod
    def validate_declarations(
        workspace: Path,
        roots: tuple[Path, ...],
        *,
        write_roots: tuple[Path, ...] | None = None,
        require_exists: bool = False,
    ) -> tuple[Path, ...]:
        """Validate exact future host grants without mkdir or following links."""
        outputs: set[Path] = set()
        for supplied in roots:
            try:
                candidate = Path(supplied).expanduser().absolute()
                target = candidate.resolve(strict=require_exists)
                if (
                    target == workspace
                    or not target.is_relative_to(workspace)
                    or target.is_relative_to(workspace / ".polaris")
                    or (target.exists() and not target.is_dir())
                    or (require_exists and not target.is_dir())
                    or candidate != target
                ):
                    raise ValueError("verification output must be a real workspace-local directory below source root")
                current = workspace
                for part in target.relative_to(workspace).parts:
                    current /= part
                    if current.is_symlink():
                        raise ValueError("verification output cannot traverse symlinks")
                if write_roots is not None:
                    grants = [Path(root).expanduser().absolute() for root in write_roots]
                    if any(grant != grant.resolve(strict=True) for grant in grants):
                        raise ValueError("verification output original authority cannot traverse symlinks")
                    if not any(target.is_relative_to(grant) for grant in grants):
                        raise ValueError("verification output exceeds original write authority")
                outputs.add(target)
            except (OSError, RuntimeError) as error:
                raise ValueError(f"verification output is unavailable: {supplied}") from error
        return tuple(sorted(outputs))

    @contextmanager
    def _observe_current_inputs(self) -> Iterator[None]:
        """Share one current KFS capture within a synchronous host read only.

        The capture reuses the last full content-hash observation while an
        lstat-only signature proves the tree is unchanged; any change forces a
        fresh full capture. Execution before/after captures still call
        _manifest directly. Reset even on errors; ordinary calls and the next
        public query stay fresh.
        """
        with self._observe_captured_inputs(self._fresh_capture(include_outputs=True)):
            yield

    @contextmanager
    def _observe_captured_inputs(self, observed: Mapping[str, object]) -> Iterator[None]:
        """Bind an already captured manifest to the current host read.

        The capture itself is a full content-hash walk of every granted read
        root.  On a real project tree that takes seconds, and running it on
        the event loop steals the wall-clock budget of the provider request
        that is waiting for its answer: the request then fails even though
        the provider answered in milliseconds.  An asynchronous caller
        therefore captures off the loop and binds the result here.
        """
        token = self._observed_inputs.set(dict(observed))
        try:
            yield
        finally:
            self._observed_inputs.reset(token)

    async def capture_current_inputs(self, *, include_outputs: bool = True) -> dict[str, object]:
        """Capture the current inputs off the event loop."""
        return await asyncio.to_thread(self._fresh_capture, include_outputs)

    def _current_manifest(self) -> dict[str, object]:
        # Use the observed inputs when a host query already captured them;
        # otherwise read the manifest directly without populating the
        # verification_input_cache, so a later explicit capture (e.g. snapshot)
        # still observes fresh inputs instead of a stale cached copy.
        observed = self._observed_inputs.get()
        return observed if observed is not None else self._manifest(include_outputs=True)

    def _input_cache_key(self, include_outputs: bool) -> tuple[object, ...]:
        """Everything that can change which inputs a capture observes."""
        return (
            tuple(sorted(str(Path(root).expanduser().absolute()) for root in self.scope.read_roots)),
            tuple(str(root) for root in self.output_roots),
            self.tools.scope.command_cache,
            include_outputs,
        )

    def _fresh_capture(self, include_outputs: bool) -> dict[str, object]:
        """Current inputs, reusing the last full capture while metadata proves it.

        A signature miss (or an unavailable signature walk) falls through to
        the full KFS content-hash capture and refreshes the store-level cache.
        Concurrent duplicate walks are harmless: the (signature, manifest)
        pair is assigned atomically, and a racing reader re-walks at most once.
        """
        key = self._input_cache_key(include_outputs)
        cached = self.store.verification_input_cache.get(key)
        if cached is not None:
            try:
                if cached[0] == self._metadata_signature(include_outputs) and self._inputs_are_readable():
                    return cached[1]
            except (OSError, ValueError, RuntimeError, PathSecurityError, GuardedMetadataError):
                pass
        manifest = self._manifest(include_outputs=include_outputs)
        with contextlib.suppress(OSError, ValueError, RuntimeError, PathSecurityError, GuardedMetadataError):
            self.store.verification_input_cache[key] = (self._metadata_signature(include_outputs), manifest)
        return manifest

    def _inputs_are_readable(self) -> bool:
        """Whether the platform's own file port can still read an input.

        A cached capture proves that the tree did not change, but a current
        pass also needs the platform to still be able to observe its inputs.
        We only need lstat metadata (existence + regular file), not content,
        so this probe does not re-read file content and does not add reads.
        """
        for root in sorted(self._capture_roots()):
            for path in sorted(root.rglob("*")):
                if path.is_relative_to(self.workspace / ".polaris"):
                    continue
                try:
                    metadata = path.lstat()
                except OSError:
                    return False
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                # lstat alone proves the file exists and is a regular file;
                # we do not need to read content to observe that it is there.
                return True
        return True

    def _capture_roots(self) -> set[Path]:
        """The roots a capture observes: the workspace and its declared reads.

        A granted read root outside the workspace is a declared dependency and
        stays part of candidate identity (mutating it must invalidate a pass),
        but only while its observed size stays within a per-root budget. Larger
        mounts are toolchain or environment authorities (same rationale as
        ``_host_runtime_roots``): binding them would walk hundreds of thousands
        of unrelated files, including content hashes, on every capture without
        proving anything about the candidate.
        """
        host_runtime = self._host_runtime_roots()
        roots = {
            root
            for root in {self.workspace, *(Path(root).expanduser().absolute() for root in self.scope.read_roots)}
            if not any(root == runtime or root.is_relative_to(runtime) for runtime in host_runtime)
        }
        roots.add(self.workspace)
        return {
            root
            for root in roots
            if root.resolve(strict=False) == self.workspace or not root.is_dir() or self._within_capture_budget(root)
        }

    @staticmethod
    def _within_capture_budget(root: Path) -> bool:
        """Cheap early-aborting file count; bounds every capture walk."""
        limit = _MAX_CAPTURE_ROOT_ENTRIES
        count = 0
        for _dirpath, _dirnames, filenames in os.walk(root):
            count += len(filenames)
            if count > limit:
                return False
        return True

    def _metadata_signature(self, include_outputs: bool) -> bytes:
        """Lstat-only tree signature; proves a cached capture is still current.

        Reads no file content. Any ordinary edit (create, delete, rename,
        content write, metadata change) advances at least one stat field --
        in particular ctime_ns, which the kernel sets and userspace cannot
        rewind -- so a matching signature means the last full capture still
        describes the tree. This is a freshness shortcut on the documented
        "not an immutable filesystem snapshot" boundary, never a substitute
        for the authoritative content-hash capture on a signature miss.
        """
        self._validate_outputs(self._declared_output_roots)
        roots = self._capture_roots()
        if any(root != root.resolve(strict=True) for root in roots):
            raise ValueError("verification input root changed or traverses a symlink")
        if any(root != self.workspace and self.workspace.is_relative_to(root) for root in roots):
            raise ValueError("verification input root is broader than workspace")
        outer = sorted(
            root for root in roots if not any(root != parent and root.is_relative_to(parent) for parent in roots)
        )
        digest = hashlib.sha256()

        def ignored(path: Path) -> bool:
            return path.is_relative_to(self.workspace / ".polaris") or (
                not include_outputs and any(path.is_relative_to(root) for root in self.output_roots)
            )

        def feed(path: Path, *, cache_input: bool = False) -> None:
            if not cache_input and ignored(path):
                return
            metadata = path.lstat()
            digest.update(f"{path} {self._metadata(metadata)}".encode())
            if stat.S_ISLNK(metadata.st_mode):
                digest.update(os.readlink(path).encode("utf-8"))
            elif stat.S_ISDIR(metadata.st_mode):
                children = sorted(child for child in path.iterdir() if cache_input or not ignored(child))
                for child in children:
                    feed(child, cache_input=cache_input)
            elif not stat.S_ISREG(metadata.st_mode):
                raise CandidateObservationUnavailableError(f"verification cannot bind special input: {path}")

        for root in outer:
            feed(root)
        cache = resolve_command_cache(self.workspace, self.tools.scope.command_cache)
        if cache is not None:
            cache.validate()
            feed(cache.root, cache_input=True)
            cache.validate()
        return digest.digest()

    def _fixture_manifest(self) -> dict[str, dict[str, object]]:
        """Re-read bounded host-owned files even when a large external mount is excluded from the candidate manifest."""
        result: dict[str, dict[str, object]] = {}
        for raw, expected in self.scope.verification_fixture_sha256:
            path = Path(raw)
            if path.is_relative_to(self.workspace) or any(path.is_relative_to(root) for root in self.scope.write_roots):
                raise CandidateObservationUnavailableError(
                    f"verification fixture is in a candidate or writable root: {path}"
                )
            roots = [root for root in self.scope.read_roots if path != root and path.is_relative_to(root)]
            if not roots:
                raise CandidateObservationUnavailableError(
                    f"verification fixture is outside granted read roots: {path}"
                )
            root = max(roots, key=lambda item: len(item.parts))
            snapshot = read_guarded_regular_file_snapshot(root, path.relative_to(root).as_posix(), 16 * 1024 * 1024)
            observed = hashlib.sha256(snapshot.content).hexdigest()
            if observed != expected:
                raise CandidateObservationUnavailableError(
                    f"verification fixture digest mismatch: {path} expected={expected} observed={observed}"
                )
            result[raw] = {
                "sha256": observed,
                "size": snapshot.size,
                "device": snapshot.device,
                "inode": snapshot.inode,
                "ctime_ns": snapshot.ctime_ns,
                "mtime_ns": snapshot.mtime_ns,
            }
        return result

    def review_binding(self, session_id: str) -> dict[str, object]:
        """Host observations bind a review to exact current inputs, never model IDs."""
        row = self.store.load_run(session_id, workspace=self.workspace)
        completed = self.store.load_events(session_id, kinds=("verification_completed",))
        # The immutable completion already binds its effective commands. Keep
        # this shape compatible with existing, unchanged QA candidates.
        return {
            "workspace_revision": row["workspace_revision"],
            "verification_seq": completed[-1]["seq"] if completed else 0,
            "required_checks": row["required_checks"],
            "scope": row["scope"],
            "profile": self._profile(),
            "inputs": self._current_manifest(),
            **({"fixture_manifest": self._fixture_manifest()} if self.scope.verification_fixture_sha256 else {}),
        }

    def _evidence_identity(self, row: dict[str, Any], commands: list[str]) -> dict[str, object]:
        def digest(payload: Mapping[str, object]) -> str:
            data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            return hashlib.sha256(data.encode("utf-8")).hexdigest()

        contract = {
            "instruction": row["instruction"],
            "declared_checks": row["required_checks"],
            "effective_checks": commands,
            "require_qa": bool(row["require_qa"]),
        }
        # This identifies the declared host context. External services and
        # arbitrary toolchain contents are outside this environment fingerprint.
        environment = {
            "python": sys.version,
            "kernel": platform.release(),
            "system": platform.system(),
            "machine": platform.machine(),
            "command_profile": self._profile(),
            "execution_policy": row["verification_execution_policy"],
        }
        return {
            "schema": "engineering.check_identity.v1",
            "contract_digest": digest(contract),
            "verifier_digest": _VERIFIER_DIGEST,
            "verifier_scope": __name__,
            "environment": environment,
            "environment_digest": digest(environment),
        }

    def _profile(self) -> dict[str, object]:
        return {
            **(
                {"verification_fixture_sha256": dict(self.scope.verification_fixture_sha256)}
                if self.scope.verification_fixture_sha256
                else {}
            ),
            "read_roots": [str(Path(root).absolute()) for root in self.scope.read_roots],
            "output_roots": [str(root) for root in self.output_roots],
            "execute_command": "execute_command" in self.tools.enabled,
            "network_allowed": self.scope.network_allowed,
            **({"command_cache": self.tools.scope.command_cache} if self.scope.command_cache != "disabled" else {}),
            **(
                {"toolchain_bin_dirs": [str(path) for path in self.tools.scope.toolchain_bin_dirs]}
                if self.tools.scope.toolchain_bin_dirs
                else {}
            ),
        }

    @staticmethod
    def _metadata(information: os.stat_result) -> list[int]:
        return [
            information.st_dev,
            information.st_ino,
            information.st_mode,
            information.st_nlink,
            information.st_size,
            information.st_mtime_ns,
            information.st_ctime_ns,
        ]

    def _host_runtime_roots(self) -> set[Path]:
        """Host runtime mounts that are not project inputs.

        The granted toolchain directories and the browser runtime are the
        platform's own authority, not the delivered project's inputs.  They
        can hold tens of thousands of files, so binding them into the
        verification input manifest would make every verification enormous
        without proving anything about the candidate.
        """
        roots: set[Path] = set()
        for directory in self.scope.toolchain_bin_dirs:
            candidate = Path(directory).expanduser().absolute()
            roots.add(candidate.parent if candidate.parent != candidate else candidate)
        browser = self.scope.browser
        executable = str(getattr(browser, "executable", "") or "").strip()
        if executable:
            binary = Path(executable).expanduser().absolute()
            roots.add(binary.parent.parent if binary.parent.parent != binary.parent else binary.parent)
        return roots

    def _manifest(self, *, include_outputs: bool) -> dict[str, object]:
        """Observe actual declared inputs through KFS; never use a model digest."""
        self._validate_outputs(self._declared_output_roots)
        roots = self._capture_roots()
        if any(root != root.resolve(strict=True) for root in roots):
            raise ValueError("verification input root changed or traverses a symlink")
        if any(root != self.workspace and self.workspace.is_relative_to(root) for root in roots):
            raise ValueError("verification input root is broader than workspace")
        # A nested read grant is already included by its parent observation.
        outer = sorted(
            root for root in roots if not any(root != parent and root.is_relative_to(parent) for parent in roots)
        )
        entries: dict[str, object] = {}

        def ignored(path: Path) -> bool:
            return path.is_relative_to(self.workspace / ".polaris") or (
                not include_outputs and any(path.is_relative_to(root) for root in self.output_roots)
            )

        def inspect(path: Path, filesystem: KernelFileSystem, *, cache_input: bool = False) -> None:
            if not cache_input and ignored(path):
                return
            before = path.lstat()
            metadata = self._metadata(before)
            if stat.S_ISLNK(before.st_mode):
                value: object = {"metadata": metadata, "symlink": os.readlink(path)}
            elif stat.S_ISDIR(before.st_mode):
                children = sorted(child for child in path.iterdir() if cache_input or not ignored(child))
                value = {"metadata": metadata, "children": [child.name for child in children]}
                for child in children:
                    inspect(child, filesystem, cache_input=cache_input)
            elif stat.S_ISREG(before.st_mode):
                content = filesystem.workspace_read_bytes(str(path))
                value = {"metadata": metadata, "sha256": hashlib.sha256(content).hexdigest()}
            else:
                raise CandidateObservationUnavailableError(f"verification cannot bind special input: {path}")
            if self._metadata(path.lstat()) != metadata:
                raise CandidateObservationUnavailableError(f"verification input changed while observed: {path}")
            entries[str(path)] = value

        for root in outer:
            filesystem_root = root if root.is_dir() else root.parent
            filesystem = KernelFileSystem(str(filesystem_root), LocalFileSystemAdapter())
            inspect(root, filesystem)
        # Keep general control hiding unchanged. Only the host-authorized exact
        # cache leaf is an executable/dependency input, including its link and
        # mode metadata. Read observations must never allocate or repair it.
        cache = resolve_command_cache(self.workspace, self.tools.scope.command_cache)
        if cache is not None:
            cache.validate()
            filesystem = KernelFileSystem(str(cache.root), LocalFileSystemAdapter(), read_only_paths=True)
            inspect(cache.root, filesystem, cache_input=True)
            cache.validate()
        return entries

    def _source_view(self, manifest: dict[str, object]) -> dict[str, object]:
        """Compare the very same result capture, allowing declared build writes.

        Strips volatile filesystem metadata (inode, ctime, etc.) so the
        comparison reflects content changes only, not incidental metadata churn
        from read-only command execution.  Keeps size and mtime_ns, which
        change when file content is written, so an edit-and-restore during a
        check is still detected as a source change.
        """
        source: dict[str, object] = {}
        for name, entry in manifest.items():
            path = Path(name)
            if any(path.is_relative_to(root) for root in self.output_roots):
                continue
            if isinstance(entry, dict):
                stripped: dict[str, object] = {}
                if "sha256" in entry:
                    stripped["sha256"] = entry["sha256"]
                if "symlink" in entry:
                    stripped["symlink"] = entry["symlink"]
                metadata = entry.get("metadata")
                if isinstance(metadata, list) and len(metadata) >= 6:
                    # Keep only size (index 4) and mtime_ns (index 5);
                    # strip dev, ino, mode, nlink, ctime_ns which can
                    # change incidentally during read-only execution.
                    stripped["metadata"] = [metadata[4], metadata[5]]
                if "children" in entry:
                    stripped["children"] = [
                        child
                        for child in entry["children"]
                        if not any((path / child).is_relative_to(root) for root in self.output_roots)
                    ]
                source[name] = stripped
        return source

    async def run_verification(
        self,
        commands: tuple[str, ...],
        *,
        session_id: str,
        workspace_revision: int,
        requested_by: tuple[int, str] | None = None,
    ) -> tuple[ToolObservation, ...]:
        """Record dispatch, execute read-only checks, then project their bound result."""
        observations: list[ToolObservation] = []
        binding: dict[str, Any] = {
            "commands": list(commands),
            "workspace_revision": workspace_revision,
            "scope": self._profile(),
        }

        def record(status: str, detail: str) -> None:
            self.store.record_verification(
                session_id,
                workspace_revision=workspace_revision,
                checks=tuple(observations),
                verification=status,
                detail=detail,
                input_binding=binding,
            )

        run = self.store.load_run(session_id, workspace=self.workspace)
        from polaris.cells.roles.runtime.internal.engineering_execution_policy import resolve_execution_policy

        execution_policy = resolve_execution_policy(run.get("verification_execution_policy"))
        saved_declarations = run.get("verification_output_roots")
        # These roots were observed when this Verifier constructed its command
        # scope. Later manifest reads must not rewrite this attempt's policy.
        absent = (
            [str(root) for root in self._declared_output_roots if root not in self.output_roots]
            if saved_declarations is not None
            else None
        )
        binding["verification_output_policy"] = {
            "declared_output_roots": list(saved_declarations) if saved_declarations is not None else None,
            "effective_output_roots": [str(root) for root in self.output_roots],
            "absent_output_roots": absent,
            "absent_reason": "not_present_at_verifier_scope_construction" if absent else None,
        }
        if requested_by is not None:
            if not self.store._valid_commands(commands, allow_empty=False):
                raise ValueError("verify_project requires nonempty command strings")
            granted = {ToolSpecRegistry.get_canonical(name) for name in self.scope.enabled_tools}
            if not {"verify_project", "execute_command"} <= granted:
                raise PermissionError("verify_project and execute_command require explicit host permission")
            binding["commands"] = self.store._merge_commands(run["required_checks"], commands)
            binding["requested_by"] = list(requested_by)
        binding["evidence_identity"] = self._evidence_identity(run, list(binding["commands"]))
        binding["execution_policy"] = execution_policy.binding(list(binding["commands"]))
        if not commands:
            record("not_run", "No final checks declared.")
            return ()
        if (
            type(workspace_revision) is not int
            or run["workspace_revision"] != workspace_revision
            or (requested_by is None and list(commands) != run["verification_commands"])
            or any(not isinstance(command, str) or not command.strip() or "\0" in command for command in commands)
        ):
            if requested_by is not None:
                raise ValueError("Final checks or requested workspace revision do not match this run.")
            record("unknown", "Final checks or requested workspace revision do not match this run.")
            return ()
        manifest_error = None
        try:
            if dict(self.scope.verification_fixture_sha256) != run["scope"].get("verification_fixture_sha256", {}):
                raise CandidateObservationUnavailableError(
                    "verification fixture policy differs from the saved host profile"
                )
            if self.scope.verification_fixture_sha256:
                if not run["required_checks"]:
                    raise CandidateObservationUnavailableError("pinned fixtures require host-declared checks")
                binding["fixture_manifest"] = await asyncio.to_thread(self._fixture_manifest)
            # The manifest walks and hashes every granted read root.  On a
            # real project tree that takes seconds; running it on the event
            # loop stalls the provider request that is waiting for its
            # answer and steals its wall-clock budget.
            binding["source_manifest"] = self._source_view(
                await asyncio.to_thread(self._manifest, include_outputs=False)
            )
        except (OSError, ValueError, RuntimeError, PathSecurityError) as error:
            manifest_error = error
        binding = self.store.begin_verification(
            session_id,
            commands=commands,
            workspace_revision=workspace_revision,
            input_binding=binding,
            requested_by=requested_by,
        )
        turn_seq = int(binding["attempt_seq"])
        commands = tuple(binding["commands"])
        if manifest_error is not None:
            record("unknown", f"Cannot bind final-check inputs: {manifest_error}")
            return ()
        try:
            for index, command in enumerate(commands):
                if self.cancel.is_set():
                    break
                call_id = f"verification-{turn_seq}-{index}"
                # Explicit maximum supported by the existing command port;
                # its physical execution owner also clamps the run deadline.
                arguments: dict[str, Any] = {"command": command, "timeout": 120}
                self.store.record_tool_dispatch(
                    session_id,
                    turn_seq=turn_seq,
                    call_id=call_id,
                    tool="execute_command",
                    arguments=arguments,
                    role="director",
                )
                observation = await self.tools.execute(
                    call_id, "execute_command", arguments, execution_policy=execution_policy
                )
                self.store.record_tool_observation(
                    session_id, turn_seq=turn_seq, observation=observation, role="director"
                )
                observations.append(observation)
                if observation.interrupted:
                    break
            result_manifest = await asyncio.to_thread(self._manifest, include_outputs=True)
            if "fixture_manifest" in binding and binding["fixture_manifest"] != await asyncio.to_thread(
                self._fixture_manifest
            ):
                record("unknown", "Pinned verification fixtures changed during final checks.")
            elif binding["source_manifest"] != self._source_view(result_manifest):
                record("unknown", "Source inputs changed during final checks.")
            else:
                binding["result_manifest"] = result_manifest
                uncertain = (
                    self.cancel.is_set()
                    or len(observations) != len(commands)
                    or any(item.interrupted or (item.exit_code is None and not item.timed_out) for item in observations)
                )
                status = (
                    "unknown"
                    if uncertain
                    else (
                        "passed"
                        if all(item.ok and item.exit_code == 0 and not item.timed_out for item in observations)
                        else "failed"
                    )
                )
                if (
                    status == "passed"
                    and self._retain_verified_candidate
                    and run["scope"].get("retain_verified_candidate") is True
                ):
                    from polaris.cells.roles.runtime.internal.engineering_checkpoint import (
                        CheckpointUnavailableError,
                        save_checkpoint,
                    )

                    if self.tools.authorize is not None:
                        self.tools.authorize()
                    try:
                        binding["verified_checkpoint"] = await run_owned_io(
                            partial(
                                save_checkpoint,
                                self.workspace,
                                self.store.control_root,
                                result_manifest,
                                (*self.scope.read_roots, *self.output_roots),
                                self._host_runtime_roots(),
                            )
                        )
                    except (ValueError, RuntimeError, OSError) as exc:
                        # Record only bounded classification. Operator paths, file bytes
                        # and raw I/O errors must never enter checkpoint metadata.
                        binding["verified_checkpoint"] = {
                            "status": "unavailable",
                            "reason": str(exc) if isinstance(exc, CheckpointUnavailableError) else type(exc).__name__,
                        }
                    if self.tools.authorize is not None:
                        self.tools.authorize()
                    if result_manifest != await run_owned_io(partial(self._manifest, include_outputs=True)):
                        binding["verified_checkpoint"] = {"status": "unavailable", "reason": "candidate_changed"}
                        record("unknown", "Candidate changed during checkpoint capture.")
                        return tuple(observations)
                record(status, "Final checks observed against current inputs.")
        except ProcessTreeDrainError as drain_error:
            # Never let reporting failure hide the physical owner's unproved
            # drain; the service must retain the workspace writer.
            try:
                record("unknown", "Physical final-check cleanup could not be proved.")
            except BaseException as journal_error:
                raise drain_error from journal_error
            raise
        except asyncio.CancelledError:
            self.cancel.set()
            record("unknown", "Final verification cancelled after physical cleanup.")
            raise
        except (OSError, ValueError, RuntimeError, PathSecurityError) as error:
            record("unknown", f"Final-check observation is uncertain: {error}")
        return tuple(observations)

    def current_completed_result(self, session_id: str) -> dict[str, Any] | None:
        """Return complete real passed/failed evidence only for the current candidate."""
        run = self.store.load_run(session_id, workspace=self.workspace)
        if run["verification"] not in {"passed", "failed"}:
            return None
        completed = self.store.load_events(session_id, kinds=("verification_completed",))
        if not completed:
            return None
        payload = completed[-1]["payload"]
        binding = payload.get("input_binding", {})
        if (
            payload.get("verification") != run["verification"]
            or payload.get("workspace_revision") != run["workspace_revision"]
            or binding.get("commands") != run["verification_commands"]
            or (
                "evidence_identity" in binding
                and binding["evidence_identity"] != self._evidence_identity(run, run["verification_commands"])
            )
            or binding.get("scope") != self._profile()
            or not run["verification_commands"]
            or not self.store.verification_completion_is_observed(session_id, payload)
        ):
            return None
        try:
            if self.scope.verification_fixture_sha256 and binding.get("fixture_manifest") != self._fixture_manifest():
                return None
            return payload if binding.get("result_manifest") == self._current_manifest() else None
        except (OSError, ValueError, RuntimeError, PathSecurityError):
            return None

    def current_verification(self, session_id: str) -> str:
        """Project historical observations only when their actual inputs remain current."""
        run = self.store.load_run(session_id, workspace=self.workspace)
        if run["verification"] not in {"passed", "failed"}:
            return str(run["verification"])
        completed = self.current_completed_result(session_id)
        return str(completed["verification"]) if completed is not None else "unknown"
