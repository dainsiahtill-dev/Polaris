"""Prompt Chunk Assembler for KernelOne context assembly.

Architecture:
    PromptChunkAssembler is the canonical entry point for assembling final
    prompt content from structured chunks. It integrates with budget tracking,
    cache control, and receipt generation.

Design constraints:
    - All text uses UTF-8 encoding.
    - Assembler is reusable across turns (reset between turns).
    - Only final state is emitted (no intermediate state printing).
    - Integrates with existing roles.kernel/prompt_builder (enhancement, not replacement).
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.llm.toolkit.contracts import TokenEstimatorPort

# Import metrics for intent switch tracking
try:
    from polaris.cells.roles.kernel.internal.metrics import get_dead_loop_metrics

    _METRICS_AVAILABLE = True
except ImportError:
    _METRICS_AVAILABLE = False

from .budget import ChunkBudgetTracker
from .receipt import (
    CompressionDecision,
    ContextOSReceipt,
    ContinuityDecision,
    FinalRequestReceipt,
    StrategyMetadata,
)
from .taxonomy import (
    CacheControl,
    ChunkMetadata,
    ChunkType,
    PromptChunk,
)

logger = logging.getLogger(__name__)


@dataclass
class AssemblyContext:
    """Context for a single assembly pass."""

    role_id: str = ""
    session_id: str = ""
    turn_index: int = 0
    model: str = ""
    provider: str = ""
    model_window: int = 128_000
    safety_margin: float = 0.85

    # Strategy metadata
    profile_id: str = ""
    profile_hash: str = ""
    strategy_bundle_hash: str = ""
    continuity_policy_id: str = ""
    compaction_policy_id: str = ""
    domain: str = ""

    # Continuity
    continuity_enabled: bool = False
    continuity_summary: str = ""
    continuity_summary_hash: str = ""
    continuity_source_messages: int = 0

    def to_strategy_metadata(self) -> StrategyMetadata | None:
        """Convert to StrategyMetadata if all fields are populated."""
        if not self.profile_id:
            return None
        return StrategyMetadata(
            profile_id=self.profile_id,
            profile_hash=self.profile_hash,
            strategy_bundle_hash=self.strategy_bundle_hash,
            continuity_policy_id=self.continuity_policy_id,
            compaction_policy_id=self.compaction_policy_id,
            domain=self.domain,
        )

    def to_continuity_decision(self) -> ContinuityDecision | None:
        """Convert to ContinuityDecision if continuity was used."""
        if not self.continuity_enabled:
            return None
        from polaris.kernelone.context._token_estimator import estimate_tokens

        return ContinuityDecision(
            enabled=True,
            summary_tokens=estimate_tokens(self.continuity_summary),
            summary_hash=self.continuity_summary_hash,
            source_messages=self.continuity_source_messages,
        )


@dataclass
class AssemblyResult:
    """Result of a prompt assembly pass."""

    messages: list[dict[str, Any]]  # Final messages to send
    receipt: FinalRequestReceipt
    admitted_chunks: list[PromptChunk]
    evicted_chunks: list[PromptChunk]

    # Budget state
    total_tokens: int
    effective_limit: int
    usage_ratio: float

    # Cache control info
    cache_control_applied: list[str]  # Chunk types with cache control


class PromptChunkAssembler:
    """Canonical chunk assembler for KernelOne prompt assembly.

    This class assembles prompt content from structured chunks, applying
    budget tracking, cache control, and generating debug receipts.

    Usage::

        assembler = PromptChunkAssembler(
            model_window=128_000,
            safety_margin=0.85,
        )

        # Add chunks
        assembler.add_chunk(PromptChunk(...))
        assembler.add_chunk(PromptChunk(...))

        # Assemble final prompt
        result = assembler.assemble(
            context=AssemblyContext(
                role_id="director",
                session_id="sess_123",
                model="claude-opus-4-5",
                provider="anthropic",
            )
        )

        # Get messages for LLM
        messages = result.messages

        # Get receipt for debugging
        print(result.receipt.to_human_readable())
    """

    def __init__(
        self,
        model_window: int = 128_000,
        safety_margin: float = 0.85,
        *,
        token_estimator: TokenEstimatorPort | None = None,
    ) -> None:
        self._model_window = model_window
        self._safety_margin = safety_margin
        self._token_estimator = token_estimator

        # Chunks accumulated in this pass
        self._chunks: list[PromptChunk] = []

        # Budget tracker
        self._tracker = ChunkBudgetTracker(
            model_window=model_window,
            safety_margin=safety_margin,
        )

        # Assembly timing
        self._assembly_start: float | None = None
        self._last_context_os_receipt: ContextOSReceipt | None = None

    @property
    def chunks(self) -> list[PromptChunk]:
        """Return all chunks added so far."""
        return list(self._chunks)

    def add_chunk(
        self,
        chunk_type: ChunkType,
        content: str,
        *,
        source: str = "",
        cache_control: CacheControl = CacheControl.EPHEMERAL,
        role_id: str = "",
        session_id: str = "",
        turn_index: int = 0,
        metadata_extra: dict[str, Any] | None = None,
    ) -> PromptChunk:
        """Add a chunk to the assembly queue.

        Args:
            chunk_type: Type of chunk
            content: Text content
            source: Human-readable source identifier
            cache_control: Cache control directive
            role_id: Role identifier
            session_id: Session identifier
            turn_index: Turn index
            metadata_extra: Extra metadata fields

        Returns:
            The created PromptChunk
        """
        # Compute content hash
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

        # Estimate tokens
        estimated_tokens = self._estimate_tokens(content)

        # Build metadata
        metadata = ChunkMetadata(
            chunk_type=chunk_type,
            source=source,
            cache_control=cache_control,
            content_hash=content_hash,
            created_at=time.time(),
            char_count=len(content),
            estimated_tokens=estimated_tokens,
            role_id=role_id,
            session_id=session_id,
            turn_index=turn_index,
        )

        # Apply extra metadata
        if metadata_extra:
            for key, value in metadata_extra.items():
                if hasattr(metadata, key):
                    object.__setattr__(metadata, key, value)

        chunk = PromptChunk(
            chunk_type=chunk_type,
            content=content,
            metadata=metadata,
        )

        self._chunks.append(chunk)
        return chunk

    def add_continuity(
        self,
        summary: str,
        *,
        source_messages: int = 0,
        context_os: Mapping[str, Any] | None = None,
        source: str = "session_continuity",
        role_id: str = "",
        session_id: str = "",
        turn_index: int = 0,
    ) -> PromptChunk:
        """Add a continuity summary chunk.

        Args:
            summary: Continuity summary text
            source_messages: Number of source messages summarized

        Returns:
            The created PromptChunk
        """
        self._last_context_os_receipt = self._build_context_os_receipt(context_os)
        rendered = self._render_continuity_block(summary, context_os=context_os)
        content_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]
        estimated_tokens = self._estimate_tokens(rendered)

        metadata = ChunkMetadata(
            chunk_type=ChunkType.CONTINUITY,
            source=source,
            cache_control=CacheControl.TRANSIENT,
            content_hash=content_hash,
            created_at=time.time(),
            char_count=len(rendered),
            estimated_tokens=estimated_tokens,
            role_id=role_id,
            session_id=session_id,
            turn_index=turn_index,
        )

        chunk = PromptChunk(
            chunk_type=ChunkType.CONTINUITY,
            content=rendered,
            metadata=metadata,
        )

        self._chunks.append(chunk)
        return chunk

    def _render_continuity_block(
        self,
        summary: str,
        *,
        context_os: Mapping[str, Any] | None = None,
    ) -> str:
        parts: list[str] = []
        summary_token = str(summary or "").strip()
        if summary_token:
            parts.append("【Session Continuity】\n" + summary_token)

        state_first = dict(context_os or {})
        if state_first:
            block_lines: list[str] = ["【State-First Context OS】"]
            run_card = state_first.get("run_card")
            head_anchor = str(state_first.get("head_anchor") or "").strip()
            tail_anchor = str(state_first.get("tail_anchor") or "").strip()
            if head_anchor:
                block_lines.append(head_anchor)
            if isinstance(run_card, Mapping):
                current_goal = str(run_card.get("current_goal") or "").strip()
                latest_intent = str(run_card.get("latest_user_intent") or "").strip()

                # Intent switch detection: view → write transition
                # Check if latest_intent indicates a new execution goal
                view_verbs = {
                    "看",
                    "分析",
                    "检查",
                    "读取",
                    "查看",
                    "探查",
                    "read",
                    "analyze",
                    "check",
                    "inspect",
                    "view",
                    "explore",
                }
                write_verbs = {
                    "写",
                    "创建",
                    "修改",
                    "生成",
                    "实现",
                    "write",
                    "create",
                    "edit",
                    "modify",
                    "generate",
                    "implement",
                    "build",
                }

                latest_has_write = any(v in latest_intent.lower() for v in write_verbs)
                goal_has_view = any(v in current_goal.lower() for v in view_verbs)

                if goal_has_view and latest_has_write and current_goal != latest_intent:
                    # Intent switch detected: extract summary instead of keeping full old goal
                    active_artifacts = run_card.get("active_artifacts")
                    recent_decisions = run_card.get("recent_decisions")
                    summary_parts = []
                    if active_artifacts:
                        summary_parts.append(f"已探明{len(active_artifacts)}个对象")
                    if recent_decisions:
                        summary_parts.append(
                            f"决策: {recent_decisions[-1] if isinstance(recent_decisions, (list, tuple)) else recent_decisions}"
                        )
                    if summary_parts:
                        block_lines.append(f"[已完成: {current_goal}] " + "; ".join(summary_parts))
                    else:
                        block_lines.append(f"[已完成: {current_goal}]")
                    block_lines.append(f"[!] 意图切换: 当前任务 → {latest_intent}")
                    # Record metrics for intent switch
                    if _METRICS_AVAILABLE:
                        get_dead_loop_metrics().record_intent_switch(current_goal, latest_intent)
                elif current_goal and current_goal not in "\n".join(block_lines):
                    block_lines.append(f"Current goal: {current_goal}")
                hard_constraints = run_card.get("hard_constraints")
                if isinstance(hard_constraints, list) and hard_constraints:
                    rendered_constraints = [
                        str(item or "").strip() for item in hard_constraints[:4] if str(item or "").strip()
                    ]
                    if rendered_constraints:
                        block_lines.append("Hard constraints: " + "; ".join(rendered_constraints))
                next_action = str(run_card.get("next_action_hint") or "").strip()
                if next_action:
                    block_lines.append("Next action: " + next_action)
            task_state = state_first.get("task_state")
            if isinstance(task_state, Mapping):
                current_goal_obj = task_state.get("current_goal")
                if isinstance(current_goal_obj, Mapping):
                    goal_value_raw = current_goal_obj.get("value")
                    goal_value = str(goal_value_raw or "").strip()
                    if goal_value and goal_value not in "\n".join(block_lines):
                        block_lines.append(f"Current goal: {goal_value}")
            decision_log = state_first.get("decision_log")
            if isinstance(decision_log, list) and decision_log:
                rendered_decisions = [
                    str(item.get("summary") or "").strip()
                    for item in decision_log
                    if isinstance(item, Mapping) and str(item.get("summary") or "").strip()
                ]
                if rendered_decisions:
                    block_lines.append("Decisions: " + "; ".join(rendered_decisions[:4]))
            active_entities = state_first.get("active_entities")
            if not isinstance(active_entities, list) and isinstance(run_card, Mapping):
                active_entities = run_card.get("active_entities")
            if isinstance(active_entities, list) and active_entities:
                rendered_entities = [str(item or "").strip() for item in active_entities if str(item or "").strip()]
                if rendered_entities:
                    block_lines.append("Active entities: " + "; ".join(rendered_entities[:6]))
            artifact_stubs = state_first.get("artifact_stubs")
            if not isinstance(artifact_stubs, list) and isinstance(run_card, Mapping):
                run_card_artifacts = run_card.get("active_artifacts")
                if isinstance(run_card_artifacts, list) and run_card_artifacts:
                    artifact_stubs = [
                        {"artifact_id": str(item or "").strip(), "type": "active_artifact", "peek": ""}
                        for item in run_card_artifacts
                        if str(item or "").strip()
                    ]
            if isinstance(artifact_stubs, list) and artifact_stubs:
                rendered_artifacts: list[str] = []
                for item in artifact_stubs[:4]:
                    if not isinstance(item, Mapping):
                        continue
                    artifact_id = str(item.get("artifact_id") or "").strip()
                    artifact_type = str(item.get("type") or "").strip()
                    peek = str(item.get("peek") or "").strip()
                    if artifact_id:
                        rendered_artifacts.append(f"{artifact_id}<{artifact_type}> {peek}".strip())
                if rendered_artifacts:
                    block_lines.append("Artifacts: " + "; ".join(rendered_artifacts))
            episode_cards = state_first.get("episode_cards")
            if isinstance(episode_cards, list) and episode_cards:
                rendered_episodes = [
                    str(item.get("digest_64") or "").strip()
                    for item in episode_cards[:3]
                    if isinstance(item, Mapping) and str(item.get("digest_64") or "").strip()
                ]
                if rendered_episodes:
                    block_lines.append("Episodes: " + "; ".join(rendered_episodes))
            slice_plan = state_first.get("context_slice_plan")
            if isinstance(slice_plan, Mapping):
                pressure = str(slice_plan.get("pressure_level") or "").strip()
                if pressure:
                    block_lines.append(f"Pressure level: {pressure}")
            if tail_anchor:
                block_lines.append(tail_anchor)
            parts.append("\n".join(block_lines))
        return "\n\n".join(part for part in parts if str(part or "").strip()).strip()

    def assemble(
        self,
        context: AssemblyContext,
    ) -> AssemblyResult:
        """Assemble chunks into final prompt messages.

        This is the main entry point for prompt assembly. It:
        1. Records assembly start time
        2. Applies budget tracking (evicts low-priority chunks)
        3. Applies cache control
        4. Generates final receipt

        Args:
            context: Assembly context with model/session info

        Returns:
            AssemblyResult with messages and receipt
        """
        self._assembly_start = time.time()
        self._tracker.reset()

        # Try to admit all chunks
        admitted, evicted = self._tracker.try_admit_many(self._chunks)

        # Mark evicted chunks
        for chunk in evicted:
            object.__setattr__(
                chunk.metadata,
                "was_evicted",
                True,
            )
            object.__setattr__(
                chunk.metadata,
                "eviction_reason",
                "Budget exceeded",
            )

        # Build eviction decisions for receipt
        eviction_decisions = [
            CompressionDecision(
                chunk_type=c.chunk_type.value,
                reason=c.metadata.eviction_reason or "Budget exceeded",
                tokens_freed=c.tokens,
                method="evicted",
            )
            for c in evicted
        ]

        # Build messages (in tier order)
        messages = self._build_messages(admitted)

        # Apply cache control
        cache_control_applied = self._apply_cache_control(messages, admitted)

        # Compute timing
        assembly_duration_ms = int((time.time() - self._assembly_start) * 1000) if self._assembly_start else 0

        # Build receipt
        receipt = FinalRequestReceipt.build(
            chunks=admitted,
            model=str(context.model) if context.model else str(self._model_window),
            provider=context.provider,
            model_window=context.model_window or self._model_window,
            safety_margin=context.safety_margin or self._safety_margin,
            role_id=context.role_id,
            session_id=context.session_id,
            turn_index=context.turn_index,
            continuity=context.to_continuity_decision(),
            context_os=self._last_context_os_receipt,
            strategy=context.to_strategy_metadata(),
            eviction_decisions=eviction_decisions,
            assembly_start=datetime.fromtimestamp(self._assembly_start, tz=timezone.utc).isoformat()
            if self._assembly_start
            else "",
            assembly_duration_ms=assembly_duration_ms,
            cache_control_applied=cache_control_applied,
        )

        # Budget state
        budget = self._tracker.get_current_budget()

        return AssemblyResult(
            messages=messages,
            receipt=receipt,
            admitted_chunks=admitted,
            evicted_chunks=evicted,
            total_tokens=budget.total_tokens,
            effective_limit=budget.effective_limit,
            usage_ratio=budget.usage_ratio,
            cache_control_applied=cache_control_applied,
        )

    def reset(self) -> None:
        """Reset assembler for next assembly pass."""
        self._chunks.clear()
        self._tracker.reset()
        self._assembly_start = None
        self._last_context_os_receipt = None

    def _build_context_os_receipt(
        self,
        context_os: Mapping[str, Any] | None,
    ) -> ContextOSReceipt | None:
        if not isinstance(context_os, Mapping):
            return None
        run_card = context_os.get("run_card")
        slice_plan = context_os.get("context_slice_plan")
        active_entities = context_os.get("active_entities")
        active_artifacts = context_os.get("active_artifacts")
        episode_cards = context_os.get("episode_cards")
        hard_constraints = run_card.get("hard_constraints") if isinstance(run_card, Mapping) else None
        open_loops = run_card.get("open_loops") if isinstance(run_card, Mapping) else None
        included = slice_plan.get("included") if isinstance(slice_plan, Mapping) else None
        excluded = slice_plan.get("excluded") if isinstance(slice_plan, Mapping) else None
        return ContextOSReceipt(
            adapter_id=str(context_os.get("adapter_id") or "").strip(),
            current_goal=str((run_card or {}).get("current_goal") or "").strip()
            if isinstance(run_card, Mapping)
            else "",
            next_action_hint=str((run_card or {}).get("next_action_hint") or "").strip()
            if isinstance(run_card, Mapping)
            else "",
            pressure_level=str((slice_plan or {}).get("pressure_level") or "").strip()
            if isinstance(slice_plan, Mapping)
            else "",
            hard_constraint_count=len(hard_constraints) if isinstance(hard_constraints, list) else 0,
            open_loop_count=len(open_loops) if isinstance(open_loops, list) else 0,
            active_entity_count=len(active_entities) if isinstance(active_entities, list) else 0,
            active_artifact_count=len(active_artifacts) if isinstance(active_artifacts, list) else 0,
            episode_count=len(episode_cards) if isinstance(episode_cards, list) else 0,
            included_count=len(included) if isinstance(included, list) else 0,
            excluded_count=len(excluded) if isinstance(excluded, list) else 0,
        )

    def _estimate_tokens(self, content: str) -> int:
        """Estimate tokens for content.

        Note (P1-CTX-006 convergence):
            Uses injected estimator if available, otherwise delegates to
            the canonical token_estimator module for consistency.
        """
        if not content:
            return 0

        # Use injected estimator if available
        if self._token_estimator is not None:
            try:
                result = self._token_estimator.estimate_messages_tokens([{"role": "user", "content": content}])
                if isinstance(result, int) and result >= 0:
                    return result
            except (RuntimeError, ValueError) as exc:
                logger.debug(
                    "_estimate_content_tokens: estimator failed, using fallback: %s",
                    exc,
                )

        # Fallback: use canonical token estimator (P1-CTX-006 convergence)
        from polaris.kernelone.context._token_estimator import estimate_tokens

        return estimate_tokens(content)

    def _build_messages(
        self,
        chunks: list[PromptChunk],
    ) -> list[dict[str, Any]]:
        """Build chat messages from chunks in priority order."""
        # Sort by eviction priority (lower number = higher priority)
        sorted_chunks = sorted(
            chunks,
            key=lambda c: c.chunk_type.eviction_priority,
        )

        # Build messages
        messages: list[dict[str, Any]] = []
        for chunk in sorted_chunks:
            messages.append(chunk.to_message())

        return messages

    def _apply_cache_control(
        self,
        messages: list[dict[str, Any]],
        chunks: list[PromptChunk],
    ) -> list[str]:
        """Apply cache control headers to eligible messages.

        Returns list of chunk types that had cache control applied.
        """
        applied: list[str] = []

        # Only apply to the last message of each cacheable chunk type
        chunk_type_to_last_idx: dict[str, int] = {}
        for idx, chunk in enumerate(chunks):
            ct = chunk.chunk_type.value
            if chunk.chunk_type.cacheable:
                chunk_type_to_last_idx[ct] = idx

        for ct, idx in chunk_type_to_last_idx.items():
            if idx < len(messages):
                msg = messages[idx]
                content = msg.get("content", "")

                # Convert to cacheable format if string
                if isinstance(content, str):
                    msg["content"] = [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                    applied.append(ct)

        return applied


__all__ = [
    "AssemblyContext",
    "AssemblyResult",
    "PromptChunkAssembler",
]
