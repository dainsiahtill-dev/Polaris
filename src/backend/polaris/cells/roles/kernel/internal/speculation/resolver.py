# ruff: noqa: BLE001
from __future__ import annotations

from typing import Any

from polaris.cells.roles.kernel.internal.speculation.fingerprints import (
    build_env_fingerprint,
    build_spec_key,
    normalize_args,
)
from polaris.cells.roles.kernel.internal.speculation.metrics import (
    SpeculationMetrics,
)
from polaris.cells.roles.kernel.internal.speculation.models import (
    ShadowTaskRecord,
    ShadowTaskState,
)
from polaris.cells.roles.kernel.internal.speculation.registry import (
    ShadowTaskRegistry,
)
from polaris.cells.roles.kernel.internal.speculation.write_phases import (
    WriteToolPhases,
)


class SpeculationResolver:
    """Authoritative 阶段裁决器：实现 ADOPT / JOIN / REPLAY 四动作."""

    def __init__(
        self,
        *,
        registry: ShadowTaskRegistry,
        metrics: SpeculationMetrics,
    ) -> None:
        self._registry = registry
        self._metrics = metrics

    @staticmethod
    def _saved_ms(task: ShadowTaskRecord) -> int:
        """从 shadow 任务的实际执行耗时估算被隐藏的时延（saved_ms）.

        adopt 命中意味着 authoritative 阶段无需重跑该工具，省下的就是 shadow
        本身的执行墙钟时间。缺少计时信息时安全返回 0（不夸大收益）。
        """
        if task.started_at is None or task.finished_at is None:
            return 0
        delta_ms = int((task.finished_at - task.started_at) * 1000)
        return delta_ms if delta_ms > 0 else 0

    async def resolve_or_execute(
        self,
        *,
        turn_id: str,
        call_id: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """根据 shadow task 状态决定 ADOPT、JOIN 或 REPLAY.

        Args:
            turn_id: 当前 turn 标识
            call_id: 正式工具调用标识
            tool_name: 工具名称
            args: 原始参数

        Returns:
            统一结果字典，keys:
            - action: "adopt" | "join" | "replay"
            - result: Any | None (adopt/join 时有效)
            - error: str | None
        """
        # Phase 5: 写工具先查找 prepare shadow
        if WriteToolPhases.is_write_tool(tool_name):
            prepare_inv = WriteToolPhases.build_prepare_shadow_key(
                source_call_id=call_id,
                arguments=dict(args),
            )
            prepare_spec_key = prepare_inv.shadow_key_hash
            prepare_task = self._registry.lookup(prepare_spec_key)
            if prepare_task is None:
                self._metrics.record_replay(turn_id, call_id, tool_name, reason="prepare_miss")
                return {
                    "action": "block",
                    "result": None,
                    "error": "write_tool_prepare_shadow_missing",
                }
            if prepare_task.state == ShadowTaskState.COMPLETED:
                try:
                    saved_ms = self._saved_ms(prepare_task)
                    result = await self._registry.adopt(prepare_task.task_id, call_id)
                    self._metrics.record_adopt(
                        turn_id,
                        call_id,
                        prepare_inv.canonical_tool_name,
                        prepare_spec_key,
                        saved_ms=saved_ms,
                    )
                    return {"action": "adopt", "result": result, "error": None}
                except Exception as exc:
                    self._metrics.record_replay(turn_id, call_id, tool_name, reason=f"prepare_adopt_failed:{exc}")
                    return {
                        "action": "block",
                        "result": None,
                        "error": f"write_tool_prepare_shadow_adopt_failed:{exc}",
                    }
            if prepare_task.state in {ShadowTaskState.STARTING, ShadowTaskState.RUNNING}:
                try:
                    result = await self._registry.join(prepare_task.task_id, call_id)
                    self._metrics.record_join(turn_id, call_id, prepare_inv.canonical_tool_name, prepare_spec_key)
                    return {"action": "join", "result": result, "error": None}
                except Exception as exc:
                    self._metrics.record_replay(turn_id, call_id, tool_name, reason=f"prepare_join_failed:{exc}")
                    return {
                        "action": "block",
                        "result": None,
                        "error": f"write_tool_prepare_shadow_join_failed:{exc}",
                    }
            self._metrics.record_replay(turn_id, call_id, tool_name, reason=prepare_task.state.value)
            return {
                "action": "block",
                "result": None,
                "error": f"write_tool_prepare_shadow_{prepare_task.state.value}",
            }

        normalized = normalize_args(tool_name, args)
        env_fp = build_env_fingerprint()
        spec_key = build_spec_key(
            tool_name=tool_name,
            normalized_args=normalized,
            env_fingerprint=env_fp,
        )

        task = self._registry.lookup(spec_key)

        if task is None:
            self._metrics.record_replay(turn_id, call_id, tool_name, reason="miss")
            return {"action": "replay", "result": None, "error": None}

        if task.state == ShadowTaskState.COMPLETED:
            try:
                saved_ms = self._saved_ms(task)
                result = await self._registry.adopt(task.task_id, call_id)
                self._metrics.record_adopt(turn_id, call_id, tool_name, spec_key, saved_ms=saved_ms)
                return {"action": "adopt", "result": result, "error": None}
            except Exception as exc:
                self._metrics.record_replay(turn_id, call_id, tool_name, reason=f"adopt_failed:{exc}")
                return {"action": "replay", "result": None, "error": str(exc)}

        if task.state in {ShadowTaskState.STARTING, ShadowTaskState.RUNNING}:
            try:
                result = await self._registry.join(task.task_id, call_id)
                self._metrics.record_join(turn_id, call_id, tool_name, spec_key)
                return {"action": "join", "result": result, "error": None}
            except Exception as exc:
                self._metrics.record_replay(turn_id, call_id, tool_name, reason=f"join_failed:{exc}")
                return {"action": "replay", "result": None, "error": str(exc)}

        if task.state in {
            ShadowTaskState.FAILED,
            ShadowTaskState.CANCELLED,
            ShadowTaskState.EXPIRED,
            ShadowTaskState.ABANDONED,
        }:
            self._metrics.record_replay(turn_id, call_id, tool_name, reason=task.state.value)
            return {"action": "replay", "result": None, "error": None}

        # 未知状态安全降级
        self._metrics.record_replay(turn_id, call_id, tool_name, reason="unexpected_state")
        return {"action": "replay", "result": None, "error": None}
