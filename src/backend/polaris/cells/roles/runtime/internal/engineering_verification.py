"""Final commands over the existing command owner, bound to observed inputs.

The host owns output grants. Source is read-only in each command sandbox; the
single engineering journal retains observations and their historical binding.
Metadata and content comparisons detect ordinary outside edits, including an
edit followed by restoration of the original bytes. They are not an immutable
filesystem snapshot or a proof about arbitrary external/network effects.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools, bounded_output
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits, ToolObservation
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.fs.runtime import KernelFileSystem
from polaris.kernelone.process.process_tree import ProcessTreeDrainError
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

from .engineering_roles import _VERIFICATION_GUIDANCE


class CandidateObservationUnavailableError(ValueError):
    """Current input observation is incomplete; this grants no verification authority."""


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
        authorize: Callable[[], None] | None = None,
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        if store.workspace != self.workspace:
            raise ValueError("verification store workspace mismatch")
        self.store = store
        self.scope = scope
        self.cancel = cancel
        self.output_roots = self._validate_outputs(writable_output_roots)
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
            ),
            limits,
            cancel,
            authorize=authorize,
        )

    def _validate_outputs(self, roots: tuple[Path, ...]) -> tuple[Path, ...]:
        return self.validate_declarations(
            self.workspace, roots, write_roots=self.scope.write_roots, require_exists=True
        )

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
            "inputs": self._manifest(include_outputs=True),
        }

    def _profile(self) -> dict[str, object]:
        return {
            "read_roots": [str(Path(root).absolute()) for root in self.scope.read_roots],
            "output_roots": [str(root) for root in self.output_roots],
            "execute_command": "execute_command" in self.tools.enabled,
            "network_allowed": self.scope.network_allowed,
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

    def _manifest(self, *, include_outputs: bool) -> dict[str, object]:
        """Observe actual declared inputs through KFS; never use a model digest."""
        self._validate_outputs(self.output_roots)
        roots = {self.workspace, *(Path(root).expanduser().absolute() for root in self.scope.read_roots)}
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

        def inspect(path: Path, filesystem: KernelFileSystem) -> None:
            if ignored(path):
                return
            before = path.lstat()
            metadata = self._metadata(before)
            if stat.S_ISLNK(before.st_mode):
                value: object = {"metadata": metadata, "symlink": os.readlink(path)}
            elif stat.S_ISDIR(before.st_mode):
                children = sorted(child for child in path.iterdir() if not ignored(child))
                value = {"metadata": metadata, "children": [child.name for child in children]}
                for child in children:
                    inspect(child, filesystem)
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
        return entries

    def _source_view(self, manifest: dict[str, object]) -> dict[str, object]:
        """Compare the very same result capture, allowing declared build writes."""
        source: dict[str, object] = {}
        for name, entry in manifest.items():
            path = Path(name)
            if any(path.is_relative_to(root) for root in self.output_roots):
                continue
            if isinstance(entry, dict) and "children" in entry:
                entry = {
                    **entry,
                    "children": [
                        child
                        for child in entry["children"]
                        if not any((path / child).is_relative_to(root) for root in self.output_roots)
                    ],
                }
            source[name] = entry
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
        if requested_by is not None:
            if not self.store._valid_commands(commands, allow_empty=False):
                raise ValueError("verify_project requires nonempty command strings")
            granted = {ToolSpecRegistry.get_canonical(name) for name in self.scope.enabled_tools}
            if not {"verify_project", "execute_command"} <= granted:
                raise PermissionError("verify_project and execute_command require explicit host permission")
            binding["commands"] = self.store._merge_commands(run["required_checks"], commands)
            binding["requested_by"] = list(requested_by)
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
            binding["source_manifest"] = self._manifest(include_outputs=False)
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
                observation = await self.tools.execute(call_id, "execute_command", arguments)
                self.store.record_tool_observation(
                    session_id, turn_seq=turn_seq, observation=observation, role="director"
                )
                observations.append(observation)
                if observation.interrupted:
                    break
            result_manifest = self._manifest(include_outputs=True)
            if binding["source_manifest"] != self._source_view(result_manifest):
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
        completed = [event for event in self.store.load_events(session_id) if event["kind"] == "verification_completed"]
        if not completed:
            return None
        payload = completed[-1]["payload"]
        binding = payload.get("input_binding", {})
        if (
            payload.get("verification") != run["verification"]
            or payload.get("workspace_revision") != run["workspace_revision"]
            or binding.get("commands") != run["verification_commands"]
            or binding.get("scope") != self._profile()
            or not run["verification_commands"]
            or not self.store.verification_completion_is_observed(session_id, payload)
        ):
            return None
        try:
            return payload if binding.get("result_manifest") == self._manifest(include_outputs=True) else None
        except (OSError, ValueError, RuntimeError, PathSecurityError):
            return None

    def current_verification(self, session_id: str) -> str:
        """Project historical observations only when their actual inputs remain current."""
        run = self.store.load_run(session_id, workspace=self.workspace)
        if run["verification"] not in {"passed", "failed"}:
            return str(run["verification"])
        completed = self.current_completed_result(session_id)
        return str(completed["verification"]) if completed is not None else "unknown"
