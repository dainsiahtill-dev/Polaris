"""reporting methods for StressEngine (mixin)."""

# mypy: ignore-errors

import asyncio
from datetime import datetime, timezone

from ..contracts import (
    is_generic_failure_point,
)
from ..tracer import RoundTrace
from ._models import RoundResult, StageResult


class _StressEngineReportingMixin:
    async def _finalize_round(self, result: RoundResult) -> RoundResult:
        """完成轮次"""
        result.end_time = datetime.now().isoformat()
        workspace_artifacts = result.workspace_artifacts if isinstance(result.workspace_artifacts, dict) else {}
        path_fallback_delta = max(
            int(self.path_fallback_count) - int(self._current_round_path_fallback_before),
            0,
        )
        workspace_artifacts["path_fallback_count"] = int(path_fallback_delta)
        workspace_artifacts["path_contract_ok"] = bool(path_fallback_delta == 0)
        result.workspace_artifacts = workspace_artifacts

        # 停止追踪并获取数据
        trace: RoundTrace | None = None
        if self.tracer:
            try:
                trace = await asyncio.wait_for(
                    self.tracer.complete_round(result.overall_result),
                    timeout=self.trace_finalize_timeout + 1.0,
                )
            except asyncio.TimeoutError:
                print(
                    f"[tracer] Complete round timed out after "
                    f"{self.trace_finalize_timeout + 1.0:.1f}s; using partial trace"
                )
                trace = self.tracer.current_round
            except (OSError, RuntimeError, ValueError) as e:
                print(f"[tracer] Complete round failed: {type(e).__name__}: {e}")
                trace = self.tracer.current_round
        result.trace = trace

        # 如果没有明确的失败点，从追踪数据分析
        if (not result.failure_point or is_generic_failure_point(result.failure_point)) and trace:
            failures = trace.get_failure_analysis()
            if failures:
                first_failure = failures[0]
                if not result.failure_point or is_generic_failure_point(result.failure_point):
                    result.failure_point = first_failure.get("type", "unknown")
                if not result.failure_evidence:
                    result.failure_evidence = str(first_failure)[:500]

        if result.diagnostic_report:
            if not result.failure_point or is_generic_failure_point(result.failure_point):
                result.failure_point = result.diagnostic_report.failure_point
            if not result.root_cause:
                result.root_cause = result.diagnostic_report.root_cause_analysis
            if not result.failure_evidence:
                result.failure_evidence = result.diagnostic_report.summary

        # 保存可观测性数据
        if self.collector:
            try:
                result.observability_data = self.collector.to_dict()
            except (TypeError, ValueError, AttributeError) as e:
                result.observability_data = {
                    "serialization_error": f"{type(e).__name__}: {e}",
                }
        self._normalize_optional_chain_stages(result)
        await self._backfill_stage_timings(result)
        await self._capture_chain_stage_evidence(result)
        self._enforce_chain_evidence_gate(result)

        print(f"\n[Result] Round #{result.round_number}: {result.overall_result}")
        if result.failure_point:
            print(f"  Failure Point: {result.failure_point}")
            print(f"  Root Cause: {result.root_cause[:200]}...")

        # 打印诊断报告摘要 (如果失败)
        if result.diagnostic_report and result.overall_result == "FAIL":
            print(f"  失败分类: {result.diagnostic_report.failure_category.value}")
            print("  建议修复:")
            for i, fix in enumerate(result.diagnostic_report.suggested_fixes[:3], 1):
                print(f"    {i}. {fix}")

        return result

    def generate_project_report(self, result: RoundResult) -> str:
        """生成项目级报告"""
        lines = [
            f"# 压测报告 - Round #{result.round_number}: {result.project.name}",
            "",
            f"- **项目**: {result.project.name}",
            f"- **类别**: {result.project.category.value}",
            f"- **结果**: {result.overall_result}",
            f"- **Factory Run**: `{result.factory_run_id}`",
            f"- **耗时**: {self._format_duration(result.start_time, result.end_time)}",
            "",
            "## 阶段执行",
            "",
        ]

        stages = [
            ("架构设计", result.architect_stage),
            ("任务规划", result.pm_stage),
            ("技术分析", result.chief_engineer_stage),
            ("代码执行", result.director_stage),
            ("质量审查", result.qa_stage),
        ]

        for name, stage in stages:
            if stage:
                icon = self._result_icon(stage.result)
                lines.append(f"- {icon} **{name}**: {stage.result.value} ({stage.duration_ms}ms)")
                if stage.error:
                    lines.append(f"  - 错误: {stage.error[:100]}")

        lines.extend(
            [
                "",
                "## 追踪统计",
                "",
            ]
        )

        if result.trace:
            stats = result.trace.to_dict().get("statistics", {})
            lines.extend(
                [
                    f"- 总任务数: {stats.get('total_tasks', 0)}",
                    f"- 完成任务: {stats.get('completed_tasks', 0)}",
                    f"- 失败任务: {stats.get('failed_tasks', 0)}",
                    f"- Factory Runs: {stats.get('total_factory_runs', 0)}",
                ]
            )

        if result.failure_point:
            lines.extend(
                [
                    "",
                    "## 失败分析",
                    "",
                    f"- **失效环节**: {result.failure_point}",
                    f"- **根因**: {result.root_cause}",
                    "",
                    "### 证据",
                    "",
                    "```",
                    result.failure_evidence[:1000],
                    "```",
                ]
            )

        return "\n".join(lines)

    def _result_icon(self, result: StageResult) -> str:
        return {
            StageResult.SUCCESS: "✅",
            StageResult.PARTIAL: "⚠️",
            StageResult.FAILURE: "❌",
            StageResult.TIMEOUT: "⏱️",
            StageResult.SKIPPED: "⏭️",
        }.get(result, "❓")

    def _format_duration(self, start: str, end: str | None) -> str:
        if not end:
            return "unknown"
        try:
            start_dt = self._parse_iso_timestamp(start)
            end_dt = self._parse_iso_timestamp(end)
            if not start_dt or not end_dt:
                return "unknown"
            delta = end_dt - start_dt
            return f"{delta.total_seconds():.1f}s"
        except (ValueError, TypeError):
            return "unknown"

    @staticmethod
    def _parse_iso_timestamp(raw: str | None) -> datetime | None:
        token = str(raw or "").strip()
        if not token:
            return None
        if token.endswith("Z"):
            token = token[:-1] + "+00:00"
        parsed = datetime.fromisoformat(token)
        if parsed.tzinfo is None:
            local_tz = datetime.now().astimezone().tzinfo
            return parsed.replace(tzinfo=local_tz or timezone.utc).astimezone(timezone.utc)
        return parsed.astimezone(timezone.utc)
