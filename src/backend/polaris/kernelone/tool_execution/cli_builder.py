"""CLI argument builder for KernelOne tool execution."""

from __future__ import annotations

from typing import Any

from polaris.kernelone.tool_execution.utils import as_list, safe_int


def build_tool_cli_args(tool: str, args: Any) -> list[str]:
    """Build CLI arguments for a tool.

    Args:
        tool: Tool name.
        args: Arguments dict or list.

    Returns:
        List of CLI argument tokens.
    """
    if isinstance(args, list):
        return [str(x) for x in args]
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return []

    tool = tool or ""

    if tool == "repo_tree":
        return _build_repo_tree_args(args)
    if tool == "repo_map":
        return _build_repo_map_args(args)
    if tool == "repo_rg":
        return _build_repo_rg_args(args)
    if tool == "repo_read_around":
        return _build_repo_read_around_args(args)
    if tool == "repo_read_slice":
        return _build_repo_read_slice_args(args)
    if tool in ("repo_read_head", "repo_read_tail"):
        return _build_repo_read_head_tail_args(tool, args)
    if tool == "repo_diff":
        return _build_repo_diff_args(args)
    return []


def _build_repo_tree_args(args: dict[str, Any]) -> list[str]:
    """Build CLI args for repo_tree."""
    path = args.get("path") or args.get("root") or "."
    depth = args.get("depth")
    max_entries = args.get("max_entries") or args.get("max")
    tokens = [str(path)]
    if depth is not None:
        tokens += ["--depth", str(depth)]
    if max_entries is not None:
        tokens += ["--max", str(max_entries)]
    return tokens


def _build_repo_map_args(args: dict[str, Any]) -> list[str]:
    """Build CLI args for repo_map."""
    root_path = args.get("root") or args.get("path") or "."
    languages = args.get("languages") or args.get("lang")
    max_files = args.get("max_files") or args.get("max")
    max_lines = args.get("max_lines")
    per_file_lines = args.get("per_file_lines") or args.get("per_file")
    include = args.get("include")
    exclude = args.get("exclude")
    tokens = [str(root_path)]
    if languages:
        tokens += ["--languages", str(languages)]
    if max_files is not None:
        tokens += ["--max-files", str(max_files)]
    if max_lines is not None:
        tokens += ["--max-lines", str(max_lines)]
    if per_file_lines is not None:
        tokens += ["--per-file-lines", str(per_file_lines)]
    if include:
        tokens += ["--include", str(include)]
    if exclude:
        tokens += ["--exclude", str(exclude)]
    return tokens


def _build_repo_rg_args(args: dict[str, Any]) -> list[str]:
    """Build CLI args for repo_rg."""
    pattern = args.get("pattern") or args.get("query")
    if not pattern:
        return []
    paths = as_list(args.get("paths") or args.get("path"))
    max_results = args.get("max_results") or args.get("max")
    glob_pat = args.get("glob")
    tokens = [str(pattern)]
    tokens += [str(p) for p in paths]
    if max_results is not None:
        tokens += ["--max", str(max_results)]
    if glob_pat:
        tokens += ["--glob", str(glob_pat)]
    return tokens


def _build_repo_read_around_args(args: dict[str, Any]) -> list[str]:
    """Build CLI args for repo_read_around."""
    file_arg = args.get("file") or args.get("path") or args.get("file_path")
    line_no = args.get("line") or args.get("around_line") or args.get("around") or args.get("center_line")
    radius = args.get("radius")
    start = args.get("start") or args.get("start_line")
    end = args.get("end") or args.get("end_line")

    line_no_int = safe_int(line_no, -1)
    start_int = safe_int(start, -1)
    end_int = safe_int(end, -1)

    if line_no_int <= 0:
        if start_int > 0 and end_int >= start_int:
            line_no_int = start_int + ((end_int - start_int) // 2)
            if radius is None:
                radius = max(1, (end_int - start_int) // 2)
        elif start_int > 0:
            line_no_int = start_int

    if not file_arg or line_no_int <= 0:
        return []
    tokens = ["--file", str(file_arg), "--line", str(line_no_int)]
    radius_int = safe_int(radius, -1)
    if radius_int > 0:
        tokens += ["--radius", str(radius_int)]
    return tokens


def _build_repo_read_slice_args(args: dict[str, Any]) -> list[str]:
    """Build CLI args for repo_read_slice."""
    file_arg = args.get("file") or args.get("path") or args.get("file_path")
    start = args.get("start") or args.get("start_line")
    end = args.get("end") or args.get("end_line")
    if not file_arg or start is None or end is None:
        return []
    return ["--file", str(file_arg), "--start", str(start), "--end", str(end)]


def _build_repo_read_head_tail_args(tool: str, args: dict[str, Any]) -> list[str]:
    """Build CLI args for repo_read_head and repo_read_tail."""
    file_arg = args.get("file") or args.get("path") or args.get("file_path")
    count = args.get("n") or args.get("lines") or args.get("count")
    if not file_arg:
        return []
    tokens = ["--file", str(file_arg)]
    if count is not None:
        tokens += ["--n", str(count)]
    return tokens


def _build_repo_diff_args(args: dict[str, Any]) -> list[str]:
    """Build CLI args for repo_diff."""
    tokens: list[str] = []
    if args.get("stat") or args.get("mode") in ("stat", "--stat"):
        tokens.append("--stat")
    return tokens
