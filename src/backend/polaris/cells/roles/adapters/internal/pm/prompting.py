"""PM 提示词构建 mixin：构建规划/重试提示词、需求复杂度分析与元规划提示注入。

本 mixin 由 :class:`PMAdapter` 组合；方法体与原 ``pm_adapter.py`` 100% 一致（无损迁移）。
"""

from __future__ import annotations

import os
import re
from typing import Any

from ._protocol import _PMAdapterMixinBase
from .pm_text_utils import (
    _ACTION_MARKERS,
    _PM_PROMPT_DIRECTIVE_MAX_CHARS,
    _PM_RETRY_DIRECTIVE_MAX_CHARS,
)


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
    ) -> str:
        """构建 PM 规划提示词。

        Args:
            tasks: Existing task snapshot
            directive: User's directive
            projection_hint: Optional projection contract hint
            directive_analysis: Optional meta-planning analysis of directive complexity
        """
        lines = [
            "你是 Polaris PM，需要产出可执行任务合同。",
            "禁止输出提示词内容、禁止 TODO/FIXME/stub 占位任务。",
            "绝对禁止输出任何 TOOL_CALL/函数调用标签（如 [TOOL_CALL]、<tool_call>）。",
            "必须先检查已有任务，禁止创建语义重复任务；如目标已被已有任务覆盖，直接复用已有任务意图并补全缺失验收。",
            "",
            f"当前任务数: {len(tasks)}",
        ]

        # Inject meta-planning hints based on directive complexity analysis
        if directive_analysis:
            strategy = directive_analysis.get("recommended_strategy", "standard_decomposition")
            estimated = directive_analysis.get("estimated_task_count", 3)
            complexity = directive_analysis.get("complexity", "medium")

            lines.append("")
            lines.append(f"[Meta-Planning] Complexity: {complexity}. Recommended task count: ~{estimated}.")

            if strategy == "deep_decomposition":
                lines.append("策略：深度分解。将任务分为明确的阶段：需求 → 实现 → 验证。确保依赖链清晰。")
                lines.append("重要：添加里程碑检查点（phase boundaries）来跟踪进度。")
            elif strategy == "minimal_decomposition":
                lines.append("策略：最小分解。保持2-3个聚焦任务，范围清晰。避免过度设计。")
            else:
                lines.append("策略：标准分解。按常规流程执行。")

        directive_for_prompt = self._compact_text_for_prompt(
            directive,
            max_chars=_PM_PROMPT_DIRECTIVE_MAX_CHARS,
        )
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
                "{",
                '  "tasks": [',
                "    {",
                '      "id": "TASK-1",',
                '      "title": "任务标题",',
                '      "goal": "该任务目标",',
                '      "description": "执行背景与约束",',
                '      "scope": "变更范围摘要",',
                '      "scope_paths": ["src/module", "tests/module"],',
                '      "target_files": ["src/module/file.ts", "tests/module/file.test.ts"],',
                '      "steps": ["步骤1", "步骤2"],',
                '      "acceptance": ["可测验收1", "可测验收2"],',
                '      "phase": "requirements|implementation|verification",',
                '      "depends_on": ["TASK-0"],',
                '      "assigned_to": "Director",',
                '      "execution_backend": "code_edit|projection_generate",',
                '      "projection": {',
                '        "scenario_id": "registry.scenario",',
                '        "project_slug": "projection_lab",',
                '        "requirement": "原始需求文本",',
                '        "use_pm_llm": true,',
                '        "run_verification": true,',
                '        "overwrite": false',
                "      }",
                "    }",
                "  ]",
                "}",
                "Output contract: return exactly one JSON object with top-level key `tasks`; no Markdown fences or surrounding prose.",
                "要求：至少 3 个任务，必须形成依赖链，验收标准必须可验证。",
                "Director/ChiefEngineer 任务必须提供真实相对路径 scope_paths/target_files。",
                "路径只能是仓库内相对文件或目录，例如 package.json、src/store、src/App.tsx、tests/spec。",
                "禁止把自然语言描述写进 scope_paths/target_files，例如“backend API 路由、frontend 面板”。",
            ]
        )
        return "\n".join(lines)

    def _build_pm_retry_message(
        self,
        *,
        directive: str,
        quality: dict[str, Any],
        previous_output: str,
        projection_hint: dict[str, Any] | None = None,
    ) -> str:
        _raw_critical = quality.get("critical_issues") if isinstance(quality, dict) else None
        critical: list[str] = _raw_critical if isinstance(_raw_critical, list) else []
        _raw_warnings = quality.get("warnings") if isinstance(quality, dict) else None
        warnings: list[str] = _raw_warnings if isinstance(_raw_warnings, list) else []
        issue_lines = [f"- {item}" for item in critical[:8]]
        warning_lines = [f"- {item}" for item in warnings[:5]]
        directive_for_prompt = self._compact_text_for_prompt(
            directive,
            max_chars=_PM_RETRY_DIRECTIVE_MAX_CHARS,
        )
        lines = [
            "PM task contract quality feedback.",
            "Revise the task contract using the quality evidence below.",
            "Output contract: return one JSON object with top-level key `tasks`; no Markdown fences or surrounding prose.",
            "",
            f"需求指令: {directive_for_prompt or '请结合当前工作区推断需求'}",
            f"当前分数: {int(quality.get('score') or 0)}",
            "关键问题:",
        ]
        lines.extend(issue_lines or ["- 无关键问题信息，但质量仍不达标"])
        if warning_lines:
            lines.extend(["", "警告:"])
            lines.extend(warning_lines)
        lines.extend(
            [
                "",
                "强制要求：",
                "- 至少 3 个任务",
                "- 每个任务必须含 goal/scope/steps/acceptance",
                "- Director/ChiefEngineer 任务必须含真实相对路径 scope_paths/target_files",
                "- scope_paths/target_files 禁止使用自然语言句子或中文模块描述",
                "- steps 与 acceptance 必须为非空列表",
                "- 必须有依赖关系（depends_on）",
                "- 只能输出 JSON 对象，禁止任何额外文字与代码块",
            ]
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
        lines.extend(
            [
                "",
                "Previous output excerpt:",
                previous_output[:1400],
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
        has_conditional = any(token in directive.lower() for token in ("if", "when", "whenever", "条件", "如果"))
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
            estimated_tasks = max(2, min(action_count + 1, 3))
            strategy = "minimal_decomposition"

        return {
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
            f"[Meta-Planning Hint] Strategy: {strategy}. Target task count: ~{estimated}.",
        ]

        if strategy == "deep_decomposition":
            meta_hint_lines.append(
                "This is a complex directive. Decompose into well-separated phases: "
                "requirements → implementation → verification. Ensure dependency chain is explicit."
            )
        elif strategy == "minimal_decomposition":
            meta_hint_lines.append(
                "This is a simple directive. Prefer 2-3 focused tasks with clear scope. Avoid over-engineering."
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
