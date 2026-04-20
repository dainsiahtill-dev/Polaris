"""
Memory tools: persistent semantic memory storage and retrieval.
"""
import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from .utils import error_result, find_repo_root

MEMORY_DIR = ".polaris/memory"
MEMORY_INDEX_FILE = ".polaris/memory/index.json"


def _get_memory_dir(root: str) -> str:
    """Get the memory directory path."""
    return os.path.join(root, MEMORY_DIR)


def _get_index_path(root: str) -> str:
    """Get the memory index file path."""
    return os.path.join(root, MEMORY_INDEX_FILE)


def _load_index(root: str) -> Dict[str, Any]:
    """Load the memory index."""
    index_path = _get_index_path(root)
    if os.path.isfile(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"entries": {}, "updated": None}


def _save_index(root: str, index: Dict[str, Any]) -> None:
    """Save the memory index."""
    memory_dir = _get_memory_dir(root)
    os.makedirs(memory_dir, exist_ok=True)
    index_path = _get_index_path(root)
    index["updated"] = datetime.now().isoformat()
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _generate_memory_id(key: str) -> str:
    """Generate a memory ID from a key."""
    # Simple hash-like ID
    clean = re.sub(r"[^a-zA-Z0-9]", "_", key)
    clean = re.sub(r"_+", "_", clean).strip("_")
    timestamp = int(time.time() * 1000)
    return f"{clean}_{timestamp}"


def memory_save(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Save a memory entry.

    Usage: memory_save --key <key> --content <content>
           memory_save <key> <content>
    """
    _ = timeout

    key = ""
    content = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--key", "-k") and i + 1 < len(args):
            key = args[i + 1]
            i += 2
            continue
        if token in ("--content", "-c") and i + 1 < len(args):
            content = args[i + 1]
            i += 2
            continue
        if not key:
            key = token
        elif not content:
            content = token
        i += 1

    if not key or not content:
        return error_result(
            "memory_save",
            "Usage: memory_save --key <key> --content <content>"
        )

    root = find_repo_root(cwd)
    memory_dir = _get_memory_dir(root)
    os.makedirs(memory_dir, exist_ok=True)

    # Generate unique ID
    memory_id = _generate_memory_id(key)

    # Save memory entry
    entry = {
        "id": memory_id,
        "key": key,
        "content": content,
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
    }

    entry_path = os.path.join(memory_dir, f"{memory_id}.json")
    with open(entry_path, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)

    # Update index
    index = _load_index(root)
    index["entries"][memory_id] = {
        "key": key,
        "created": entry["created"],
    }
    _save_index(root, index)

    return {
        "ok": True,
        "tool": "memory_save",
        "id": memory_id,
        "key": key,
        "bytes_stored": len(content.encode("utf-8")),
        "error": None,
        "exit_code": 0,
        "stdout": f"OK: saved memory '{key}' (id: {memory_id})",
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["memory_save", key],
    }


def memory_recall(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Recall a memory entry.

    Usage: memory_recall --key <key>
           memory_recall --query <query>
           memory_recall <key>
    """
    _ = timeout

    key = ""
    query = ""
    limit = 10

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--key", "-k") and i + 1 < len(args):
            key = args[i + 1]
            i += 2
            continue
        if token in ("--query", "-q") and i + 1 < len(args):
            query = args[i + 1]
            i += 2
            continue
        if token in ("--limit", "-l") and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except Exception:
                pass
            i += 2
            continue
        if not key and not query:
            key = token
        i += 1

    if not key and not query:
        return error_result(
            "memory_recall",
            "Usage: memory_recall --key <key> or --query <query>"
        )

    root = find_repo_root(cwd)
    memory_dir = _get_memory_dir(root)

    if not os.path.isdir(memory_dir):
        return {
            "ok": True,
            "tool": "memory_recall",
            "results": [],
            "error": None,
            "exit_code": 0,
            "stdout": "(no memories found)",
            "stderr": "",
            "duration": 0.0,
            "duration_ms": 0,
            "truncated": False,
            "artifacts": [],
            "command": ["memory_recall"],
        }

    results: List[Dict[str, Any]] = []

    # If key provided, exact match
    if key:
        index = _load_index(root)
        for memory_id, info in index.get("entries", {}).items():
            if info.get("key") == key:
                entry_path = os.path.join(memory_dir, f"{memory_id}.json")
                if os.path.isfile(entry_path):
                    try:
                        with open(entry_path, "r", encoding="utf-8") as f:
                            entry = json.load(f)
                            results.append(entry)
                    except Exception:
                        pass
                break

    # If query provided, search in content
    if query:
        query_lower = query.lower()
        index = _load_index(root)
        count = 0
        for memory_id in list(index.get("entries", {}).keys()):
            if count >= limit:
                break
            entry_path = os.path.join(memory_dir, f"{memory_id}.json")
            if os.path.isfile(entry_path):
                try:
                    with open(entry_path, "r", encoding="utf-8") as f:
                        entry = json.load(f)
                        content_lower = entry.get("content", "").lower()
                        key_lower = entry.get("key", "").lower()
                        if query_lower in content_lower or query_lower in key_lower:
                            results.append(entry)
                            count += 1
                except Exception:
                    pass

    if not results:
        return {
            "ok": True,
            "tool": "memory_recall",
            "results": [],
            "error": None,
            "exit_code": 0,
            "stdout": "(no matching memories)",
            "stderr": "",
            "duration": 0.0,
            "duration_ms": 0,
            "truncated": False,
            "artifacts": [],
            "command": ["memory_recall"],
        }

    # Format output
    output_lines = ["Memory results:"]
    for entry in results:
        output_lines.append(f"\n[{entry.get('id')}] {entry.get('key')}")
        output_lines.append(f"Created: {entry.get('created')}")
        output_lines.append(f"Content: {entry.get('content')[:200]}")
        if len(entry.get("content", "")) > 200:
            output_lines.append("...")

    return {
        "ok": True,
        "tool": "memory_recall",
        "results": results,
        "count": len(results),
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["memory_recall"],
    }


def memory_list(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    List all memory entries.

    Usage: memory_list [--limit N]
    """
    _ = timeout

    limit = 50

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--limit", "-l") and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except Exception:
                pass
            i += 2
            continue
        i += 1

    root = find_repo_root(cwd)
    index = _load_index(root)

    entries = index.get("entries", {})
    if not entries:
        return {
            "ok": True,
            "tool": "memory_list",
            "entries": [],
            "count": 0,
            "error": None,
            "exit_code": 0,
            "stdout": "(no memories)",
            "stderr": "",
            "duration": 0.0,
            "duration_ms": 0,
            "truncated": False,
            "artifacts": [],
            "command": ["memory_list"],
        }

    # Get all entries with details
    memory_dir = _get_memory_dir(root)
    all_entries: List[Dict[str, Any]] = []

    for memory_id, info in entries.items():
        entry_path = os.path.join(memory_dir, f"{memory_id}.json")
        if os.path.isfile(entry_path):
            try:
                with open(entry_path, "r", encoding="utf-8") as f:
                    entry = json.load(f)
                    all_entries.append(entry)
            except Exception:
                pass

    # Sort by created time
    all_entries.sort(key=lambda x: x.get("created", ""), reverse=True)

    # Apply limit
    truncated = len(all_entries) > limit
    all_entries = all_entries[:limit]

    output_lines = [f"Total memories: {len(all_entries)}"]
    if truncated:
        output_lines.append(f"(showing first {limit})")

    for entry in all_entries:
        content_preview = entry.get("content", "")[:50]
        if len(entry.get("content", "")) > 50:
            content_preview += "..."
        output_lines.append(f"\n[{entry.get('id')}] {entry.get('key')}")
        output_lines.append(f"  Created: {entry.get('created')}")
        output_lines.append(f"  Preview: {content_preview}")

    return {
        "ok": True,
        "tool": "memory_list",
        "entries": all_entries,
        "count": len(all_entries),
        "truncated": truncated,
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": truncated,
        "artifacts": [],
        "command": ["memory_list"],
    }


def memory_delete(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Delete a memory entry.

    Usage: memory_delete --key <key>
           memory_delete <key>
    """
    _ = timeout

    key = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--key", "-k") and i + 1 < len(args):
            key = args[i + 1]
            i += 2
            continue
        if not key:
            key = token
        i += 1

    if not key:
        return error_result("memory_delete", "Usage: memory_delete --key <key>")

    root = find_repo_root(cwd)
    memory_dir = _get_memory_dir(root)

    index = _load_index(root)
    deleted_id = None

    for memory_id, info in list(index["entries"].items()):
        if info.get("key") == key:
            entry_path = os.path.join(memory_dir, f"{memory_id}.json")
            if os.path.isfile(entry_path):
                try:
                    os.unlink(entry_path)
                except Exception as exc:
                    return error_result("memory_delete", str(exc), exit_code=1)
            del index["entries"][memory_id]
            deleted_id = memory_id
            break

    if deleted_id:
        _save_index(root, index)
        return {
            "ok": True,
            "tool": "memory_delete",
            "key": key,
            "deleted_id": deleted_id,
            "error": None,
            "exit_code": 0,
            "stdout": f"OK: deleted memory '{key}' (id: {deleted_id})",
            "stderr": "",
            "duration": 0.0,
            "duration_ms": 0,
            "truncated": False,
            "artifacts": [],
            "command": ["memory_delete", key],
        }

    return error_result("memory_delete", f"Memory not found: {key}")
