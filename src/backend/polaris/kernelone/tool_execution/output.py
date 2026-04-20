"""Output processing and analysis functions for KernelOne tool execution."""

from __future__ import annotations

import os
import re
from typing import Any

from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.storage.io_paths import resolve_run_dir
from polaris.kernelone.tool_execution.constants import MAX_EVENT_CONTENT_LINES
from polaris.kernelone.tool_execution.utils import safe_int, sanitize_tool_name


def persist_tool_raw_output(
    state: Any,
    tool: str,
    *,
    stdout_text: str = "",
    stderr_text: str = "",
    error_text: str = "",
) -> dict[str, str]:
    """Persist raw tool output to disk."""
    run_id = str(getattr(state, "current_run_id", "") or "").strip()
    if not run_id:
        return {}
    run_dir = resolve_run_dir(
        str(getattr(state, "workspace_full", "") or ""),
        str(getattr(state, "cache_root_full", "") or ""),
        run_id,
    )
    if not run_dir:
        return {}

    output_dir = os.path.join(run_dir, "tool_output")
    os.makedirs(output_dir, exist_ok=True)

    seq = safe_int(getattr(state, "_tool_output_seq", 0), 0) + 1
    state._tool_output_seq = seq
    prefix = f"{seq:05d}_{sanitize_tool_name(tool)}"

    paths: dict[str, str] = {}
    if stdout_text:
        stdout_path = os.path.join(output_dir, f"{prefix}.stdout.log")
        write_text_atomic(stdout_path, stdout_text)
        paths["tool_stdout_path"] = stdout_path
    if stderr_text:
        stderr_path = os.path.join(output_dir, f"{prefix}.stderr.log")
        write_text_atomic(stderr_path, stderr_text)
        paths["tool_stderr_path"] = stderr_path
    if error_text:
        error_path = os.path.join(output_dir, f"{prefix}.error.log")
        write_text_atomic(error_path, error_text)
        paths["tool_error_path"] = error_path
    return paths


def build_refs(state: Any, phase: str) -> dict[str, Any]:
    """Build reference dictionary for events."""
    return {
        "task_id": getattr(state, "current_task_id", "") or None,
        "task_fingerprint": getattr(state, "current_task_fingerprint", "") or None,
        "run_id": getattr(state, "current_run_id", "") or None,
        "pm_iteration": getattr(state, "current_pm_iteration", None),
        "director_iteration": getattr(state, "current_director_iteration", None),
        "phase": phase,
    }


def compact_tool_output(tool: str, output: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compact tool output for event emission."""
    truncation: dict[str, Any] = {"truncated": False}
    if not isinstance(output, dict):
        return {"raw": str(output)}, truncation
    compact = dict(output)
    if "content" in compact and isinstance(compact["content"], list):
        content = compact["content"]
        if len(content) > MAX_EVENT_CONTENT_LINES:
            compact["content"] = content[:MAX_EVENT_CONTENT_LINES]
            truncation = {
                "truncated": True,
                "reason": "content_lines",
                "original_lines": len(content),
                "kept_lines": MAX_EVENT_CONTENT_LINES,
            }
    if compact.get("truncated") and not truncation.get("truncated"):
        truncation = {"truncated": True, "reason": "tool_truncated"}
    return compact, truncation


def score_hit(text: str, file_path: str, patterns: list[str]) -> int:
    """Score a search hit based on relevance."""
    score = 0
    lowered = text.lower()
    if re.search(r"\b(def|class|function)\b", lowered):
        score += 5
    if re.search(r"\b(export\s+function|export\s+class)\b", lowered):
        score += 5
    for pat in patterns:
        if not pat:
            continue
        try:
            if re.search(rf"\b{re.escape(pat)}\b", text):
                score += 3
        except re.error:
            continue
    path_lower = file_path.replace("\\", "/").lower()
    if "/loops/" in path_lower or "/modules/" in path_lower:
        score += 2
    if "/test/" in path_lower or "/tests/" in path_lower or "/docs/" in path_lower:
        score -= 3
    if path_lower.endswith(".md"):
        score -= 3
    return score


def annotate_rg_output(output: dict[str, Any]) -> None:
    """Annotate rg output with ranked hits in-place."""
    hits = output.get("hits")
    if not isinstance(hits, list) or not hits:
        return
    raw_pattern = str(output.get("pattern") or "")
    patterns = [p.strip() for p in raw_pattern.split("|")] if raw_pattern else []
    scored: list[dict[str, Any]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        text = str(hit.get("text") or "")
        file_path = str(hit.get("file") or "")
        score = score_hit(text, file_path, patterns)
        hit_copy = dict(hit)
        hit_copy["score"] = score
        scored.append(hit_copy)
    scored.sort(
        key=lambda item: (
            item.get("score", 0),
            item.get("file", ""),
            item.get("line", 0),
        ),
        reverse=True,
    )
    output["ranked_hits"] = scored[:3]
    if scored:
        output["best_hit"] = scored[0]


def analyze_slice_content(content: list[dict[str, Any]]) -> dict[str, bool]:
    """Analyze slice content for code structure."""
    has_def = False
    has_end = False
    for item in content:
        if not isinstance(item, dict):
            continue
        line = str(item.get("t") or "")
        lowered = line.lower()
        if re.search(r"\b(def|class|function)\b", lowered) or "export function" in lowered or "export class" in lowered:
            has_def = True
        if re.search(r"^\s*}\s*$", line) or re.search(r"\breturn\b", lowered):
            has_end = True
    return {"has_def": has_def, "has_end": has_end}


def suggest_radius(truncated: bool, analysis: dict[str, bool], current_radius: int) -> int | None:
    """Suggest new radius based on content analysis."""
    if not truncated:
        return None
    if not analysis.get("has_def"):
        return max(current_radius, 140)
    if analysis.get("has_def") and not analysis.get("has_end"):
        return max(current_radius, 120)
    return None


def count_tool_output_lines(output: dict[str, Any]) -> int:
    """Count lines in tool output."""
    if not isinstance(output, dict):
        return 0
    if output.get("cache_hit") is True:
        return 0
    tool = output.get("tool")
    if tool in (
        "repo_read_around",
        "repo_read_slice",
        "repo_read_head",
        "repo_read_tail",
    ):
        content = output.get("content")
        if isinstance(content, list):
            return len(content)
    return 0
