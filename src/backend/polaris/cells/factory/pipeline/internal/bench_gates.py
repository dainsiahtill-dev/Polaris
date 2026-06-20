"""Factory-bench goal gates and audit attribution.

The public bench runner remains a delivery harness.  The platform-owned facts
that decide whether a generated project is actually runnable live here, inside
the ``factory.pipeline`` cell boundary.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Iterable
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_REQUIRED_LLM_ROLES = ("pm", "chief_engineer", "qa", "director")
_ROLE_ALIASES = {
    "ce": "chief_engineer",
    "chief engineer": "chief_engineer",
    "chief-engineer": "chief_engineer",
    "chiefeng": "chief_engineer",
    "chief_engineer": "chief_engineer",
    "pm": "pm",
    "qa": "qa",
    "director": "director",
    "architect": "architect",
}
_ROLE_FAMILIES: dict[str, tuple[tuple[str, ...], ...]] = {
    "pm": (("kimi",),),
    "chief_engineer": (("kimi",),),
    "qa": (("minimax",), ("mini", "max")),
    "director": (("qwen", "3.6", "27"), ("qwen3.6",), ("qwen", "27b")),
}
_PY_ENTRYPOINT_NAMES = ("main.py", "app.py", "cli.py", "__main__.py")
_ENTRYPOINT_FAILURE_MARKER_RE = re.compile(r"(?im)^\s*FAIL(?:ED)?(?:\b|:)")
_FAILURE_CATEGORIES = {
    "pm_contract",
    "director_tool_execution",
    "llm_output",
    "context_budget",
    "target_project_baseline",
    "runtime_environment",
    "unknown",
}


def _norm_text(value: Any) -> str:
    return str(value or "").strip()


def _norm_role(value: Any) -> str:
    raw = _norm_text(value).lower().replace("-", "_")
    return _ROLE_ALIASES.get(raw, raw)


def _tail(value: str, limit: int = 1600) -> str:
    text = str(value or "")
    return text[-limit:]


def _to_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _run_command(command: list[str], cwd: Path, *, timeout_s: int) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, int(timeout_s)),
            check=False,
        )
        return {
            "command": command,
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "duration_s": round(time.time() - started, 3),
            "stdout_tail": _tail(proc.stdout),
            "stderr_tail": _tail(proc.stderr),
            "timeout": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "ok": False,
            "returncode": None,
            "duration_s": round(time.time() - started, 3),
            "stdout_tail": _tail(_to_text(exc.stdout)),
            "stderr_tail": _tail(_to_text(exc.stderr)),
            "timeout": True,
        }
    except OSError as exc:
        return {
            "command": command,
            "ok": False,
            "returncode": None,
            "duration_s": round(time.time() - started, 3),
            "stdout_tail": "",
            "stderr_tail": str(exc),
            "timeout": False,
        }


def _entrypoint_has_failure_marker(result: dict[str, Any]) -> bool:
    output = f"{result.get('stdout_tail') or ''}\n{result.get('stderr_tail') or ''}"
    return bool(_ENTRYPOINT_FAILURE_MARKER_RE.search(output))


def _mark_entrypoint_failure(result: dict[str, Any]) -> dict[str, Any]:
    return {
        **result,
        "ok": False,
        "detail": "entrypoint output contained a failure marker",
        "failure_marker": True,
    }


def _load_package_json(workspace: Path) -> dict[str, Any]:
    package_path = workspace / "package.json"
    if not package_path.is_file():
        return {}
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _has_package_dependencies(package: dict[str, Any]) -> bool:
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        if isinstance(package.get(key), dict) and package[key]:
            return True
    return False


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def _smoke_static_web(workspace: Path, html_rel: str, *, timeout_s: int) -> dict[str, Any]:
    handler = partial(_QuietStaticHandler, directory=str(workspace))
    started = time.time()
    server: ThreadingHTTPServer | None = None
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = int(server.server_address[1])
        path = urllib.request.pathname2url(html_rel)
        url = f"http://127.0.0.1:{port}/{path}"
        with urllib.request.urlopen(url, timeout=max(1, min(10, int(timeout_s)))) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
        ok = response.status == 200 and "<html" in body.lower()
        return {
            "kind": "web_static",
            "ok": ok,
            "url": url,
            "entrypoint": html_rel,
            "duration_s": round(time.time() - started, 3),
            "detail": "static web entrypoint served over local HTTP"
            if ok
            else "static web response did not look like HTML",
        }
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {
            "kind": "web_static",
            "ok": False,
            "entrypoint": html_rel,
            "duration_s": round(time.time() - started, 3),
            "detail": str(exc),
        }
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


def _find_html_entrypoint(workspace: Path, code_files: list[str]) -> str:
    candidates = [rel for rel in code_files if rel.lower().endswith(".html")]
    for preferred in ("index.html", "public/index.html", "src/index.html"):
        if preferred in candidates:
            return preferred
    return candidates[0] if candidates else ""


def _find_python_entrypoint(workspace: Path, code_files: list[str]) -> str:
    py_files = [rel for rel in code_files if rel.lower().endswith(".py")]
    by_name = {Path(rel).name: rel for rel in py_files}
    for name in _PY_ENTRYPOINT_NAMES:
        if name in by_name:
            return by_name[name]
    for rel in py_files:
        try:
            text = (workspace / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "__main__" in text:
            return rel
    return ""


def _looks_like_python_test(rel_path: str) -> bool:
    path = Path(rel_path)
    name = path.name
    return name.startswith("test_") and name.endswith(".py")


def _discover_python_test_files(workspace: Path, code_files: list[str]) -> list[str]:
    discovered: set[str] = set()

    def add_path(path: Path) -> None:
        try:
            rel_path = path.relative_to(workspace)
        except ValueError:
            return
        rel = rel_path.as_posix()
        if path.is_file() and _looks_like_python_test(rel):
            discovered.add(rel)

    for rel in code_files:
        add_path(workspace / rel)

    for path in workspace.glob("test_*.py"):
        add_path(path)

    tests_dir = workspace / "tests"
    if tests_dir.is_dir():
        for path in tests_dir.rglob("test_*.py"):
            add_path(path)

    return sorted(discovered)


def _python_test_command_has_zero_tests(result: dict[str, Any]) -> bool:
    output = f"{result.get('stdout_tail') or ''}\n{result.get('stderr_tail') or ''}"
    return bool(re.search(r"Ran\s+0\s+tests", output))


def _python_pytest_command_has_zero_tests(result: dict[str, Any]) -> bool:
    output = f"{result.get('stdout_tail') or ''}\n{result.get('stderr_tail') or ''}".lower()
    return "no tests ran" in output or "collected 0 items" in output


def _run_python_unittest_suite(workspace: Path, test_files: list[str], *, timeout_s: int) -> dict[str, Any]:
    start_dir = "tests" if (workspace / "tests").is_dir() else "."
    result = _run_command(
        [sys.executable, "-m", "unittest", "discover", "-s", start_dir, "-p", "test_*.py", "-v"],
        workspace,
        timeout_s=max(10, int(timeout_s)),
    )
    result["kind"] = "python_tests"
    result["runner"] = "unittest"
    result["test_files"] = test_files
    if _python_test_command_has_zero_tests(result):
        result["ok"] = False
        result["detail"] = "python unittest discovered zero tests from generated test files"
    return result


def _run_python_pytest_suite(workspace: Path, test_files: list[str], *, timeout_s: int) -> dict[str, Any]:
    result = _run_command(
        [sys.executable, "-m", "pytest", *test_files, "-q"],
        workspace,
        timeout_s=max(10, int(timeout_s)),
    )
    result["kind"] = "python_tests"
    result["runner"] = "pytest"
    result["test_files"] = test_files
    if _python_pytest_command_has_zero_tests(result):
        result["ok"] = False
        result["detail"] = "python pytest discovered zero tests from generated test files"
    return result


def _run_python_test_suite(workspace: Path, test_files: list[str], *, timeout_s: int) -> list[dict[str, Any]]:
    unittest_result = _run_python_unittest_suite(workspace, test_files, timeout_s=timeout_s)
    if not _python_test_command_has_zero_tests(unittest_result):
        return [unittest_result]
    pytest_result = _run_python_pytest_suite(workspace, test_files, timeout_s=timeout_s)
    pytest_result["fallback_from"] = "unittest_zero_tests"
    return [unittest_result, pytest_result]


def _smoke_python_cli(workspace: Path, entrypoint: str, *, timeout_s: int) -> dict[str, Any]:
    command = [sys.executable, entrypoint, "--help"]
    result = _run_command(command, workspace, timeout_s=min(max(2, int(timeout_s)), 10))
    if result["ok"]:
        if _entrypoint_has_failure_marker(result):
            return _mark_entrypoint_failure({"kind": "python_cli", "entrypoint": entrypoint, **result})
        return {"kind": "python_cli", "entrypoint": entrypoint, **result}
    fallback = _run_command([sys.executable, entrypoint], workspace, timeout_s=min(max(2, int(timeout_s)), 5))
    fallback_output = f"{fallback.get('stdout_tail') or ''}\n{fallback.get('stderr_tail') or ''}".lower()
    if (
        fallback.get("returncode") in {1, 2}
        and "usage:" in fallback_output
        and "traceback" not in fallback_output
        and "syntaxerror" not in fallback_output
    ):
        return {
            "kind": "python_cli",
            "entrypoint": entrypoint,
            "usage_screen": True,
            **fallback,
            "ok": True,
        }
    if fallback["ok"] or fallback.get("timeout"):
        if _entrypoint_has_failure_marker(fallback):
            return _mark_entrypoint_failure(
                {
                    "kind": "python_cli",
                    "entrypoint": entrypoint,
                    "started": bool(fallback.get("timeout")),
                    **fallback,
                }
            )
        return {
            "kind": "python_cli",
            "entrypoint": entrypoint,
            "started": bool(fallback.get("timeout")),
            **fallback,
            "ok": True,
        }
    return {"kind": "python_cli", "entrypoint": entrypoint, **fallback}


def _first_ok_command(commands: list[dict[str, Any]]) -> dict[str, Any] | None:
    for command in commands:
        if command.get("ok"):
            return command
    return None


def build_real_run_gate(workspace: Path, record: dict[str, Any], *, timeout_s: int = 60) -> dict[str, Any]:
    """Run the platform's real-runnability gate for one generated project."""
    code_files = [str(item) for item in record.get("code_files") or []]
    commands: list[dict[str, Any]] = []
    package = _load_package_json(workspace)
    scripts = _as_dict(package.get("scripts"))

    environment_ok = False
    environment_detail = "no environment preparation ran"
    if package:
        npm = shutil.which("npm")
        if npm and _has_package_dependencies(package) and not (workspace / "node_modules").exists():
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

    build_command_ok = False
    build_detail = "no build/test/lint command was discovered"
    if package and shutil.which("npm"):
        for script_name in ("test", "build", "lint", "check"):
            if script_name in scripts:
                cmd = _run_command(["npm", "run", script_name], workspace, timeout_s=max(10, int(timeout_s)))
                cmd["phase"] = "build_test_lint"
                cmd["script"] = script_name
                commands.append(cmd)
                build_command_ok = bool(cmd.get("ok"))
                build_detail = f"npm run {script_name} {'passed' if build_command_ok else 'failed'}"
                break
    python_test_files = _discover_python_test_files(workspace, code_files)
    if not build_command_ok and any(rel.endswith(".py") for rel in code_files):
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

    entrypoint: dict[str, Any] = {"ok": False, "kind": "", "detail": "no CLI/Web/API entrypoint discovered"}
    html_entry = _find_html_entrypoint(workspace, code_files)
    if html_entry:
        entrypoint = _smoke_static_web(workspace, html_entry, timeout_s=timeout_s)
    elif package and shutil.which("npm") and "start" in scripts:
        cmd = _run_command(["npm", "run", "start"], workspace, timeout_s=min(max(3, int(timeout_s)), 8))
        entrypoint = {
            "kind": "npm_start",
            "entrypoint": "npm run start",
            "ok": bool(cmd.get("ok") or cmd.get("timeout")),
            **cmd,
        }
    else:
        py_entry = _find_python_entrypoint(workspace, code_files)
        if py_entry:
            entrypoint = _smoke_python_cli(workspace, py_entry, timeout_s=timeout_s)

    if (
        not build_command_ok
        and bool(entrypoint.get("ok"))
        and str(entrypoint.get("kind") or "") == "web_static"
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
    return {
        "ok": ok,
        "requirements": requirements,
        "commands": commands[-12:],
        "entrypoint": entrypoint,
        "summary": "real run gate passed" if ok else "real run gate failed: " + ", ".join(failing),
    }


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
        data_metadata.get("provider_id"),
        extra_fields.get("provider_id"),
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
    terminal = lowered_event in {"llm_call_end", "llm_error", "call_end", "call_error", "invoke_end", "error"}
    invocation = terminal or "llm" in lowered_event or lowered_event.startswith("invoke")
    return {
        "event": event_name,
        "role": role,
        "provider_id": provider,
        "model": model,
        "binding_id": binding_id,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "terminal": terminal,
        "invocation": invocation,
        "source_path": source_path,
        "raw": raw,
    }


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
    for base in (*runtime_dirs, workspace / ".polaris" / "runtime", workspace / ".polaris"):
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
                    from polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline import (
                        _reachable_provider_pool,
                    )

                    live_provider_ids = set(_reachable_provider_pool(provider_ids))
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


def _model_key(row: dict[str, Any]) -> str:
    return _norm_text(row.get("model"))


def _matches_family(role: str, row: dict[str, Any]) -> bool:
    alternatives = _ROLE_FAMILIES.get(role)
    if not alternatives:
        return True
    haystack = f"{row.get('provider_id') or row.get('provider') or ''} {row.get('model') or ''}".lower()
    return any(all(token in haystack for token in alternative) for alternative in alternatives)


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
    evidence = [
        event for event in events if event.get("invocation") and _norm_role(event.get("role")) in required_roles
    ]
    terminal = [event for event in evidence if event.get("terminal")]
    by_role: dict[str, list[dict[str, Any]]] = {}
    for event in terminal or evidence:
        by_role.setdefault(_norm_role(event.get("role")), []).append(event)

    role_results: dict[str, dict[str, Any]] = {}
    ok = True
    for role in required_roles:
        normalized = _norm_role(role)
        configured = list(expected.get(normalized) or [])
        observed = list(by_role.get(normalized) or [])
        configured_keys = {_binding_key(row) for row in configured if _loose_binding_key(row) != "|"}
        configured_loose = {_loose_binding_key(row) for row in configured if _loose_binding_key(row) != "|"}
        configured_models = {_model_key(row) for row in configured if _model_key(row)}
        observed_keys = {_binding_key(row) for row in observed if _loose_binding_key(row) != "|"}
        observed_loose = {_loose_binding_key(row) for row in observed if _loose_binding_key(row) != "|"}
        observed_providerless_models = {
            _model_key(row)
            for row in observed
            if _model_key(row) and not _norm_text(row.get("provider_id") or row.get("provider"))
        }
        missing = sorted(
            key
            for key in configured_keys
            if key not in observed_keys
            and key.rsplit("|", 1)[0] not in observed_loose
            and key.split("|", 2)[1] not in observed_providerless_models
        )
        family_ok = any(_matches_family(normalized, row) for row in observed)
        binding_ok = bool(configured) and bool(observed) and not missing
        if normalized != "director" and configured_loose:
            binding_ok = bool(observed_loose.intersection(configured_loose)) or bool(
                observed_providerless_models.intersection(configured_models)
            )
        multi_route_ok = True
        if normalized == "director":
            configured_routes = configured_loose or configured_models
            multi_route_ok = bool(configured_routes) and not missing
            if require_all_director_routes:
                binding_ok = binding_ok and multi_route_ok
            else:
                binding_ok = bool(observed) and (
                    not configured_routes
                    or bool(observed_loose.intersection(configured_loose))
                    or bool(observed_providerless_models.intersection(configured_models))
                )
        role_ok = binding_ok and family_ok
        role_results[normalized] = {
            "ok": role_ok,
            "configured": configured,
            "observed_count": len(observed),
            "observed_bindings": sorted(observed_loose),
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
        "terminal_events_observed": len(terminal),
        "summary": "LLM route audit passed" if ok else "LLM route audit failed: " + ", ".join(failing_roles),
    }


def _gate_failures(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [gate for gate in record.get("factory_gates") or [] if isinstance(gate, dict) and not gate.get("ok")]


def _check_failures(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [check for check in record.get("checks") or [] if isinstance(check, dict) and not check.get("ok")]


def _contains_context_budget_signal(text: str) -> bool:
    return bool(re.search(r"budget|context[_ -]?window|max[_ -]?tokens|truncat|token budget", text, re.IGNORECASE))


def _first_real_run_failure(real_run_gate: dict[str, Any]) -> str:
    requirements = real_run_gate.get("requirements")
    if not isinstance(requirements, dict):
        return ""
    for name, payload in requirements.items():
        if isinstance(payload, dict) and not payload.get("ok"):
            return str(name)
    return ""


def _category_signature(category: str, reason: str) -> str:
    stable_category = category if category in _FAILURE_CATEGORIES else "unknown"
    stable_reason = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", reason.strip().lower()).strip("_") or "unknown"
    return f"{stable_category}:{stable_reason}"


def classify_factory_bench_failure(record: dict[str, Any]) -> dict[str, Any]:
    """Assign one stable root-cause category to a per-project bench record."""
    if record.get("all_checks_passed"):
        return {
            "ok": True,
            "category": "",
            "root_cause_signature": "pass",
            "reasons": [],
            "evidence": [],
        }

    evidence: list[str] = []
    reasons: list[str] = []
    combined = json.dumps(record, ensure_ascii=False, default=str)
    if _contains_context_budget_signal(combined):
        category, reason = "context_budget", "context_or_token_budget"
    elif isinstance(record.get("llm_route_audit"), dict) and not record["llm_route_audit"].get("ok"):
        category, reason = "llm_output", "llm_route_audit"
        evidence.append(str(record["llm_route_audit"].get("summary") or ""))
    elif isinstance(record.get("real_run_gate"), dict) and not record["real_run_gate"].get("ok"):
        failed_requirement = _first_real_run_failure(record["real_run_gate"])
        reason = f"real_run_gate.{failed_requirement or 'unknown'}"
        if failed_requirement == "artifact_landed":
            category = "director_tool_execution"
        elif failed_requirement == "environment_prepared":
            category = "runtime_environment"
        else:
            category = "target_project_baseline"
        evidence.append(str(record["real_run_gate"].get("summary") or ""))
    elif not record.get("has_plan_doc") or record.get("wrong_product_suspect"):
        category = "pm_contract"
        reason = "missing_or_wrong_contract"
    elif str(record.get("chain_state") or "") != "clean":
        director = (
            record.get("chain_results", {}).get("director", {}) if isinstance(record.get("chain_results"), dict) else {}
        )
        if isinstance(director, dict) and (
            int(director.get("failures") or 0) > 0 or int(director.get("blocked") or 0) > 0
        ):
            category, reason = "director_tool_execution", "director_failures_or_blocked"
        else:
            category, reason = "runtime_environment", f"chain_state.{record.get('chain_state') or 'unknown'}"
    elif _check_failures(record):
        category, reason = "target_project_baseline", str(_check_failures(record)[0].get("check") or "check_failed")
    else:
        failed_gates = _gate_failures(record)
        category = "unknown"
        reason = str(failed_gates[0].get("gate") if failed_gates else "unclassified_failure")

    for gate in _gate_failures(record):
        reasons.append(f"gate:{gate.get('gate')}={gate.get('detail')}")
    for check in _check_failures(record):
        reasons.append(f"check:{check.get('check')}={check.get('detail')}")
    return {
        "ok": False,
        "category": category,
        "root_cause_signature": _category_signature(category, reason),
        "reasons": reasons,
        "evidence": [item for item in evidence if item],
    }


def aggregate_goal_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    real_passed = sum(
        1 for record in records if isinstance(record.get("real_run_gate"), dict) and record["real_run_gate"].get("ok")
    )
    route_passed = sum(
        1
        for record in records
        if isinstance(record.get("llm_route_audit"), dict) and record["llm_route_audit"].get("ok")
    )
    categories: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    for record in records:
        taxonomy = record.get("failure_taxonomy")
        if not isinstance(taxonomy, dict) or taxonomy.get("ok"):
            continue
        category = str(taxonomy.get("category") or "unknown")
        signature = str(taxonomy.get("root_cause_signature") or f"{category}:unknown")
        categories[category] += 1
        signatures[signature] += 1
    return {
        "total": total,
        "real_run_gate": {"passed": real_passed, "total": total},
        "llm_route_audit": {"passed": route_passed, "total": total},
        "failure_categories": dict(sorted(categories.items())),
        "root_cause_signatures": dict(sorted(signatures.items())),
    }


__all__ = [
    "aggregate_goal_audit",
    "build_llm_route_audit",
    "build_real_run_gate",
    "classify_factory_bench_failure",
    "collect_llm_events",
    "resolve_expected_llm_bindings",
]
