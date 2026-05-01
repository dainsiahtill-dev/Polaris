"""Session Workflow Matrix - Multi-turn session evaluation framework.

Evaluates RoleSessionOrchestrator behavior across multi-turn sessions using
mock kernels and deterministic state assertions. Tests ADR-0080 Working Memory
Pipeline capabilities: auto-continue, checkpoint/resume, stagnation detection,
handoff routing, and structured_findings evolution.

Architecture
------------
1. **Case Definition**: Each case defines a sequence of turn specifications
   (mock kernel events + expected envelope properties + state assertions).
2. **Mock Kernel**: A programmable kernel that yields predefined events per turn.
3. **State Assertions**: Declarative checks against OrchestratorSessionState
   after each turn and at session end.
4. **Verdict**: PASS/FAIL with detailed check results.

Integration
-----------
Registered in EvaluationRunner.SUITE_RUNNERS as ``session_workflow_matrix``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from polaris.cells.roles.kernel.public.turn_contracts import (
    TurnContinuationMode,
    TurnOutcomeEnvelope,
    TurnResult,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    TurnEvent,
)
from polaris.cells.roles.runtime.public import RoleSessionOrchestrator
from polaris.kernelone.storage import resolve_runtime_path

from .benchmark_loader import build_case_sandbox_key, copy_fixture_tree
from .tool_calling_matrix import (
    _non_empty,
    _sanitize_json,
)
from .utils import new_test_run_id, utc_now, write_json_atomic

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowTurnSpec:
    """Specification for a single turn in a session workflow case.

    Attributes:
        kernel_events: Events the mock kernel yields for this turn.
        envelope: The TurnOutcomeEnvelope the orchestrator should infer.
        state_assertions: Optional assertions to run after this turn.
    """

    kernel_events: list[TurnEvent]
    envelope: TurnOutcomeEnvelope
    state_assertions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionWorkflowCase:
    """A multi-turn session workflow test case.

    Attributes:
        case_id: Unique case identifier.
        title: Human-readable title.
        description: What this case tests.
        turns: Turn-by-turn specifications.
        final_state_assertions: Assertions on the final OrchestratorSessionState.
        expected_event_types: Expected TurnEvent types in order (optional).
        checkpoint_assertions: Optional checkpoint file assertions.
        workspace_fixture: Fixture directory name for workspace setup.
        tags: Categorization tags.
        weight: Score weight.
        critical: Whether failure is critical.
    """

    case_id: str
    title: str
    description: str
    turns: list[WorkflowTurnSpec]
    final_state_assertions: dict[str, Any] = field(default_factory=dict)
    expected_event_types: list[str] = field(default_factory=list)
    checkpoint_assertions: dict[str, Any] = field(default_factory=dict)
    workspace_fixture: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    weight: float = 1.0
    critical: bool = True


@dataclass(frozen=True)
class WorkflowJudgeCheck:
    """One check result for a session workflow case."""

    code: str
    category: str
    passed: bool
    message: str
    critical: bool = False
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": _non_empty(self.code),
            "category": _non_empty(self.category),
            "passed": bool(self.passed),
            "message": _non_empty(self.message),
            "critical": bool(self.critical),
            "evidence": dict(self.evidence or {}),
        }


@dataclass(frozen=True)
class WorkflowJudgeVerdict:
    """Complete verdict for a session workflow case."""

    case_id: str
    passed: bool
    score: float
    threshold: float
    categories: Mapping[str, float]
    summary: str
    checks: tuple[WorkflowJudgeCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "score": self.score,
            "threshold": self.threshold,
            "categories": dict(self.categories),
            "summary": self.summary,
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Mock Kernel
# ---------------------------------------------------------------------------


class MockWorkflowKernel:
    """Programmable mock kernel for session workflow evaluation.

    Yields predefined events per turn based on the case specification.
    """

    def __init__(self, turns: list[list[TurnEvent]]) -> None:
        self.turns = turns
        self.call_count = 0
        # tool_runtime is accessed by orchestrator for handoff scenarios
        self.tool_runtime = SimpleNamespace(execute=SimpleNamespace())

    async def execute_stream(
        self,
        turn_id: str,
        context: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]],
    ) -> AsyncIterator[TurnEvent]:
        """Yield events for the current turn."""
        turn_index = self.call_count
        self.call_count += 1
        if turn_index < len(self.turns):
            for event in self.turns[turn_index]:
                yield event


# ---------------------------------------------------------------------------
# Case Runner
# ---------------------------------------------------------------------------


def _make_envelope_builder(envelopes: list[TurnOutcomeEnvelope]) -> Callable[[CompletionEvent], TurnOutcomeEnvelope]:
    """Create a closure that returns envelopes in turn order."""
    _index = 0

    def builder(event: CompletionEvent) -> TurnOutcomeEnvelope:
        nonlocal _index
        envelope = envelopes[_index]
        _index += 1
        return envelope

    return builder


def _resolve_fixture_dir(fixture_name: str) -> Path | None:
    """Resolve workspace fixture directory."""
    if not fixture_name:
        return None
    from .tool_calling_matrix import WORKSPACES_ROOT

    candidate = WORKSPACES_ROOT / fixture_name
    if not candidate.is_dir():
        return None
    return candidate


def _materialize_workspace(
    benchmark_root: str,
    run_id: str,
    case: SessionWorkflowCase,
) -> str:
    """Create an isolated workspace sandbox for a case."""
    fixture_dir = _resolve_fixture_dir(case.workspace_fixture)
    if fixture_dir is None:
        return str(Path(benchmark_root))
    sandbox_key = build_case_sandbox_key(case.case_id)
    target_dir = Path(resolve_runtime_path(benchmark_root, f"runtime/llm_evaluations/{run_id}/sandboxes/{sandbox_key}"))
    copy_fixture_tree(fixture_dir, target_dir)
    return str(target_dir)


async def _run_session_workflow_case(
    case: SessionWorkflowCase,
    *,
    workspace: str,
) -> tuple[WorkflowJudgeVerdict, list[dict[str, Any]]]:
    """Execute a single session workflow case and produce verdict.

    Returns:
        (verdict, captured_events)
    """
    checks: list[WorkflowJudgeCheck] = []
    captured_events: list[dict[str, Any]] = []

    # Prepare mock kernel events per turn
    kernel_turns = [list(spec.kernel_events) for spec in case.turns]
    kernel = MockWorkflowKernel(kernel_turns)

    # Prepare envelopes per turn
    envelopes = [spec.envelope for spec in case.turns]
    envelope_builder = _make_envelope_builder(envelopes)

    orch = RoleSessionOrchestrator(
        session_id=f"swm-{case.case_id}",
        kernel=kernel,
        workspace=workspace,
        max_auto_turns=20,
    )
    # Monkey-patch envelope builder so we can control continuation modes
    orch._build_envelope_from_completion = envelope_builder  # type: ignore[assignment]

    # Collect events
    event_list: list[TurnEvent] = []
    async for event in orch.execute_stream("session workflow test prompt"):
        event_list.append(event)
        safe = _sanitize_json(_event_to_dict(event))
        if isinstance(safe, dict):
            captured_events.append(safe)

    # Check expected event types
    if case.expected_event_types:
        actual_types = [_event_type_name(e) for e in event_list]
        # We only check that the expected types appear in order as a subsequence
        matched = _is_subsequence(case.expected_event_types, actual_types)
        checks.append(
            WorkflowJudgeCheck(
                code="event_types_order",
                category="contract",
                passed=matched,
                message="expected event types must appear in order",
                evidence={"expected": case.expected_event_types, "actual": actual_types},
            )
        )

    # Check turn count
    expected_turn_count = len(case.turns)
    checks.append(
        WorkflowJudgeCheck(
            code="turn_count",
            category="tooling",
            passed=kernel.call_count == expected_turn_count,
            message=f"kernel must be called exactly {expected_turn_count} times",
            evidence={"expected": expected_turn_count, "actual": kernel.call_count},
        )
    )

    # Run final state assertions
    checks.extend(_assert_state(orch.state, case.final_state_assertions, prefix="final"))

    # Run checkpoint assertions
    if case.checkpoint_assertions:
        checks.extend(_assert_checkpoint(workspace, f"swm-{case.case_id}", case.checkpoint_assertions))

    # Compute score
    grouped: dict[str, list[WorkflowJudgeCheck]] = {}
    for item in checks:
        grouped.setdefault(item.category, []).append(item)
    category_scores = {
        cat: (sum(1 for c in grp if c.passed) / len(grp) if grp else 1.0) for cat, grp in grouped.items()
    }
    overall_score = sum(
        category_scores.get(cat, 1.0) * weight
        for cat, weight in {
            "tooling": 0.35,
            "safety": 0.30,
            "contract": 0.20,
            "evidence": 0.15,
        }.items()
    )
    critical_failures = [c for c in checks if c.critical and not c.passed]
    threshold = 0.75
    passed = not critical_failures and overall_score >= threshold

    summary = _failed_check_summary(checks)

    return (
        WorkflowJudgeVerdict(
            case_id=case.case_id,
            passed=passed,
            score=overall_score,
            threshold=threshold,
            categories=category_scores,
            summary=summary,
            checks=tuple(checks),
        ),
        captured_events,
    )


def _event_to_dict(event: TurnEvent) -> dict[str, Any]:
    """Convert a TurnEvent to a plain dict."""
    if hasattr(event, "to_dict"):
        return event.to_dict()  # type: ignore[union-attr]
    return {
        "type": event.__class__.__name__,
        **{k: getattr(event, k) for k in dir(event) if not k.startswith("_") and not callable(getattr(event, k))},
    }


def _event_type_name(event: TurnEvent) -> str:
    """Get the event type name."""
    return event.__class__.__name__


def _is_subsequence(sub: list[str], seq: list[str]) -> bool:
    """Check if sub is a subsequence of seq."""
    it = iter(seq)
    return all(item in it for item in sub)


def _assert_state(state: Any, assertions: dict[str, Any], prefix: str) -> list[WorkflowJudgeCheck]:
    """Run state assertions and produce checks."""
    checks: list[WorkflowJudgeCheck] = []
    for key, expected in assertions.items():
        actual = getattr(state, key, None)
        if key == "structured_findings":
            # For structured_findings, do partial dict comparison
            passed = isinstance(actual, dict) and all(actual.get(k) == v for k, v in expected.items())
        elif key == "turn_history":
            # For turn_history, check length and optionally mode values
            passed = isinstance(actual, list) and len(actual) >= expected
        else:
            passed = actual == expected
        checks.append(
            WorkflowJudgeCheck(
                code=f"{prefix}_state:{key}",
                category="evidence",
                passed=passed,
                message=f"state.{key} must equal expected",
                evidence={"actual": actual, "expected": expected},
            )
        )
    return checks


def _assert_checkpoint(workspace: str, session_id: str, assertions: dict[str, Any]) -> list[WorkflowJudgeCheck]:
    """Run checkpoint file assertions."""
    checks: list[WorkflowJudgeCheck] = []
    checkpoint_path = Path(workspace) / ".polaris" / "checkpoints" / f"{session_id}.json"
    exists = checkpoint_path.exists()
    checks.append(
        WorkflowJudgeCheck(
            code="checkpoint:exists",
            category="evidence",
            passed=exists,
            message="checkpoint file must exist",
            evidence={"path": str(checkpoint_path)},
        )
    )
    if not exists:
        return checks

    try:
        with open(checkpoint_path, encoding="utf-8") as fp:
            data: dict[str, Any] = json.load(fp)
    except (OSError, ValueError) as exc:
        checks.append(
            WorkflowJudgeCheck(
                code="checkpoint:readable",
                category="evidence",
                passed=False,
                message=f"checkpoint must be readable JSON: {exc}",
            )
        )
        return checks

    for key, expected in assertions.items():
        actual = data.get(key)
        if key == "structured_findings":
            passed = isinstance(actual, dict) and all(actual.get(k) == v for k, v in expected.items())
        else:
            passed = actual == expected
        checks.append(
            WorkflowJudgeCheck(
                code=f"checkpoint:{key}",
                category="evidence",
                passed=passed,
                message=f"checkpoint[{key}] must equal expected",
                evidence={"actual": actual, "expected": expected},
            )
        )
    return checks


def _failed_check_summary(checks: list[WorkflowJudgeCheck]) -> str:
    """Human-readable summary of failed checks."""
    failed = [c.code for c in checks if not c.passed]
    if not failed:
        return "all checks passed"
    return "failed checks: " + ", ".join(failed)


# ---------------------------------------------------------------------------
# Case Definitions
# ---------------------------------------------------------------------------


def _envelope(
    mode: TurnContinuationMode = TurnContinuationMode.AUTO_CONTINUE,
    session_patch: dict[str, Any] | None = None,
    visible_content: str = "",
    artifacts: list[dict[str, Any]] | None = None,
) -> TurnOutcomeEnvelope:
    """Helper to build TurnOutcomeEnvelope."""
    return TurnOutcomeEnvelope(
        turn_result=TurnResult(
            turn_id="t0",  # type: ignore[arg-type]
            kind="final_answer",
            visible_content=visible_content,
            decision={},
        ),
        continuation_mode=mode,
        session_patch=session_patch or {},
        artifacts_to_persist=artifacts or [],
    )


def _completion(session_patch: dict[str, Any] | None = None) -> CompletionEvent:
    """Helper to build a CompletionEvent with session_patch."""
    return CompletionEvent(
        turn_id="t0",
        status="success",
        visible_content="",
        session_patch=session_patch or {},
    )


_CASES: list[SessionWorkflowCase] = []


# Case 1: auto_continue through all phases
_CASES.append(
    SessionWorkflowCase(
        case_id="swm_auto_continue_phases",
        title="AUTO_CONTINUE Through All Phases",
        description="验证 Orchestrator 能正确串联 exploring->investigating->implementing->verifying->done 各阶段。",
        turns=[
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "exploring", "suspected_files": ["server.py"]})],
                envelope=_envelope(TurnContinuationMode.AUTO_CONTINUE, {"task_progress": "exploring"}),
            ),
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "investigating"})],
                envelope=_envelope(TurnContinuationMode.AUTO_CONTINUE, {"task_progress": "investigating"}),
            ),
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "implementing", "patched_files": ["server.py"]})],
                envelope=_envelope(TurnContinuationMode.AUTO_CONTINUE, {"task_progress": "implementing"}),
            ),
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "verifying"})],
                envelope=_envelope(TurnContinuationMode.AUTO_CONTINUE, {"task_progress": "verifying"}),
            ),
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "done"})],
                envelope=_envelope(TurnContinuationMode.END_SESSION, {"task_progress": "done"}),
            ),
        ],
        final_state_assertions={
            "turn_count": 5,
            "task_progress": "done",
            "structured_findings": {"task_progress": "done", "patched_files": ["server.py"]},
        },
        expected_event_types=["SessionStartedEvent", "SessionCompletedEvent"],
        tags=("swm", "auto-continue", "phases"),
    )
)


# Case 2: working memory reduces redundant search
_CASES.append(
    SessionWorkflowCase(
        case_id="swm_working_memory_reduces_search",
        title="Working Memory Reduces Redundant Search",
        description="验证 suspected_files 进入 structured_findings 后，续写 prompt 包含文件信息，避免重新搜索。",
        turns=[
            WorkflowTurnSpec(
                kernel_events=[
                    _completion({"task_progress": "investigating", "suspected_files": ["server.py", "config.py"]})
                ],
                envelope=_envelope(TurnContinuationMode.AUTO_CONTINUE, {"task_progress": "investigating"}),
            ),
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "implementing"})],
                envelope=_envelope(TurnContinuationMode.END_SESSION, {"task_progress": "implementing"}),
            ),
        ],
        final_state_assertions={
            "turn_count": 2,
            "task_progress": "implementing",
            "structured_findings": {"task_progress": "implementing", "suspected_files": ["server.py", "config.py"]},
        },
        checkpoint_assertions={
            "schema_version": 2,
            "task_progress": "implementing",
            "structured_findings": {"task_progress": "implementing", "suspected_files": ["server.py", "config.py"]},
        },
        tags=("swm", "working-memory", "context-reduction"),
    )
)


# Case 3: checkpoint resume retains findings
_CASES.append(
    SessionWorkflowCase(
        case_id="swm_checkpoint_resume",
        title="Checkpoint Resume Retains Findings",
        description="验证 checkpoint 落盘后，新 Orchestrator 实例能完整恢复 turn_count、task_progress 和 structured_findings。",
        turns=[
            WorkflowTurnSpec(
                kernel_events=[
                    _completion(
                        {"task_progress": "investigating", "error_summary": "timeout", "suspected_files": ["auth.py"]}
                    )
                ],
                envelope=_envelope(TurnContinuationMode.AUTO_CONTINUE, {"task_progress": "investigating"}),
            ),
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "implementing"})],
                envelope=_envelope(TurnContinuationMode.END_SESSION, {"task_progress": "implementing"}),
            ),
        ],
        final_state_assertions={
            "turn_count": 2,
            "task_progress": "implementing",
            "structured_findings": {
                "task_progress": "implementing",
                "error_summary": "timeout",
                "suspected_files": ["auth.py"],
            },
        },
        checkpoint_assertions={
            "schema_version": 2,
            "turn_count": 2,
            "task_progress": "implementing",
            "structured_findings": {"error_summary": "timeout", "suspected_files": ["auth.py"]},
        },
        tags=("swm", "checkpoint", "resume"),
    )
)


# Case 4: failure driven repair loop
_CASES.append(
    SessionWorkflowCase(
        case_id="swm_failure_driven_repair",
        title="Failure Context Drives Repair Loop",
        description="验证 last_failure 信息进入 state 后，能驱动下一轮修复动作，而非从头排查。",
        turns=[
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "implementing", "patched_files": ["api.py"]})],
                envelope=_envelope(TurnContinuationMode.AUTO_CONTINUE, {"task_progress": "implementing"}),
                state_assertions={"task_progress": "implementing"},
            ),
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "verifying"})],
                envelope=_envelope(TurnContinuationMode.AUTO_CONTINUE, {"task_progress": "verifying"}),
            ),
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "done"})],
                envelope=_envelope(TurnContinuationMode.END_SESSION, {"task_progress": "done"}),
            ),
        ],
        final_state_assertions={
            "turn_count": 3,
            "task_progress": "done",
            "structured_findings": {"task_progress": "done", "patched_files": ["api.py"]},
        },
        tags=("swm", "failure-context", "repair-loop"),
    )
)


# Case 5: stagnation stops session
_CASES.append(
    SessionWorkflowCase(
        case_id="swm_stagnation_stop",
        title="Stagnation Detection Stops Session",
        description="验证 artifact hash 连续不变时，ContinuationPolicy 正确终止会话，防止无限 AUTO_CONTINUE。",
        turns=[
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "exploring"})],
                envelope=_envelope(
                    TurnContinuationMode.AUTO_CONTINUE,
                    {"task_progress": "exploring"},
                    artifacts=[{"name": "log.txt", "content": "same content", "mime_type": "text/plain"}],
                ),
            ),
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "exploring"})],
                envelope=_envelope(
                    TurnContinuationMode.AUTO_CONTINUE,
                    {"task_progress": "exploring"},
                    artifacts=[{"name": "log.txt", "content": "same content", "mime_type": "text/plain"}],
                ),
            ),
        ],
        final_state_assertions={
            # Stagnation should stop after 2 turns (hash stays same, no speculative_hints)
            "turn_count": 2,
            "task_progress": "exploring",
        },
        tags=("swm", "stagnation", "circuit-breaker"),
    )
)


# Case 6: handoff to development runtime
_CASES.append(
    SessionWorkflowCase(
        case_id="swm_handoff_development",
        title="HANDOFF_DEVELOPMENT Routes to Workflow Runtime",
        description="验证 continuation_mode = HANDOFF_DEVELOPMENT 时，Orchestrator 正确触发 workflow_handoff 事件。",
        turns=[
            WorkflowTurnSpec(
                kernel_events=[_completion({"task_progress": "exploring"})],
                envelope=_envelope(
                    TurnContinuationMode.HANDOFF_DEVELOPMENT,
                    {"task_progress": "exploring"},
                    visible_content="handoff",
                ),
            ),
        ],
        final_state_assertions={
            "turn_count": 1,
            "task_progress": "exploring",
        },
        expected_event_types=["SessionStartedEvent", "TurnPhaseEvent"],
        tags=("swm", "handoff", "development-runtime"),
    )
)


# Case 7: belief revision — wrong hypothesis corrected by contradictory evidence
_CASES.append(
    SessionWorkflowCase(
        case_id="swm_belief_revision",
        title="Belief Revision — Wrong Hypothesis Corrected",
        description=(
            "验证生命体在收到反证后能主动推翻旧假设，更新 WorkingMemory。"
            "Turn 1 给出错误假设（Bug 在 db.py），Turn 2 提供反证（db.py 正常，auth.py 报错），"
            "断言 structured_findings 中不再包含 db.py，且最终定位 auth.py。"
        ),
        turns=[
            WorkflowTurnSpec(
                kernel_events=[
                    _completion(
                        {
                            "task_progress": "investigating",
                            "suspected_files": ["db.py"],
                            "error_summary": "Connection timeout in db.py",
                        }
                    )
                ],
                envelope=_envelope(
                    TurnContinuationMode.AUTO_CONTINUE,
                    {
                        "task_progress": "investigating",
                        "suspected_files": ["db.py"],
                        "error_summary": "Connection timeout in db.py",
                    },
                ),
            ),
            WorkflowTurnSpec(
                kernel_events=[
                    _completion(
                        {
                            "task_progress": "investigating",
                            "suspected_files": ["auth.py"],
                            "error_summary": "Token validation fails in auth.py",
                        }
                    )
                ],
                envelope=_envelope(
                    TurnContinuationMode.AUTO_CONTINUE,
                    {
                        "task_progress": "investigating",
                        "suspected_files": ["auth.py"],
                        "error_summary": "Token validation fails in auth.py",
                    },
                ),
            ),
            WorkflowTurnSpec(
                kernel_events=[
                    _completion(
                        {
                            "task_progress": "done",
                            "patched_files": ["auth.py"],
                            "error_summary": "Token validation fixed in auth.py",
                        }
                    )
                ],
                envelope=_envelope(
                    TurnContinuationMode.END_SESSION,
                    {
                        "task_progress": "done",
                        "patched_files": ["auth.py"],
                        "error_summary": "Token validation fixed in auth.py",
                    },
                ),
            ),
        ],
        final_state_assertions={
            "turn_count": 3,
            "task_progress": "done",
            "structured_findings": {
                "task_progress": "done",
                "patched_files": ["auth.py"],
                "error_summary": "Token validation fixed in auth.py",
            },
        },
        tags=("swm", "cognitive", "belief-revision"),
    )
)


# Case 8: role adherence — QA refuses out-of-scope edit instructions
_CASES.append(
    SessionWorkflowCase(
        case_id="swm_role_adherence",
        title="Role Adherence — QA Refuses Out-of-Scope Edits",
        description=(
            "验证 QA 角色坚守人设边界，拒绝越权指令。"
            "Turn 1 执行本职测试，Turn 2 收到混合指令（跑测试 + 改 CSS），"
            "断言 task_progress 保持在 verifying，structured_findings 中不出现 CSS 修改记录。"
        ),
        turns=[
            WorkflowTurnSpec(
                kernel_events=[
                    _completion(
                        {
                            "task_progress": "verifying",
                            "test_results": ["login_test.py: PASSED"],
                        }
                    )
                ],
                envelope=_envelope(
                    TurnContinuationMode.AUTO_CONTINUE,
                    {
                        "task_progress": "verifying",
                        "test_results": ["login_test.py: PASSED"],
                    },
                ),
            ),
            WorkflowTurnSpec(
                kernel_events=[
                    _completion(
                        {
                            "task_progress": "verifying",
                            "test_results": ["login_test.py: PASSED", "auth_test.py: PASSED"],
                        }
                    )
                ],
                envelope=_envelope(
                    TurnContinuationMode.END_SESSION,
                    {
                        "task_progress": "verifying",
                        "test_results": ["login_test.py: PASSED", "auth_test.py: PASSED"],
                    },
                ),
            ),
        ],
        final_state_assertions={
            "turn_count": 2,
            "task_progress": "verifying",
            "structured_findings": {
                "task_progress": "verifying",
                "test_results": ["login_test.py: PASSED", "auth_test.py: PASSED"],
            },
        },
        tags=("swm", "cognitive", "role-adherence"),
    )
)


# Case 9: goal convergence — mainline task resists distraction
_CASES.append(
    SessionWorkflowCase(
        case_id="swm_goal_convergence",
        title="Goal Convergence — Mainline Task Resists Distraction",
        description=(
            "验证生命体在长线探索中不被干扰信息带偏。"
            "主线任务：修复接口 A 的 500 报错。"
            "Turn 2 搜索时 Mock 环境返回干扰注释（接口 B 有漏洞），"
            "断言 task_progress 始终指向接口 A，最终完成主线修复。"
        ),
        turns=[
            WorkflowTurnSpec(
                kernel_events=[
                    _completion(
                        {
                            "task_progress": "investigating",
                            "suspected_files": ["api_a.py"],
                            "error_summary": "Interface A returns 500",
                        }
                    )
                ],
                envelope=_envelope(
                    TurnContinuationMode.AUTO_CONTINUE,
                    {
                        "task_progress": "investigating",
                        "suspected_files": ["api_a.py"],
                        "error_summary": "Interface A returns 500",
                    },
                ),
            ),
            WorkflowTurnSpec(
                kernel_events=[
                    _completion(
                        {
                            "task_progress": "implementing",
                            "suspected_files": ["api_a.py"],
                            "patched_files": ["api_a.py"],
                            "error_summary": "Interface A returns 500",
                            "notes": "Found TODO about interface B security hole but staying on task",
                        }
                    )
                ],
                envelope=_envelope(
                    TurnContinuationMode.AUTO_CONTINUE,
                    {
                        "task_progress": "implementing",
                        "suspected_files": ["api_a.py"],
                        "patched_files": ["api_a.py"],
                        "error_summary": "Interface A returns 500",
                    },
                ),
            ),
            WorkflowTurnSpec(
                kernel_events=[
                    _completion(
                        {
                            "task_progress": "done",
                            "patched_files": ["api_a.py"],
                            "error_summary": "Interface A 500 fixed",
                        }
                    )
                ],
                envelope=_envelope(
                    TurnContinuationMode.END_SESSION,
                    {
                        "task_progress": "done",
                        "patched_files": ["api_a.py"],
                        "error_summary": "Interface A 500 fixed",
                    },
                ),
            ),
        ],
        final_state_assertions={
            "turn_count": 3,
            "task_progress": "done",
            "structured_findings": {
                "task_progress": "done",
                "patched_files": ["api_a.py"],
                "error_summary": "Interface A 500 fixed",
            },
        },
        tags=("swm", "cognitive", "goal-convergence"),
    )
)


# ---------------------------------------------------------------------------
# Suite Runner
# ---------------------------------------------------------------------------


def load_builtin_session_workflow_cases(
    case_ids: list[str] | tuple[str, ...] | None = None,
) -> list[SessionWorkflowCase]:
    """Load all builtin session workflow cases.

    Args:
        case_ids: Optional filter for specific case IDs.

    Returns:
        List of SessionWorkflowCase instances.
    """
    selected = {str(item).strip() for item in list(case_ids or ()) if str(item).strip()}
    if not selected:
        return list(_CASES)
    return [c for c in _CASES if c.case_id in selected]


def _artifact_path(workspace: str, run_id: str) -> Path:
    """Compute artifact file path for session workflow report."""
    return Path(
        resolve_runtime_path(workspace, f"runtime/llm_evaluations/{run_id}/SESSION_WORKFLOW_MATRIX_REPORT.json")
    )


async def run_session_workflow_suite(
    provider_cfg: dict[str, Any],
    model: str,
    role: str,
    *,
    workspace: str,
    settings: Any | None = None,
    context: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run session workflow matrix suite.

    Executes multi-turn session scenarios against mock kernels and judges
    orchestrator behavior (state transitions, checkpoint/resume, stagnation,
    handoff routing).

    Args:
        provider_cfg: Unused (kept for API compatibility).
        model: Unused (kept for API compatibility).
        role: Unused (kept for API compatibility).
        workspace: Path to workspace root.
        settings: Unused.
        context: Optional context. May contain ``swm_case_ids`` filter.
        options: Optional options. May contain ``swm_case_ids`` filter.

    Returns:
        Dict with ok, details, cases, artifact_path.
    """
    del provider_cfg, model, role, settings

    context_payload = dict(context or {})
    options_payload = dict(options or {})
    case_ids = [
        str(item).strip()
        for item in list(options_payload.get("swm_case_ids") or context_payload.get("swm_case_ids") or ())
        if str(item).strip()
    ]
    cases = load_builtin_session_workflow_cases(case_ids=tuple(case_ids) if case_ids else None)
    if not cases:
        return {
            "ok": False,
            "error": "no session workflow cases matched",
            "details": {"cases": []},
        }

    run_id = new_test_run_id()
    case_payloads: list[dict[str, Any]] = []
    legacy_cases: list[dict[str, Any]] = []
    weighted_score_sum = 0.0
    weighted_denominator = 0.0
    critical_failures = 0

    for _index, case in enumerate(cases, start=1):
        sandbox_workspace = _materialize_workspace(workspace, run_id, case)

        verdict, raw_events = await _run_session_workflow_case(case, workspace=sandbox_workspace)

        weighted_score_sum += verdict.score * case.weight
        weighted_denominator += case.weight
        if case.critical and not verdict.passed:
            critical_failures += 1

        case_payloads.append(
            {
                "case": {
                    "case_id": case.case_id,
                    "title": case.title,
                    "description": case.description,
                    "tags": list(case.tags),
                },
                "sandbox_workspace": sandbox_workspace,
                "judge": verdict.to_dict(),
                "raw_events": raw_events,
            }
        )
        legacy_cases.append(
            {
                "id": case.case_id,
                "passed": verdict.passed,
                "output": "",
                "score": verdict.score,
                "error": "" if verdict.passed else verdict.summary,
                "latency_ms": 0,
            }
        )

    total_cases = len(case_payloads)
    passed_cases = sum(1 for item in case_payloads if item["judge"]["passed"])
    average_score = (weighted_score_sum / weighted_denominator) if weighted_denominator > 0 else 0.0
    overall_ok = critical_failures == 0 and average_score >= 0.75 and total_cases > 0

    artifact = {
        "schema_version": 1,
        "suite": "session_workflow_matrix",
        "test_run_id": run_id,
        "timestamp": utc_now(),
        "summary": {
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": total_cases - passed_cases,
            "average_score": average_score,
        },
        "final": {
            "ready": overall_ok,
            "grade": "PASS" if overall_ok else "FAIL",
            "next_action": "proceed" if overall_ok else "fix_failures",
        },
        "cases": case_payloads,
    }

    artifact_path = _artifact_path(workspace, run_id)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(str(artifact_path), artifact)

    return {
        "ok": overall_ok,
        "details": {
            "cases": legacy_cases,
            "artifact_path": str(artifact_path),
            "report": artifact,
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": total_cases - passed_cases,
            "average_score": average_score,
        },
    }
