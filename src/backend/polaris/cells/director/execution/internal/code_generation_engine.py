"""Director runtime code generation bridge.

Legacy deterministic code generation, template fallback, and emergency bootstrap
helpers remain forbidden. Real code writing is only allowed through the Director
role runtime when explicitly enabled by environment, so writes go through the
same LLM/tool policy and workspace guards as the interactive Director role.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Any, NoReturn

logger = logging.getLogger(__name__)

CODE_WRITING_FORBIDDEN_WARNING = (
    "SECURITY POLICY VIOLATION: deterministic/fallback code generation "
    "is strictly forbidden in "
    "polaris.cells.director.execution.internal.code_generation_engine."
)
_RUNTIME_CODEGEN_ENV = "KERNELONE_DIRECTOR_RUNTIME_CODEGEN"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


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
        raw = os.environ.get("KERNELONE_WORKER_LLM_TIMEOUT", "")
        try:
            timeout = int(raw) if raw else int(default_timeout)
        except ValueError:
            timeout = int(default_timeout)
        if timeout <= 0:
            timeout = int(default_timeout)
        return min(max(timeout, 15), 300)

    def resolve_task_timeout_budget(self, task: Any, *, rounds: int) -> int:
        """Resolve total timeout budget for one task, not per round."""
        raw = os.environ.get("KERNELONE_WORKER_TOTAL_TIMEOUT", "")
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
        raw = os.environ.get("KERNELONE_WORKER_PATCH_RETRIES", "2")
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
        return self._env_flag("KERNELONE_STRESS_STRICT", default=False)

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
        raw = os.environ.get("KERNELONE_WORKER_SPIN_MAX_REPEAT", "3")
        try:
            repeats = int(raw)
        except ValueError:
            repeats = 3
        return min(max(repeats, 2), 8)

    # === Low Signal Detection ===

    def is_low_signal_response(self, response: str) -> bool:
        """Check low-signal responses (utility retained for compatibility)."""
        text = str(response or "").strip()
        raw = os.environ.get("KERNELONE_WORKER_LOW_SIGNAL_CHARS", "180")
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

    # === Runtime Director bridge ===

    def runtime_codegen_enabled(self) -> bool:
        """Return whether real Director runtime code writing is explicitly enabled."""
        return _env_flag(_RUNTIME_CODEGEN_ENV, default=False)

    async def _invoke_director_role_response(
        self,
        *,
        task: Any,
        prompt: str,
        timeout: int,
        round_label: str,
        round_files: list[str] | None,
    ) -> dict[str, Any]:
        """Invoke the canonical Director role runtime for one generation round."""
        from polaris.bootstrap import config as backend_config
        from polaris.cells.llm.dialogue.public.service import generate_role_response

        try:
            settings = backend_config.get_settings()
        except AttributeError:
            settings = None

        task_id = str(getattr(task, "id", "") or "").strip()
        context = {
            "task_id": task_id,
            "round_label": str(round_label or "").strip(),
            "target_files": list(round_files or []),
            "llm_call_timeout_seconds": timeout,
            "director_runtime_codegen": True,
            "director_runtime_codegen_mode": "proposal_then_apply",
            "delivery_mode": "propose_patch",
            "disable_internal_tool_rounds": True,
        }
        target_hint = ", ".join(path for path in (round_files or []) if str(path or "").strip())
        if not target_hint:
            target_hint = "the listed target files"
        user_message = (
            "[mode:propose] Non-interactive batch worker. Return only fenced "
            f"file sections for: {target_hint}. The first non-whitespace text "
            "must be ```file:. No progress notes. Do not call tools."
        )
        appendix = (
            "Polaris Director proposal-to-apply bridge. The user message is "
            "intentionally in PROPOSE mode because this bridge validates and "
            "applies the returned file blocks through FileApplyService. Return "
            "only PATCH_FILE blocks or fenced file sections for the target files; "
            "do not ask follow-up questions, do not narrate phases/progress, and "
            "do not return placeholder content. The response must contain at "
            "least one parsable file operation."
            "\n\n"
            f"{prompt}"
        )
        return await asyncio.wait_for(
            generate_role_response(
                workspace=self.workspace,
                settings=settings,
                role="director",
                message=user_message,
                context=context,
                validate_output=False,
                max_retries=1,
                prompt_appendix=appendix,
                enable_cognitive=False,
            ),
            timeout=max(1.0, float(timeout)),
        )

    @staticmethod
    def _extract_response_text(response: dict[str, Any]) -> str:
        return str(response.get("response") or response.get("content") or response.get("reply") or "").strip()

    @staticmethod
    def _normalize_tool_results(response: dict[str, Any]) -> list[dict[str, Any]]:
        raw_results = response.get("tool_results")
        if not isinstance(raw_results, list):
            raw_results = response.get("tool_calls")
        if not isinstance(raw_results, list):
            return []
        return [dict(item) for item in raw_results if isinstance(item, dict)]

    @staticmethod
    def _extract_written_files_from_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, str]]:
        files: list[dict[str, str]] = []
        seen: set[str] = set()
        write_tools = {"write_file", "edit_file", "patch_apply", "append_to_file", "search_replace"}
        for item in tool_results:
            tool_name = str(item.get("tool") or item.get("name") or "").strip().lower()
            if tool_name not in write_tools or not bool(item.get("success")):
                continue
            result = item.get("result")
            candidates: list[Any] = []
            if isinstance(result, dict):
                candidates.extend(
                    [
                        result.get("file"),
                        result.get("path"),
                        result.get("file_path"),
                    ]
                )
                changed_files = result.get("changed_files")
                if isinstance(changed_files, list):
                    candidates.extend(changed_files)
            for candidate in candidates:
                path = str(candidate or "").strip()
                if path and path not in seen:
                    seen.add(path)
                    files.append({"path": path, "content": ""})
        return files

    def _collect_existing_round_files(self, round_files: list[str] | None) -> list[dict[str, str]]:
        files: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw_path in round_files or []:
            path = str(raw_path or "").strip()
            if not path or path in seen:
                continue
            full_path = os.path.join(self.workspace, path)
            if os.path.isfile(full_path):
                seen.add(path)
                files.append({"path": path, "content": ""})
        return files

    def _apply_response_operations(
        self,
        *,
        response_text: str,
        task_id: str,
        llm_metadata: dict[str, Any],
    ) -> tuple[list[dict], list[str]]:
        apply_func = getattr(self._executor, "_apply_response_operations", None)
        if not callable(apply_func):
            return [], ["director executor cannot apply response operations"]
        applied_files, errors = apply_func(
            response_text,
            task_id=task_id,
            llm_metadata=llm_metadata,
        )
        normalized_files = [dict(item) for item in applied_files if isinstance(item, dict)]
        normalized_errors = [str(item) for item in errors if str(item or "").strip()]
        return normalized_files, normalized_errors

    # === Blocked legacy entry points ===

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
        """Generate code through the Director role runtime when explicitly enabled."""
        _ = model
        if not self.runtime_codegen_enabled():
            warning = (
                f"{CODE_WRITING_FORBIDDEN_WARNING} "
                f"blocked_action=invoke_generation_with_retries; enable {_RUNTIME_CODEGEN_ENV}=1 "
                "to use the audited Director runtime bridge"
            )
            logger.error(warning)
            return [], [warning]

        warnings: list[str] = []
        task_id = str(getattr(task, "id", "") or "").strip()
        attempts = self.resolve_patch_retry_attempts()
        current_prompt = prompt

        for attempt in range(1, attempts + 1):
            remaining = self.remaining_timeout(deadline_ts)
            if remaining <= 0:
                warnings.append("director_runtime_codegen_deadline_exhausted")
                break
            timeout = min(max(int(per_call_timeout or 0), 15), remaining)
            try:
                response = await self._invoke_director_role_response(
                    task=task,
                    prompt=current_prompt,
                    timeout=timeout,
                    round_label=f"{round_label}:attempt-{attempt}",
                    round_files=round_files,
                )
            except (asyncio.TimeoutError, TimeoutError):
                warnings.append(f"director_runtime_codegen_timeout:{timeout}s")
                break
            except (
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                warnings.append(f"director_runtime_codegen_invoke_failed:{exc}")
                continue

            response_text = self._extract_response_text(response)
            try:
                self.register_spin_guard(
                    spin_tracker,
                    scope=f"{task_id or 'task'}:{round_label}",
                    prompt=current_prompt,
                    output=response_text,
                )
            except RuntimeError as exc:
                warnings.append(str(exc))
                break

            tool_files = self._extract_written_files_from_tool_results(self._normalize_tool_results(response))
            if tool_files:
                return tool_files, warnings

            if response_text:
                applied_files, apply_errors = self._apply_response_operations(
                    response_text=response_text,
                    task_id=task_id,
                    llm_metadata={
                        "provider": response.get("provider"),
                        "model": response.get("model"),
                        "attempt": attempt,
                        "round_label": round_label,
                    },
                )
                if applied_files:
                    return applied_files, [*warnings, *apply_errors]
                warnings.extend(apply_errors)
            else:
                warnings.append("director_runtime_codegen_empty_response")

            current_prompt = (
                f"{prompt}\n\nPrevious attempt {attempt} produced no workspace changes. "
                "Return valid PATCH_FILE blocks or fenced file sections for the listed target files."
            )

        if not warnings:
            warnings.append("director_runtime_codegen_no_files_created")
        return [], warnings


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
