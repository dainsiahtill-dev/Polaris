"""Evaluation Framework - Suite Runner

统一评测运行器，提供流式和非流式两种模式。

✅ MIGRATION COMPLETED (2026-04-09): kernelone.llm.engine imports 已迁移到 cell-defined types。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone.storage import resolve_runtime_path

from .agentic_benchmark import run_agentic_benchmark_suite
from .constants import REQUIRED_SUITES_BY_ROLE
from .index import update_index_with_report
from .session_workflow_matrix import run_session_workflow_suite
from .suites import (
    run_connectivity_suite,
    run_interview_suite,
    run_qualification_suite,
    run_response_suite,
    run_thinking_suite,
)
from .timeout import TimeoutConfig, TimeoutResult, run_with_timeout_optional
from .tool_calling_matrix import run_tool_calling_matrix_suite
from .utils import dedupe, new_test_run_id, utc_now, write_json_atomic

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.bootstrap.config import Settings

logger = logging.getLogger(__name__)


# Cell-defined evaluation types (替代 kernelone.llm.engine imports)
class AIStreamEventType(Enum):
    CHUNK = "chunk"
    REASONING_CHUNK = "reasoning_chunk"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class AIStreamEvent:
    """Cell-defined stream event (替代 kernelone.llm.engine.AIStreamEvent)."""

    type: AIStreamEventType
    content: str = ""
    reasoning: str = ""
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def chunk_event(content: str) -> AIStreamEvent:
        return AIStreamEvent(type=AIStreamEventType.CHUNK, content=content)

    @staticmethod
    def reasoning_chunk(reasoning: str) -> AIStreamEvent:
        return AIStreamEvent(type=AIStreamEventType.REASONING_CHUNK, reasoning=reasoning)

    @staticmethod
    def complete(meta: dict[str, Any] | None = None) -> AIStreamEvent:
        return AIStreamEvent(type=AIStreamEventType.COMPLETE, meta=meta or {})

    @staticmethod
    def error_event(error_msg: str) -> AIStreamEvent:
        return AIStreamEvent(type=AIStreamEventType.ERROR, error=error_msg)


@dataclass
class EvaluationRequest:
    """Cell-defined evaluation request."""

    provider_id: str
    model: str = ""
    role: str = "default"
    suites: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationResult:
    """Cell-defined evaluation result."""

    case_id: str
    passed: bool
    output: str = ""
    score: float = 0.0
    error: str = ""
    latency_ms: int = 0


@dataclass
class EvaluationSuiteResult:
    """Cell-defined evaluation suite result."""

    suite_name: str
    results: list[EvaluationResult] = field(default_factory=list)
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    total_latency_ms: int = 0


@dataclass
class EvaluationReport:
    """Cell-defined evaluation report."""

    report_id: str
    run_id: str
    timestamp: str
    provider_id: str
    model: str
    role: str
    suites: list[EvaluationSuiteResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "provider_id": self.provider_id,
            "model": self.model,
            "role": self.role,
            "suites": [
                {
                    "suite_name": s.suite_name,
                    "results": [
                        {
                            "case_id": r.case_id,
                            "passed": r.passed,
                            "output": r.output,
                            "score": r.score,
                            "error": r.error,
                            "latency_ms": r.latency_ms,
                        }
                        for r in s.results
                    ],
                    "total_cases": s.total_cases,
                    "passed_cases": s.passed_cases,
                    "failed_cases": s.failed_cases,
                    "total_latency_ms": s.total_latency_ms,
                }
                for s in self.suites
            ],
            "summary": self.summary,
            "metadata": self.metadata,
        }


class EvaluationRunner:
    """评测运行器

    提供统一的评测执行能力，支持流式和非流式两种模式。
    """

    SUITE_RUNNERS = {
        "connectivity": run_connectivity_suite,
        "response": run_response_suite,
        "thinking": run_thinking_suite,
        "qualification": run_qualification_suite,
        "interview": run_interview_suite,
        "agentic_benchmark": run_agentic_benchmark_suite,
        "tool_calling_matrix": run_tool_calling_matrix_suite,
        "session_workflow_matrix": run_session_workflow_suite,
    }

    def __init__(
        self,
        workspace: str,
        settings: Settings | None = None,
        timeout_config: TimeoutConfig | None = None,
    ) -> None:
        self.workspace = workspace
        self.settings = settings
        self.timeout_config = timeout_config or TimeoutConfig()

    def _get_suite_timeout(self, suite_name: str, options: dict[str, Any]) -> float:
        """Get timeout value for a specific suite.

        Priority: options[suite_name_timeout_sec] > options[suite_timeout_sec] > config
        """
        suite_specific_key = f"{suite_name}_timeout_sec"
        if suite_specific_key in options:
            return float(options[suite_specific_key])
        if "suite_timeout_sec" in options:
            return float(options["suite_timeout_sec"])
        return self.timeout_config.suite_timeout_sec

    def _convert_suite_result(self, suite_name: str, result_data: dict[str, Any]) -> EvaluationSuiteResult:
        """Convert raw result data to EvaluationSuiteResult.

        Args:
            suite_name: Name of the suite.
            result_data: Raw result dictionary from suite runner.

        Returns:
            EvaluationSuiteResult with parsed results.
        """
        results: list[EvaluationResult] = []
        suite_total = 1
        suite_passed = 1 if result_data.get("ok") else 0

        # If suite has detailed cases, parse each one
        if "details" in result_data and "cases" in result_data["details"]:
            for case_data in result_data["details"]["cases"]:
                results.append(
                    EvaluationResult(
                        case_id=case_data.get("id", "unknown"),
                        passed=case_data.get("passed", False),
                        output=case_data.get("output", ""),
                        score=case_data.get("score", 1.0 if case_data.get("passed") else 0.0),
                    )
                )
            suite_total = len(results)
            suite_passed = sum(1 for r in results if r.passed)

        return EvaluationSuiteResult(
            suite_name=suite_name,
            results=results,
            total_cases=suite_total,
            passed_cases=suite_passed,
            failed_cases=suite_total - suite_passed,
        )

    def normalize_suites(
        self,
        suites: list[str],
        role: str,
    ) -> list[str]:
        """标准化套件列表"""
        normalized_role = role.strip().lower()

        if normalized_role in ("connectivity", ""):
            return ["connectivity"]

        if not suites:
            return REQUIRED_SUITES_BY_ROLE.get(normalized_role, REQUIRED_SUITES_BY_ROLE["default"])

        normalized = [str(s).strip().lower() for s in suites if str(s).strip()]
        return dedupe(normalized)

    def _load_provider_cfg(self, provider_id: str) -> dict[str, Any]:
        if not provider_id:
            return {}
        try:
            from polaris.bootstrap.config import Settings
            from polaris.cells.llm.provider_config.public import (
                resolve_provider_request_context,
            )
            from polaris.cells.llm.provider_runtime.public.service import resolve_provider_api_key

            settings = self.settings if self.settings is not None else Settings()
            workspace = str(getattr(settings, "workspace", None) or self.workspace or ".").strip() or "."

            context = resolve_provider_request_context(
                workspace=workspace,
                cache_root=getattr(settings, "ramdisk_root", "") or "",
                provider_id=provider_id,
                api_key=None,
                headers=None,
            )
            return resolve_provider_api_key(provider_id, context.provider_type or "generic", context.provider_cfg or {})
        except (RuntimeError, ValueError) as exc:
            logger.debug("[evaluation.runner] failed to load provider config for %s: %s", provider_id, exc)
            return {}

    def _resolve_provider_cfg(self, provider_id: str, context_provider_cfg: Any) -> dict[str, Any]:
        context_cfg = dict(context_provider_cfg) if isinstance(context_provider_cfg, dict) else {}
        if str(context_cfg.get("type") or "").strip():
            return context_cfg

        loaded_cfg = self._load_provider_cfg(provider_id)
        if not loaded_cfg:
            return context_cfg
        # Direct payload fields should win over saved config.
        return {**loaded_cfg, **context_cfg}

    async def _run_suite(
        self,
        suite_name: str,
        runner: Any,
        request: EvaluationRequest,
        provider_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        if suite_name == "interview":
            return await runner(provider_cfg, request.model, request.role)
        if suite_name in {"agentic_benchmark", "tool_calling_matrix"}:
            return await runner(
                provider_cfg,
                request.model,
                request.role,
                workspace=self.workspace,
                settings=self.settings,
                context=request.context,
                options=request.options,
            )
        return await runner(provider_cfg, request.model)

    async def _run_suite_with_timeout(
        self,
        suite_name: str,
        runner: Any,
        request: EvaluationRequest,
        provider_cfg: dict[str, Any],
        timeout_sec: float,
    ) -> TimeoutResult:
        """Run suite with optional timeout protection.

        Args:
            suite_name: Name of the suite being run.
            runner: The suite runner coroutine.
            request: The evaluation request.
            provider_cfg: Resolved provider configuration.
            timeout_sec: Timeout in seconds (0 or negative disables timeout).

        Returns:
            TimeoutResult containing the suite result or error info.
        """
        if not self.timeout_config.enable_timeout or timeout_sec <= 0:
            # Run without timeout protection
            try:
                result = await self._run_suite(suite_name, runner, request, provider_cfg)
                return TimeoutResult(ok=True, result=result)
            except asyncio.CancelledError:
                raise
            except (RuntimeError, ValueError) as exc:
                return TimeoutResult(ok=False, error=str(exc))

        # Run with timeout protection
        return await run_with_timeout_optional(
            self._run_suite(suite_name, runner, request, provider_cfg),
            timeout_sec,
            f"suite:{suite_name}",
        )

    async def run(
        self,
        request: EvaluationRequest,
    ) -> EvaluationReport:
        """运行评测（非流式）"""
        run_id = new_test_run_id()
        timestamp = utc_now()

        provider_id = request.provider_id or "unknown"
        model = request.model or "unknown"
        role = request.role or "default"

        suites = self.normalize_suites(request.suites, role)
        provider_cfg = self._resolve_provider_cfg(
            provider_id,
            request.context.get("provider_cfg"),
        )

        # Apply API key from context
        if request.context.get("api_key"):
            provider_cfg["api_key"] = request.context["api_key"]

        suite_results: list[EvaluationSuiteResult] = []
        total_cases = 0
        passed_cases = 0

        for suite_name in suites:
            if suite_name not in self.SUITE_RUNNERS:
                continue

            runner = self.SUITE_RUNNERS[suite_name]
            timeout_sec = self._get_suite_timeout(suite_name, request.options)

            # Run with timeout protection
            timeout_result = await self._run_suite_with_timeout(suite_name, runner, request, provider_cfg, timeout_sec)

            if timeout_result.timed_out:
                result_data = {"ok": False, "error": timeout_result.error, "timed_out": True}
            elif not timeout_result.ok:
                result_data = {"ok": False, "error": timeout_result.error}
            else:
                result_data = timeout_result.result

            # 转换为 EvaluationSuiteResult
            results: list[EvaluationResult] = []
            suite_total = 1
            suite_passed = 1 if result_data.get("ok") else 0

            # 如果套件有详细 cases，逐个记录
            details = result_data.get("details")
            if isinstance(details, dict) and "cases" in details:
                cases = details.get("cases")
                if isinstance(cases, list):
                    for case_data in cases:
                        results.append(
                            EvaluationResult(
                                case_id=case_data.get("id", "unknown"),
                                passed=case_data.get("passed", False),
                                output=case_data.get("output", ""),
                                score=case_data.get("score", 1.0 if case_data.get("passed") else 0.0),
                            )
                        )
            if results:
                suite_total = len(results)
                suite_passed = sum(1 for r in results if r.passed)

            suite_result = EvaluationSuiteResult(
                suite_name=suite_name,
                results=results,
                total_cases=suite_total,
                passed_cases=suite_passed,
                failed_cases=suite_total - suite_passed,
            )
            suite_results.append(suite_result)

            total_cases += suite_total
            passed_cases += suite_passed

        # 生成报告
        pass_rate = passed_cases / total_cases if total_cases > 0 else 0.0

        report = EvaluationReport(
            report_id=f"rep-{run_id}",
            run_id=run_id,
            timestamp=timestamp,
            provider_id=provider_id,
            model=model,
            role=role,
            suites=suite_results,
            summary={
                "total_cases": total_cases,
                "passed_cases": passed_cases,
                "failed_cases": total_cases - passed_cases,
                "pass_rate": pass_rate,
                "grade": "PASS" if pass_rate >= 0.8 else "FAIL",
                "ready": pass_rate >= 0.6,
            },
            metadata={
                "requested_suites": suites,
                "options": request.options,
            },
        )

        # 持久化报告
        self._save_report(report)

        # 更新索引
        if request.options.get("update_index", True):
            update_index_with_report(self.workspace, report.to_dict())

        return report

    async def run_streaming(
        self,
        request: EvaluationRequest,
    ) -> AsyncGenerator[AIStreamEvent, None]:
        """运行评测（流式）"""
        run_id = new_test_run_id()
        timestamp = utc_now()

        provider_id = request.provider_id or "unknown"
        model = request.model or "unknown"
        role = request.role or "default"

        suites = self.normalize_suites(request.suites, role)
        provider_cfg = self._resolve_provider_cfg(
            provider_id,
            request.context.get("provider_cfg"),
        )

        if request.context.get("api_key"):
            provider_cfg["api_key"] = request.context["api_key"]

        # 发送开始事件
        yield AIStreamEvent.complete(
            {
                "event": "evaluation_start",
                "run_id": run_id,
                "suites": suites,
            }
        )

        suite_results: list[EvaluationSuiteResult] = []

        for suite_name in suites:
            if suite_name not in self.SUITE_RUNNERS:
                continue

            # 发送套件开始事件
            yield AIStreamEvent.chunk_event(f"Running {suite_name} suite...\n")

            runner = self.SUITE_RUNNERS[suite_name]
            try:
                result_data = await self._run_suite(suite_name, runner, request, provider_cfg)

                ok = result_data.get("ok", False)
                yield AIStreamEvent.chunk_event(f"  Result: {'PASS' if ok else 'FAIL'}\n")

            except (RuntimeError, ValueError) as e:
                result_data = {"ok": False, "error": str(e)}
                yield AIStreamEvent.chunk_event(f"  Error: {e}\n")

            # 转换为结果对象
            results: list[EvaluationResult] = []
            suite_total = 1
            suite_passed = 1 if result_data.get("ok") else 0

            if "details" in result_data and "cases" in result_data["details"]:
                for case_data in result_data["details"]["cases"]:
                    results.append(
                        EvaluationResult(
                            case_id=case_data.get("id", "unknown"),
                            passed=case_data.get("passed", False),
                            output=case_data.get("output", ""),
                            score=case_data.get("score", 1.0 if case_data.get("passed") else 0.0),
                        )
                    )
                suite_total = len(results)
                suite_passed = sum(1 for r in results if r.passed)

            suite_result = EvaluationSuiteResult(
                suite_name=suite_name,
                results=results,
                total_cases=suite_total,
                passed_cases=suite_passed,
                failed_cases=suite_total - suite_passed,
            )
            suite_results.append(suite_result)

        # 计算汇总
        total_cases = sum(s.total_cases for s in suite_results)
        passed_cases = sum(s.passed_cases for s in suite_results)
        pass_rate = passed_cases / total_cases if total_cases > 0 else 0.0

        report = EvaluationReport(
            report_id=f"rep-{run_id}",
            run_id=run_id,
            timestamp=timestamp,
            provider_id=provider_id,
            model=model,
            role=role,
            suites=suite_results,
            summary={
                "total_cases": total_cases,
                "passed_cases": passed_cases,
                "failed_cases": total_cases - passed_cases,
                "pass_rate": pass_rate,
                "grade": "PASS" if pass_rate >= 0.8 else "FAIL",
                "ready": pass_rate >= 0.6,
            },
        )

        # 持久化
        self._save_report(report)
        if request.options.get("update_index", True):
            update_index_with_report(self.workspace, report.to_dict())

        # 发送完成事件
        yield AIStreamEvent.complete(
            {
                "event": "evaluation_complete",
                "report": report.to_dict(),
            }
        )

    def _save_report(self, report: EvaluationReport) -> None:
        """保存报告到文件"""
        if not self.workspace:
            return

        reports_dir = Path(resolve_runtime_path(self.workspace, "runtime/llm_tests/reports"))
        reports_dir.mkdir(parents=True, exist_ok=True)

        report_path = reports_dir / f"{report.run_id}.json"
        write_json_atomic(str(report_path), report.to_dict())


__all__ = ["EvaluationRunner"]
