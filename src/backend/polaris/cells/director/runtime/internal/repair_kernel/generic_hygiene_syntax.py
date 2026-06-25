"""Generic hygiene repair planning helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

PATCH_RESIDUE_CLEANUP_SOURCE_TOOL = "deterministic_patch_residue_cleanup"

_PATCH_RESIDUE_LINE_RE = re.compile(
    r"(?m)^\s*(?:<{4,7}\s*SEARCH\b.*|>{4,7}\s*REPLACE\b.*|END\s+PATCH_FILE\b.*|PATCH_FILE(?::|\s+).*)\s*$",
    re.IGNORECASE,
)
_PATCH_RESIDUE_FILE_SUFFIXES = frozenset((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))


def remove_patch_residue_lines(text: str) -> str:
    """Remove leaked patch-protocol marker lines from source text."""

    cleaned = _PATCH_RESIDUE_LINE_RE.sub("", str(text or ""))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if str(text or "").endswith("\n") and not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned


def build_patch_residue_cleanup_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic] = (),
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a runtime plan for scoped patch-residue cleanup."""

    operations: list[RepairOperation] = []
    for path, content in sorted(_normalize_base_files(base_files).items()):
        if not _patch_residue_cleanup_supported(path):
            continue
        operations.extend(_patch_residue_line_operations(path=path, content=content))
    if not operations:
        return None
    return RepairPlan(
        rule_id="generic.patch_residue_cleanup",
        source_tool=PATCH_RESIDUE_CLEANUP_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=0,
        metadata={"cleanup": "patch_residue"},
    )


def _normalize_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_path, content in dict(base_files or {}).items():
        path = _normalize_repair_path(str(raw_path or ""))
        if path:
            normalized[path] = str(content or "")
    return normalized


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def _patch_residue_cleanup_supported(path: str) -> bool:
    lowered = str(path or "").lower()
    return any(lowered.endswith(suffix) for suffix in _PATCH_RESIDUE_FILE_SUFFIXES)


def _patch_residue_line_operations(*, path: str, content: str) -> tuple[RepairOperation, ...]:
    operations: list[RepairOperation] = []
    offset = 0
    for line in str(content or "").splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        if _PATCH_RESIDUE_LINE_RE.fullmatch(line_body):
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=offset,
                    span_end=offset + len(line),
                    expected=line,
                    replacement="",
                    before_hash=sha256_text(content),
                    metadata={"cleanup": "patch_residue"},
                )
            )
        offset += len(line)
    return tuple(operations)


__all__ = [
    "PATCH_RESIDUE_CLEANUP_SOURCE_TOOL",
    "build_patch_residue_cleanup_plan",
    "remove_patch_residue_lines",
]
