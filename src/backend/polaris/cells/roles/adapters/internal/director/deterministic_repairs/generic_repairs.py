"""Pure generic deterministic-repair helpers kept for adapter compatibility.

File-mutating generic repairs are owned by
``polaris.cells.director.runtime``. Missing declared target diagnostics are
task-boundary/orchestration facts, not repair-kernel write operations.
"""

from __future__ import annotations

import re
from typing import Any

from ..task_scope_paths import (
    _task_text_blob,
)
from ._common import (
    _PATCH_RESIDUE_LINE_RE,
    _SCAFFOLD_MARKER_REPLACEMENTS,
    _parse_missing_declared_target_files,
)


def _remove_patch_residue_lines(text: str) -> str:
    """Remove generated patch protocol markers that leaked into source files."""

    cleaned = _PATCH_RESIDUE_LINE_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if text.endswith("\n") and not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned


def _task_allows_scaffold_marker_cleanup(task: dict[str, Any]) -> bool:
    metadata_raw = task.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    if str(metadata.get("autofix_reason") or "").strip() == "deterministic_scaffold_residue_cleanup":
        return True
    task_text = _task_text_blob(task).lower()
    return "scaffold" in task_text and "residue" in task_text and "audit-seed" in task_text


def _replace_deterministic_scaffold_markers(text: str) -> str:
    cleaned = str(text or "")
    for marker, replacement in _SCAFFOLD_MARKER_REPLACEMENTS:
        cleaned = cleaned.replace(marker, replacement)
    return cleaned


def _filter_pre_materialization_declared_target_errors(artifact_quality_errors: list[str]) -> list[str]:
    filtered: list[str] = []
    for missing_path in _parse_missing_declared_target_files(artifact_quality_errors):
        if _pre_materialization_declared_target_repair_allowed(missing_path):
            filtered.append(f"Artifact quality scan failed: declared target file missing {missing_path!r}")
    return filtered


def _pre_materialization_declared_target_repair_allowed(relative_path: str) -> bool:
    lowered = str(relative_path or "").strip().replace("\\", "/").lower()
    if lowered in {"package.json", "pyproject.toml", "tsconfig.json", "readme.md"}:
        return True
    if lowered.startswith("src/") and lowered.endswith((".model.ts", ".repository.ts")):
        return True
    return (
        lowered == "src/models/task.model.ts"
        or lowered.endswith("/task.model.ts")
        or lowered == "src/models/tenant.model.ts"
        or lowered.endswith("/tenant.model.ts")
        or lowered == "src/services/taskgraph.ts"
        or lowered.endswith("/taskgraph.ts")
        or lowered == "tests/unit/taskgraph.test.ts"
        or lowered.endswith("/taskgraph.test.ts")
    )
