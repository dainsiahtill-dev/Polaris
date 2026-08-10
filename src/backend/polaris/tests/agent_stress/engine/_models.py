"""Result models for agent_stress StressEngine package."""

# mypy: ignore-errors

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..observability import DiagnosticReport
from ..project_pool import ProjectDefinition
from ..tracer import RoundTrace


class StageResult(Enum):
    """阶段执行结果"""

    PENDING = "pending"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


@dataclass
class StageExecution:
    """阶段执行记录"""

    stage_name: str
    result: StageResult
    start_time: str
    end_time: str
    duration_ms: int
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error: str = ""
    artifacts: list[str] = field(default_factory=list)


@dataclass
class CodeFileSnapshot:
    """代码文件快照"""

    digest: str
    line_count: int


@dataclass
class RoundResult:
    """单轮压测结果"""

    round_number: int
    project: ProjectDefinition
    start_time: str
    entry_stage: str = "architect"
    end_time: str | None = None
    overall_result: str = "pending"  # PASS/FAIL/PARTIAL

    # Factory 运行 ID
    factory_run_id: str | None = None

    # 各阶段结果 (从 Factory 运行状态映射)
    architect_stage: StageExecution | None = None
    pm_stage: StageExecution | None = None
    chief_engineer_stage: StageExecution | None = None
    director_stage: StageExecution | None = None
    qa_stage: StageExecution | None = None

    # 追踪数据
    trace: RoundTrace | None = None

    # 失败分析
    failure_point: str = ""  # Polaris 哪一环失效
    failure_evidence: str = ""  # 失败证据
    root_cause: str = ""  # 根因分析

    # 诊断报告 (AI Agent 可据此修复 Polaris)
    diagnostic_report: DiagnosticReport | None = None
    observability_data: dict[str, Any] | None = None
    workspace_artifacts: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        project: ProjectDefinition,
    ) -> "RoundResult":
        result = cls(
            round_number=int(payload.get("round_number") or 0),
            project=project,
            start_time=str(payload.get("start_time") or "").strip(),
            entry_stage=str(payload.get("entry_stage") or "architect").strip() or "architect",
            end_time=str(payload.get("end_time") or "").strip() or None,
            overall_result=str(payload.get("overall_result") or "pending"),
            factory_run_id=str(payload.get("factory_run_id") or "").strip() or None,
            failure_point=str(((payload.get("failure_analysis") or {}).get("failure_point")) or "").strip(),
            failure_evidence=str(((payload.get("failure_analysis") or {}).get("failure_evidence")) or "").strip(),
            root_cause=str(((payload.get("failure_analysis") or {}).get("root_cause")) or "").strip(),
        )
        stages_data = payload.get("stages") if isinstance(payload.get("stages"), dict) else {}
        for stage_name, stage_data in stages_data.items():
            if not isinstance(stage_data, dict):
                continue
            try:
                stage_result = StageResult(str(stage_data.get("result") or "failure"))
            except ValueError:
                stage_result = StageResult.FAILURE
            stage = StageExecution(
                stage_name=str(stage_data.get("stage_name") or stage_name),
                result=stage_result,
                start_time=str(stage_data.get("start_time") or "").strip(),
                end_time=str(stage_data.get("end_time") or "").strip(),
                duration_ms=int(stage_data.get("duration_ms") or 0),
                exit_code=int(stage_data.get("exit_code") or 0),
                stdout=str(stage_data.get("stdout") or "").strip(),
                stderr=str(stage_data.get("stderr") or "").strip(),
                error=str(stage_data.get("error") or "").strip(),
                artifacts=[str(item).strip() for item in (stage_data.get("artifacts") or []) if str(item).strip()],
            )
            setattr(result, f"{stage_name}_stage", stage)

        trace_payload = payload.get("trace")
        result.trace = (
            RoundTrace.from_dict(trace_payload) if isinstance(trace_payload, dict) and trace_payload else None
        )
        diagnostic_payload = payload.get("diagnostic_report")
        result.diagnostic_report = (
            DiagnosticReport.from_dict(diagnostic_payload)
            if isinstance(diagnostic_payload, dict) and diagnostic_payload
            else None
        )
        observability_data = payload.get("observability_data")
        result.observability_data = dict(observability_data) if isinstance(observability_data, dict) else None
        workspace_artifacts = payload.get("workspace_artifacts")
        result.workspace_artifacts = dict(workspace_artifacts) if isinstance(workspace_artifacts, dict) else {}
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_number": self.round_number,
            "project": {
                "id": self.project.id,
                "name": self.project.name,
                "category": self.project.category.value,
            },
            "start_time": self.start_time,
            "entry_stage": self.entry_stage,
            "end_time": self.end_time,
            "overall_result": self.overall_result,
            "factory_run_id": self.factory_run_id,
            "stages": {
                "architect": self._stage_to_dict(self.architect_stage),
                "pm": self._stage_to_dict(self.pm_stage),
                "chief_engineer": self._stage_to_dict(self.chief_engineer_stage),
                "director": self._stage_to_dict(self.director_stage),
                "qa": self._stage_to_dict(self.qa_stage),
            },
            "trace": self.trace.to_dict() if self.trace else None,
            "failure_analysis": {
                "failure_point": self.failure_point,
                "failure_evidence": self.failure_evidence,
                "root_cause": self.root_cause,
            },
            "diagnostic_report": self._diagnostic_to_dict(self.diagnostic_report),
            "observability_data": self.observability_data,
            "workspace_artifacts": self.workspace_artifacts,
        }

    def _diagnostic_to_dict(self, report: DiagnosticReport | None) -> dict[str, Any] | None:
        if not report:
            return None
        return {
            "round_number": report.round_number,
            "factory_run_id": report.factory_run_id,
            "failure_category": report.failure_category.value,
            "failure_point": report.failure_point,
            "timestamp": report.timestamp,
            "summary": report.summary,
            "evidence": report.evidence,
            "root_cause_analysis": report.root_cause_analysis,
            "suggested_fixes": report.suggested_fixes,
            "related_logs": report.related_logs,
        }

    def _stage_to_dict(self, stage: StageExecution | None) -> dict[str, Any] | None:
        if not stage:
            return None
        return {
            "stage_name": stage.stage_name,
            "result": stage.result.value,
            "start_time": stage.start_time,
            "end_time": stage.end_time,
            "duration_ms": stage.duration_ms,
            "exit_code": stage.exit_code,
            "error": stage.error,
            "artifacts": stage.artifacts,
        }
