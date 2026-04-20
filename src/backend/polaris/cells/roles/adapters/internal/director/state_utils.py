"""状态追踪辅助工具

包含输出要求提取、代码行统计、领域关键词提取、任务上下文收集等辅助功能。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .helpers import _DOMAIN_STOPWORDS, _MIN_FILES_PATTERN, _MIN_LINES_PATTERN

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Output Requirements Extraction
# -----------------------------------------------------------------------------


def extract_output_requirements(task: dict[str, Any]) -> tuple[int, int]:
    """从任务中提取最低输出要求（文件数和行数）"""
    min_files = 1
    min_lines = 1

    text_blocks: list[str] = [
        str(task.get("subject") or ""),
        str(task.get("description") or ""),
    ]
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    if isinstance(metadata, dict):
        text_blocks.extend(
            [
                str(metadata.get("goal") or ""),
                str(metadata.get("scope") or ""),
            ]
        )
        steps = metadata.get("steps")
        if isinstance(steps, list):
            text_blocks.extend(str(step or "") for step in steps[:10])

    combined_text = "\n".join(block for block in text_blocks if str(block or "").strip())
    file_match = _MIN_FILES_PATTERN.search(combined_text)
    line_match = _MIN_LINES_PATTERN.search(combined_text)

    if file_match:
        try:
            min_files = max(1, int(file_match.group(1)))
        except (TypeError, ValueError):
            min_files = 1
    if line_match:
        try:
            min_lines = max(1, int(line_match.group(1)))
        except (TypeError, ValueError):
            min_lines = 1
    return min_files, min_lines


# -----------------------------------------------------------------------------
# Code Line Counting
# -----------------------------------------------------------------------------


def count_changed_code_lines(workspace: str, file_paths: list[str]) -> int:
    """统计变更文件的代码行数"""
    workspace_path = Path(workspace).resolve()
    total_lines = 0
    for rel_path in file_paths:
        target = (workspace_path / rel_path).resolve()
        if workspace_path not in target.parents and target != workspace_path:
            continue
        if not target.exists() or not target.is_file():
            continue
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = content.splitlines()
        total_lines += len(lines)
    return total_lines


def is_output_sparse(
    *,
    file_count: int,
    line_count: int,
    min_files: int,
    min_lines: int,
) -> bool:
    """判断输出是否稀疏"""
    return file_count < max(1, min_files) or line_count < max(1, min_lines)


# -----------------------------------------------------------------------------
# Domain Token Extraction
# -----------------------------------------------------------------------------


def extract_domain_tokens(task: dict[str, Any]) -> list[str]:
    """从任务描述中提取领域关键词"""
    subject = str(task.get("subject") or "").strip().lower()
    description = str(task.get("description") or "").strip().lower()
    tokens = re.findall(r"[a-z][a-z0-9_-]{2,}", f"{subject} {description}")
    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in _DOMAIN_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
    return unique[:10]


# -----------------------------------------------------------------------------
# Tool Results Summary
# -----------------------------------------------------------------------------


def summarize_tool_results(tool_results: list[dict[str, Any]]) -> str:
    """提炼工具结果，避免上下文爆炸。"""
    import json

    summary: list[dict[str, Any]] = []
    for item in tool_results[:4]:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool") or "")
        success = bool(item.get("success", False))
        error = str(item.get("error") or "").strip()
        result = item.get("result")
        reduced: dict[str, Any] = {
            "tool": tool_name,
            "success": success,
        }
        if error:
            reduced["error"] = error[:400]
        if isinstance(result, dict):
            clipped = {}
            for key in ("file", "path", "query", "count", "results", "stdout", "stderr"):
                if key in result:
                    value = result.get(key)
                    if isinstance(value, str) and len(value) > 1200:
                        value = value[:1200] + "...[truncated]"
                    clipped[key] = value
                if clipped:
                    reduced["result"] = clipped
        summary.append(reduced)

    payload = {"tool_results": summary}
    return json.dumps(payload, ensure_ascii=False, indent=2)


# -----------------------------------------------------------------------------
# Pending Task Context
# -----------------------------------------------------------------------------


def collect_pending_task_context(
    workspace: str,
    task_board: Any,
    *,
    limit: int = 3,
) -> list[str]:
    """收集待办任务上下文"""

    lines: list[str] = []
    entries = task_board.list_all()

    for entry in entries:
        record: dict[str, Any]
        if isinstance(entry, dict):
            record = entry
        elif hasattr(entry, "to_dict"):
            record = entry.to_dict()
        else:
            continue

        status = str(record.get("status") or "").strip().lower()
        if status in {"completed", "done", "failed", "cancelled"}:
            continue
        subject = str(record.get("subject") or record.get("title") or "").strip()
        if not subject:
            continue
        _raw_meta = record.get("metadata")
        metadata: dict[str, Any] = _raw_meta if isinstance(_raw_meta, dict) else {}
        scope = str(metadata.get("scope") or "") if metadata else ""
        _raw_steps = metadata.get("steps") if isinstance(metadata, dict) else None
        steps = _raw_steps if isinstance(_raw_steps, list) else []
        summary = f"- {subject}"
        if scope:
            summary = f"{summary} | scope: {scope}"
        if steps:
            first_step = str(steps[0] or "").strip()
            if first_step:
                summary = f"{summary} | step: {first_step}"
        lines.append(summary)
        if len(lines) >= max(1, int(limit)):
            break
    return lines


# -----------------------------------------------------------------------------
# Default Projection Slug
# -----------------------------------------------------------------------------


def default_projection_slug(
    task_id: str,
    task: dict[str, Any],
    input_data: dict[str, Any],
) -> str:
    """生成默认的 projection slug"""
    subject = str(task.get("subject") or task.get("title") or "").strip().lower()
    if subject:
        slug = re.sub(r"[^a-z0-9_]+", "_", subject).strip("_")
        if slug:
            return slug[:48]
    explicit = str(input_data.get("project_slug") or "").strip().lower()
    if explicit:
        slug = re.sub(r"[^a-z0-9_]+", "_", explicit).strip("_")
        if slug:
            return slug[:48]
    return (
        re.sub(r"[^a-z0-9_]+", "_", str(task_id or "projection_task").strip().lower()).strip("_") or "projection_task"
    )


def compose_projection_requirement(
    task: dict[str, Any],
    input_data: dict[str, Any],
) -> str:
    """组合 projection requirement"""
    _raw_meta = task.get("metadata")
    metadata: dict[str, Any] = _raw_meta if isinstance(_raw_meta, dict) else {}
    _raw_proj = metadata.get("projection") if isinstance(metadata, dict) else None
    projection_metadata: dict[str, Any] = _raw_proj if isinstance(_raw_proj, dict) else {}
    candidates = (
        input_data.get("projection_requirement"),
        input_data.get("requirement_delta"),
        input_data.get("requirement"),
        projection_metadata.get("requirement"),
        metadata.get("projection_requirement"),
        metadata.get("requirement_delta"),
        metadata.get("goal"),
        task.get("description"),
        input_data.get("description"),
        input_data.get("input"),
        task.get("subject"),
        task.get("title"),
    )
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return "完成当前 Director 任务并生成可验证的传统代码产物。"
