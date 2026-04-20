import os
import time
from typing import Dict, Any, List, Optional, Tuple, Deque, OrderedDict
from collections import deque, OrderedDict as OrderedDictType
from .utils import (
    MAX_FILE_BYTES, MAX_READ_LINES, MAX_READ_BYTES,
    truncate_line, format_slice, error_result,
    find_repo_root, ensure_within_root, relpath,
    read_text_file_utf8, detect_utf8_warning
)

# Cache configuration with memory leak protection
_MAX_CACHE_ENTRIES = 1000  # Maximum number of files to cache
_CACHE_TTL_SECONDS = 300   # Time-to-live for cache entries (5 minutes)

class FileCache:
    """
    LRU cache for file contents with TTL expiration.
    Prevents unbounded memory growth (fixes memory leak issue).
    """

    def __init__(self, max_entries: int = _MAX_CACHE_ENTRIES, ttl_seconds: float = _CACHE_TTL_SECONDS):
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        # OrderedDict maintains insertion order for LRU eviction
        self._cache: OrderedDictType[str, Dict[str, Any]] = OrderedDict()
        self._access_count = 0
        self._hit_count = 0

    def get(self, full_path: str) -> Optional[Dict[str, Any]]:
        """Get cached entry with LRU update."""
        self._access_count += 1
        if full_path not in self._cache:
            return None

        entry = self._cache[full_path]

        # Check TTL expiration
        if time.time() - entry.get("cached_at", 0) > self._ttl:
            del self._cache[full_path]
            return None

        # Move to end (most recently used)
        self._cache.move_to_end(full_path)
        self._hit_count += 1
        return entry

    def set(self, full_path: str, data: Dict[str, Any]) -> None:
        """Set cache entry with LRU eviction."""
        # Evict oldest entries if at capacity
        while len(self._cache) >= self._max_entries:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]

        data["cached_at"] = time.time()
        self._cache[full_path] = data
        self._cache.move_to_end(full_path)

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "entries": len(self._cache),
            "max_entries": self._max_entries,
            "ttl_seconds": self._ttl,
            "access_count": self._access_count,
            "hit_count": self._hit_count,
            "hit_rate": self._hit_count / max(1, self._access_count),
        }

# Global cache instance with bounded size
_FILE_CACHE = FileCache(max_entries=_MAX_CACHE_ENTRIES, ttl_seconds=_CACHE_TTL_SECONDS)

def get_cached_lines(full_path: str) -> Optional[List[str]]:
    try:
        stat = os.stat(full_path)
    except Exception:
        return None
    if stat.st_size > MAX_FILE_BYTES:
        return None

    # Check cache with TTL and LRU
    cached = _FILE_CACHE.get(full_path)
    if cached:
        if cached.get("mtime") == stat.st_mtime and cached.get("size") == stat.st_size:
            return cached.get("lines")

    try:
        text, warning = read_text_file_utf8(full_path)
        lines = text.splitlines()
    except Exception:
        return None

    # Store in cache with bounded size
    _FILE_CACHE.set(full_path, {
        "mtime": stat.st_mtime,
        "size": stat.st_size,
        "lines": lines,
        "encoding_warning": warning,
    })
    return lines


def get_cached_warning(full_path: str) -> Optional[str]:
    cached = _FILE_CACHE.get(full_path)
    if cached:
        return cached.get("encoding_warning")
    return None


def get_cache_stats() -> Dict[str, Any]:
    """Get file cache statistics for monitoring."""
    return _FILE_CACHE.get_stats()


def clear_file_cache() -> None:
    """Clear the file cache. Useful for testing or memory pressure."""
    _FILE_CACHE.clear()


def lines_to_content(lines: List[Tuple[int, str]]) -> List[Dict[str, Any]]:
    return [{"n": line_no, "t": text} for line_no, text in lines]

def read_lines_range(full_path: str, start: int, end: int) -> Tuple[List[Tuple[int, str]], bool, Optional[str]]:
    lines: List[Tuple[int, str]] = []
    truncated = False
    byte_budget = MAX_READ_BYTES
    warning = detect_utf8_warning(full_path)
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle, start=1):
                if line_no < start:
                    continue
                if line_no > end:
                    break
                text = truncate_line(line.rstrip("\n\r"))
                if byte_budget <= 0 or len(lines) >= MAX_READ_LINES:
                    truncated = True
                    break
                byte_budget -= len(text.encode("utf-8", errors="ignore"))
                if byte_budget < 0:
                    truncated = True
                    break
                lines.append((line_no, text))
    except Exception:
        raise
    return lines, truncated, warning


def read_lines_range_cached(full_path: str, start: int, end: int) -> Tuple[List[Tuple[int, str]], bool, Optional[str]]:
    cached_lines = get_cached_lines(full_path)
    warning = get_cached_warning(full_path)
    if cached_lines is None:
        return read_lines_range(full_path, start, end)
    warning = get_cached_warning(full_path)
    lines: List[Tuple[int, str]] = []
    truncated = False
    byte_budget = MAX_READ_BYTES
    total_lines = len(cached_lines)
    last = min(end, total_lines)
    for idx in range(start - 1, last):
        text = truncate_line(cached_lines[idx])
        if byte_budget <= 0 or len(lines) >= MAX_READ_LINES:
            truncated = True
            break
        byte_budget -= len(text.encode("utf-8", errors="ignore"))
        if byte_budget < 0:
            truncated = True
            break
        lines.append((idx + 1, text))
    if end > last:
        truncated = truncated
    return lines, truncated, warning


def repo_read_slice(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    _ = timeout
    file_arg = ""
    start = None
    end = None
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if token in ("--start", "-s") and i + 1 < len(args):
            try:
                start = int(args[i + 1])
            except Exception:
                start = None
            i += 2
            continue
        if token in ("--end", "-e") and i + 1 < len(args):
            try:
                end = int(args[i + 1])
            except Exception:
                end = None
            i += 2
            continue
        if not file_arg:
            file_arg = token
        elif start is None:
            try:
                start = int(token)
            except Exception:
                start = None
        elif end is None:
            try:
                end = int(token)
            except Exception:
                end = None
        i += 1
    if not file_arg or start is None or end is None:
        return error_result("repo_read_slice", "Usage: repo_read_slice <file> <start> <end> (or --file/--start/--end)")
    if start < 1 or end < start:
        return error_result("repo_read_slice", "invalid line range")

    root = find_repo_root(cwd)
    try:
        full_path = ensure_within_root(root, file_arg)
    except ValueError as exc:
        return error_result("repo_read_slice", str(exc))
    if not os.path.isfile(full_path):
        return error_result("repo_read_slice", f"Not a file: {file_arg}")

    try:
        lines, truncated, warning = read_lines_range_cached(full_path, start, end)
    except Exception as exc:
        return error_result("repo_read_slice", str(exc), exit_code=1)

    rel = relpath(root, full_path)
    actual_end = lines[-1][0] if lines else start - 1
    output = format_slice(rel, start, max(end, actual_end), lines, truncated)
    return {
        "ok": True,
        "tool": "repo_read_slice",
        "file": rel,
        "start_line": start,
        "end_line": actual_end,
        "max_lines": MAX_READ_LINES,
        "truncated": truncated,
        "content": lines_to_content(lines),
        "encoding_warning": warning,
        "warnings": [warning] if warning else [],
        "error": None,
        "exit_code": 0,
        "stdout": output,
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "artifacts": [],
        "command": ["repo_read_slice"],
    }


def repo_read_around(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    file_arg = ""
    line_no = None
    radius = 80
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if token in ("--line", "-l") and i + 1 < len(args):
            try:
                line_no = int(args[i + 1])
            except Exception:
                line_no = None
            i += 2
            continue
        if token in ("--radius", "-r") and i + 1 < len(args):
            try:
                radius = int(args[i + 1])
            except Exception:
                radius = 80
            i += 2
            continue
        if not file_arg:
            file_arg = token
        elif line_no is None:
            try:
                line_no = int(token)
            except Exception:
                line_no = None
        elif i + 1 == len(args):
            try:
                radius = int(token)
            except Exception:
                radius = radius
        i += 1
    if not file_arg or line_no is None:
        return error_result("repo_read_around", "Usage: repo_read_around <file> <line> [radius] (or --file/--line/--radius)")
    start = max(1, line_no - radius)
    end = line_no + radius
    # Reuse repo_read_slice logic explicitly or call it? Calling it to avoid dup
    # But need to construct args. A bit messy. Better to refactor repo_read_slice core to be reusable.
    # Since I can't easily refactor the core out without more edits, I will just call repo_read_slice.
    result = repo_read_slice([file_arg, str(start), str(end)], cwd, timeout)
    result["tool"] = "repo_read_around"
    result["around_line"] = line_no
    result["radius"] = radius
    result["command"] = ["repo_read_around"]
    return result


def repo_read_head(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    file_arg = ""
    n = 60
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if token in ("--n", "-n") and i + 1 < len(args):
            try:
                n = int(args[i + 1])
            except Exception:
                n = 60
            i += 2
            continue
        if not file_arg:
            file_arg = token
        elif i + 1 == len(args):
            try:
                n = int(token)
            except Exception:
                n = n
        i += 1
    if not file_arg:
        return error_result("repo_read_head", "Usage: repo_read_head <file> [n] (or --file/--n)")
    requested = max(1, n)
    result = repo_read_slice([file_arg, "1", str(requested)], cwd, timeout)
    result["tool"] = "repo_read_head"
    result["head_lines"] = requested
    result["command"] = ["repo_read_head"]
    return result


def repo_read_tail(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    _ = timeout
    file_arg = ""
    n = 60
    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if token in ("--n", "-n") and i + 1 < len(args):
            try:
                n = int(args[i + 1])
            except Exception:
                n = 60
            i += 2
            continue
        if not file_arg:
            file_arg = token
        elif i + 1 == len(args):
            try:
                n = int(token)
            except Exception:
                n = n
        i += 1
    if not file_arg:
        return error_result("repo_read_tail", "Usage: repo_read_tail <file> [n] (or --file/--n)")
    root = find_repo_root(cwd)
    try:
        full_path = ensure_within_root(root, file_arg)
    except ValueError as exc:
        return error_result("repo_read_tail", str(exc))
    if not os.path.isfile(full_path):
        return error_result("repo_read_tail", f"Not a file: {file_arg}")

    requested = max(1, n)
    truncated = False
    if requested > MAX_READ_LINES:
        requested = MAX_READ_LINES
        truncated = True

    cached_lines = get_cached_lines(full_path)
    warning = get_cached_warning(full_path)
    total = 0
    if cached_lines is not None:
        total = len(cached_lines)
        lines_list = [
            (idx + 1, truncate_line(cached_lines[idx]))
            for idx in range(max(0, total - requested), total)
        ]
    else:
        warning = detect_utf8_warning(full_path)
        q: Deque[Tuple[int, str]] = deque(maxlen=requested)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as handle:
                for total, line in enumerate(handle, start=1):
                    q.append((total, truncate_line(line.rstrip("\n\r"))))
        except Exception as exc:
            return error_result("repo_read_tail", str(exc), exit_code=1)
        lines_list = list(q)

    rel = relpath(root, full_path)
    if total == 0 or not lines_list:
        return {
            "ok": True,
            "tool": "repo_read_tail",
            "file": rel,
            "start_line": 0,
            "end_line": 0,
            "max_lines": MAX_READ_LINES,
            "tail_lines": requested,
            "truncated": truncated,
            "content": [],
            "encoding_warning": warning,
            "warnings": [warning] if warning else [],
            "error": None,
            "exit_code": 0,
            "stdout": "(empty)",
            "stderr": "",
            "duration": 0.0,
            "duration_ms": 0,
            "artifacts": [],
            "command": ["repo_read_tail"],
        }

    byte_budget = MAX_READ_BYTES
    kept_rev: List[Tuple[int, str]] = []
    for line_no, text in reversed(lines_list):
        line_bytes = len(text.encode("utf-8", errors="ignore"))
        if byte_budget - line_bytes < 0:
            truncated = True
            break
        byte_budget -= line_bytes
        kept_rev.append((line_no, text))
    kept = list(reversed(kept_rev))
    start = kept[0][0] if kept else lines_list[0][0]
    end = kept[-1][0] if kept else lines_list[-1][0]
    output = format_slice(rel, start, end, kept, truncated)
    return {
        "ok": True,
        "tool": "repo_read_tail",
        "file": rel,
        "start_line": start,
        "end_line": end,
        "max_lines": MAX_READ_LINES,
        "tail_lines": requested,
        "truncated": truncated,
        "content": lines_to_content(kept),
        "encoding_warning": warning,
        "warnings": [warning] if warning else [],
        "error": None,
        "exit_code": 0,
        "stdout": output,
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "artifacts": [],
        "command": ["repo_read_tail"],
    }


def repo_write(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Write content to a file.

    Usage: repo_write --file <path> --content <text>
           repo_write <path> <content>
    """
    _ = timeout
    file_arg = ""
    content = ""
    append = False

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if token in ("--content", "-c") and i + 1 < len(args):
            content = args[i + 1]
            i += 2
            continue
        if token in ("--append", "-a"):
            append = True
            i += 1
            continue
        if not file_arg:
            file_arg = token
        elif not content:
            content = token
        i += 1

    if not file_arg or not content:
        return error_result(
            "repo_write",
            "Usage: repo_write --file <path> --content <text> (or --file/-f and positional)"
        )

    root = find_repo_root(cwd)
    try:
        full_path = ensure_within_root(root, file_arg)
    except ValueError as exc:
        return error_result("repo_write", str(exc))

    # Create parent directories if they don't exist
    parent_dir = os.path.dirname(full_path)
    if parent_dir:
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except Exception as exc:
            return error_result("repo_write", f"Failed to create directory: {exc}", exit_code=1)

    # Write the file
    mode = "a" if append else "w"
    try:
        with open(full_path, mode, encoding="utf-8") as handle:
            handle.write(content)
    except Exception as exc:
        return error_result("repo_write", str(exc), exit_code=1)

    rel = relpath(root, full_path)
    action = "appended to" if append else "written to"
    return {
        "ok": True,
        "tool": "repo_write",
        "file": rel,
        "action": action,
        "bytes_written": len(content.encode("utf-8")),
        "error": None,
        "exit_code": 0,
        "stdout": f"OK: {action} {rel} ({len(content)} chars)",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["repo_write"],
    }


import fnmatch
import shutil


def file_delete(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Delete a file.

    Usage: file_delete --file <path>
           file_delete <path>
    """
    _ = timeout
    file_arg = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if not file_arg:
            file_arg = token
        i += 1

    if not file_arg:
        return error_result("file_delete", "Usage: file_delete --file <path>")

    root = find_repo_root(cwd)
    try:
        full_path = ensure_within_root(root, file_arg)
    except ValueError as exc:
        return error_result("file_delete", str(exc))

    if not os.path.isfile(full_path):
        return error_result("file_delete", f"Not a file: {file_arg}")

    try:
        os.unlink(full_path)
    except Exception as exc:
        return error_result("file_delete", str(exc), exit_code=1)

    rel = relpath(root, full_path)
    return {
        "ok": True,
        "tool": "file_delete",
        "file": rel,
        "error": None,
        "exit_code": 0,
        "stdout": f"OK: deleted {rel}",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["file_delete"],
    }


def file_copy(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Copy a file.

    Usage: file_copy --from <src> --to <dst>
           file_copy <src> <dst>
    """
    _ = timeout
    src = ""
    dst = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--from", "-f") and i + 1 < len(args):
            src = args[i + 1]
            i += 2
            continue
        if token in ("--to", "-t") and i + 1 < len(args):
            dst = args[i + 1]
            i += 2
            continue
        if not src:
            src = token
        elif not dst:
            dst = token
        i += 1

    if not src or not dst:
        return error_result("file_copy", "Usage: file_copy --from <src> --to <dst>")

    root = find_repo_root(cwd)
    try:
        src_path = ensure_within_root(root, src)
    except ValueError as exc:
        return error_result("file_copy", str(exc))

    if not os.path.isfile(src_path):
        return error_result("file_copy", f"Source not a file: {src}")

    # For destination, we allow creating new files outside root
    try:
        if os.path.isabs(dst):
            dst_path = dst
        else:
            dst_path = os.path.join(root, dst)
    except ValueError:
        return error_result("file_copy", "Invalid destination path")

    # Create parent directories if needed
    parent_dir = os.path.dirname(dst_path)
    if parent_dir:
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except Exception as exc:
            return error_result("file_copy", f"Failed to create directory: {exc}", exit_code=1)

    try:
        shutil.copy2(src_path, dst_path)
    except Exception as exc:
        return error_result("file_copy", str(exc), exit_code=1)

    return {
        "ok": True,
        "tool": "file_copy",
        "source": relpath(root, src_path),
        "destination": relpath(root, dst_path) if dst_path.startswith(root) else dst,
        "error": None,
        "exit_code": 0,
        "stdout": f"OK: copied {src} to {dst}",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["file_copy"],
    }


def file_move(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Move a file.

    Usage: file_move --from <src> --to <dst>
           file_move <src> <dst>
    """
    _ = timeout
    src = ""
    dst = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--from", "-f") and i + 1 < len(args):
            src = args[i + 1]
            i += 2
            continue
        if token in ("--to", "-t") and i + 1 < len(args):
            dst = args[i + 1]
            i += 2
            continue
        if not src:
            src = token
        elif not dst:
            dst = token
        i += 1

    if not src or not dst:
        return error_result("file_move", "Usage: file_move --from <src> --to <dst>")

    root = find_repo_root(cwd)
    try:
        src_path = ensure_within_root(root, src)
    except ValueError as exc:
        return error_result("file_move", str(exc))

    if not os.path.exists(src_path):
        return error_result("file_move", f"Source not found: {src}")

    try:
        if os.path.isabs(dst):
            dst_path = dst
        else:
            dst_path = os.path.join(root, dst)
    except ValueError:
        return error_result("file_move", "Invalid destination path")

    parent_dir = os.path.dirname(dst_path)
    if parent_dir:
        try:
            os.makedirs(parent_dir, exist_ok=True)
        except Exception as exc:
            return error_result("file_move", f"Failed to create directory: {exc}", exit_code=1)

    try:
        shutil.move(src_path, dst_path)
    except Exception as exc:
        return error_result("file_move", str(exc), exit_code=1)

    return {
        "ok": True,
        "tool": "file_move",
        "source": relpath(root, src_path),
        "destination": relpath(root, dst_path) if dst_path.startswith(root) else dst,
        "error": None,
        "exit_code": 0,
        "stdout": f"OK: moved {src} to {dst}",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["file_move"],
    }


def dir_create(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Create a directory.

    Usage: dir_create --path <path>
           dir_create <path>
    """
    _ = timeout
    path_arg = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--path", "-p") and i + 1 < len(args):
            path_arg = args[i + 1]
            i += 2
            continue
        if token in ("--dir", "-d") and i + 1 < len(args):
            path_arg = args[i + 1]
            i += 2
            continue
        if not path_arg:
            path_arg = token
        i += 1

    if not path_arg:
        return error_result("dir_create", "Usage: dir_create --path <path>")

    root = find_repo_root(cwd)
    try:
        full_path = ensure_within_root(root, path_arg)
    except ValueError:
        # Allow creating directories outside root
        full_path = path_arg if os.path.isabs(path_arg) else os.path.join(cwd, path_arg)

    try:
        os.makedirs(full_path, exist_ok=True)
    except Exception as exc:
        return error_result("dir_create", str(exc), exit_code=1)

    rel = relpath(root, full_path) if full_path.startswith(root) else full_path
    return {
        "ok": True,
        "tool": "dir_create",
        "path": rel,
        "error": None,
        "exit_code": 0,
        "stdout": f"OK: created directory {rel}",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["dir_create"],
    }


MAX_GLOB_RESULTS = 500


def repo_glob(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Find files matching a glob pattern.

    Usage: repo_glob --pattern <pattern>
           repo_glob <pattern>
    """
    _ = timeout
    pattern = ""
    path_arg = "."

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--pattern", "-p") and i + 1 < len(args):
            pattern = args[i + 1]
            i += 2
            continue
        if token in ("--path", "--dir") and i + 1 < len(args):
            path_arg = args[i + 1]
            i += 2
            continue
        if not pattern:
            pattern = token
        i += 1

    if not pattern:
        return error_result("repo_glob", "Usage: repo_glob --pattern <pattern>")

    root = find_repo_root(cwd)
    try:
        search_path = ensure_within_root(root, path_arg)
    except ValueError:
        search_path = root

    if not os.path.isdir(search_path):
        search_path = root

    matches: List[str] = []
    skipped_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}

    try:
        for dirpath, dirnames, filenames in os.walk(search_path):
            # Skip hidden and common non-code directories
            dirnames[:] = [d for d in dirnames if d not in skipped_dirs and not d.startswith(".")]
            for name in filenames:
                if fnmatch.fnmatch(name, pattern):
                    full = os.path.join(dirpath, name)
                    rel = relpath(root, full)
                    matches.append(rel)
                    if len(matches) >= MAX_GLOB_RESULTS:
                        break
            if len(matches) >= MAX_GLOB_RESULTS:
                break
    except Exception as exc:
        return error_result("repo_glob", str(exc), exit_code=1)

    output = "\n".join(matches) if matches else "(no matches)"
    return {
        "ok": True,
        "tool": "repo_glob",
        "pattern": pattern,
        "path": path_arg,
        "matches": matches,
        "count": len(matches),
        "truncated": len(matches) >= MAX_GLOB_RESULTS,
        "error": None,
        "exit_code": 0,
        "stdout": output,
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": len(matches) >= MAX_GLOB_RESULTS,
        "artifacts": [],
        "command": ["repo_glob", pattern],
    }
