"""Canonical Go syntax repair rules for Director Runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

GO_BARE_IMPORT_STRING_SOURCE_TOOL = "deterministic_go_bare_import_string_repair"


def repair_go_bare_import_strings_text(text: str) -> str:
    """Repair top-level bare quoted strings that should be Go import statements."""

    lines = str(text or "").split("\n")
    modified = False
    in_import_block = False
    past_package = False

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("package "):
            past_package = True
            continue
        if stripped == "import (" or stripped.startswith("import ("):
            in_import_block = True
            continue
        if in_import_block:
            if stripped == ")":
                in_import_block = False
            continue
        if stripped.startswith("import "):
            continue
        if (
            past_package
            and stripped
            and stripped.startswith('"')
            and stripped.endswith('"')
            and not line.startswith("\t\t")
            and not line.startswith("    ")
        ):
            lines[index] = f"import {stripped}"
            modified = True

    return "\n".join(lines) if modified else str(text or "")


def build_go_bare_import_string_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical plan for Go files with bare import strings."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    for path in sorted(normalized_base_files):
        if not path.endswith(".go") or path.endswith("_test.go"):
            continue
        original = normalized_base_files[path]
        repaired = repair_go_bare_import_strings_text(original)
        if repaired == original:
            continue
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired,
                before_hash=sha256_text(original),
                metadata={"repair_kind": "go_bare_import_string"},
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="go.bare_import_string",
        source_tool=GO_BARE_IMPORT_STRING_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=0,
    )


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


__all__ = [
    "GO_BARE_IMPORT_STRING_SOURCE_TOOL",
    "build_go_bare_import_string_plan",
    "repair_go_bare_import_strings_text",
]
