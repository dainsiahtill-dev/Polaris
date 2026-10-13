"""Read-only projection of command recipes pinned by the existing JobToken."""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from polaris.kernelone.quality.verification_commands import verification_command_authority_hash


def derive_bound_verification_command_scope(
    *, token: Mapping[str, Any], task_id: str, authority_mappings: Sequence[Mapping[str, Any]]
) -> list[dict[str, str]] | None:
    """Reconstruct exact command/cwd pairs from token-pinned authority rows.

    An absent binding keeps legacy semantics. An explicit empty binding denies
    commands. Candidate context is never authority: every row must reproduce a
    hash already present in the base JobToken for this exact task.
    """
    policy = token.get("gate_policy")
    if not isinstance(policy, Mapping) or "command_authority_hashes" not in policy:
        return None
    raw_hashes = policy["command_authority_hashes"]
    if not isinstance(raw_hashes, (list, tuple)) or any(
        not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
        for value in raw_hashes
    ):
        raise ValueError("verification_command_authority_hashes_invalid")
    hashes = set(raw_hashes)
    if not hashes:
        return []
    records = list(authority_mappings)
    rows: list[Mapping[str, Any]] = []
    visited: set[int] = set()
    for record in records:
        if id(record) in visited:
            continue
        visited.add(id(record))
        candidates = record.get("verification_command_authority")
        if isinstance(candidates, (list, tuple)):
            rows.extend(row for row in candidates if isinstance(row, Mapping))
        for key in (
            "metadata",
            "task_metadata",
            "context",
            "task",
            "current_task",
            "task_payload",
            "project_completion_contract",
        ):
            nested = record.get(key)
            if isinstance(nested, Mapping):
                records.append(nested)
    scope: list[dict[str, str]] = []
    matched: set[str] = set()
    for row in rows:
        if row.get("task_id") != task_id:
            continue
        argv = row.get("argv")
        modality = row.get("modality")
        cwd = row.get("cwd")
        if (
            not isinstance(argv, (list, tuple))
            or not argv
            or any(not isinstance(value, str) or not value or "\x00" in value for value in argv)
            or not isinstance(modality, str)
            or not modality
            or not isinstance(cwd, str)
            or not cwd
            or PurePosixPath(cwd).is_absolute()
            or ".." in cwd.split("/")
            or "\\" in cwd
            or ":" in cwd
        ):
            continue
        digest = verification_command_authority_hash(task_id=task_id, modality=modality, argv=tuple(argv), cwd=cwd)
        command = shlex.join(argv)
        if digest not in hashes or row.get("authority_hash") != digest or row.get("command") != command:
            continue
        if digest not in matched:
            scope.append({"command": command, "cwd": cwd, "authority_hash": digest})
            matched.add(digest)
    if matched != hashes:
        raise ValueError("verification_command_authority_rows_missing_or_mismatched")
    return scope
