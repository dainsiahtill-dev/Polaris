"""PM 角色适配器.

强化任务合同生成与质量门禁，避免"有任务但不可执行"的空壳输出。
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, cast

from polaris.bootstrap.config import get_settings
from polaris.cells.llm.dialogue.public.service import generate_role_response
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

_DEFAULT_PHASE_SEQUENCE = ("requirements", "implementation", "verification")
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "task",
    "tasks",
    "feature",
    "project",
    "module",
    "system",
    "please",
    "need",
    "build",
    "implement",
    "create",
    "develop",
}
_ACTION_MARKERS = ("implement", "build", "define", "design", "create", "实现", "构建", "设计", "编写", "定义")
_TASK_LINE_PREFIX = re.compile(r"^(?:[-*]\s+|\d+[.)]\s+|[（(]?\d+[）)]\s+)")
_TASK_SECTION_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:task|任务)\s*[-_ ]*(\d+)?\s*[:：.\-]?\s*(.*?)\s*$",
    re.IGNORECASE,
)


class PMAdapter(BaseRoleAdapter):
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
            settings = get_settings()
            response = await generate_role_response(
                workspace=self.workspace,
                settings=settings,
                role="architect",
                message=message,
                context=None,
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

            settings = get_settings()
            response = await generate_role_response(
                workspace=self.workspace,
                settings=settings,
                role=self.role_id,
                message=message,
                context=None,
                validate_output=False,
                max_retries=1,
            )
            raw_output = str(response.get("response") or "") if isinstance(response, dict) else str(response or "")
            contracts = self._extract_task_contracts(
                raw_output,
                directive=directive,
                projection_hint=projection_hint,
            )
            normalized_contracts: list[dict[str, Any]] = []
            quality: dict[str, Any] = {
                "ok": False,
                "score": 0,
                "critical_issues": ["pm_contracts_missing"],
                "warnings": [],
                "summary": "pm_contracts_missing_on_first_attempt",
            }
            if contracts:
                normalized_contracts, quality = self._evaluate_contract_quality(contracts)
            else:
                quality_signals.append(
                    {
                        "code": "pm.contracts.unparseable_first_attempt",
                        "severity": "warning",
                        "detail": "PM first attempt returned no parseable task contracts",
                    }
                )

            if (not contracts) or (not quality.get("ok", False) or int(quality.get("score") or 0) < 80):
                retry_prompt = self._build_pm_retry_message(
                    directive=directive,
                    quality=quality,
                    previous_output=raw_output,
                    projection_hint=projection_hint,
                )
                settings = get_settings()
                response = await generate_role_response(
                    workspace=self.workspace,
                    settings=settings,
                    role=self.role_id,
                    message=retry_prompt,
                    context=None,
                    validate_output=False,
                    max_retries=1,
                )
                retry_output = (
                    str(response.get("response") or "") if isinstance(response, dict) else str(response or "")
                )
                contracts = self._extract_task_contracts(
                    retry_output,
                    directive=directive,
                    projection_hint=projection_hint,
                )
                if contracts:
                    normalized_contracts, quality = self._evaluate_contract_quality(contracts)
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
                        normalized_contracts, quality = self._evaluate_contract_quality(synthesized_contracts)
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
            artifacts: list[str] = [str(plan_path)]
            if signal_artifact:
                artifacts.append(signal_artifact)

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
                "artifacts": artifacts,
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

        if directive:
            lines.extend(
                [
                    "需求指令:",
                    directive,
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
                '      "scope": "变更范围（文件/模块）",',
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
                "禁止返回 Markdown、解释文本、代码块或工具调用标签；仅返回一个 JSON 对象。",
                "要求：至少 3 个任务，必须形成依赖链，验收标准必须可验证。",
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
        lines = [
            "上一版 PM 合同未通过质量门禁，请重写并只输出 JSON。",
            "禁止输出 [TOOL_CALL]、<tool_call>、函数调用或任意工具参数。",
            "",
            f"需求指令: {directive or '请结合当前工作区推断需求'}",
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
                "上一版输出片段：",
                previous_output[:1400],
            ]
        )
        return "\n".join(lines)

    def _extract_task_contracts(
        self,
        response: str,
        *,
        directive: str,
        projection_hint: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._extract_json_payload(response)
        raw_tasks: list[Any] = self._extract_tasks_from_payload(payload)

        if raw_tasks:
            contracts = [
                self._normalize_task_contract(item, idx + 1, directive)
                for idx, item in enumerate(raw_tasks)
                if isinstance(item, dict)
            ]
            return self._apply_projection_contract_hint(
                [item for item in contracts if item],
                projection_hint=projection_hint,
            )

        section_contracts = self._extract_tasks_from_sections(response, directive=directive)
        if section_contracts:
            return self._apply_projection_contract_hint(
                section_contracts,
                projection_hint=projection_hint,
            )

        return self._apply_projection_contract_hint(
            self._extract_tasks_from_bullets(response, directive=directive),
            projection_hint=projection_hint,
        )

    @classmethod
    def _extract_tasks_from_payload(cls, payload: Any) -> list[Any]:
        if payload is None:
            return []

        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        queue: list[Any] = [payload]
        visited: set[int] = set()
        candidate_keys = (
            "tasks",
            "task_list",
            "tasklist",
            "work_items",
            "workitems",
            "items",
            "todo",
            "todos",
            "deliverables",
            "backlog",
            "plan",
        )

        while queue:
            node = queue.pop(0)
            marker = id(node)
            if marker in visited:
                continue
            visited.add(marker)

            if isinstance(node, list):
                dict_items = [item for item in node if isinstance(item, dict)]
                if dict_items:
                    return dict_items
                for item in node:
                    if isinstance(item, (dict, list)):
                        queue.append(item)
                continue

            if not isinstance(node, dict):
                continue

            for key in candidate_keys:
                items = node.get(key)
                if isinstance(items, list):
                    dict_items = [item for item in items if isinstance(item, dict)]
                    if dict_items:
                        return dict_items

            mapped_tasks = [
                value
                for key, value in node.items()
                if isinstance(value, dict)
                and re.match(r"^(?:task[-_ ]*\d+|t[-_ ]*\d+)$", str(key or "").strip(), re.IGNORECASE)
            ]
            if mapped_tasks:
                return mapped_tasks

            for value in node.values():
                if isinstance(value, (dict, list)):
                    queue.append(value)

        return []

    @staticmethod
    def _extract_json_payload(response: str) -> Any:
        text = str(response or "").strip()
        if not text:
            return None
        candidates = [text]
        fenced_blocks = re.findall(
            r"```(?:json|yaml|yml|markdown|md)?\s*(.*?)```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        candidates.extend(item.strip() for item in fenced_blocks if item.strip())
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except (RuntimeError, ValueError):
                decoder = json.JSONDecoder()
                for index, char in enumerate(candidate):
                    if char not in "{[":
                        continue
                    try:
                        parsed, _end = decoder.raw_decode(candidate[index:])
                    except (RuntimeError, ValueError):
                        continue
                    return parsed
                try:
                    parsed = ast.literal_eval(candidate)
                except (RuntimeError, ValueError):
                    parsed = None
                if isinstance(parsed, (dict, list)):
                    return parsed
        return None

    def _extract_tasks_from_sections(self, response: str, *, directive: str) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        active_list_field = ""

        for raw_line in str(response or "").splitlines():
            line = str(raw_line or "").rstrip()
            stripped = line.strip()
            if not stripped:
                continue

            heading_match = _TASK_SECTION_HEADING.match(stripped)
            if heading_match:
                if current:
                    sections.append(current)
                title = str(heading_match.group(2) or "").strip()
                current = {
                    "title": title or f"Task {len(sections) + 1}",
                    "description": "",
                    "steps": [],
                    "acceptance": [],
                    "depends_on": [],
                }
                active_list_field = ""
                continue

            if current is None:
                continue

            key_match = re.match(
                r"^\s*([a-zA-Z_][a-zA-Z0-9_ ]*|目标|描述|范围|步骤|验收标准|验收|依赖|阶段)\s*[:：]\s*(.*)$", stripped
            )
            if key_match:
                key = str(key_match.group(1) or "").strip().lower()
                value = str(key_match.group(2) or "").strip()
                if key in {"title", "任务", "task"}:
                    if value:
                        current["title"] = value
                    active_list_field = ""
                    continue
                if key in {"goal", "目标"}:
                    current["goal"] = value
                    active_list_field = ""
                    continue
                if key in {"description", "描述"}:
                    current["description"] = value
                    active_list_field = ""
                    continue
                if key in {"scope", "范围"}:
                    current["scope"] = value
                    active_list_field = ""
                    continue
                if key in {"phase", "阶段"}:
                    current["phase"] = value
                    active_list_field = ""
                    continue
                if key in {"depends_on", "依赖"}:
                    current["depends_on"] = self._normalize_list(value)
                    active_list_field = ""
                    continue
                if key in {"steps", "步骤", "执行步骤"}:
                    current["steps"] = self._normalize_list(value)
                    active_list_field = "steps"
                    continue
                if key in {"acceptance", "acceptance_criteria", "验收", "验收标准"}:
                    current["acceptance"] = self._normalize_list(value)
                    active_list_field = "acceptance"
                    continue

            bullet_match = re.match(r"^\s*[-*]\s+(.*)$", stripped)
            if bullet_match and active_list_field in {"steps", "acceptance"}:
                item = str(bullet_match.group(1) or "").strip()
                if item:
                    rows = current.get(active_list_field)
                    if not isinstance(rows, list):
                        rows = []
                    rows.append(item)
                    current[active_list_field] = rows
                continue

            if active_list_field in {"steps", "acceptance"}:
                rows = current.get(active_list_field)
                if not isinstance(rows, list):
                    rows = []
                rows.extend(self._normalize_list(stripped))
                current[active_list_field] = [item for item in rows if str(item).strip()]
                continue

            desc = str(current.get("description") or "").strip()
            current["description"] = f"{desc} {stripped}".strip() if desc else stripped

        if current:
            sections.append(current)

        contracts = [
            self._normalize_task_contract(section, idx + 1, directive)
            for idx, section in enumerate(sections)
            if isinstance(section, dict)
        ]
        return [item for item in contracts if item]

    def _extract_tasks_from_bullets(self, response: str, *, directive: str) -> list[dict[str, Any]]:
        contracts: list[dict[str, Any]] = []
        for line in str(response or "").splitlines():
            token = line.strip()
            if not token:
                continue
            match = _TASK_LINE_PREFIX.match(token)
            if not match:
                continue
            payload = token[match.end() :].strip()
            payload = re.sub(r"^\*\*(.*?)\*\*$", r"\1", payload).strip()
            payload = re.sub(r"^`(.*?)`$", r"\1", payload).strip()
            payload = payload.lstrip("- ").strip()
            if not payload:
                continue
            if ":" in payload or "：" in payload:
                title, desc = re.split(r"[:：]", payload, maxsplit=1)
            else:
                title, desc = payload, ""
            contracts.append(
                self._normalize_task_contract(
                    {
                        "title": title.strip(),
                        "description": desc.strip(),
                    },
                    len(contracts) + 1,
                    directive,
                )
            )
        return [item for item in contracts if item]

    def _normalize_task_contract(
        self,
        raw: dict[str, Any],
        index: int,
        directive: str,
    ) -> dict[str, Any]:
        title = str(raw.get("title") or raw.get("subject") or "").strip()
        if not title:
            title = f"Task {index}"
        title_lower = title.lower()
        if not any(marker in title_lower for marker in _ACTION_MARKERS):
            title = f"实现{title}"

        description = str(raw.get("description") or "").strip()
        goal = str(raw.get("goal") or "").strip()
        if not goal:
            goal = description or f"完成任务: {title}"
            if directive:
                goal = f"{goal}；满足需求: {directive[:120]}"

        scope_values = raw.get("scope")
        if not scope_values:
            scope_values = raw.get("scope_paths") or raw.get("target_files")
        scope_items = self._normalize_list(scope_values)
        if not scope_items:
            scope_items = self._infer_scope_from_title(title)
        scope_text = ", ".join(scope_items[:4]) if scope_items else "src/"
        scope_paths = scope_items[:4] if scope_items else ["src/", "tests/"]

        steps = self._normalize_list(raw.get("steps") or raw.get("execution_checklist"))
        if len(steps) < 2:
            steps = [
                f"分析并定位 {title} 所需改动",
                f"实现 {title} 并补充必要测试",
                "运行验证命令并记录结果",
            ]

        acceptance = self._normalize_list(raw.get("acceptance") or raw.get("acceptance_criteria"))
        if len(acceptance) < 2:
            acceptance = [
                "相关测试命令执行通过（如 `pytest`/`npm test`）",
                "功能行为与预期一致并可复现验证",
            ]

        depends_on = self._normalize_list(raw.get("depends_on") or raw.get("dependencies"))
        phase = str(raw.get("phase") or _DEFAULT_PHASE_SEQUENCE[(index - 1) % len(_DEFAULT_PHASE_SEQUENCE)]).strip()
        task_id = str(raw.get("id") or f"TASK-{index}").strip()
        assigned_to = str(raw.get("assigned_to") or "Director").strip() or "Director"

        _raw_meta = raw.get("metadata")
        metadata: dict[str, Any] = dict(_raw_meta) if isinstance(_raw_meta, dict) else {}
        execution_backend = str(raw.get("execution_backend") or metadata.get("execution_backend") or "").strip().lower()
        if execution_backend:
            metadata["execution_backend"] = execution_backend
        _raw_proj = metadata.get("projection")
        projection: dict[str, Any] = dict(_raw_proj) if isinstance(_raw_proj, dict) else {}
        _raw_raw_proj = raw.get("projection")
        raw_projection: dict[str, Any] = dict(_raw_raw_proj) if isinstance(_raw_raw_proj, dict) else {}
        if raw_projection:
            projection.update(raw_projection)
        for source_key, target_key in (
            ("projection_scenario", "scenario_id"),
            ("scenario_id", "scenario_id"),
            ("project_slug", "project_slug"),
            ("experiment_id", "experiment_id"),
            ("projection_experiment_id", "experiment_id"),
            ("projection_requirement", "requirement"),
            ("requirement_delta", "requirement"),
            ("use_pm_llm", "use_pm_llm"),
            ("run_verification", "run_verification"),
            ("overwrite", "overwrite"),
        ):
            value = raw.get(source_key)
            if value is None:
                continue
            token = str(value).strip() if isinstance(value, str) else value
            if token == "":
                continue
            projection[target_key] = value
        if projection:
            metadata["projection"] = projection
        normalized = {
            "id": task_id,
            "title": title,
            "goal": goal,
            "description": description or f"实现 {title}，并满足验收标准。",
            "scope": scope_text,
            "scope_paths": scope_paths,
            "target_files": scope_paths,
            "steps": steps,
            "acceptance": acceptance,
            "acceptance_criteria": acceptance,
            "phase": phase,
            "depends_on": depends_on,
            "assigned_to": assigned_to,
            "execution_checklist": steps,
            "backlog_ref": str(raw.get("backlog_ref") or task_id).strip() or task_id,
            "metadata": metadata,
        }
        return normalized

    @staticmethod
    def _normalize_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
        if isinstance(value, list):
            items = []
            for item in value:
                token = str(item).strip()
                if token:
                    items.append(token)
            return items
        return []

    def _infer_scope_from_title(self, title: str) -> list[str]:
        keyword_tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", title.lower())
        normalized = [token for token in keyword_tokens if token not in _STOPWORDS]
        if not normalized:
            return ["src/", "tests/"]
        first = normalized[0]
        return [f"src/{first}", "tests/"]

    def _derive_domain_token(self, directive: str) -> str:
        workspace_name = Path(self.workspace).resolve().name.lower()
        workspace_tokens = [token.strip() for token in re.split(r"[^a-z0-9]+", workspace_name) if token.strip()]
        for token in workspace_tokens:
            if len(token) < 3:
                continue
            if token in _STOPWORDS:
                continue
            return token

        keyword_match = re.search(
            r"(?:关键词|keywords?)\s*[:：]\s*([^\n]+)",
            str(directive or ""),
            flags=re.IGNORECASE,
        )
        if keyword_match:
            keyword_tokens = re.findall(
                r"[a-z][a-z0-9_-]{2,}",
                str(keyword_match.group(1) or "").lower(),
            )
            for token in keyword_tokens:
                if token in _STOPWORDS:
                    continue
                return token

        text = str(directive or "").lower()
        tokens = re.findall(r"[a-z][a-z0-9_-]{3,}", text)
        for token in tokens:
            if token in _STOPWORDS:
                continue
            return token
        return "project"

    def _extract_domain_keywords(self, directive: str, *, limit: int = 4) -> list[str]:
        text = str(directive or "")
        tokens: list[str] = []

        keyword_hint_pattern = re.compile(r"(?:示例|关键词|keywords?)\s*[:：]\s*([^\n]+)", re.IGNORECASE)
        for match in keyword_hint_pattern.finditer(text):
            chunk = str(match.group(1) or "").lower()
            for token in re.findall(r"[a-z][a-z0-9_-]{2,}", chunk):
                if token in _STOPWORDS or token in tokens:
                    continue
                tokens.append(token)
                if len(tokens) >= limit:
                    return tokens

        for token in re.findall(r"[a-z][a-z0-9_-]{2,}", text.lower()):
            if token in _STOPWORDS or token in tokens:
                continue
            tokens.append(token)
            if len(tokens) >= limit:
                return tokens

        fallback = self._derive_domain_token(directive)
        if fallback and fallback not in tokens:
            tokens.append(fallback)
        return tokens[:limit]

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

        # Inject before the JSON format section
        if '"tasks": [' in message:
            parts = message.split('"tasks": [')
            return parts[0] + '"tasks": [' + "\n".join(meta_hint_lines) + "\n" + parts[1]

        return message

    @staticmethod
    def _normalize_projection_project_slug(value: Any, *, default_value: str = "projection_lab") -> str:
        token = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower())
        token = re.sub(r"_+", "_", token).strip("_")
        return token or str(default_value or "projection_lab").strip()

    def _extract_projection_contract_hint(
        self,
        *,
        input_data: dict[str, Any],
        context: dict[str, Any],
        directive: str,
    ) -> dict[str, Any]:
        _raw_input_meta = input_data.get("metadata") if isinstance(input_data, dict) else None
        input_metadata: dict[str, Any] = dict(_raw_input_meta) if isinstance(_raw_input_meta, dict) else {}
        _raw_ctx_meta = context.get("metadata") if isinstance(context, dict) else None
        context_metadata: dict[str, Any] = dict(_raw_ctx_meta) if isinstance(_raw_ctx_meta, dict) else {}

        execution_backend = (
            str(
                input_data.get("execution_backend")
                or input_metadata.get("execution_backend")
                or context.get("execution_backend")
                or context_metadata.get("execution_backend")
                or ""
            )
            .strip()
            .lower()
        )
        if execution_backend != "projection_generate":
            return {}

        projection: dict[str, Any] = {}
        for payload in (
            context_metadata.get("projection"),
            context.get("projection"),
            input_metadata.get("projection"),
            input_data.get("projection"),
        ):
            if isinstance(payload, dict):
                projection.update(payload)

        for source in (input_data, input_metadata, context, context_metadata):
            if not isinstance(source, dict):
                continue
            mapping = (
                ("projection_scenario", "scenario_id"),
                ("scenario_id", "scenario_id"),
                ("project_slug", "project_slug"),
                ("projection_requirement", "requirement"),
                ("requirement_delta", "requirement"),
                ("use_pm_llm", "use_pm_llm"),
                ("run_verification", "run_verification"),
                ("overwrite", "overwrite"),
            )
            for source_key, target_key in mapping:
                value = source.get(source_key)
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                projection[target_key] = value

        scenario_id = str(projection.get("scenario_id") or "").strip()
        if not scenario_id:
            return {}

        projection["scenario_id"] = scenario_id
        projection["project_slug"] = self._normalize_projection_project_slug(
            projection.get("project_slug"),
        )
        projection["requirement"] = str(projection.get("requirement") or directive or "").strip()
        projection["use_pm_llm"] = bool(projection.get("use_pm_llm", True))
        projection["run_verification"] = bool(projection.get("run_verification", True))
        projection["overwrite"] = bool(projection.get("overwrite", False))

        return {
            "execution_backend": execution_backend,
            "projection": projection,
        }

    def _apply_projection_contract_hint(
        self,
        contracts: list[dict[str, Any]],
        *,
        projection_hint: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if (
            not projection_hint
            or str(projection_hint.get("execution_backend") or "").strip().lower() != "projection_generate"
        ):
            return contracts

        _raw_proj = projection_hint.get("projection") if isinstance(projection_hint, dict) else None
        projection: dict[str, Any] = dict(_raw_proj) if isinstance(_raw_proj, dict) else {}
        has_projection_generate = any(
            str(item.get("execution_backend") or "").strip().lower() == "projection_generate"
            or (
                isinstance(item.get("metadata"), dict)
                and str(item["metadata"].get("execution_backend") or "").strip().lower() == "projection_generate"
            )
            for item in contracts
            if isinstance(item, dict)
        )
        normalized_contracts: list[dict[str, Any]] = []

        for index, contract in enumerate(contracts):
            if not isinstance(contract, dict):
                continue
            enriched = dict(contract)
            _raw_enr_meta = enriched.get("metadata")
            metadata: dict[str, Any] = dict(_raw_enr_meta) if isinstance(_raw_enr_meta, dict) else {}
            _raw_proj = metadata.get("projection")
            projection_payload: dict[str, Any] = dict(_raw_proj) if isinstance(_raw_proj, dict) else {}
            if isinstance(enriched.get("projection"), dict):
                projection_payload.update(enriched.get("projection") or {})

            execution_backend = (
                str(enriched.get("execution_backend") or metadata.get("execution_backend") or "").strip().lower()
            )
            if index == 0 and not has_projection_generate:
                execution_backend = "projection_generate"
            if execution_backend == "projection_generate":
                projection_payload.update(projection)
                metadata["projection"] = projection_payload
                metadata["execution_backend"] = "projection_generate"
                enriched["execution_backend"] = "projection_generate"
            elif not execution_backend:
                metadata["execution_backend"] = "code_edit"
                enriched["execution_backend"] = "code_edit"

            enriched["metadata"] = metadata
            normalized_contracts.append(enriched)

        return normalized_contracts

    def _build_projection_hint_contracts(
        self,
        *,
        directive: str,
        projection_hint: dict[str, Any],
    ) -> list[dict[str, Any]]:
        _raw_proj = projection_hint.get("projection") if isinstance(projection_hint, dict) else None
        projection: dict[str, Any] = dict(_raw_proj) if isinstance(_raw_proj, dict) else {}
        scenario_id = str(projection.get("scenario_id") or "").strip() or "registry.scenario"
        project_slug = self._normalize_projection_project_slug(projection.get("project_slug"))
        requirement = str(projection.get("requirement") or directive or "").strip()
        project_root = f"experiments/{project_slug}"

        return [
            {
                "id": "TASK-1",
                "title": "通过 Projection 生成受控基线子项目",
                "goal": "使用显式 projection_generate 后端生成传统代码基线并产出审计资产",
                "description": "基于上游给定的 projection 契约生成基线项目，不在 Polaris 主仓内内置任何业务模板名称。",
                "scope": [project_root, "workspace/factory/projection_lab"],
                "steps": [
                    "校验 projection 契约参数并归一化需求",
                    "执行 projection_generate 生成传统项目与隐藏 IR 资产",
                    "记录 experiment_id / project_root / artifact 路径并运行基础验证",
                ],
                "acceptance": [
                    "生成结果包含 experiment_id、project_root 与 artifact_paths",
                    "投影后的传统项目可运行基础验证命令且无空壳产物",
                ],
                "phase": "implementation",
                "depends_on": [],
                "assigned_to": "Director",
                "execution_backend": "projection_generate",
                "projection": {
                    "scenario_id": scenario_id,
                    "project_slug": project_slug,
                    "requirement": requirement,
                    "use_pm_llm": bool(projection.get("use_pm_llm", True)),
                    "run_verification": bool(projection.get("run_verification", True)),
                    "overwrite": bool(projection.get("overwrite", False)),
                },
            },
            {
                "id": "TASK-2",
                "title": "收敛生成结果与工程约束",
                "goal": "检查投影结果是否满足当前工作区工程约束并补齐缺口",
                "description": "在已生成基线之上做必要的传统代码收敛，避免生成结果与仓库约束脱节。",
                "scope": [project_root, "tests/"],
                "steps": [
                    "检查生成目录、配置与测试布局是否符合当前工程约束",
                    "对生成结果进行必要的代码编辑或补强",
                    "保留审计证据并记录需要 QA 关注的风险点",
                ],
                "acceptance": [
                    "关键目录结构、配置文件与测试入口满足工程约束",
                    "新增修改具有明确验证路径且无 TODO/FIXME/stub 残留",
                ],
                "phase": "implementation",
                "depends_on": ["TASK-1"],
                "assigned_to": "Director",
                "execution_backend": "code_edit",
            },
            {
                "id": "TASK-3",
                "title": "固化验证与交付说明",
                "goal": "为投影结果固化回归验证、交付说明与后续操作边界",
                "description": "补齐最终验证步骤、交付说明和已知风险记录，确保 QA 可以基于证据做最终裁决。",
                "scope": [project_root, "tui_runtime.md", "tests/"],
                "steps": [
                    "整理可复现的验证命令与预期结果",
                    "补充必要测试或交付说明",
                    "记录当前投影结果的边界、风险与后续扩展点",
                ],
                "acceptance": [
                    "验证步骤可被 QA 独立复现且结果明确",
                    "交付说明包含运行方式、验证命令与当前已知限制",
                ],
                "phase": "verification",
                "depends_on": ["TASK-2"],
                "assigned_to": "Director",
                "execution_backend": "code_edit",
            },
        ]

    def _synthesize_task_contracts_from_directive(
        self,
        *,
        directive: str,
        projection_hint: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """在 PM 输出不可解析时，基于需求指令合成最小可执行任务合同。"""
        if projection_hint:
            contracts = self._build_projection_hint_contracts(
                directive=directive,
                projection_hint=projection_hint,
            )
            normalized = [self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(contracts)]
            return [item for item in normalized if isinstance(item, dict)]

        keywords = self._extract_domain_keywords(directive, limit=4)
        domain = keywords[0] if keywords else self._derive_domain_token(directive)
        secondary = keywords[1] if len(keywords) > 1 else f"{domain}_feature"

        raw_contracts: list[dict[str, Any]] = [
            {
                "id": "TASK-1",
                "title": f"实现 {domain} 核心业务模块",
                "goal": f"完成 {domain} 领域核心功能落地，形成可执行主流程",
                "description": "建立核心数据结构、领域服务与入口调用链，确保关键场景可运行。",
                "scope": [f"src/{domain}", f"src/{domain}_core", "tests/"],
                "steps": [
                    f"梳理并实现 {domain} 核心数据模型与服务接口",
                    "补齐主流程入口与基础错误处理",
                    "为核心流程增加最小可运行验证用例",
                ],
                "acceptance": [
                    f"执行 `pytest -q` 或 `npm test` 时，{domain} 核心模块测试通过",
                    f"运行主流程后可看到 {domain} 关键业务输出，无 TODO/FIXME/stub",
                ],
                "phase": "requirements",
                "depends_on": [],
                "assigned_to": "Director",
            },
            {
                "id": "TASK-2",
                "title": f"实现 {secondary} 增强能力与集成链路",
                "goal": f"补齐 {secondary} 相关增强特性并接入主流程",
                "description": "实现增强功能、状态同步与异常回退路径，确保与核心模块联动。",
                "scope": [f"src/{domain}_feature", f"src/{secondary}", "tests/integration/"],
                "steps": [
                    f"实现 {secondary} 增强逻辑并与核心模块集成",
                    "补齐失败重试、异常处理与边界校验",
                    "增加集成测试覆盖主流程与异常分支",
                ],
                "acceptance": [
                    "执行 `pytest -q` 或 `npm test` 时，集成测试覆盖核心链路并通过",
                    "异常输入触发回退逻辑后，系统返回可预期错误结果并记录日志",
                ],
                "phase": "implementation",
                "depends_on": ["TASK-1"],
                "assigned_to": "Director",
            },
            {
                "id": "TASK-3",
                "title": f"编写 {domain} 验收测试与交付校验脚本",
                "goal": f"固化 {domain} 交付基线，确保回归可复现",
                "description": "补齐单元/集成验证与质量检查脚本，形成可重复验收证据。",
                "scope": [f"tests/{domain}", "scripts/", "tui_runtime.md"],
                "steps": [
                    "补充关键路径单元测试与回归测试",
                    "编写或更新质量检查脚本与执行说明",
                    "运行验证命令并记录结果到项目文档",
                ],
                "acceptance": [
                    "执行 `pytest -q`、`npm test` 或等价测试命令后返回 PASS",
                    "交付物包含可复现的验证步骤与命令输出说明",
                ],
                "phase": "verification",
                "depends_on": ["TASK-2"],
                "assigned_to": "Director",
            },
        ]

        contracts = [self._normalize_task_contract(item, idx + 1, directive) for idx, item in enumerate(raw_contracts)]
        return [item for item in contracts if isinstance(item, dict)]

    def _evaluate_contract_quality(
        self,
        contracts: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payload = {"tasks": [dict(item) for item in contracts if isinstance(item, dict)]}
        autofix_pm_contract_for_quality(
            payload,
            workspace_full=str(Path(self.workspace).resolve()),
        )
        quality = evaluate_pm_task_quality(payload, docs_stage={})
        _raw_tasks = payload.get("tasks") if isinstance(payload, dict) else None
        tasks: list[dict[str, Any]] = _raw_tasks if isinstance(_raw_tasks, list) else []
        normalized = [item for item in tasks if isinstance(item, dict)]
        return normalized, quality

    def _validate_task_contracts(self, task_contracts: list[dict[str, Any]]) -> ValidationResult:
        """Validate task contract dependencies using StructuralPlanValidator.

        Args:
            task_contracts: List of task contract dictionaries

        Returns:
            ValidationResult with is_valid and any violations found
        """
        if not task_contracts:
            return ValidationResult(
                is_valid=False,
                violations=(),
                suggestions=("At least one task is required",),
            )

        # Build PlanStep objects from task contracts
        plan_steps: list[PlanStep] = []
        for contract in task_contracts:
            task_id = str(contract.get("id") or contract.get("title") or "unknown").strip()
            depends_on = contract.get("depends_on") or []
            if isinstance(depends_on, str):
                depends_on = [d.strip() for d in depends_on.split(",") if d.strip()]
            plan_steps.append(
                PlanStep(
                    id=task_id,
                    description=str(contract.get("description") or contract.get("title") or ""),
                    depends_on=tuple(depends_on),
                    estimated_duration=None,
                    metadata={},
                )
            )

        # Build Plan and validate
        plan = Plan(
            steps=tuple(plan_steps),
            max_duration=None,
            metadata={},
        )

        validator = StructuralPlanValidator()
        return validator.validate(plan)

    def _create_board_tasks(self, task_contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        by_id: dict[str, int] = {}
        board_task_ids_by_contract_index: list[int] = []
        created_task_ids: set[int] = set()
        self._deduplicate_existing_board_tasks()
        existing_tasks = self.task_board.list_all()
        signature_index: dict[str, list[dict[str, Any]]] = {}
        title_index: dict[str, list[dict[str, Any]]] = {}

        # Validate task contract dependencies
        validation_result = self._validate_task_contracts(task_contracts)
        validation_metadata: dict[str, Any] = {
            "plan_validation_is_valid": validation_result.is_valid,
            "plan_validation_errors": [
                {"rule_id": v.rule_id, "message": v.message, "location": v.location}
                for v in validation_result.violations
                if v.severity.name == "ERROR"
            ],
            "plan_validation_warnings": [
                {"rule_id": v.rule_id, "message": v.message, "location": v.location}
                for v in validation_result.violations
                if v.severity.name == "WARNING"
            ],
            "plan_validation_suggestions": list(validation_result.suggestions),
        }

        def _index_task(task_row: dict[str, Any]) -> None:
            title = str(task_row.get("subject") or "").strip()
            _raw_meta = task_row.get("metadata")
            metadata: dict[str, Any] = _raw_meta if isinstance(_raw_meta, dict) else {}
            goal = str(metadata.get("goal") or "").strip()
            signature = self._build_task_identity_signature(title=title, goal=goal)
            title_key = self._canonical_text(title)
            if signature:
                signature_index.setdefault(signature, []).append(task_row)
            if title_key:
                title_index.setdefault(title_key, []).append(task_row)

        for existing in existing_tasks:
            _index_task(existing.to_dict())

        for contract in task_contracts:
            _raw_contract_meta = contract.get("metadata")
            contract_metadata: dict[str, Any] = dict(_raw_contract_meta) if isinstance(_raw_contract_meta, dict) else {}
            metadata = {
                "goal": contract.get("goal"),
                "scope": contract.get("scope"),
                "steps": contract.get("steps"),
                "acceptance": contract.get("acceptance"),
                "phase": contract.get("phase"),
                "depends_on_external": contract.get("depends_on"),
                "assigned_to": contract.get("assigned_to"),
                "backlog_ref": contract.get("backlog_ref"),
                "quality_source": "pm_adapter_v2",
            }
            # Merge validation metadata (contract_metadata takes precedence)
            metadata = {**validation_metadata, **contract_metadata, **metadata}
            subject = str(contract.get("title") or "").strip() or "Untitled task"
            description = str(contract.get("description") or "").strip()
            matched_id = self._find_existing_task_match(
                subject=subject,
                goal=str(contract.get("goal") or "").strip(),
                signature_index=signature_index,
                title_index=title_index,
            )
            if matched_id is not None and self._board_task_exists(matched_id):
                merged_metadata = dict(metadata)
                merged_metadata["pm_deduplicated"] = True
                merged_metadata["pm_last_contract_subject"] = subject
                existing_task = self.task_board.update(matched_id, metadata=merged_metadata)
                task = existing_task or self.task_board.get(matched_id)
                if task is None:
                    task = self.task_board.create(
                        subject=subject,
                        description=description,
                        metadata=metadata,
                    )
            else:
                task = self.task_board.create(
                    subject=subject,
                    description=description,
                    metadata=metadata,
                )
                _index_task(task.to_dict())

            board_task_ids_by_contract_index.append(int(task.id))
            token = str(contract.get("id") or "").strip()
            if token:
                by_id[token] = int(task.id)
            if int(task.id) not in created_task_ids:
                created_task_ids.add(int(task.id))
                created.append(task.to_dict())

        for idx, contract in enumerate(task_contracts):
            dependencies = contract.get("depends_on")
            dep_ids = dependencies if isinstance(dependencies, list) else []
            if not dep_ids:
                continue
            board_task_id = board_task_ids_by_contract_index[idx] if idx < len(board_task_ids_by_contract_index) else 0
            blocked_by: list[int] = []
            for dep in dep_ids:
                mapped = by_id.get(str(dep).strip())
                if mapped is not None and mapped != board_task_id:
                    blocked_by.append(mapped)
            if blocked_by and self._board_task_exists(board_task_id):
                self.task_board.update(
                    board_task_id,
                    metadata={"resolved_depends_on_task_ids": blocked_by},
                )
                refreshed = self.task_board.get(board_task_id)
                if refreshed is not None:
                    refreshed_row = refreshed.to_dict()
                    for position, row in enumerate(created):
                        if int(row.get("id") or 0) == board_task_id:
                            created[position] = refreshed_row
                            break

        return created

    def _deduplicate_existing_board_tasks(self) -> None:
        tasks = [task.to_dict() for task in self.task_board.list_all()]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in tasks:
            subject_key = self._canonical_text(str(row.get("subject") or ""))
            if not subject_key:
                continue
            grouped.setdefault(subject_key, []).append(row)

        for _, rows in grouped.items():
            if len(rows) <= 1:
                continue
            primary_id = self._pick_preferred_task_id(rows)
            if primary_id is None:
                continue
            for row in rows:
                task_id = int(row.get("id") or 0)
                if task_id <= 0 or task_id == primary_id:
                    continue
                status = str(row.get("status") or "").strip().lower()
                if status not in {"pending", "blocked", "in_progress", "failed"}:
                    continue
                self.task_board.update(
                    task_id,
                    status="cancelled",
                    metadata={
                        "dedup_merged_into": primary_id,
                        "dedup_reason": "pm_duplicate_subject",
                        "dedup_source": "pm_adapter",
                    },
                )

    @staticmethod
    def _canonical_text(value: str) -> str:
        token = str(value or "").strip().lower()
        if not token:
            return ""
        # 保留中英文和数字，移除符号噪声。
        normalized = "".join(ch for ch in token if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff"))
        return normalized

    def _build_task_identity_signature(self, *, title: str, goal: str) -> str:
        left = self._canonical_text(title)
        right = self._canonical_text(goal)
        if left and right:
            return f"{left}::{right}"
        if left:
            return left
        if right:
            return right
        return ""

    @staticmethod
    def _pick_preferred_task_id(candidates: list[dict[str, Any]]) -> int | None:
        if not candidates:
            return None

        def _status_rank(row: dict[str, Any]) -> int:
            status = str(row.get("status") or "").strip().lower()
            if status == "in_progress":
                return 0
            if status in {"pending", "blocked"}:
                return 1
            if status == "completed":
                return 2
            if status in {"failed", "cancelled"}:
                return 3
            return 4

        ordered = sorted(
            candidates,
            key=lambda row: (_status_rank(row), -int(row.get("id") or 0)),
        )
        best = ordered[0] if ordered else None
        if not isinstance(best, dict):
            return None
        try:
            return int(best.get("id") or 0)
        except (RuntimeError, ValueError):
            return None

    def _find_existing_task_match(
        self,
        *,
        subject: str,
        goal: str,
        signature_index: dict[str, list[dict[str, Any]]],
        title_index: dict[str, list[dict[str, Any]]],
    ) -> int | None:
        signature = self._build_task_identity_signature(title=subject, goal=goal)
        if signature and signature in signature_index:
            matched = self._pick_preferred_task_id(signature_index[signature])
            if matched:
                return matched

        title_key = self._canonical_text(subject)
        if title_key and title_key in title_index:
            matched = self._pick_preferred_task_id(title_index[title_key])
            if matched:
                return matched

        if not title_key:
            return None

        # 高阈值模糊匹配：仅在标题极相近时复用，避免误并不同任务。
        best_id: int | None = None
        best_ratio = 0.0
        for indexed_title, rows in title_index.items():
            if not indexed_title:
                continue
            ratio = SequenceMatcher(None, title_key, indexed_title).ratio()
            if ratio < 0.93 or ratio < best_ratio:
                continue
            candidate_id = self._pick_preferred_task_id(rows)
            if candidate_id:
                best_ratio = ratio
                best_id = candidate_id

        return best_id

    def _write_plan_artifact(
        self,
        *,
        directive: str,
        task_contracts: list[dict[str, Any]],
        quality: dict[str, Any],
        quality_signals: list[dict[str, Any]] | None = None,
    ) -> Path:
        tasks_dir = Path(resolve_runtime_path(self.workspace, "runtime/tasks"))
        tasks_dir.mkdir(parents=True, exist_ok=True)
        plan_path = tasks_dir / "plan.json"
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "pm_adapter_v2",
            "directive": directive,
            "quality_gate": {
                "score": int(quality.get("score") or 0),
                "critical_issue_count": (
                    len(cast("list", quality.get("critical_issues")))
                    if isinstance(quality, dict) and isinstance(quality.get("critical_issues"), list)
                    else 0
                ),
                "summary": str(quality.get("summary") or "").strip(),
                "signals": list(quality_signals or []),
            },
            "tasks": task_contracts,
        }
        write_text_atomic(
            str(plan_path),
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return plan_path

    def _parse_and_create_tasks(self, response: str) -> list[dict[str, Any]]:
        """兼容旧接口: 从文本中解析并创建任务."""
        contracts = self._extract_task_contracts(response, directive="")
        normalized, _quality = self._evaluate_contract_quality(contracts)
        return self._create_board_tasks(normalized)
