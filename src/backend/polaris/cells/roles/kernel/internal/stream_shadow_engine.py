from __future__ import annotations

import asyncio
import os
from typing import Any

from polaris.cells.roles.kernel.internal.speculation.candidate_decoder import (
    CandidateDecoder,
)
from polaris.cells.roles.kernel.internal.speculation.chain_speculator import (
    ChainSpeculator,
)
from polaris.cells.roles.kernel.internal.speculation.metrics import (
    SpeculationMetrics,
)
from polaris.cells.roles.kernel.internal.speculation.registry import (
    ShadowTaskRegistry,
)
from polaris.cells.roles.kernel.internal.speculation.resolver import (
    SpeculationResolver,
)
from polaris.cells.roles.kernel.internal.speculation.salvage import SalvageGovernor
from polaris.cells.roles.kernel.internal.speculation.stability_scorer import (
    StabilityScorer,
)
from polaris.cells.roles.kernel.internal.speculation.task_group import (
    TurnScopedTaskGroup,
)
from polaris.cells.roles.kernel.internal.speculation.write_phases import (
    WriteToolPhases,
)
from polaris.cells.roles.kernel.internal.speculative_executor import (
    ShadowExecutionError,
    SpeculativeExecutor,
)
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime


async def _task_group_proxy(future: asyncio.Task[Any]) -> Any:
    """将已创建的 asyncio.Task 包装为可供 TurnScopedTaskGroup 追踪的协程."""
    return await future


def _eval_read_shadow_timeout_ms() -> int:
    """只读 shadow 的超时（毫秒）。默认 1200；评测注入大延迟时经 env 上调，
    避免 shadow 在注入延迟完成前超时而无法被领养。
    env: ``SPECULATION_EVAL_READ_TIMEOUT_MS``。
    """
    raw = os.environ.get("SPECULATION_EVAL_READ_TIMEOUT_MS")
    if not raw:
        return 1200
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1200


class StreamShadowEngine:
    """流式工具推测执行引擎的外观门面(Facade).

    Phase 1 升级：保留所有旧接口兼容，同时注入 Registry + Resolver，
    支持 ADOPT / JOIN / CANCEL / REPLAY 事务语义。
    Phase 4 升级：可选 CandidateDecoder 增量解析。
    """

    def __init__(
        self,
        speculative_executor: SpeculativeExecutor,
        *,
        registry: ShadowTaskRegistry | None = None,
        resolver: SpeculationResolver | None = None,
        salvage_governor: SalvageGovernor | None = None,
        task_group: TurnScopedTaskGroup | None = None,
        candidate_decoder: CandidateDecoder | None = None,
        stability_scorer: StabilityScorer | None = None,
        chain_speculator: ChainSpeculator | None = None,
        metrics: SpeculationMetrics | None = None,
    ) -> None:
        self._speculative_executor = speculative_executor
        self._registry = registry
        self._resolver = resolver
        self._metrics = metrics
        self._salvage_governor = salvage_governor
        self._task_group = task_group
        self._candidate_decoder = candidate_decoder
        self._stability_scorer = stability_scorer
        self._buffer: list[str] = []
        # Cross-turn speculation cache: session_id -> speculation result
        self._cross_turn_cache: dict[str, dict[str, Any]] = {}
        # Speculated patch cache: intent -> patch result
        self._speculated_patch_cache: dict[str, dict[str, Any]] = {}
        if registry is not None and chain_speculator is not None:
            registry._on_shadow_completed = chain_speculator.on_shadow_completed

    def consume_delta(self, delta: str) -> dict[str, Any] | None:
        """Consume a stream delta and attempt to predict a tool call.

        保留兼容接口；如果注入了 CandidateDecoder，则使用增量解析。
        否则 fallback 到 keyword heuristic。
        """
        if not delta:
            return None

        # Phase 4: use CandidateDecoder if available
        if self._candidate_decoder is not None:
            candidate = self._candidate_decoder.feed_delta(delta)
            if candidate is not None and self._stability_scorer is not None:
                self._stability_scorer.update_parse_state(candidate)
                return {
                    "tool_name": candidate.tool_name,
                    "arguments": dict(candidate.partial_args),
                    "confidence": candidate.stability_score,
                    "parse_state": candidate.parse_state,
                }
            if candidate is not None:
                return {
                    "tool_name": candidate.tool_name,
                    "arguments": dict(candidate.partial_args),
                    "confidence": 0.0,
                    "parse_state": candidate.parse_state,
                }
            return None

        # Fallback: Phase 1 keyword heuristic
        self._buffer.append(delta)
        combined = "".join(self._buffer)
        prediction: dict[str, Any] = {
            "tool_name": None,
            "arguments": None,
            "confidence": 0.0,
        }
        if "<tool_call>" in combined or "```tool" in combined:
            prediction["confidence"] = 0.1
        return prediction

    async def speculate_from_buffer(self) -> dict[str, Any]:
        """Try to form a speculative tool invocation from the current buffer."""
        combined = "".join(self._buffer)
        return {
            "enabled": self._speculative_executor.enabled,
            "buffer_length": len(combined),
            "speculation": None,
        }

    async def speculate_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        call_id: str,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        """推测执行单个工具调用.

        如果已注入 Registry，将 shadow task 注册到 Registry 并启动后台执行；
        否则使用本地 SpeculativeExecutor 执行只读推测。
        """
        normalized = tool_name.strip().lower().replace("-", "_")

        # Phase 5: write tool prepare shadow
        is_write_tool = WriteToolPhases.is_write_tool(tool_name)
        if normalized not in ToolBatchRuntime.READONLY_TOOLS and not is_write_tool:
            return {
                "enabled": self._speculative_executor.enabled,
                "result": None,
                "error": "non_readonly_tool",
            }
        if is_write_tool and (self._registry is None or not self._speculative_executor.enabled):
            return {
                "enabled": self._speculative_executor.enabled,
                "result": None,
                "error": "write_tool_prepare_shadow_unavailable",
            }

        # Phase 1: register shadow task to Registry, executed by Registry background runner
        if self._registry is not None and self._speculative_executor.enabled:
            from polaris.cells.roles.kernel.internal.speculation.fingerprints import (
                build_env_fingerprint,
                build_spec_key,
                normalize_args,
            )
            from polaris.cells.roles.kernel.internal.speculation.models import ToolSpecPolicy

            effective_turn_id = turn_id or "current_turn"

            if is_write_tool:
                # 为写工具启动 Prepare shadow(只读校验)
                prepare_inv = WriteToolPhases.build_prepare_shadow_key(
                    source_call_id=call_id,
                    arguments=dict(arguments or {}),
                )
                prepare_args = WriteToolPhases.prepare_shadow_normalized_args(arguments or {})
                env_fp = build_env_fingerprint()
                prepare_spec_key = prepare_inv.shadow_key_hash
                if not self._registry.exists_active(prepare_spec_key):
                    policy = ToolSpecPolicy(
                        tool_name=WriteToolPhases.prepare_shadow_execution_tool_name(),
                        side_effect="readonly",
                        cost="cheap",
                        cancellability="cooperative",
                        reusability="adoptable",
                        speculate_mode="speculative_allowed",
                    )
                    record = await self._registry.start_shadow_task(
                        turn_id=effective_turn_id,
                        candidate_id=f"prepare_{call_id}",
                        tool_name=WriteToolPhases.prepare_shadow_execution_tool_name(),
                        normalized_args=prepare_args,
                        spec_key=prepare_spec_key,
                        env_fingerprint=env_fp,
                        policy=policy,
                    )
                    if self._task_group is not None and record.future is not None:
                        self._task_group.create_task(
                            _task_group_proxy(record.future),
                            name=f"shadow_group:{record.task_id}",
                        )
                return {"enabled": True, "result": None, "error": None}

            normalized_args = normalize_args(tool_name, arguments)
            env_fp = build_env_fingerprint()
            spec_key = build_spec_key(
                tool_name=tool_name,
                normalized_args=normalized_args,
                env_fingerprint=env_fp,
            )
            if not self._registry.exists_active(spec_key):
                policy = ToolSpecPolicy(
                    tool_name=tool_name,
                    side_effect="readonly",
                    cost="cheap",
                    cancellability="cooperative",
                    reusability="adoptable",
                    speculate_mode="speculative_allowed",
                    timeout_ms=_eval_read_shadow_timeout_ms(),
                )
                record = await self._registry.start_shadow_task(
                    turn_id=effective_turn_id,
                    candidate_id=call_id,
                    tool_name=tool_name,
                    normalized_args=normalized_args,
                    spec_key=spec_key,
                    env_fingerprint=env_fp,
                    policy=policy,
                )
                if self._task_group is not None and record.future is not None:
                    self._task_group.create_task(
                        _task_group_proxy(record.future),
                        name=f"shadow_group:{record.task_id}",
                    )
            return {"enabled": True, "result": None, "error": None}

        # Local speculation path when no registry-backed shadow task runner is configured.
        # Write tools are handled above because they require registry-backed prepare shadow.
        try:
            result = await self._speculative_executor.execute_speculative(
                tool_name,
                dict(arguments or {}),
                timeout_ms=_eval_read_shadow_timeout_ms(),
            )
        except ShadowExecutionError as exc:
            return {"enabled": True, "result": None, "error": str(exc)}
        return {"enabled": True, "result": result, "error": None}

    async def resolve_or_execute(
        self,
        *,
        turn_id: str,
        call_id: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Authoritative 阶段入口：ADOPT / JOIN / REPLAY 裁决."""
        if self._resolver is None:
            raise RuntimeError("resolver not configured")
        return await self._resolver.resolve_or_execute(
            turn_id=turn_id,
            call_id=call_id,
            tool_name=tool_name,
            args=args,
        )

    @property
    def metrics(self) -> SpeculationMetrics | None:
        """暴露本 turn 的推测执行指标记录器（可能为 None）."""
        return self._metrics

    def metrics_snapshot(self) -> dict[str, float | int]:
        """返回当前 turn 的推测执行指标快照；无 metrics 时返回空 dict."""
        if self._metrics is None:
            return {}
        return self._metrics.snapshot()

    def reset(self) -> None:
        """Clear the internal buffer and close the task group."""
        self._buffer.clear()
        if self._task_group is not None:
            self._task_group.close()

    # ------------------------------------------------------------------
    # Cross-turn speculation support for RoleSessionOrchestrator
    # ------------------------------------------------------------------

    def has_valid_speculation(self, session_id: str) -> bool:
        """Return True if a cross-turn speculation exists and is non-empty."""
        token = str(session_id or "").strip()
        if not token:
            return False
        cached = self._cross_turn_cache.get(token)
        if not isinstance(cached, dict):
            return False
        return bool(cached.get("tools") or cached.get("content"))

    async def consume_speculation(self, session_id: str) -> dict[str, Any]:
        """Consume and clear the cross-turn speculation for a session."""
        token = str(session_id or "").strip()
        cached = self._cross_turn_cache.pop(token, {})
        if not isinstance(cached, dict):
            return {}
        return dict(cached)

    def start_cross_turn_speculation(
        self,
        session_id: str,
        predicted_next_tools: list[dict[str, Any]] | None = None,
        hints: dict[str, Any] | None = None,
    ) -> None:
        """Start a cross-turn speculation for the next turn."""
        token = str(session_id or "").strip()
        if not token:
            return
        self._cross_turn_cache[token] = {
            "tools": list(predicted_next_tools) if predicted_next_tools else [],
            "hints": dict(hints) if hints else {},
            "timestamp_ms": __import__("time").time() * 1000,
        }

    def has_speculated_patch(self, intent: str) -> bool:
        """Return True if a speculated patch exists for the given intent."""
        token = str(intent or "").strip()
        if not token:
            return False
        cached = self._speculated_patch_cache.get(token)
        return isinstance(cached, dict) and bool(cached)

    async def consume_speculated_patch(self, intent: str) -> dict[str, Any]:
        """Consume and clear the speculated patch for the given intent."""
        token = str(intent or "").strip()
        cached = self._speculated_patch_cache.pop(token, {})
        if not isinstance(cached, dict):
            return {}
        return dict(cached)

    def cache_speculated_patch(self, intent: str, patch: dict[str, Any]) -> None:
        """Cache a speculated patch for future consumption."""
        token = str(intent or "").strip()
        if token and isinstance(patch, dict):
            self._speculated_patch_cache[token] = dict(patch)
