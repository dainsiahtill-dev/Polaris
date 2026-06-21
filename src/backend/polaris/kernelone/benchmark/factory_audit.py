"""Deterministic audit primitives for factory-bench full-chain project runs.

Zero-LLM: inspects a workspace that the Polaris role chain (PM→Architect/CE→
Director→QA) just generated a project into, and produces a schema-stamped
audit record: which planning/blueprint documents exist, what code was
produced, and whether deterministic runnability checks pass (Python compiles,
HTML present, JS syntax, minimum file counts).

Check vocabulary (mirrors ``scripts/factory_bench/projects_v1.json``):
``py_compile`` / ``html`` / ``js_syntax`` / ``min_files:N`` /
``package_scripts`` / ``ts_syntax`` / ``go_compile`` / ``rust_compile`` /
``cpp_compile`` / ``java_compile``.
"""

from __future__ import annotations

import json
import os
import py_compile
import re
import shlex
import shutil
import subprocess
import tempfile
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
_SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".rs",
    ".go",
    ".java",
}
_SCAFFOLD_EXTENSIONS = {".json", ".html", ".css", ".sh", ".sql"}
_DOC_EXTENSIONS = {".md", ".rst", ".txt"}
# "runtime" excluded: the chain mirrors traceability json INTO the workspace,
# which inflated code_file_count (L1-02 min_files passed on a matrix json).
_SKIP_DIRS = {".git", ".polaris", "__pycache__", "node_modules", ".venv", "venv", "runtime"}
_MAX_SCAN_FILES = 20000
_SCRIPT_INTERPRETERS = {"node", "python", "python3", "bash", "sh"}
_SCRIPT_PATH_EXTENSIONS = {".cjs", ".js", ".mjs", ".py", ".sh", ".ts", ".tsx"}
_SHELL_OPERATORS = {"&&", "||", ";", "|"}
_BUILD_OUTPUT_DIR_NAMES = {"dist", "build", "out", "bin"}
_PLACEHOLDER_SCRIPT_COMMANDS = {"echo", "printf"}
_SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE = re.compile(
    r"(?:npm\s+run\s+(?:build|compile)|pnpm\s+(?:build|compile)|yarn\s+(?:build|compile)|\btsc\b)",
    re.IGNORECASE,
)


def collect_workspace_inventory(workspace: str) -> dict[str, Any]:
    """Enumerate generated code/doc files (workspace-relative, sorted).

    Returns dict with keys:
    - ``code_files``: all files matching _CODE_EXTENSIONS (backward compatible)
    - ``source_files``: subset of code_files that are real source code (not scaffold)
    - ``doc_files``: documentation files
    """
    code_files: list[str] = []
    source_files: list[str] = []
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
            ext_lower = ext.lower()
            if ext_lower in _CODE_EXTENSIONS:
                code_files.append(rel)
                if ext_lower in _SOURCE_EXTENSIONS:
                    source_files.append(rel)
            elif ext_lower in _DOC_EXTENSIONS:
                doc_files.append(rel)
    return {
        "code_files": sorted(code_files),
        "source_files": sorted(source_files),
        "doc_files": sorted(doc_files),
    }


def _iter_files(workspace: str, suffix: str) -> list[str]:
    matches: list[str] = []
    for current_root, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            if filename.lower().endswith(suffix):
                matches.append(os.path.join(current_root, filename))
    return sorted(matches)


def _iter_files_any(workspace: str, suffixes: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    lowered = tuple(suffix.lower() for suffix in suffixes)
    for current_root, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            if filename.lower().endswith(lowered):
                matches.append(os.path.join(current_root, filename))
    return sorted(matches)


def _rel_paths(workspace: str, paths: list[str]) -> list[str]:
    return [os.path.relpath(path, workspace).replace("\\", "/") for path in paths]


def _tool_unavailable_detail(tool: str, language: str, count: int) -> str:
    return f"{tool} unavailable — {count} {language} file(s) present but compile check could not run"


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
        # Single-file HTML apps with inline <script> are a legitimate
        # implementation shape (live L2-10 r4: a complete one-file Markdown
        # previewer); judge the inline script presence instead of failing.
        for html_path in _iter_files(workspace, ".html"):
            try:
                with open(html_path, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue
            inline_match = re.search(
                r"<script\b[^>]*>(?P<body>.*?)</script>",
                content,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if inline_match and inline_match.group("body").strip():
                rel = os.path.relpath(html_path, workspace)
                return True, f"no standalone .js; inline <script> present in {rel}"
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


def _check_ts_syntax(workspace: str) -> tuple[bool, str]:
    ts_files = [path for path in _iter_files_any(workspace, (".ts", ".tsx")) if not path.endswith(".d.ts")]
    if not ts_files:
        return False, "no .ts/.tsx files found"
    tsc = shutil.which("tsc")
    if not tsc:
        return False, _tool_unavailable_detail("tsc", "TypeScript", len(ts_files))
    cmd = [
        tsc,
        "--noEmit",
        "--target",
        "ES2020",
        "--module",
        "ESNext",
        "--jsx",
        "react-jsx",
        *_rel_paths(workspace, ts_files[:80]),
    ]
    proc = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return False, "tsc --noEmit failed: " + (detail[0] if detail else "unknown TypeScript error")
    return True, f"{len(ts_files)} TypeScript files pass tsc --noEmit"


def _check_go_compile(workspace: str) -> tuple[bool, str]:
    go_files = _iter_files(workspace, ".go")
    if not go_files:
        return False, "no .go files found"
    go = shutil.which("go")
    if not go:
        return False, _tool_unavailable_detail("go", "Go", len(go_files))
    if os.path.exists(os.path.join(workspace, "go.mod")):
        cmd = [go, "test", "./..."]
    else:
        cmd = [go, "test", *_rel_paths(workspace, go_files[:80])]
    proc = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, timeout=60, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return False, "go test compile failed: " + (detail[0] if detail else "unknown Go error")
    return True, f"{len(go_files)} Go files compile via go test"


def _check_rust_compile(workspace: str) -> tuple[bool, str]:
    rust_files = _iter_files(workspace, ".rs")
    if not rust_files:
        return False, "no .rs files found"
    cargo = shutil.which("cargo")
    if os.path.exists(os.path.join(workspace, "Cargo.toml")) and cargo:
        proc = subprocess.run(
            [cargo, "check", "--quiet"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            return False, "cargo check failed: " + (detail[0] if detail else "unknown Rust error")
        return True, f"{len(rust_files)} Rust files compile via cargo check"
    rustc = shutil.which("rustc")
    if not rustc:
        return False, _tool_unavailable_detail("rustc/cargo", "Rust", len(rust_files))
    rels = _rel_paths(workspace, rust_files)
    root = next(
        (rel for rel in ("src/main.rs", "main.rs", "src/lib.rs", "lib.rs") if rel in rels),
        rels[0],
    )
    proc = subprocess.run(
        [rustc, "--edition=2021", "--emit=metadata", root],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return False, "rustc metadata compile failed: " + (detail[0] if detail else "unknown Rust error")
    return True, f"{len(rust_files)} Rust files compile via rustc metadata"


def _check_cpp_compile(workspace: str) -> tuple[bool, str]:
    cpp_files = _iter_files_any(workspace, (".cc", ".cpp", ".cxx"))
    if not cpp_files:
        return False, "no C++ source files found"
    compiler = shutil.which("g++") or shutil.which("c++")
    if not compiler:
        return False, _tool_unavailable_detail("g++/c++", "C++", len(cpp_files))
    failures: list[str] = []
    for rel in _rel_paths(workspace, cpp_files[:80]):
        proc = subprocess.run(
            [compiler, "-std=c++17", "-fsyntax-only", rel],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            failures.append(f"{rel}: {detail[0] if detail else 'syntax error'}")
    if failures:
        return False, f"{len(failures)}/{len(cpp_files)} C++ files fail syntax check: " + "; ".join(failures[:3])
    return True, f"{len(cpp_files)} C++ files pass g++ -fsyntax-only"


def _check_java_compile(workspace: str) -> tuple[bool, str]:
    java_files = _iter_files(workspace, ".java")
    if not java_files:
        return False, "no .java files found"
    javac = shutil.which("javac")
    if not javac:
        return False, _tool_unavailable_detail("javac", "Java", len(java_files))
    with tempfile.TemporaryDirectory(prefix="polaris-factory-javac-") as out_dir:
        proc = subprocess.run(
            [javac, "-encoding", "UTF-8", "-d", out_dir, *_rel_paths(workspace, java_files[:120])],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return False, "javac compile failed: " + (detail[0] if detail else "unknown Java error")
    return True, f"{len(java_files)} Java files compile via javac"


def _is_local_script_reference(token: str) -> bool:
    if not token or token.startswith("-"):
        return False
    normalized = token.replace("\\", "/")
    if "://" in normalized:
        return False
    _, ext = os.path.splitext(normalized)
    return "/" in normalized or ext.lower() in _SCRIPT_PATH_EXTENSIONS


def _is_local_script_option_reference(token: str) -> bool:
    normalized = token.replace("\\", "/")
    _, ext = os.path.splitext(normalized)
    return normalized.startswith(("./", "../", "/")) or ext.lower() in _SCRIPT_PATH_EXTENSIONS


def _script_reference_exists(workspace: str, token: str) -> bool:
    normalized = token.replace("\\", "/")
    if os.path.isabs(normalized):
        return os.path.exists(normalized)
    exact = os.path.join(workspace, normalized)
    if os.path.exists(exact):
        return True
    base, ext = os.path.splitext(exact)
    if ext:
        return False
    return any(os.path.exists(base + suffix) for suffix in _SCRIPT_PATH_EXTENSIONS)


def _script_builds_before_interpreter(tokens: list[str], interpreter_index: int) -> bool:
    return bool(_SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE.search(" ".join(tokens[:interpreter_index])))


def _is_build_output_reference(candidate: str) -> bool:
    normalized = candidate.replace("\\", "/").lstrip("./")
    first_segment = normalized.split("/", 1)[0]
    return first_segment in _BUILD_OUTPUT_DIR_NAMES


def _script_lifecycle_can_build_output(scripts: dict[str, Any], script_name: str, tokens: list[str]) -> bool:
    pre_script = scripts.get(f"pre{script_name}")
    command = " ".join(tokens)
    if isinstance(pre_script, str) and _SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE.search(pre_script):
        return True
    if _SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE.search(command):
        return True
    return script_name in {"start", "serve", "preview"} and isinstance(scripts.get("build"), str)


def _missing_package_script_entrypoints(
    workspace: str,
    script_name: str,
    command: str,
    *,
    scripts: dict[str, Any] | None = None,
) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return [f"script {script_name!r} has invalid shell syntax: {exc}"]
    all_scripts = scripts or {}
    missing: list[str] = []
    index = 0
    while index < len(tokens):
        interpreter_index = index
        token = os.path.basename(tokens[index])
        if token not in _SCRIPT_INTERPRETERS:
            index += 1
            continue
        index += 1
        while index < len(tokens):
            candidate = tokens[index]
            if candidate in _SHELL_OPERATORS:
                break
            if token == "node" and candidate in {"-e", "--eval", "-p", "--print"}:
                index += 2
                continue
            if token == "node" and candidate in {"-r", "--require", "--import", "--loader"}:
                if index + 1 < len(tokens):
                    option_value = tokens[index + 1]
                    if _is_local_script_option_reference(option_value) and not _script_reference_exists(
                        workspace, option_value
                    ):
                        missing.append(f"script {script_name!r} references missing local entrypoint: {option_value}")
                index += 2
                continue
            if candidate.startswith("-"):
                index += 1
                continue
            if _is_local_script_reference(candidate) and not _script_reference_exists(workspace, candidate):
                if _script_builds_before_interpreter(tokens, interpreter_index):
                    break
                if _is_build_output_reference(candidate) and _script_lifecycle_can_build_output(
                    all_scripts,
                    script_name,
                    tokens,
                ):
                    break
                missing.append(f"script {script_name!r} references missing local entrypoint: {candidate}")
            break
    return missing


def _placeholder_package_script_reason(script_name: str, command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ""
    if not tokens:
        return f"script {script_name!r} is empty"
    first_command = os.path.basename(tokens[0])
    if first_command not in _PLACEHOLDER_SCRIPT_COMMANDS:
        return ""
    for index, token in enumerate(tokens):
        if token not in _SHELL_OPERATORS or index + 1 >= len(tokens):
            continue
        next_command = os.path.basename(tokens[index + 1])
        if next_command not in {*_PLACEHOLDER_SCRIPT_COMMANDS, "exit", "true"}:
            return ""
    return f"script {script_name!r} is a placeholder command: {command}"


def _check_package_scripts(workspace: str) -> tuple[bool, str]:
    package_path = os.path.join(workspace, "package.json")
    if not os.path.exists(package_path):
        return False, "package.json not found"
    try:
        with open(package_path, encoding="utf-8") as fh:
            package = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"package.json unreadable or invalid: {exc}"
    scripts = package.get("scripts")
    if not isinstance(scripts, dict) or not scripts:
        return False, "package.json has no scripts to validate"
    failures: list[str] = []
    for script_name, command in scripts.items():
        if not isinstance(command, str):
            failures.append(f"script {script_name!r} is not a string")
            continue
        placeholder_reason = _placeholder_package_script_reason(str(script_name), command)
        if placeholder_reason:
            failures.append(placeholder_reason)
            continue
        failures.extend(_missing_package_script_entrypoints(workspace, str(script_name), command, scripts=scripts))
    if failures:
        return False, "; ".join(failures[:3])
    return True, f"{len(scripts)} package scripts have valid local entrypoint references"


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
        elif kind == "ts_syntax":
            ok, detail = _check_ts_syntax(workspace)
        elif kind == "go_compile":
            ok, detail = _check_go_compile(workspace)
        elif kind == "rust_compile":
            ok, detail = _check_rust_compile(workspace)
        elif kind == "cpp_compile":
            ok, detail = _check_cpp_compile(workspace)
        elif kind == "java_compile":
            ok, detail = _check_java_compile(workspace)
        elif kind == "package_scripts":
            ok, detail = _check_package_scripts(workspace)
        elif kind == "runnable_any":
            # Shape-neutral runnability: briefs like "typing tester with live
            # highlighting" are legitimately delivered as either a Python
            # program or a web app (live L2-11 r1: PM chose web, the fixture
            # demanded py_compile). Pass when either shape is fully runnable.
            py_ok, py_detail = _check_py_compile(workspace)
            if py_ok:
                ok, detail = True, f"python shape: {py_detail}"
            else:
                html_ok, html_detail = _check_html(workspace)
                js_ok, js_detail = _check_js_syntax(workspace)
                if html_ok and js_ok:
                    ok, detail = True, f"web shape: {html_detail}; {js_detail}"
                else:
                    ok = False
                    detail = f"no runnable shape: py({py_detail}); web({html_detail}; {js_detail})"
        elif kind.startswith("content_any:"):
            # Feature probe: at least one SOURCE file must mention the pattern.
            # Scaffold files (package.json, tsconfig.json, etc.) are excluded
            # to prevent hollow-scaffold deliveries from passing on metadata
            # keywords alone (e.g. "firefly" in package.json description).
            pattern_text = kind.split(":", 1)[1]
            try:
                probe = re.compile(pattern_text, re.IGNORECASE)
            except re.error as exc:
                results.append({"check": kind, "ok": False, "detail": f"bad pattern: {exc}"})
                continue
            matched_file = ""
            for rel in inventory["source_files"]:
                try:
                    with open(os.path.join(workspace, rel), encoding="utf-8", errors="replace") as fh:
                        if probe.search(fh.read()):
                            matched_file = rel
                            break
                except OSError:
                    continue
            ok = bool(matched_file)
            detail = (
                f"feature pattern {pattern_text!r} found in {matched_file}"
                if ok
                else f"feature pattern {pattern_text!r} not found in any source file"
            )
        elif kind.startswith("source_target_coverage:"):
            # Source target coverage gate: verifies that source files exist under
            # expected directories (e.g. src/**/*.ts). Prevents projects from
            # passing with only scaffold files (package.json, tsconfig.json, README).
            # Format: source_target_coverage:<glob_pattern>
            # Example: source_target_coverage:src/**/*.ts
            target_pattern = kind.split(":", 1)[1].strip()
            if not target_pattern:
                results.append({"check": kind, "ok": False, "detail": "empty source target pattern"})
                continue
            import glob as globmod

            abs_pattern = os.path.join(workspace, target_pattern)
            matched_paths = sorted(globmod.glob(abs_pattern, recursive=True))
            # Filter to actual source files only
            matched_source = [
                os.path.relpath(p, workspace).replace("\\", "/") for p in matched_paths if os.path.isfile(p)
            ]
            ok = bool(matched_source)
            detail = (
                f"source target {target_pattern!r}: {len(matched_source)} file(s) found"
                if ok
                else f"source target {target_pattern!r}: no source files found — "
                "Director only produced scaffold files, not core source code"
            )
        elif kind.startswith("min_files:"):
            try:
                minimum = int(kind.split(":", 1)[1])
            except ValueError:
                minimum = 1
            count = len(inventory["source_files"])
            ok = count >= minimum
            detail = f"{count} source files (need >= {minimum})"
        else:
            detail = f"unknown check kind: {kind}"
        results.append({"check": kind, "ok": ok, "detail": detail})
    return results


def build_factory_audit_record(
    *,
    project: dict[str, Any],
    workspace: str,
    artifact_globs: dict[str, list[str]] | None = None,
    chain_terminal: bool = True,
    chain_status: str = "",
    chain_phase: str = "",
) -> dict[str, Any]:
    """Assemble the full per-project audit record.

    ``artifact_globs`` maps artifact kinds (plan/blueprint/verdict) to lists of
    workspace-relative paths discovered by the runner (the chain's artifact
    layout is runner-configured, not hardcoded here).

    ``chain_terminal`` indicates whether the chain reached a terminal state
    (completed/failed/cancelled) before this audit was taken.  When *False* the
    snapshot is provisional and must NOT be treated as a final project verdict.

    ``chain_status`` and ``chain_phase`` capture the terminal status/phase for
    traceability.
    """
    inventory = collect_workspace_inventory(workspace)
    configured_checks = list(project.get("checks") or [])
    supplemental_checks = []
    if os.path.exists(os.path.join(workspace, "package.json")) and "package_scripts" not in configured_checks:
        supplemental_checks.append("package_scripts")
    checks = run_checks(workspace, configured_checks + supplemental_checks)
    artifacts = artifact_globs or {}

    # Read PM plan and extract declared source targets
    plan = _read_plan_json(workspace)
    declared_source_targets = _extract_declared_source_targets(workspace, plan)
    _, missing_targets = compute_declared_source_target_coverage(workspace, declared_source_targets)

    # Snapshot kind: "terminal" when chain reached a final state, "non_terminal"
    # when the chain was still running / errored before a definitive outcome.
    snapshot_kind = "terminal" if chain_terminal else "non_terminal"

    return {
        "schema_version": FACTORY_AUDIT_SCHEMA_VERSION,
        "project_id": str(project.get("id") or ""),
        "level": int(project.get("level") or 0),
        "domain": str(project.get("domain") or ""),
        "title": str(project.get("title") or ""),
        "code_file_count": len(inventory["code_files"]),
        "source_file_count": len(inventory["source_files"]),
        "code_files": inventory["code_files"][:60],
        "source_files": inventory["source_files"][:60],
        "doc_files": inventory["doc_files"][:30],
        "artifacts": {kind: paths[:10] for kind, paths in artifacts.items()},
        "has_plan_doc": bool(artifacts.get("plan")),
        "has_blueprint_doc": bool(artifacts.get("blueprint")),
        "has_qa_verdict": bool(artifacts.get("verdict")),
        "checks": checks,
        "all_checks_passed": bool(configured_checks) and all(c["ok"] for c in checks),
        "declared_source_targets": declared_source_targets,
        "declared_source_target_count": len(declared_source_targets),
        "missing_declared_source_targets": missing_targets,
        "missing_declared_source_target_count": len(missing_targets),
        "pm_plan_missing_source_targets": plan is not None and not declared_source_targets,
        "audit_snapshot_kind": snapshot_kind,
        "audit_terminal": bool(chain_terminal),
        "terminal_status": chain_status,
        "terminal_phase": chain_phase,
    }


def _read_plan_json(workspace: str) -> dict[str, Any] | None:
    """Read plan.json from workspace, trying multiple candidate paths.

    Returns the parsed JSON dict or None if not found/invalid.
    """
    candidates = [
        os.path.join(workspace, ".polaris", "docs", "product", "plan.json"),
        os.path.join(workspace, ".polaris", "runtime", "tasks", "plan.json"),
        os.path.join(workspace, "tasks", "plan.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                if isinstance(payload, dict):
                    return payload
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
    return None


def _extract_declared_source_targets(workspace: str, plan: dict[str, Any] | None) -> list[str]:
    """Extract declared source file targets from PM plan.

    Scans tasks[].target_files for entries that look like source code paths
    (containing src/ or having a source file extension).
    """
    if not plan:
        return []

    source_extensions = {".py", ".js", ".jsx", ".ts", ".tsx", ".c", ".h", ".cpp", ".rs", ".go", ".java"}
    declared: list[str] = []

    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        return []

    for task in tasks:
        if not isinstance(task, dict):
            continue
        target_files = task.get("target_files") or task.get("files") or task.get("paths") or []
        if not isinstance(target_files, list):
            continue
        for tf in target_files:
            if not isinstance(tf, str) or not tf.strip():
                continue
            tf_clean = tf.strip().replace("\\", "/")
            _, ext = os.path.splitext(tf_clean)
            ext_lower = ext.lower()
            # Include if it has a source extension or is under src/ directory
            if (
                ext_lower in source_extensions or "/src/" in tf_clean or tf_clean.startswith("src/")
            ) and tf_clean not in declared:
                declared.append(tf_clean)

    return sorted(declared)


def compute_declared_source_target_coverage(
    workspace: str,
    declared_targets: list[str],
) -> tuple[list[str], list[str]]:
    """Check which declared source targets exist in workspace.

    Returns (present_targets, missing_targets).
    """
    if not declared_targets:
        return [], []

    present: list[str] = []
    missing: list[str] = []

    for target in declared_targets:
        # Normalize path separators
        normalized = target.replace("\\", "/")
        full_path = os.path.join(workspace, normalized)
        if os.path.isfile(full_path):
            present.append(normalized)
        else:
            missing.append(normalized)

    return present, missing


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
        "with_source_files": sum(1 for r in records if r.get("source_file_count", 0) > 0),
        "zero_source_files": sum(1 for r in records if r.get("source_file_count", 0) == 0),
        "by_level": dict(sorted(by_level.items())),
    }
