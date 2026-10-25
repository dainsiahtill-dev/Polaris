"""One host-authorized persistent input tree behind an opaque sandbox mount.

This resource never owns a process, a permission decision or a cleanup policy.
Only the existing command owner may allocate it after admission; observations
are strictly read-only and preserve unavailable or unsafe trees as failures.
"""

from __future__ import annotations

import stat
from collections import deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from polaris.kernelone.fs import KernelFileSystem, get_default_adapter, guarded_create_directory
from polaris.kernelone.fs.types import FileMetadataV1
from polaris.kernelone.storage import resolve_runtime_path

COMMAND_CACHE_ALIAS = "/polaris-command-cache"
_MODES = frozenset({"disabled", "read_only", "read_write"})


class CommandCacheUnavailableError(ValueError):
    """A requested cache input cannot be safely observed or mounted."""


def _directory_identity(path: Path) -> tuple[int, int]:
    if not path.is_absolute() or ".." in path.parts:
        raise CommandCacheUnavailableError("command cache ancestry must be canonical")
    filesystem = KernelFileSystem(str(path.parent), get_default_adapter(), read_only_paths=True)
    metadata = filesystem.workspace_lstat("." if path == path.parent else path.name)
    if not stat.S_ISDIR(metadata.mode):
        raise CommandCacheUnavailableError("command cache ancestry must contain only real directories")
    return metadata.device, metadata.inode


def _root(workspace: Path) -> Path:
    root = Path(resolve_runtime_path(str(workspace), "runtime/command-cache/data", read_only=True))
    if root != workspace / ".polaris/runtime/command-cache/data":
        raise CommandCacheUnavailableError("command cache requires the canonical workspace-local runtime layout")
    return root


def _link_parts(text: str, root: Path) -> tuple[bool, tuple[str, ...]]:
    path = PurePosixPath(text)
    if path.is_absolute():
        for allowed in (PurePosixPath(COMMAND_CACHE_ALIAS), PurePosixPath(root)):
            if path.is_relative_to(allowed):
                return True, path.relative_to(allowed).parts
        raise CommandCacheUnavailableError("command cache symlink leaves the cache domain")
    return False, path.parts


def _validate_tree(root: Path) -> None:
    """Observe exact no-follow membership, SDK links and independent inode closure."""
    filesystem = KernelFileSystem(str(root), get_default_adapter(), read_only_paths=True)
    entries: dict[tuple[str, ...], FileMetadataV1] = {}
    links: dict[tuple[str, ...], str] = {}
    aliases: dict[tuple[int, int], int] = {}
    counts: dict[tuple[int, int], int] = {}
    pending: list[tuple[str, ...]] = [()]
    while pending:
        parts = pending.pop()
        listing = filesystem.workspace_list_directory("/".join(parts) or ".", max_entries=2**63 - 1)
        if not listing.complete:
            raise CommandCacheUnavailableError("command cache directory membership is incomplete")
        entries[parts] = listing.directory
        for entry in listing.entries:
            child = (*parts, entry.name)
            metadata = entry.metadata
            entries[child] = metadata
            if stat.S_ISDIR(metadata.mode):
                pending.append(child)
            elif stat.S_ISREG(metadata.mode) or stat.S_ISLNK(metadata.mode):
                identity = metadata.device, metadata.inode
                aliases[identity] = aliases.get(identity, 0) + 1
                counts[identity] = metadata.nlink
                if stat.S_ISLNK(metadata.mode):
                    links[child] = filesystem.workspace_readlink("/".join(child), expected=metadata).target
            else:
                raise CommandCacheUnavailableError("command cache contains a special file")
    if any(aliases[identity] != count for identity, count in counts.items()):
        raise CommandCacheUnavailableError("command cache hardlink has an external, protected or unknown alias")
    for link, text in links.items():
        absolute, target = _link_parts(text, root)
        current = [] if absolute else list(link[:-1])
        remaining = deque(target)
        followed = 0
        while remaining:
            component = remaining.popleft()
            if component == ".":
                continue
            if component == "..":
                if not current:
                    raise CommandCacheUnavailableError("command cache symlink leaves the cache domain")
                current.pop()
                continue
            current.append(component)
            observed = entries.get(tuple(current))
            if observed is None:
                raise CommandCacheUnavailableError("command cache contains a broken symlink")
            if stat.S_ISLNK(observed.mode):
                followed += 1
                if followed > 40:
                    raise CommandCacheUnavailableError("command cache symlink loop or excessive indirection")
                absolute, target = _link_parts(links[tuple(current)], root)
                current = [] if absolute else current[:-1]
                remaining.extendleft(reversed(target))
            elif remaining and not stat.S_ISDIR(observed.mode):
                raise CommandCacheUnavailableError("command cache symlink traverses a non-directory")
        if tuple(current) not in entries:
            raise CommandCacheUnavailableError("command cache contains a broken symlink")
    # Every member observation remains current through the complete validation.
    # This also catches a replaced directory, link or hardlink during the walk.
    for parts, expected in entries.items():
        if filesystem.workspace_lstat("/".join(parts) or ".") != expected:
            raise CommandCacheUnavailableError("command cache changed during tree validation")


@dataclass(frozen=True, slots=True)
class CommandCacheMount:
    """Pinned exact leaf and ancestry; no caller-selected additional mount root."""

    workspace: Path
    root: Path
    mode: str
    _ancestry: tuple[tuple[Path, tuple[int, int]], ...]

    def validate(self) -> None:
        """Revalidate before spawn and after known process drain without writes."""
        try:
            if self.mode not in {"read_only", "read_write"} or self.root != _root(self.workspace):
                raise CommandCacheUnavailableError("command cache mount identity or mode is invalid")
            expected_paths = (*reversed(self.root.parents), self.root)
            if tuple(path for path, _identity in self._ancestry) != expected_paths:
                raise CommandCacheUnavailableError("command cache mount ancestry is invalid")
            for path, identity in self._ancestry:
                if _directory_identity(path) != identity:
                    raise CommandCacheUnavailableError("command cache ancestry identity changed")
            _validate_tree(self.root)
            for path, identity in self._ancestry:
                if _directory_identity(path) != identity:
                    raise CommandCacheUnavailableError("command cache ancestry identity changed")
        except (OSError, RuntimeError, ValueError) as exc:
            if isinstance(exc, CommandCacheUnavailableError):
                raise
            raise CommandCacheUnavailableError(f"command cache validation unavailable: {exc}") from exc


def resolve_command_cache(workspace: Path, mode: str, *, create: bool = False) -> CommandCacheMount | None:
    """Resolve a host mode; disabled touches nothing, readers never allocate.

    ``create=True`` is reserved for admitted ordinary execute_command under its
    existing workspace writer. Missing enabled inputs stay unavailable.
    """
    if not isinstance(mode, str) or mode not in _MODES:
        raise CommandCacheUnavailableError("command cache mode must be disabled, read_only or read_write")
    if mode == "disabled":
        return None
    if type(create) is not bool or (create and mode != "read_write"):
        raise CommandCacheUnavailableError("only admitted read_write commands may allocate command cache")
    project = Path(workspace)
    try:
        _directory_identity(project)
        root = _root(project)
        if create:
            control = project / ".polaris"
            identity = _directory_identity(control)
            guarded_create_directory(str(control), "runtime/command-cache/data", parents=True, exist_ok=True)
            if _directory_identity(control) != identity:
                raise CommandCacheUnavailableError("command cache control ancestry changed during allocation")
        ancestry = tuple((path, _directory_identity(path)) for path in (*reversed(root.parents), root))
        mount = CommandCacheMount(project, root, mode, ancestry)
        mount.validate()
        return mount
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, CommandCacheUnavailableError):
            raise
        raise CommandCacheUnavailableError(f"command cache unavailable: {exc}") from exc


__all__ = ["COMMAND_CACHE_ALIAS", "CommandCacheMount", "CommandCacheUnavailableError", "resolve_command_cache"]
