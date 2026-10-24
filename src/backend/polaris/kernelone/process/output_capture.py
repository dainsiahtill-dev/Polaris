"""Bounded export of real command output from one host-owned scratch directory.

The owner must physically drain the command and descendants before reading or
leaving this resource. Export bounds constrain returned bytes, not disk usage.
"""

from __future__ import annotations

import re
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from polaris.kernelone.fs import (
    KernelFileSystem,
    get_default_adapter,
    guarded_create_directory,
    read_guarded_regular_file_snapshot,
)
from polaris.kernelone.storage import resolve_runtime_path

from .process_tree import ProcessTreeDrainError

_NAMESPACE = "runtime/command-output"
_UUID_NAME = re.compile(r"[0-9a-f]{32}\Z")
_MAX_FILES = 16
_MAX_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class CapturedOutput:
    """Actual bytes of one safely read relative command output."""

    name: str
    data: bytes


def _directory_identity(path: Path) -> tuple[int, int]:
    try:
        if not path.is_absolute() or ".." in path.parts or path.resolve(strict=True) != path:
            raise ValueError("command output directory must be canonical and contain no symlink")
        info = KernelFileSystem(str(path.parent), get_default_adapter(), read_only_paths=True).workspace_lstat(
            path.name
        )
    except (OSError, RuntimeError) as exc:
        raise ValueError("command output directory must exist") from exc
    if not stat.S_ISDIR(info.mode):
        raise ValueError("command output root must be a real directory")
    return info.device, info.inode


def validate_command_output_root(workspace: Path, root: Path, *, empty: bool) -> tuple[int, int]:
    """Validate a direct UUID child of this workspace's exact output namespace."""
    namespace = Path(resolve_runtime_path(str(workspace), _NAMESPACE, read_only=True))
    identity = _directory_identity(root)
    _directory_identity(namespace)
    if root.parent != namespace or not _UUID_NAME.fullmatch(root.name):
        raise ValueError("command output root must be a dedicated UUID child of this workspace runtime")
    if empty:
        listing = KernelFileSystem(
            str(root.parent), get_default_adapter(), read_only_paths=True
        ).workspace_list_directory(root.name, max_entries=1)
        if (listing.directory.device, listing.directory.inode) != identity:
            raise ValueError("command output directory changed before mount")
        if listing.entries or not listing.complete:
            raise ValueError("command output root must be empty before command launch")
    if _directory_identity(root) != identity:
        raise ValueError("command output directory identity changed")
    return identity


class CommandOutputCapture:
    """Own one temporary output directory; read only after physical drain."""

    def __init__(self, workspace: Path, root: Path) -> None:
        self.root = root
        self._workspace = workspace
        self._identity = validate_command_output_root(workspace, root, empty=True)
        self._fs = KernelFileSystem(str(root.parent), get_default_adapter(), read_only_paths=True)
        self._metadata = self._fs.workspace_lstat(root.name)
        if (self._metadata.device, self._metadata.inode) != self._identity:
            raise ValueError("command output root changed during allocation")
        self._closed = False
        self._retained_for_reconciliation = False

    def retain_for_reconciliation(self) -> None:
        """Preserve scratch after uncertain effects; confer no committed-byte authority.

        The caller records the prepared path and reconciliation state in its
        existing journal. Context exit still closes this capture's read API.
        """
        if self._closed:
            raise ValueError("command output capture is closed")
        self._retained_for_reconciliation = True

    def read(
        self,
        names: Sequence[str],
        *,
        max_files: int = _MAX_FILES,
        max_bytes: int = _MAX_BYTES,
    ) -> tuple[CapturedOutput, ...]:
        """Read bounded regular bytes; reject links, drift and incomplete output."""
        if self._closed:
            raise ValueError("command output capture is closed")
        if type(max_files) is not int or not 1 <= max_files <= _MAX_FILES:
            raise ValueError("command output max_files must be between 1 and 16")
        if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_BYTES:
            raise ValueError("command output max_bytes must be between 1 and 1000000")
        if isinstance(names, (str, bytes)) or not isinstance(names, Sequence) or len(names) > max_files:
            raise ValueError("command output names must be a bounded sequence")
        seen: set[str] = set()
        for name in names:
            if (
                not isinstance(name, str)
                or not name
                or "\\" in name
                or "\0" in name
                or name.startswith("/")
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or name in seen
            ):
                raise ValueError("command output names must be unique canonical relative file paths")
            seen.add(name)
        if validate_command_output_root(self._workspace, self.root, empty=False) != self._identity:
            raise ValueError("command output root identity changed")
        outputs: list[CapturedOutput] = []
        size = 0
        for name in names:
            snapshot = read_guarded_regular_file_snapshot(self.root, name, max(1, max_bytes - size))
            if (snapshot.root_device, snapshot.root_inode) != self._identity:
                raise ValueError("command output root identity changed during read")
            size += snapshot.size
            if size > max_bytes:
                raise ValueError("command output aggregate bytes exceed max_bytes")
            outputs.append(CapturedOutput(name, snapshot.content))
        if validate_command_output_root(self._workspace, self.root, empty=False) != self._identity:
            raise ValueError("command output root identity changed after read")
        return tuple(outputs)


@contextmanager
def open_command_output_capture(workspace: Path) -> Iterator[CommandOutputCapture]:
    """Allocate one canonical runtime scratch directory; never infer a workspace.

    The caller must exclusively own the namespace parent and physically drain
    workers before cleanup, shielding/awaiting them on cancellation. A failed
    physical drain retains scratch for its still-possible writer. A failed
    durable observation commit must request retention before context exit.
    """
    project = Path(workspace)
    _directory_identity(project)
    namespace = Path(resolve_runtime_path(str(project), _NAMESPACE, read_only=True))
    runtime = Path(resolve_runtime_path(str(project), "runtime", read_only=True))
    # Existing supervisor-provisioned control/runtime ancestry is mandatory;
    # only the dedicated namespace and one random child are allocated here.
    if not runtime.exists():
        control = project / ".polaris"
        _directory_identity(control)
        if not runtime.is_relative_to(control):
            raise ValueError("configured external runtime must already exist")
        guarded_create_directory(str(control), str(runtime.relative_to(control)), parents=True, exist_ok=True)
    _directory_identity(runtime)
    guarded_create_directory(str(runtime), "command-output", exist_ok=True)
    name = uuid4().hex
    guarded_create_directory(str(namespace), name)
    capture = CommandOutputCapture(project, namespace / name)
    try:
        yield capture
    except ProcessTreeDrainError:
        capture._retained_for_reconciliation = True
        raise
    finally:
        capture._closed = True
        if not capture._retained_for_reconciliation:
            _cleanup_capture(capture)


def _cleanup_capture(capture: CommandOutputCapture) -> None:
    capture._fs.workspace_remove_tree(capture.root.name, expected=capture._metadata)


__all__ = ["CapturedOutput", "CommandOutputCapture", "open_command_output_capture"]
