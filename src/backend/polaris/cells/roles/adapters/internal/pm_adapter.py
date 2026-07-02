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
import json
import os
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, cast

from polaris.cells.orchestration.pm_planning.public.service import (
    autofix_pm_contract_for_quality,
    evaluate_pm_task_quality,
)
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.planning import (
    Plan,
    PlanStep,
    StructuralPlanValidator,
    ValidationResult,
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
    "SequenceMatcher",
    "StructuralPlanValidator",
    "ValidationResult",
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
        stage = str(input_data.get("stage", "pm")).strip().lower()
        directive = str(input_data.get("input", "")).strip()

        if stage == "architect":
            return await self._run_architect_stage(task_id, directive)
        return await self._run_pm_stage(task_id, directive, input_data, context)

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

    async def _run_pm_stage(
        self,
        task_id: str,
        directive: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """运行 PM 阶段."""
        self._update_task_progress(task_id, "planning")
        self._deduplicate_existing_board_tasks()
        tasks_snapshot = [task.to_dict() for task in self.task_board.list_all()]
        quality_signals: list[dict[str, Any]] = []

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
                normalized_contracts, quality = self._evaluate_contract_quality(contracts, directive=directive)
            else:
                response = await self._call_role_llm(message, context={"mode": "pm_task_contract"})
                raw_output = self._response_text(response)
                contracts = self._extract_task_contracts(
                    raw_output,
                    directive=directive,
                    projection_hint=projection_hint,
                )
                if contracts:
                    normalized_contracts, quality = self._evaluate_contract_quality(contracts, directive=directive)
                else:
                    quality_signals.append(
                        {
                            "code": "pm.contracts.unparseable_first_attempt",
                            "severity": "warning",
                            "detail": "PM first attempt returned no parseable task contracts",
                        }
                    )

            if not using_deterministic_contracts and (
                (not contracts) or (not quality.get("ok", False) or int(quality.get("score") or 0) < 80)
            ):
                retry_prompt = self._build_pm_retry_message(
                    directive=directive,
                    quality=quality,
                    previous_output=raw_output,
                    projection_hint=projection_hint,
                )
                response = await self._call_role_llm(retry_prompt, context={"mode": "pm_task_contract_retry"})
                retry_output = self._response_text(response)
                contracts = self._extract_task_contracts(
                    retry_output,
                    directive=directive,
                    projection_hint=projection_hint,
                )
                if contracts:
                    normalized_contracts, quality = self._evaluate_contract_quality(contracts, directive=directive)
                else:
                    quality_signals.append(
                        {
                            "code": "pm.contracts.unparseable_after_retry",
                            "severity": "error",
                            "detail": "PM retry still returned no parseable task contracts",
                        }
                    )
                    synthesized_contracts = self._synthesize_task_contracts_from_directive(
                        directive=directive,
                        projection_hint=projection_hint,
                    )
                    if synthesized_contracts:
                        normalized_contracts, quality = self._evaluate_contract_quality(
                            synthesized_contracts,
                            directive=directive,
                        )
                        quality_signals.append(
                            {
                                "code": "pm.contracts.synthetic_recovery",
                                "severity": "warning",
                                "detail": (
                                    "PM outputs remained unparseable after retry; "
                                    "recovered by deterministic directive-based contracts"
                                ),
                            }
                        )
                    else:
                        normalized_contracts = []
                        quality = {
                            "ok": False,
                            "score": 0,
                            "critical_issues": ["pm_contracts_unparseable_after_retry"],
                            "warnings": [],
                            "summary": "pm_contracts_unparseable_after_retry",
                        }
                raw_output = f"{raw_output}\n\n[retry]\n{retry_output}"

            if not using_deterministic_contracts and (
                (not normalized_contracts) or (not quality.get("ok", False) or int(quality.get("score") or 0) < 80)
            ):
                synthesized_contracts = self._synthesize_task_contracts_from_directive(
                    directive=directive,
                    projection_hint=projection_hint,
                )
                if synthesized_contracts:
                    synthesized_normalized, synthesized_quality = self._evaluate_contract_quality(
                        synthesized_contracts,
                        directive=directive,
                    )
                    if (
                        synthesized_normalized
                        and synthesized_quality.get("ok", False)
                        and int(synthesized_quality.get("score") or 0) >= 80
                    ):
                        normalized_contracts = synthesized_normalized
                        quality = synthesized_quality
                        quality_signals.append(
                            {
                                "code": "pm.contracts.synthetic_quality_recovery",
                                "severity": "warning",
                                "detail": (
                                    "PM LLM contracts failed the execution-readiness gate; "
                                    "recovered with deterministic directive-based contracts"
                                ),
                            }
                        )
                    else:
                        critical_raw = synthesized_quality.get("critical_issues")
                        quality_signals.append(
                            {
                                "code": "pm.contracts.synthetic_recovery_failed_quality",
                                "severity": "error",
                                "detail": str(synthesized_quality.get("summary") or "synthetic_quality_failed"),
                                "critical_issues": [
                                    str(item) for item in (critical_raw if isinstance(critical_raw, list) else [])[:8]
                                ],
                            }
                        )

            score = int(quality.get("score") or 0)
            _raw_critical = quality.get("critical_issues") if isinstance(quality, dict) else None
            critical_issues: list[str] = _raw_critical if isinstance(_raw_critical, list) else []
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

            if critical_issues or not normalized_contracts:
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
                self._update_board_task(
                    task_id,
                    status="failed",
                    metadata={
                        "pm_quality_gate": {
                            "score": score,
                            "critical_issue_count": len(critical_issues),
                            "summary": str(quality.get("summary") or "").strip(),
                            "blocked": True,
                        }
                    },
                )
                return {
                    "success": False,
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
            self._update_board_task(task_id, status="failed", metadata={"pm_error": str(e)})
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

        probe_context: dict[str, Any] = {
            "mode": "pm_task_contract_route_probe",
            "deterministic_pm_contracts": True,
            "route_audit_probe": True,
            "task_id": normalized_task_id,
            "pm_task_id": normalized_task_id,
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
