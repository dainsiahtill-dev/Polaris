"""
Batch edit tools: multi-file editing operations.
"""
import json
import os
from typing import Any, Dict, List

from .utils import (
    ensure_within_root,
    error_result,
    find_repo_root,
    relpath,
)


def multi_file_edit(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Edit multiple files at once.

    Usage: multi_file_edit --ops <json>
           multi_file_edit --file <ops_file>

    The ops JSON should be an array of operations:
    [
        {"file": "path/to/file.py", "search": "old text", "replace": "new text"},
        {"file": "path/to/another.py", "search": "old", "replace": "new"}
    ]
    """
    _ = timeout

    ops_json = ""
    ops_file = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--ops", "-o") and i + 1 < len(args):
            ops_json = args[i + 1]
            i += 2
            continue
        if token in ("--file", "-f") and i + 1 < len(args):
            ops_file = args[i + 1]
            i += 2
            continue
        i += 1

    operations: List[Dict[str, str]] = []

    if ops_file:
        # Load from file
        root = find_repo_root(cwd)
        try:
            full_path = ensure_within_root(root, ops_file)
        except ValueError as exc:
            return error_result("multi_file_edit", str(exc))

        if not os.path.isfile(full_path):
            return error_result("multi_file_edit", f"Ops file not found: {ops_file}")

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
                operations = json.loads(content)
        except json.JSONDecodeError as exc:
            return error_result("multi_file_edit", f"Invalid JSON: {exc}")
        except Exception as exc:
            return error_result("multi_file_edit", str(exc), exit_code=1)

    elif ops_json:
        try:
            operations = json.loads(ops_json)
        except json.JSONDecodeError as exc:
            return error_result("multi_file_edit", f"Invalid JSON: {exc}")

    else:
        return error_result(
            "multi_file_edit",
            "Usage: multi_file_edit --ops <json> or --file <path>"
        )

    if not operations:
        return error_result("multi_file_edit", "No operations provided")

    if not isinstance(operations, list):
        return error_result("multi_file_edit", "Operations must be an array")

    root = find_repo_root(cwd)
    results: List[Dict[str, Any]] = []
    changed_files: List[str] = []

    for idx, op in enumerate(operations):
        if not isinstance(op, dict):
            results.append({
                "index": idx,
                "ok": False,
                "error": "Operation must be an object",
            })
            continue

        file_path = op.get("file", "")
        search = op.get("search", "")
        replace = op.get("replace", "")

        if not file_path:
            results.append({
                "index": idx,
                "ok": False,
                "error": "Missing 'file' field",
            })
            continue

        try:
            full_path = ensure_within_root(root, file_path)
        except ValueError as exc:
            results.append({
                "index": idx,
                "file": file_path,
                "ok": False,
                "error": str(exc),
            })
            continue

        # Check if file exists
        if not os.path.isfile(full_path):
            # Create new file if it doesn't exist (and has content)
            try:
                parent = os.path.dirname(full_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(replace)
                rel = relpath(root, full_path)
                changed_files.append(rel)
                results.append({
                    "index": idx,
                    "file": rel,
                    "ok": True,
                    "action": "created",
                })
            except Exception as exc:
                results.append({
                    "index": idx,
                    "file": file_path,
                    "ok": False,
                    "error": str(exc),
                })
            continue

        # Read file content
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as exc:
            results.append({
                "index": idx,
                "file": file_path,
                "ok": False,
                "error": f"Failed to read: {exc}",
            })
            continue

        # Check if search pattern exists
        if search not in content:
            results.append({
                "index": idx,
                "file": file_path,
                "ok": False,
                "error": f"Search pattern not found (occurences: {content.count(search)})",
            })
            continue

        # Replace
        updated = content.replace(search, replace, 1)

        # Check if anything changed
        if updated == content:
            results.append({
                "index": idx,
                "file": file_path,
                "ok": True,
                "action": "no_change",
            })
            continue

        # Write back
        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(updated)
            rel = relpath(root, full_path)
            changed_files.append(rel)
            results.append({
                "index": idx,
                "file": rel,
                "ok": True,
                "action": "replaced",
            })
        except Exception as exc:
            results.append({
                "index": idx,
                "file": file_path,
                "ok": False,
                "error": str(exc),
            })

    # Summary
    success_count = sum(1 for r in results if r.get("ok"))
    fail_count = len(results) - success_count

    output_lines = [f"Multi-file edit results: {success_count} succeeded, {fail_count} failed"]
    for r in results:
        status = "OK" if r.get("ok") else "FAIL"
        action = r.get("action", "")
        file = r.get("file", "")
        error = r.get("error", "")
        output_lines.append(f"  [{status}] {file}" + (f" ({action})" if action else ""))
        if error:
            output_lines.append(f"        Error: {error}")

    return {
        "ok": fail_count == 0,
        "tool": "multi_file_edit",
        "operations": results,
        "changed_files": changed_files,
        "success_count": success_count,
        "fail_count": fail_count,
        "error": None if fail_count == 0 else f"{fail_count} operations failed",
        "exit_code": 0 if fail_count == 0 else 1,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["multi_file_edit"],
    }
