"""Cognitive-pipeline benchmark executors (TC-COG-001..004).

Cautious execution policy, cognitive orchestrator session isolation,
cognitive middleware context merge, and evolution-engine triggers.
"""

from __future__ import annotations

import asyncio
import json
import time

from polaris.kernelone.benchmark.holographic.stats import _now_iso, _perf_ms
from polaris.kernelone.benchmark.holographic_models import HolographicCase
from polaris.kernelone.benchmark.holographic_stats import summarize_samples
from polaris.kernelone.cognitive.evolution.engine import EvolutionEngine
from polaris.kernelone.cognitive.evolution.models import TriggerType
from polaris.kernelone.cognitive.evolution.store import EvolutionStore
from polaris.kernelone.cognitive.execution.cautious_policy import CautiousExecutionPolicy
from polaris.kernelone.cognitive.middleware import CognitiveMiddleware
from polaris.kernelone.cognitive.orchestrator import CognitiveOrchestrator
from polaris.kernelone.cognitive.perception.models import (
    IntentGraph,
    IntentNode,
    UncertaintyAssessment,
)
from polaris.kernelone.cognitive.types import ExecutionPath as CognitiveExecutionPath


def _reset_cognitive_globals_for_benchmark() -> None:
    import polaris.kernelone.cognitive.context as ctx_module

    # Reset the module-level singletons via __dict__ so the benchmark runs in
    # isolation. These globals are managed dynamically inside cognitive.context,
    # so mypy cannot see them as attributes; __dict__ assignment is the
    # type-clean equivalent of `ctx_module._global_x = None` (no type: ignore).
    ctx_module.__dict__["_global_session_manager"] = None
    ctx_module.__dict__["_global_workspace"] = None


def _build_cognitive_intent_graph(*, session_id: str, intent_type: str, confidence: float = 0.9) -> IntentGraph:
    now = _now_iso()
    node = IntentNode(
        node_id=f"{session_id}-node-1",
        intent_type=intent_type,
        content=f"{intent_type}-content",
        confidence=confidence,
        source_event_id=f"{session_id}-event-1",
    )
    return IntentGraph(
        graph_id=f"{session_id}-graph",
        session_id=session_id,
        created_at=now,
        updated_at=now,
        nodes=(node,),
        edges=(),
        chains=(),
    )


def _build_cognitive_uncertainty(score: float) -> UncertaintyAssessment:
    bounded = min(max(float(score), 0.0), 1.0)
    return UncertaintyAssessment(
        uncertainty_score=bounded,
        confidence_lower=max(0.0, 1.0 - bounded),
        confidence_upper=min(1.0, 1.0 - bounded * 0.2),
        recommended_action="full_pipe" if bounded >= 0.6 else "fast_think",
        uncertainty_factors=("benchmark",),
    )


async def _exec_tc_cog_001(case: HolographicCase) -> dict[str, float]:
    policy = CautiousExecutionPolicy()
    total = max(1000, case.min_samples)
    decision_ms: list[float] = []
    path_ok = 0
    override_ok = 0

    for index in range(total):
        high_uncertainty = index % 5 == 0
        graph = _build_cognitive_intent_graph(
            session_id=f"cog-policy-{index}",
            intent_type="create_file",
        )
        uncertainty = _build_cognitive_uncertainty(0.9 if high_uncertainty else 0.1)
        started = time.perf_counter_ns()
        recommendation = await policy.evaluate(intent_graph=graph, uncertainty=uncertainty)
        decision_ms.append(_perf_ms(started))

        if high_uncertainty:
            if recommendation.path == CognitiveExecutionPath.FULL_PIPE:
                path_ok += 1
            if recommendation.uncertainty_threshold_exceeded:
                override_ok += 1
        else:
            if recommendation.path == CognitiveExecutionPath.FAST_THINK:
                path_ok += 1
            if not recommendation.uncertainty_threshold_exceeded:
                override_ok += 1

    stats = summarize_samples(decision_ms, warmup_rounds=case.warmup_rounds)
    return {
        "path_accuracy_percent": (path_ok / total) * 100.0,
        "override_accuracy_percent": (override_ok / total) * 100.0,
        "decision_p99_ms": stats.p99,
    }


async def _exec_tc_cog_002(case: HolographicCase) -> dict[str, float]:
    import tempfile

    _reset_cognitive_globals_for_benchmark()
    session_count = 8
    turns_per_session = max(3, min(6, case.min_samples // 20 or 3))
    process_samples_ms: list[float] = []

    with tempfile.TemporaryDirectory(prefix="holo-cog-002-") as directory:
        orchestrator = CognitiveOrchestrator(workspace=directory, enable_evolution=True, enable_personality=True)

        async def _worker(session_id: str) -> None:
            for turn in range(turns_per_session):
                started = time.perf_counter_ns()
                await orchestrator.process(
                    message=f"{session_id}-msg-{turn}",
                    session_id=session_id,
                    role_id="director",
                )
                process_samples_ms.append(_perf_ms(started))

        await asyncio.gather(*(_worker(f"bench-cog-{index}") for index in range(session_count)))

        count_ok = 0
        isolation_ok = 0
        for index in range(session_count):
            session_id = f"bench-cog-{index}"
            context = orchestrator.get_session(session_id)
            if context is None:
                continue
            if len(context.conversation_history) == turns_per_session:
                count_ok += 1
            if all(turn.message.startswith(session_id) for turn in context.conversation_history):
                isolation_ok += 1

    stats = summarize_samples(process_samples_ms, warmup_rounds=case.warmup_rounds)
    return {
        "session_count_accuracy_percent": (count_ok / session_count) * 100.0,
        "isolation_accuracy_percent": (isolation_ok / session_count) * 100.0,
        "process_p99_ms": stats.p99,
    }


async def _exec_tc_cog_003(case: HolographicCase) -> dict[str, float]:
    import tempfile

    _reset_cognitive_globals_for_benchmark()
    loops = max(40, case.min_samples)
    process_samples_ms: list[float] = []
    enabled_count = 0
    merge_ok = 0

    with tempfile.TemporaryDirectory(prefix="holo-cog-003-") as directory:
        middleware = CognitiveMiddleware(workspace=directory, enabled=True)

        for index in range(loops):
            started = time.perf_counter_ns()
            result = await middleware.process(
                message=f"Read file bench_{index}.py",
                role_id="director",
                session_id=f"bench-mw-{index}",
            )
            process_samples_ms.append(_perf_ms(started))
            if result.get("enabled"):
                enabled_count += 1

            merged = middleware.inject_into_context(
                result,
                {"trace_id": f"trace-{index}", "request_id": f"req-{index}"},
            )
            if (
                merged.get("trace_id") == f"trace-{index}"
                and merged.get("request_id") == f"req-{index}"
                and isinstance(merged.get("cognitive"), dict)
            ):
                merge_ok += 1

    stats = summarize_samples(process_samples_ms, warmup_rounds=case.warmup_rounds)
    return {
        "middleware_enabled_percent": (enabled_count / loops) * 100.0,
        "middleware_p99_ms": stats.p99,
        "context_merge_accuracy_percent": (merge_ok / loops) * 100.0,
    }


async def _exec_tc_cog_004(case: HolographicCase) -> dict[str, float]:
    import tempfile

    loops = max(80, case.min_samples)
    trigger_ms: list[float] = []

    with tempfile.TemporaryDirectory(prefix="holo-cog-004-") as directory:
        store = EvolutionStore(directory)
        engine = EvolutionEngine(store)

        for index in range(loops):
            started = time.perf_counter_ns()
            await engine.process_trigger(
                trigger_type=TriggerType.SELF_REFLECTION,
                content=f"rule-{index}",
                context="benchmark",
            )
            trigger_ms.append(_perf_ms(started))

        state_path = store._get_state_path()
        state_exists = state_path.exists()
        history_accuracy = 0.0
        if state_exists:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            history = payload.get("update_history", [])
            if isinstance(history, list) and history:
                history_accuracy = min(100.0, (len(history) / loops) * 100.0)

    stats = summarize_samples(trigger_ms, warmup_rounds=case.warmup_rounds)
    return {
        "state_persist_accuracy_percent": 100.0 if state_exists else 0.0,
        "history_growth_accuracy_percent": history_accuracy,
        "trigger_p99_ms": stats.p99,
    }
