import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from .utils import Result, error_result, ensure_within_root, find_repo_root, normalize_args, relpath


def _load_repo_map() -> Optional[Any]:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    core_dir = os.path.join(root, "backend", "core", "polaris_loop")
    if core_dir not in sys.path:
        sys.path.insert(0, core_dir)
    try:
        from repo_map import build_repo_map  # type: ignore
    except Exception:
        return None
    return build_repo_map


def repo_map(args: List[str], cwd: str, timeout: int) -> Result:
    _ = timeout
    args = normalize_args(args)
    root_arg = "."
    languages: List[str] = []
    max_files = 200
    max_lines = 200
    per_file_lines = 12
    include_glob = ""
    exclude_glob = ""
    output_format = "text"
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--root", "-r") and i + 1 < len(args):
            root_arg = args[i + 1]
            i += 2
            continue
        if token in ("--languages", "--lang", "-l") and i + 1 < len(args):
            languages = [s.strip() for s in args[i + 1].split(",") if s.strip()]
            i += 2
            continue
        if token in ("--max-files", "--max", "-m") and i + 1 < len(args):
            try:
                max_files = int(args[i + 1])
            except Exception:
                max_files = 200
            i += 2
            continue
        if token in ("--max-lines",) and i + 1 < len(args):
            try:
                max_lines = int(args[i + 1])
            except Exception:
                max_lines = 200
            i += 2
            continue
        if token in ("--per-file-lines", "--per-file") and i + 1 < len(args):
            try:
                per_file_lines = int(args[i + 1])
            except Exception:
                per_file_lines = 12
            i += 2
            continue
        if token in ("--include",) and i + 1 < len(args):
            include_glob = args[i + 1]
            i += 2
            continue
        if token in ("--exclude",) and i + 1 < len(args):
            exclude_glob = args[i + 1]
            i += 2
            continue
        if token in ("--format", "-f") and i + 1 < len(args):
            output_format = args[i + 1].strip().lower()
            i += 2
            continue
        if not token.startswith("--") and root_arg == ".":
            root_arg = token
        i += 1

    build_repo_map = _load_repo_map()
    if build_repo_map is None:
        return error_result("repo_map", "repo_map backend unavailable")

    repo_root = find_repo_root(cwd)
    try:
        full_root = ensure_within_root(repo_root, root_arg)
    except ValueError as exc:
        return error_result("repo_map", str(exc))

    start = time.time()
    result = build_repo_map(
        full_root,
        languages=languages if languages else None,
        max_files=max_files,
        max_lines=max_lines,
        per_file_lines=per_file_lines,
        include_glob=include_glob or None,
        exclude_glob=exclude_glob or None,
    )
    output_payload: Dict[str, Any] = {
        "root": relpath(repo_root, full_root),
        "languages": result.get("languages", []),
        "stats": result.get("stats", {}),
        "truncated": result.get("truncated", False),
        "lines": result.get("lines", []),
    }
    if output_format == "json":
        stdout = json.dumps(output_payload, ensure_ascii=False, indent=2)
    else:
        stdout = result.get("text") or ""
    return {
        "ok": True,
        "tool": "repo_map",
        "root": output_payload["root"],
        "languages": output_payload["languages"],
        "stats": output_payload["stats"],
        "truncated": output_payload["truncated"],
        "lines": output_payload["lines"],
        "exit_code": 0,
        "stdout": stdout,
        "stderr": "",
        "duration": time.time() - start,
        "duration_ms": int((time.time() - start) * 1000),
        "artifacts": [],
        "command": ["repo_map"],
    }
