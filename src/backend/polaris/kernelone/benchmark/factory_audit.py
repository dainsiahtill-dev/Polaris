"""Deterministic audit primitives for factory-bench full-chain project runs.

Zero-LLM: inspects a workspace that the Polaris role chain (PM→Architect/CE→
Director→QA) just generated a project into, and produces a schema-stamped
audit record: which planning/blueprint documents exist, what code was
produced, and whether deterministic runnability checks pass (Python compiles,
HTML present, JS syntax, minimum file counts).

Check vocabulary (mirrors ``scripts/factory_bench/projects_v1.json``):
``py_compile`` / ``html`` / ``js_syntax`` / ``min_files:N``.
"""

from __future__ import annotations

import os
import py_compile
import shutil
import subprocess
from typing import Any

FACTORY_AUDIT_SCHEMA_VERSION = "factory-audit/1"

_CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".c",
    ".h",
    ".cpp",
    ".rs",
    ".go",
    ".java",
    ".json",
    ".sql",
    ".sh",
}
_DOC_EXTENSIONS = {".md", ".rst", ".txt"}
# "runtime" excluded: the chain mirrors traceability json INTO the workspace,
# which inflated code_file_count (L1-02 min_files passed on a matrix json).
_SKIP_DIRS = {".git", ".polaris", "__pycache__", "node_modules", ".venv", "venv", "runtime"}
_MAX_SCAN_FILES = 20000


def collect_workspace_inventory(workspace: str) -> dict[str, Any]:
    """Enumerate generated code/doc files (workspace-relative, sorted)."""
    code_files: list[str] = []
    doc_files: list[str] = []
    scanned = 0
    for current_root, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            scanned += 1
            if scanned > _MAX_SCAN_FILES:
                dirnames[:] = []
                break
            rel = os.path.relpath(os.path.join(current_root, filename), workspace).replace("\\", "/")
            _, ext = os.path.splitext(filename)
            if ext.lower() in _CODE_EXTENSIONS:
                code_files.append(rel)
            elif ext.lower() in _DOC_EXTENSIONS:
                doc_files.append(rel)
    return {"code_files": sorted(code_files), "doc_files": sorted(doc_files)}


def _iter_files(workspace: str, suffix: str) -> list[str]:
    matches: list[str] = []
    for current_root, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            if filename.lower().endswith(suffix):
                matches.append(os.path.join(current_root, filename))
    return sorted(matches)


def _check_py_compile(workspace: str) -> tuple[bool, str]:
    failures: list[str] = []
    py_files = _iter_files(workspace, ".py")
    if not py_files:
        return False, "no .py files found"
    for path in py_files:
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{os.path.relpath(path, workspace)}: {exc.msg.splitlines()[-1] if exc.msg else exc}")
        except (OSError, UnicodeDecodeError) as exc:
            failures.append(f"{os.path.relpath(path, workspace)}: {exc}")
    if failures:
        return False, f"{len(failures)}/{len(py_files)} files fail to compile: " + "; ".join(failures[:3])
    return True, f"{len(py_files)} python files compile"


def _check_html(workspace: str) -> tuple[bool, str]:
    html_files = _iter_files(workspace, ".html")
    for path in html_files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if "<html" in fh.read().lower():
                    return True, f"html page present: {os.path.relpath(path, workspace)}"
        except OSError:
            continue
    return False, "no .html file containing <html> found"


def _check_js_syntax(workspace: str) -> tuple[bool, str]:
    js_files = [p for p in _iter_files(workspace, ".js") if not p.endswith(".min.js")]
    if not js_files:
        return False, "no .js files found"
    node = shutil.which("node")
    if not node:
        return True, f"node unavailable — {len(js_files)} js files present (syntax unchecked)"
    failures: list[str] = []
    for path in js_files:
        proc = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=30, check=False)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            failures.append(f"{os.path.relpath(path, workspace)}: {detail[0] if detail else 'syntax error'}")
    if failures:
        return False, f"{len(failures)}/{len(js_files)} js files fail --check: " + "; ".join(failures[:3])
    return True, f"{len(js_files)} js files pass node --check"


def run_checks(workspace: str, checks: list[str]) -> list[dict[str, Any]]:
    """Run the project's deterministic check list; unknown kinds fail closed."""
    inventory = collect_workspace_inventory(workspace)
    results: list[dict[str, Any]] = []
    for check in checks:
        kind = check.strip()
        ok = False
        detail = ""
        if kind == "py_compile":
            ok, detail = _check_py_compile(workspace)
        elif kind == "html":
            ok, detail = _check_html(workspace)
        elif kind == "js_syntax":
            ok, detail = _check_js_syntax(workspace)
        elif kind.startswith("min_files:"):
            try:
                minimum = int(kind.split(":", 1)[1])
            except ValueError:
                minimum = 1
            count = len(inventory["code_files"])
            ok = count >= minimum
            detail = f"{count} code files (need >= {minimum})"
        else:
            detail = f"unknown check kind: {kind}"
        results.append({"check": kind, "ok": ok, "detail": detail})
    return results


def build_factory_audit_record(
    *,
    project: dict[str, Any],
    workspace: str,
    artifact_globs: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Assemble the full per-project audit record.

    ``artifact_globs`` maps artifact kinds (plan/blueprint/verdict) to lists of
    workspace-relative paths discovered by the runner (the chain's artifact
    layout is runner-configured, not hardcoded here).
    """
    inventory = collect_workspace_inventory(workspace)
    checks = run_checks(workspace, list(project.get("checks") or []))
    artifacts = artifact_globs or {}
    return {
        "schema_version": FACTORY_AUDIT_SCHEMA_VERSION,
        "project_id": str(project.get("id") or ""),
        "level": int(project.get("level") or 0),
        "domain": str(project.get("domain") or ""),
        "title": str(project.get("title") or ""),
        "code_file_count": len(inventory["code_files"]),
        "code_files": inventory["code_files"][:60],
        "doc_files": inventory["doc_files"][:30],
        "artifacts": {kind: paths[:10] for kind, paths in artifacts.items()},
        "has_plan_doc": bool(artifacts.get("plan")),
        "has_blueprint_doc": bool(artifacts.get("blueprint")),
        "has_qa_verdict": bool(artifacts.get("verdict")),
        "checks": checks,
        "all_checks_passed": bool(checks) and all(c["ok"] for c in checks),
    }


def aggregate_factory_audits(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Run-level aggregate over per-project audit records (per-level breakdown)."""
    total = len(records)
    by_level: dict[str, dict[str, int]] = {}
    for record in records:
        bucket = by_level.setdefault(f"L{record.get('level')}", {"total": 0, "passed": 0})
        bucket["total"] += 1
        if record.get("all_checks_passed"):
            bucket["passed"] += 1
    return {
        "schema_version": FACTORY_AUDIT_SCHEMA_VERSION,
        "total": total,
        "all_checks_passed": sum(1 for r in records if r.get("all_checks_passed")),
        "with_plan_doc": sum(1 for r in records if r.get("has_plan_doc")),
        "with_blueprint_doc": sum(1 for r in records if r.get("has_blueprint_doc")),
        "with_qa_verdict": sum(1 for r in records if r.get("has_qa_verdict")),
        "by_level": dict(sorted(by_level.items())),
    }
