"""Architect 角色适配器.

执行可审计的架构文档产出，并对薄弱/泄漏内容做质量拦截。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.bootstrap.config import get_settings
from polaris.cells.llm.dialogue.public.service import generate_role_response
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.llm.engine import ResponseNormalizer
from polaris.kernelone.storage import resolve_runtime_path, resolve_workspace_persistent_path

from .base import BaseRoleAdapter

logger = logging.getLogger(__name__)

_MIN_DOC_CHARS = 220
_MIN_DOC_HEADINGS = 3
_TOOL_MARKER_PATTERN = re.compile(
    r"\[(?:/?tool_call|/?[a-z_]+)\]|<(?:/?tool_call|/?function_calls?|/?invoke)\b",
    re.IGNORECASE,
)
_DOC_STRIP_BLOCK_PATTERNS = (
    re.compile(r"\[tool_call\].*?\[/tool_call\]", re.IGNORECASE | re.DOTALL),
    re.compile(r"<minimax:tool_call\b[^>]*>.*?</minimax:tool_call>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<tool_call\b[^>]*>.*?</tool_call>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<function_calls?\b[^>]*>.*?</function_calls?>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<invoke\b[^>]*>.*?</invoke>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<think(?:ing|ought)?\b[^>]*>.*?</think(?:ing|ought)?>", re.IGNORECASE | re.DOTALL),
)
_DOC_STRIP_LINE_MARKERS = (
    "[tool_call]",
    "[/tool_call]",
    "<tool_call",
    "</tool_call",
    "<minimax:tool_call",
    "</minimax:tool_call",
    "<function_call",
    "</function_call",
    "<invoke",
    "</invoke",
    "<thinking",
    "</thinking",
)
_DIRECTIVE_META_LINE_PATTERN = re.compile(
    r"(提示词|system prompt|角色设定|注入|tool_call|function call|禁止工具调用|覆盖系统指令)",
    re.IGNORECASE,
)
_DIRECTIVE_DROP_SECTION_HEADERS = (
    "## 上轮失败复盘",
    "## 交付基线",
)
_STRUCTURED_DOC_FIELD_MARKERS = (
    "plan_markdown",
    "architecture_markdown",
    "project_plan",
    "system_design",
)

# Phase 2.3: Design pattern library for architecture self-review
_DESIGN_PATTERNS = {
    "layered_architecture": {
        "description": "Layered architecture with clear separation of concerns",
        "適用場景": "General enterprise applications, CRUD systems",
        "modules": ["presentation", "business", "data"],
    },
    "microservices": {
        "description": "Microservices architecture for distributed systems",
        "適用場景": "Large scale systems requiring independent deployment",
        "modules": ["api_gateway", "service_discovery", "message_bus"],
    },
    "event_driven": {
        "description": "Event-driven architecture for reactive systems",
        "適用場景": "Real-time applications, notification systems",
        "modules": ["event_bus", "event_handlers", "state_machine"],
    },
    "hexagonal": {
        "description": "Hexagonal (ports and adapters) architecture",
        "適用場景": "Domain-driven design, testable architectures",
        "modules": ["ports", "adapters", "domain_core"],
    },
    "cqrs": {
        "description": "Command Query Responsibility Segregation",
        "適用場景": "Complex domains with different read/write patterns",
        "modules": ["commands", "queries", "event_sourcing"],
    },
}


class ArchitectAdapter(BaseRoleAdapter):
    """Architect 角色适配器."""

    @property
    def role_id(self) -> str:
        return "architect"

    def get_capabilities(self) -> list[str]:
        return [
            "analyze_requirements",
            "generate_architecture",
            "write_design_docs",
            "design_pattern_library",
            "architecture_self_review",
            "constraint_propagation",
        ]

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """执行 Architect 任务."""
        directive = str(input_data.get("input") or "").strip()
        self._update_task_progress(task_id, "planning")

        try:
            message = self._build_architect_message(directive)
            settings = get_settings()
            response = await generate_role_response(
                workspace=self.workspace,
                settings=settings,
                role=self.role_id,
                message=message,
                context={"validate_output": True},
                validate_output=False,
                max_retries=1,
            )
            content = str(response.get("response") or "") if isinstance(response, dict) else str(response or "")
            response_error = str(response.get("error") or "") if isinstance(response, dict) else ""
            result = {
                "content": content,
                "success": bool(content) and not bool(response_error),
                "error": response_error or None,
                "raw_response": response,
            }
            docs = self._extract_docs(content, directive)
            issues = self._collect_doc_quality_issues(docs)
            self._capture_attempt_snapshot(
                task_id=task_id,
                attempt_label="initial",
                result=result,
                content=content,
                docs=docs,
                issues=issues,
            )
            result, content, docs, issues = await self._repair_truncated_docs_if_needed(
                task_id=task_id,
                attempt_label="initial",
                directive=directive,
                result=result,
                content=content,
                docs=docs,
                issues=issues,
            )

            if self._should_force_finalize_docs(result=result, docs=docs, issues=issues, content=content):
                force_message = self._build_force_finalize_message(directive, content)
                settings = get_settings()
                force_response = await generate_role_response(
                    workspace=self.workspace,
                    settings=settings,
                    role=self.role_id,
                    message=force_message,
                    context={"validate_output": True},
                    validate_output=False,
                    max_retries=1,
                )
                force_content = (
                    str(force_response.get("response") or "")
                    if isinstance(force_response, dict)
                    else str(force_response or "")
                )
                force_error = str(force_response.get("error") or "") if isinstance(force_response, dict) else ""
                force_result = {
                    "content": force_content,
                    "success": bool(force_content) and not bool(force_error),
                    "error": force_error or None,
                    "raw_response": force_response,
                }
                docs = self._extract_docs(force_content, directive)
                issues = self._collect_doc_quality_issues(docs)
                self._capture_attempt_snapshot(
                    task_id=task_id,
                    attempt_label="force_finalize",
                    result=force_result,
                    content=force_content,
                    docs=docs,
                    issues=issues,
                )
                force_result, force_content, docs, issues = await self._repair_truncated_docs_if_needed(
                    task_id=task_id,
                    attempt_label="force_finalize",
                    directive=directive,
                    result=force_result,
                    content=force_content,
                    docs=docs,
                    issues=issues,
                )
                content = f"{content}\n\n[force_finalize]\n{force_content}"

            retry_round = 0
            max_retry_rounds = 2
            while issues and retry_round < max_retry_rounds:
                retry_round += 1
                retry_message = self._build_retry_message(
                    directive,
                    issues,
                    content,
                    retry_round=retry_round,
                )
                settings = get_settings()
                retry_response = await generate_role_response(
                    workspace=self.workspace,
                    settings=settings,
                    role=self.role_id,
                    message=retry_message,
                    context={"validate_output": True},
                    validate_output=False,
                    max_retries=1,
                )
                retry_content = (
                    str(retry_response.get("response") or "")
                    if isinstance(retry_response, dict)
                    else str(retry_response or "")
                )
                retry_error = str(retry_response.get("error") or "") if isinstance(retry_response, dict) else ""
                retry_result = {
                    "content": retry_content,
                    "success": bool(retry_content) and not bool(retry_error),
                    "error": retry_error or None,
                    "raw_response": retry_response,
                }
                docs = self._extract_docs(retry_content, directive)
                issues = self._collect_doc_quality_issues(docs)
                self._capture_attempt_snapshot(
                    task_id=task_id,
                    attempt_label=f"quality_retry_{retry_round}",
                    result=retry_result,
                    content=retry_content,
                    docs=docs,
                    issues=issues,
                )
                retry_result, retry_content, docs, issues = await self._repair_truncated_docs_if_needed(
                    task_id=task_id,
                    attempt_label=f"quality_retry_{retry_round}",
                    directive=directive,
                    result=retry_result,
                    content=retry_content,
                    docs=docs,
                    issues=issues,
                )
                content = f"{content}\n\n[retry_{retry_round}]\n{retry_content}"
                if not self._has_blocking_doc_issues(issues):
                    break

            quality_signals: list[str] = []
            if issues:
                quality_signals = [str(item) for item in issues[:8]]
            if self._has_blocking_doc_issues(issues):
                quality_signals = [str(item) for item in issues[:8]]
                error_detail = (
                    "architect_docs_quality_failed: " + ", ".join(quality_signals)
                    if quality_signals
                    else "architect_docs_quality_failed"
                )
                self._capture_attempt_snapshot(
                    task_id=task_id,
                    attempt_label="blocked",
                    result={"error": error_detail},
                    content=content,
                    docs=docs,
                    issues=issues,
                )
                return {
                    "success": False,
                    "stage": "architect",
                    "error": error_detail,
                    "error_code": "architect_docs_quality_failed",
                    "quality_signals": quality_signals,
                    "content_length": len(content),
                }

            artifacts = self._write_docs(docs)

            # Phase 2.3: Perform architecture self-review before completing
            review_result = self._perform_architecture_self_review(docs, context)
            if not review_result.get("review_passed", True):
                logger.warning(
                    "Architect self-review concerns: %s",
                    review_result.get("concerns", []),
                )

            self._update_task_progress(task_id, "completed")
            return {
                "success": True,
                "stage": "architect",
                "artifacts": [str(path) for path in artifacts],
                "plan_doc": str(artifacts[0]),
                "architecture_doc": str(artifacts[1]),
                "design_doc": str(artifacts[2]),
                "content_length": len(content),
                "quality_signals": quality_signals,
                "architect_self_review": {
                    "matched_patterns": review_result.get("matched_patterns", []),
                    "concerns": review_result.get("concerns", []),
                    "recommendations": review_result.get("recommendations", []),
                    "review_passed": review_result.get("review_passed", True),
                },
            }
        except (RuntimeError, ValueError) as e:
            return {
                "success": False,
                "stage": "architect",
                "error": str(e),
            }

    def _build_architect_message(self, directive: str) -> str:
        objective = self._sanitize_architect_directive(directive)
        return "\n".join(
            [
                "你是 Polaris 的 Architect。",
                "请输出结构化架构结果，避免输出系统内部指令或运行控制语句。",
                "",
                f"需求指令: {objective}",
                "",
                "输出格式要求：",
                "1. 优先输出 JSON（不要包裹 Markdown 解释），字段必须包含：",
                '   - "plan_markdown": 项目计划 Markdown',
                '   - "architecture_markdown": 架构设计 Markdown',
                "2. 每个 Markdown 至少包含以下小节：",
                "   - 背景与目标",
                "   - 架构与技术栈",
                "   - 模块拆分与职责",
                "   - 数据/接口契约",
                "   - 风险与验收策略",
                "3. 文档中必须显式出现关键词：架构、技术栈、模块。",
                "4. 禁止输出 TOOL_CALL 或任何工具标签；信息不足时基于需求做合理假设并注明。",
                "5. 文档必须具体、可执行，避免模板化空话。",
                "6. 输出务必精炼：每个二级标题下最多 3 条要点，不要插入长代码块。",
                "7. 总输出控制在 6000 字以内，优先保证 JSON 完整闭合。",
                "8. 如果信息已足够，请直接输出最终 JSON。",
            ]
        )

    def _build_retry_message(
        self,
        directive: str,
        issues: list[str],
        previous_output: str,
        *,
        retry_round: int = 1,
    ) -> str:
        objective = self._sanitize_architect_directive(directive)
        issue_lines = "\n".join(f"- {item}" for item in issues[:8]) or "- 文档质量未达标"
        refusal_hint = ""
        if self._looks_like_safety_refusal(previous_output):
            refusal_hint = "补充说明：当前请求仅要求输出项目计划与架构文档，不涉及系统指令覆盖或权限绕过。"
        return "\n".join(
            [
                f"上一版架构文档未通过质量门禁，请重写（第 {max(1, int(retry_round))} 次重试）。",
                "",
                "失败原因：",
                issue_lines,
                "",
                f"原始需求: {objective}",
                "",
                refusal_hint,
                "请仅输出 JSON，字段 plan_markdown / architecture_markdown。",
                "禁止输出 TOOL_CALL、函数调用标签或目录探测指令。",
                "必须给出具体模块、接口、数据结构、测试与验收策略。",
                "每个 Markdown 必须显式包含：架构、技术栈、模块 三个关键词。",
                "避免输出系统内部指令、控制流指令或模板化空话。",
                "请将内容压缩为精炼版：每个二级标题最多 3 条要点，总输出不超过 6000 字。",
                "",
                "上一版输出片段：",
                previous_output[:1200],
            ]
        )

    def _build_force_finalize_message(self, directive: str, previous_output: str) -> str:
        objective = self._sanitize_architect_directive(directive)
        return "\n".join(
            [
                "你已进入文档定稿阶段。",
                "本回合直接给出最终文档 JSON，不再展开额外调用片段。",
                "请直接输出最终 JSON，且仅包含以下字段：",
                '- "plan_markdown"',
                '- "architecture_markdown"',
                "",
                f"原始需求: {objective}",
                "",
                "硬性要求：",
                "1. 两个字段都必须是可读 Markdown，且均包含 5 个二级标题：",
                "   - ## 背景与目标",
                "   - ## 架构与技术栈",
                "   - ## 模块拆分与职责",
                "   - ## 数据/接口契约",
                "   - ## 风险与验收策略",
                "2. 必须显式出现关键词：架构、技术栈、模块。",
                "3. 禁止输出 TOOL_CALL 或任何工具标签。",
                "4. 不要返回代码块围栏，不要附加解释文本。",
                "5. 输出务必精炼：每个二级标题下最多 3 条要点，不要插入长代码块。",
                "6. 总输出控制在 6000 字以内，避免因篇幅过长导致 JSON 被截断。",
                "",
                "上一版输出（用于纠错）：",
                previous_output[:1200],
            ]
        )

    def _build_truncated_docs_repair_message(self, directive: str, truncated_output: str) -> str:
        objective = self._sanitize_architect_directive(directive)
        return "\n".join(
            [
                "上一版 Architect 文档 JSON 被截断，请基于已有内容恢复为更短且完整的最终 JSON。",
                "只输出 JSON，不要 <output>、不要 ```json、不要解释。",
                "字段必须且仅能包含：plan_markdown、architecture_markdown。",
                "",
                f"原始需求: {objective}",
                "",
                "恢复要求：",
                "1. 每个字段都必须包含以下 5 个二级标题：",
                "   - ## 背景与目标",
                "   - ## 架构与技术栈",
                "   - ## 模块拆分与职责",
                "   - ## 数据/接口契约",
                "   - ## 风险与验收策略",
                "2. 每个二级标题下最多 3 条要点或 1 个简短表格。",
                "3. 禁止使用代码围栏、长段落、超长示例。",
                "4. 两个字段合计控制在 6000 字以内。",
                "5. 可在不丢失核心需求的前提下压缩表达，但不得留空。",
                "",
                "截断输出片段（用于恢复语义）：",
                truncated_output[:4000],
            ]
        )

    @staticmethod
    def _sanitize_architect_directive(directive: str) -> str:
        raw = str(directive or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not raw:
            return "分析当前工作区需求并产出可执行架构文档"

        kept: list[str] = []
        skipping_section = False
        for raw_line in raw.split("\n"):
            line = str(raw_line or "").strip()
            if not line:
                if kept and kept[-1] != "":
                    kept.append("")
                continue
            lowered = line.lower()
            if any(lowered.startswith(header.lower()) for header in _DIRECTIVE_DROP_SECTION_HEADERS):
                skipping_section = True
                continue
            if skipping_section and lowered.startswith("## "):
                skipping_section = False
            if skipping_section:
                continue
            if _DIRECTIVE_META_LINE_PATTERN.search(line):
                continue
            if lowered.startswith("- 禁止"):
                continue
            kept.append(line)
            if len("\n".join(kept)) >= 1200:
                break

        compact = "\n".join(kept).strip()
        if compact:
            return compact
        return "分析当前工作区需求并产出可执行架构文档"

    @staticmethod
    def _should_force_finalize_docs(
        *,
        result: dict[str, Any],
        docs: dict[str, str],
        issues: list[str],
        content: str,
    ) -> bool:
        error_text = str(result.get("error") or "").strip().lower()
        if "role_tool_rounds_exhausted" in error_text:
            return True
        plan_text = str(docs.get("plan_markdown") or "").strip()
        architecture_text = str(docs.get("architecture_markdown") or "").strip()
        if not plan_text and not architecture_text:
            return True
        if not issues:
            return False
        normalized_content = str(content or "").strip()
        if not normalized_content:
            return True
        return bool(
            _TOOL_MARKER_PATTERN.search(normalized_content) and not re.search(r"(?m)^##\s+", normalized_content)
        )

    @staticmethod
    def _should_attempt_truncated_docs_repair(content: str, docs: dict[str, str]) -> bool:
        body = str(content or "").strip()
        if not body:
            return False
        if str(docs.get("plan_markdown") or "").strip() and str(docs.get("architecture_markdown") or "").strip():
            return False
        return ResponseNormalizer.looks_truncated_json(body)

    async def _repair_truncated_docs_if_needed(
        self,
        *,
        task_id: str,
        attempt_label: str,
        directive: str,
        result: dict[str, Any],
        content: str,
        docs: dict[str, str],
        issues: list[str],
    ) -> tuple[dict[str, Any], str, dict[str, str], list[str]]:
        if not self._should_attempt_truncated_docs_repair(content, docs):
            return result, content, docs, issues

        settings = get_settings()
        repair_response = await generate_role_response(
            workspace=self.workspace,
            settings=settings,
            role=self.role_id,
            message=self._build_truncated_docs_repair_message(directive, content),
            context={"validate_output": True},
            validate_output=False,
            max_retries=1,
        )
        repair_content = (
            str(repair_response.get("response") or "")
            if isinstance(repair_response, dict)
            else str(repair_response or "")
        )
        repair_error = str(repair_response.get("error") or "") if isinstance(repair_response, dict) else ""
        repair_result = {
            "content": repair_content,
            "success": bool(repair_content) and not bool(repair_error),
            "error": repair_error or None,
            "raw_response": repair_response,
        }
        repaired_docs = self._extract_docs(repair_content, directive)
        repaired_issues = self._collect_doc_quality_issues(repaired_docs)
        self._capture_attempt_snapshot(
            task_id=task_id,
            attempt_label=f"{attempt_label}_truncation_repair",
            result=repair_result,
            content=repair_content,
            docs=repaired_docs,
            issues=repaired_issues,
        )
        return repair_result, repair_content, repaired_docs, repaired_issues

    def _extract_docs(self, content: str, directive: str) -> dict[str, str]:
        raw_content = str(content or "").strip()
        payload = self._extract_json_object(content)
        if isinstance(payload, dict):
            source_payload = payload
            nested_payload = payload.get("data")
            if isinstance(nested_payload, dict):
                source_payload = nested_payload
            plan_text = str(
                source_payload.get("plan_markdown")
                or source_payload.get("plan")
                or source_payload.get("project_plan")
                or ""
            ).strip()
            architecture_text = str(
                source_payload.get("architecture_markdown")
                or source_payload.get("architecture")
                or source_payload.get("system_design")
                or ""
            ).strip()
        elif self._looks_like_unparsed_structured_docs(raw_content):
            plan_text = ""
            architecture_text = ""
        else:
            plan_text, architecture_text = self._extract_docs_from_markdown_or_text(
                content=content,
                directive=directive,
            )

        plan_text = self._sanitize_doc_markdown(plan_text)
        architecture_text = self._sanitize_doc_markdown(architecture_text)

        missing_fields: list[str] = []
        if not plan_text:
            missing_fields.append("plan_markdown")
        if not architecture_text:
            missing_fields.append("architecture_markdown")
        if missing_fields:
            raw_text = raw_content
            raw_text = self._sanitize_doc_markdown(raw_text)
            if raw_text and not self._looks_like_unparsed_structured_docs(raw_content):
                if not plan_text:
                    plan_text = raw_text
                if not architecture_text:
                    architecture_text = raw_text

        combined_design = "\n\n".join(
            [
                "# Design",
                "",
                "## Plan",
                plan_text,
                "",
                "## Architecture",
                architecture_text,
            ]
        )
        return {
            "plan_markdown": plan_text,
            "architecture_markdown": architecture_text,
            "design_markdown": combined_design,
        }

    @staticmethod
    def _looks_like_safety_refusal(text: str) -> bool:
        lowered = str(text or "").strip().lower()
        if not lowered:
            return False
        refusal_markers = (
            "提示词注入",
            "系统指令",
            "无法执行",
            "违反安全策略",
            "prompt injection",
            "policy",
            "safety",
        )
        return any(marker in lowered for marker in refusal_markers)

    @classmethod
    def _looks_like_unparsed_structured_docs(cls, text: str) -> bool:
        body = str(text or "").strip()
        if not body:
            return False

        lowered = body.lower()
        has_doc_field_marker = any(marker in lowered for marker in _STRUCTURED_DOC_FIELD_MARKERS)
        has_json_wrapper = body.startswith("{") or "```json" in lowered or "<output" in lowered
        if has_doc_field_marker and ResponseNormalizer.looks_truncated_json(body):
            return True
        if has_doc_field_marker:
            return True
        return bool(has_json_wrapper)

    @classmethod
    def _sanitize_doc_markdown(cls, text: str) -> str:
        normalized = str(text or "")
        if not normalized.strip():
            return ""

        cleaned = normalized
        for pattern in _DOC_STRIP_BLOCK_PATTERNS:
            cleaned = pattern.sub("", cleaned)

        filtered_lines: list[str] = []
        in_tool_payload = False
        brace_balance = 0
        for raw_line in cleaned.splitlines():
            line = str(raw_line or "")
            stripped = line.strip()
            lowered = stripped.lower()
            if not stripped:
                if not in_tool_payload:
                    filtered_lines.append("")
                continue

            if not in_tool_payload and "tool =>" in lowered:
                in_tool_payload = True
                brace_balance = line.count("{") - line.count("}")
                if brace_balance <= 0:
                    in_tool_payload = False
                    brace_balance = 0
                continue

            if in_tool_payload:
                brace_balance += line.count("{") - line.count("}")
                if brace_balance <= 0:
                    in_tool_payload = False
                    brace_balance = 0
                continue

            if lowered == "tool_calls":
                continue
            if any(marker in lowered for marker in _DOC_STRIP_LINE_MARKERS):
                continue
            filtered_lines.append(line)

        sanitized = "\n".join(filtered_lines)
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
        return sanitized.strip()

    @staticmethod
    def _extract_json_object(content: str) -> dict[str, Any] | None:
        text = str(content or "").strip()
        if not text:
            return None

        output_unwrapped = re.sub(r"</?output\b[^>]*>", "", text, flags=re.IGNORECASE).strip()
        output_blocks = re.findall(r"<output\b[^>]*>(.*?)</output>", text, flags=re.IGNORECASE | re.DOTALL)

        candidates: list[str] = [text]
        if output_unwrapped:
            candidates.append(output_unwrapped)
        candidates.extend(str(item or "").strip() for item in output_blocks if str(item or "").strip())
        fenced = re.findall(
            r"(?:```|''')\s*(?:json|yaml|yml|markdown|md)?\s*(.*?)(?:```|''')",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        candidates.extend(item.strip() for item in fenced if item.strip())

        decoder = json.JSONDecoder()
        for candidate in candidates:
            candidate_text = str(candidate or "").strip()
            if not candidate_text:
                continue

            parsed_payloads: list[Any] = []
            try:
                parsed_payloads.append(json.loads(candidate_text))
            except (RuntimeError, ValueError) as exc:
                logger.debug("architect_adapter.py:499 json.loads failed: %s", exc)

            for index, char in enumerate(candidate_text):
                if char not in "{[":
                    continue
                try:
                    parsed, _end = decoder.raw_decode(candidate_text[index:])
                except (RuntimeError, ValueError):
                    continue
                parsed_payloads.append(parsed)

            for parsed in parsed_payloads:
                if isinstance(parsed, dict) and ArchitectAdapter._looks_like_doc_payload(parsed):
                    return parsed
        return None

    @staticmethod
    def _looks_like_doc_payload(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        keys = {str(key or "").strip().lower() for key in payload}
        if not keys:
            return False
        expected = {
            "plan_markdown",
            "architecture_markdown",
            "plan",
            "architecture",
            "project_plan",
            "system_design",
            "design_markdown",
        }
        if keys.intersection(expected):
            return True
        nested = payload.get("data")
        if isinstance(nested, dict):
            nested_keys = {str(key or "").strip().lower() for key in nested}
            if nested_keys.intersection(expected):
                return True
        return False

    def _extract_docs_from_markdown_or_text(
        self,
        *,
        content: str,
        directive: str,
    ) -> tuple[str, str]:
        del directive
        text = str(content or "").strip()
        if not text:
            return "", ""

        plan_chunks: list[str] = []
        architecture_chunks: list[str] = []
        for heading, body in self._split_markdown_sections(text):
            heading_lower = heading.lower()
            body_text = str(body or "").strip()
            if not body_text:
                continue
            if any(token in heading_lower for token in ("计划", "plan", "roadmap", "milestone", "任务")):
                plan_chunks.append(body_text)
            if any(token in heading_lower for token in ("架构", "architecture", "design", "module", "模块")):
                architecture_chunks.append(body_text)

        if not plan_chunks:
            plan_chunks.append(text)
        if not architecture_chunks:
            architecture_chunks.append(text)
        plan_text = "\n\n".join(plan_chunks).strip()
        architecture_text = "\n\n".join(architecture_chunks).strip()
        return plan_text, architecture_text

    @staticmethod
    def _split_markdown_sections(content: str) -> list[tuple[str, str]]:
        text = str(content or "").strip()
        if not text:
            return []

        lines = text.splitlines()
        sections: list[tuple[str, str]] = []
        current_heading = "content"
        current_body: list[str] = []
        heading_re = re.compile(r"^\s*#{1,6}\s*(.+?)\s*$")
        for line in lines:
            match = heading_re.match(line)
            if match:
                body = "\n".join(current_body).strip()
                if body:
                    sections.append((current_heading, body))
                current_heading = str(match.group(1) or "").strip() or "content"
                current_body = []
                continue
            current_body.append(line)

        tail = "\n".join(current_body).strip()
        if tail:
            sections.append((current_heading, tail))
        return sections

    def _perform_architecture_self_review(
        self,
        docs: dict[str, str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Phase 2.3: Architecture self-review against design pattern library.

        Args:
            docs: Extracted documents (plan_markdown, architecture_markdown)
            context: Execution context for constraint propagation

        Returns:
            Self-review result with pattern matches, concerns, and recommendations
        """
        plan_text = str(docs.get("plan_markdown") or "")
        arch_text = str(docs.get("architecture_markdown") or "")
        combined = f"{plan_text} {arch_text}".lower()

        # Match against design patterns
        matched_patterns: list[dict[str, Any]] = []
        for pattern_name, pattern_info in _DESIGN_PATTERNS.items():
            pattern_keywords = pattern_name.replace("_", " ")
            name_match = pattern_keywords in combined or pattern_name.replace("_", "") in combined
            if name_match:
                matched_patterns.append(
                    {
                        "pattern": pattern_name,
                        "description": pattern_info.get("description", ""),
                        "modules": pattern_info.get("modules", []),
                    }
                )

        # Check for architecture red flags
        concerns: list[str] = []
        if "tight_coupling" in combined or "coupling" in combined:
            concerns.append("Potential tight coupling detected - consider hexagonal architecture")
        if "single" in combined and "monolith" in combined:
            concerns.append("Monolithic structure - consider microservices if scale requires")
        if len(plan_text) > 0 and len(arch_text) < 100:
            concerns.append("Architecture documentation is sparse - may need expansion")
        if matched_patterns and len(matched_patterns) >= 2:
            concerns.append("Multiple architectural patterns detected - ensure they are compatible")

        # Generate recommendations
        recommendations: list[str] = []
        if not matched_patterns:
            recommendations.append("Consider applying a known design pattern for maintainability")
            recommendations.append("Layered architecture is a safe default for most applications")
        if concerns:
            recommendations.append("Address architecture concerns before proceeding to implementation")
        if matched_patterns:
            recommendations.append(f"Detected patterns: {', '.join(p['pattern'] for p in matched_patterns)}")

        # Propagate constraints to Director via context
        if concerns or recommendations:
            self._propagate_architect_constraints(context, concerns, recommendations)

        return {
            "matched_patterns": matched_patterns,
            "concerns": concerns,
            "recommendations": recommendations,
            "review_passed": len(concerns) == 0,
        }

    def _propagate_architect_constraints(
        self,
        context: dict[str, Any],
        concerns: list[str],
        recommendations: list[str],
    ) -> None:
        """Phase 2.3: Propagate architect constraints back to Director for execution.

        Args:
            context: Runtime context to update
            concerns: Architecture concerns to propagate
            recommendations: Recommendations to propagate
        """
        if not context:
            return

        # Update context with architect constraints
        ctx_metadata = context.get("metadata") if isinstance(context, dict) else None
        if ctx_metadata is None:
            ctx_metadata = {}
            if isinstance(context, dict):
                context["metadata"] = ctx_metadata

        # Add architect-derived constraints
        if isinstance(ctx_metadata, dict):
            existing_constraints = ctx_metadata.get("architect_constraints", [])
            if not isinstance(existing_constraints, list):
                existing_constraints = []
            new_constraints = (
                existing_constraints
                + [{"type": "concern", "text": c} for c in concerns]
                + [{"type": "recommendation", "text": r} for r in recommendations]
            )
            ctx_metadata["architect_constraints"] = new_constraints[:10]  # Cap at 10

    def _collect_doc_quality_issues(self, docs: dict[str, str]) -> list[str]:
        issues: list[str] = []
        for label, text in (
            ("plan_markdown", str(docs.get("plan_markdown") or "")),
            ("architecture_markdown", str(docs.get("architecture_markdown") or "")),
        ):
            normalized = text.strip()
            if len(normalized) < _MIN_DOC_CHARS:
                issues.append(f"{label}_too_short")
            headings = len(re.findall(r"(?m)^##\s+", normalized))
            if headings < _MIN_DOC_HEADINGS:
                issues.append(f"{label}_insufficient_headings")
        return issues

    @staticmethod
    def _has_blocking_doc_issues(issues: list[str]) -> bool:
        return any(str(item or "").endswith("_too_short") for item in issues)

    def _write_docs(self, docs: dict[str, str]) -> tuple[Path, Path, Path]:
        docs_dir = Path(resolve_workspace_persistent_path(self.workspace, "workspace/docs"))
        docs_dir.mkdir(parents=True, exist_ok=True)

        plan_doc = docs_dir / "plan.md"
        architecture_doc = docs_dir / "architecture.md"
        design_doc = docs_dir / "design.md"
        timestamp = datetime.now(timezone.utc).isoformat()
        plan_markdown = self._sanitize_doc_markdown(str(docs.get("plan_markdown") or ""))
        architecture_markdown = self._sanitize_doc_markdown(str(docs.get("architecture_markdown") or ""))
        design_markdown = self._sanitize_doc_markdown(str(docs.get("design_markdown") or ""))

        write_text_atomic(
            str(plan_doc),
            f"# 项目计划\n\n生成时间: {timestamp}\n\n{plan_markdown}\n",
            encoding="utf-8",
        )
        write_text_atomic(
            str(architecture_doc),
            f"# 架构设计\n\n生成时间: {timestamp}\n\n{architecture_markdown}\n",
            encoding="utf-8",
        )
        write_text_atomic(
            str(design_doc),
            f"# 设计总览\n\n生成时间: {timestamp}\n\n{design_markdown}\n",
            encoding="utf-8",
        )
        return plan_doc, architecture_doc, design_doc

    def _capture_attempt_snapshot(
        self,
        *,
        task_id: str,
        attempt_label: str,
        result: dict[str, Any],
        content: str,
        docs: dict[str, str],
        issues: list[str],
    ) -> None:
        """落盘 Architect 调试快照，便于压测失败后追踪真实模型输出。"""
        try:
            output_dir = Path(
                resolve_runtime_path(
                    self.workspace,
                    "runtime/roles/architect/outputs",
                )
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_task = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(task_id or "task")).strip("_") or "task"
            safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(attempt_label or "attempt")).strip("_") or "attempt"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "role": self.role_id,
                "task_id": str(task_id or ""),
                "attempt_label": str(attempt_label or ""),
                "result_success": bool(result.get("success")) if isinstance(result, dict) else False,
                "result_error": str(result.get("error") or "") if isinstance(result, dict) else "",
                "content_length": len(str(content or "")),
                "content": str(content or ""),
                "issues": [str(item) for item in (issues or [])],
                "docs_lengths": {
                    "plan_markdown": len(str(docs.get("plan_markdown") or "")),
                    "architecture_markdown": len(str(docs.get("architecture_markdown") or "")),
                    "design_markdown": len(str(docs.get("design_markdown") or "")),
                },
            }
            snapshot_path = output_dir / f"{safe_task}_{safe_label}_{stamp}.json"
            write_text_atomic(
                str(snapshot_path),
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (RuntimeError, ValueError):
            # 调试快照失败不应影响主流程
            return
