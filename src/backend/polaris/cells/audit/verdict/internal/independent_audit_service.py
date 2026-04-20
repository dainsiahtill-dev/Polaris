"""Independent Audit Service - Chancellery (门下省) for governance.

Provides separation of powers by running QA review independently of the
Director who produced the work. Uses a different LLM role configuration
to ensure impartiality.

Migrated from: core/polaris_loop/auditor.py
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from polaris.domain.verification.evidence_collector import EvidencePackage

logger = logging.getLogger(__name__)


@dataclass
class AuditVerdict:
    """Structured verdict from the Chancellery."""

    accepted: bool | None  # True=PASS, False=FAIL, None=INCONCLUSIVE
    raw_output: str
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    defect_ticket: dict[str, Any] = field(default_factory=dict)
    provider_info: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_pass(self) -> bool:
        """Whether the audit passed."""
        return self.accepted is True

    @property
    def is_fail(self) -> bool:
        """Whether the audit failed."""
        return self.accepted is False

    @property
    def is_inconclusive(self) -> bool:
        """Whether the audit is inconclusive."""
        return self.accepted is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "is_pass": self.is_pass,
            "is_fail": self.is_fail,
            "summary": self.summary,
            "findings": self.findings,
            "defect_ticket": self.defect_ticket,
            "provider_info": self.provider_info,
            "timestamp": self.timestamp.isoformat(),
            "raw_output_preview": self.raw_output[:500] if self.raw_output else "",
        }


@dataclass
class AuditContext:
    """Context for running an audit."""

    task_id: str
    plan_text: str = ""
    memory_summary: str = ""
    target_note: str = ""
    changed_files: list[str] = field(default_factory=list)
    planner_output: str = ""
    executor_output: str = ""  # Renamed from ollama_output for generic use
    tool_results: str = ""
    reviewer_summary: str = ""
    patch_risk_summary: str = ""
    evidence_package: EvidencePackage | None = None
    step: int = 0
    run_id: str = ""


class IndependentAuditService:
    """Independent audit service providing Chancellery (门下省) review.

        This service implements the separation of powers principle:
        - The Director (工部) builds/executes
        - The Auditor (门下省) reviews independently

        The auditor uses a DIFFERENT LLM role configuration than the Director
    to ensure genuine independence and prevent self-review.
    """

    def __init__(
        self,
        llm_caller: Callable[[str, str], tuple[str, dict[str, str]]] | None = None,
    ) -> None:
        """Initialize the audit service.

        Args:
            llm_caller: Function(role, prompt) -> (output, provider_info)
                        If None, audits will return inconclusive.
        """
        self._llm_caller = llm_caller
        self._audit_history: list[AuditVerdict] = []

    async def run_audit(
        self,
        context: AuditContext,
        max_retries: int = 1,
    ) -> AuditVerdict:
        """Run independent audit review.

        Args:
            context: Audit context with all necessary information
            max_retries: Number of retries for inconclusive verdicts

        Returns:
            AuditVerdict with acceptance decision
        """
        if not self._llm_caller:
            return AuditVerdict(
                accepted=None,
                raw_output="No LLM caller configured",
                summary="Audit inconclusive - no LLM configured",
            )

        prompt = self._build_audit_prompt(context)

        start_ts = time.time()
        try:
            output, provider_info = self._llm_caller("qa", prompt)
        except Exception as e:
            # Preserve the exception context so callers can trace the root cause.
            # raw_output contains the error details; summary provides a human-readable summary.
            logger.error("Independent audit LLM call failed: %s", e, exc_info=True)
            return AuditVerdict(
                accepted=None,
                raw_output=f"LLM call failed: {type(e).__name__}: {e}",
                summary=f"Audit failed: {e}",
            )

        # Track duration for logging purposes
        int((time.time() - start_ts) * 1000)

        # Parse verdict
        verdict = self._parse_verdict(output, provider_info)

        # Retry for inconclusive verdicts
        retry_count = 0
        while verdict.is_inconclusive and retry_count < max_retries:
            retry_count += 1
            finalize_prompt = self._build_finalize_prompt(
                raw_output=output,
                context=context,
            )
            try:
                output, _ = self._llm_caller("qa", finalize_prompt)
                verdict = self._parse_verdict(output, provider_info)
            except (RuntimeError, ValueError) as exc:
                logger.warning("Finalize verdict LLM call failed (retry %d/%d): %s", retry_count, max_retries, exc)
                break

        # Evidence consistency check
        if verdict.is_fail and self._mentions_missing_evidence(verdict.raw_output, context):
            consistency_prompt = self._build_consistency_prompt(context, output)
            try:
                output, _ = self._llm_caller("qa", consistency_prompt)
                verdict = self._parse_verdict(output, provider_info)
            except (RuntimeError, ValueError) as exc:
                logger.warning("Consistency check LLM call failed: %s", exc)

        # Record in history
        self._audit_history.append(verdict)

        return verdict

    def _build_audit_prompt(self, context: AuditContext) -> str:
        """Build the audit prompt."""
        files_text = "\n".join(f"- {path}" for path in context.changed_files) if context.changed_files else "- (none)"

        evidence_summary = ""
        if context.evidence_package:
            evidence_summary = f"""
Evidence Summary:
- File changes: {len(context.evidence_package.file_changes)}
- Tool outputs: {len(context.evidence_package.tool_outputs)}
- Verification results: {len(context.evidence_package.verification_results)}
- Critical issues: {context.evidence_package.has_critical_issues()}
"""

        return f"""你是门下省 QA 审核官。请对以下工作进行独立审核。

=== 任务计划 ===
{context.plan_text[:2000] if context.plan_text else "(no plan)"}

=== 改动文件 ===
{files_text}

=== 执行输出摘要 ===
{context.executor_output[:1500] if context.executor_output else "(no output)"}

=== 工具结果 ===
{context.tool_results[:1000] if context.tool_results else "(no tool results)"}

=== 评审摘要 ===
{context.reviewer_summary[:500] if context.reviewer_summary else "(no review)"}

=== 风险摘要 ===
{context.patch_risk_summary}

{evidence_summary}

请输出审核结论（单个 JSON 对象，禁止 Markdown、禁止额外文本）：

{{"acceptance":"PASS|FAIL","summary":"简短总结","next":"下一步行动","findings":["问题1","问题2"]}}

规则：
- 证据充分且满足验收标准才可 PASS
- 证据不足或存在缺陷必须 FAIL
- findings 必须具体且可执行
"""

    def _build_finalize_prompt(
        self,
        raw_output: str,
        context: AuditContext,
    ) -> str:
        """Build prompt to finalize inconclusive verdict."""
        files_text = "\n".join(f"- {path}" for path in context.changed_files) if context.changed_files else "- (none)"

        return f"""你是门下省 QA 审核官。上一条输出未给出可解析的最终验收结论。
现在必须给出最终裁定，且只能输出单个 JSON 对象（禁止 Markdown、禁止额外文本）。

输出格式（必填）：
{{"acceptance":"PASS|FAIL","summary":"...","next":"...","findings":["..."]}}

规则：
- 证据充分且满足验收标准才可 PASS
- 证据不足或存在缺陷必须 FAIL，并在 findings 中列出关键问题
- 不得继续请求工具调用

改动文件:
{files_text}

工具结果摘要:
{context.tool_results[:500] if context.tool_results else ""}

评审摘要:
{context.reviewer_summary[:300] if context.reviewer_summary else ""}

上一条原始输出:
{raw_output[:800]}
"""

    def _build_consistency_prompt(
        self,
        context: AuditContext,
        raw_output: str,
    ) -> str:
        """Build prompt for evidence consistency check."""
        files_text = "\n".join(f"- {path}" for path in context.changed_files) if context.changed_files else "- (none)"

        planner_preview = context.planner_output[:1200] if context.planner_output else ""
        executor_preview = context.executor_output[:1200] if context.executor_output else ""

        return f"""你是门下省 QA 审核官。上一条结论与已知证据状态存在冲突，需重新裁定。
事实：planner_output 与 executor_output 均已提供，不能再声称"输出为空"。
请基于以下证据重新输出单个 JSON（禁止 Markdown/额外文本）：
{{"acceptance":"PASS|FAIL","summary":"...","next":"...","findings":["..."]}}

planner_output_length={len(context.planner_output)}
executor_output_length={len(context.executor_output)}

planner_output_preview:
{planner_preview}

executor_output_preview:
{executor_preview}

changed_files:
{files_text}

tool_results:
{context.tool_results[:500] if context.tool_results else ""}

previous_verdict:
{raw_output[:500]}
"""

    def _parse_verdict(
        self,
        output: str,
        provider_info: dict[str, str],
    ) -> AuditVerdict:
        """Parse audit output into structured verdict."""
        if not output:
            return AuditVerdict(
                accepted=None,
                raw_output="",
                summary="Empty output",
            )

        # Try to parse JSON
        try:
            # Extract JSON if wrapped in markdown
            json_str = output
            if "```json" in output:
                json_str = output.split("```json")[1].split("```", maxsplit=1)[0]
            elif "```" in output:
                json_str = output.split("```")[1].split("```", maxsplit=1)[0]

            payload = json.loads(json_str.strip())

            # Parse acceptance
            acceptance_str = str(payload.get("acceptance", "")).upper()
            if acceptance_str in ("PASS", "TRUE", "YES", "APPROVED"):
                accepted = True
            elif acceptance_str in ("FAIL", "FALSE", "NO", "REJECTED"):
                accepted = False
            else:
                accepted = None

            # Extract fields
            summary = str(payload.get("summary", "")).strip()
            findings = payload.get("findings", [])
            if isinstance(findings, str):
                findings = [findings]
            elif not isinstance(findings, list):
                findings = []

            findings = [str(f) for f in findings if f]

            # Extract defect ticket if present
            defect_ticket = payload.get("defect_ticket", {})
            if not isinstance(defect_ticket, dict):
                defect_ticket = {}

            return AuditVerdict(
                accepted=accepted,
                raw_output=output,
                summary=summary,
                findings=findings,
                defect_ticket=defect_ticket,
                provider_info=provider_info,
            )

        except json.JSONDecodeError:
            # Try to infer from text
            output_upper = output.upper()
            if "PASS" in output_upper or "APPROVED" in output_upper:
                accepted = True
            elif "FAIL" in output_upper or "REJECTED" in output_upper:
                accepted = False
            else:
                accepted = None

            return AuditVerdict(
                accepted=accepted,
                raw_output=output,
                summary="Failed to parse structured output",
            )

    def _mentions_missing_evidence(
        self,
        raw_output: str,
        context: AuditContext,
    ) -> bool:
        """Check if verdict claims missing evidence when evidence exists."""
        texts = [raw_output.lower()]
        merged = "\n".join(texts)

        markers = (
            "planner输出为空",
            "planner 输出为空",
            "executor输出为空",
            "output empty",
            "文件状态未知",
            "无法确认文件",
            "state unknown",
            "cannot confirm file",
        )

        has_marker = any(marker in merged for marker in markers)
        has_evidence = bool(context.planner_output.strip() or context.executor_output.strip())

        return has_marker and has_evidence

    def get_audit_history(self) -> list[AuditVerdict]:
        """Get history of all audits run by this service."""
        return self._audit_history.copy()

    def get_stats(self) -> dict[str, Any]:
        """Get audit statistics."""
        if not self._audit_history:
            return {"total": 0, "pass": 0, "fail": 0, "inconclusive": 0}

        total = len(self._audit_history)
        passed = sum(1 for v in self._audit_history if v.is_pass)
        failed = sum(1 for v in self._audit_history if v.is_fail)
        inconclusive = sum(1 for v in self._audit_history if v.is_inconclusive)

        return {
            "total": total,
            "pass": passed,
            "fail": failed,
            "inconclusive": inconclusive,
            "pass_rate": passed / total if total > 0 else 0,
        }
