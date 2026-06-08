"""Speculation 评测矩阵 harness 的单测（注入假执行器，不依赖真实 LLM）.

假执行器在 ON 跑期间通过 ``emit`` 发射一个模拟的 ``speculation.turn.summary``
事件，从而验证：差分 ON/OFF 执行、事件采集、wrong_adoption 硬门禁、收益聚合与
报告结构——全部无需真实模型。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest
from polaris.cells.llm.evaluation.internal.speculation_matrix import (
    run_speculation_matrix_suite,
)
from polaris.cells.roles.kernel.public.service import (
    SpeculationEvent,
    publish_speculation_event,
)
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1

_CASE_ID = "l1_grep_search"


class _FakeExecutor:
    """假执行器：ON 跑（session_id 以 -on 结尾）时发射模拟的 turn summary."""

    def __init__(self, on_summary: dict[str, int] | None, on_error: str = "") -> None:
        self._on_summary = on_summary
        self._on_error = on_error

    def stream_session(self, command: ExecuteRoleSessionCommandV1) -> AsyncIterator[Mapping[str, Any]]:
        return self._gen(command)

    async def _gen(self, command: ExecuteRoleSessionCommandV1) -> AsyncIterator[Mapping[str, Any]]:
        is_on = command.session_id.endswith("-on")
        yield {"type": "tool_call", "tool": "repo_rg", "args": {"query": "needle"}}
        yield {"type": "tool_result", "result": {"content": "match", "ok": True}}
        if is_on and self._on_summary is not None:
            publish_speculation_event(
                SpeculationEvent(
                    event_type="speculation.turn.summary",
                    turn_id=command.session_id,
                    action="summary",
                    saved_ms=int(self._on_summary.get("saved_ms_total", 0)),
                    metadata=dict(self._on_summary),
                )
            )
        if is_on and self._on_error:
            yield {"type": "error", "error": self._on_error}
            return
        yield {"type": "complete", "result": {"content": "done"}}

    async def run_session(self, command: ExecuteRoleSessionCommandV1) -> Mapping[str, Any]:
        return {"content": "done"}


async def _run(tmp_path: Any, summary: dict[str, int] | None, on_error: str = "") -> dict[str, Any]:
    executor = _FakeExecutor(summary, on_error=on_error)
    return await run_speculation_matrix_suite(
        {},
        "fake-model",
        "director",
        workspace=str(tmp_path),
        context={"role_session_executor": executor},
        options={"matrix_case_ids": [_CASE_ID]},
    )


@pytest.mark.asyncio
async def test_benefit_metrics_aggregated_and_pass(tmp_path: Any) -> None:
    summary = {"adopted": 2, "joined": 1, "replayed": 1, "saved_ms_total": 100, "wrong_adoption": 0}
    result = await _run(tmp_path, summary)

    assert result["ok"] is True
    details = result["details"]
    assert details["suite"] == "speculation_matrix"
    s = details["summary"]
    assert s["total_cases"] == 1
    assert s["passed_cases"] == 1
    assert s["adopted_total"] == 2
    assert s["joined_total"] == 1
    assert s["replayed_total"] == 1
    assert s["saved_ms_total"] == 100
    assert s["wrong_adoption_total"] == 0
    assert s["speculation_active_cases"] == 1
    # hit_rate = (2 + 1) / (2 + 1 + 1) = 0.75
    assert s["hit_rate"] == 0.75
    case = details["cases"][0]
    assert case["case_id"] == _CASE_ID
    assert case["passed"] is True
    assert case["saved_ms_total"] == 100


@pytest.mark.asyncio
async def test_wrong_adoption_fails_hard(tmp_path: Any) -> None:
    summary = {"adopted": 1, "joined": 0, "replayed": 0, "saved_ms_total": 5, "wrong_adoption": 1}
    result = await _run(tmp_path, summary)

    assert result["ok"] is False
    s = result["details"]["summary"]
    assert s["wrong_adoption_total"] == 1
    assert s["passed_cases"] == 0
    assert result["details"]["cases"][0]["passed"] is False


@pytest.mark.asyncio
async def test_no_speculation_activity_still_passes(tmp_path: Any) -> None:
    # 非 FC / 未触发 speculation：无 summary 事件，活跃度为 0，但无 wrong_adoption -> PASS。
    result = await _run(tmp_path, summary=None)

    assert result["ok"] is True
    s = result["details"]["summary"]
    assert s["speculation_active_cases"] == 0
    assert s["adopted_total"] == 0
    assert s["wrong_adoption_total"] == 0
    assert result["details"]["cases"][0]["passed"] is True


@pytest.mark.asyncio
async def test_non_attributable_error_divergence_is_soft(tmp_path: Any) -> None:
    # ON 出现 delivery-contract 类错误（不可归因到 speculation），OFF 不出错：
    # 记为 error_divergence（软诊断），但不算 speculation 回归 -> 仍 PASS / ok。
    summary = {"adopted": 1, "joined": 0, "replayed": 0, "saved_ms_total": 7, "wrong_adoption": 0}
    result = await _run(tmp_path, summary, on_error="single_batch_contract_violation: mutation requested but no write")

    assert result["ok"] is True
    s = result["details"]["summary"]
    assert s["error_divergence_cases"] == 1
    assert s["speculation_regressed_cases"] == 0
    case = result["details"]["cases"][0]
    assert case["passed"] is True
    assert case["error_divergence"] is True
    assert case["speculation_regressed"] is False


@pytest.mark.asyncio
async def test_speculation_attributable_error_is_hard(tmp_path: Any) -> None:
    # ON 错误信息可归因到 speculation（提到 shadow），OFF 不出错 -> 硬失败。
    summary = {"adopted": 0, "joined": 0, "replayed": 1, "saved_ms_total": 0, "wrong_adoption": 0}
    result = await _run(tmp_path, summary, on_error="shadow task crashed the turn")

    assert result["ok"] is False
    s = result["details"]["summary"]
    assert s["speculation_regressed_cases"] == 1
    assert result["details"]["cases"][0]["passed"] is False


@pytest.mark.asyncio
async def test_unknown_role_returns_no_cases(tmp_path: Any) -> None:
    executor = _FakeExecutor(None)
    result = await run_speculation_matrix_suite(
        {},
        "fake-model",
        "director",
        workspace=str(tmp_path),
        context={"role_session_executor": executor},
        options={"matrix_case_ids": ["__nonexistent_case__"]},
    )
    assert result["ok"] is False
    assert "no speculation matrix cases" in result.get("error", "")
