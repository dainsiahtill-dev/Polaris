"""Shared quality helpers for PM task contracts and integration QA checks."""

from __future__ import annotations

import logging
import os
import re
import shlex
from typing import Any

from polaris.cells.orchestration.pm_planning.internal.task_quality_gate import (
    autofix_pm_contract_for_quality,
    check_quality_promote_candidate,
    evaluate_pm_task_quality,
    get_quality_gate_config,
)
from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest

logger = logging.getLogger(__name__)

_PM_PROMPT_LEAK_TOKENS = (
    "you are ",
    "角色设定",
    "system prompt",
    "no yapping",
    "<thinking>",
    "<tool_call>",
    "提示词",
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
    return any(token in lowered for token in _PM_PROMPT_LEAK_TOKENS)


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
            return "python -m pytest --collect-only -q"
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
        return "python -m compileall -q " + " ".join(unique_targets)
    if any(os.path.isfile(os.path.join(workspace_full, item)) for item in markers["node"]):
        return "npm run -s test -- --watch=false"
    if any(os.path.isfile(os.path.join(workspace_full, item)) for item in markers["go"]):
        return "go test ./... -run TestDoesNotExist"
    if any(os.path.isfile(os.path.join(workspace_full, item)) for item in markers["rust"]):
        return "cargo test --no-run"
    return "python -m compileall -q ."


def run_integration_verify_runner(workspace_full: str) -> tuple[bool, str, list[str]]:
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
]
