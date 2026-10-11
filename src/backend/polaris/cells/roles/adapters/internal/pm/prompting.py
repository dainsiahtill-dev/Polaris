"""PM 提示词构建 mixin：构建规划/重试提示词、需求复杂度分析与元规划提示注入。

本 mixin 由 :class:`PMAdapter` 组合；首次与修订共用验证命令规范。
"""

from __future__ import annotations

import json
import os
import re
from hashlib import sha256
from typing import Any

from polaris.cells.orchestration.pm_planning.public.contracts import parse_pm_product_planning_input

from ._protocol import _PMAdapterMixinBase
from .normalization import _PM_VERIFICATION_MODALITY_ORDER
from .pm_text_utils import _ACTION_MARKERS
from .revision import PMRevisionBase, canonical_json


def _pm_task_output_example(projection_hint: dict[str, Any] | None) -> str:
    """One existing task-field contract for initial and full-JSON revision."""
    task: dict[str, Any] = {
        "id": "TASK-1",
        "title": "任务标题",
        "goal": "该任务目标",
        "scope_paths": ["src/module", "tests/module"],
        "target_files": ["src/module/file.ts", "tests/module/file.test.ts"],
        "steps": ["步骤1", "步骤2"],
        "acceptance": ["可测验收1", "可测验收2"],
        "verification_commands": [
            {"modality": "|".join(_PM_VERIFICATION_MODALITY_ORDER), "argv": ["executable", "arg"], "cwd": "."}
        ],
        "phase": "implementation",
        "depends_on": [],
        "assigned_to": "Director",
        "execution_backend": "code_edit",
    }
    if projection_hint:
        task["execution_backend"] = "projection_generate"
        task["projection"] = dict(projection_hint["projection"])
    fields = [f"      {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}" for key, value in task.items()]
    return '{\n  "tasks": [\n    {\n' + ",\n".join(fields) + "\n    }\n  ]\n}"


class PMPromptBuildingMixin(_PMAdapterMixinBase):
    """PM 提示词构建 mixin：构建规划/重试提示词、需求复杂度分析与元规划提示注入。"""

    @staticmethod
    def _response_text(response: Any) -> str:
        if isinstance(response, dict):
            return str(response.get("response") or response.get("content") or "")
        return str(response or "")

    @staticmethod
    def _metadata_from_input(input_data: dict[str, Any]) -> dict[str, Any]:
        raw_metadata = input_data.get("metadata") if isinstance(input_data, dict) else None
        return raw_metadata if isinstance(raw_metadata, dict) else {}

    @staticmethod
    def _compact_text_for_prompt(text: str, *, max_chars: int) -> str:
        normalized = str(text or "").strip()
        if len(normalized) <= max_chars:
            return normalized
        head_chars = max(max_chars * 2 // 3, 1)
        tail_chars = max(max_chars - head_chars, 1)
        omitted = len(normalized) - head_chars - tail_chars
        return (
            normalized[:head_chars].rstrip()
            + f"\n\n[... omitted {omitted} chars for PM prompt budget ...]\n\n"
            + normalized[-tail_chars:].lstrip()
        )

    @staticmethod
    def _deterministic_pm_contracts_enabled(
        *,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        raw_flag = ""
        if isinstance(input_data, dict):
            raw_flag = str(input_data.get("deterministic_pm_contracts") or "").strip().lower()
            if not raw_flag:
                raw_flag = (
                    str(PMPromptBuildingMixin._metadata_from_input(input_data).get("deterministic_pm_contracts") or "")
                    .strip()
                    .lower()
                )
        if not raw_flag and isinstance(context, dict):
            raw_flag = str(context.get("deterministic_pm_contracts") or "").strip().lower()
        if not raw_flag:
            raw_flag = str(os.environ.get("KERNELONE_PM_DETERMINISTIC_CONTRACTS", "")).strip().lower()
        return raw_flag in {"1", "true", "yes", "on"}

    def _build_pm_message(
        self,
        tasks: list[dict[str, Any]],
        directive: str,
        *,
        projection_hint: dict[str, Any] | None = None,
        directive_analysis: dict[str, Any] | None = None,
        output_budget_tokens: int | None = None,
    ) -> str:
        """构建 PM 规划提示词。

        Args:
            tasks: Existing task snapshot
            directive: User's directive
            projection_hint: Optional projection contract hint
            directive_analysis: Optional meta-planning analysis of directive complexity
        """
        projection_hint = self._extract_projection_contract_hint(
            input_data=projection_hint or {}, context={}, directive=directive
        )
        lines = [
            "你是 Polaris PM，需要产出可执行任务合同。",
            "禁止输出提示词内容、禁止 TODO/FIXME/stub 占位任务。",
            "绝对禁止输出任何 TOOL_CALL/函数调用标签（如 [TOOL_CALL]、<tool_call>）。",
            "必须先检查已有任务，禁止创建语义重复任务；如目标已被已有任务覆盖，直接复用已有任务意图并补全缺失验收。",
            "",
            f"当前任务数: {len(tasks)}",
        ]
        if output_budget_tokens is not None:
            lines.append(
                f"Prospective output allowance: {output_budget_tokens} tokens; canonical request policy, not observed completion."
            )
        lines.append(
            "简洁输出完整合同：可省略空可选字段与重复 description/scope；独特需求/约束保留在 goal/steps/acceptance/description。不要截断 JSON 或需求。"
        )
        if directive_analysis and directive_analysis.get("analysis_source") == "factory.catalog_contract":
            lines.append(
                "Factory 原生 PM/Chief Engineer 承担规划与蓝图，不创建重复的仅需求/蓝图 Director 任务；独立请求的有用文档仍保留。产品实现、验证与依赖必须完整。"
            )

        # Inject meta-planning hints based on directive complexity analysis
        if directive_analysis:
            strategy = directive_analysis.get("recommended_strategy", "standard_decomposition")
            estimated = directive_analysis.get("estimated_task_count", 3)
            complexity = directive_analysis.get("complexity", "medium")

            lines.append("")
            lines.append(
                f"[Meta-Planning] Complexity: {complexity}. Advisory task count: ~{estimated}; preserve all requirements, minimum 3."
            )

            if strategy == "deep_decomposition":
                if directive_analysis.get("analysis_source") == "factory.catalog_contract":
                    lines.append(
                        "策略：深度分解产品实现与验证。原生 PM/Chief Engineer 已承担规划与蓝图，不重复创建仅需求/蓝图文档的 Director 任务；独立请求的有用文档仍保留。"
                    )
                else:
                    lines.append("策略：深度分解。将任务分为明确的阶段：需求 → 实现 → 验证。确保依赖链清晰。")
                    lines.append("重要：添加里程碑检查点（phase boundaries）来跟踪进度。")
            elif strategy == "minimal_decomposition":
                lines.append("策略：最小分解。至少3个聚焦任务，范围清晰；保留完整需求。")
            else:
                lines.append("策略：标准分解。按常规流程执行。")

        directive_for_prompt = directive
        if directive_for_prompt:
            lines.extend(
                [
                    "需求指令:",
                    directive_for_prompt,
                ]
            )
        if projection_hint:
            _raw_proj = projection_hint.get("projection") if isinstance(projection_hint, dict) else None
            projection: dict[str, Any] = _raw_proj if isinstance(_raw_proj, dict) else {}
            lines.extend(
                [
                    "",
                    "受控 Projection 契约约束：",
                    "- 已显式要求第一个 Director 任务走 projection_generate 后端",
                    f"- scenario_id: {projection.get('scenario_id') or 'required'}",
                    f"- project_slug: {projection.get('project_slug') or 'required'}",
                    "- projection.requirement 必须保留原始需求语义，不得改写成提示词",
                    "- 后续常规增量任务如无特殊要求，显式使用 execution_backend=code_edit",
                ]
            )
        if tasks:
            lines.append("")
            lines.append("已有任务（最多 10 条）:")
            for task in tasks[:10]:
                lines.append(f"- {task.get('subject', 'unknown')} [{task.get('status', 'unknown')}]")

        lines.extend(
            [
                "",
                "请仅输出 JSON，格式如下：",
                _pm_task_output_example(projection_hint),
                "Output contract: return exactly one JSON object with top-level key `tasks`; no Markdown fences or surrounding prose.",
                "要求：至少 3 个任务，必须形成依赖链，验收标准必须可验证。",
                "Director/ChiefEngineer 任务必须提供真实相对路径 scope_paths/target_files。",
                "路径只能是仓库内相对文件或目录，例如 package.json、src/store、src/App.tsx、tests/spec。",
                "禁止把自然语言描述写进 scope_paths/target_files，例如“backend API 路由、frontend 面板”。",
                "每个任务必须显式提供 verification_commands；没有命令的任务使用空数组。",
                "target_files 是模型拥有的写入产物；context_files 仅只读依赖。code_edit 验证必须拥有有用的测试/报告，纯里程碑交原生 QA/依赖/验收流程。",
                "可选 generated_outputs=[{path,inputs:[{task_id,path}],build:{argv,cwd}}]；owner 为所在任务；输入是自己或依赖祖先的 authored targets；build 按完整值绑定本任务 build verifier。生成声明不授予直接写权限、不代表构建已完成。",
                "项目整体必须声明真实 environment_prep、至少一个 build/test/lint、以及应用入口命令；library 可由 CE 显式判定 entrypoint N/A。",
                "verification_commands 只能使用 argv 数组与相对 cwd；禁止 shell command string、echo-only 或 shell -c 包装。",
            ]
        )
        return "\n".join(lines)

    def _build_pm_retry_message(
        self,
        *,
        directive: str,
        quality: dict[str, Any],
        previous_output: str,
        validated_candidate: bool = False,
        projection_hint: dict[str, Any] | None = None,
        revision_base: PMRevisionBase | None = None,
        output_budget_tokens: int | None = None,
        generation_diagnostic: dict[str, Any] | None = None,
        directive_analysis: dict[str, Any] | None = None,
    ) -> str:
        """Revise full JSON; only strictly parsed candidates receive full feedback."""
        if revision_base is not None:
            # Lossless grouping avoids repeating the same program explanation
            # for every verifier/output row in a bounded context window.
            grouped: dict[str, dict[str, Any]] = {}
            for diagnostic in quality.get("contract_diagnostics", []):
                if not isinstance(diagnostic, dict):
                    continue
                shared = {
                    key: diagnostic[key] for key in ("code", "task_id", "message", "provenance") if key in diagnostic
                }
                group = grouped.setdefault(canonical_json(shared), {**shared, "paths": []})
                group["paths"].append(diagnostic.get("path"))
            visible_diagnostics = []
            for group in grouped.values():
                row = dict(group)
                if len(row["paths"]) == 1:
                    row["path"] = row.pop("paths")[0]
                visible_diagnostics.append(row)
            # This private output mode derives a full plan server-side. It neither
            # replaces the global PM role nor clips required content for budget fit.
            return "\n".join(
                [
                    "PM task contract quality feedback. Private bounded revision.",
                    f"Prospective output allowance: {output_budget_tokens} tokens; canonical request policy, not observed completion."
                    if output_budget_tokens is not None
                    else "Output allowance unavailable.",
                    "Return one bare pm.task_revision.v1 JSON object; not a replacement plan.",
                    "Exact keys: schema_version,base_hash,updates,add_tasks,remove_task_ids.",
                    "updates: [{task_id,set:{field:complete_value},append:{steps:[new_step],acceptance:[new_criterion]}}].",
                    "Only manifest fields permit edits; display grants none. "
                    "absent_fields means no original value; hidden fields remain intact. append preserves all old elements.",
                    "Existing id/assigned_to/execution_backend/projection/metadata are immutable. "
                    "No task removal. New tasks require full contracts, unique IDs and code_edit; "
                    "no projection override or reused ID.",
                    "Merged plan must pass original quality/verifier/coverage/dependency gates. JSON only.",
                    "verification_commands rows: exact modality/argv/cwd; modality="
                    + "|".join(_PM_VERIFICATION_MODALITY_ORDER)
                    + "; argv array only; no shell -c. New tasks need id/title/goal/scope_paths/target_files/steps/acceptance/verification_commands.",
                    "target_files own writes; context_files are read-only. Verification code_edit must own a useful test/report; pure milestones use native QA/dependency/acceptance orchestration. "
                    "Optional generated_outputs=[{path,inputs:[{task_id,path}],build:{argv,cwd}}], exact keys. Owner is containing task; inputs bind self/ancestor authored targets; build equals its typed build verifier by value. No direct write grant or completed-build evidence.",
                    "Original requirement (complete):",
                    directive,
                    "Quality evidence (not execution authority):",
                    canonical_json(
                        {
                            "critical_issues": quality.get("critical_issues", []),
                            "warnings": quality.get("warnings", []),
                            "contract_diagnostics": visible_diagnostics,
                        }
                    ),
                    "Revision base/view/manifest (server-owned):",
                    revision_base.view_json,
                ]
            )
        projection_hint = self._extract_projection_contract_hint(
            input_data=projection_hint or {}, context={}, directive=directive
        )
        _raw_critical = quality.get("critical_issues") if isinstance(quality, dict) else None
        critical: list[str] = _raw_critical if isinstance(_raw_critical, list) else []
        _raw_warnings = quality.get("warnings") if isinstance(quality, dict) else None
        warnings: list[str] = _raw_warnings if isinstance(_raw_warnings, list) else []
        issue_lines = [f"- {item}" for item in (critical if validated_candidate else critical[:8])]
        warning_lines = [f"- {item}" for item in (warnings if validated_candidate else warnings[:5])]
        directive_for_prompt = directive
        lines = [
            "PM task contract quality feedback.",
            "Revise the task contract using the quality evidence below.",
            "Output contract: return one JSON object with top-level key `tasks`; no Markdown fences or surrounding prose.",
            _pm_task_output_example(projection_hint),
            "",
            f"需求指令: {directive_for_prompt or '请结合当前工作区推断需求'}",
            f"当前分数: {int(quality.get('score') or 0)}",
            "关键问题:",
        ]
        if output_budget_tokens is not None:
            lines.append(
                f"Prospective output allowance: {output_budget_tokens} tokens; canonical request policy, not observed completion."
            )
        if directive_analysis and directive_analysis.get("analysis_source") == "factory.catalog_contract":
            lines.append(
                "Factory 原生 PM/Chief Engineer 已承担规划与蓝图，不生成重复的仅需求/蓝图 Director 任务；保留独立文档需求。"
            )
        lines.append("简洁完整 JSON；省略空可选字段与重复 prose，保留所有独特约束及授权字段。")
        lines.extend(issue_lines or ["- 无关键问题信息，但质量仍不达标"])
        if warning_lines:
            lines.extend(["", "警告:"])
            lines.extend(warning_lines)
        lines.extend(
            [
                "",
                "强制要求：",
                "- 至少 3 个任务",
                "- 每个任务必须含 goal/steps/acceptance；scope/description 可省略重复说明，独特约束必须完整保留",
                "- Director/ChiefEngineer 任务必须含真实相对路径 scope_paths/target_files",
                "- scope_paths/target_files 禁止使用自然语言句子或中文模块描述",
                "- steps 与 acceptance 必须为非空列表",
                "- 每个任务必须含 verification_commands 数组；行字段严格为 modality/argv/cwd",
                "- modality 只能是 "
                + "|".join(_PM_VERIFICATION_MODALITY_ORDER)
                + " 中的一个值；read/exec 不是有效枚举",
                "- 项目整体必须含 environment_prep、至少一个 build/test/lint，并为应用声明 entrypoint",
                "- verification command 必须是 argv 数组，禁止 shell command string 或 shell -c",
                "- target_files 只含拥有的写入产物；context_files 只读；code_edit 验证必须拥有有用测试/报告。",
                "- generated_outputs 可选，严格为 [{path,inputs:[{task_id,path}],build:{argv,cwd}}]；绑定自己/祖先源文件及本任务 typed build 完整值；不授予写权限或执行成功。",
                "- 必须有依赖关系（depends_on）",
                "- 只能输出 JSON 对象，禁止任何额外文字与代码块",
            ]
        )
        diagnostics = quality.get("contract_diagnostics")
        if isinstance(diagnostics, list) and diagnostics:
            lines.extend(["", "合同结构诊断（不构成执行权限）："])
            for diagnostic in diagnostics if validated_candidate else diagnostics[:8]:
                if isinstance(diagnostic, dict):
                    lines.append(
                        f"- {diagnostic.get('path')}: {diagnostic.get('code')}: {diagnostic.get('message')}"
                        f"; task_id={diagnostic.get('task_id')}; provenance={canonical_json(diagnostic.get('provenance', {}))}"
                        f"; allowed={diagnostic.get('allowed', [])}"
                    )
        if projection_hint:
            _raw_proj = projection_hint.get("projection") if isinstance(projection_hint, dict) else None
            projection: dict[str, Any] = _raw_proj if isinstance(_raw_proj, dict) else {}
            lines.extend(
                [
                    "- 第一个任务必须显式使用 execution_backend=projection_generate",
                    f"- projection.scenario_id 必须为 {projection.get('scenario_id') or 'required'}",
                    f"- projection.project_slug 必须为 {projection.get('project_slug') or 'required'}",
                    "- projection.requirement 必须直接复述原始需求，不要改写成系统提示",
                    "- 其余常规 Director 任务必须显式写 execution_backend=code_edit",
                ]
            )
        if validated_candidate:
            lines.extend(
                [
                    "",
                    "Complete validated candidate (model-authored; quality failed):",
                    "Preserve every original task ID and relevant declared fields while fixing the diagnostics; "
                    "return the complete replacement JSON, not a delta or a fragment.",
                    previous_output,
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "Incomplete/unparseable candidate: no revision base or executable prefix. Return one complete replacement JSON once.",
                    "Prior generation diagnostic (not task authority):",
                    canonical_json(
                        generation_diagnostic
                        or {
                            "generation_outcome_unavailable": "generation_outcome_missing",
                            "response_sha256": sha256(previous_output.encode("utf-8")).hexdigest(),
                            "response_chars": len(previous_output),
                        }
                    ),
                ]
            )
        return "\n".join(lines)

    def _analyze_directive_complexity(
        self,
        directive: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Analyze directive complexity to guide adaptive task decomposition.

        Args:
            directive: The user's request/prompt
            context: Execution context

        Returns:
            Analysis result with complexity, estimated_task_count, recommended_strategy
        """
        if not directive:
            return {}

        metadata = context.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        payload = context.get("pm_product_planning_input", metadata.get("pm_product_planning_input"))
        product_input = parse_pm_product_planning_input(payload, directive=directive)
        analysis_source = "factory.catalog_contract" if product_input is not None else "directive"
        if product_input is not None:
            directive = product_input.semantic_text()

        # Complexity indicators
        length = len(directive)
        has_technical_terms = any(
            token in directive.lower()
            for token in (
                "api",
                "database",
                "authentication",
                "frontend",
                "backend",
                "deployment",
                "ci/cd",
                "test",
                "schema",
            )
        )
        has_multiple_targets = directive.count("/") >= 2 or directive.count("\n") >= 2
        has_conditional = bool(re.search(r"\b(?:if|when|whenever)\b", directive, re.IGNORECASE)) or any(
            token in directive for token in ("条件", "如果")
        )
        has_iteration = any(token in directive.lower() for token in ("iterate", "loop", "batch", "批量", "循环"))

        # Count structural keywords
        action_count = sum(1 for token in _ACTION_MARKERS if token in directive.lower())
        target_files_count = len(re.findall(r"[A-Za-z]:[\\/]|/[\w.\-/\\]+", directive))

        # Determine complexity
        complexity_factors = [
            length > 300,
            has_technical_terms,
            has_multiple_targets,
            has_conditional,
            has_iteration,
            action_count >= 3,
            target_files_count >= 3,
        ]
        complexity_score = sum(complexity_factors)

        if complexity_score >= 5:
            complexity = "high"
            estimated_tasks = max(5, target_files_count // 2 + action_count)
            strategy = "deep_decomposition"
        elif complexity_score >= 3:
            complexity = "medium"
            estimated_tasks = max(3, action_count + 1)
            strategy = "standard_decomposition"
        else:
            complexity = "low"
            estimated_tasks = 3
            strategy = "minimal_decomposition"

        return {
            "analysis_source": analysis_source,
            "has_conditional": has_conditional,
            "complexity": complexity,
            "estimated_task_count": estimated_tasks,
            "recommended_strategy": strategy,
            "action_count": action_count,
            "target_files_hint": target_files_count,
            "has_technical_terms": has_technical_terms,
            "has_multiple_targets": has_multiple_targets,
        }

    def _apply_meta_planning_hints(
        self,
        message: str,
        directive_analysis: dict[str, Any],
    ) -> str:
        """Apply meta-planning hints to the PM message to guide decomposition strategy.

        Args:
            message: Original PM prompt message
            directive_analysis: Result from _analyze_directive_complexity

        Returns:
            Modified message with meta-planning hints injected
        """
        if not directive_analysis:
            return message

        if "[Meta-Planning]" in message or "[Meta-Planning Hint]" in message:
            return message

        strategy = directive_analysis.get("recommended_strategy", "standard_decomposition")
        estimated = directive_analysis.get("estimated_task_count", 3)

        # Build meta-planning hint section
        meta_hint_lines = [
            "",
            f"[Meta-Planning Hint] Strategy: {strategy}. Advisory task count: ~{estimated}; minimum 3, preserve full requirements.",
        ]

        if directive_analysis.get("analysis_source") == "factory.catalog_contract":
            meta_hint_lines.append(
                "Native PM/Chief Engineer own planning/blueprints. Plan product implementation and verification; preserve independently requested documentation."
            )
        elif strategy == "deep_decomposition":
            meta_hint_lines.append(
                "This is a complex directive. Decompose into well-separated phases: "
                "requirements → implementation → verification. Ensure dependency chain is explicit."
            )
        elif strategy == "minimal_decomposition":
            meta_hint_lines.append(
                "This is a simple directive. Use at least 3 focused tasks with clear scope; preserve requirements."
            )

        complexity = directive_analysis.get("complexity", "medium")
        if complexity == "high":
            meta_hint_lines.append("Important: Add milestone checkpoints (phase boundaries) in the task decomposition.")
            if directive_analysis.get("has_technical_terms"):
                meta_hint_lines.append(
                    "Technical directive detected. Ensure acceptance criteria include verifiable build/test outcomes."
                )

        meta_hint = "\n".join(meta_hint_lines).strip()

        # Inject before the JSON format section. The previous implementation
        # inserted hint text inside the example "tasks" array, producing an
        # invalid JSON-shaped example for PM planning.
        json_marker = "请仅输出 JSON"
        if json_marker in message:
            return message.replace(json_marker, f"{meta_hint}\n\n{json_marker}", 1)

        tasks_marker = '"tasks": ['
        if tasks_marker in message:
            index = message.find(tasks_marker)
            prefix = message[:index].rstrip()
            suffix = message[index:]
            if prefix:
                return f"{prefix}\n\n{meta_hint}\n\n{suffix}"
            return f"{meta_hint}\n\n{suffix}"

        return message
