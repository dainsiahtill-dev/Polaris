"""Filesystem materialization evidence helpers."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path


def materialized_file_paths(
    workspace_path: str | Path,
    reported_paths: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Return reported paths that physically exist as non-empty workspace files."""

    workspace_root = Path(workspace_path).resolve()
    materialized: list[str] = []
    unmaterialized: list[str] = []

    for raw_path in reported_paths:
        normalized = str(raw_path or "").replace("\\", "/").strip()
        if not normalized or "\x00" in normalized:
            continue

        candidate = Path(normalized)
        absolute_candidate = candidate if candidate.is_absolute() else workspace_root / candidate
        try:
            resolved_candidate = absolute_candidate.resolve(strict=False)
            if os.path.commonpath([str(workspace_root), str(resolved_candidate)]) != str(workspace_root):
                if normalized not in unmaterialized:
                    unmaterialized.append(normalized)
                continue

            relative_path = resolved_candidate.relative_to(workspace_root).as_posix()
            if resolved_candidate.is_file() and resolved_candidate.stat().st_size > 0:
                if relative_path not in materialized:
                    materialized.append(relative_path)
            elif normalized not in unmaterialized:
                unmaterialized.append(normalized)
        except (OSError, RuntimeError, ValueError):
            if normalized not in unmaterialized:
                unmaterialized.append(normalized)

    return materialized, unmaterialized
