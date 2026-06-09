"""自适应排序 A/B 矩阵 harness 单测（注入假执行器，不依赖真实 LLM）.

假执行器对 ON/OFF 产出相同观测 → 同分 → tie，验证差分 A/B 的装配/打分/聚合结构。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest
from polaris.cells.llm.evaluation.internal.projection_adaptive_matrix import (
    run_projection_adaptive_matrix_suite,
)
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1

_CASE_ID = "l2_multi_file_read"


class _FakeExecutor:
    def stream_session(self, command: ExecuteRoleSessionCommandV1) -> AsyncIterator[Mapping[str, Any]]:
        return self._gen(command)

    async def _gen(self, command: ExecuteRoleSessionCommandV1) -> AsyncIterator[Mapping[str, Any]]:
        yield {"type": "tool_call", "tool": "read_file", "args": {"path": "config.py"}}
        yield {"type": "tool_result", "result": {"content": "x", "ok": True}}
        yield {"type": "complete", "result": {"content": "done"}}

    async def run_session(self, command: ExecuteRoleSessionCommandV1) -> Mapping[str, Any]:
        return {"content": "done"}


@pytest.mark.asyncio
async def test_ab_matrix_structure_and_tie_on_identical(tmp_path: Any) -> None:
    result = await run_projection_adaptive_matrix_suite(
        {},
        "fake-model",
        "director",
        workspace=str(tmp_path),
        context={"role_session_executor": _FakeExecutor()},
        options={"matrix_case_ids": [_CASE_ID]},
    )
    assert result["ok"] is True
    details = result["details"]
    assert details["suite"] == "projection_adaptive_matrix"
    s = details["summary"]
    assert s["total_cases"] == 1
    # 同一假执行器 ON/OFF 产出相同 → 平局，delta=0
    case = details["cases"][0]
    assert case["case_id"] == _CASE_ID
    assert case["verdict"] == "tie"
    assert case["delta"] == 0.0
    assert s["adaptive_helped"] + s["adaptive_hurt"] + s["tie"] == 1


@pytest.mark.asyncio
async def test_ab_matrix_unknown_case(tmp_path: Any) -> None:
    result = await run_projection_adaptive_matrix_suite(
        {},
        "fake-model",
        "director",
        workspace=str(tmp_path),
        context={"role_session_executor": _FakeExecutor()},
        options={"matrix_case_ids": ["__nope__"]},
    )
    assert result["ok"] is False
    assert "no adaptive matrix cases" in result.get("error", "")
