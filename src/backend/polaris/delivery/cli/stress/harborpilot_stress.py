"""Polaris stress helpers.

This module contains deterministic helper functions used by stress harness tests.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_PM_FALLBACK_NOTES_TOKENS = (
    "fallback tasks",
    "fallback",
    "empty/invalid task list",
)

_PM_FALLBACK_TASK_ID_RE = re.compile(r"\bPM-[A-Za-z0-9_-]*-F\d+\b", re.IGNORECASE)
_FALLBACK_LOG_RE = re.compile(r"fallback generated", re.IGNORECASE)
_LOW_SIGNAL_RETRY_RE = re.compile(r"low-signal output,\s*retrying", re.IGNORECASE)


def _build_pm_command(
    workspace: Path,
    *,
    pm_backend: str,
    director_iterations: int,
    timeout: int,
    director_timeout: int,
    start_from: str,
    directive_via_stdin: bool,
    run_director: bool,
) -> list[str]:
    """Build PM CLI command with explicit backend and stress defaults."""
    cmd: list[str] = [
        sys.executable,
        "-m",
        "polaris.delivery.cli.pm.cli",
        "--workspace",
        str(workspace),
        "--start-from",
        str(start_from or "pm"),
        "--pm-backend",
        str(pm_backend or "auto"),
        "--timeout",
        str(int(timeout or 0)),
        "--director-iterations",
        str(max(int(director_iterations or 1), 1)),
        "--director-result-timeout",
        str(max(int(director_timeout or 0), 0)),
        "--chief-engineer-mode",
        "on",
    ]
    if directive_via_stdin:
        cmd.append("--directive-via-stdin")
    if run_director:
        cmd.append("--run-director")
    return cmd


def _build_round_directive(round_no: int, directive_text: str) -> str:
    """Return a stress-safe round directive.

    If input already looks like requirements markdown, keep it unchanged.
    """
    raw = str(directive_text or "")
    lowered = raw.lower()
    if "product requirements" in lowered or "acceptance criteria" in lowered:
        return raw

    orchestration_markers = (
        "polaris",
        "压力测试",
        "支持的语言",
        "每轮执行步骤",
        "开始执行压力测试循环",
    )
    if any(marker in raw.lower() for marker in orchestration_markers) or any(
        marker in raw for marker in ("压力测试", "支持的语言", "每轮执行步骤", "开始执行压力测试循环")
    ):
        return (
            "# Product Requirements\n"
            f"- Round {int(round_no)} deep implementation cycle\n"
            "- Implement at least one concrete module change with tests\n"
            "- Keep runtime artifacts under `runtime/` and include evidence paths\n\n"
            "# Acceptance Criteria\n"
            "- Include executable verification commands\n"
            "- Include at least one `test` command and expected signal\n"
            "- Provide deterministic file-level evidence\n"
        )
    return raw


def _resolve_phase_timeout(
    *,
    base_timeout: int,
    director_timeout: int,
    buffer_seconds: int,
    min_seconds: int,
    max_seconds: int,
) -> int:
    """Resolve timeout with buffer and clamp."""
    base = max(int(base_timeout or 0), int(director_timeout or 0))
    resolved = base + max(int(buffer_seconds or 0), 0)
    resolved = max(resolved, int(min_seconds or 0))
    resolved = min(resolved, int(max_seconds or resolved))
    return int(resolved)


def _is_agents_content_usable(content: str, *, min_bytes: int = 256) -> bool:
    """Return True when AGENTS.md content is sufficiently structured."""
    text = str(content or "")
    if len(text.encode("utf-8")) < max(int(min_bytes), 1):
        return False
    lowered = text.lower()
    if "<instructions>" not in lowered or "</instructions>" not in lowered:
        return False
    required_signals = ("utf-8", "verification", "runtime", "evidence")
    return sum(1 for token in required_signals if token in lowered) >= 2


def _pm_fallback_detected(pm_contract: dict[str, Any]) -> bool:
    """Detect PM fallback payload from notes and synthetic task IDs."""
    if not isinstance(pm_contract, dict):
        return False
    notes = str(pm_contract.get("notes") or "").lower()
    if any(token in notes for token in _PM_FALLBACK_NOTES_TOKENS):
        return True
    raw_tasks = pm_contract.get("tasks")
    tasks: list[Any] = raw_tasks if isinstance(raw_tasks, list) else []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        if _PM_FALLBACK_TASK_ID_RE.search(task_id):
            return True
    return False


def _extract_primary_file(
    pm_contract: dict[str, Any],
    *,
    primary_file_hint: str,
) -> tuple[str, int]:
    engine_execution = pm_contract.get("engine_execution")
    records = engine_execution.get("records") if isinstance(engine_execution, dict) else []
    counter: Counter[str] = Counter()
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        payload = record.get("result_payload")
        changed_files = payload.get("changed_files") if isinstance(payload, dict) else []
        if not isinstance(changed_files, list):
            continue
        for item in changed_files:
            token = str(item or "").strip().replace("\\", "/")
            if token:
                counter[token] += 1

    hint = str(primary_file_hint or "").strip().replace("\\", "/")
    if hint:
        return hint, int(counter.get(hint, 0))
    if not counter:
        return "", 0
    primary_file, touches = counter.most_common(1)[0]
    return primary_file, int(touches)


def _count_primary_lines(workspace: Path, primary_file: str) -> int:
    if not primary_file:
        return 0
    full = (Path(workspace) / primary_file).resolve()
    if not full.is_file():
        return 0
    try:
        return len(full.read_text(encoding="utf-8").splitlines())
    except (RuntimeError, ValueError):
        return 0


def _count_template_fallback_hits(runtime_root: Path) -> int:
    if not Path(runtime_root).exists():
        return 0
    hits = 0
    for log_file in Path(runtime_root).rglob("*.log"):
        try:
            text = log_file.read_text(encoding="utf-8")
        except (RuntimeError, ValueError):
            continue
        for line in text.splitlines():
            if _LOW_SIGNAL_RETRY_RE.search(line):
                continue
            if _FALLBACK_LOG_RE.search(line):
                hits += 1
    return hits


def _evaluate_strict_depth(
    *,
    workspace: Path,
    runtime_root: Path,
    pm_contract: dict[str, Any],
    directive_text: str,
    primary_file_hint: str,
    min_rounds: int,
    min_primary_lines: int,
    require_llm_output: bool,
) -> tuple[bool, list[str], str, int, int, int]:
    """Evaluate strict depth constraints for stress runs."""
    reasons: list[str] = []
    primary_file, touches = _extract_primary_file(
        pm_contract if isinstance(pm_contract, dict) else {},
        primary_file_hint=primary_file_hint,
    )
    lines = _count_primary_lines(Path(workspace), primary_file)
    fallback_hits = _count_template_fallback_hits(Path(runtime_root))

    if touches < max(int(min_rounds or 0), 0):
        reasons.append(f"primary_file_touches<{int(min_rounds)}")
    if lines < max(int(min_primary_lines or 0), 0):
        reasons.append(f"primary_file_lines<{int(min_primary_lines)}")
    if fallback_hits > 0:
        reasons.append("template_fallback_hits>0")
    if require_llm_output and _pm_fallback_detected(pm_contract if isinstance(pm_contract, dict) else {}):
        reasons.append("pm_fallback_payload_detected")

    passed = len(reasons) == 0
    return passed, reasons, primary_file, touches, lines, fallback_hits


__all__ = [
    "_build_pm_command",
    "_build_round_directive",
    "_evaluate_strict_depth",
    "_is_agents_content_usable",
    "_pm_fallback_detected",
    "_resolve_phase_timeout",
]
