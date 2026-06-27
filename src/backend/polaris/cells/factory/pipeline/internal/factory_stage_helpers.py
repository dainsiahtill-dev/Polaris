"""Pure helper functions for the factory stage executor.

These are the side-effect-free building blocks extracted verbatim from
``OrchestrationStageExecutor`` (text shaping, delivery-target normalization,
director-evidence truth tables, env/bool resolution, command resolution, and
output trimming). ``OrchestrationStageExecutor`` keeps same-named delegating
shims so every existing test-called / subclassed entry point is preserved.

Monkeypatch note: ``resolve_workspace_quality_command`` references ``shutil``
and ``os`` through the module namespace at call time. The historical tests
monkeypatch ``factory_run_service.shutil.which`` / ``factory_run_service.os.name``;
because Python caches module objects, those patches mutate the shared ``shutil``
/ ``os`` module objects this module also imports, so resolution stays patchable.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from .factory_run_models import (
    _PM_DIRECTIVE_META_LINE_PATTERN,
    _PM_PLAN_META_DIAGNOSTIC_MARKERS,
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
)

_DECLARED_FILE_TOKEN_RE = re.compile(
    r"(?<![\w./-])"
    r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\."
    r"(?:py|txt|toml|json|md|html|js|ts|tsx|jsx|css|yaml|yml|go|mod|sum|sh)"
    r"(?![\w.-])"
)
_FILE_AS_DIRECTORY_SUFFIXES = frozenset(
    {
        ".css",
        ".go",
        ".html",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".mod",
        ".py",
        ".sh",
        ".sum",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_MAX_DECLARED_DELIVERY_TARGET_CHARS = 240
_PATHLIKE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./@+-]+$")


def extend_artifacts(artifacts: list[str], *paths: str) -> None:
    seen = set(artifacts)
    for path in paths:
        normalized = str(path or "").replace("\\", "/").strip().lstrip("/")
        if not normalized or normalized in seen:
            continue
        artifacts.append(normalized)
        seen.add(normalized)


def normalize_declared_delivery_target(value: Any) -> str:
    token = str(value or "").replace("\\", "/").strip().strip("`'\"")
    if (
        not token
        or "\n" in token
        or "\r" in token
        or len(token) > _MAX_DECLARED_DELIVERY_TARGET_CHARS
        or not _PATHLIKE_TOKEN_RE.fullmatch(token)
    ):
        return ""
    while token.startswith("./"):
        token = token[2:]
    token = token.lstrip("/")
    if token.startswith("workspace/"):
        token = token.removeprefix("workspace/")
    if not token or token.endswith("/"):
        return ""
    lowered = token.lower()
    if lowered.startswith(("http://", "https://", "#")):
        return ""
    parts = tuple(part for part in token.split("/") if part)
    if not parts or any(part in {"", ".."} for part in parts):
        return ""
    if any(len(part) > 120 for part in parts):
        return ""
    if parts[0] in {".git", ".polaris", "runtime"}:
        return ""
    for index, part in enumerate(parts[:-1]):
        if Path(part).suffix.lower() in _FILE_AS_DIRECTORY_SUFFIXES:
            return "/".join(parts[: index + 1])
    return token


def collect_declared_delivery_targets(tasks: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    targets: list[str] = []

    def add(value: Any, *, require_file_like: bool = False) -> None:
        normalized = normalize_declared_delivery_target(value)
        if not normalized:
            return
        if require_file_like and not (
            Path(normalized).suffix or normalized.upper().startswith("README") or normalized.startswith("tests/")
        ):
            return
        if normalized in seen:
            return
        targets.append(normalized)
        seen.add(normalized)

    for task in tasks:
        if not isinstance(task, dict):
            continue
        for field in ("target_files", "output_files", "expected_files"):
            raw_values = task.get(field)
            if isinstance(raw_values, str):
                add(raw_values)
            elif isinstance(raw_values, (list, tuple, set)):
                for item in raw_values:
                    add(item)
        raw_scope_paths = task.get("scope_paths")
        if isinstance(raw_scope_paths, str):
            add(raw_scope_paths, require_file_like=True)
        elif isinstance(raw_scope_paths, (list, tuple, set)):
            for item in raw_scope_paths:
                add(item, require_file_like=True)
        scope = str(task.get("scope") or "")
        for item in scope.replace("\n", ",").split(","):
            add(item, require_file_like=True)
        for field in ("goal", "description", "steps", "acceptance", "acceptance_criteria", "execution_checklist"):
            raw_value = task.get(field)
            values = raw_value if isinstance(raw_value, (list, tuple, set)) else [raw_value]
            for value in values:
                text = str(value or "")
                for match in _DECLARED_FILE_TOKEN_RE.finditer(text):
                    add(match.group(0), require_file_like=True)
    return targets


def artifact_file_ready(target: Path) -> bool:
    """Return whether an expected stage artifact is present after upstream completion."""
    try:
        return target.exists() and target.is_file() and target.stat().st_size > 0
    except OSError:
        return False


def is_substantive_doc_text(text: str, *, min_chars: int = 200) -> bool:
    normalized = str(text or "").strip()
    if len(normalized) < min_chars:
        return False
    heading_count = len([line for line in normalized.splitlines() if str(line or "").strip().startswith("#")])
    return heading_count >= 2


def is_pm_meta_diagnostic_task(task: dict[str, Any]) -> bool:
    text = "\n".join(
        str(task.get(key) or "").strip() for key in ("title", "goal", "description") if str(task.get(key) or "").strip()
    ).lower()
    if not text:
        return False
    return any(marker.lower() in text for marker in _PM_PLAN_META_DIAGNOSTIC_MARKERS)


def compact_text_for_prompt(text: str, *, max_chars: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    head_chars = max(max_chars * 2 // 3, 1)
    tail_chars = max(max_chars - head_chars, 1)
    omitted = len(normalized) - head_chars - tail_chars
    return (
        normalized[:head_chars].rstrip()
        + f"\n\n[... omitted {omitted} chars for PM planning context ...]\n\n"
        + normalized[-tail_chars:].lstrip()
    )


def strip_prompt_meta_lines(text: str) -> str:
    lines = [
        line for line in str(text or "").splitlines() if not _PM_DIRECTIVE_META_LINE_PATTERN.search(str(line or ""))
    ]
    return "\n".join(lines).strip()


def build_director_task_filter(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "Execute ready tasks from PM contract"
    lines: list[str] = []
    for task in tasks[:4]:
        title = str(task.get("title") or task.get("goal") or "").strip()
        scope = str(task.get("scope") or "").strip()
        if not title:
            continue
        if scope:
            lines.append(f"- {title} [scope: {scope}]")
        else:
            lines.append(f"- {title}")
    if not lines:
        return "Execute ready tasks from PM contract"
    return "Execute PM tasks strictly in order:\n" + "\n".join(lines)


def task_string(task: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, (dict, list, tuple, set)):
            token = str(value).strip()
            if token:
                return token
    return ""


def task_string_list(task: dict[str, Any], *keys: str) -> list[str]:
    rows: list[str] = []
    for key in keys:
        value = task.get(key)
        if isinstance(value, list):
            for item in value:
                token = str(item or "").strip()
                if token:
                    rows.append(token)
        elif isinstance(value, str) and value.strip():
            rows.append(value.strip())
    return rows


def is_taskboard_converged(stats: dict[str, int]) -> bool:
    return (
        int(stats.get("pending") or 0) <= 0
        and int(stats.get("ready") or 0) <= 0
        and int(stats.get("in_progress") or 0) <= 0
        and int(stats.get("in_design") or 0) <= 0
        and int(stats.get("in_execution") or 0) <= 0
        and int(stats.get("in_qa") or 0) <= 0
        and int(stats.get("running") or 0) <= 0
        and int(stats.get("processing") or 0) <= 0
        and int(stats.get("executing") or 0) <= 0
        and int(stats.get("waiting_human") or 0) <= 0
        and int(stats.get("blocked") or 0) <= 0
    )


def has_director_progress(before: dict[str, int], after: dict[str, int]) -> bool:
    return any(
        int(after.get(key) or 0) != int(before.get(key) or 0)
        for key in (
            "pending",
            "ready",
            "in_progress",
            "in_design",
            "in_execution",
            "in_qa",
            "running",
            "processing",
            "executing",
            "waiting_human",
            "completed",
            "failed",
            "blocked",
            "cancelled",
            "timeout",
        )
    )


def has_director_execution_evidence(
    *,
    attempts: list[dict[str, Any]],
    initial_stats: dict[str, int],
    final_stats: dict[str, int],
    converged: bool,
) -> bool:
    completed_delta = int(final_stats.get("completed") or 0) - int(initial_stats.get("completed") or 0)
    failed_delta = int(final_stats.get("failed") or 0) - int(initial_stats.get("failed") or 0)
    if completed_delta > 0 or failed_delta > 0:
        return True

    for attempt in attempts:
        if bool(attempt.get("progress_made")):
            return True
        metadata = attempt.get("metadata")
        if not isinstance(metadata, dict):
            continue
        counts = metadata.get("task_status_counts")
        if not isinstance(counts, dict):
            continue
        try:
            total = sum(int(value or 0) for value in counts.values())
        except (TypeError, ValueError):
            total = 0
        if total > 0:
            return True

    return bool(converged and int(final_stats.get("completed") or 0) > 0)


def metadata_indicates_execution(metadata: dict[str, Any]) -> bool:
    if not isinstance(metadata, dict):
        return False
    counts = metadata.get("task_status_counts")
    if not isinstance(counts, dict):
        return False
    try:
        completed = int(counts.get("completed") or 0)
        failed = int(counts.get("failed") or 0)
        blocked = int(counts.get("blocked") or 0)
        cancelled = int(counts.get("cancelled") or 0)
    except (TypeError, ValueError):
        return False
    return (completed + failed + blocked + cancelled) > 0


def is_director_no_materialized_changes(result: Any) -> bool:
    if str(result.status or "").strip().lower() not in {"failed", "error"}:
        return False
    message = str(result.message or "").strip().lower()
    if "director_no_materialized_changes" in message:
        return True
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    failed_tasks = metadata.get("failed_tasks")
    if isinstance(failed_tasks, list):
        for item in failed_tasks:
            if not isinstance(item, dict):
                continue
            if "director_no_materialized_changes" in str(item.get("error_message") or "").lower():
                return True
    return False


def bool_from_context_or_env(
    context: dict[str, Any],
    *keys: str,
    env_var: str = "",
    default: bool = True,
) -> bool:
    raw: Any = None
    for key in keys:
        if key in context:
            raw = context.get(key)
            break
    if raw is None and env_var:
        raw = os.environ.get(env_var)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    token = str(raw).strip().lower()
    if token in {"1", "true", "yes", "on", "enabled"}:
        return True
    if token in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def trim_command_output(text: str, limit: int = _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS) -> str:
    body = str(text or "")
    if len(body) <= limit:
        return body
    return body[-limit:]


def resolve_workspace_quality_command(command: list[str]) -> list[str]:
    if not command:
        return []
    executable = str(command[0] or "").strip()
    if not executable:
        return []
    resolved = shutil.which(executable)
    if resolved is None and os.name == "nt":
        for suffix in (".cmd", ".exe", ".bat"):
            resolved = shutil.which(f"{executable}{suffix}")
            if resolved:
                break
    if not resolved:
        return []
    return [resolved, *command[1:]]


def qa_report_has_warning(payload: dict[str, Any], warning: str) -> bool:
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        return any(str(item or "").strip() == warning for item in warnings)
    if isinstance(warnings, str):
        return any(part.strip() == warning for part in warnings.split(","))
    return False
