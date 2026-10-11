"""PM 角色适配器.

强化任务合同生成与质量门禁，避免"有任务但不可执行"的空壳输出。

本模块是 PM 适配器的 **唯一装配点（assembly point）** 与 canonical 导入路径：
- 从 :mod:`polaris.cells.roles.adapters.internal.pm.pm_text_utils`
  重新导出全部冻结常量与 ``_pm_*`` 纯函数；
- 通过组合 :mod:`polaris.cells.roles.adapters.internal.pm` 下 6 个职责 mixin
  定义 :class:`PMAdapter`；
- 保持与历史版本完全一致的公开/私有顶层符号面（无损重构）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, cast

from polaris.cells.control_plane.run_ledger.public.failure_evidence import FailureClassV1
from polaris.cells.orchestration.pm_planning.public.service import (
    autofix_pm_contract_for_quality,
    evaluate_pm_task_quality,
)
from polaris.cells.roles.kernel.public.llm_caller_contracts import (
    bind_role_generation_outcome,
    resolve_role_output_budget,
)
from polaris.cells.runtime.task_runtime.public.evidence import task_row_execution_event_failure
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.llm.budget_policy import (
    BUDGET_CONTEXT_KEYS_CANONICAL,
    DEFAULT_PLANNING_ROLE_OUTPUT_TOKENS,
    OUTPUT_BUDGET_CONTEXT_KEYS,
    PM_PLANNING_SETTLEMENT_GRACE_SECONDS,
    TIMEOUT_OVERRIDE_CONTEXT_KEYS,
    chief_engineer_generation_floor_seconds_for_output_tokens,
    resolve_pm_planning_time_budget,
)
from polaris.kernelone.planning import (
    Plan,
    PlanStep,
    PlanValidationResult,
    StructuralPlanValidator,
)
from polaris.kernelone.storage import (
    resolve_runtime_path,
    resolve_workspace_persistent_path,
)

from .base import BaseRoleAdapter
from .pm import (
    PMBoardTaskMixin,
    PMContractNormalizationMixin,
    PMContractParsingMixin,
    PMContractSynthesisMixin,
    PMPlanArtifactMixin,
    PMPromptBuildingMixin,
)
from .pm.parsing import _PMContractError

# ---------------------------------------------------------------------------
# Re-export frozen constants and pure helpers from the leaf module so the
# canonical import path ``...internal.pm_adapter`` keeps an identical public
# AND privately-imported top-level symbol surface (lossless refactor).
# ---------------------------------------------------------------------------
from .pm.pm_text_utils import (
    _ACTION_MARKERS,
    _DEFAULT_PHASE_SEQUENCE,
    _PM_BARE_FILENAME_HINT_RE,
    _PM_CONTRACT_SCOPE_PATH_LIMIT,
    _PM_DETAIL_BULLET_PREFIX,
    _PM_EXPLICIT_FILE_PATH_RE,
    _PM_META_DIAGNOSTIC_TEXT_RE,
    _PM_META_DIAGNOSTIC_TITLES,
    _PM_NON_DELIVERY_CONSTRAINT_TEXT_RE,
    _PM_NON_PATH_SCOPE_RE,
    _PM_PLAN_DIRECTIVE_REDACTED,
    _PM_PLAN_FORBIDDEN_TEXT_REPLACEMENTS,
    _PM_PROMPT_DIRECTIVE_MAX_CHARS,
    _PM_PROMPT_ECHO_MARKERS,
    _PM_PYTHON_HINT_RE,
    _PM_README_HINT_RE,
    _PM_RETRY_DIRECTIVE_MAX_CHARS,
    _PM_ROOT_WORKSPACE_HINT_RE,
    _PM_SCHEMA_PLACEHOLDER_VALUES,
    _PM_SCOPE_PATH_FILENAMES,
    _PM_SCOPE_PATH_ROOTS,
    _PM_SCOPE_PATH_SUFFIXES,
    _PM_SOURCE_FILE_HINT_RE,
    _PM_TASK_LABEL_PREFIX,
    _PM_TASK_LABEL_SUFFIX,
    _PM_TEST_CONTRACT_HINT_RE,
    _STOPWORDS,
    _TASK_LINE_PREFIX,
    _TASK_SECTION_HEADING,
    _pm_append_unique_path,
    _pm_extract_concrete_file_paths_from_text,
    _pm_extract_inline_list_field,
    _pm_extract_requirement_subject,
    _pm_flatten_raw_path_values,
    _pm_infer_test_target_file_for_contract,
    _pm_is_concrete_target_file_path,
    _pm_is_dependency_chain_text,
    _pm_is_placeholder_task_title,
    _pm_is_prompt_echo_response,
    _pm_normalize_explicit_file_path,
    _pm_path_token_from_subject,
    _pm_raw_task_has_explicit_concrete_target,
    _pm_raw_task_is_dependency_chain,
    _pm_raw_task_is_meta_diagnostic,
    _pm_raw_task_is_non_delivery_constraint,
    _pm_root_source_filename_from_text,
    _pm_root_workspace_contract_targets_from_directive,
    _pm_root_workspace_target_files_from_context,
    _pm_split_concrete_targets_and_scopes,
    _pm_strip_action_prefix,
    _pm_strip_markdown_title_noise,
    _pm_strip_task_label_prefix,
    _pm_target_files_include_tests,
    _pm_text,
    _pm_title_fragment,
)
from .pm.revision import (
    apply_revision,
    canonical_json,
    content_hash,
    make_revision_base,
    revision_derivation,
)
from .runtime_dialogue import invoke_role_runtime_first

__all__ = [
    "_ACTION_MARKERS",
    "_DEFAULT_PHASE_SEQUENCE",
    "_PM_BARE_FILENAME_HINT_RE",
    "_PM_CONTRACT_SCOPE_PATH_LIMIT",
    "_PM_DETAIL_BULLET_PREFIX",
    "_PM_EXPLICIT_FILE_PATH_RE",
    "_PM_META_DIAGNOSTIC_TEXT_RE",
    "_PM_META_DIAGNOSTIC_TITLES",
    "_PM_NON_DELIVERY_CONSTRAINT_TEXT_RE",
    "_PM_NON_PATH_SCOPE_RE",
    "_PM_PLAN_DIRECTIVE_REDACTED",
    "_PM_PLAN_FORBIDDEN_TEXT_REPLACEMENTS",
    "_PM_PROMPT_DIRECTIVE_MAX_CHARS",
    "_PM_PROMPT_ECHO_MARKERS",
    "_PM_PYTHON_HINT_RE",
    "_PM_README_HINT_RE",
    "_PM_RETRY_DIRECTIVE_MAX_CHARS",
    "_PM_ROOT_WORKSPACE_HINT_RE",
    "_PM_SCHEMA_PLACEHOLDER_VALUES",
    "_PM_SCOPE_PATH_FILENAMES",
    "_PM_SCOPE_PATH_ROOTS",
    "_PM_SCOPE_PATH_SUFFIXES",
    "_PM_SOURCE_FILE_HINT_RE",
    "_PM_TASK_LABEL_PREFIX",
    "_PM_TASK_LABEL_SUFFIX",
    "_PM_TEST_CONTRACT_HINT_RE",
    "_STOPWORDS",
    "_TASK_LINE_PREFIX",
    "_TASK_SECTION_HEADING",
    "Any",
    "BaseRoleAdapter",
    "PMAdapter",
    "PMBoardTaskMixin",
    "PMContractNormalizationMixin",
    "PMContractParsingMixin",
    "PMContractSynthesisMixin",
    "PMPlanArtifactMixin",
    "PMPromptBuildingMixin",
    "Path",
    "Plan",
    "PlanStep",
    "PlanValidationResult",
    "SequenceMatcher",
    "StructuralPlanValidator",
    "_pm_append_unique_path",
    "_pm_extract_concrete_file_paths_from_text",
    "_pm_extract_inline_list_field",
    "_pm_extract_requirement_subject",
    "_pm_flatten_raw_path_values",
    "_pm_infer_test_target_file_for_contract",
    "_pm_is_concrete_target_file_path",
    "_pm_is_dependency_chain_text",
    "_pm_is_placeholder_task_title",
    "_pm_is_prompt_echo_response",
    "_pm_normalize_explicit_file_path",
    "_pm_path_token_from_subject",
    "_pm_raw_task_has_explicit_concrete_target",
    "_pm_raw_task_is_dependency_chain",
    "_pm_raw_task_is_meta_diagnostic",
    "_pm_raw_task_is_non_delivery_constraint",
    "_pm_root_source_filename_from_text",
    "_pm_root_workspace_contract_targets_from_directive",
    "_pm_root_workspace_target_files_from_context",
    "_pm_split_concrete_targets_and_scopes",
    "_pm_strip_action_prefix",
    "_pm_strip_markdown_title_noise",
    "_pm_strip_task_label_prefix",
    "_pm_target_files_include_tests",
    "_pm_text",
    "_pm_title_fragment",
    "ast",
    "autofix_pm_contract_for_quality",
    "cast",
    "datetime",
    "evaluate_pm_task_quality",
    "invoke_role_runtime_first",
    "json",
    "os",
    "re",
    "resolve_runtime_path",
    "resolve_workspace_persistent_path",
    "timezone",
    "write_text_atomic",
]


class PMAdapter(
    PMContractParsingMixin,
    PMContractNormalizationMixin,
    PMContractSynthesisMixin,
    PMPromptBuildingMixin,
    PMBoardTaskMixin,
    PMPlanArtifactMixin,
    BaseRoleAdapter,
):
    """PM 角色适配器."""

    @property
    def role_id(self) -> str:
        return "pm"

    def get_capabilities(self) -> list[str]:
        return [
            "analyze_requirements",
            "generate_tasks",
            "review_results",
            "meta_planning",
            "adaptive_task_decomposition",
        ]

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """执行 PM 任务."""
        self._reset_task_runtime_transition_failures()
        stage = str(input_data.get("stage", "pm")).strip().lower()
        directive = str(input_data.get("input", ""))
        # Exact Factory binding and requirement bytes survive the normal entry.
        # Whitespace-only input keeps its existing empty-request behavior.
        if not directive.strip():
            directive = ""

        if stage == "architect":
            result = await self._run_architect_stage(task_id, directive)
        else:
            result = await self._run_pm_stage(task_id, directive, input_data, context)
        return self._with_task_runtime_transition_failure_evidence(result)

    async def _run_architect_stage(
        self,
        task_id: str,
        directive: str,
    ) -> dict[str, Any]:
        """运行 Architect 阶段（兼容保留，实际由 architect 角色承担）."""
        self._update_task_progress(task_id, "planning")

        try:
            message = directive or "请分析当前工作区并生成可执行架构文档"
            response = await invoke_role_runtime_first(
                workspace=self.workspace,
                role="architect",
                message=message,
                context={"task_id": task_id, "mode": "pm_architect_stage"},
                validate_output=False,
                max_retries=1,
            )
            content = str(response.get("response") or "") if isinstance(response, dict) else str(response or "")

            docs_dir = Path(resolve_workspace_persistent_path(self.workspace, "workspace/docs"))
            docs_dir.mkdir(parents=True, exist_ok=True)
            design_doc = docs_dir / "design.md"
            write_text_atomic(
                str(design_doc),
                f"# 设计文档\n\n生成时间: {datetime.now(timezone.utc).isoformat()}\n\n{content}\n",
                encoding="utf-8",
            )
            self._update_task_progress(task_id, "completed")
            return {
                "success": True,
                "stage": "architect",
                "design_doc": str(design_doc),
                "content_length": len(content),
            }
        except (RuntimeError, ValueError) as e:
            return {
                "success": False,
                "stage": "architect",
                "error": str(e),
            }

    @staticmethod
    def _record_pm_generation_outcome(
        response: Any,
        response_text: str,
        planning_context: dict[str, Any],
        quality_signals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Use own-call facts only when the exact visible text and child bind."""
        metadata = response.get("metadata") if isinstance(response, dict) else None
        outcome, unavailable = bind_role_generation_outcome(
            metadata,
            response_text=response_text,
            expected_run_id=str(planning_context.get("run_id") or ""),
        )
        diagnostic: dict[str, Any] = {
            "response_sha256": hashlib.sha256(response_text.encode("utf-8")).hexdigest(),
            "response_chars": len(response_text),
        }
        if outcome is not None:
            diagnostic["generation_outcome"] = outcome.to_payload()
        else:
            diagnostic["generation_outcome_unavailable"] = unavailable
        quality_signals.append(
            {
                "code": "pm.generation.outcome",
                "severity": "info",
                "detail": "Bound own-call generation facts; parse/quality retain authority."
                if outcome is not None
                else str(unavailable),
                **diagnostic,
            }
        )
        return diagnostic

    async def _run_pm_stage(
        self,
        task_id: str,
        directive: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """运行 PM 阶段."""
        self._update_task_progress(task_id, "planning")
        quality_signals: list[dict[str, Any]] = []
        dedup_cancelled_rows = self._deduplicate_existing_board_tasks()
        quality_signals.extend(self._task_runtime_dedup_quality_signals(dedup_cancelled_rows))
        tasks_snapshot = self._list_board_task_rows()

        # Resolve the exact trusted policy snapshot before analyzing/rendering.
        # Free-form input_data and model metadata cannot supply output policy.
        planning_context: dict[str, Any] = {"request_purpose": "task_planning"}
        child_run_id = context.get("run_id")
        if isinstance(child_run_id, str) and child_run_id.strip():
            planning_context["run_id"] = child_run_id.strip()
        metadata = context.get("metadata")
        trusted_metadata = metadata if isinstance(metadata, dict) else {}
        for key in (*TIMEOUT_OVERRIDE_CONTEXT_KEYS, *OUTPUT_BUDGET_CONTEXT_KEYS, *BUDGET_CONTEXT_KEYS_CANONICAL):
            if key in context:
                planning_context[key] = deepcopy(context[key])
            elif key in trusted_metadata:
                planning_context[key] = deepcopy(trusted_metadata[key])
        output_budget_tokens = resolve_role_output_budget(
            context=planning_context, requested=DEFAULT_PLANNING_ROLE_OUTPUT_TOKENS
        )
        planning_context["llm_max_tokens"] = output_budget_tokens
        pm_deadline = context.get(
            "pm_planning_deadline_epoch_seconds", trusted_metadata.get("pm_planning_deadline_epoch_seconds")
        )
        if not any(key in planning_context for key in TIMEOUT_OVERRIDE_CONTEXT_KEYS):
            from math import ceil

            planning_context["llm_call_timeout_seconds"] = max(
                60, ceil(chief_engineer_generation_floor_seconds_for_output_tokens(output_budget_tokens))
            )

        def dispatch_context(mode: str) -> dict[str, Any]:
            dispatched = {**planning_context, "mode": mode}
            if pm_deadline is not None:
                deadline = float(pm_deadline)
                if isinstance(pm_deadline, bool) or not math.isfinite(deadline):
                    raise ValueError("pm_planning_budget_invalid_deadline")
                remaining = math.floor(deadline - time.time() - PM_PLANNING_SETTLEMENT_GRACE_SECONDS)
                floor = max(
                    60, math.ceil(chief_engineer_generation_floor_seconds_for_output_tokens(output_budget_tokens))
                )
                if remaining < floor:
                    raise ValueError(f"pm_planning_budget_deadline_exhausted:remaining={remaining},required={floor}")
                _, call_timeout = resolve_pm_planning_time_budget(output_budget_tokens, planning_context)
                dispatched["llm_call_timeout_seconds"] = min(call_timeout, remaining)
            return dispatched

        # Phase 2.1: Meta-planning - analyze directive before task generation
        directive_analysis: dict[str, Any] | None = None
        if directive and len(directive) > 50:
            directive_analysis = self._analyze_directive_complexity(directive, context)
            if directive_analysis:
                quality_signals.append(
                    {
                        "code": "pm.meta.planning",
                        "severity": "info",
                        "detail": (
                            f"complexity={directive_analysis.get('complexity')}; "
                            f"estimated_tasks={directive_analysis.get('estimated_task_count')}; "
                            f"strategy={directive_analysis.get('recommended_strategy')}"
                        ),
                        "analysis_source": directive_analysis.get("analysis_source"),
                    }
                )
                # Apply adaptive task count hint if complexity is high
                if (
                    directive_analysis.get("complexity") == "high"
                    and directive_analysis.get("estimated_task_count", 0) > 5
                ):
                    input_data = dict(input_data)
                    input_data["_meta_hint"] = directive_analysis

        projection_hint = self._extract_projection_contract_hint(
            input_data=input_data,
            context=context,
            directive=directive,
        )

        try:
            message = self._build_pm_message(
                tasks_snapshot,
                directive,
                projection_hint=projection_hint,
                directive_analysis=directive_analysis,
                output_budget_tokens=output_budget_tokens,
            )

            # Apply meta-planning hints to guide decomposition strategy
            if directive_analysis:
                message = self._apply_meta_planning_hints(message, directive_analysis)

            normalized_contracts: list[dict[str, Any]] = []
            quality: dict[str, Any] = {
                "ok": False,
                "score": 0,
                "critical_issues": ["pm_contracts_missing"],
                "warnings": [],
                "summary": "pm_contracts_missing_on_first_attempt",
            }
            raw_output = ""
            using_deterministic_contracts = False

            if self._deterministic_pm_contracts_enabled(input_data=input_data, context=context):
                using_deterministic_contracts = True
                if self._pm_route_audit_probe_enabled(input_data=input_data, context=context):
                    probe_message = self._build_deterministic_pm_route_probe_message(directive)
                    try:
                        probe_response = await self._call_role_llm(
                            probe_message,
                            context=self._build_deterministic_pm_route_probe_context(
                                task_id=task_id,
                                input_data=input_data,
                                context=context,
                            ),
                        )
                        probe_output = self._response_text(probe_response)
                        quality_signals.append(
                            {
                                "code": "pm.contracts.deterministic_route_probe",
                                "severity": "info",
                                "detail": (
                                    "PM deterministic contract mode emitted a real LLM route audit probe; "
                                    f"response_chars={len(probe_output)}"
                                ),
                            }
                        )
                    except (RuntimeError, ValueError) as e:
                        quality_signals.append(
                            {
                                "code": "pm.contracts.deterministic_route_probe_failed",
                                "severity": "warning",
                                "detail": str(e),
                            }
                        )
                contracts = self._synthesize_task_contracts_from_directive(
                    directive=directive,
                    projection_hint=projection_hint,
                )
                raw_output = json.dumps({"tasks": contracts}, ensure_ascii=False, indent=2)
                quality_signals.append(
                    {
                        "code": "pm.contracts.deterministic_fallback",
                        "severity": "warning",
                        "detail": "PM LLM invocation bypassed by deterministic planning fallback",
                    }
                )
                normalized_contracts, quality = self._evaluate_contract_quality(
                    contracts,
                    directive=directive,
                    context=context,
                )
            else:
                response = await self._call_role_llm(message, context=dispatch_context("pm_task_contract"))
                raw_output = self._response_text(response)
                generation_diagnostic = self._record_pm_generation_outcome(
                    response,
                    raw_output,
                    planning_context,
                    quality_signals,
                )
                contract_diagnostics: list[dict[str, Any]] = []
                contracts = self._extract_task_contracts(
                    raw_output,
                    directive=directive,
                    projection_hint=projection_hint,
                    diagnostics=contract_diagnostics,
                )
                if contracts:
                    normalized_contracts, quality = self._evaluate_contract_quality(
                        contracts,
                        directive=directive,
                        context=context,
                    )
                else:
                    quality = {
                        "ok": False,
                        "score": 0,
                        "critical_issues": [str(item["code"]) for item in contract_diagnostics]
                        or ["pm_contracts_missing"],
                        "warnings": [],
                        "summary": "; ".join(str(item["message"]) for item in contract_diagnostics)
                        or "pm_contracts_missing",
                        "contract_diagnostics": contract_diagnostics,
                    }
                    quality_signals.append(
                        {
                            "code": "pm.contracts.unparseable_first_attempt",
                            "severity": "warning",
                            "detail": "PM first attempt returned no parseable task contracts",
                            "contract_diagnostics": contract_diagnostics,
                        }
                    )

            if not using_deterministic_contracts and (
                (not contracts) or (not quality.get("ok", False) or int(quality.get("score") or 0) < 80)
            ):
                first_critical = quality.get("critical_issues")
                quality_signals.append(
                    {
                        "code": "pm.contracts.quality_revision",
                        "severity": "warning",
                        "detail": "PM first attempt requires the existing bounded quality revision",
                        "score": int(quality.get("score") or 0),
                        "critical_issues": [str(item) for item in first_critical[:8]]
                        if isinstance(first_critical, list)
                        else [],
                        "parsed_task_count": len(contracts),
                        "contract_diagnostics": quality.get("contract_diagnostics", []),
                    }
                )
                revision_base = make_revision_base(
                    raw_output,
                    workspace=self.workspace,
                    run_id=planning_context.get("run_id"),
                    task_id=task_id,
                    directive=directive,
                    quality=quality,
                    projection_hint=projection_hint,
                )
                if revision_base is not None:
                    base_evidence = {
                        "schema_version": "pm.task_revision_base.v1",
                        "base_hash": revision_base.base_hash,
                        "identity": revision_base.identity,
                        "base": {"tasks": revision_base.tasks},
                        "original_response": revision_base.response,
                        "original_response_hash": content_hash(revision_base.response),
                        "view": revision_base.view,
                        "execution_qualified": False,
                    }
                    base_text = canonical_json(base_evidence)
                    base_path = Path(
                        resolve_runtime_path(
                            self.workspace,
                            f"runtime/signals/pm_revision/{revision_base.base_hash}.base.json",
                        )
                    )
                    write_text_atomic(base_path, base_text, encoding="utf-8")
                    quality_signals.append(
                        {
                            "code": "pm.contracts.revision_base",
                            "severity": "info",
                            "detail": "Immutable original candidate retained before the one correction request; no execution authority.",
                            "base_hash": revision_base.base_hash,
                            "artifact_hash": content_hash(base_text),
                            "artifact_path": str(base_path),
                            "identity": revision_base.identity,
                            "original_response_hash": base_evidence["original_response_hash"],
                        }
                    )
                retry_prompt = self._build_pm_retry_message(
                    directive=directive,
                    quality=quality,
                    previous_output=raw_output,
                    validated_candidate=bool(contracts),
                    projection_hint=projection_hint,
                    revision_base=revision_base,
                    output_budget_tokens=output_budget_tokens,
                    generation_diagnostic=generation_diagnostic,
                    directive_analysis=directive_analysis,
                )
                response = await self._call_role_llm(retry_prompt, context=dispatch_context("pm_task_contract_retry"))
                retry_output = self._response_text(response)
                self._record_pm_generation_outcome(response, retry_output, planning_context, quality_signals)
                contract_diagnostics = []
                raw_merged_candidate: dict[str, Any] | None = None
                candidate_output = retry_output
                if revision_base is not None:
                    try:
                        raw_merged_candidate = apply_revision(revision_base, retry_output)
                        candidate_output = canonical_json(raw_merged_candidate)
                    except (_PMContractError, ValueError) as exc:
                        contract_diagnostics.append(
                            exc.diagnostic
                            if isinstance(exc, _PMContractError)
                            else {
                                "code": "pm_revision_invalid",
                                "path": "$",
                                "message": str(exc),
                            }
                        )
                        candidate_output = ""
                contracts = (
                    self._extract_task_contracts(
                        candidate_output,
                        directive=directive,
                        projection_hint=projection_hint,
                        diagnostics=contract_diagnostics,
                    )
                    if candidate_output
                    else []
                )
                if contracts:
                    normalized_contracts, quality = self._evaluate_contract_quality(
                        contracts,
                        directive=directive,
                        context=context,
                    )
                    if revision_base is not None:
                        dependency_result = self._validate_task_contracts(normalized_contracts)
                        if not dependency_result.is_valid:
                            quality = {
                                **quality,
                                "ok": False,
                                "score": 0,
                                "critical_issues": [
                                    *quality.get("critical_issues", []),
                                    "pm_revision_dependencies_invalid",
                                ],
                            }
                else:
                    quality_signals.append(
                        {
                            "code": "pm.contracts.unparseable_after_retry",
                            "severity": "error",
                            "detail": "PM retry still returned no parseable task contracts",
                            "contract_diagnostics": contract_diagnostics,
                        }
                    )
                    normalized_contracts = []
                    quality = {
                        "ok": False,
                        "score": 0,
                        "critical_issues": [str(item["code"]) for item in contract_diagnostics]
                        or ["pm_contracts_unparseable_after_retry"],
                        "warnings": [],
                        "summary": "; ".join(str(item["message"]) for item in contract_diagnostics)
                        or "pm_contracts_unparseable_after_retry",
                        "contract_diagnostics": contract_diagnostics,
                    }
                if revision_base is not None:
                    derivation = revision_derivation(
                        revision_base,
                        retry_output,
                        raw_merged_candidate=raw_merged_candidate,
                        normalized_plan=normalized_contracts,
                        quality=quality,
                    )
                    derivation_text = canonical_json(derivation)
                    derivation_hash = content_hash(derivation_text)
                    derivation_path = Path(
                        resolve_runtime_path(
                            self.workspace,
                            f"runtime/signals/pm_revision/{derivation_hash}.json",
                        )
                    )
                    write_text_atomic(derivation_path, derivation_text, encoding="utf-8")
                    quality_signals.append(
                        {
                            "code": "pm.contracts.revision_derivation",
                            "severity": "info",
                            "detail": "Retained model base plus explicit typed edits; raw merge and normalization are separate.",
                            "artifact_path": str(derivation_path),
                            "artifact_hash": derivation_hash,
                            "base_hash": revision_base.base_hash,
                            "original_response_hash": derivation["original_response_hash"],
                            "revision_response_hash": derivation["revision_response_hash"],
                            "raw_merged_candidate_hash": derivation["raw_merged_candidate_hash"],
                            "normalized_plan_hash": derivation["normalized_plan_hash"],
                            "identity": revision_base.identity,
                            "merged_candidate_is_original_provider_response": False,
                        }
                    )
                raw_output = f"{raw_output}\n\n[retry]\n{retry_output}"

            score = int(quality.get("score") or 0)
            _raw_critical = quality.get("critical_issues") if isinstance(quality, dict) else None
            critical_issues: list[str] = _raw_critical if isinstance(_raw_critical, list) else []
            contract_origin = "synthesized_fixture" if using_deterministic_contracts else "provider_planning"
            real_planning_qualified = bool(
                not using_deterministic_contracts
                and normalized_contracts
                and quality.get("ok") is True
                and not critical_issues
                and score >= 80
            )
            quality_signals.append(
                {
                    "code": "pm.contracts.origin",
                    "severity": "info",
                    "detail": "Contract origin and planning qualification are separate from route invocation.",
                    "contract_origin": contract_origin,
                    "real_planning_qualified": real_planning_qualified,
                }
            )
            if critical_issues or score < 80:
                quality_signals.append(
                    {
                        "code": "pm.quality.soft_failed",
                        "severity": "warning" if score >= 60 else "error",
                        "detail": (
                            f"PM quality below preferred threshold; score={score}; critical={len(critical_issues)}"
                        ),
                        "critical_issues": [str(item) for item in critical_issues[:8]],
                    }
                )
            if not normalized_contracts:
                quality_signals.append(
                    {
                        "code": "pm.tasks.empty_after_normalization",
                        "severity": "error",
                        "detail": "PM produced zero executable task contracts",
                    }
                )

            if critical_issues or not normalized_contracts or score < 80 or quality.get("ok") is not True:
                block_reason = (
                    f"PM quality gate blocked execution; score={score}; critical={len(critical_issues)}; "
                    f"tasks={len(normalized_contracts)}"
                )
                quality_signals.append(
                    {
                        "code": "pm.quality.blocked",
                        "severity": "error",
                        "detail": block_reason,
                        "critical_issues": [str(item) for item in critical_issues[:8]],
                        "contract_diagnostics": quality.get("contract_diagnostics", []),
                    }
                )
                plan_path = self._write_plan_artifact(
                    directive=directive,
                    task_contracts=normalized_contracts,
                    quality=quality,
                    quality_signals=quality_signals,
                )
                signal_rows = list(quality_signals)
                signal_rows.append(
                    {
                        "code": "pm.execution.summary",
                        "severity": "error",
                        "detail": (
                            f"tasks_created=0; score={score}; critical={len(critical_issues)}; "
                            "qa_required_for_final_verdict=true"
                        ),
                    }
                )
                signal_artifact = self._append_runtime_stage_signals(
                    stage="pm_planning",
                    task_id=task_id,
                    signals=signal_rows,
                    context=context,
                    source="pm_adapter",
                )
                blocked_artifacts: list[str] = [str(plan_path)]
                if signal_artifact:
                    blocked_artifacts.append(signal_artifact)
                self._update_task_progress(task_id, "failed")
                self.task_runtime.fail_task_row_from_role_adapter(
                    task_id,
                    reason="pm_quality_gate_blocked",
                    metadata={
                        "pm_quality_gate": {
                            "score": score,
                            "critical_issue_count": len(critical_issues),
                            "summary": str(quality.get("summary") or "").strip(),
                            "blocked": True,
                        }
                    },
                    role_id=self.role_id,
                    source="pm_adapter",
                    failure_class=FailureClassV1.QUALITY_GATE_BLOCKED.value,
                )
                return {
                    "success": False,
                    "contract_origin": contract_origin,
                    "real_planning_qualified": False,
                    "stage": "pm",
                    "tasks_created": 0,
                    "tasks": [],
                    "director_dispatched": False,
                    "qa_required_for_final_verdict": True,
                    "quality_gate": {
                        "score": score,
                        "critical_issue_count": len(critical_issues),
                        "summary": str(quality.get("summary") or "").strip(),
                        "signals": quality_signals,
                        "blocked": True,
                    },
                    "artifacts": blocked_artifacts,
                    "content_length": len(raw_output),
                }

            self._update_task_progress(task_id, "executing")
            created_tasks: list[dict[str, Any]] = self._create_board_tasks(normalized_contracts)
            plan_path = self._write_plan_artifact(
                directive=directive,
                task_contracts=normalized_contracts,
                quality=quality,
                quality_signals=quality_signals,
            )
            signal_rows = list(quality_signals)
            signal_rows.append(
                {
                    "code": "pm.execution.summary",
                    "severity": "info",
                    "detail": (
                        f"tasks_created={len(created_tasks)}; score={score}; "
                        f"critical={len(critical_issues)}; qa_required_for_final_verdict=true"
                    ),
                }
            )
            signal_artifact = self._append_runtime_stage_signals(
                stage="pm_planning",
                task_id=task_id,
                signals=signal_rows,
                context=context,
                source="pm_adapter",
            )
            success_artifacts: list[str] = [str(plan_path)]
            if signal_artifact:
                success_artifacts.append(signal_artifact)

            self._update_task_progress(task_id, "completed")
            return {
                "success": True,
                "contract_origin": contract_origin,
                "real_planning_qualified": real_planning_qualified,
                "stage": "pm",
                "tasks_created": len(created_tasks),
                "tasks": [t.get("subject", "unknown") for t in created_tasks],
                "director_dispatched": False,
                "qa_required_for_final_verdict": True,
                "quality_gate": {
                    "score": score,
                    "critical_issue_count": len(critical_issues),
                    "summary": str(quality.get("summary") or "").strip(),
                    "signals": quality_signals,
                },
                "artifacts": success_artifacts,
                "content_length": len(raw_output),
            }

        except (RuntimeError, ValueError) as e:
            quality_signals.append(
                {
                    "code": "pm.runtime.exception",
                    "severity": "error",
                    "detail": str(e),
                }
            )
            fallback_quality = {
                "ok": False,
                "score": 0,
                "critical_issues": ["pm_runtime_exception"],
                "warnings": [],
                "summary": f"pm_runtime_exception:{type(e).__name__}",
            }
            plan_path = self._write_plan_artifact(
                directive=directive,
                task_contracts=[],
                quality=fallback_quality,
                quality_signals=quality_signals,
            )
            signal_rows = list(quality_signals)
            signal_rows.append(
                {
                    "code": "pm.execution.summary",
                    "severity": "info",
                    "detail": "tasks_created=0; score=0; critical=1; qa_required_for_final_verdict=true",
                }
            )
            signal_artifact = self._append_runtime_stage_signals(
                stage="pm_planning",
                task_id=task_id,
                signals=signal_rows,
                context=context,
                source="pm_adapter",
            )
            error_artifacts: list[str] = [str(plan_path)]
            if signal_artifact:
                error_artifacts.append(signal_artifact)
            self._update_task_progress(task_id, "failed")
            try:
                transition_result = self.task_runtime.fail_task_row_from_role_adapter(
                    task_id,
                    reason="pm_runtime_exception",
                    metadata={"pm_error": str(e)},
                    role_id=self.role_id,
                    source="pm_adapter",
                    failure_class=FailureClassV1.ROLE_ADAPTER_EXCEPTION.value,
                )
            except (RuntimeError, ValueError) as transition_exc:
                self._record_task_runtime_transition_failure(
                    action="fail_task_row_from_role_adapter",
                    task_id=task_id,
                    failure={
                        "ok": False,
                        "error_code": "task_runtime_transition_exception",
                        "exception_type": type(transition_exc).__name__,
                        "message": str(transition_exc),
                    },
                    metadata={"reason": "pm_runtime_exception"},
                )
            else:
                execution_failure = (
                    task_row_execution_event_failure(transition_result) if isinstance(transition_result, dict) else None
                )
                if execution_failure is not None:
                    self._record_task_runtime_transition_failure(
                        action="fail_task_row_from_role_adapter",
                        task_id=task_id,
                        failure=execution_failure,
                        metadata={"reason": "pm_runtime_exception"},
                    )
            return {
                "success": False,
                "stage": "pm",
                "qa_required_for_final_verdict": True,
                "tasks_created": 0,
                "tasks": [],
                "director_dispatched": False,
                "quality_gate": {
                    "score": 0,
                    "critical_issue_count": 1,
                    "summary": f"pm_runtime_exception:{type(e).__name__}",
                    "signals": quality_signals,
                },
                "artifacts": error_artifacts,
                "error": str(e),
            }

    async def _call_role_llm(self, message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call PM planning through the role runtime/Context OS path.

        PM task contracts are still parsed and quality-gated locally, but the
        LLM turn must enter roles.runtime so Context OS and cognitive receipts
        are exercised in production workflows.
        """
        return await invoke_role_runtime_first(
            workspace=self.workspace,
            role=self.role_id,
            message=message,
            context=context,
            validate_output=False,
            max_retries=1,
        )

    @staticmethod
    def _pm_route_audit_probe_enabled(
        *,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        raw_flag = ""
        if isinstance(input_data, dict):
            raw_flag = str(input_data.get("pm_route_audit_probe") or "").strip().lower()
            if not raw_flag:
                raw_flag = (
                    str(PMPromptBuildingMixin._metadata_from_input(input_data).get("pm_route_audit_probe") or "")
                    .strip()
                    .lower()
                )
        if not raw_flag and isinstance(context, dict):
            raw_flag = str(context.get("pm_route_audit_probe") or "").strip().lower()
        return raw_flag in {"1", "true", "yes", "on"}

    @staticmethod
    def _build_deterministic_pm_route_probe_context(
        *,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Build an isolated PM route-probe context.

        The route probe exists only to prove that deterministic PM contract
        mode still enters RoleRuntime/ContextOS. It must never expose tools,
        inherit Director execution guards, or feed SESSION_PATCH/finalization
        residue back into the real planning contract.
        """

        metadata = PMPromptBuildingMixin._metadata_from_input(input_data)
        run_id = str(
            context.get("run_id")
            or context.get("factory_run_id")
            or metadata.get("run_id")
            or metadata.get("factory_run_id")
            or ""
        ).strip()
        session_id = str(
            context.get("session_id")
            or context.get("runtime_session_id")
            or metadata.get("session_id")
            or metadata.get("runtime_session_id")
            or ""
        ).strip()
        normalized_task_id = str(
            task_id
            or context.get("task_id")
            or context.get("pm_task_id")
            or metadata.get("task_id")
            or metadata.get("pm_task_id")
            or ""
        ).strip()
        turn_request_id = str(context.get("turn_request_id") or metadata.get("turn_request_id") or "").strip()
        if not turn_request_id:
            identity_material = "\n".join(
                (
                    "pm.route_audit_probe.v1",
                    run_id,
                    session_id,
                    normalized_task_id,
                )
            ).encode("utf-8")
            turn_request_id = f"pm-route-probe-{hashlib.sha256(identity_material).hexdigest()[:24]}"

        probe_context: dict[str, Any] = {
            "mode": "pm_task_contract_route_probe",
            "request_purpose": "route_probe",
            "real_planning_qualified": False,
            "deterministic_pm_contracts": True,
            "route_audit_probe": True,
            "task_id": normalized_task_id,
            "pm_task_id": normalized_task_id,
            # A route probe is a real physical Provider attempt even though
            # its output is advisory.  Bind it to an explicit stable turn
            # identity so TransactionKernel can audit/retry it without the
            # forbidden run/task/session fallback.
            "turn_request_id": turn_request_id,
            "disable_internal_tool_rounds": True,
            "tool_contract_require_no_tool_calls": True,
            "require_no_tool_calls": True,
            "no_tool_calls": True,
            "tool_contract": {
                "require_no_tool_calls": True,
                "execution_mode": "text_only_probe",
                "source": "pm.route_audit_probe",
            },
            "_transaction_kernel_forced_tool_definitions": [],
            "_transaction_kernel_forced_tool_choice": "none",
            "suppress_tool_policy_prompt": True,
            "suppress_working_memory_contract": True,
            "_transaction_kernel_suppress_session_patch": True,
        }
        if run_id:
            probe_context["run_id"] = run_id
        if session_id:
            probe_context["session_id"] = session_id
            probe_context["runtime_session_id"] = session_id
        return probe_context

    def _build_deterministic_pm_route_probe_message(self, directive: str) -> str:
        directive_excerpt = self._compact_text_for_prompt(directive or "No directive provided.", max_chars=1800)
        return (
            "PM route audit probe for deterministic contract mode.\n"
            "Respond with one short sentence confirming you are the PM planning role for this requirement.\n"
            "Do not produce task JSON, tool calls, implementation code, or project files.\n\n"
            "Requirement excerpt:\n"
            f"{directive_excerpt}"
        )
