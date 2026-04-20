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
    ShadowTaskState,
)
from polaris.cells.roles.kernel.internal.speculation.registry import (
    ShadowTaskRegistry,
)
from polaris.cells.roles.kernel.internal.speculation.write_phases import (
    WriteToolPhases,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
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
            prepare_inv = WriteToolPhases.build_prepare_invocation(
                ToolInvocation(
                    call_id=ToolCallId(call_id),
                    tool_name=tool_name,
                    arguments=args,
                    effect_type=ToolEffectType.READ,
                    execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                )
            )
            prepare_norm = normalize_args(prepare_inv.tool_name, prepare_inv.arguments)
            prepare_env_fp = build_env_fingerprint()
            prepare_spec_key = build_spec_key(
                tool_name=prepare_inv.tool_name,
                normalized_args=prepare_norm,
                env_fingerprint=prepare_env_fp,
            )
            prepare_task = self._registry.lookup(prepare_spec_key)
            if prepare_task is None:
                self._metrics.record_replay(turn_id, call_id, tool_name, reason="prepare_miss")
                return {"action": "replay", "result": None, "error": None}
            if prepare_task.state == ShadowTaskState.COMPLETED:
                try:
                    result = await self._registry.adopt(prepare_task.task_id, call_id)
                    self._metrics.record_adopt(turn_id, call_id, prepare_inv.tool_name, prepare_spec_key)
                    return {"action": "adopt", "result": result, "error": None}
                except Exception as exc:
                    self._metrics.record_replay(turn_id, call_id, tool_name, reason=f"prepare_adopt_failed:{exc}")
                    return {"action": "replay", "result": None, "error": str(exc)}
            if prepare_task.state in {ShadowTaskState.STARTING, ShadowTaskState.RUNNING}:
                try:
                    result = await self._registry.join(prepare_task.task_id, call_id)
                    self._metrics.record_join(turn_id, call_id, prepare_inv.tool_name, prepare_spec_key)
                    return {"action": "join", "result": result, "error": None}
                except Exception as exc:
                    self._metrics.record_replay(turn_id, call_id, tool_name, reason=f"prepare_join_failed:{exc}")
                    return {"action": "replay", "result": None, "error": str(exc)}
            self._metrics.record_replay(turn_id, call_id, tool_name, reason=prepare_task.state.value)
            return {"action": "replay", "result": None, "error": None}

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
                result = await self._registry.adopt(task.task_id, call_id)
                self._metrics.record_adopt(turn_id, call_id, tool_name, spec_key)
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
