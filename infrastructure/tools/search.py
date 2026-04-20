import os
import time
import fnmatch
import re
import json
from typing import List, Dict, Any, Iterable
from .utils import (
    find_repo_root, ensure_within_root, relpath, error_result, Result,
    SKIP_DIRS, MAX_RG_RESULTS_DEFAULT, MAX_RG_RESULTS_LIMIT,
    MAX_TREE_ENTRIES, MAX_FILE_BYTES, truncate_line, detect_utf8_warning
)
from .files import get_cached_lines

def iter_files(root: str, paths: List[str], glob_pat: str) -> Iterable[str]:
    for raw in paths:
        try:
            target = ensure_within_root(root, raw)
        except ValueError:
            continue
        if os.path.isfile(target):
            if glob_pat and not fnmatch.fnmatch(os.path.basename(target), glob_pat):
                continue
            yield target
        elif os.path.isdir(target):
            for dirpath, dirnames, filenames in os.walk(target):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for name in filenames:
                    if glob_pat and not fnmatch.fnmatch(name, glob_pat):
                        continue
                    yield os.path.join(dirpath, name)


def repo_tree(args: List[str], cwd: str, timeout: int) -> Result:
    path = "."
    depth = 3
    max_entries = MAX_TREE_ENTRIES
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--depth", "-d") and i + 1 < len(args):
            try:
                depth = max(0, int(args[i + 1]))
            except Exception:
                depth = 3
            i += 2
            continue
        if token in ("--max", "--max-entries") and i + 1 < len(args):
            try:
                max_entries = max(1, int(args[i + 1]))
            except Exception:
                max_entries = MAX_TREE_ENTRIES
            i += 2
            continue
        if not token.startswith("--") and path == ".":
            path = token
        i += 1

    root = find_repo_root(cwd)
    try:
        target = ensure_within_root(root, path)
    except ValueError as exc:
        return error_result("repo_tree", str(exc))

    if os.path.isfile(target):
        start = time.time()
        entry = os.path.basename(target)
        return {
            "ok": True,
            "tool": "repo_tree",
            "path": relpath(root, target),
            "depth": depth,
            "max_entries": max_entries,
            "entries": [entry],
            "truncated": False,
            "warning": "path_is_file",
            "error": None,
            "exit_code": 0,
            "stdout": entry,
            "stderr": "",
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "artifacts": [],
            "command": ["repo_tree"],
        }

    if not os.path.isdir(target):
        start = time.time()
        return {
            "ok": True,
            "tool": "repo_tree",
            "path": path,
            "depth": depth,
            "max_entries": max_entries,
            "entries": [],
            "truncated": False,
            "warning": "path_not_directory",
            "error": None,
            "exit_code": 0,
            "stdout": "(empty)",
            "stderr": "",
            "duration": time.time() - start,
            "duration_ms": int((time.time() - start) * 1000),
            "artifacts": [],
            "command": ["repo_tree"],
        }
    
    start = time.time()
    lines: List[str] = []
    truncated = False
    
    # Using walk for simpler tree construction as per original implementation
    for dirpath, dirnames, filenames in os.walk(target):
        rel_dir = os.path.relpath(dirpath, target)
        depth_level = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        if depth_level > depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        indent = "  " * depth_level
        if rel_dir != ".":
            lines.append(f"{indent}{os.path.basename(dirpath)}/")
        for name in sorted(filenames):
            lines.append(f"{indent}  {name}")
            if len(lines) >= max_entries:
                truncated = True
                break
        if truncated:
            break
            
    if truncated:
        lines.append("TRUNCATED: true")
    output = "\n".join(lines) if lines else "(empty)"
    return {
        "ok": True,
        "tool": "repo_tree",
        "path": relpath(root, target),
        "depth": depth,
        "max_entries": max_entries,
        "entries": lines,
        "truncated": truncated,
        "error": None,
        "exit_code": 0,
        "stdout": output,
        "stderr": "",
        "duration": time.time() - start,
        "duration_ms": int((time.time() - start) * 1000),
        "artifacts": [],
        "command": ["repo_tree"],
    }


def repo_rg(args: List[str], cwd: str, timeout: int) -> Result:
    _ = timeout
    if not args:
        return error_result("repo_rg", "Usage: repo_rg <pattern> [paths...] [--max N] [--glob pattern]")
    pattern = args[0]
    max_results = MAX_RG_RESULTS_DEFAULT
    glob_pat = ""
    paths: List[str] = []
    i = 1
    while i < len(args):
        token = args[i]
        if token in ("--max", "-m") and i + 1 < len(args):
            try:
                max_results = int(args[i + 1])
            except Exception:
                max_results = MAX_RG_RESULTS_DEFAULT
            i += 2
            continue
        if token in ("--glob", "-g") and i + 1 < len(args):
            glob_pat = args[i + 1]
            i += 2
            continue
        if not token.startswith("--"):
            paths.append(token)
        i += 1
    max_results = max(1, min(max_results, MAX_RG_RESULTS_LIMIT))
    if not paths:
        paths = ["."]

    root = find_repo_root(cwd)
    pattern_mode = "regex"
    regex_error = ""
    try:
        regex = re.compile(pattern)
    except Exception as exc:
        pattern_mode = "literal"
        regex_error = f"{exc}"
        try:
            regex = re.compile(re.escape(pattern))
        except Exception:
            return error_result("repo_rg", f"Invalid pattern: {exc}")

    hits: List[Dict[str, Any]] = []
    lines_out: List[str] = []
    encoding_warnings: List[str] = []
    truncated = False
    
    start = time.time()
    for file_path in iter_files(root, paths, glob_pat):
        try:
            if os.path.getsize(file_path) > MAX_FILE_BYTES:
                continue
        except Exception:
            continue
        warning = detect_utf8_warning(file_path)
        if warning:
            encoding_warnings.append(f"{relpath(root, file_path)}: {warning}")
        try:
            cached_lines = get_cached_lines(file_path)
            if cached_lines is not None:
                for line_no, line in enumerate(cached_lines, start=1):
                    match = regex.search(line)
                    if match:
                        snippet = truncate_line(line)
                        rel = relpath(root, file_path)
                        col = match.start() + 1
                        hits.append({"file": rel, "line": line_no, "col": col, "text": snippet})
                        lines_out.append(f"{rel}:{line_no}:{col}: {snippet}")
                        if len(hits) >= max_results:
                            truncated = True
                            break
            else:
                with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
                    for line_no, line in enumerate(handle, start=1):
                        match = regex.search(line)
                        if match:
                            snippet = truncate_line(line.rstrip("\n\r"))
                            rel = relpath(root, file_path)
                            col = match.start() + 1
                            hits.append({"file": rel, "line": line_no, "col": col, "text": snippet})
                            lines_out.append(f"{rel}:{line_no}:{col}: {snippet}")
                            if len(hits) >= max_results:
                                truncated = True
                                break
            if truncated:
                break
        except Exception:
            continue

    if truncated:
        lines_out.append("TRUNCATED: true")
    output = "\n".join(lines_out) if lines_out else "(no matches)"
    return {
        "ok": True,
        "tool": "repo_rg",
        "pattern": pattern,
        "pattern_mode": pattern_mode,
        "regex_error": regex_error or None,
        "paths": paths,
        "max_results": max_results,
        "hits": hits,
        "truncated": truncated,
        "encoding_warnings": encoding_warnings,
        "error": None,
        "exit_code": 0,
        "stdout": output,
        "stderr": "",
        "duration": time.time() - start,
        "duration_ms": int((time.time() - start) * 1000),
        "artifacts": [],
        "command": ["repo_rg"],
    }

def repo_symbols_index(args: List[str], cwd: str, timeout: int) -> Result:
    _ = timeout
    max_results = 500
    glob_pat = ""
    paths: List[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--max", "-m") and i + 1 < len(args):
            try:
                max_results = int(args[i + 1])
            except Exception:
                max_results = 500
            i += 2
            continue
        if token in ("--glob", "-g") and i + 1 < len(args):
            glob_pat = args[i + 1]
            i += 2
            continue
        if not token.startswith("--"):
            paths.append(token)
        i += 1
    if not paths:
        paths = ["."]
    root = find_repo_root(cwd)
    entries: List[Dict[str, Any]] = []
    truncated = False
    encoding_warnings: List[str] = []
    py_re = re.compile(r"^\\s*(def|class)\\s+([A-Za-z_][A-Za-z0-9_]*)")
    js_re = re.compile(r"^\\s*export\\s+(function|class|const|let|var|interface|type)\\s+([A-Za-z_][A-Za-z0-9_]*)")
    
    start = time.time()
    for file_path in iter_files(root, paths, glob_pat):
        if not any(file_path.endswith(ext) for ext in (".py", ".js", ".jsx", ".ts", ".tsx")):
            continue
        warning = detect_utf8_warning(file_path)
        if warning:
            encoding_warnings.append(f"{relpath(root, file_path)}: {warning}")
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
                for line_no, line in enumerate(handle, start=1):
                    match = py_re.match(line)
                    if match:
                        entries.append({
                            "symbol": match.group(2),
                            "kind": match.group(1),
                            "file": relpath(root, file_path),
                            "line": line_no,
                        })
                    match = js_re.match(line)
                    if match:
                        entries.append({
                            "symbol": match.group(2),
                            "kind": match.group(1),
                            "file": relpath(root, file_path),
                            "line": line_no,
                        })
                    if len(entries) >= max_results:
                        truncated = True
                        break
            if truncated:
                break
        except Exception:
            continue
    return {
        "ok": True,
        "tool": "repo_symbols_index",
        "paths": paths,
        "max_results": max_results,
        "entries": entries,
        "truncated": truncated,
        "encoding_warnings": encoding_warnings,
        "error": None,
        "exit_code": 0,
        "stdout": json.dumps(entries, ensure_ascii=False, indent=2),
        "stderr": "",
        "duration": time.time() - start,
        "duration_ms": int((time.time() - start) * 1000),
        "artifacts": [],
        "command": ["repo_symbols_index"],
    }
