"""Content-addressed CommandCards for bounded command output and exact recovery."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from polaris.kernelone.fs import (
    GuardedRegularFileSnapshotError,
    guarded_compare_and_create_regular_file,
    read_guarded_regular_file_snapshot,
)

_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
_PREVIEW_BYTES = 4000
_ERROR_PATTERN = re.compile(
    r"(?:\bfail(?:ed|ure)?\b|\berror\b|referenceerror|assertionerror|typeerror|syntaxerror|runtimeerror"
    r"|\bexception\b|\btraceback\b|\bassert(?:ion)?\b|\bdenied\b|\bcannot\b|\bnot found\b|\[FAIL\]|✗)",
    re.IGNORECASE,
)


def _error_excerpt(stdout: str, stderr: str) -> str:
    for line in (stderr or stdout).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("[PASS]"):
            continue
        if _ERROR_PATTERN.search(stripped):
            return stripped[:500]
    return ""


def persist_command_result(workspace: Path, command: str, stdout: str, stderr: str, exit_code: int) -> str | None:
    """Persist exact command output before creating a bounded CommandCard."""

    payload = json.dumps(
        {"command": command, "stdout": stdout, "stderr": stderr, "exit_code": exit_code}, ensure_ascii=False
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    root = workspace / ".polaris" / "runtime" / "command-results"
    for parent, relative in (
        (workspace, ".polaris"),
        (workspace / ".polaris", "runtime"),
        (workspace / ".polaris" / "runtime", "command-results"),
    ):
        if not (parent / relative).exists():
            from polaris.kernelone.fs import guarded_create_directory

            guarded_create_directory(str(parent), relative)
    reference = f"command:{digest}"
    if (root / f"{digest}.json").exists():
        read_command_result(workspace, reference)
        return reference
    try:
        guarded_compare_and_create_regular_file(root, f"{digest}.json", payload, max_bytes=_MAX_OUTPUT_BYTES)
    except (ValueError, OSError, GuardedRegularFileSnapshotError):
        return None
    return reference


def read_command_result(workspace: Path, reference: str) -> dict[str, Any]:
    """Read exact saved output without substituting a new command's output."""

    if not reference.startswith("command:"):
        raise ValueError("invalid command result reference")
    root = workspace / ".polaris" / "runtime" / "command-results"
    try:
        snapshot = read_guarded_regular_file_snapshot(
            root, reference.removeprefix("command:") + ".json", _MAX_OUTPUT_BYTES
        )
    except OSError as error:
        raise ValueError(f"command result snapshot unavailable: {reference}") from error
    payload = json.loads(snapshot.content.decode("utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("command"), str):
        raise ValueError("command result snapshot is invalid")
    if hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest() != reference.removeprefix(
        "command:"
    ):
        raise ValueError("command result snapshot integrity mismatch")
    return payload


def build_command_card(
    command: str, stdout: str, stderr: str, exit_code: int, snapshot_ref: str | None
) -> dict[str, Any]:
    """Create a bounded command result with exact recovery and output bounds."""

    return {
        "schema_version": "engineering.command_card.v1",
        "command": command,
        "exit_code": exit_code,
        "ok": exit_code == 0,
        "summary": _error_excerpt(stdout, stderr)
        or (
            (stderr or stdout).strip()[-500:]
            if exit_code != 0 and (stderr or stdout).strip()
            else "completed"
            if exit_code == 0
            else "command failed"
        ),
        "content_ref": snapshot_ref,
        "recoverable": snapshot_ref is not None,
        "stdout_preview": stdout[:_PREVIEW_BYTES],
        "stderr_preview": stderr[:_PREVIEW_BYTES],
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "presentation_truncated": len(stdout) > _PREVIEW_BYTES or len(stderr) > _PREVIEW_BYTES,
    }
