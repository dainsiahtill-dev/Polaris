"""Tool plan parsing functions for KernelOne tool execution.

Uses KernelOne public contracts for tool name normalization and argument validation.
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.kernelone.tool_execution.constants import KV_ALLOWED_KEYS, MAX_TOOL_READ_LINES
from polaris.kernelone.tool_execution.contracts import canonicalize_tool_name, normalize_tool_args
from polaris.kernelone.tool_execution.utils import safe_int, split_list_value, split_tool_step

logger = logging.getLogger(__name__)


def _parse_key_value_token(token: str) -> tuple[str, str] | None:
    """Parse a key=value or key:value token."""
    if not token or token.startswith("--"):
        return None
    sep = None
    if ":" in token:
        sep = ":"
    elif "=" in token:
        sep = "="
    if sep is None:
        return None
    key, value = token.split(sep, 1)
    key = key.strip().lower()
    if key not in KV_ALLOWED_KEYS:
        return None
    value = value.strip()
    if not key or value == "":
        return None
    return key, value


def _normalize_tool_plan_dict_step(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a tool plan dictionary step."""
    raw_tool = item.get("tool")
    if not isinstance(raw_tool, str) or not raw_tool.strip():
        return None
    tool = canonicalize_tool_name(raw_tool.strip())
    normalized: dict[str, Any] = dict(item)
    raw_args = item.get("args")
    if isinstance(raw_args, list):
        normalized["tool"] = tool
        normalized["args"] = [str(part) for part in raw_args]
        return normalized
    args: dict[str, Any] = dict(raw_args) if isinstance(raw_args, dict) else {}
    promoted_keys = (
        "file",
        "file_path",
        "path",
        "line",
        "around",
        "around_line",
        "center_line",
        "radius",
        "start",
        "start_line",
        "end",
        "end_line",
        "depth",
        "max",
        "max_entries",
        "n",
        "lines",
        "count",
        "pattern",
        "query",
        "paths",
        "glob",
        "root",
        "languages",
        "lang",
        "max_files",
        "max_lines",
        "per_file",
        "per_file_lines",
        "include",
        "exclude",
        "stat",
        "mode",
        "directory",
        "dir",
        "limit",
        "keyword",
        "search",
        "text",
    )
    for key in promoted_keys:
        if key in item and key not in args:
            args[key] = item.get(key)
    normalized["tool"] = tool
    normalized["args"] = normalize_tool_args(tool, args)
    return normalized


def extract_tool_plan(
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract tool plan from payload."""
    if not isinstance(payload, dict):
        return []
    plan = payload.get("tool_plan")
    if not isinstance(plan, list):
        return []
    steps: list[dict[str, Any]] = []
    for item in plan:
        if isinstance(item, dict):
            normalized = _normalize_tool_plan_dict_step(item)
            if isinstance(normalized, dict) and normalized.get("tool"):
                steps.append(normalized)
            continue
        if isinstance(item, str) and item.strip():
            parsed = parse_tool_plan_item(item)
            if isinstance(parsed, dict) and parsed.get("tool"):
                steps.append(parsed)
    return steps


def extract_tool_budget(payload: dict[str, Any] | None, default_rounds: int, default_lines: int) -> tuple[int, int]:
    """Extract budget constraints from payload."""
    max_rounds = default_rounds
    max_lines = default_lines
    if not isinstance(payload, dict):
        return max_rounds, max_lines
    budget = payload.get("budget")
    if isinstance(budget, dict):
        max_rounds = safe_int(budget.get("max_rounds"), max_rounds)
        max_lines = safe_int(budget.get("max_total_lines"), max_lines)
    return max_rounds, max_lines


def parse_tool_plan_item(item: str) -> dict[str, Any] | None:
    """Parse a tool plan item from string."""
    tokens = split_tool_step(item)
    if not tokens:
        return None
    raw_tool = tokens[0].strip()
    if not raw_tool:
        return None
    if raw_tool == "cat" and len(tokens) >= 2:
        return {
            "tool": "repo_read_head",
            "args": normalize_tool_args("repo_read_head", {"file": tokens[1], "n": MAX_TOOL_READ_LINES}),
        }

    tool = canonicalize_tool_name(raw_tool)
    if not tool:
        return None

    def make_step(step_args: dict[str, Any]) -> dict[str, Any]:
        return {"tool": tool, "args": normalize_tool_args(tool, step_args)}

    # repo_tree parsing
    if tool == "repo_tree":
        return _parse_repo_tree_tokens(tokens, make_step)

    # repo_rg parsing
    if tool == "repo_rg":
        return _parse_repo_rg_tokens(tokens, make_step)

    # repo_map parsing
    if tool == "repo_map":
        return _parse_repo_map_tokens(tokens, make_step)

    # repo_read_* and repo_diff parsing
    if tool in (
        "repo_read_around",
        "repo_read_slice",
        "repo_read_head",
        "repo_read_tail",
        "repo_diff",
    ):
        return _parse_repo_read_tokens(tool, tokens, make_step)

    return make_step({})


def _parse_repo_tree_tokens(tokens: list[str], make_step: Any) -> dict[str, Any]:
    """Parse repo_tree tokens."""
    path: str | None = None
    depth: int | None = None
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        kv = _parse_key_value_token(tok)
        if kv:
            key, value = kv
            if key in ("path", "paths", "include"):
                items = split_list_value(value)
                if items:
                    path = items[0]
            elif key == "recursive":
                if value.lower() in ("1", "true", "yes", "y", "on"):
                    depth = 6
            i += 1
            continue
        if tok in ("--include", "--path", "--paths") and i + 1 < len(tokens):
            path = tokens[i + 1].strip("'\"")
            i += 2
            continue
        if tok in ("--recursive", "-r", "-R"):
            depth = 6
            i += 1
            continue
        if not tok.startswith("-") and path is None:
            path = tok.strip("'\"")
        i += 1
    args: dict[str, Any] = {"path": path or "."}
    if depth is not None and depth > 0:
        args["depth"] = depth
    return make_step(args)


def _parse_repo_rg_tokens(tokens: list[str], make_step: Any) -> dict[str, Any]:
    """Parse repo_rg tokens."""
    pattern: str | None = None
    paths: list[str] = []
    max_results: int | None = None
    glob_pat: str | None = None
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        kv = _parse_key_value_token(tok)
        if kv:
            key, value = kv
            if key in ("pattern", "p", "query"):
                pattern = value.strip("'\"")
            elif key in ("paths", "path", "file"):
                for part in split_list_value(value):
                    paths.append(part)
            elif key in ("max", "max_results"):
                try:
                    max_results = int(value)
                except ValueError:
                    logger.debug("Failed to parse max_results: %s", value)
            elif key in ("glob", "g"):
                glob_pat = value.strip("'\"")
            i += 1
            continue
        if tok in ("-p", "--pattern") and i + 1 < len(tokens):
            pattern = tokens[i + 1]
            i += 2
            continue
        if tok in ("--max", "-m") and i + 1 < len(tokens):
            try:
                max_results = int(tokens[i + 1])
            except ValueError:
                logger.debug("Failed to parse max_results: %s", tokens[i + 1])
            i += 2
            continue
        if tok in ("--glob", "-g") and i + 1 < len(tokens):
            glob_pat = tokens[i + 1]
            i += 2
            continue
        if tok in ("--path", "--paths") and i + 1 < len(tokens):
            paths.extend(split_list_value(tokens[i + 1]))
            i += 2
            continue
        if tok.startswith("--"):
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                i += 2
            else:
                i += 1
            continue
        if pattern is None:
            pattern = tok.strip("'\"")
        else:
            paths.append(tok.strip("'\""))
        i += 1
    if not pattern:
        return make_step({})
    args: dict[str, Any] = {"pattern": pattern}
    if paths:
        args["paths"] = paths
    if max_results is not None:
        args["max_results"] = max_results
    if glob_pat:
        args["glob"] = glob_pat
    return make_step(args)


def _parse_repo_map_tokens(tokens: list[str], make_step: Any) -> dict[str, Any]:
    """Parse repo_map tokens."""
    root_path: str | None = None
    languages: str | None = None
    max_files: int | None = None
    max_lines: int | None = None
    per_file_lines: int | None = None
    include: str | None = None
    exclude: str | None = None
    positional: list[str] = []
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        kv = _parse_key_value_token(tok)
        if kv:
            key, value = kv
            if key in ("path", "paths"):
                root_path = value.strip("'\"")
            elif key in ("languages", "lang"):
                languages = value.strip("'\"")
            elif key in ("max", "max_files"):
                max_files = safe_int(value, -1)
            elif key == "max_lines":
                max_lines = safe_int(value, -1)
            elif key in ("per_file", "per_file_lines"):
                per_file_lines = safe_int(value, -1)
            elif key == "include":
                include = value.strip("'\"")
            elif key == "exclude":
                exclude = value.strip("'\"")
            i += 1
            continue
        if tok in ("--root", "--path") and i + 1 < len(tokens):
            root_path = tokens[i + 1].strip("'\"")
            i += 2
            continue
        if tok in ("--languages", "--lang", "-l") and i + 1 < len(tokens):
            languages = tokens[i + 1].strip("'\"")
            i += 2
            continue
        if tok in ("--max", "--max-files") and i + 1 < len(tokens):
            max_files = safe_int(tokens[i + 1], -1)
            i += 2
            continue
        if tok == "--max-lines" and i + 1 < len(tokens):
            max_lines = safe_int(tokens[i + 1], -1)
            i += 2
            continue
        if tok in ("--per-file", "--per-file-lines") and i + 1 < len(tokens):
            per_file_lines = safe_int(tokens[i + 1], -1)
            i += 2
            continue
        if tok == "--include" and i + 1 < len(tokens):
            include = tokens[i + 1].strip("'\"")
            i += 2
            continue
        if tok == "--exclude" and i + 1 < len(tokens):
            exclude = tokens[i + 1].strip("'\"")
            i += 2
            continue
        if not tok.startswith("-"):
            positional.append(tok)
        i += 1
    if root_path is None and positional:
        root_path = positional[0].strip("'\"")
    args: dict[str, Any] = {"root": root_path or "."}
    if languages:
        args["languages"] = languages
    if max_files is not None and max_files > 0:
        args["max_files"] = max_files
    if max_lines is not None and max_lines > 0:
        args["max_lines"] = max_lines
    if per_file_lines is not None and per_file_lines > 0:
        args["per_file_lines"] = per_file_lines
    if include:
        args["include"] = include
    if exclude:
        args["exclude"] = exclude
    return make_step(args)


def _parse_repo_read_tokens(tool: str, tokens: list[str], make_step: Any) -> dict[str, Any]:
    """Parse repo_read_* and repo_diff tokens."""
    file_arg: str | None = None
    line_no: int | None = None
    radius: int | None = None
    start: int | None = None
    end: int | None = None
    count: int | None = None
    stat = False
    positional: list[str] = []
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        kv = _parse_key_value_token(tok)
        if kv:
            key, value = kv
            if key in ("file", "path", "file_path"):
                file_arg = value.strip("'\"")
            elif key in ("line", "around", "around_line", "center_line"):
                line_no = safe_int(value, -1)
            elif key in ("radius",):
                radius = safe_int(value, -1)
            elif key in ("start", "start_line"):
                start = safe_int(value, -1)
            elif key in ("end", "end_line"):
                end = safe_int(value, -1)
            elif key in ("n", "lines", "count"):
                count = safe_int(value, -1)
            i += 1
            continue
        if tok in ("--file", "-f", "--path") and i + 1 < len(tokens):
            file_arg = tokens[i + 1].strip("'\"")
            i += 2
            continue
        if tok in ("--line", "--around", "--around_line") and i + 1 < len(tokens):
            line_no = safe_int(tokens[i + 1], -1)
            i += 2
            continue
        if tok == "--radius" and i + 1 < len(tokens):
            radius = safe_int(tokens[i + 1], -1)
            i += 2
            continue
        if tok in ("--start", "--start_line") and i + 1 < len(tokens):
            start = safe_int(tokens[i + 1], -1)
            i += 2
            continue
        if tok in ("--end", "--end_line") and i + 1 < len(tokens):
            end = safe_int(tokens[i + 1], -1)
            i += 2
            continue
        if tok in ("--n", "--lines") and i + 1 < len(tokens):
            count = safe_int(tokens[i + 1], -1)
            i += 2
            continue
        if tok == "--stat":
            stat = True
            i += 1
            continue
        if tok.startswith("--"):
            i += 1
            continue
        positional.append(tok)
        i += 1

    if tool == "repo_diff":
        args: dict[str, Any] = {"stat": stat}
        return make_step(args)

    if not file_arg and positional:
        file_arg = positional[0].strip("'\"")
    if tool == "repo_read_around":
        if (line_no is None or line_no <= 0) and len(positional) >= 2:
            line_no = safe_int(positional[1], -1)
        if (radius is None or radius <= 0) and len(positional) >= 3:
            radius = safe_int(positional[2], 80)
        step_args: dict[str, Any] = {"file": file_arg or "", "line": line_no}
        if radius is not None and radius > 0:
            step_args["radius"] = radius
        return make_step(step_args)
    if tool == "repo_read_slice":
        if (start is None or start <= 0) and len(positional) >= 2:
            start = safe_int(positional[1], -1)
        if (end is None or end <= 0) and len(positional) >= 3:
            end = safe_int(positional[2], -1)
        return make_step({"file": file_arg or "", "start": start, "end": end})
    if tool in ("repo_read_head", "repo_read_tail"):
        if (count is None or count <= 0) and len(positional) >= 2:
            count = safe_int(positional[1], -1)
        read_args: dict[str, Any] = {"file": file_arg or ""}
        if count is not None and count > 0:
            read_args["n"] = count
        return make_step(read_args)

    return make_step({})
