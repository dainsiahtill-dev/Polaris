"""LanceDB code search repository.

Provides:
- index_workspace(): Walk workspace, chunk code files, store in LanceDB
- search_code(): Semantic search over indexed code chunks
- refresh_index(): Incremental re-index of changed files
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from polaris.infrastructure.db.adapters import LanceDbAdapter
from polaris.kernelone.constants import MAX_FILE_SIZE_BYTES
from polaris.kernelone.db import KernelDatabase

logger = logging.getLogger(__name__)

# File extensions to index
INDEXABLE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".md",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
}

# Directories to skip
SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "target",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    ".turbo",
}

CHUNK_SIZE = 80  # lines per chunk
CHUNK_OVERLAP = 10  # overlap lines


def _should_index(path: Path) -> bool:
    """Check if a file should be indexed."""
    if path.suffix.lower() not in INDEXABLE_EXTENSIONS:
        return False
    try:
        if path.stat().st_size > MAX_FILE_SIZE_BYTES:
            return False
    except OSError:
        return False
    return True


def _chunk_file(file_path: Path, rel_path: str) -> list[dict[str, Any]]:
    """Split a file into overlapping chunks for indexing."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except (RuntimeError, ValueError):
        return []

    lines = text.splitlines()
    if not lines:
        return []

    chunks = []
    file_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]

    for start in range(0, len(lines), CHUNK_SIZE - CHUNK_OVERLAP):
        end = min(start + CHUNK_SIZE, len(lines))
        chunk_lines = lines[start:end]
        chunk_text = "\n".join(chunk_lines)
        if not chunk_text.strip():
            continue

        chunk_id = f"{rel_path}:{start + 1}-{end}"
        chunks.append(
            {
                "id": chunk_id,
                "file_path": rel_path,
                "line_start": start + 1,
                "line_end": end,
                "text": chunk_text,
                "language": file_path.suffix.lstrip("."),
                "file_hash": file_hash,
                "chunk_size": len(chunk_lines),
            }
        )

        if end >= len(lines):
            break

    return chunks


def _discover_files(workspace: str) -> list[Path]:
    """Discover indexable files in workspace."""
    workspace_path = Path(workspace)
    files = []
    for root, dirs, filenames in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in filenames:
            fpath = Path(root) / fname
            if _should_index(fpath):
                files.append(fpath)
    return files


def index_workspace(
    workspace: str,
    db_path: str | None = None,
    table_name: str = "code_chunks",
) -> dict[str, Any]:
    """Index all code files in the workspace into LanceDB.

    Args:
        workspace: Path to the workspace root
        db_path: Path to LanceDB directory (default: workspace persistent lancedb)
        table_name: Name of the LanceDB table

    Returns:
        Dict with indexing statistics
    """
    workspace_path = Path(workspace).resolve()
    db_token = str(db_path or "workspace/lancedb")
    kernel_db = KernelDatabase(
        str(workspace_path),
        lancedb_adapter=LanceDbAdapter(),
        allow_unmanaged_absolute=True,
    )
    try:
        resolved_db_path = kernel_db.resolve_lancedb_path(db_token, ensure_exists=True)
        db = kernel_db.lancedb(db_token, ensure_exists=True)
    except (RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "indexed": 0}

    files = _discover_files(workspace)
    all_chunks: list[dict[str, Any]] = []

    for fpath in files:
        try:
            rel_path = str(fpath.relative_to(workspace_path)).replace("\\", "/")
        except ValueError:
            continue
        chunks = _chunk_file(fpath, rel_path)
        all_chunks.extend(chunks)

    if not all_chunks:
        return {"ok": True, "indexed": 0, "files": 0, "chunks": 0}

    try:
        try:
            db.drop_table(table_name)
        except (RuntimeError, ValueError):
            logger.debug("DEBUG: lancedb_code_search.py:{150} {exc} (swallowed)")
        db.create_table(table_name, data=all_chunks)
    except (RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "indexed": 0}

    return {
        "ok": True,
        "indexed": len(all_chunks),
        "files": len(files),
        "chunks": len(all_chunks),
        "db_path": resolved_db_path,
    }


def search_code(
    query: str,
    workspace: str,
    db_path: str | None = None,
    table_name: str = "code_chunks",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search indexed code using text matching.

    Args:
        query: Search query string
        workspace: Workspace path
        db_path: LanceDB directory path
        table_name: Table name
        limit: Max results to return

    Returns:
        List of matching code chunks
    """
    workspace_path = Path(workspace).resolve()
    db_token = str(db_path or "workspace/lancedb")
    kernel_db = KernelDatabase(
        str(workspace_path),
        lancedb_adapter=LanceDbAdapter(),
        allow_unmanaged_absolute=True,
    )
    try:
        resolved_db_path = kernel_db.resolve_lancedb_path(db_token, ensure_exists=False)
    except (RuntimeError, ValueError):
        return []
    if not os.path.isdir(resolved_db_path):
        return []

    try:
        db = kernel_db.lancedb(db_token, ensure_exists=False)
        table = db.open_table(table_name)
    except (RuntimeError, ValueError):
        return []

    # Full-text search on the text column
    query_lower = query.lower()
    try:
        # Use LanceDB's search capabilities
        df = table.to_pandas()
        # Simple text matching (can be upgraded to vector search with embeddings)
        mask = df["text"].str.lower().str.contains(query_lower, na=False)
        matches = df[mask].head(limit)
        results = []
        for _, row in matches.iterrows():
            results.append(
                {
                    "file_path": row["file_path"],
                    "line_start": int(row["line_start"]),
                    "line_end": int(row["line_end"]),
                    "text": row["text"],
                    "language": row["language"],
                }
            )
        return results
    except (RuntimeError, ValueError):
        return []


def refresh_index(
    workspace: str,
    changed_files: list[str],
    db_path: str | None = None,
    table_name: str = "code_chunks",
) -> dict[str, Any]:
    """Incrementally update the index for changed files only.

    Args:
        workspace: Workspace path
        changed_files: List of changed file paths (relative to workspace)
        db_path: LanceDB directory path
        table_name: Table name

    Returns:
        Dict with update statistics
    """
    workspace_path = Path(workspace).resolve()
    db_token = str(db_path or "workspace/lancedb")
    kernel_db = KernelDatabase(
        str(workspace_path),
        lancedb_adapter=LanceDbAdapter(),
        allow_unmanaged_absolute=True,
    )
    try:
        resolved_db_path = kernel_db.resolve_lancedb_path(db_token, ensure_exists=False)
    except (RuntimeError, ValueError):
        return {"ok": False, "error": "invalid_lancedb_path"}
    if not os.path.isdir(resolved_db_path):
        return index_workspace(workspace, db_token, table_name)

    try:
        db = kernel_db.lancedb(db_token, ensure_exists=False)
        table = db.open_table(table_name)
    except (RuntimeError, ValueError):
        return index_workspace(workspace, db_token, table_name)

    # Remove old chunks for changed files
    normalized = [f.replace("\\", "/") for f in changed_files]
    new_chunks: list[dict[str, Any]] = []

    for rel_path in normalized:
        fpath = workspace_path / rel_path
        if fpath.is_file() and _should_index(fpath):
            new_chunks.extend(_chunk_file(fpath, rel_path))

    try:
        # Remove old entries for changed files
        df = table.to_pandas()
        keep_mask = ~df["file_path"].isin(normalized)
        kept = df[keep_mask].to_dict("records")

        # Rebuild table with kept + new
        all_records = kept + new_chunks
        if all_records:
            db.drop_table(table_name)
            db.create_table(table_name, data=all_records)
        else:
            db.drop_table(table_name)
    except (RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "updated_files": len(normalized),
        "new_chunks": len(new_chunks),
    }


__all__ = ["index_workspace", "refresh_index", "search_code"]
