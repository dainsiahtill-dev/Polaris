"""ProjectionEngine 自适应排序的真实 LLM A/B 评测矩阵.

自适应学习现已激活（权重影响 `sort_events` 排序 + `record_projection_outcome` 在
turn 收尾被实调）。本套件做**差分 A/B**：对每个 case 用自适应排序 **ON**（按学习权重
重排上下文）与 **OFF**（纯时序）各跑一遍真实流式 turn，用确定性 judge 给每遍打分，
比较 ON vs OFF 的得分——回答"自适应学习在真实环境里到底帮没帮上忙"。

开关：`ENABLE_PROJECTION_ADAPTIVE_ORDERING`（每 turn 的 sort_events 新鲜读取，进程内
逐次切换即生效）。复用 `tool_calling_matrix` 的 case 装载 / fixture 物化 / 流式观测 /
judge，避免重复造轮子。

诚实预期：单 turn 工具调用里重排上下文的影响通常较小，多为平局 + 噪声；本套件如实报告
胜/负/平与平均得分差，并标注噪声。要放大信号应选多事件历史的 case。
"""

from __future__ import annotations

import math
import os
import statistics
from collections.abc import Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from .tool_calling_matrix import (
    _collect_stream_observation,
    _judge_case,
    _non_empty,
    _normalize_case_ids,
    _resolve_executor,
    load_builtin_tool_calling_matrix_cases,
    materialize_case_workspace,
)
from .utils import new_test_run_id

if TYPE_CHECKING:
    from polaris.bootstrap.config import Settings

# 默认 case：偏多事件历史 / 多步，自适应重排最可能体现差异。可经 matrix_case_ids 覆盖。
DEFAULT_ADAPTIVE_CASE_IDS: tuple[str, ...] = (
    "l7_context_memory",
    "l7_context_switch_with_memory",
    "l7_multi_tool_coordination",
    "l7_tool_result_interpretation",
    "l3_search_then_read",
    "l2_multi_file_read",
)

_TIE_EPS = 0.05  # N=1 时：得分差小于此值视为平局（容忍模型噪声）
# t(0.975, df) 小样本查表（N-repeats 统计版用）。
_T_TABLE = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}


def _delta_ci(on_scores: list[float], off_scores: list[float]) -> tuple[float, float]:
    """两组独立得分的均值差半宽（95% CI）。N<2 返回 (delta, 0)。"""
    n_on, n_off = len(on_scores), len(off_scores)
    delta = (statistics.mean(on_scores) if n_on else 0.0) - (statistics.mean(off_scores) if n_off else 0.0)
    if n_on < 2 or n_off < 2:
        return delta, 0.0
    se = math.sqrt(statistics.pvariance(on_scores) / n_on + statistics.pvariance(off_scores) / n_off)
    df = min(n_on, n_off) - 1
    t = _T_TABLE.get(df, 1.96)
    return delta, t * se


@contextmanager
def _adaptive_env(enabled: bool) -> Any:
    key = "ENABLE_PROJECTION_ADAPTIVE_ORDERING"
    prev = os.environ.get(key)
    try:
        os.environ[key] = "1" if enabled else "0"
        yield
    finally:
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


async def _score_once(*, case: Any, executor: Any, run_id: str, provider_id: str, model: str, workspace: str) -> float:
    """跑一遍流式 turn 并用 judge 打分（0..1）。失败返回 0。"""
    sandbox = materialize_case_workspace(benchmark_root=workspace, run_id=run_id, case=case)
    obs, _events = await _collect_stream_observation(
        case=case,
        sandbox_workspace=sandbox,
        benchmark_root=workspace,
        workspace=sandbox,
        provider_id=provider_id,
        model=model,
        executor=executor,
        run_id=run_id,
    )
    verdict = _judge_case(case=case, stream_observed=obs, non_stream_observed=None, transport_mode="stream")
    return float(getattr(verdict, "score", 0.0) or 0.0)


async def _evaluate_case(
    *, case: Any, executor: Any, run_id: str, provider_id: str, model: str, workspace: str, repeats: int = 1
) -> dict[str, Any]:
    """A/B：同 case 跑自适应 ON 与 OFF 各 ``repeats`` 次，比较 judge 得分（带 CI）。"""
    on_scores: list[float] = []
    off_scores: list[float] = []
    for i in range(repeats):
        with _adaptive_env(True):
            on_scores.append(
                await _score_once(
                    case=case,
                    executor=executor,
                    run_id=f"{run_id}-{case.case_id}-on-{i}",
                    provider_id=provider_id,
                    model=model,
                    workspace=workspace,
                )
            )
        with _adaptive_env(False):
            off_scores.append(
                await _score_once(
                    case=case,
                    executor=executor,
                    run_id=f"{run_id}-{case.case_id}-off-{i}",
                    provider_id=provider_id,
                    model=model,
                    workspace=workspace,
                )
            )
    mean_on = statistics.mean(on_scores) if on_scores else 0.0
    mean_off = statistics.mean(off_scores) if off_scores else 0.0
    delta, ci = _delta_ci(on_scores, off_scores)

    if repeats >= 2:
        # 统计判定：CI 排除 0 才下结论，否则"噪声内不显著"。
        if delta - ci > 0:
            verdict = "adaptive_helped"
        elif delta + ci < 0:
            verdict = "adaptive_hurt"
        else:
            verdict = "inconclusive"
    else:
        verdict = "adaptive_helped" if delta > _TIE_EPS else ("adaptive_hurt" if delta < -_TIE_EPS else "tie")

    return {
        "case_id": case.case_id,
        "role": case.role,
        "repeats": repeats,
        "score_on": round(mean_on, 4),
        "score_off": round(mean_off, 4),
        "delta": round(delta, 4),
        "ci95": round(ci, 4),
        "verdict": verdict,
        "on_scores": [round(s, 4) for s in on_scores],
        "off_scores": [round(s, 4) for s in off_scores],
    }


async def run_projection_adaptive_matrix_suite(
    provider_cfg: dict[str, Any],
    model: str,
    role: str,
    *,
    workspace: str,
    settings: Settings | None = None,
    context: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """运行自适应排序 A/B 矩阵（ON vs OFF，真实 LLM，judge 打分）.

    ``ok`` 恒为 True（这是测量性套件，不是硬门禁）；真正信号在 summary 的
    helped/hurt/tie 与 mean_delta。
    """
    del provider_cfg, settings
    context_payload = dict(context or {})
    options_payload = dict(options or {})
    provider_id = (
        _non_empty(context_payload.get("provider_id") or options_payload.get("provider_id") or "runtime_binding")
        or "runtime_binding"
    )
    requested_role = _non_empty(role).lower() or "director"
    max_failed = 0
    raw_max = options_payload.get("max_failed")
    if isinstance(raw_max, int) and raw_max > 0:
        max_failed = raw_max
    repeats = 1
    raw_repeats = options_payload.get("repeats") or context_payload.get("repeats")
    if isinstance(raw_repeats, int) and raw_repeats > 0:
        repeats = min(raw_repeats, 10)

    case_ids = [
        _non_empty(item)
        for item in _normalize_case_ids(
            options_payload.get("matrix_case_ids") or context_payload.get("matrix_case_ids") or ()
        )
        if _non_empty(item)
    ]
    if not case_ids:
        case_ids = list(DEFAULT_ADAPTIVE_CASE_IDS)

    cases = load_builtin_tool_calling_matrix_cases(role=requested_role, case_ids=case_ids)
    if not cases:
        return {
            "ok": False,
            "error": f"no adaptive matrix cases matched role={requested_role!r}",
            "details": {"cases": []},
        }

    run_id = new_test_run_id()
    executor = _resolve_executor(context_payload)
    results: list[dict[str, Any]] = []
    hurt_count = 0
    for case in cases:
        result = await _evaluate_case(
            case=case,
            executor=executor,
            run_id=run_id,
            provider_id=provider_id,
            model=model,
            workspace=workspace,
            repeats=repeats,
        )
        results.append(result)
        if result["verdict"] == "adaptive_hurt":
            hurt_count += 1
            if max_failed > 0 and hurt_count >= max_failed:
                break

    helped = sum(1 for r in results if r["verdict"] == "adaptive_helped")
    hurt = sum(1 for r in results if r["verdict"] == "adaptive_hurt")
    tie = sum(1 for r in results if r["verdict"] == "tie")
    inconclusive = sum(1 for r in results if r["verdict"] == "inconclusive")
    mean_delta = round(sum(r["delta"] for r in results) / len(results), 4) if results else 0.0
    mean_on = round(sum(r["score_on"] for r in results) / len(results), 4) if results else 0.0
    mean_off = round(sum(r["score_off"] for r in results) / len(results), 4) if results else 0.0

    return {
        "ok": True,
        "details": {
            "suite": "projection_adaptive_matrix",
            "run_id": run_id,
            "cases": results,
            "summary": {
                "total_cases": len(results),
                "repeats": repeats,
                "adaptive_helped": helped,
                "adaptive_hurt": hurt,
                "tie": tie,
                "inconclusive": inconclusive,
                "mean_delta_on_minus_off": mean_delta,
                "mean_score_on": mean_on,
                "mean_score_off": mean_off,
            },
        },
    }
