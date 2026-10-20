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
import os
import stat
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits, ToolObservation
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.fs.runtime import KernelFileSystem
from polaris.kernelone.process.process_tree import ProcessTreeDrainError
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


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
            ),
            limits,
            cancel,
        )

    def _validate_outputs(self, roots: tuple[Path, ...]) -> tuple[Path, ...]:
        outputs: set[Path] = set()
        for supplied in roots:
            try:
                candidate = Path(supplied).expanduser().absolute()
                target = candidate.resolve(strict=True)
                if (
                    target == self.workspace
                    or not target.is_relative_to(self.workspace)
                    or target.is_relative_to(self.workspace / ".polaris")
                    or not target.is_dir()
                    or candidate != target
                ):
                    raise ValueError("verification output must be a real workspace-local directory below source root")
                current = self.workspace
                for part in target.relative_to(self.workspace).parts:
                    current /= part
                    if current.is_symlink():
                        raise ValueError("verification output cannot traverse symlinks")
                grants = [Path(root).expanduser().absolute() for root in self.scope.write_roots]
                if any(grant != grant.resolve(strict=True) for grant in grants):
                    raise ValueError("verification output original authority cannot traverse symlinks")
                if not any(target.is_relative_to(grant) for grant in grants):
                    raise ValueError("verification output exceeds original write authority")
                outputs.add(target)
            except (OSError, RuntimeError) as error:
                raise ValueError(f"verification output is unavailable: {supplied}") from error
        return tuple(sorted(outputs))

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
                raise ValueError(f"verification cannot bind special input: {path}")
            if self._metadata(path.lstat()) != metadata:
                raise ValueError(f"verification input changed while observed: {path}")
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
        self, commands: tuple[str, ...], *, session_id: str, workspace_revision: int
    ) -> tuple[ToolObservation, ...]:
        """Record dispatch, execute read-only checks, then project their bound result."""
        observations: list[ToolObservation] = []
        binding: dict[str, object] = {
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
        if not commands:
            record("not_run", "No final checks declared.")
            return ()
        if (
            type(workspace_revision) is not int
            or run["workspace_revision"] != workspace_revision
            or list(commands) != run["required_checks"]
            or any(not isinstance(command, str) or not command.strip() or "\0" in command for command in commands)
        ):
            record("unknown", "Final checks or requested workspace revision do not match this run.")
            return ()
        try:
            binding["source_manifest"] = self._manifest(include_outputs=False)
        except (OSError, ValueError, RuntimeError, PathSecurityError) as error:
            record("unknown", f"Cannot bind final-check inputs: {error}")
            return ()
        turn_seq = self.store.append_event(
            session_id, kind="verification_started", role="director", payload={"input_binding": binding}
        )
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
                    or any(item.interrupted or item.exit_code is None for item in observations)
                )
                status = (
                    "unknown"
                    if uncertain
                    else ("passed" if all(item.ok and item.exit_code == 0 for item in observations) else "failed")
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

    def current_verification(self, session_id: str) -> str:
        """Project historical observations only when their actual inputs remain current."""
        run = self.store.load_run(session_id, workspace=self.workspace)
        if run["verification"] != "passed":
            return str(run["verification"])
        completed = [event for event in self.store.load_events(session_id) if event["kind"] == "verification_completed"]
        if not completed:
            return "unknown"
        payload = completed[-1]["payload"]
        binding = payload.get("input_binding", {})
        if (
            payload.get("verification") != "passed"
            or payload.get("workspace_revision") != run["workspace_revision"]
            or binding.get("commands") != run["required_checks"]
            or binding.get("scope") != self._profile()
            or not run["required_checks"]
        ):
            return "unknown"
        try:
            return "passed" if binding.get("result_manifest") == self._manifest(include_outputs=True) else "unknown"
        except (OSError, ValueError, RuntimeError, PathSecurityError):
            return "unknown"
