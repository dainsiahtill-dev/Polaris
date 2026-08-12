"""Build gates, scaffolding requirements, and LLM route collection/audit."""

from __future__ import annotations

import json
import shlex
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from polaris.kernelone.events.final_request_evidence import normalize_context_snapshot_ref

from ._core import (
    _CPP_SOURCE_SUFFIXES,
    _REQUIRED_LLM_ROLES,
    _ROLE_FAMILIES,
    _as_dict,
    _discover_python_test_files,
    _files_with_suffix,
    _find_html_entrypoint,
    _find_python_entrypoint,
    _has_package_dependencies,
    _load_package_json,
    _norm_role,
    _norm_text,
    _package_declares_dependency,
    _package_has_local_tsc,
    _package_requires_project_typescript,
    _primary_source_language,
    _required_user_verifier_requirement,
    _resolve_go_binary,
    _run_command,
    _run_language_build_gate,
    _run_platform_verifiers,
    _run_python_test_suite,
    _smoke_cpp_cli,
    _smoke_go_cli,
    _smoke_java_cli,
    _smoke_python_cli,
    _smoke_rust_cli,
    _smoke_static_web,
    _which_any,
)

_BUILD_OUTPUT_DIR_NAMES = frozenset({"dist", "build", "out", "bin"})

_SOURCE_FILE_EXTENSIONS = frozenset(
    {
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
)
_SCAFFOLD_FILE_EXTENSIONS = frozenset({".json", ".html", ".css", ".sh", ".sql"})


def _is_build_output_path(path: str) -> bool:
    """Check if a path starts with a build output directory as its first segment."""
    normalized = path.replace("\\", "/")
    clean = normalized
    while clean.startswith("./"):
        clean = clean[2:]
    parts = clean.split("/")
    if not parts:
        return False
    first_segment = parts[0].lower()
    return first_segment in _BUILD_OUTPUT_DIR_NAMES


def _token_references_build_output(token: str) -> bool:
    """Check if a single command token references a build output directory."""
    if "=" in token:
        _, _, value = token.partition("=")
        value = value.strip("'\"")
        if _is_build_output_path(value):
            return True
    return _is_build_output_path(token)


def _has_build_output_path_reference(command: str) -> bool:
    """Check if command contains a build output dir used as a path root."""
    normalized = command.replace("\\", "/")
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        tokens = normalized.split()
    return any(_token_references_build_output(t) for t in tokens)


def _command_serves_build_output(command: str) -> bool:
    """Check if the command is known to serve build output (e.g. vite preview, serve -s dist)."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return False
    idx = 0
    if tokens[0] == "npx" and len(tokens) >= 2:
        idx = 1
    if len(tokens) > idx + 1 and tokens[idx] == "vite" and tokens[idx + 1] == "preview":
        return True
    if tokens[idx] in ("serve", "http-server"):
        remaining = tokens[idx + 1 :]
        return any(_token_references_build_output(t) for t in remaining)
    return False


def _script_depends_on_build_output(scripts: dict[str, Any], script_name: str) -> bool:
    """Check if an npm script's command references build artifact directories.

    Detects build output dirs as path roots (dist, ./dist, dist/index.js),
    flag values (--dir=dist), and known build-serving commands (serve, vite preview).
    Avoids false positives for source paths like scripts/build/start.js.
    """
    command = str(scripts.get(script_name) or "").strip()
    if not command:
        return False
    if _command_serves_build_output(command):
        return True
    return _has_build_output_path_reference(command)


def _any_script_references_build_output(scripts: dict[str, Any], script_names: tuple[str, ...]) -> bool:
    """Check if any of the given npm scripts reference build artifact directories."""
    return any(_script_depends_on_build_output(scripts, name) for name in script_names if name in scripts)


def _build_declared_source_targets_requirement(record: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Build the declared_source_targets_present requirement.

    Checks if PM plan declared source targets and whether they all exist.
    """
    missing_targets = record.get("missing_declared_source_targets") or []
    declared_count = record.get("declared_source_target_count", 0)
    missing_count = record.get("missing_declared_source_target_count", 0)

    # If no declared targets, this is a risk signal but not a hard failure
    if declared_count == 0:
        pm_plan_missing = record.get("pm_plan_missing_source_targets", False)
        if pm_plan_missing:
            return {
                "ok": False,
                "detail": "PM plan has no declared source targets (pm_plan_missing_source_targets)",
            }
        return {
            "ok": True,
            "detail": "no declared source targets in PM plan",
        }

    # If declared targets exist but some are missing, fail
    if missing_count > 0:
        return {
            "ok": False,
            "detail": f"{missing_count} declared source target(s) missing: {', '.join(missing_targets[:5])}",
        }

    return {
        "ok": True,
        "detail": f"all {declared_count} declared source target(s) present",
    }


def _build_scaffolding_requirement(workspace: Path, code_files: list[str]) -> dict[str, Any]:
    """Check that TypeScript/Web projects have required scaffolding files.

    TypeScript projects must have package.json and tsconfig.json.
    Node.js/JS-only projects (no HTML) must have package.json.
    Static web projects (HTML + JS) don't require package.json.
    """
    has_ts = _files_with_suffix(code_files, (".ts", ".tsx"))
    has_js = _files_with_suffix(code_files, (".js", ".jsx", ".mjs", ".cjs"))
    has_html = [rel for rel in code_files if rel.lower().endswith(".html")]
    # TypeScript always needs npm scaffolding; JS-only (no HTML) needs it too;
    # JS + HTML is a static web project that doesn't need package.json.
    needs_package = bool(has_ts) or (bool(has_js) and not bool(has_html))
    missing: list[str] = []

    if needs_package:
        package_json = workspace / "package.json"
        if not package_json.is_file():
            missing.append("package.json")

    if has_ts:
        tsconfig_json = workspace / "tsconfig.json"
        if not tsconfig_json.is_file():
            missing.append("tsconfig.json")

    if has_html:
        html_entry = _find_html_entrypoint(workspace, code_files)
        if not html_entry:
            missing.append("index.html")

    if missing:
        return {
            "ok": False,
            "detail": f"missing required scaffolding: {', '.join(missing)}",
        }
    parts: list[str] = []
    if needs_package:
        parts.append("package.json present")
    if has_ts:
        parts.append("tsconfig.json present")
    if has_html:
        parts.append("HTML entrypoint present")
    if not parts:
        parts.append("no scaffolding required for this project type")
    return {
        "ok": True,
        "detail": "; ".join(parts),
    }


def build_real_run_gate(workspace: Path, record: dict[str, Any], *, timeout_s: int = 60) -> dict[str, Any]:
    """Run the platform's real-runnability gate for one generated project."""
    code_files = [str(item) for item in record.get("code_files") or []]
    source_files = [rel for rel in code_files if Path(rel).suffix.lower() in _SOURCE_FILE_EXTENSIONS]
    html_css_only = code_files and all(Path(rel).suffix.lower() in {".html", ".css"} for rel in code_files)
    scaffold_only = code_files and not source_files and not html_css_only
    source_files_ok = bool(source_files) or bool(html_css_only)
    commands: list[dict[str, Any]] = []
    package = _load_package_json(workspace)
    scripts = _as_dict(package.get("scripts"))

    environment_ok = False
    environment_detail = "no environment preparation ran"
    if package:
        npm = shutil.which("npm")
        if (
            npm
            and _package_requires_project_typescript(workspace, package, scripts, code_files)
            and not _package_declares_dependency(package, "typescript")
            and not _package_has_local_tsc(workspace)
        ):
            environment_detail = "package.json missing devDependency 'typescript' for TypeScript build"
        elif npm and _has_package_dependencies(package) and not (workspace / "node_modules").exists():
            install = _run_command(
                [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund"],
                workspace,
                timeout_s=max(30, int(timeout_s)),
            )
            install["phase"] = "environment"
            commands.append(install)
            environment_ok = bool(install.get("ok"))
            environment_detail = "npm dependencies installed" if environment_ok else "npm install failed"
        elif npm:
            environment_ok = True
            environment_detail = "npm available; no dependency install required"
        else:
            environment_detail = "npm unavailable for package.json project"
    elif any(rel.endswith(".py") for rel in code_files):
        environment_ok = True
        environment_detail = f"python executable available: {sys.executable}"
    elif any(rel.endswith(".html") for rel in code_files):
        environment_ok = True
        environment_detail = "static web project has no dependency manifest"
    elif _files_with_suffix(code_files, (".go",)):
        environment_ok = bool(_resolve_go_binary())
        environment_detail = "go toolchain available" if environment_ok else "go toolchain unavailable"
    elif _files_with_suffix(code_files, (".rs",)):
        environment_ok = bool(_which_any("cargo", "rustc"))
        environment_detail = "rust toolchain available" if environment_ok else "rust toolchain unavailable"
    elif _files_with_suffix(code_files, _CPP_SOURCE_SUFFIXES):
        environment_ok = bool(_which_any("g++", "c++"))
        environment_detail = "C++ compiler available" if environment_ok else "g++/c++ unavailable"
    elif _files_with_suffix(code_files, (".java",)):
        environment_ok = bool(shutil.which("javac") and shutil.which("java"))
        environment_detail = "Java toolchain available" if environment_ok else "javac/java unavailable"
    elif _files_with_suffix(code_files, (".ts", ".tsx")):
        environment_ok = bool(shutil.which("tsc"))
        environment_detail = "TypeScript compiler available" if environment_ok else "tsc unavailable"

    build_command_ok = False
    build_detail = "no build/test/lint command was discovered"
    package_script_failed = False
    if package and shutil.which("npm") and environment_ok:
        has_build_script = "build" in scripts
        has_ts_files = _files_with_suffix(code_files, (".ts", ".tsx"))

        build_cmd_str = str(scripts.get("build") or "")
        has_build_output_ref = _any_script_references_build_output(scripts, ("test", "start", "check", "lint"))
        should_build_first = has_build_script and (has_ts_files or has_build_output_ref or "tsc" in build_cmd_str)
        if should_build_first:
            cmd = _run_command(["npm", "run", "build"], workspace, timeout_s=max(10, int(timeout_s)))
            cmd["phase"] = "build_test_lint"
            cmd["script"] = "build"
            commands.append(cmd)
            npm_build_ok = bool(cmd.get("ok"))
            # True-run pillar: at least ONE of build/test/lint must succeed.
            # Do not let a failing test script erase a green build — that also
            # falsely blocked entrypoint_smoke when start depends on build output
            # (r126 L1-01: tsc passed, npm start worked, but test dir missing).
            build_command_ok = npm_build_ok
            package_script_failed = not npm_build_ok
            if npm_build_ok:
                build_detail = "npm run build passed"
                quality_parts: list[str] = ["build"]
                for script_name in ("test", "lint", "check"):
                    if script_name not in scripts:
                        continue
                    quality_cmd = _run_command(
                        ["npm", "run", script_name], workspace, timeout_s=max(10, int(timeout_s))
                    )
                    quality_cmd["phase"] = "build_test_lint"
                    quality_cmd["script"] = script_name
                    commands.append(quality_cmd)
                    if quality_cmd.get("ok"):
                        quality_parts.append(script_name)
                        build_detail = "npm run " + " and npm run ".join(quality_parts) + " passed"
                    else:
                        # Keep build_command_ok True (build already green). Record residual.
                        build_detail = (
                            f"npm run build passed; npm run {script_name} failed "
                            f"(build_test_lint pillar still satisfied by build)"
                        )
                    break
            else:
                stderr = str(cmd.get("stderr_tail") or "")
                build_detail = "npm run build failed" + (f": {stderr}" if stderr else "")
        else:
            for script_name in ("test", "build", "lint", "check"):
                if script_name in scripts:
                    cmd = _run_command(["npm", "run", script_name], workspace, timeout_s=max(10, int(timeout_s)))
                    cmd["phase"] = "build_test_lint"
                    cmd["script"] = script_name
                    commands.append(cmd)
                    build_command_ok = bool(cmd.get("ok"))
                    package_script_failed = not build_command_ok
                    build_detail = f"npm run {script_name} {'passed' if build_command_ok else 'failed'}"
                    break
    python_test_files = _discover_python_test_files(workspace, code_files)
    primary_lang = _primary_source_language(code_files)
    # Skip the Python compileall/test path when the project is primarily a
    # compiled-language project (Go, Rust, …) that happens to include a
    # Python contract-verification test.  Running ``python -m unittest`` on a
    # Go project's contract test would fail on symbol mismatches and mask the
    # real Go build gate result.
    _skip_python_for_non_python_project = primary_lang in ("go", "rust", "java", "cpp")
    if (
        not build_command_ok
        and not package_script_failed
        and not _skip_python_for_non_python_project
        and any(rel.endswith(".py") for rel in code_files)
    ):
        cmd = _run_command(
            [sys.executable, "-m", "compileall", "-q", "."], workspace, timeout_s=max(10, int(timeout_s))
        )
        cmd["phase"] = "build_test_lint"
        commands.append(cmd)
        build_command_ok = bool(cmd.get("ok"))
        build_detail = "python compileall passed" if build_command_ok else "python compileall failed"
        if build_command_ok and python_test_files:
            test_commands = _run_python_test_suite(workspace, python_test_files, timeout_s=timeout_s)
            for test_cmd in test_commands:
                test_cmd["phase"] = "build_test_lint"
                commands.append(test_cmd)
            test_cmd = test_commands[-1]
            build_command_ok = bool(test_cmd.get("ok"))
            if build_command_ok:
                runner = str(test_cmd.get("runner") or "tests")
                build_detail = f"python compileall and {runner} passed ({len(python_test_files)} test file(s))"
            else:
                runner = str(test_cmd.get("runner") or "python tests")
                build_detail = str(test_cmd.get("detail") or f"python {runner} failed")
    if (
        not build_command_ok
        and not package_script_failed
        and any(rel.endswith((".js", ".mjs", ".cjs")) for rel in code_files)
        and shutil.which("node")
    ):
        js_files = [rel for rel in code_files if rel.endswith((".js", ".mjs", ".cjs")) and not rel.endswith(".min.js")]
        failures: list[str] = []
        for rel in js_files[:20]:
            cmd = _run_command(["node", "--check", rel], workspace, timeout_s=max(5, min(30, int(timeout_s))))
            cmd["phase"] = "build_test_lint"
            commands.append(cmd)
            if not cmd.get("ok"):
                failures.append(rel)
        build_command_ok = bool(js_files) and not failures
        build_detail = "node --check passed" if build_command_ok else f"node --check failed: {', '.join(failures[:3])}"
    if not build_command_ok and not package_script_failed:
        language_ok, language_detail, language_commands = _run_language_build_gate(
            workspace,
            code_files,
            timeout_s=timeout_s,
        )
        commands.extend(language_commands)
        if language_detail != "no language build command was discovered":
            build_command_ok = language_ok
            build_detail = language_detail

    entrypoint: dict[str, Any] = {"ok": False, "kind": "", "detail": "no CLI/Web/API entrypoint discovered"}
    html_entry = _find_html_entrypoint(workspace, code_files)
    if html_entry:
        entrypoint = _smoke_static_web(workspace, html_entry, timeout_s=timeout_s)
    elif package and shutil.which("npm") and "start" in scripts:
        start_needs_build = _script_depends_on_build_output(scripts, "start")
        # Entrypoint depends on the *build script* outcome only — never on test/lint.
        # Coupling entrypoint to aggregate build_test_lint hid true CLI/Web success
        # when only tests were missing (r126: npm start rc=0 while gate said smoke fail).
        build_script_succeeded = any(
            str(cmd.get("script") or "") == "build" and bool(cmd.get("ok")) for cmd in commands
        )
        build_was_attempted = any(str(cmd.get("script") or "") == "build" for cmd in commands)
        if start_needs_build and not build_script_succeeded:
            fail_detail = "build did not succeed"
            if build_was_attempted:
                build_fail_cmds = [
                    cmd for cmd in commands if str(cmd.get("script") or "") == "build" and not bool(cmd.get("ok"))
                ]
                if build_fail_cmds:
                    fail_detail = str(
                        build_fail_cmds[-1].get("stderr_tail")
                        or build_fail_cmds[-1].get("detail")
                        or "npm run build failed"
                    )
            entrypoint = {
                "kind": "npm_start",
                "entrypoint": "npm run start",
                "ok": False,
                "detail": f"npm start depends on build output but {fail_detail}",
            }
        else:
            cmd = _run_command(["npm", "run", "start"], workspace, timeout_s=min(max(3, int(timeout_s)), 8))
            # npm start timeout 不得直接算成功；server 项目需要端口/health probe 或明确启动成功证据
            has_success_evidence = bool(cmd.get("ok")) and not bool(cmd.get("timeout"))
            entrypoint = {
                "kind": "npm_start",
                "entrypoint": "npm run start",
                "ok": has_success_evidence,
                "detail": "npm run start completed successfully"
                if has_success_evidence
                else "npm run start timed out or failed",
                **cmd,
            }
    else:
        # Determine the primary source language so that a Go project with a
        # stray ``tests/test_*.py`` doesn't get the Python CLI smoke path.
        _ep_lang = primary_lang if primary_lang else _primary_source_language(code_files)
        if _ep_lang == "go" and _files_with_suffix(code_files, (".go",)):
            entrypoint = _smoke_go_cli(workspace, code_files, timeout_s=timeout_s)
        elif _ep_lang == "rust" and _files_with_suffix(code_files, (".rs",)):
            entrypoint = _smoke_rust_cli(workspace, code_files, timeout_s=timeout_s)
        elif _ep_lang == "java" and _files_with_suffix(code_files, (".java",)):
            entrypoint = _smoke_java_cli(workspace, code_files, timeout_s=timeout_s)
        elif _ep_lang == "cpp" and _files_with_suffix(code_files, _CPP_SOURCE_SUFFIXES):
            entrypoint = _smoke_cpp_cli(workspace, code_files, timeout_s=timeout_s)
        else:
            py_entry = _find_python_entrypoint(workspace, code_files)
            if py_entry:
                entrypoint = _smoke_python_cli(workspace, py_entry, timeout_s=timeout_s)
            elif _files_with_suffix(code_files, (".go",)):
                entrypoint = _smoke_go_cli(workspace, code_files, timeout_s=timeout_s)
            elif _files_with_suffix(code_files, (".rs",)):
                entrypoint = _smoke_rust_cli(workspace, code_files, timeout_s=timeout_s)
            elif _files_with_suffix(code_files, _CPP_SOURCE_SUFFIXES):
                entrypoint = _smoke_cpp_cli(workspace, code_files, timeout_s=timeout_s)
            elif _files_with_suffix(code_files, (".java",)):
                entrypoint = _smoke_java_cli(workspace, code_files, timeout_s=timeout_s)

    if (
        not build_command_ok
        and bool(entrypoint.get("ok"))
        and str(entrypoint.get("kind") or "") in ("web_static", "web_playwright")
        and not package
        and code_files
        and all(rel.endswith((".html", ".css")) for rel in code_files)
    ):
        build_command_ok = True
        build_detail = "static HTML/CSS entrypoint smoke passed"

    requirements = {
        "artifact_landed": {
            "ok": bool(code_files),
            "detail": f"{len(code_files)} generated code file(s)",
        },
        "source_files_present": {
            "ok": source_files_ok,
            "detail": (
                f"{len(source_files)} source file(s)"
                if source_files
                else (
                    "pure HTML/CSS web project (no business-logic source files)"
                    if html_css_only
                    else f"scaffold-only delivery: {len(code_files)} code file(s) but zero source files "
                    "(only config/metadata like package.json, tsconfig.json)"
                )
            ),
        },
        "declared_source_targets_present": _build_declared_source_targets_requirement(record, workspace),
        "scaffolding_present": _build_scaffolding_requirement(workspace, code_files),
        "environment_prepared": {"ok": environment_ok, "detail": environment_detail},
        "build_test_lint_ran": {"ok": build_command_ok, "detail": build_detail},
        "entrypoint_smoke": {
            "ok": bool(entrypoint.get("ok")),
            "detail": str(entrypoint.get("detail") or entrypoint.get("stderr_tail") or entrypoint.get("kind") or ""),
            "kind": str(entrypoint.get("kind") or ""),
        },
    }
    ok = all(bool(item.get("ok")) for item in requirements.values())
    failing = [name for name, item in requirements.items() if not item.get("ok")]
    result: dict[str, Any] = {
        "ok": ok,
        "requirements": requirements,
        "commands": commands[-12:],
        "command_count_total": len(commands),
        "commands_truncated": len(commands) > 12,
        "entrypoint": entrypoint,
        "summary": "real run gate passed" if ok else "real run gate failed: " + ", ".join(failing),
    }
    verifier_patch = _run_platform_verifiers(workspace, timeout_s=timeout_s)
    if verifier_patch:
        verifier_requirement = _required_user_verifier_requirement(verifier_patch)
        if verifier_requirement is not None:
            requirements["user_verifiers"] = verifier_requirement
            if not bool(verifier_requirement.get("ok")):
                result["ok"] = False
                result["summary"] = "real run gate failed: user_verifiers"
        result.update(verifier_patch)
    if scaffold_only:
        result["missing_source_targets"] = {
            "code_file_count": len(code_files),
            "source_file_count": 0,
            "scaffold_files": code_files[:10],
            "detail": "Director produced only scaffold files (package.json, tsconfig.json, etc.) "
            "with zero source code files",
        }
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _nested_dict(value: Any, *keys: str) -> dict[str, Any]:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _first_string(*values: Any) -> str:
    for value in values:
        token = _norm_text(value)
        if token:
            return token
    return ""


def _normalize_llm_event(raw: dict[str, Any], *, source_path: str = "") -> dict[str, Any] | None:
    data = _as_dict(raw.get("data"))
    payload = _as_dict(raw.get("payload"))
    meta = _as_dict(raw.get("meta"))
    metadata = _as_dict(raw.get("metadata"))
    data_metadata = _as_dict(data.get("metadata"))
    extra_fields = _as_dict(data_metadata.get("extra_fields"))
    tokens = _as_dict(raw.get("tokens"))
    audit_refs = _as_dict(raw.get("audit_refs"))
    final_request_evidence = _as_dict(raw.get("final_request_evidence"))
    if not final_request_evidence:
        final_request_evidence = _as_dict(data.get("final_request_evidence"))
    if not final_request_evidence:
        final_request_evidence = _as_dict(data_metadata.get("final_request_evidence"))
    final_request_evidence_authority = _as_dict(final_request_evidence.get("final_request_evidence_authority"))
    final_request_context_audit = _as_dict(raw.get("final_request_context_audit"))
    if not final_request_context_audit:
        final_request_context_audit = _as_dict(data.get("final_request_context_audit"))
    if not final_request_context_audit:
        final_request_context_audit = _as_dict(data_metadata.get("final_request_context_audit"))
    context_snapshot_ref = normalize_context_snapshot_ref(
        _first_string(
            raw.get("context_snapshot_ref"),
            data.get("context_snapshot_ref"),
            metadata.get("context_snapshot_ref"),
            data_metadata.get("context_snapshot_ref"),
            extra_fields.get("context_snapshot_ref"),
            audit_refs.get("context_snapshot_ref"),
            final_request_evidence.get("context_snapshot_ref"),
        )
    )

    event_name = _first_string(
        raw.get("event_type"), raw.get("event"), raw.get("type"), raw.get("name"), data.get("event_type")
    )
    role = _norm_role(_first_string(raw.get("role"), data.get("role"), payload.get("role"), meta.get("role")))
    provider = _first_string(
        raw.get("provider_id"),
        raw.get("provider"),
        data.get("provider_id"),
        data.get("provider"),
        metadata.get("provider_id"),
        metadata.get("provider"),
        data_metadata.get("provider_id"),
        data_metadata.get("provider"),
        extra_fields.get("provider_id"),
        extra_fields.get("provider"),
    )
    model = _first_string(
        raw.get("model"),
        data.get("model"),
        metadata.get("model"),
        data_metadata.get("model"),
        extra_fields.get("model"),
    )
    binding_id = _first_string(
        raw.get("binding_id"), data.get("binding_id"), data_metadata.get("binding_id"), extra_fields.get("binding_id")
    )
    source = _first_string(
        raw.get("source"),
        data.get("source"),
        metadata.get("source"),
        data_metadata.get("source"),
        extra_fields.get("source"),
    )
    lowered_source = source.lower()
    if lowered_source == "llm":
        source = "llm"
    elif "llm" not in lowered_source:
        metadata_source = _norm_text(data_metadata.get("source"))
        if metadata_source.lower() == "llm" or "llm" in event_name.lower():
            source = "llm"
            lowered_source = "llm"
    cache_hit = bool(
        raw.get("cache_hit")
        or data.get("cache_hit")
        or metadata.get("cache_hit")
        or data_metadata.get("cache_hit")
        or extra_fields.get("cache_hit")
        or data_metadata.get("cached")
        or lowered_source == "cache"
    )
    prompt_tokens = data.get("prompt_tokens", tokens.get("prompt"))
    completion_tokens = data.get("completion_tokens", tokens.get("completion"))
    total_tokens = tokens.get("total")
    if total_tokens is None:
        try:
            total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
        except (TypeError, ValueError):
            total_tokens = None

    marker = " ".join([event_name, str(raw.get("kind") or ""), str(raw.get("channel") or "")]).lower()
    if not role and "llm" not in marker and not event_name.startswith("invoke"):
        return None
    if not role:
        role = "unknown"
    lowered_event = event_name.lower()
    terminal = lowered_event in {
        "llm_call_end",
        "llm_error",
        "call_end",
        "call_error",
        "invoke_end",
        "error",
        "llm_route_terminal",
    }
    raw_invocation = raw.get("invocation")
    if isinstance(raw_invocation, bool):
        invocation = raw_invocation
    else:
        invocation = terminal or "llm" in lowered_event or lowered_event.startswith("invoke")
    skipped = bool(raw.get("skipped") or data.get("skipped") or metadata.get("skipped") or data_metadata.get("skipped"))
    fail_closed = bool(
        raw.get("fail_closed")
        or data.get("fail_closed")
        or metadata.get("fail_closed")
        or data_metadata.get("fail_closed")
    )
    skip_reason = _first_string(
        raw.get("skip_reason"),
        data.get("skip_reason"),
        metadata.get("skip_reason"),
        data_metadata.get("skip_reason"),
        extra_fields.get("skip_reason"),
    )
    return {
        "event": event_name,
        "role": role,
        "provider_id": provider,
        "model": model,
        "binding_id": binding_id,
        "source": source,
        "cache_hit": cache_hit,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "terminal": terminal,
        "invocation": invocation,
        "skipped": skipped,
        "skip_reason": skip_reason,
        "fail_closed": fail_closed,
        "context_snapshot_ref": context_snapshot_ref,
        "final_request_context_audit_present": bool(
            final_request_context_audit or final_request_evidence.get("final_request_context_audit_present")
        ),
        "final_request_context_audit_hash": _first_string(
            raw.get("final_request_context_audit_hash"),
            data.get("final_request_context_audit_hash"),
            audit_refs.get("final_request_context_audit_hash"),
            final_request_evidence.get("final_request_context_audit_hash"),
        ),
        "final_request_evidence_hash": _first_string(
            raw.get("final_request_evidence_hash"),
            data.get("final_request_evidence_hash"),
            audit_refs.get("final_request_evidence_hash"),
            final_request_evidence.get("final_request_evidence_hash"),
        ),
        "final_request_evidence_authority_hash": _first_string(
            raw.get("final_request_evidence_authority_hash"),
            data.get("final_request_evidence_authority_hash"),
            audit_refs.get("final_request_evidence_authority_hash"),
            final_request_evidence.get("final_request_evidence_authority_hash"),
            final_request_evidence_authority.get("final_request_evidence_authority_hash"),
        ),
        "final_request_evidence_coverage_pass": final_request_evidence.get("final_request_evidence_coverage_pass"),
        "role_id": _first_string(
            final_request_evidence.get("role_id"), final_request_evidence_authority.get("role_id")
        ),
        "expected_role_id": _first_string(
            final_request_evidence.get("expected_role_id"),
            final_request_evidence_authority.get("expected_role_id"),
        ),
        "role_identity_ok": final_request_evidence.get(
            "role_identity_ok", final_request_evidence_authority.get("role_identity_ok")
        ),
        "required_refs": final_request_evidence.get("required_refs")
        if isinstance(final_request_evidence.get("required_refs"), list)
        else final_request_evidence_authority.get("required_refs")
        if isinstance(final_request_evidence_authority.get("required_refs"), list)
        else [],
        "included_refs": final_request_evidence.get("included_refs")
        if isinstance(final_request_evidence.get("included_refs"), list)
        else final_request_evidence_authority.get("included_refs")
        if isinstance(final_request_evidence_authority.get("included_refs"), list)
        else [],
        "missing_required_refs": final_request_evidence.get("missing_required_refs")
        if isinstance(final_request_evidence.get("missing_required_refs"), list)
        else [],
        "required_tools": final_request_evidence.get("required_tools")
        if isinstance(final_request_evidence.get("required_tools"), list)
        else final_request_evidence_authority.get("required_tools")
        if isinstance(final_request_evidence_authority.get("required_tools"), list)
        else [],
        "available_tools": final_request_evidence.get("available_tools")
        if isinstance(final_request_evidence.get("available_tools"), list)
        else final_request_evidence_authority.get("available_tools")
        if isinstance(final_request_evidence_authority.get("available_tools"), list)
        else [],
        "missing_required_tools": final_request_evidence.get("missing_required_tools")
        if isinstance(final_request_evidence.get("missing_required_tools"), list)
        else [],
        "unexpected_tool_pruning": final_request_evidence.get("unexpected_tool_pruning")
        if isinstance(final_request_evidence.get("unexpected_tool_pruning"), list)
        else final_request_evidence_authority.get("unexpected_tool_pruning")
        if isinstance(final_request_evidence_authority.get("unexpected_tool_pruning"), list)
        else [],
        "tool_schema_registry_coverage": _as_dict(
            final_request_evidence.get("tool_schema_registry_coverage")
            or final_request_evidence_authority.get("tool_schema_registry_coverage")
        ),
        "workflow_chain": _as_dict(
            final_request_evidence.get("workflow_chain") or final_request_evidence_authority.get("workflow_chain")
        ),
        "source_path": source_path,
        "raw": raw,
    }


def _resolve_polaris_roots_runtime_dir(workspace: Path) -> Path | None:
    """Resolve the canonical runtime_root via resolve_polaris_roots if available."""
    try:
        from polaris.cells.storage.layout import resolve_polaris_roots

        roots = resolve_polaris_roots(str(workspace))
        runtime_root = roots.runtime_root
        if runtime_root:
            return Path(runtime_root)
    except (ImportError, RuntimeError, ValueError, OSError):
        pass
    return None


def _append_dispatch_route_events(
    normalized: list[dict[str, Any]],
    dispatch_data: dict[str, Any],
    *,
    source_path: str,
) -> None:
    """Append normalized LLM route events embedded in a Director dispatch log."""
    for raw in dispatch_data.get("fail_closed_route_events") or []:
        if not isinstance(raw, dict):
            continue
        item = _normalize_llm_event(raw, source_path=source_path)
        if item is not None:
            item["fail_closed"] = True
            normalized.append(item)
    for raw in dispatch_data.get("per_binding_route_events") or []:
        if not isinstance(raw, dict):
            continue
        item = _normalize_llm_event(raw, source_path=source_path)
        if item is not None:
            normalized.append(item)


def collect_llm_events(
    workspace: Path,
    runtime_dir: Path | Iterable[Path] | None,
    audit_bundle: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Collect normalized LLM invocation evidence from runtime artifacts."""
    candidates: set[Path] = set()
    if runtime_dir is None:
        runtime_dirs: list[Path] = []
    elif isinstance(runtime_dir, Path):
        runtime_dirs = [runtime_dir]
    else:
        runtime_dirs = [path for path in runtime_dir if isinstance(path, Path)]
    extra_bases: list[Path] = [
        workspace / ".polaris" / "runtime",
        workspace / ".polaris",
    ]
    polaris_roots_runtime = _resolve_polaris_roots_runtime_dir(workspace)
    if polaris_roots_runtime is not None:
        extra_bases.insert(0, polaris_roots_runtime)
    for base in (*runtime_dirs, *extra_bases):
        if base is None:
            continue
        candidates.update(base.glob("events/*.llm.events.jsonl"))
        candidates.update(base.glob("telemetry/events_*.jsonl"))
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(candidates):
        for raw in _read_jsonl(path):
            item = _normalize_llm_event(raw, source_path=str(path))
            if item is None:
                continue
            key = json.dumps(
                {
                    "event": item.get("event"),
                    "role": item.get("role"),
                    "provider_id": item.get("provider_id"),
                    "model": item.get("model"),
                    "binding_id": item.get("binding_id"),
                    "tokens": item.get("total_tokens"),
                    "source_path": item.get("source_path"),
                    "event_id": raw.get("event_id"),
                    "seq": raw.get("seq"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)

    bundle = audit_bundle if isinstance(audit_bundle, dict) else {}
    for raw in bundle.get("events_tail") or []:
        if not isinstance(raw, dict):
            continue
        item = _normalize_llm_event(raw, source_path="audit_bundle.events_tail")
        if item is not None:
            normalized.append(item)
        result_payload = _as_dict(raw.get("result"))
        if result_payload:
            _append_dispatch_route_events(
                normalized,
                result_payload,
                source_path="audit_bundle.events_tail.result",
            )

    for base in (*runtime_dirs, *extra_bases):
        if base is None:
            continue
        dispatch_dir = base / "dispatch"
        if not dispatch_dir.is_dir():
            continue
        dispatch_logs = {dispatch_dir / "log.json"}
        dispatch_logs.update(dispatch_dir.glob("*.log.json"))
        for dispatch_log in sorted(path for path in dispatch_logs if path.exists()):
            try:
                dispatch_data = json.loads(dispatch_log.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(dispatch_data, dict):
                continue
            _append_dispatch_route_events(normalized, dispatch_data, source_path=str(dispatch_log))
    return normalized


def resolve_expected_llm_bindings(roles: tuple[str, ...] = _REQUIRED_LLM_ROLES) -> dict[str, list[dict[str, Any]]]:
    """Resolve actual configured role bindings from the runtime LLM config."""
    expected: dict[str, list[dict[str, Any]]] = {}
    try:
        from polaris.kernelone.llm.runtime_config import load_role_config, resolve_role_worker_plan
    except (ImportError, RuntimeError, ValueError):
        return expected
    for role in roles:
        normalized = _norm_role(role)
        rows: list[dict[str, Any]] = []
        try:
            if normalized == "director":
                slots = resolve_role_worker_plan(normalized)
                provider_ids = tuple(dict.fromkeys(str(slot.provider_id) for slot in slots if slot.provider_id))
                try:
                    from polaris.cells.orchestration.pm_dispatch.public.service import (
                        reachable_provider_pool,
                    )

                    live_provider_ids = set(reachable_provider_pool(provider_ids))
                except (ImportError, RuntimeError, ValueError, TypeError, OSError):
                    live_provider_ids = set()
                if live_provider_ids:
                    slots = [slot for slot in slots if str(slot.provider_id) in live_provider_ids]
                seen_routes: set[str] = set()
                for slot in slots:
                    route_key = f"{slot.provider_id}|{slot.model}"
                    if route_key in seen_routes:
                        continue
                    seen_routes.add(route_key)
                    rows.append(
                        {
                            "role": normalized,
                            "provider_id": slot.provider_id,
                            "model": slot.model,
                            "binding_id": slot.binding_id,
                            "slot_index": slot.slot_index,
                        }
                    )
            else:
                role_config = load_role_config(normalized)
                if role_config is not None and role_config.bindings:
                    rows = [
                        {
                            "role": normalized,
                            "provider_id": binding.provider_id,
                            "model": binding.model,
                            "binding_id": binding.binding_id,
                            "binding_index": binding.binding_index,
                        }
                        for binding in role_config.bindings
                    ]
                elif role_config is not None:
                    rows = [
                        {
                            "role": normalized,
                            "provider_id": role_config.provider_id,
                            "model": role_config.model,
                            "binding_id": "",
                        }
                    ]
        except (RuntimeError, ValueError, TypeError, OSError):
            rows = []
        expected[normalized] = rows
    return expected


def _binding_key(row: dict[str, Any]) -> str:
    provider = _norm_text(row.get("provider_id") or row.get("provider"))
    model = _norm_text(row.get("model"))
    binding_id = _norm_text(row.get("binding_id"))
    if binding_id:
        return f"{provider}|{model}|{binding_id}"
    return f"{provider}|{model}"


def _loose_binding_key(row: dict[str, Any]) -> str:
    return f"{_norm_text(row.get('provider_id') or row.get('provider'))}|{_norm_text(row.get('model'))}"


def _matches_family(role: str, row: dict[str, Any]) -> bool:
    alternatives = _ROLE_FAMILIES.get(role)
    if not alternatives:
        return True
    haystack = f"{row.get('provider_id') or row.get('provider') or ''} {row.get('model') or ''}".lower()
    return any(all(token in haystack for token in alternative) for alternative in alternatives)


def _is_real_llm_route_event(event: dict[str, Any]) -> bool:
    source = _norm_text(event.get("source"))
    data = _as_dict(event.get("data"))
    data_meta = _as_dict(data.get("metadata"))
    if not source or source.lower() != "llm":
        source = _norm_text(data_meta.get("source"))
    provider = _norm_text(event.get("provider_id") or event.get("provider"))
    model = _norm_text(event.get("model"))
    if not model:
        model = _norm_text(data.get("model"))
    cache_hit = bool(event.get("cache_hit") or data_meta.get("cached"))
    if event.get("skipped") or event.get("fail_closed"):
        return False
    return bool(event.get("invocation") and source.lower() == "llm" and not cache_hit and provider and model)


def _is_llm_route_skip_event(event: dict[str, Any]) -> bool:
    source = _norm_text(event.get("source"))
    data = _as_dict(event.get("data"))
    data_meta = _as_dict(data.get("metadata"))
    if not source or source.lower() != "llm":
        source = _norm_text(data_meta.get("source"))
    provider = _norm_text(event.get("provider_id") or event.get("provider"))
    model = _norm_text(event.get("model") or data.get("model"))
    reason = _norm_text(event.get("skip_reason") or data_meta.get("skip_reason"))
    allowed_reasons = {
        "provider_connectivity_unavailable",
        "provider_unreachable",
        "provider_readiness_failed",
        "role_binding_cooldown",
        "binding_unavailable",
    }
    return bool(source.lower() == "llm" and provider and model and event.get("skipped") and reason in allowed_reasons)


def _resolve_provider_from_expected(
    event: dict[str, Any],
    expected_bindings: dict[str, list[dict[str, Any]]],
) -> bool:
    if _norm_text(event.get("provider_id") or event.get("provider")):
        return False
    model = _norm_text(event.get("model"))
    if not model:
        data = _as_dict(event.get("data"))
        model = _norm_text(data.get("model"))
        if model:
            event["model"] = model
    if not model:
        return False
    role = _norm_role(event.get("role"))
    candidates = [
        row
        for row in expected_bindings.get(role, [])
        if _norm_text(row.get("model")) == model and _norm_text(row.get("provider_id") or row.get("provider"))
    ]
    if len(candidates) == 1:
        match = candidates[0]
        event["provider_id"] = _norm_text(match.get("provider_id") or match.get("provider"))
        binding_id = _norm_text(match.get("binding_id"))
        if binding_id:
            event["binding_id"] = binding_id
        return True
    return False


def build_llm_route_audit(
    events: list[dict[str, Any]],
    *,
    expected_bindings: dict[str, list[dict[str, Any]]] | None = None,
    required_roles: tuple[str, ...] = _REQUIRED_LLM_ROLES,
    require_all_director_routes: bool = True,
) -> dict[str, Any]:
    expected = (
        expected_bindings if isinstance(expected_bindings, dict) else resolve_expected_llm_bindings(required_roles)
    )
    candidate_events = [
        event for event in events if event.get("invocation") and _norm_role(event.get("role")) in required_roles
    ]
    for event in candidate_events:
        _resolve_provider_from_expected(event, expected)
    evidence = [event for event in candidate_events if _is_real_llm_route_event(event)]
    terminal = [event for event in evidence if event.get("terminal")]
    diagnostic_events = [
        event for event in events if _norm_role(event.get("role")) in required_roles and _is_llm_route_skip_event(event)
    ]
    by_role: dict[str, list[dict[str, Any]]] = {}
    for event in terminal or evidence:
        by_role.setdefault(_norm_role(event.get("role")), []).append(event)
    diagnostic_by_role: dict[str, list[dict[str, Any]]] = {}
    for event in diagnostic_events:
        _resolve_provider_from_expected(event, expected)
        diagnostic_by_role.setdefault(_norm_role(event.get("role")), []).append(event)

    role_results: dict[str, dict[str, Any]] = {}
    ok = True
    for role in required_roles:
        normalized = _norm_role(role)
        configured = list(expected.get(normalized) or [])
        observed = list(by_role.get(normalized) or [])
        configured_keys = {_binding_key(row) for row in configured if _loose_binding_key(row) != "|"}
        configured_loose = {_loose_binding_key(row) for row in configured if _loose_binding_key(row) != "|"}
        observed_keys = {_binding_key(row) for row in observed if _loose_binding_key(row) != "|"}
        observed_loose = {_loose_binding_key(row) for row in observed if _loose_binding_key(row) != "|"}
        skipped = list(diagnostic_by_role.get(normalized) or [])
        skipped_keys = {_binding_key(row) for row in skipped if _loose_binding_key(row) != "|"}
        skipped_loose = {_loose_binding_key(row) for row in skipped if _loose_binding_key(row) != "|"}
        missing = sorted(
            key
            for key in configured_keys
            if key not in observed_keys
            and key not in skipped_keys
            and key.rsplit("|", 1)[0] not in observed_loose
            and key.rsplit("|", 1)[0] not in skipped_loose
        )
        configured_match_ok = bool(observed_keys.intersection(configured_keys)) or bool(
            observed_loose.intersection(configured_loose)
        )
        family_ok = configured_match_ok or any(_matches_family(normalized, row) for row in observed)
        binding_ok = bool(configured) and bool(observed) and not missing
        if normalized != "director" and configured_loose:
            binding_ok = bool(observed_loose.intersection(configured_loose))
        multi_route_ok = True
        if normalized == "director":
            configured_routes = configured_loose
            multi_route_ok = bool(observed) and bool(configured_routes) and not missing
            if require_all_director_routes:
                binding_ok = binding_ok and multi_route_ok
            else:
                binding_ok = bool(observed) and (
                    not configured_routes or bool(observed_loose.intersection(configured_loose))
                )
        role_ok = binding_ok and family_ok
        role_results[normalized] = {
            "ok": role_ok,
            "configured": configured,
            "observed_count": len(observed),
            "observed_bindings": sorted(observed_loose),
            "skipped_bindings": sorted(skipped_loose),
            "fail_closed_count": len(skipped),
            "missing_bindings": missing,
            "family_ok": family_ok,
            "multi_route_ok": multi_route_ok,
            "multi_route_required": bool(normalized == "director" and require_all_director_routes),
        }
        ok = ok and role_ok

    failing_roles = [role for role, result in role_results.items() if not result.get("ok")]
    return {
        "ok": ok,
        "roles": role_results,
        "events_observed": len(evidence),
        "candidate_events_observed": len(candidate_events),
        "events_rejected": len(candidate_events) - len(evidence),
        "terminal_events_observed": len(terminal),
        "summary": "LLM route audit passed" if ok else "LLM route audit failed: " + ", ".join(failing_roles),
    }
