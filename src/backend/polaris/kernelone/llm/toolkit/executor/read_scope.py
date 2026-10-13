"""Filesystem operand checks derived from an existing committed read scope.

Ancestor permission is metadata enumeration only, consumed by repo_tree's
filtered handler. It never expands content-read or write capability.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone.exceptions import PathSecurityError

if TYPE_CHECKING:
    from polaris.kernelone.fs import KernelFileSystem


PATH_FIELDS = frozenset({"file", "path", "paths", "root", "directory", "dir", "target", "source"})


def resolve_committed_read_paths(filesystem: KernelFileSystem, token: Mapping[str, object]) -> tuple[Path, ...] | None:
    """Resolve a validated scope; None means the undefined legacy scope."""
    if "allowed_read_paths" not in token:
        return None
    scope = token["allowed_read_paths"]
    if (
        not isinstance(scope, (list, tuple))
        or not scope
        or any(not isinstance(item, str) or not item.strip() for item in scope)
    ):
        raise ValueError("Missing or invalid committed read scope")
    return tuple(filesystem.resolve_workspace_path(item) for item in scope)


def read_scope_permits(
    filesystem: KernelFileSystem,
    token: Mapping[str, object],
    operands: Sequence[str],
    *,
    allow_filtered_ancestors: bool = False,
) -> bool:
    """Check operands without turning ancestor visibility into a read grant."""
    try:
        authorized = resolve_committed_read_paths(filesystem, token)
        if authorized is None:
            return True
        for operand in operands:
            target = filesystem.resolve_workspace_path(operand)
            if not any(
                target == path
                or (filesystem.workspace_is_dir(str(path)) and target.is_relative_to(path))
                or (allow_filtered_ancestors and path.is_relative_to(target))
                for path in authorized
            ):
                return False
    except (OSError, ValueError, PathSecurityError):
        return False
    return True


def registered_read_scope_permits(
    filesystem: KernelFileSystem,
    token: Mapping[str, object],
    tool_name: str,
    arguments: Mapping[str, object],
    spec: Mapping[str, Any],
) -> bool:
    """Use canonical registered operands/defaults, shared by both dispatch gates."""
    if "allowed_read_paths" not in token:
        return True
    operands: list[str] = []
    for parameter in spec.get("arguments", []):
        if not isinstance(parameter, dict) or parameter.get("name") not in PATH_FIELDS:
            continue
        name = str(parameter["name"])
        value = arguments.get(name, parameter.get("default", "."))
        values = value if isinstance(value, (list, tuple)) else [value]
        if not values or any(not isinstance(item, str) or not item.strip() for item in values):
            return False
        operands.extend(str(item) for item in values)
    if tool_name == "repo_diff":
        operands.append(".")
    return read_scope_permits(filesystem, token, operands, allow_filtered_ancestors=tool_name == "repo_tree")
