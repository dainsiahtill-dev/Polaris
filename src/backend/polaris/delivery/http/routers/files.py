import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.runtime.artifact_store.public.service import resolve_safe_path
from polaris.cells.runtime.projection.public.service import format_mtime, read_file_tail
from polaris.delivery.http.routers._shared import StructuredHTTPException, get_state, require_auth
from polaris.delivery.http.schemas import FileReadResponse, FileTreeResponse
from polaris.kernelone.runtime.defaults import DEFAULT_WORKSPACE
from polaris.kernelone.storage.io_paths import build_cache_root

router = APIRouter()

_BINARY_EXTENSIONS = {
    ".7z",
    ".avif",
    ".bmp",
    ".class",
    ".dll",
    ".dmg",
    ".eot",
    ".exe",
    ".gif",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lockb",
    ".mov",
    ".mp3",
    ".mp4",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".sqlite",
    ".ttf",
    ".wasm",
    ".webm",
    ".woff",
    ".woff2",
    ".zip",
}
_HEAVY_DIR_NAMES = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".parcel-cache",
    ".polaris_runtime",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}
_LANGUAGE_BY_EXTENSION = {
    ".c": "c",
    ".cc": "cpp",
    ".conf": "ini",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".dockerfile": "dockerfile",
    ".csv": "csv",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsonl": "json",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".lock": "text",
    ".log": "log",
    ".md": "markdown",
    ".mdx": "markdown",
    ".makefile": "makefile",
    ".py": "python",
    ".rs": "rust",
    ".scss": "scss",
    ".sh": "shell",
    ".sql": "sql",
    ".svg": "xml",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".txt": "text",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}
_TEXT_FILE_NAMES = {
    "changelog",
    "copying",
    "dockerfile",
    "license",
    "makefile",
    "notice",
    "readme",
}
_ICON_BY_EXTENSION = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".css": "css",
    ".go": "go",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsonl": "json",
    ".jsx": "react",
    ".md": "markdown",
    ".mdx": "markdown",
    ".py": "python",
    ".rs": "rust",
    ".svg": "image",
    ".ts": "typescript",
    ".tsx": "react",
    ".yaml": "yaml",
    ".yml": "yaml",
}

_ScannedEntry = tuple[str, Path, bool, bool, os.stat_result]


def _workspace_from_request(request: Request) -> str:
    state = get_state(request)
    return str(state.settings.workspace or DEFAULT_WORKSPACE)


def _normalize_tree_root(root: str) -> str:
    token = str(root or "").strip().replace("\\", "/")
    if token in {"", ".", "/"}:
        return ""
    return token.lstrip("/")


def _commonpath_contains(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath([str(root), str(candidate)]) == str(root)
    except ValueError:
        return False


def _resolve_workspace_path(workspace: str, path: str) -> Path:
    workspace_root = Path(workspace).expanduser().resolve()
    token = _normalize_tree_root(path)
    raw_candidate = Path(token).expanduser() if os.path.isabs(token) else workspace_root / token
    candidate = raw_candidate.resolve(strict=False)
    if not _commonpath_contains(workspace_root, candidate):
        raise StructuredHTTPException(
            status_code=400,
            code="PATH_OUTSIDE_WORKSPACE",
            message="Path must stay inside the active workspace",
            details={"path": path},
        )
    return candidate


def _resolve_scoped_path(workspace: str, cache_root: str, scope: str, path: str) -> Path:
    normalized_scope = str(scope or "artifact").strip().lower()
    if normalized_scope == "workspace":
        return _resolve_workspace_path(workspace, path)
    if normalized_scope in {"runtime", "config"}:
        root = _normalize_tree_root(path)
        logical_path = normalized_scope if not root else f"{normalized_scope}/{root}"
        return Path(resolve_safe_path(workspace, cache_root, logical_path)).resolve(strict=False)
    if normalized_scope in {"artifact", "logical"}:
        return Path(resolve_safe_path(workspace, cache_root, path)).resolve(strict=False)
    raise StructuredHTTPException(
        status_code=400,
        code="INVALID_FILE_SCOPE",
        message="File scope must be workspace, runtime, config, or artifact",
        details={"scope": scope},
    )


def _extension_for_name(name: str) -> str:
    path = Path(name)
    if name in {"Dockerfile", "Makefile"}:
        return f".{name.lower()}"
    return path.suffix.lower()


def _classify_file(name: str) -> dict[str, Any]:
    extension = _extension_for_name(name)
    name_lower = name.lower()
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    is_known_text = extension in _LANGUAGE_BY_EXTENSION or name_lower in _TEXT_FILE_NAMES
    return {
        "extension": extension or None,
        "language": _LANGUAGE_BY_EXTENSION.get(extension),
        "mime": mime,
        "icon": _ICON_BY_EXTENSION.get(extension, "file"),
        "is_binary": not is_known_text
        and (extension in _BINARY_EXTENSIONS or not mime.startswith(("text/", "application/json", "application/xml"))),
    }


def _should_skip_entry(name: str, is_dir: bool, *, include_hidden: bool, include_ignored: bool) -> tuple[bool, str]:
    if not include_hidden and name.startswith("."):
        return True, "hidden"
    if is_dir and not include_ignored and name in _HEAVY_DIR_NAMES:
        return True, "ignored"
    return False, ""


def _scoped_rel_path(base: Path, entry_path: Path) -> str:
    rel = entry_path.relative_to(base).as_posix()
    return "" if rel == "." else rel


def _scan_workspace_tree(
    *,
    root_path: Path,
    base_path: Path,
    scope: str,
    max_depth: int,
    max_entries: int,
    include_hidden: bool,
    include_ignored: bool,
) -> dict[str, Any]:
    stats = {
        "files": 0,
        "directories": 0,
        "omitted": 0,
        "hidden": 0,
        "binary": 0,
        "total_size": 0,
    }
    state = {"count": 0, "truncated": False}
    excluded: set[str] = set()

    def make_item(path: Path, name: str, depth: int, entry_stat: os.stat_result | None = None) -> dict[str, Any]:
        stat_result = entry_stat
        if stat_result is None:
            try:
                stat_result = path.stat()
            except OSError:
                stat_result = None
        rel_path = _scoped_rel_path(base_path, path)
        return {
            "id": f"{scope}:{rel_path or '.'}",
            "name": name,
            "path": rel_path,
            "type": "directory",
            "depth": depth,
            "size": None if stat_result is None else int(stat_result.st_size),
            "mtime": ""
            if stat_result is None
            else datetime.fromtimestamp(stat_result.st_mtime, timezone.utc).isoformat(),
            "icon": "folder",
            "children": [],
        }

    def walk_dir(path: Path, depth: int) -> list[dict[str, Any]]:
        if state["count"] >= max_entries:
            state["truncated"] = True
            return []
        remaining = max_entries - state["count"]
        entries: list[_ScannedEntry] = []
        try:
            with os.scandir(path) as iterator:
                for entry in iterator:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        is_symlink = entry.is_symlink()
                        stat_result = entry.stat(follow_symlinks=False)
                    except OSError:
                        stats["omitted"] += 1
                        continue

                    skip, reason = _should_skip_entry(
                        entry.name,
                        is_dir,
                        include_hidden=include_hidden,
                        include_ignored=include_ignored,
                    )
                    if skip:
                        stats["omitted"] += 1
                        if reason == "hidden":
                            stats["hidden"] += 1
                        excluded.add(entry.name)
                        continue

                    if len(entries) >= remaining:
                        state["truncated"] = True
                        stats["omitted"] += 1
                        break
                    entries.append((entry.name, Path(entry.path), is_dir, is_symlink, stat_result))
        except OSError:
            stats["omitted"] += 1
            return []

        def sort_key(entry: _ScannedEntry) -> tuple[int, str]:
            name, _, is_dir, _, _ = entry
            return (0 if is_dir else 1, name.lower())

        children: list[dict[str, Any]] = []
        sorted_entries = sorted(entries, key=sort_key)
        for index, (entry_name, entry_path, is_dir, is_symlink, stat_result) in enumerate(sorted_entries):
            if state["count"] >= max_entries:
                state["truncated"] = True
                stats["omitted"] += len(sorted_entries) - index
                break

            rel_path = _scoped_rel_path(base_path, entry_path)
            state["count"] += 1
            if is_dir:
                stats["directories"] += 1
                item = make_item(entry_path, entry_name, depth, stat_result)
                item["is_symlink"] = is_symlink
                item["children"] = [] if depth >= max_depth or is_symlink else walk_dir(entry_path, depth + 1)
                children.append(item)
                continue

            classification = _classify_file(entry_name)
            size = int(stat_result.st_size)
            stats["files"] += 1
            stats["total_size"] += size
            if classification["is_binary"]:
                stats["binary"] += 1
            children.append(
                {
                    "id": f"{scope}:{rel_path}",
                    "name": entry_name,
                    "path": rel_path,
                    "type": "file",
                    "depth": depth,
                    "size": size,
                    "mtime": datetime.fromtimestamp(stat_result.st_mtime, timezone.utc).isoformat(),
                    "is_symlink": is_symlink,
                    **classification,
                }
            )
        return children

    root_name = root_path.name or scope
    root_item = make_item(root_path, root_name, 0)
    root_item["children"] = walk_dir(root_path, 1)
    return {
        "tree": root_item,
        "stats": stats,
        "truncated": bool(state["truncated"]),
        "excluded": sorted(excluded),
    }


def _read_file_head(full_path: str, max_chars: int) -> str:
    if not full_path or not os.path.isfile(full_path):
        return ""
    char_limit = max(1, min(int(max_chars or 20000), 1_000_000))
    byte_limit = char_limit * 4
    try:
        with open(full_path, "rb") as handle:
            data = handle.read(byte_limit)
    except (OSError, ValueError):
        return ""
    return data.decode("utf-8", errors="replace")[:char_limit]


def _read_file_response(
    workspace: str,
    cache_root: str,
    path: str,
    tail_lines: int,
    max_chars: int,
    scope: str,
    read_mode: str,
) -> dict[str, Any]:
    full_path = str(_resolve_scoped_path(workspace, cache_root, scope, path))
    normalized_mode = str(read_mode or "tail").strip().lower()
    if normalized_mode == "head":
        content = _read_file_head(full_path, max_chars=max_chars)
    elif normalized_mode == "tail":
        normalized = full_path.replace("\\", "/").lower()
        allow_fallback = not normalized.endswith("/dialogue.jsonl")
        content = read_file_tail(full_path, max_lines=tail_lines, max_chars=max_chars, allow_fallback=allow_fallback)
    else:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_FILE_READ_MODE",
            message="File read mode must be head or tail",
            details={"read_mode": read_mode},
        )
    return {
        "path": full_path,
        "rel_path": path,
        "mtime": format_mtime(full_path),
        "content": content,
    }


@router.get("/files/read", dependencies=[Depends(require_auth)], response_model=FileReadResponse)  # DEPRECATED
def read_file(
    request: Request,
    path: str,
    tail_lines: int = 400,
    max_chars: int = 20000,
    scope: str = "artifact",
    read_mode: str = "tail",
) -> dict[str, Any]:
    workspace = _workspace_from_request(request)
    cache_root = build_cache_root("", str(workspace))
    return _read_file_response(str(workspace), str(cache_root), path, tail_lines, max_chars, scope, read_mode)


@router.get("/v2/files/read", dependencies=[Depends(require_auth)], response_model=FileReadResponse)
def v2_read_file(
    request: Request,
    path: str,
    tail_lines: int = 400,
    max_chars: int = 20000,
    scope: str = "artifact",
    read_mode: str = "tail",
) -> dict[str, Any]:
    """Read a workspace file with optional tail truncation."""
    workspace = _workspace_from_request(request)
    cache_root = build_cache_root("", str(workspace))
    return _read_file_response(str(workspace), str(cache_root), path, tail_lines, max_chars, scope, read_mode)


@router.get("/v2/files/tree", dependencies=[Depends(require_auth)], response_model=FileTreeResponse)
def v2_file_tree(
    request: Request,
    root: str = "",
    scope: str = "workspace",
    max_depth: int = 8,
    max_entries: int = 4000,
    include_hidden: bool = True,
    include_ignored: bool = False,
) -> dict[str, Any]:
    """Return a bounded, workspace-scoped file tree snapshot."""
    workspace = _workspace_from_request(request)
    cache_root = build_cache_root("", str(workspace))
    normalized_scope = str(scope or "workspace").strip().lower()
    safe_depth = max(1, min(int(max_depth or 1), 32))
    safe_entries = max(1, min(int(max_entries or 1), 20000))
    base_path = _resolve_scoped_path(str(workspace), str(cache_root), normalized_scope, "")
    root_path = _resolve_scoped_path(str(workspace), str(cache_root), normalized_scope, root)
    if not root_path.exists():
        raise StructuredHTTPException(
            status_code=404,
            code="FILE_TREE_ROOT_NOT_FOUND",
            message="File tree root does not exist",
            details={"root": root, "scope": normalized_scope},
        )
    if not root_path.is_dir():
        raise StructuredHTTPException(
            status_code=400,
            code="FILE_TREE_ROOT_NOT_DIRECTORY",
            message="File tree root must be a directory",
            details={"root": root, "scope": normalized_scope},
        )
    scan = _scan_workspace_tree(
        root_path=root_path,
        base_path=base_path,
        scope=normalized_scope,
        max_depth=safe_depth,
        max_entries=safe_entries,
        include_hidden=include_hidden,
        include_ignored=include_ignored,
    )
    return {
        "workspace": str(workspace),
        "scope": normalized_scope,
        "root": _normalize_tree_root(root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "max_depth": safe_depth,
        "max_entries": safe_entries,
        "truncated": scan["truncated"],
        "excluded": scan["excluded"],
        "stats": scan["stats"],
        "tree": scan["tree"],
    }
