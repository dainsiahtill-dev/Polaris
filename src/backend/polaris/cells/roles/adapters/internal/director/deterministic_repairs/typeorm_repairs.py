"""Deterministic typeorm model normalization repair, carved verbatim."""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

from ..execution_tools import DirectorToolExecutor
from ._common import (
    _TS_CLASS_FIELD_DECL_RE,
    _TS_DECORATOR_LINE_RE,
    _TYPEORM_IMPORT_LINE_RE,
    _parse_undeclared_runtime_import_paths,
)


def _apply_deterministic_typeorm_model_normalization_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    target_paths = _parse_undeclared_runtime_import_paths(artifact_quality_errors, package_name="typeorm")
    if not target_paths:
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    results: list[dict[str, Any]] = []
    for rel_path in target_paths:
        target_path = (workspace_path / rel_path).resolve()
        try:
            target_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not target_path.is_file():
            continue
        try:
            original = target_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        normalized = _normalize_undeclared_typeorm_model_source(original)
        if normalized == original:
            continue
        write_result = executor.execute_tool(
            "write_file",
            {"file": rel_path, "content": normalized},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=rel_path)
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typeorm_model_normalization_repair",
                    "file": rel_path,
                    "bytes_written": int(write_result.get("bytes_written") or len(normalized.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "modify"),
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return results


def _normalize_undeclared_typeorm_model_source(text: str) -> str:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        if _TYPEORM_IMPORT_LINE_RE.match(raw_line):
            continue
        if _TS_DECORATOR_LINE_RE.match(raw_line):
            continue
        lines.append(_normalize_ts_class_field_initialization(raw_line))
    normalized = "\n".join(lines).strip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", normalized)


def _normalize_ts_class_field_initialization(line: str) -> str:
    match = _TS_CLASS_FIELD_DECL_RE.match(line)
    if not match:
        return line
    indent = match.group("indent")
    name = match.group("name")
    optional = match.group("optional")
    type_text = str(match.group("type") or "").strip()
    if optional:
        return f"{indent}{name}?: {type_text};"
    lowered = type_text.lower()
    if "[]" in type_text:
        return f"{indent}{name}: unknown[] = [];"
    if lowered == "string":
        return f'{indent}{name}: string = "";'
    if lowered == "number":
        return f"{indent}{name}: number = 0;"
    if lowered == "boolean":
        return f"{indent}{name}: boolean = false;"
    if lowered == "date":
        return f"{indent}{name}: Date = new Date(0);"
    return f"{indent}{name}: unknown = null;"
