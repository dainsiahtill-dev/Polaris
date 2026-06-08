"""Shared quality helpers for PM task contracts and integration QA checks."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import sys
from typing import Any

from polaris.cells.orchestration.pm_planning.internal.task_quality_gate import (
    autofix_pm_contract_for_quality,
    check_quality_promote_candidate,
    evaluate_pm_task_quality,
    get_quality_gate_config,
)
from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest
from polaris.kernelone.quality import scan_workspace_artifact_quality

logger = logging.getLogger(__name__)

_PM_PROMPT_LEAK_TOKENS = (
    "you are ",
    "角色设定",
    "system prompt",
    "no yapping",
    "<thinking>",
    "<tool_call>",
)
_PM_CHINESE_PROMPT_LEAK_TOKENS = (
    "系统提示词",
    "开发者提示词",
    "角色提示词",
    "内部提示词",
    "完整提示词",
    "提示词泄露",
    "提示词泄漏",
    "提示词注入",
)
_PM_ACTION_TOKENS = (
    "build",
    "implement",
    "define",
    "design",
    "write",
    "create",
    "refactor",
    "verify",
    "构建",
    "实现",
    "设计",
    "编写",
    "重构",
    "验证",
)
_PM_MEASURABLE_COMMAND_RE = re.compile(
    r"\b(curl|wget|httpie|npm|pnpm|yarn|npx|node|python|pytest|go\s+test|mvn|gradle|dotnet|cargo|grep|jq|awk|sed|powershell|pwsh)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_ASSERT_RE = re.compile(
    r"\b(verify|assert|expect|should|must|returns?|response|status|校验|验证|断言|应当|必须)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_RESULT_RE = re.compile(
    r"\b(200|201|202|204|400|401|403|404|409|422|500|pass|fail|true|false|ok|error)\b|[<>]=?\s*\d+|\b\d+\s*(ms|s|sec|seconds?|分钟|小时|days?)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|[\w.\-]+[\\/][\w.\-/\\]+)",
)
_PM_MEASURABLE_BACKTICK_RE = re.compile(r"`[^`]{2,}`")


def _python_module_command(module: str, args: list[str] | None = None) -> str:
    executable = shlex.quote(str(sys.executable or "python3"))
    tokens = [executable, "-m", shlex.quote(str(module or "").strip())]
    tokens.extend(shlex.quote(str(arg)) for arg in args or [])
    return " ".join(token for token in tokens if token)


def _strip_wrapping_quotes(token: str) -> str:
    text = str(token or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _parse_command_args(command: str) -> list[str]:
    raw = str(command or "").strip()
    if not raw:
        raise ValueError("empty command")
    try:
        tokens = shlex.split(raw, posix=(os.name != "nt"))
    except ValueError as exc:
        raise ValueError(f"invalid command syntax: {exc}") from exc
    if os.name == "nt":
        tokens = [_strip_wrapping_quotes(token) for token in tokens]
    normalized = [str(token).strip() for token in tokens if str(token).strip()]
    if not normalized:
        raise ValueError("empty command")
    return normalized


def _normalize_path_list(value: Any) -> list[str]:
    if isinstance(value, str):
        entries = [segment.strip() for segment in value.split(",") if segment.strip()]
    elif isinstance(value, list):
        entries = [str(item).strip() for item in value if str(item).strip()]
    else:
        entries = []
    normalized: list[str] = []
    for item in entries:
        token = str(item).strip().replace("\\", "/")
        token = token.lstrip("./")
        token = re.sub(r"/+", "/", token)
        if token:
            normalized.append(token)
    return normalized


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_path(value: Any) -> str:
    token = str(value or "").strip().replace("\\", "/")
    token = re.sub(r"^[A-Za-z]:/", "", token)
    token = token.lstrip("./").strip("/")
    token = re.sub(r"/+", "/", token)
    return token.lower()


def _contains_prompt_leakage(text: str) -> bool:
    lowered = _normalize_text(text).lower()
    if not lowered:
        return False
    if any(token in lowered for token in _PM_PROMPT_LEAK_TOKENS):
        return True
    return any(token in lowered for token in _PM_CHINESE_PROMPT_LEAK_TOKENS)


def _has_measurable_acceptance_anchor(acceptance_items: list[str]) -> bool:
    for item in acceptance_items:
        normalized = _normalize_text(item)
        if not normalized:
            continue
        if _PM_MEASURABLE_BACKTICK_RE.search(normalized):
            return True
        if _PM_MEASURABLE_COMMAND_RE.search(normalized):
            return True
        has_assert = bool(_PM_MEASURABLE_ASSERT_RE.search(normalized))
        has_observable = bool(_PM_MEASURABLE_RESULT_RE.search(normalized) or _PM_MEASURABLE_PATH_RE.search(normalized))
        if has_assert and has_observable:
            return True
    return False


def _tail_non_empty_lines(text: str, *, limit: int = 8) -> list[str]:
    lines = [str(line).rstrip() for line in str(text or "").splitlines() if str(line).strip()]
    if len(lines) <= limit:
        return lines
    return lines[-limit:]


def _read_package_json(workspace_full: str) -> dict[str, Any]:
    package_path = os.path.join(workspace_full, "package.json")
    try:
        with open(package_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, RuntimeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_package_scripts(package_payload: dict[str, Any]) -> dict[str, str]:
    scripts = package_payload.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    normalized: dict[str, str] = {}
    for name, command in scripts.items():
        key = str(name or "").strip()
        value = str(command or "").strip()
        if key and value:
            normalized[key] = value
    return normalized


def _detect_node_verify_command(workspace_full: str) -> str:
    package_payload = _read_package_json(workspace_full)
    scripts = _read_package_scripts(package_payload)
    if "test" in scripts:
        return "npm run test -- --watch=false"
    for script_name in ("verify:final", "verify", "smoke:boot", "smoke", "build", "lint"):
        if script_name in scripts:
            return f"npm run {script_name}"
    return "node -e \"JSON.parse(require('fs').readFileSync('package.json', 'utf8'))\""


def _package_has_declared_dependencies(package_payload: dict[str, Any]) -> bool:
    for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = package_payload.get(section_name)
        if isinstance(section, dict) and section:
            return True
    return False


def _node_dependencies_missing(workspace_full: str, package_payload: dict[str, Any]) -> bool:
    if not _package_has_declared_dependencies(package_payload):
        return False
    return not os.path.isdir(os.path.join(workspace_full, "node_modules"))


_NODE_SCRIPT_DEPENDENCY_TOOL_RE = re.compile(
    r"(^|[;&|]\s*)(babel|eslint|jest|mocha|rollup|ts-node|tsc|tsx|vite|vitest|webpack)\b",
    re.IGNORECASE,
)


def _node_package_script_name(command_args: list[str]) -> str:
    if not command_args:
        return ""
    executable = str(command_args[0] or "").strip().lower()
    args = [str(item or "").strip() for item in command_args[1:] if str(item or "").strip()]
    if executable == "npm":
        if args and args[0] == "run" and len(args) >= 2:
            return args[1]
        if args and args[0] in {"test", "start"}:
            return args[0]
    if executable in {"pnpm", "yarn"}:
        if args and args[0] == "run" and len(args) >= 2:
            return args[1]
        if args:
            return args[0]
    return ""


def _node_missing_dependencies_should_block(
    workspace_full: str,
    package_payload: dict[str, Any],
    command_args: list[str],
) -> bool:
    if not _node_dependencies_missing(workspace_full, package_payload):
        return False
    script_name = _node_package_script_name(command_args)
    script_command = _read_package_scripts(package_payload).get(script_name, "")
    return not (script_command and not _NODE_SCRIPT_DEPENDENCY_TOOL_RE.search(script_command))


def _node_static_fallback_allowed() -> bool:
    raw = str(os.environ.get("KERNELONE_INTEGRATION_QA_ALLOW_STATIC_NODE_FALLBACK") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _node_auto_install_allowed() -> bool:
    raw = str(os.environ.get("KERNELONE_INTEGRATION_QA_AUTO_INSTALL_NODE_DEPS", "1") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _is_node_package_command(command_args: list[str]) -> bool:
    if not command_args:
        return False
    executable = str(command_args[0] or "").strip().lower()
    return executable in {"npm", "pnpm", "yarn"}


def _node_dependency_install_args(command_args: list[str]) -> list[str]:
    executable = str(command_args[0] if command_args else "npm").strip().lower()
    if executable == "pnpm":
        return ["pnpm", "install", "--ignore-scripts"]
    if executable == "yarn":
        return ["yarn", "install", "--ignore-scripts"]
    return ["npm", "install", "--ignore-scripts"]


def _node_install_timeout_seconds() -> int:
    raw = os.environ.get("KERNELONE_INTEGRATION_QA_INSTALL_TIMEOUT_SECONDS", "300")
    try:
        return max(int(raw), 30)
    except (TypeError, ValueError):
        return 300


def _prepare_node_dependencies_for_verify(
    workspace_full: str,
    command_args: list[str],
) -> tuple[bool, str, list[str]]:
    install_args = _node_dependency_install_args(command_args)
    command_text = " ".join(shlex.quote(token) for token in install_args)
    try:
        cmd_svc = CommandExecutionService(workspace_full)
        request = CommandRequest(
            executable=install_args[0],
            args=install_args[1:],
            cwd=workspace_full,
            timeout_seconds=_node_install_timeout_seconds(),
        )
        result = cmd_svc.run(request)
    except (RuntimeError, ValueError) as exc:
        summary = f"Integration dependency installation runtime error: {exc}"
        return False, summary, [summary]

    stdout_tail = _tail_non_empty_lines(result.get("stdout", ""), limit=6)
    stderr_tail = _tail_non_empty_lines(result.get("stderr", ""), limit=6)
    if int(result.get("returncode", -1)) == 0:
        return True, f"Integration dependency installation passed: {command_text}", []

    errors: list[str] = [f"Command failed ({result.get('returncode', -1)}): {command_text}"]
    errors.extend(f"[stdout] {line}" for line in stdout_tail)
    errors.extend(f"[stderr] {line}" for line in stderr_tail)
    summary = f"Integration dependency installation failed: {command_text}"
    return False, summary, errors[:20]


def _has_node_test_files(workspace_full: str) -> bool:
    test_roots = ("tests", "test", "src")
    test_markers = (".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx")
    for rel_root in test_roots:
        root = os.path.join(workspace_full, rel_root)
        if not os.path.isdir(root):
            continue
        try:
            for _current_root, dir_names, file_names in os.walk(root):
                dir_names[:] = [
                    name
                    for name in dir_names
                    if name not in {"node_modules", "dist", "build", "coverage", ".git", ".polaris"}
                ]
                for name in file_names:
                    token = str(name or "").strip().lower()
                    if token.endswith(test_markers):
                        return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


def _count_node_source_files(workspace_full: str) -> int:
    count = 0
    source_exts = (".ts", ".tsx", ".js", ".jsx", ".json", ".css", ".html")
    skip_dirs = {"node_modules", "dist", "build", "coverage", ".git", ".polaris"}
    try:
        for _current_root, dir_names, file_names in os.walk(workspace_full):
            dir_names[:] = [name for name in dir_names if name not in skip_dirs]
            for name in file_names:
                if str(name or "").strip().lower().endswith(source_exts):
                    count += 1
    except (OSError, RuntimeError, ValueError):
        return 0
    return count


def _run_node_static_verify_runner(workspace_full: str, package_payload: dict[str, Any]) -> tuple[bool, str, list[str]]:
    scripts = package_payload.get("scripts")
    script_names = set(scripts.keys()) if isinstance(scripts, dict) else set()
    source_count = _count_node_source_files(workspace_full)
    has_tests = _has_node_test_files(workspace_full)
    errors: list[str] = []
    if source_count <= 0:
        errors.append("Node static verification failed: no source/config files found")
    if "test" in script_names and not has_tests:
        errors.append("Node static verification failed: package has a test script but no test/spec files exist")
    if errors:
        return False, "Node static verification failed while dependencies are not installed", errors
    artifact_errors = scan_workspace_artifact_quality(workspace_full)
    if artifact_errors:
        return (
            False,
            "Node static verification failed artifact quality scan while dependencies are not installed",
            artifact_errors[:20],
        )
    summary = (
        "Node static verification passed while dependencies are not installed "
        f"(source_files={source_count}, tests={'present' if has_tests else 'not-required'})."
    )
    return True, summary, []


def detect_integration_verify_command(workspace_full: str) -> str:
    override = str(os.environ.get("KERNELONE_INTEGRATION_QA_COMMAND") or "").strip()
    if override:
        return override

    def _dir_has_python_test_files(rel_path: str) -> bool:
        target = os.path.join(workspace_full, rel_path)
        if not os.path.isdir(target):
            return False
        try:
            for root, _, files in os.walk(target):
                for name in files:
                    token = str(name or "").strip().lower()
                    if not token.endswith(".py"):
                        continue
                    if token.startswith("test_") or token.endswith("_test.py") or root == target:
                        return True
        except (OSError, RuntimeError, ValueError):
            return False
        return False

    python_test_markers = [
        "pytest.ini",
        "tox.ini",
        "conftest.py",
    ]
    markers = {
        "python": [
            "pyproject.toml",
            "requirements.txt",
            "setup.py",
            "tox.ini",
        ],
        "node": ["package.json"],
        "go": ["go.mod"],
        "rust": ["Cargo.toml"],
    }
    has_python_tests = _dir_has_python_test_files("tests")
    has_root_python_tests = any(os.path.isfile(os.path.join(workspace_full, marker)) for marker in python_test_markers)
    if not has_root_python_tests:
        try:
            for entry in os.listdir(workspace_full):
                token = str(entry or "").strip().lower()
                if not token.endswith(".py"):
                    continue
                if token.startswith("test_") or token.endswith("_test.py"):
                    has_root_python_tests = True
                    break
        except (OSError, RuntimeError, ValueError):
            has_root_python_tests = False

    if any(os.path.isfile(os.path.join(workspace_full, item)) for item in markers["python"]):
        if has_python_tests or has_root_python_tests:
            return _python_module_command("pytest", ["-q"])
        compile_targets: list[str] = []
        for candidate in ("app", "src", "storage", "services", "tests"):
            if os.path.isdir(os.path.join(workspace_full, candidate)):
                compile_targets.append(candidate)
        try:
            for entry in os.listdir(workspace_full):
                token = str(entry or "").strip()
                if token.endswith(".py") and os.path.isfile(os.path.join(workspace_full, token)):
                    compile_targets.append(token)
        except (OSError, RuntimeError, ValueError) as e:
            logger.debug(f"Failed to list Python files: {e}")
        if not compile_targets:
            compile_targets.append(".")
        unique_targets: list[str] = []
        for item in compile_targets:
            if item not in unique_targets:
                unique_targets.append(item)
        return _python_module_command("compileall", ["-q", *unique_targets])
    if any(os.path.isfile(os.path.join(workspace_full, item)) for item in markers["node"]):
        return _detect_node_verify_command(workspace_full)
    if any(os.path.isfile(os.path.join(workspace_full, item)) for item in markers["go"]):
        return "go test ./... -run TestDoesNotExist"
    if any(os.path.isfile(os.path.join(workspace_full, item)) for item in markers["rust"]):
        return "cargo test --no-run"
    return _python_module_command("compileall", ["-q", "."])


_TS_TYPECHECK_IGNORE_CODES: tuple[str, ...] = ("TS6053", "TS18003")
_TS_ERROR_RE = re.compile(r"error (TS\d+):")
_TS_MODULE_MISSING_RE = re.compile(r"error TS(?:2307|2792): Cannot find module ['\"]([^'\"]+)['\"]")
_TS_TYPES_MISSING_RE = re.compile(r"error TS2688: Cannot find type definition file for ['\"]([^'\"]+)['\"]")
_TS_DECL_MISSING_RE = re.compile(r"error TS7016: Could not find a declaration file for module ['\"]([^'\"]+)['\"]")
_TEST_FRAMEWORK_TYPE_NOISE_MODULES: tuple[str, ...] = ("@jest/globals", "jest", "vitest", "mocha")


def _resolve_repo_tsc() -> str:
    """Resolve a usable ``tsc`` executable from env override or the Polaris repo toolchain."""
    override = str(os.environ.get("KERNELONE_TSC_PATH") or "").strip()
    if override and os.path.isfile(override):
        return override
    current = os.path.dirname(os.path.abspath(__file__))
    while True:
        candidate = os.path.join(current, "node_modules", ".bin", "tsc")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return ""
        current = parent


def _has_typescript_sources(workspace_full: str) -> bool:
    """Return True when the workspace contains non-declaration ``.ts``/``.tsx`` sources."""
    skip_dirs = {"node_modules", "dist", "build", "coverage", ".git", ".polaris"}
    for rel_root in ("src", "tests", "test"):
        root = os.path.join(workspace_full, rel_root)
        if not os.path.isdir(root):
            continue
        try:
            for _current_root, dir_names, file_names in os.walk(root):
                dir_names[:] = [name for name in dir_names if name not in skip_dirs]
                for name in file_names:
                    token = str(name or "").strip().lower()
                    if token.endswith((".ts", ".tsx")) and not token.endswith(".d.ts"):
                        return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


def _is_typescript_project(workspace_full: str) -> bool:
    """Detect a TypeScript project: a ``tsconfig.json`` plus real ``.ts`` sources."""
    return os.path.isfile(os.path.join(workspace_full, "tsconfig.json")) and _has_typescript_sources(workspace_full)


def _declared_dependency_names(package_payload: dict[str, Any]) -> set[str]:
    """Collect declared npm dependency names across all dependency sections."""
    names: set[str] = set()
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section_value = package_payload.get(section)
        if isinstance(section_value, dict):
            for dep in section_value:
                token = str(dep or "").strip()
                if token:
                    names.add(token)
    return names


def _ts_error_is_declared_dep_noise(line: str, declared_deps: set[str]) -> bool:
    """True when a tsc error is only a *declared-but-uninstalled* dependency resolution miss.

    Relative-import misses (``./foo``) are real errors and never treated as noise.
    """
    match = _TS_MODULE_MISSING_RE.search(line) or _TS_TYPES_MISSING_RE.search(line) or _TS_DECL_MISSING_RE.search(line)
    if not match:
        return False
    module_name = match.group(1).strip()
    if not module_name or module_name.startswith("."):
        return False
    parts = module_name.split("/")
    root = "/".join(parts[:2]) if module_name.startswith("@") and len(parts) >= 2 else parts[0]
    if root in _TEST_FRAMEWORK_TYPE_NOISE_MODULES and _ts_error_originates_from_test_file(line):
        return True
    return module_name in declared_deps or root in declared_deps


def _ts_error_originates_from_test_file(line: str) -> bool:
    path_hint = str(line or "").split(":", 1)[0].replace("\\", "/").lower()
    name = os.path.basename(path_hint)
    return "/tests/" in f"/{path_hint}" or "/test/" in f"/{path_hint}" or ".test." in name or ".spec." in name


def _run_typescript_typecheck(workspace_full: str) -> tuple[bool, str, list[str]] | None:
    """Run a real ``tsc --noEmit`` typecheck for TypeScript projects.

    Returns ``None`` when the gate is not applicable (disabled via
    ``KERNELONE_INTEGRATION_QA_TS_TYPECHECK=0``, not a TS project, or no ``tsc``
    resolvable), preserving prior behavior. Declared-but-uninstalled dependency
    resolution misses are ignored so the workspace's "no new deps" constraint does
    not produce false negatives; every other ``error TS####`` (syntax, type
    errors) fails the gate fail-closed.
    """
    flag = str(os.environ.get("KERNELONE_INTEGRATION_QA_TS_TYPECHECK", "1")).strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return None
    if not _is_typescript_project(workspace_full):
        return None
    tsc = _resolve_repo_tsc()
    if not tsc:
        logger.info("[integration-qa] TypeScript project detected but no tsc resolvable; skipping typecheck gate")
        return None

    package_payload = _read_package_json(workspace_full)
    if _node_dependencies_missing(workspace_full, package_payload) and _node_auto_install_allowed():
        install_ok, install_summary, install_errors = _prepare_node_dependencies_for_verify(
            workspace_full,
            ["npm"],
        )
        if not install_ok:
            return False, install_summary, install_errors
        if _node_dependencies_missing(workspace_full, package_payload):
            summary = "Integration dependency installation finished but node_modules is still missing before typecheck"
            return False, summary, [summary]
        logger.info("[integration-qa] %s", install_summary)

    timeout_raw = os.environ.get("KERNELONE_INTEGRATION_QA_TYPECHECK_TIMEOUT_SECONDS", "180")
    try:
        timeout_seconds = max(int(timeout_raw), 30)
    except (TypeError, ValueError):
        timeout_seconds = 180

    command = [tsc, "--noEmit", "--skipLibCheck", "--pretty", "false", "-p", "tsconfig.json"]
    try:
        completed = subprocess.run(
            command,
            cwd=workspace_full,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        summary = f"Integration verification failed: TypeScript typecheck timed out after {timeout_seconds}s"
        return False, summary, [summary]
    except (OSError, ValueError) as exc:
        logger.info("[integration-qa] tsc invocation failed (skipping typecheck gate): %s", exc)
        return None

    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    declared_deps = _declared_dependency_names(package_payload)
    real_errors: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or not _TS_ERROR_RE.search(line):
            continue
        if any(code in line for code in _TS_TYPECHECK_IGNORE_CODES):
            continue
        if _ts_error_is_declared_dep_noise(line, declared_deps):
            continue
        real_errors.append(line)

    if real_errors:
        summary = (
            f"Integration verification failed: TypeScript typecheck reported "
            f"{len(real_errors)} compiler error(s) (tsc --noEmit)"
        )
        return False, summary, real_errors[:20]
    return True, "TypeScript typecheck passed: tsc --noEmit", []


def run_integration_verify_runner(workspace_full: str) -> tuple[bool, str, list[str]]:
    # Real-compilation gate: for TypeScript projects a genuine `tsc --noEmit`
    # typecheck must pass before any (possibly structural) project script runs.
    # This prevents hollow build/test scripts from yielding a false-green.
    typecheck_result = _run_typescript_typecheck(workspace_full)
    if typecheck_result is not None:
        ts_passed, ts_summary, ts_errors = typecheck_result
        if not ts_passed:
            return False, ts_summary, ts_errors

    command = detect_integration_verify_command(workspace_full)
    timeout_seconds_raw = os.environ.get("KERNELONE_INTEGRATION_QA_TIMEOUT_SECONDS", "300")
    try:
        timeout_seconds = max(int(timeout_seconds_raw), 30)
    except (RuntimeError, ValueError):
        timeout_seconds = 300

    try:
        command_args = _parse_command_args(command)
    except ValueError as exc:
        summary = f"Integration verification command rejected: {exc}"
        return False, summary, [summary]

    package_payload = _read_package_json(workspace_full)
    missing_dependencies_block = _is_node_package_command(command_args) and _node_missing_dependencies_should_block(
        workspace_full,
        package_payload,
        command_args,
    )
    if missing_dependencies_block and _node_auto_install_allowed():
        install_ok, install_summary, install_errors = _prepare_node_dependencies_for_verify(
            workspace_full,
            command_args,
        )
        if not install_ok:
            return False, install_summary, install_errors
        package_payload = _read_package_json(workspace_full)
        if not _node_missing_dependencies_should_block(
            workspace_full,
            package_payload,
            command_args,
        ):
            logger.info("[integration-qa] %s", install_summary)
            missing_dependencies_block = False
        else:
            logger.info("[integration-qa] dependency install finished but node_modules is still missing")
    if missing_dependencies_block:
        if not _node_static_fallback_allowed():
            summary = (
                "Integration verification blocked: Node dependencies are declared but not installed "
                f"for command: {command}"
            )
            return (
                False,
                summary,
                [
                    summary,
                    "Run npm install/pnpm install/yarn install before integration QA, or set "
                    "KERNELONE_INTEGRATION_QA_ALLOW_STATIC_NODE_FALLBACK=1 for explicit structural-only QA.",
                ],
            )
        return _run_node_static_verify_runner(workspace_full, package_payload)

    try:
        cmd_svc = CommandExecutionService(workspace_full)
        request = CommandRequest(
            executable=command_args[0],
            args=command_args[1:] if len(command_args) > 1 else [],
            cwd=workspace_full,
            timeout_seconds=int(timeout_seconds) if timeout_seconds else 60,
        )
        result = cmd_svc.run(request)
    except (RuntimeError, ValueError) as exc:
        summary = f"Integration verification runtime error: {exc}"
        return False, summary, [summary]

    stdout_tail = _tail_non_empty_lines(result.get("stdout", ""), limit=6)
    stderr_tail = _tail_non_empty_lines(result.get("stderr", ""), limit=6)
    if int(result.get("returncode", -1)) == 0:
        artifact_errors = scan_workspace_artifact_quality(workspace_full)
        if artifact_errors:
            summary = f"Integration verification failed artifact quality scan after command passed: {command}"
            return False, summary, artifact_errors[:20]
        summary = f"Integration verification passed: {command}"
        return True, summary, []

    errors: list[str] = [f"Command failed ({result.get('returncode', -1)}): {command}"]
    errors.extend(f"[stdout] {line}" for line in stdout_tail)
    errors.extend(f"[stderr] {line}" for line in stderr_tail)
    summary = f"Integration verification failed: {command}"
    return False, summary, errors[:20]


def _default_integration_verify_runner(workspace_full: str) -> tuple[bool, str, list[str]]:
    """Default integration verify runner (wrapper around run_integration_verify_runner).

    This function provides a stable entry point for integration verification
    that can be used as a default callback in various contexts.

    Args:
        workspace_full: Workspace path

    Returns:
        Tuple of (success, summary, errors)
    """
    return run_integration_verify_runner(workspace_full)


# Re-export from task_quality_gate for backward compatibility
__all__ = [
    "_default_integration_verify_runner",
    "autofix_pm_contract_for_quality",
    "check_quality_promote_candidate",
    "detect_integration_verify_command",
    "evaluate_pm_task_quality",
    "get_quality_gate_config",
    "run_integration_verify_runner",
    "scan_workspace_artifact_quality",
]
