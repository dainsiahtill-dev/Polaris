"""Provider 延迟看护：慢 provider 不许把预算静默烧光。

实测 L1-03：provider 每轮固定 ~721s（output 只有 92 token），9 轮就把 5400s
预算吃光 → verification 从未运行 → run interrupted/unknown。底座当时全程沉默。

三道动作：
1. 记录每轮延迟与 provider。
2. 连续 N 轮超阈值 → `provider_degraded`，并归因：
   - output 少而耗时长 → provider 侧（排队/冷启动/限速）
   - output 多 → 生成慢（任务/模型侧）
3. 若剩余预算按当前速率连 `budget_floor_turns` 轮都跑不完 → 早停，
   记 `budget_starved_by_provider`。**诚实**：不冒充完成，也不冒充模型慢。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Literal

Attribution = Literal["provider_side", "generation", "mixed"]
Decision = Literal["ok", "degraded", "starved"]


@dataclass
class ProviderLatencyGuard:
    slow_threshold_ms: int = 120_000
    consecutive: int = 3
    budget_floor_turns: int = 8
    short_output_tokens: int = 64
    _samples: list[dict[str, Any]] = field(default_factory=list)

    def observe(self, *, latency_ms: int, output_tokens: int, provider_id: str | None) -> None:
        self._samples.append(
            {
                "latency_ms": int(latency_ms or 0),
                "output_tokens": int(output_tokens or 0),
                "provider_id": provider_id,
            }
        )

    def reset(self) -> None:
        self._samples.clear()

    def _slow_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for sample in self._samples:
            if sample["latency_ms"] >= self.slow_threshold_ms:
                runs.append(sample)
            else:
                runs = []
        return runs

    def evaluate(
        self,
        *,
        remaining_budget_s: float | None,
        avg_turn_seconds: float | None = None,
    ) -> tuple[Decision, dict[str, Any]]:
        """返回判定与证据；`starved` 时调用方应早停。

        `avg_turn_seconds` 可省略 —— 省略时用样本中位延迟估算，这样调用方
        （工程 loop）不需要自己取时钟（测试里 time 常被替换）。
        """

        slow = self._slow_runs()
        if not slow:
            return "ok", {}
        latencies = [item["latency_ms"] for item in slow]
        outputs = [item["output_tokens"] for item in slow]
        providers = sorted({str(item["provider_id"]) for item in slow if item.get("provider_id")})
        # 归因：慢而输出少 = provider 侧；慢而输出多 = 生成侧。
        short = sum(1 for value in outputs if value < self.short_output_tokens)
        if short == len(outputs):
            attribution: Attribution = "provider_side"
        elif short == 0:
            attribution = "generation"
        else:
            attribution = "mixed"
        evidence: dict[str, Any] = {
            "slow_rounds": len(slow),
            "latency_ms_median": int(statistics.median(latencies)),
            "latency_ms_max": max(latencies),
            "output_tokens_median": int(statistics.median(outputs)),
            "providers": providers,
            "attribution": attribution,
        }
        if remaining_budget_s is None:
            return "degraded", evidence
        pace = max(float(avg_turn_seconds), 1.0) if avg_turn_seconds else (
            max(int(statistics.median(latencies)) / 1000.0, 1.0)
        )
        projected = float(remaining_budget_s) / pace
        evidence["remaining_budget_s"] = round(float(remaining_budget_s), 1)
        evidence["avg_turn_seconds"] = round(pace, 1)
        evidence["projected_turns"] = round(projected, 1)
        if projected < self.budget_floor_turns:
            evidence["reason"] = "budget_starved_by_provider"
            return "starved", evidence
        return "degraded", evidence

    def snapshot(self) -> dict[str, Any]:
        if not self._samples:
            return {}
        latencies = [item["latency_ms"] for item in self._samples]
        return {
            "rounds": len(self._samples),
            "latency_ms_median": int(statistics.median(latencies)),
            "latency_ms_max": max(latencies),
        }


def projected_turns(*, remaining_budget_s: float, avg_turn_seconds: float) -> float:
    return float(remaining_budget_s) / max(float(avg_turn_seconds), 1.0)
