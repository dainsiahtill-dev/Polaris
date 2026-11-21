"""Content-addressed FileCards for bounded source reads."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from polaris.kernelone.fs import (
    GuardedRegularFileSnapshotError,
    guarded_compare_and_create_regular_file,
    guarded_create_directory,
    read_guarded_regular_file_snapshot,
)

_MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


def persist_file_snapshot(workspace: Path, path: str, content: str) -> str:
    """Persist full immutable bytes before exposing a recoverable FileCard ref."""

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    root = workspace / ".polaris" / "runtime" / "filecards"
    reference = f"filecard:{digest}"
    if (root / f"{digest}.json").exists():
        read_file_snapshot(workspace, reference)
        return reference
    for parent, relative in (
        (workspace, ".polaris"),
        (workspace / ".polaris", "runtime"),
        (workspace / ".polaris" / "runtime", "filecards"),
    ):
        if not (parent / relative).exists():
            guarded_create_directory(str(parent), relative)
    payload = json.dumps({"path": path, "sha256": digest, "content": content}, ensure_ascii=False).encode("utf-8")
    guarded_compare_and_create_regular_file(root, f"{digest}.json", payload, max_bytes=_MAX_SNAPSHOT_BYTES)
    return f"filecard:{digest}"


def read_file_snapshot(workspace: Path, reference: str) -> dict[str, Any]:
    """Read the exact saved snapshot; never substitute the live file for a missing ref."""

    if not reference.startswith("filecard:"):
        raise ValueError("invalid filecard reference")
    root = workspace / ".polaris" / "runtime" / "filecards"
    name = reference.removeprefix("filecard:") + ".json"
    try:
        snapshot = read_guarded_regular_file_snapshot(root, name, _MAX_SNAPSHOT_BYTES)
    except (OSError, GuardedRegularFileSnapshotError) as error:
        raise ValueError(f"filecard snapshot unavailable: {reference}") from error
    payload = json.loads(snapshot.content.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("filecard snapshot must be a JSON object")
    if (
        payload.get("sha256") != reference.removeprefix("filecard:")
        or hashlib.sha256(payload["content"].encode("utf-8")).hexdigest() != payload["sha256"]
    ):
        raise ValueError("filecard snapshot integrity mismatch")
    return payload


_SYMBOL_PATTERNS = (
    re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:class|def|function|interface|type|enum|fn|struct|trait|impl)\s+([A-Za-z_][A-Za-z0-9_]*)"
    ),
    re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*(?:pub\s+)?(?:type|func)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*(?:class|struct|namespace)\s+([A-Za-z_][A-Za-z0-9_]*)"),
)


def _symbols(content: str) -> list[str]:
    symbols: list[str] = []
    for line in content.splitlines():
        for pattern in _SYMBOL_PATTERNS:
            match = pattern.match(line)
            if match:
                symbols.append(match.group(1))
                break
        if len(symbols) >= 24:
            break
    return symbols


def build_file_card(
    path: str,
    content: str,
    *,
    revision: int | None,
    preview: str,
    truncated: bool,
    start_line: int,
    end_line: int,
    snapshot_ref: str | None = None,
) -> dict[str, Any]:
    """Create a deterministic card; full source remains available through content_ref."""

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    lines = content.splitlines()
    symbols = _symbols(content)
    return {
        "schema_version": "engineering.file_card.v1",
        "path": path,
        "revision": revision,
        "sha256": digest,
        "size_bytes": len(content.encode("utf-8")),
        "line_count": len(lines),
        "symbols": symbols,
        "summary": f"{Path(path).name} · {len(lines)} lines · {len(symbols)} symbols",
        "content_ref": snapshot_ref or f"filecard:{digest}",
        "snapshot_ref": snapshot_ref,
        "recoverable": snapshot_ref is not None,
        "content_preview": preview,
        "coverage": {
            "complete_source": True,
            "source_truncated": False,
            "observed_line_ranges": [[start_line, end_line]],
            "presentation_truncated": truncated,
        },
        "content_bounds": {
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": len(lines),
            "truncated": truncated,
        },
        "stale": False,
    }


def card_from_observation(content: str) -> dict[str, Any] | None:
    """Read a FileCard from a durable read_file observation, if present."""

    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    card = payload.get("file_card") if isinstance(payload, dict) else None
    return card if isinstance(card, dict) and card.get("schema_version") == "engineering.file_card.v1" else None
