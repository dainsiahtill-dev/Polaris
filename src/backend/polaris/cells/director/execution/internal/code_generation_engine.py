"""Policy-guarded code generation engine.

WARNING - ABSOLUTE PROHIBITION:
This module MUST NOT generate code, template code, fallback code, or bootstrap
code for any LLM/AI/Agent workflow.

If any caller attempts to use this module for code writing, the call is blocked
fail-closed with explicit error/warning signals.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, NoReturn

logger = logging.getLogger(__name__)

CODE_WRITING_FORBIDDEN_WARNING = (
    "SECURITY POLICY VIOLATION: LLM/AI/Agent code-writing and fallback "
    "generation are strictly forbidden in "
    "polaris.cells.director.execution.internal.code_generation_engine."
)


class CodeGenerationPolicyViolationError(RuntimeError):
    """Raised when forbidden code-writing behavior is requested."""


def _raise_policy_violation(action: str) -> NoReturn:
    """Raise a fail-closed policy error for forbidden actions."""
    message = f"{CODE_WRITING_FORBIDDEN_WARNING} blocked_action={action}"
    logger.error(message)
    raise CodeGenerationPolicyViolationError(message)


class CodeGenerationEngine:
    """Policy guard for legacy code-generation entry points.

    This class intentionally blocks code generation behavior. It preserves
    selected utility methods and legacy method signatures to avoid import-time
    breakage while enforcing a strict no-code-writing policy.
    """

    def __init__(
        self,
        workspace: str,
        executor: Any,
    ) -> None:
        self.workspace = workspace
        self._executor = executor

    # === Timeout and Configuration Resolution ===

    def resolve_llm_timeout(self, default_timeout: int) -> int:
        """Resolve per-call LLM timeout with sane upper/lower bounds."""
        raw = os.environ.get("POLARIS_WORKER_LLM_TIMEOUT", "")
        try:
            timeout = int(raw) if raw else int(default_timeout)
        except ValueError:
            timeout = int(default_timeout)
        if timeout <= 0:
            timeout = int(default_timeout)
        return min(max(timeout, 15), 300)

    def resolve_task_timeout_budget(self, task: Any, *, rounds: int) -> int:
        """Resolve total timeout budget for one task, not per round."""
        raw = os.environ.get("POLARIS_WORKER_TOTAL_TIMEOUT", "")
        try:
            configured = int(raw) if raw else 0
        except ValueError:
            configured = 0

        if configured > 0:
            return min(max(configured, 30), 1800)

        base_timeout = int(getattr(task, "timeout_seconds", 0) or 0)
        if base_timeout <= 0:
            base_timeout = self.resolve_llm_timeout(120) * max(1, min(rounds, 2))
        return min(max(base_timeout, 30), 1800)

    def remaining_timeout(self, deadline_ts: float) -> int:
        """Return remaining whole seconds to deadline."""
        return max(0, int(deadline_ts - time.time()))

    def resolve_patch_retry_attempts(self) -> int:
        """Resolve retry attempts for legacy call sites."""
        raw = os.environ.get("POLARIS_WORKER_PATCH_RETRIES", "2")
        try:
            attempts = int(raw)
        except ValueError:
            attempts = 2
        return min(max(attempts, 1), 4)

    # === Environment Flags ===

    def _env_flag(self, name: str, default: bool = False) -> bool:
        raw = str(os.environ.get(name) or "").strip().lower()
        if not raw:
            return bool(default)
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    def stress_strict_mode_enabled(self) -> bool:
        """Return strict-mode switch."""
        return self._env_flag("POLARIS_STRESS_STRICT", default=False)

    def allow_template_fallback(self, task: Any | None = None) -> bool:
        """Always deny template fallback to enforce policy."""
        _ = task  # keep signature compatibility
        logger.warning(
            "%s blocked_action=allow_template_fallback",
            CODE_WRITING_FORBIDDEN_WARNING,
        )
        return False

    def resolve_spin_guard_repeat_limit(self) -> int:
        """Resolve spin-guard limit for legacy call sites."""
        raw = os.environ.get("POLARIS_WORKER_SPIN_MAX_REPEAT", "3")
        try:
            repeats = int(raw)
        except ValueError:
            repeats = 3
        return min(max(repeats, 2), 8)

    # === Low Signal Detection ===

    def is_low_signal_response(self, response: str) -> bool:
        """Check low-signal responses (utility retained for compatibility)."""
        text = str(response or "").strip()
        raw = os.environ.get("POLARIS_WORKER_LOW_SIGNAL_CHARS", "180")
        try:
            min_chars = int(raw)
        except ValueError:
            min_chars = 180
        min_chars = min(max(min_chars, 40), 1200)
        if len(text) < min_chars:
            return True
        lowered = text.lower()
        refusal_markers = (
            "need more context",
            "cannot complete",
            "can't complete",
            "无法完成",
            "需要更多信息",
            "请提供更多",
        )
        return any(marker in lowered for marker in refusal_markers)

    # === Spin Guard ===

    def register_spin_guard(
        self,
        tracker: dict[str, dict[str, Any]],
        *,
        scope: str,
        prompt: str,
        output: str,
    ) -> None:
        """Register spin guard and detect repeated prompt-output pairs."""
        prompt_hash = hashlib.sha1(str(prompt or "").strip().encode("utf-8")).hexdigest()
        output_hash = hashlib.sha1(str(output or "").strip().encode("utf-8")).hexdigest()
        prev_raw = tracker.get(scope)
        previous: dict[str, Any] = prev_raw if isinstance(prev_raw, dict) else {}
        same_pair = (
            str(previous.get("prompt_hash") or "") == prompt_hash
            and str(previous.get("output_hash") or "") == output_hash
        )
        repeat_count = int(previous.get("repeat_count") or 0) + 1 if same_pair else 1
        tracker[scope] = {
            "prompt_hash": prompt_hash,
            "output_hash": output_hash,
            "repeat_count": repeat_count,
        }
        limit = self.resolve_spin_guard_repeat_limit()
        if repeat_count >= limit:
            raise RuntimeError(f"WORKER_SPIN_GUARD[{scope}] repeated identical prompt+output x{repeat_count}")

    # === Blocked code-writing entry points ===

    def invoke_runtime_provider(
        self,
        *,
        prompt: str,
        model: str,
        timeout: int,
    ) -> NoReturn:
        """Blocked: runtime provider invocation for code writing."""
        _ = (prompt, model, timeout)
        _raise_policy_violation("invoke_runtime_provider")

    def invoke_ollama(
        self,
        *,
        prompt: str,
        model: str,
        timeout: int,
    ) -> NoReturn:
        """Blocked: LLM invocation for code writing."""
        _ = (prompt, model, timeout)
        _raise_policy_violation("invoke_ollama")

    def build_patch_retry_prompt(
        self,
        task: Any,
        *,
        round_files: list[str] | None,
        round_label: str,
    ) -> NoReturn:
        """Blocked: patch prompt construction for code writing."""
        _ = (task, round_files, round_label)
        _raise_policy_violation("build_patch_retry_prompt")

    async def invoke_generation_with_retries(
        self,
        *,
        task: Any,
        prompt: str,
        model: str,
        per_call_timeout: int,
        deadline_ts: float,
        round_label: str,
        round_files: list[str] | None,
        spin_tracker: dict[str, dict[str, Any]],
    ) -> tuple[list[dict], list[str]]:
        """Blocked: all code generation attempts are forbidden.

        Returns:
            Empty file list and one blocking warning, so callers can degrade
            gracefully without implicit code generation.
        """
        _ = (
            task,
            prompt,
            model,
            per_call_timeout,
            deadline_ts,
            round_label,
            round_files,
            spin_tracker,
        )
        warning = f"{CODE_WRITING_FORBIDDEN_WARNING} blocked_action=invoke_generation_with_retries"
        logger.error(warning)
        return [], [warning]


def generate_fallback_code_content(path: str, language: str, task_subject: str) -> NoReturn:
    """Blocked: deterministic fallback code generation is forbidden."""
    _ = (path, language, task_subject)
    _raise_policy_violation("generate_fallback_code_content")


def generate_phase_aware_fallback_content(
    path: str,
    language: str,
    task_subject: str,
    phase: str,
) -> NoReturn:
    """Blocked: phase-aware fallback code generation is forbidden."""
    _ = (path, language, task_subject, phase)
    _raise_policy_violation("generate_phase_aware_fallback_content")


async def generate_bootstrap_with_llm(
    workspace: str,
    task_subject: str,
    task_description: str,
    language: str,
    framework: str | None,
    timeout_override: int | None = None,
    invoke_func: Any = None,
) -> NoReturn:
    """Blocked: bootstrap code generation via LLM is forbidden."""
    _ = (
        workspace,
        task_subject,
        task_description,
        language,
        framework,
        timeout_override,
        invoke_func,
    )
    _raise_policy_violation("generate_bootstrap_with_llm")
