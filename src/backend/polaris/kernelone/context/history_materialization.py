"""History Materialization & Session Continuity Strategy.

WS3: Shared Agent Foundation Convergence.
Converges the ad-hoc continuity and history materialization logic from
console_host.py and context_gateway.py into a unified strategy layer.

This module provides:
  - SessionContinuityStrategy: implements SessionContinuityStrategyPort +
    HistoryMaterializationStrategyPort; wraps SessionContinuityEngine with
    profile-driven overrides.
  - HistoryMaterializationStrategy: implements HistoryMaterializationStrategyPort;
    unifies history + tool receipt micro-compaction.

All strategy implementations delegate to the existing SessionContinuityEngine
and RoleContextCompressor; no existing behavior is modified.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from polaris.kernelone.context.chunks import PromptChunkAssembler
from polaris.kernelone.context.context_os import ContextOSInvariantViolation
from polaris.kernelone.context.session_continuity import (
    SessionContinuityEngine,
    SessionContinuityPack,
    SessionContinuityPolicy,
    SessionContinuityProjection,
    SessionContinuityRequest,
)
from polaris.kernelone.llm.reasoning import ReasoningStripper

from .strategy_contracts import (
    HistoryMaterialization,
)

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Receipt micro-compaction helpers
# ------------------------------------------------------------------


def _micro_compact_receipts(
    receipts: list[dict[str, Any]],
    *,
    keep_count: int = 3,
    threshold_chars: int = 500,
) -> tuple[list[dict[str, Any]], int]:
    """Apply receipt micro-compaction with artifact-aware stubs.

    Rules:
      - 3+ consecutive identical tool calls → keep last 1 + tool name
      - Success tool results > threshold_chars → replace with a restorable stub
      - Failed tool calls → preserve error type + key args

    Args:
        receipts: Raw tool receipt list (each dict has at minimum 'tool' and 'result').
        keep_count: Number of recent receipts to keep intact.
        threshold_chars: Chars above which results are truncated.

    Returns:
        Tuple of:
          - new list of (possibly compacted) receipt dicts
          - number of artifact stubs emitted
    """
    if not receipts:
        return [], 0

    # Separate success / failure receipts
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for r in receipts:
        is_error = bool(r.get("error") or r.get("is_error"))
        if is_error or _is_error_result(r):
            failures.append(dict(r))
        else:
            successes.append(dict(r))

    # Keep last `keep_count` successes intact
    keep = successes[-keep_count:] if keep_count > 0 else []
    compact: list[dict[str, Any]] = list(successes[:-keep_count]) if keep_count > 0 else list(successes)

    artifact_stub_count = 0

    # Offload older successes that exceed threshold
    for i, r in enumerate(compact):
        stubbed = _stub_receipt_content(r, threshold_chars=threshold_chars)
        if stubbed is not None:
            compact[i] = stubbed
            artifact_stub_count += 1

    # Compact 3+ consecutive identical tool calls
    compact = _compact_repeated_tools(compact)

    # Failures: preserve error type + key args (never truncate)
    compact.extend(failures)

    return keep + compact, artifact_stub_count


def _is_error_result(receipt: dict[str, Any]) -> bool:
    """Return True if the receipt represents a failed tool call."""
    success = receipt.get("success")
    if isinstance(success, bool):
        return not success
    ok = receipt.get("ok")
    if isinstance(ok, bool):
        return not ok
    return bool(receipt.get("error"))


def _stub_receipt_content(
    receipt: dict[str, Any],
    *,
    threshold_chars: int = 500,
) -> dict[str, Any] | None:
    """Replace oversized receipt content with a typed artifact stub.

    Returns a new receipt dict with stub content, or None if threshold not exceeded.
    The original receipt is preserved unchanged.
    """
    result_payload = receipt.get("result")
    target = receipt.get("content") or (result_payload.get("content", "") if isinstance(result_payload, dict) else "")
    text = str(target)
    if len(text) <= threshold_chars:
        return None

    tool_name = str(receipt.get("tool") or receipt.get("name") or "unknown").strip() or "unknown"
    artifact_type = _infer_receipt_artifact_type(tool_name)
    artifact_id = _stable_receipt_artifact_id(receipt, text=text, tool_name=tool_name)
    preview = _receipt_preview(text, limit=min(max(threshold_chars // 2, 80), 180))
    stub = {
        "artifact_id": artifact_id,
        "type": artifact_type,
        "chars": len(text),
        "preview": preview,
        "restore_hint": "read from raw receipt/transcript source-of-truth",
    }
    stub_text = f"[artifact_stub:{artifact_id} type={artifact_type} chars={len(text)} preview={preview}]"

    # Create new receipt dict to avoid modifying original
    modified_receipt = dict(receipt)

    # T6-8 Fix: Properly merge stub content while preserving other fields
    if "content" in modified_receipt:
        modified_receipt["content"] = stub_text
    elif isinstance(result_payload, dict):
        # Clone result_payload and add stub content
        cloned_result = dict(result_payload)
        cloned_result["content"] = stub_text
        # Keep other fields from result_payload (like success, error, etc.)
        for key, value in result_payload.items():
            if key != "content":
                cloned_result.setdefault(key, value)
        modified_receipt["result"] = cloned_result

    modified_receipt["_artifact_stub"] = stub
    modified_receipt["_compacted"] = True
    modified_receipt["_original_char_count"] = len(text)

    return modified_receipt


def _infer_receipt_artifact_type(tool_name: str) -> str:
    token = str(tool_name or "").strip().lower()
    if any(item in token for item in ("read", "open", "file")):
        return "file_excerpt"
    if any(item in token for item in ("search", "grep", "query")):
        return "search_result"
    return "tool_result"


def _stable_receipt_artifact_id(
    receipt: dict[str, Any],
    *,
    text: str,
    tool_name: str,
) -> str:
    explicit = str(receipt.get("artifact_id") or receipt.get("receipt_id") or "").strip()
    if explicit:
        return explicit
    # Use UUID for uniqueness — content hash (below) is only for integrity verification
    return f"art_{uuid.uuid4().hex[:12]}"


def _receipt_preview(text: str, *, limit: int = 120) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _compact_repeated_tools(
    receipts: list[dict[str, Any]],
    *,
    repeat_threshold: int = 3,
) -> list[dict[str, Any]]:
    """Collapse runs of 3+ identical tool calls to one + summary."""
    if len(receipts) < repeat_threshold:
        return receipts

    result: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []

    def _flush_run() -> None:
        nonlocal run
        if not run:
            return
        if len(run) >= repeat_threshold:
            last = run[-1]
            tool = str(last.get("tool") or last.get("name") or "unknown")
            artifact_stub = next(
                (item.get("_artifact_stub") for item in reversed(run) if isinstance(item.get("_artifact_stub"), dict)),
                None,
            )
            summary_content = f"[{len(run)}x {tool} — repeated calls collapsed by history materialization]"
            if isinstance(artifact_stub, dict):
                artifact_id = str(artifact_stub.get("artifact_id") or "").strip()
                if artifact_id:
                    summary_content = f"{summary_content} (last_artifact={artifact_id})"
            result.append(
                {
                    "tool": tool,
                    "content": summary_content,
                    "_compacted": True,
                    "_original_count": len(run),
                    "_artifact_stub": artifact_stub if isinstance(artifact_stub, dict) else None,
                }
            )
        else:
            result.extend(run)
        run = []

    for r in receipts:
        if run and r.get("tool") == run[-1].get("tool"):
            run.append(r)
        else:
            _flush_run()
            run = [r]
    _flush_run()
    return result


# ------------------------------------------------------------------
# Profile overrides → engine parameter mapping
# ------------------------------------------------------------------


def _build_continuity_policy(overrides: dict[str, Any]) -> SessionContinuityPolicy:
    """Build SessionContinuityPolicy from a profile overrides dict.

    Maps strategy_contracts/strategy_profiles override keys
    to SessionContinuityEngine policy parameters.
    """
    sc = overrides.get("session_continuity", {})
    return SessionContinuityPolicy(
        default_history_window_messages=int(sc.get("history_window_messages", 8)),
        max_history_window_messages=int(sc.get("history_window_messages", 8)) * 3,
        max_continuity_source_messages=int(sc.get("max_continuity_source_messages", 40)),
        max_summary_items=int(sc.get("max_summary_items", 8)),
        max_summary_chars=int(sc.get("max_summary_chars", 1500)),
        max_stable_facts=int(sc.get("max_stable_facts", 6)),
        max_open_loops=int(sc.get("max_open_loops", 6)),
        summary_focus=str(sc.get("summary_focus", "")),
    )


# ------------------------------------------------------------------
# SessionContinuityStrategy
# ------------------------------------------------------------------


class SessionContinuityStrategy:
    """Strategy-backed session continuity engine.

    Implements:
      - SessionContinuityStrategyPort: profile-driven session continuity projection
      - HistoryMaterializationStrategyPort: profile-driven history materialization

    Wraps SessionContinuityEngine; applies profile overrides to policy.
    Deterministic fallback is preserved (no LLM dependency).

    Usage::

        strategy = SessionContinuityStrategy(profile_overrides=overrides)
        pack_dict = await strategy.project(request={"session_id": ..., "messages": [...], ...})
        materialization = strategy.materialize(messages=[...], receipts=[...])
    """

    def __init__(
        self,
        profile_overrides: dict[str, Any] | None = None,
        *,
        # Backward-compatible direct parameters
        policy: SessionContinuityPolicy | None = None,
    ) -> None:
        self._profile_overrides = dict(profile_overrides or {})
        # Explicit policy wins over profile overrides (backward compat)
        self._policy = policy or _build_continuity_policy(self._profile_overrides)
        self._engine = SessionContinuityEngine(policy=self._policy)
        self._stripper = ReasoningStripper()

    # ------------------------------------------------------------------
    # SessionContinuityStrategyPort
    # ------------------------------------------------------------------

    async def project(self, request: dict[str, Any]) -> dict[str, Any]:
        """Project session continuity for the given request.

        Implements SessionContinuityStrategyPort.project().
        Converts dict to SessionContinuityRequest, delegates to engine,
        returns SessionContinuityPack-compatible dict.

        Args:
            request: Dict with keys matching SessionContinuityRequest fields:
                - session_id: str
                - role: str
                - workspace: str
                - session_title: str (optional)
                - messages: list[dict] (optional)
                - session_context_config: dict (optional)
                - incoming_context: dict (optional)
                - history_limit: int (optional)
                - focus: str (optional)

        Returns:
            dict compatible with SessionContinuityPack.to_dict().
        """
        try:
            typed_request = SessionContinuityRequest(
                session_id=str(request.get("session_id") or ""),
                role=str(request.get("role") or "unknown"),
                workspace=str(request.get("workspace") or ""),
                session_title=str(request.get("session_title", "")),
                messages=tuple(request.get("messages", [])),
                session_context_config=request.get("session_context_config"),
                incoming_context=request.get("incoming_context"),
                history_limit=request.get("history_limit"),
                focus=str(request.get("focus", "")),
            )
        except (RuntimeError, ValueError) as exc:
            _logger.warning("SessionContinuityStrategy.project: failed to build request: %s", exc)
            return {}

        try:
            projection = await self._engine.project(typed_request)
        except ContextOSInvariantViolation as exc:
            _logger.warning("SessionContinuityStrategy.project: invalid Context OS projection: %s", exc)
            return {}
        pack = projection.continuity_pack
        if pack is None:
            return {}
        return pack.to_dict()

    async def project_to_projection(self, request: dict[str, Any]) -> SessionContinuityProjection | None:
        """Project session continuity and return the full projection object.

        Unlike project() which returns a dict (SessionContinuityStrategyPort interface),
        this returns the raw SessionContinuityProjection so callers can access
        .recent_messages, .changed, .prompt_context, etc.

        Args:
            request: Same dict as project() — SessionContinuityRequest fields.

        Returns:
            SessionContinuityProjection or None on error.
        """
        try:
            typed_request = SessionContinuityRequest(
                session_id=str(request.get("session_id") or ""),
                role=str(request.get("role") or "unknown"),
                workspace=str(request.get("workspace") or ""),
                session_title=str(request.get("session_title", "")),
                messages=tuple(request.get("messages", [])),
                session_context_config=request.get("session_context_config"),
                incoming_context=request.get("incoming_context"),
                history_limit=request.get("history_limit"),
                focus=str(request.get("focus", "")),
            )
        except (RuntimeError, ValueError) as exc:
            _logger.warning("SessionContinuityStrategy.project_to_projection: failed to build request: %s", exc)
            return None
        try:
            return await self._engine.project(typed_request)
        except ContextOSInvariantViolation as exc:
            _logger.warning("SessionContinuityStrategy.project_to_projection: invalid Context OS projection: %s", exc)
            return None

    async def build_continuity_prompt_block(self, request: dict[str, Any]) -> str:
        """Render a prompt-facing continuity block from a continuity request."""
        projection = await self.project_to_projection(request)
        if projection is None:
            return ""
        return self.build_continuity_prompt_block_from_projection(projection)

    def build_continuity_prompt_block_from_projection(
        self,
        projection: SessionContinuityProjection | None,
    ) -> str:
        """Render a prompt-facing continuity block from an existing projection."""
        if projection is None:
            return ""
        summary = (
            str(projection.continuity_pack.summary or "").strip() if projection.continuity_pack is not None else ""
        )
        context_os = (
            projection.prompt_context.get("state_first_context_os")
            if isinstance(projection.prompt_context.get("state_first_context_os"), dict)
            else None
        )
        assembler = PromptChunkAssembler(model_window=128_000, safety_margin=0.85)
        chunk = assembler.add_continuity(
            summary,
            source_messages=(
                int(projection.continuity_pack.source_message_count) if projection.continuity_pack is not None else 0
            ),
            context_os=context_os,
            source="session_continuity",
        )
        return chunk.content

    # ------------------------------------------------------------------
    # HistoryMaterializationStrategyPort
    # ------------------------------------------------------------------

    def materialize(
        self,
        messages: list[dict[str, Any]],
        receipts: list[dict[str, Any]],
    ) -> HistoryMaterialization:
        """Apply history materialization to messages and tool receipts.

        Implements HistoryMaterializationStrategyPort.materialize().
        Strategy: receipt micro-compact + optional continuity pack injection.

        Args:
            messages: Role message history (list of dicts with 'role'/'content').
            receipts: Tool receipt list (list of dicts with 'tool'/'content'/'result').

        Returns:
            HistoryMaterialization with token counts and flags.
        """
        # Strip reasoning/thinking content before any history/token materialization.
        cleaned_messages = self._stripper.strip_from_history(
            [dict(item) for item in messages if isinstance(item, dict)]
        )
        cleaned_receipts = self._stripper.strip_from_history(
            [dict(item) for item in receipts if isinstance(item, dict)]
        )

        # Apply receipt micro-compact based on profile overrides
        compaction_cfg = self._profile_overrides.get("compaction", {})
        keep_count = int(compaction_cfg.get("micro_compact_keep", 3))
        threshold = int(compaction_cfg.get("compress_threshold_chars", 500))
        enabled = compaction_cfg.get("receipt_micro_compact", True)

        if enabled and cleaned_receipts:
            compacted_receipts, artifact_stub_count = _micro_compact_receipts(
                cleaned_receipts,
                keep_count=keep_count,
                threshold_chars=threshold,
            )
            micro_compacted = len(compacted_receipts) < len(cleaned_receipts)
        else:
            compacted_receipts = list(cleaned_receipts)
            micro_compacted = False
            artifact_stub_count = 0

        # Token estimation (rough, deterministic)
        msg_tokens = _estimate_tokens_for_messages(cleaned_messages)
        rcpt_tokens = _estimate_tokens_for_messages(compacted_receipts)

        return HistoryMaterialization(
            history_tokens=msg_tokens,
            receipt_tokens=rcpt_tokens,
            total_tokens=msg_tokens + rcpt_tokens,
            message_count=len(messages),
            receipt_count=len(compacted_receipts),
            micro_compacted=micro_compacted,
            artifact_stub_count=artifact_stub_count,
            materialized_messages=tuple(dict(item) for item in cleaned_messages),
            materialized_receipts=tuple(dict(item) for item in compacted_receipts),
        )

    # ------------------------------------------------------------------
    # Convenience: direct engine access for backward compatibility
    # ------------------------------------------------------------------

    async def build_pack(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> SessionContinuityPack | None:
        """Backward-compatible pack builder.

        Delegates directly to the wrapped SessionContinuityEngine.
        Prefer project() for strategy-compliant usage.
        """
        return await self._engine.build_pack(messages, **kwargs)

    @property
    def policy(self) -> SessionContinuityPolicy:
        """Return the active continuity policy (for diagnostics)."""
        return self._policy


# ------------------------------------------------------------------
# HistoryMaterializationStrategy
# ------------------------------------------------------------------


class HistoryMaterializationStrategy:
    """Unified history + tool receipt materialization strategy.

    Implements HistoryMaterializationStrategyPort.
    Composes SessionContinuityStrategy for continuity projection
    and applies profile-driven receipt micro-compaction.

    This is the canonical entry point for prompt history assembly.
    Use when you need both continuity (older turns) and
    materialization (recent receipts) in a single pass.

    Usage::

        strategy = HistoryMaterializationStrategy(profile_overrides=profile.overrides)
        materialization = strategy.materialize(messages=[...], receipts=[...])
        continuity_dict = await strategy.get_continuity_pack(request={...})
    """

    def __init__(
        self,
        profile_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._profile_overrides = dict(profile_overrides or {})
        self._session_strategy = SessionContinuityStrategy(
            profile_overrides=self._profile_overrides,
        )

    def materialize(
        self,
        messages: list[dict[str, Any]],
        receipts: list[dict[str, Any]],
    ) -> HistoryMaterialization:
        """Apply unified history materialization.

        Implements HistoryMaterializationStrategyPort.materialize().

        Pipeline:
          1. Apply receipt micro-compact (per profile overrides)
          2. Estimate tokens for messages and compacted receipts
          3. Return HistoryMaterialization result

        Note: Continuity pack generation is handled separately by
        get_continuity_pack() to keep message flow pure.
        """
        return self._session_strategy.materialize(messages, receipts)

    async def get_continuity_pack(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate continuity pack from a session continuity request.

        Args:
            request: SessionContinuityRequest-compatible dict.

        Returns:
            SessionContinuityPack-compatible dict.
        """
        return await self._session_strategy.project(request)

    async def get_continuity_prompt_block(
        self,
        request: dict[str, Any],
    ) -> str:
        """Render a prompt-facing continuity block for continuity-aware consumers."""
        return await self._session_strategy.build_continuity_prompt_block(request)

    @property
    def session_strategy(self) -> SessionContinuityStrategy:
        """Expose the underlying session continuity strategy (for advanced use)."""
        return self._session_strategy


# ------------------------------------------------------------------
# Token estimation (deterministic fallback)
# ------------------------------------------------------------------


def _estimate_tokens_for_messages(items: list[dict[str, Any]]) -> int:
    """Rough deterministic token estimation for messages/receipts.

    Uses the same heuristic as RoleContextGateway:
      - ASCII: 4 chars/token
      - CJK: 1.5 chars/token
    """
    if not items:
        return 0
    total = 0
    for item in items:
        content = str(item.get("content") or item.get("message") or "")
        if not content:
            total += 4  # minimal overhead per empty item
            continue
        ascii_chars = sum(1 for c in content if ord(c) < 128)
        cjk_chars = len(content) - ascii_chars
        total += int(ascii_chars / 4) + int(cjk_chars * 1.5) + 4
    return max(1, total)


__all__ = [
    "HistoryMaterializationStrategy",
    "SessionContinuityStrategy",
    "_estimate_tokens_for_messages",
    "_micro_compact_receipts",
]
