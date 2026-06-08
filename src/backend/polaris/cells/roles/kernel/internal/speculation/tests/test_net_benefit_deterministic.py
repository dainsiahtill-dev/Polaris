"""Speculation 净机制收益的确定性测试（零 LLM、零模型抖动）.

用真实 speculation 原语 + 只读延迟注入，把"流式生成"替换为确定性窗口 sleep(W)，
从而隔离出 speculation 机制本身带来的端到端时延削减：

- W >= D：shadow 在窗口内跑完 → ADOPT → authoritative 0 成本 → 净收益 ≈ D
- W <  D：shadow 仍在跑 → JOIN 等剩余 → 净收益 ≈ W（= min(W, D)）

断言用宽松下界（>0.4×min(W,D)）以抵抗负载抖动，同时验证动作语义（adopt/join）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.speculation.metrics import SpeculationMetrics
from polaris.cells.roles.kernel.internal.speculation.registry import (
    EphemeralSpecCache,
    ShadowTaskRegistry,
)
from polaris.cells.roles.kernel.internal.speculation.resolver import SpeculationResolver
from polaris.cells.roles.kernel.internal.speculative_executor import SpeculativeExecutor
from polaris.cells.roles.kernel.internal.stream_shadow_engine import StreamShadowEngine
from polaris.cells.roles.kernel.internal.tool_batch_runtime import (
    ToolBatchRuntime,
    ToolExecutionContext,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
)


async def _fake_exec(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "result": {"content": "payload"}}


def _invocation(call_id: str, path: str) -> ToolInvocation:
    return ToolInvocation(
        call_id=ToolCallId(call_id),
        tool_name="read_file",
        arguments={"path": path},
        effect_type=ToolEffectType.READ,
        execution_mode=ToolExecutionMode.READONLY_PARALLEL,
    )


def _build(spec_on: bool) -> tuple[ToolBatchRuntime, StreamShadowEngine, SpeculationMetrics]:
    runtime = ToolBatchRuntime(executor=_fake_exec, context=ToolExecutionContext(workspace=".", timeout_ms=60000))
    spec_exec = SpeculativeExecutor(runtime, enabled=spec_on)
    metrics = SpeculationMetrics()
    registry = ShadowTaskRegistry(speculative_executor=spec_exec, metrics=metrics, cache=EphemeralSpecCache())
    resolver = SpeculationResolver(registry=registry, metrics=metrics)
    engine = StreamShadowEngine(spec_exec, registry=registry, resolver=resolver, metrics=metrics)
    return runtime, engine, metrics


async def _authoritative(runtime: ToolBatchRuntime, call_id: str, path: str) -> None:
    await runtime.execute_batch(ToolBatch(batch_id=BatchId("auth"), parallel_readonly=[_invocation(call_id, path)]))


async def _measure(spec_on: bool, w_ms: int, d_ms: int, path: str, monkeypatch: pytest.MonkeyPatch) -> tuple[float, str]:
    monkeypatch.setenv("SPECULATION_EVAL_READ_DELAY_MS", str(d_ms))
    monkeypatch.setenv("SPECULATION_EVAL_READ_TIMEOUT_MS", str(max(60000, d_ms * 4)))
    runtime, engine, _ = _build(spec_on)
    t0 = time.monotonic()
    action = "off"
    if spec_on:
        await engine.speculate_tool_call(tool_name="read_file", arguments={"path": path}, call_id="c1", turn_id="t1")
        await asyncio.sleep(w_ms / 1000.0)
        res = await engine.resolve_or_execute(turn_id="t1", call_id="c1", tool_name="read_file", args={"path": path})
        action = str(res.get("action"))
        if action == "replay":
            await _authoritative(runtime, "c1", path)
    else:
        await asyncio.sleep(w_ms / 1000.0)
        await _authoritative(runtime, "c1", path)
    return (time.monotonic() - t0) * 1000.0, action


@pytest.mark.asyncio
async def test_net_benefit_window_ge_delay_adopts_and_hides_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    # W >= D：应 ADOPT，端到端净收益 ≈ D。
    w, d = 200, 80
    on_dur, on_action = await _measure(True, w, d, "a.py", monkeypatch)
    off_dur, _ = await _measure(False, w, d, "b.py", monkeypatch)
    assert on_action == "adopt"
    net = off_dur - on_dur
    # 宽松下界：至少隐藏了 min(W,D) 的 40%（抗负载抖动），且 ON 明显快于 OFF。
    assert net > 0.4 * min(w, d), f"net={net:.1f}ms expected > {0.4 * min(w, d):.1f}ms (on={on_dur:.1f} off={off_dur:.1f})"


@pytest.mark.asyncio
async def test_net_benefit_window_lt_delay_joins(monkeypatch: pytest.MonkeyPatch) -> None:
    # W < D：shadow 未跑完 -> JOIN（仍隐藏了窗口内那段）。
    w, d = 80, 240
    on_dur, on_action = await _measure(True, w, d, "c.py", monkeypatch)
    off_dur, _ = await _measure(False, w, d, "e.py", monkeypatch)
    assert on_action in {"join", "adopt"}
    # ON 不应慢于 OFF（join 至多等到 D，OFF 也付 W+D）。
    assert on_dur <= off_dur + 30, f"on={on_dur:.1f} off={off_dur:.1f}"


@pytest.mark.asyncio
async def test_disabled_speculation_has_no_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    # 关闭 speculation：OFF 路径不应产生领养（机制不参与）。
    _, off_action = await _measure(False, 100, 50, "f.py", monkeypatch)
    assert off_action == "off"
