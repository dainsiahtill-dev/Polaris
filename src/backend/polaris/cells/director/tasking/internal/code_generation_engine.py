"""Director tasking runtime code generation bridge.

Deterministic code generation, template fallback, and emergency bootstrap helper
paths remain forbidden. Real code writing is only allowed through the Director
role runtime when explicitly enabled by environment, so writes go through the
same LLM/tool policy and workspace guards as the interactive Director role.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, NoReturn

from polaris.kernelone.llm.toolkit.write_policy import parse_agents_write_policy
from polaris.kernelone.tools.tool_kinds import is_write_tool_name

logger = logging.getLogger(__name__)

CODE_WRITING_FORBIDDEN_WARNING = (
    "SECURITY POLICY VIOLATION: deterministic/fallback code generation "
    "is strictly forbidden in "
    "polaris.cells.director.tasking.internal.code_generation_engine."
)
_RUNTIME_CODEGEN_ENV = "KERNELONE_DIRECTOR_RUNTIME_CODEGEN"
_DEFAULT_LLM_TIMEOUT_MAX_SECONDS = 300
_DEFAULT_RUNTIME_CODEGEN_LLM_TIMEOUT_SECONDS = 600
_RUNTIME_CODEGEN_LLM_TIMEOUT_MAX_SECONDS = 900
_MIN_RUNTIME_CODEGEN_CALL_SECONDS = 90
_RUNTIME_CODEGEN_TASK_TIMEOUT_MAX_SECONDS = 3570
_RUNTIME_CODEGEN_TASK_TIMEOUT_MARGIN_SECONDS = 300
_POLICY_TEXT_MAX_CHARS = 8000


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
    """Policy guard for deterministic code-generation entry points.

    This class intentionally blocks code generation behavior. It preserves
    selected utility methods and compatibility method signatures to avoid import-time
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
        timeout_max = (
            _RUNTIME_CODEGEN_LLM_TIMEOUT_MAX_SECONDS
            if self.runtime_codegen_enabled()
            else _DEFAULT_LLM_TIMEOUT_MAX_SECONDS
        )
        return min(max(timeout, 15), timeout_max)

    def _default_llm_timeout_hint(self) -> int:
        """Return the default per-call timeout for the active codegen mode."""
        if self.runtime_codegen_enabled():
            return _DEFAULT_RUNTIME_CODEGEN_LLM_TIMEOUT_SECONDS
        return _DEFAULT_LLM_TIMEOUT_MAX_SECONDS

    def resolve_task_timeout_budget(self, task: Any, *, rounds: int) -> int:
        """Resolve total timeout budget for one task, not per round."""
        raw = os.environ.get("KERNELONE_WORKER_TOTAL_TIMEOUT", "")
        try:
            configured = int(raw) if raw else 0
        except ValueError:
            configured = 0

        if configured > 0:
            return min(max(configured, 30), _RUNTIME_CODEGEN_TASK_TIMEOUT_MAX_SECONDS)

        base_timeout = int(getattr(task, "timeout_seconds", 0) or 0)
        round_count = max(1, min(int(rounds or 1), 12))
        per_round_timeout = self.resolve_llm_timeout(self._default_llm_timeout_hint())
        round_floor = 30
        if round_count > 1:
            round_floor = per_round_timeout * round_count + _RUNTIME_CODEGEN_TASK_TIMEOUT_MARGIN_SECONDS
        if base_timeout <= 0:
            base_timeout = per_round_timeout * max(1, min(rounds, 2))
        return min(max(base_timeout, round_floor, 30), _RUNTIME_CODEGEN_TASK_TIMEOUT_MAX_SECONDS)

    def remaining_timeout(self, deadline_ts: float) -> int:
        """Return remaining whole seconds to deadline."""
        return max(0, int(deadline_ts - time.time()))

    def resolve_patch_retry_attempts(self) -> int:
        """Resolve retry attempts for compatibility call sites."""
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
        """Resolve spin-guard limit for compatibility call sites."""
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

    def _read_workspace_text_file(self, relative_path: str, *, max_chars: int = _POLICY_TEXT_MAX_CHARS) -> str:
        """Read a small UTF-8 workspace text file through a path-boundary check."""
        full_path = self._resolve_round_file_path(relative_path)
        if full_path is None or not os.path.isfile(full_path):
            return ""
        try:
            with open(full_path, encoding="utf-8") as handle:
                return handle.read(max(1, int(max_chars or _POLICY_TEXT_MAX_CHARS)))
        except (OSError, UnicodeError, ValueError):
            return ""

    def _workspace_rules_text(self) -> str:
        """Return user/workspace rules that must constrain Director proposals."""
        return self._read_workspace_text_file("AGENTS.md")

    def _workspace_package_scripts(self) -> dict[str, str]:
        """Return current package.json scripts, preserving only string values."""
        return self._workspace_package_policy_snapshot().get("scripts", {})

    @staticmethod
    def _string_dict(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {
            str(name): str(command)
            for name, command in value.items()
            if str(name or "").strip() and isinstance(command, str) and command.strip()
        }

    def _workspace_package_policy_snapshot(self) -> dict[str, dict[str, str]]:
        """Return protected package.json sections before a runtime call mutates files."""
        text = self._read_workspace_text_file("package.json", max_chars=80_000)
        if not text:
            return {"scripts": {}, "dependencies": {}, "devDependencies": {}}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"scripts": {}, "dependencies": {}, "devDependencies": {}}
        if not isinstance(payload, dict):
            return {"scripts": {}, "dependencies": {}, "devDependencies": {}}
        return {
            "scripts": self._string_dict(payload.get("scripts")),
            "dependencies": self._string_dict(payload.get("dependencies")),
            "devDependencies": self._string_dict(payload.get("devDependencies")),
        }

    def _build_workspace_policy_prompt(self) -> str:
        """Build a compact immutable-policy appendix from workspace facts."""
        parts: list[str] = []
        rules = self._workspace_rules_text().strip()
        if rules:
            parts.extend(
                [
                    "WORKSPACE AGENTS.md RULES (hard constraints, higher priority than task convenience):",
                    rules,
                ]
            )
        scripts = self._workspace_package_scripts()
        if scripts:
            parts.extend(
                [
                    "CURRENT package.json scripts. Preserve these unless AGENTS.md explicitly allows changing them:",
                    json.dumps(scripts, ensure_ascii=False, sort_keys=True),
                ]
            )
        if not parts:
            return ""
        return "\n".join(parts)

    def _proposal_policy_violations(self, response_text: str) -> list[str]:
        """Return proposal violations against local workspace rules before applying writes."""
        rules_text = self._workspace_rules_text()
        rules = rules_text.lower()
        text = str(response_text or "")
        lowered = text.lower()
        violations: list[str] = []
        if (
            "preserve package.json scripts" in rules
            and self._proposal_mentions_path(text, "package.json")
            and ('"scripts"' in text or '"devdependencies"' in lowered or '"dependencies"' in lowered)
        ):
            violations.append("workspace_policy_violation:package_json_scripts_or_dependencies_change_forbidden")
        for name in self._forbidden_workspace_file_names(rules):
            if self._proposal_mentions_path(text, name):
                violations.append(f"workspace_policy_violation:forbidden_file:{name}")
        policy = parse_agents_write_policy(rules_text)
        for rule in policy.forbidden_paths:
            if self._proposal_mentions_path(text, rule.path):
                violations.append(f"workspace_policy_violation:forbidden_file:{rule.path}")
        for rule in policy.forbidden_file_patterns:
            if self._proposal_mentions_file_pattern(text, rule.pattern):
                violations.append(f"workspace_policy_violation:forbidden_file:{rule.pattern}")
        return violations

    @staticmethod
    def _forbidden_workspace_file_names(rules: str) -> list[str]:
        """Resolve forbidden tooling filenames from workspace policy text."""
        lowered = str(rules or "").lower()
        if "do not introduce" not in lowered:
            return []
        forbidden: list[str] = []
        if "cargo" in lowered or "rust" in lowered:
            forbidden.append("cargo.toml")
        if "webpack" in lowered:
            forbidden.append("webpack.config.js")
        if "jest" in lowered:
            forbidden.append("jest.config.js")
        if "vite" in lowered:
            forbidden.append("vite.config.ts")
        if "vitest" in lowered:
            forbidden.append("vitest.config.ts")
        return forbidden

    def _file_policy_violations(
        self,
        files: list[dict[str, str]],
        package_snapshot: dict[str, dict[str, str]] | None = None,
    ) -> list[str]:
        """Return policy violations for actual files written by tools or direct workspace writes."""
        rules = self._workspace_rules_text().lower()
        if not rules:
            return []
        snapshot = package_snapshot or self._workspace_package_policy_snapshot()
        violations: list[str] = []
        forbidden_names = self._forbidden_workspace_file_names(rules)
        policy = parse_agents_write_policy(self._workspace_rules_text())
        preserve_scripts = "preserve package.json scripts" in rules
        protect_dependencies = "external build/test dependency" in rules or "do not introduce" in rules

        for file_info in files:
            relative_path = str(file_info.get("path") or "").strip().replace("\\", "/").lower()
            if not relative_path:
                continue
            for name in forbidden_names:
                if relative_path == name or relative_path.endswith(f"/{name}"):
                    violations.append(f"workspace_policy_violation:forbidden_file:{name}")
            for rule in policy.forbidden_paths:
                path_rule = str(rule.path or "").strip().replace("\\", "/").lower()
                if relative_path == path_rule or relative_path.startswith(f"{path_rule}/"):
                    violations.append(f"workspace_policy_violation:forbidden_file:{rule.path}")
            for rule in policy.forbidden_file_patterns:
                if self._path_matches_forbidden_file_pattern(relative_path, rule.pattern):
                    violations.append(f"workspace_policy_violation:forbidden_file:{rule.pattern}")

            if relative_path != "package.json":
                continue
            if not preserve_scripts and not protect_dependencies:
                continue

            content = str(file_info.get("content") or "")
            if not content.strip():
                content = self._read_workspace_text_file("package.json", max_chars=80_000)
            if not content.strip():
                violations.append("workspace_policy_violation:package_json_change_requires_validation")
                continue

            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                violations.append("workspace_policy_violation:package_json_invalid_after_write")
                continue
            if not isinstance(payload, dict):
                violations.append("workspace_policy_violation:package_json_invalid_after_write")
                continue

            if preserve_scripts and self._string_dict(payload.get("scripts")) != snapshot.get("scripts", {}):
                violations.append("workspace_policy_violation:package_json_scripts_change_forbidden")

            if protect_dependencies:
                for section in ("dependencies", "devDependencies"):
                    if self._string_dict(payload.get(section)) != snapshot.get(section, {}):
                        violations.append(f"workspace_policy_violation:package_json_{section}_change_forbidden")

        return list(dict.fromkeys(violations))

    @staticmethod
    def _normalize_workspace_relative_paths(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
        paths: list[str] = []
        seen: set[str] = set()
        for raw_value in values or []:
            value = str(raw_value or "").replace("\\", "/").strip().strip("/")
            if not value or value.startswith("/") or ":" in value or ".." in value.split("/"):
                continue
            if value not in seen:
                seen.add(value)
                paths.append(value)
        return paths

    def _changed_paths_from_file_records(self, files: list[dict[str, str]]) -> list[str]:
        return self._normalize_workspace_relative_paths([item.get("path") for item in files if isinstance(item, dict)])

    @staticmethod
    def _identity_token(value: Any) -> str:
        if value is None or isinstance(value, bool):
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, int):
            return str(value).strip()
        return ""

    @staticmethod
    def _director_codegen_task_identity(task: Any) -> tuple[str, str, dict[str, Any]]:
        metadata = getattr(task, "metadata", None)
        task_metadata: dict[str, Any] = metadata if isinstance(metadata, dict) else {}
        task_id = CodeGenerationEngine._identity_token(getattr(task, "id", ""))
        run_id = (
            CodeGenerationEngine._identity_token(getattr(task, "run_id", ""))
            or CodeGenerationEngine._identity_token(task_metadata.get("run_id"))
            or CodeGenerationEngine._identity_token(task_metadata.get("factory_run_id"))
        )
        return task_id, run_id, task_metadata

    @staticmethod
    def _director_codegen_session_id(task: Any, prompt: str = "") -> str:
        task_id, run_id, _task_metadata = CodeGenerationEngine._director_codegen_task_identity(task)
        basis = run_id or task_id or hashlib.sha1(str(prompt or "").encode("utf-8")).hexdigest()[:12]
        return f"director-codegen-{basis}"

    def _allowed_scope_for_round(self, task: Any, round_files: list[str] | None) -> list[str]:
        round_scope = self._normalize_workspace_relative_paths(list(round_files or []))
        if round_scope:
            return round_scope

        metadata = getattr(task, "metadata", None)
        task_metadata: dict[str, Any] = metadata if isinstance(metadata, dict) else {}
        scope_candidates: list[Any] = []
        for key in ("target_files", "scope_paths"):
            raw_value = task_metadata.get(key)
            if isinstance(raw_value, list):
                scope_candidates.extend(raw_value)
        file_plan = task_metadata.get("file_plan")
        if isinstance(file_plan, list):
            for item in file_plan:
                if isinstance(item, dict):
                    scope_candidates.append(item.get("path"))
        return self._normalize_workspace_relative_paths(scope_candidates)

    def _cognitive_runtime_write_gate_violations(
        self,
        *,
        files: list[dict[str, str]],
        task: Any,
        round_files: list[str] | None,
        round_label: str,
        session_id: str,
    ) -> list[str]:
        changed_files = self._changed_paths_from_file_records(files)
        if not changed_files:
            return ["cognitive_runtime_write_gate:no_changed_files"]

        allowed_scope_paths = self._allowed_scope_for_round(task, round_files)
        if not allowed_scope_paths:
            return ["cognitive_runtime_write_gate:missing_allowed_scope_paths"]

        try:
            from polaris.cells.factory.cognitive_runtime.public import (
                LeaseEditScopeCommandV1,
                MapDiffToCellsCommandV1,
                PromoteOrRejectCommandV1,
                RecordRuntimeReceiptCommandV1,
                RequestProjectionCompileCommandV1,
                ValidateChangeSetCommandV1,
                get_cognitive_runtime_public_service,
            )
        except (ImportError, RuntimeError) as exc:
            return [f"cognitive_runtime_write_gate:unavailable:{exc}"]

        task_id, run_id, _task_metadata = self._director_codegen_task_identity(task)
        subject_ref = f"director_codegen:{task_id or 'unknown'}:{str(round_label or '').strip() or 'round'}"

        service = get_cognitive_runtime_public_service()
        lease = service.lease_edit_scope(
            LeaseEditScopeCommandV1(
                workspace=self.workspace,
                requested_by="director_runtime_codegen",
                scope_paths=tuple(allowed_scope_paths),
                session_id=session_id,
                reason="Director runtime code generation write gate",
                metadata={
                    "task_id": task_id,
                    "run_id": run_id,
                    "session_id": session_id,
                    "round_label": str(round_label or ""),
                    "changed_files": changed_files,
                },
            )
        )
        if not lease.ok:
            return [f"cognitive_runtime_write_gate:lease_failed:{lease.error_message or lease.error_code}"]

        validation = service.validate_change_set(
            ValidateChangeSetCommandV1(
                workspace=self.workspace,
                changed_files=tuple(changed_files),
                allowed_scope_paths=tuple(allowed_scope_paths),
                evidence_refs=(task_id,) if task_id else (),
                require_change=True,
            )
        )
        if not validation.ok or validation.validation is None:
            return [
                "cognitive_runtime_write_gate:validate_change_set_failed:"
                f"{validation.error_message or validation.error_code or 'unknown'}"
            ]
        if not validation.validation.write_gate_allowed:
            reasons = "; ".join(validation.validation.reasons) or "write_gate_not_allowed"
            return [f"cognitive_runtime_write_gate:write_gate_denied:{reasons}"]

        receipt_refs: list[str] = []
        receipt = service.record_runtime_receipt(
            RecordRuntimeReceiptCommandV1(
                workspace=self.workspace,
                receipt_type="director_codegen_change_set_validated",
                payload={
                    "task_id": task_id,
                    "round_label": str(round_label or ""),
                    "changed_files": changed_files,
                    "allowed_scope_paths": allowed_scope_paths,
                    "validation_id": validation.validation.validation_id,
                    "risk_level": validation.validation.risk_level,
                    "impact_score": validation.validation.impact_score,
                },
                session_id=session_id,
                run_id=run_id or None,
                trace_refs=(validation.validation.validation_id,) if validation.validation.validation_id else (),
            )
        )
        if receipt.ok and receipt.receipt is not None:
            receipt_refs.append(receipt.receipt.receipt_id)

        mapping = service.map_diff_to_cells(
            MapDiffToCellsCommandV1(
                workspace=self.workspace,
                changed_files=tuple(changed_files),
            )
        )
        mapped_cells = tuple(mapping.mapping.matched_cells) if mapping.ok and mapping.mapping is not None else ()
        if not mapping.ok:
            logger.warning(
                "cognitive_runtime_diff_mapping_failed: task=%s round=%s error=%s",
                task_id,
                round_label,
                mapping.error_message or mapping.error_code,
            )

        projection = service.request_projection_compile(
            RequestProjectionCompileCommandV1(
                workspace=self.workspace,
                requested_by="director_runtime_codegen",
                subject_ref=subject_ref,
                changed_files=tuple(changed_files),
                mapped_cells=mapped_cells,
                session_id=session_id,
                run_id=run_id or None,
                metadata={
                    "task_id": task_id,
                    "run_id": run_id,
                    "session_id": session_id,
                    "round_label": str(round_label or ""),
                    "mapping_id": getattr(mapping.mapping, "mapping_id", "") if mapping.mapping is not None else "",
                    "mapping_notes": list(getattr(mapping.mapping, "notes", ()) if mapping.mapping is not None else ()),
                },
            )
        )
        if not projection.ok or projection.request is None:
            return [
                "cognitive_runtime_write_gate:projection_compile_failed:"
                f"{projection.error_message or projection.error_code or 'unknown'}"
            ]

        if mapped_cells:
            promotion = service.promote_or_reject(
                PromoteOrRejectCommandV1(
                    workspace=self.workspace,
                    subject_ref=subject_ref,
                    changed_files=tuple(changed_files),
                    mapped_cells=mapped_cells,
                    write_gate_allowed=True,
                    projection_status=projection.request.status,
                    projection_request_id=projection.request.request_id,
                    receipt_refs=tuple(receipt_refs),
                    reasons=tuple(validation.validation.reasons),
                    metadata={
                        "task_id": task_id,
                        "run_id": run_id,
                        "session_id": session_id,
                        "round_label": str(round_label or ""),
                    },
                )
            )
            if not promotion.ok:
                reason = ""
                if promotion.decision is not None:
                    reason = "; ".join(promotion.decision.reasons)
                return [
                    "cognitive_runtime_write_gate:promotion_rejected:"
                    f"{reason or promotion.error_message or promotion.error_code or 'unknown'}"
                ]
        return []

    @staticmethod
    def _proposal_mentions_path(response_text: str, relative_path: str) -> bool:
        """Return whether a proposal declares a PATCH_FILE/FILE block for a path."""
        path_token = re.escape(str(relative_path or "").strip().replace("\\", "/"))
        if not path_token:
            return False
        return bool(
            re.search(
                rf"(?im)^\s*(?:`{{3}}\s*)?(?:patch_file|file)\s*:\s*{path_token}\b",
                str(response_text or "").replace("\\", "/"),
            )
        )

    @staticmethod
    def _path_matches_forbidden_file_pattern(relative_path: str, pattern: str) -> bool:
        path = str(relative_path or "").strip().replace("\\", "/").lower()
        rule = str(pattern or "").strip().lower()
        if not path or not rule:
            return False
        if rule.startswith("*."):
            return path.endswith(rule[1:])
        return path == rule or path.endswith(f"/{rule}")

    @classmethod
    def _proposal_mentions_file_pattern(cls, response_text: str, pattern: str) -> bool:
        text = str(response_text or "").replace("\\", "/")
        rule = str(pattern or "").strip()
        if not text or not rule:
            return False
        if rule.startswith("*."):
            suffix = re.escape(rule[1:])
            return bool(re.search(rf"(?im)^\s*(?:`{{3}}\s*)?(?:patch_file|file)\s*:\s*\S+{suffix}\b", text))
        return cls._proposal_mentions_path(text, rule)

    async def _invoke_director_role_response(
        self,
        *,
        task: Any,
        prompt: str,
        timeout: int,
        round_label: str,
        round_files: list[str] | None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Invoke the canonical Director role runtime for one generation round."""
        from polaris.cells.director.tasking.internal.execution_profile import resolve_director_execution_profile
        from polaris.cells.director.tasking.internal.execution_strategy import (
            apply_execution_strategy_overrides,
            resolve_director_execution_strategy,
        )
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        task_id, run_id, task_metadata = self._director_codegen_task_identity(task)
        execution_profile = resolve_director_execution_profile(
            subject=str(getattr(task, "subject", "") or ""),
            description=str(getattr(task, "description", "") or ""),
            metadata=task_metadata,
            target_files=list(round_files or []),
            scope_paths=task_metadata.get("scope_paths")
            if isinstance(task_metadata.get("scope_paths"), list)
            else None,
            workspace=self.workspace,
        )
        execution_profile_payload = execution_profile.to_dict()
        execution_strategy = resolve_director_execution_strategy(
            execution_profile,
            metadata=task_metadata,
        )
        execution_strategy_payload = execution_strategy.to_dict()
        context = {
            "task_id": task_id,
            "run_id": run_id,
            "round_label": str(round_label or "").strip(),
            "target_files": list(round_files or []),
            "task_type": execution_profile.task_type,
            "phase": execution_profile.phase,
            "stage": execution_profile.temperature_phase,
            "temperature_phase": execution_profile.temperature_phase,
            "director_execution_profile": execution_profile_payload,
            "director_execution_profile_schema": execution_profile.schema_version,
            "director_execution_profile_source": execution_profile.source,
            "director_execution_strategy": execution_strategy_payload,
            "task_execution_strategy": execution_strategy_payload,
            "llm_call_timeout_seconds": timeout,
            "director_runtime_codegen": True,
            "director_runtime_codegen_mode": execution_profile.generation_mode,
            "delivery_mode": "propose_patch",
            "disable_internal_tool_rounds": True,
            "suppress_working_memory_contract": True,
            "suppress_tool_policy_prompt": True,
            "_transaction_kernel_forced_tool_definitions": [],
            "_transaction_kernel_forced_tool_choice": "none",
        }
        metadata = {
            "source": "director.execution.code_generation_engine",
            "role_runtime_required": True,
            "cognitive_runtime_required": True,
            "context_os_expected": True,
            "director_runtime_codegen": True,
            "director_execution_profile": execution_profile_payload,
            "director_execution_strategy": execution_strategy_payload,
            "task_execution_strategy": execution_strategy_payload,
            "task_type": execution_profile.task_type,
            "phase": execution_profile.phase,
            "temperature": execution_strategy.temperature,
            "temperature_phase": execution_strategy.temperature_phase,
            "temperature_source": execution_strategy.source,
            "validate_output": False,
            "max_retries": 0,
        }
        apply_execution_strategy_overrides(
            context=context,
            metadata=metadata,
            profile=execution_profile,
            strategy=execution_strategy,
        )
        user_message = "[mode:propose] Do not call tools. Please complete the assigned implementation task."
        workspace_policy_prompt = self._build_workspace_policy_prompt()
        proposal_prompt = self._normalize_proposal_prompt(
            "\n\n".join(part for part in (workspace_policy_prompt, prompt) if part.strip())
        )
        appendix = (
            "Polaris Director proposal-to-apply bridge. This runtime bridge validates "
            "and applies the returned file blocks through FileApplyService. Return "
            "only PATCH_FILE blocks or fenced file sections for the target files; "
            "do not ask follow-up questions, do not narrate phases/progress, and "
            "do not return placeholder content. Do not output Command:, shell "
            "commands, SESSION_PATCH, status updates, or tool-call text. The "
            "response must contain at least one parsable file operation."
            "\n\n"
            f"{proposal_prompt}"
        )
        session_id = session_id or self._director_codegen_session_id(task, prompt)
        command = ExecuteRoleSessionCommandV1(
            role="director",
            session_id=session_id,
            workspace=self.workspace,
            user_message=user_message,
            run_id=run_id or None,
            task_id=task_id or None,
            domain="code",
            context=context,
            metadata={**metadata, "prompt_appendix": appendix},
            stream=False,
            host_kind="director_runtime_codegen",
        )
        result = await asyncio.wait_for(
            RoleRuntimeService().execute_role_session(command),
            timeout=max(1.0, float(timeout)),
        )
        metadata = dict(result.metadata or {})
        metadata.update(
            {
                "role_runtime_entrypoint": "roles.runtime.execute_role_session",
                "context_os_expected": True,
                "runtime_fallback_used": False,
                "fallback_policy": "fail_closed",
            }
        )
        return {
            "response": str(result.output or ""),
            "content": str(result.output or ""),
            "thinking": result.thinking,
            "role": result.role,
            "metadata": metadata,
            "execution_stats": dict(result.usage or {}),
            "tool_calls": list(result.tool_calls or ()),
            "artifacts": list(result.artifacts or ()),
            "success": bool(result.ok),
            "error": result.error_message or result.error_code or "",
        }

    @staticmethod
    def _normalize_proposal_prompt(prompt: str) -> str:
        """Ensure projected task prompts still carry the proposal/no-tools contract."""
        text = str(prompt or "").strip()
        lowered = text.lower()
        prefix: list[str] = []
        if "[mode:propose]" not in lowered and "[mode:propose_patch]" not in lowered:
            prefix.append("[mode:propose]")
        if "do not call tools" not in lowered:
            prefix.append("Do not call tools. Return only parsable PATCH_FILE blocks or fenced file sections.")
        parts = [*prefix, text]
        return "\n".join(part for part in parts if part).strip()

    @staticmethod
    def _extract_response_text(response: dict[str, Any]) -> str:
        for candidate in CodeGenerationEngine._iter_response_text_candidates(response):
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _iter_response_text_candidates(response: dict[str, Any]) -> list[Any]:
        """Collect possible final text fields from role/dialogue response shapes."""
        candidates: list[Any] = []
        direct_keys = (
            "response",
            "content",
            "reply",
            "visible_content",
            "response_content",
            "output",
            "raw_content",
        )
        for key in direct_keys:
            candidates.append(response.get(key))

        for nested_key in ("raw_response", "data", "metadata", "turn_result", "result"):
            nested = response.get(nested_key)
            if isinstance(nested, dict):
                for key in direct_keys:
                    candidates.append(nested.get(key))

        raw_events = response.get("turn_events_metadata")
        if isinstance(raw_events, list):
            for item in raw_events:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind") or "").strip().lower()
                role = str(item.get("role") or "").strip().lower()
                if kind == "assistant_turn" or role == "assistant":
                    candidates.append(item.get("content"))
        return candidates

    @staticmethod
    def _extract_turn_ids(response: dict[str, Any]) -> list[str]:
        """Extract turn/run ids that can join role results to LLM event logs."""
        ids: list[str] = []

        def add(value: Any) -> None:
            token = str(value or "").strip()
            if token and token not in ids:
                ids.append(token)

        for key in ("turn_id", "run_id"):
            add(response.get(key))

        for nested_key in ("metadata", "raw_response", "data", "result"):
            nested = response.get(nested_key)
            if not isinstance(nested, dict):
                continue
            for key in ("turn_id", "run_id"):
                add(nested.get(key))
            envelope = nested.get("turn_envelope")
            if isinstance(envelope, dict):
                for key in ("turn_id", "run_id"):
                    add(envelope.get(key))

        return ids

    def _director_llm_event_paths(self) -> list[str]:
        """Return candidate director LLM event logs for this workspace."""
        paths: list[str] = []
        try:
            from polaris.kernelone.storage.layout import resolve_runtime_path

            paths.append(resolve_runtime_path(self.workspace, "runtime/events/director.llm.events.jsonl"))
        except (ImportError, RuntimeError, TypeError, ValueError):
            pass
        paths.append(os.path.join(self.workspace, ".polaris", "runtime", "events", "director.llm.events.jsonl"))

        seen: set[str] = set()
        unique_paths: list[str] = []
        for path in paths:
            token = str(path or "").strip()
            if token and token not in seen:
                seen.add(token)
                unique_paths.append(token)
        return unique_paths

    def _recover_response_text_from_llm_events(self, response: dict[str, Any]) -> str:
        """Recover raw LLM content when RoleTurnResult visible content was empty."""
        turn_ids = set(self._extract_turn_ids(response))
        if not turn_ids:
            return ""

        recovered = ""
        for event_path in self._director_llm_event_paths():
            if not os.path.isfile(event_path):
                continue
            try:
                with open(event_path, encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        data = event.get("data") if isinstance(event, dict) else None
                        if not isinstance(data, dict):
                            continue
                        event_name = str(event.get("event") or data.get("event_type") or "").strip()
                        if event_name != "llm_call_end":
                            continue
                        event_ids = {
                            str(event.get("run_id") or "").strip(),
                            str(data.get("run_id") or "").strip(),
                        }
                        if not turn_ids.intersection(token for token in event_ids if token):
                            continue
                        metadata = data.get("metadata")
                        if not isinstance(metadata, dict):
                            continue
                        content = str(metadata.get("response_content") or "").strip()
                        if content:
                            recovered = content
            except OSError:
                continue
        return recovered

    @staticmethod
    def _file_operation_marker_score(text: str) -> int:
        """Score whether text contains actionable file operation protocol."""
        payload = str(text or "")
        lowered = payload.lower()
        return (
            lowered.count("```file:")
            + payload.count("PATCH_FILE:")
            + len(re.findall(r"(?im)^\s*FILE\s*:", payload))
            + len(re.findall(r"(?im)^\s*DELETE(?:_FILE)?\s*:", payload))
        )

    @classmethod
    def _should_prefer_recovered_response(cls, current: str, recovered: str) -> bool:
        """Return true when event-log content is a more complete codegen payload."""
        recovered_text = str(recovered or "").strip()
        if not recovered_text:
            return False
        current_text = str(current or "").strip()
        if not current_text:
            return True

        current_score = cls._file_operation_marker_score(current_text)
        recovered_score = cls._file_operation_marker_score(recovered_text)
        if recovered_score <= 0:
            return False
        if current_score <= 0:
            return True
        if recovered_score > current_score:
            return True
        return recovered_score == current_score and len(recovered_text) > len(current_text) * 2

    @staticmethod
    def _response_timeout_warning(response: dict[str, Any], timeout: int) -> str | None:
        """Return a terminal timeout warning when the provider reports timeout."""
        values: list[Any] = [
            response.get("error"),
            response.get("error_category"),
            response.get("error_message"),
            response.get("status"),
        ]
        for nested_key in ("raw_response", "data"):
            nested = response.get(nested_key)
            if isinstance(nested, dict):
                values.extend(
                    [
                        nested.get("error"),
                        nested.get("error_category"),
                        nested.get("error_message"),
                        nested.get("status"),
                    ]
                )
                if bool(nested.get("timeout")):
                    return f"director_runtime_codegen_timeout:{timeout}s"
        for value in values:
            text = str(value or "").strip().lower()
            if "timeout" in text or "timed out" in text:
                return f"director_runtime_codegen_timeout:{timeout}s"
        return None

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
        for item in tool_results:
            tool_name = item.get("tool") or item.get("tool_name") or item.get("name") or ""
            if not is_write_tool_name(tool_name) or not bool(item.get("success")):
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
            full_path = self._resolve_round_file_path(path)
            if full_path is None:
                continue
            if os.path.isfile(full_path):
                seen.add(path)
                files.append({"path": path, "content": ""})
        return files

    def _resolve_round_file_path(self, relative_path: str) -> str | None:
        """Resolve a round file path inside the workspace boundary."""
        path = str(relative_path or "").strip()
        if not path or os.path.isabs(path):
            return None
        workspace_abs = os.path.abspath(self.workspace)
        full_path = os.path.abspath(os.path.join(workspace_abs, path))
        try:
            if os.path.commonpath([workspace_abs, full_path]) != workspace_abs:
                return None
        except ValueError:
            return None
        return full_path

    def _snapshot_round_files(self, round_files: list[str] | None) -> dict[str, tuple[bool, int, int]]:
        """Capture file existence, size, and mtime before a runtime generation call."""
        snapshot: dict[str, tuple[bool, int, int]] = {}
        for raw_path in round_files or []:
            path = str(raw_path or "").strip()
            if not path or path in snapshot:
                continue
            full_path = self._resolve_round_file_path(path)
            if full_path is None:
                continue
            try:
                stat = os.stat(full_path)
            except OSError:
                snapshot[path] = (False, -1, -1)
            else:
                snapshot[path] = (True, int(stat.st_size), int(stat.st_mtime_ns))
        return snapshot

    def _collect_changed_round_files(
        self,
        round_files: list[str] | None,
        before: dict[str, tuple[bool, int, int]],
    ) -> list[dict[str, str]]:
        """Collect target files that were created or changed by the runtime call."""
        files: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw_path in round_files or []:
            path = str(raw_path or "").strip()
            if not path or path in seen:
                continue
            full_path = self._resolve_round_file_path(path)
            if full_path is None or not os.path.isfile(full_path):
                continue
            try:
                stat = os.stat(full_path)
            except OSError:
                continue
            current = (True, int(stat.st_size), int(stat.st_mtime_ns))
            if current == before.get(path, (False, -1, -1)):
                continue
            seen.add(path)
            files.append({"path": path, "content": ""})
        return files

    def _apply_response_operations(
        self,
        *,
        response_text: str,
        task_id: str,
        allowed_scope_paths: list[str] | tuple[str, ...] | None,
        llm_metadata: dict[str, Any],
    ) -> tuple[list[dict], list[str]]:
        apply_func = getattr(self._executor, "_apply_response_operations", None)
        if not callable(apply_func):
            return [], ["director executor cannot apply response operations"]
        applied_files, errors = apply_func(
            response_text,
            task_id=task_id,
            llm_metadata=llm_metadata,
            allowed_scope_paths=allowed_scope_paths,
        )
        normalized_files = [dict(item) for item in applied_files if isinstance(item, dict)]
        normalized_errors = [str(item) for item in errors if str(item or "").strip()]
        return normalized_files, normalized_errors

    # === Blocked compatibility entry points ===

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
        task_id, _run_id, _task_metadata = self._director_codegen_task_identity(task)
        session_id = self._director_codegen_session_id(task, prompt)
        attempts = self.resolve_patch_retry_attempts()
        current_prompt = prompt

        for attempt in range(1, attempts + 1):
            remaining = self.remaining_timeout(deadline_ts)
            if remaining <= 0:
                warnings.append("director_runtime_codegen_deadline_exhausted")
                break
            if remaining < _MIN_RUNTIME_CODEGEN_CALL_SECONDS:
                warnings.append(f"director_runtime_codegen_deadline_too_short:{remaining}s")
                break
            timeout = min(max(int(per_call_timeout or 0), 15), remaining)
            package_policy_snapshot = self._workspace_package_policy_snapshot()
            before_signatures = self._snapshot_round_files(round_files)
            try:
                response = await self._invoke_director_role_response(
                    task=task,
                    prompt=current_prompt,
                    timeout=timeout,
                    round_label=f"{round_label}:attempt-{attempt}",
                    round_files=round_files,
                    session_id=session_id,
                )
            except (asyncio.TimeoutError, TimeoutError):
                changed_files = self._collect_changed_round_files(round_files, before_signatures)
                if changed_files:
                    file_policy_violations = self._file_policy_violations(changed_files, package_policy_snapshot)
                    if file_policy_violations:
                        warnings.extend(file_policy_violations)
                        break
                    cognitive_gate_violations = self._cognitive_runtime_write_gate_violations(
                        files=changed_files,
                        task=task,
                        round_files=round_files,
                        round_label=round_label,
                        session_id=session_id,
                    )
                    if cognitive_gate_violations:
                        warnings.extend(cognitive_gate_violations)
                        break
                    warnings.append(f"director_runtime_codegen_timeout_after_changes:{timeout}s")
                    return changed_files, warnings
                warnings.append(f"director_runtime_codegen_timeout:{timeout}s")
                break
            except (
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                warnings.append(f"director_runtime_codegen_invoke_failed:{exc}")
                break

            timeout_warning = self._response_timeout_warning(response, timeout)
            if timeout_warning is not None:
                warnings.append(timeout_warning)
                break

            response_text = self._extract_response_text(response)
            recovered_response_text = self._recover_response_text_from_llm_events(response)
            if self._should_prefer_recovered_response(response_text, recovered_response_text):
                response_text = recovered_response_text
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
                file_policy_violations = self._file_policy_violations(tool_files, package_policy_snapshot)
                if file_policy_violations:
                    warnings.extend(file_policy_violations)
                    break
                cognitive_gate_violations = self._cognitive_runtime_write_gate_violations(
                    files=tool_files,
                    task=task,
                    round_files=round_files,
                    round_label=round_label,
                    session_id=session_id,
                )
                if cognitive_gate_violations:
                    warnings.extend(cognitive_gate_violations)
                    break
                return tool_files, warnings

            changed_files = self._collect_changed_round_files(round_files, before_signatures)
            if changed_files:
                file_policy_violations = self._file_policy_violations(changed_files, package_policy_snapshot)
                if file_policy_violations:
                    warnings.extend(file_policy_violations)
                    break
                cognitive_gate_violations = self._cognitive_runtime_write_gate_violations(
                    files=changed_files,
                    task=task,
                    round_files=round_files,
                    round_label=round_label,
                    session_id=session_id,
                )
                if cognitive_gate_violations:
                    warnings.extend(cognitive_gate_violations)
                    break
                return changed_files, warnings

            if response_text:
                policy_violations = self._proposal_policy_violations(response_text)
                if policy_violations:
                    warnings.extend(policy_violations)
                    current_prompt = (
                        f"{prompt}\n\nPrevious attempt {attempt} violated workspace policy: "
                        f"{'; '.join(policy_violations)}. Read and obey AGENTS.md. "
                        "Return a new proposal that preserves package.json scripts/dependencies and avoids forbidden files."
                    )
                    continue
                applied_files, apply_errors = self._apply_response_operations(
                    response_text=response_text,
                    task_id=task_id,
                    allowed_scope_paths=round_files,
                    llm_metadata={
                        "provider": response.get("provider"),
                        "model": response.get("model"),
                        "attempt": attempt,
                        "round_label": round_label,
                    },
                )
                if applied_files:
                    file_policy_violations = self._file_policy_violations(applied_files, package_policy_snapshot)
                    if file_policy_violations:
                        warnings.extend(file_policy_violations)
                        break
                    cognitive_gate_violations = self._cognitive_runtime_write_gate_violations(
                        files=applied_files,
                        task=task,
                        round_files=round_files,
                        round_label=round_label,
                        session_id=session_id,
                    )
                    if cognitive_gate_violations:
                        warnings.extend([*apply_errors, *cognitive_gate_violations])
                        break
                    return applied_files, [*warnings, *apply_errors]
                changed_files = self._collect_changed_round_files(round_files, before_signatures)
                if changed_files:
                    file_policy_violations = self._file_policy_violations(changed_files, package_policy_snapshot)
                    if file_policy_violations:
                        warnings.extend([*apply_errors, *file_policy_violations])
                        break
                    cognitive_gate_violations = self._cognitive_runtime_write_gate_violations(
                        files=changed_files,
                        task=task,
                        round_files=round_files,
                        round_label=round_label,
                        session_id=session_id,
                    )
                    if cognitive_gate_violations:
                        warnings.extend([*apply_errors, *cognitive_gate_violations])
                        break
                    return changed_files, [*warnings, *apply_errors]
                warnings.extend(apply_errors)
            else:
                warnings.append("director_runtime_codegen_empty_response")

            recent_errors = "; ".join(warnings[-4:])
            error_hint = f" Validation/apply errors: {recent_errors}." if recent_errors else ""
            current_prompt = (
                f"{prompt}\n\nPrevious attempt {attempt} produced no accepted workspace changes."
                f"{error_hint} Return valid PATCH_FILE blocks or fenced file sections for the listed target files."
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
