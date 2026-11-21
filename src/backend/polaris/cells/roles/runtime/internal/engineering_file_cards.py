"""Content-addressed FileCards for bounded source reads."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

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
    path: str, content: str, *, revision: int | None, preview: str, truncated: bool, start_line: int, end_line: int
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
        "content_ref": f"filecard:{digest}",
        "content_preview": preview,
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
