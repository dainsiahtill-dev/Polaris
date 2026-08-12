"""Core types for runtime_dispatch (cell-private)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..contracts import (
    CompositionResult,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairExecutionResult,
    RepairPlan,
)
from ..executor import DeleteFileFn, EditFileFn, TransactionalRepairExecutor, WriteFileFn
from ..policy_gate import PolicyDecision

RuntimePlannerFn = Callable[
    [Mapping[str, str], Sequence[str], Sequence[RepairAdvisorNote] | None, str],
    "RuntimeRepairPlanning",
]
RuntimeTypedPlannerFn = Callable[
    [
        Mapping[str, str],
        Sequence[RepairDiagnostic],
        Sequence[str],
        Sequence[RepairAdvisorNote] | None,
        str,
    ],
    "RuntimeRepairPlanning",
]
RuntimeRunnerFn = Callable[
    [
        str | Path,
        Mapping[str, str],
        Sequence[str],
        WriteFileFn,
        EditFileFn | None,
        DeleteFileFn | None,
        Sequence[str] | None,
        Sequence[RepairAdvisorNote] | None,
        str,
    ],
    "RuntimeRepairRun",
]
RuntimeTypedRunnerFn = Callable[
    [
        str | Path,
        Mapping[str, str],
        Sequence[RepairDiagnostic],
        Sequence[str],
        WriteFileFn,
        EditFileFn | None,
        DeleteFileFn | None,
        Sequence[str] | None,
        Sequence[RepairAdvisorNote] | None,
        str,
    ],
    "RuntimeRepairRun",
]


@dataclass(frozen=True)
class RuntimeRepairPlanning:
    """Language-neutral planning result for one deterministic repair source tool."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RuntimeRepairRun:
    """Language-neutral execution result for one deterministic repair source tool."""

    planning: RuntimeRepairPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RuntimeRepairBinding:
    """Registered runtime planner/runner binding for one source tool."""

    source_tool: str
    language: str
    rule_id: str
    planner: RuntimePlannerFn
    runner: RuntimeRunnerFn
    typed_planner: RuntimeTypedPlannerFn | None = None
    typed_runner: RuntimeTypedRunnerFn | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            "source_tool": self.source_tool,
            "language": self.language,
            "rule_id": self.rule_id,
        }


@dataclass(frozen=True)
class _DeleterBoundRepairExecutor(TransactionalRepairExecutor):
    deleter: DeleteFileFn

    def execute(
        self,
        *,
        workspace: Path,
        plan: RepairPlan,
        composition: CompositionResult,
        writer: WriteFileFn | None = None,
        editor: EditFileFn | None = None,
        deleter: DeleteFileFn | None = None,
    ) -> RepairExecutionResult:
        del deleter
        return TransactionalRepairExecutor().execute(
            workspace=workspace,
            plan=plan,
            composition=composition,
            writer=writer,
            editor=editor,
            deleter=self.deleter,
        )
