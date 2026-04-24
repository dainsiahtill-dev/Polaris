"""KernelOne context compaction primitives.

This module owns the reusable context compaction capability for
AI/Agent runtimes. It intentionally excludes message-queue or
role-lifecycle concerns.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.abc import LLMClient

from polaris.kernelone.context.control_plane_noise import is_control_plane_noise
from polaris.kernelone.llm.toolkit.contracts import ServiceLocator


@runtime_checkable
class TranscriptServicePort(Protocol):
    """Minimal transcript persistence contract used by context compaction."""

    def record_message(self, *args: Any, **kwargs: Any) -> Any: ...


_HIGH_SIGNAL_TERMS = (
    "error",
    "traceback",
    "exception",
    "failed",
    "failure",
    "fix",
    "bug",
    "refactor",
    "session",
    "history",
    "context",
    "compaction",
    "summary",
    "prompt",
    "tool",
    "stream",
    "output",
    "kernel",
    "workspace",
    "project",
    "code",
    "test",
    "patch",
    "diff",
    "session_id",
    "history_limit",
    "session/history/context",
    "错误",
    "异常",
    "失败",
    "修复",
    "重构",
    "会话",
    "历史",
    "上下文",
    "压缩",
    "摘要",
    "提示词",
    "工具",
    "流式",
    "输出",
    "工作区",
    "项目",
    "代码",
    "测试",
    "补丁",
)
_LOW_SIGNAL_PATTERNS = (
    r"^(hi|hello|hey|你好|您好|嗨|thanks|thank you|谢谢|ok|好的|收到|稍等|bye|再见)\b",
    r"(换个名字|改名字|改名|叫我|叫你|你是什么模型|what model are you|who are you)",
)
_CODE_PATH_RE = re.compile(
    r"([A-Za-z]:\\|[/\\]|`[^`]+\.(py|md|ya?ml|json|toml|ts|tsx|js|jsx|sql|sh|ps1)`|\b[\w.-]+\.(py|md|ya?ml|json|toml|ts|tsx|js|jsx|sql|sh|ps1)\b)",
    re.IGNORECASE,
)


def _normalize_continuity_text(value: Any) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text.strip()


def _trim_continuity_snippet(text: str, *, max_chars: int = 220) -> str:
    if len(text) <= max_chars:
        return text
    head = max(1, int(max_chars * 0.72))
    tail = max(1, max_chars - head - 14)
    return f"{text[:head].rstrip()} ...[snip]... {text[-tail:].lstrip()}"


def _continuity_signal_score(role: str, text: str) -> int:
    """Calculate signal score for continuity scoring.

    T3-4: Unified threshold from 40 to 48 (conservative threshold) for
    "meaningful text" to match compaction.py.
    """
    lowered = text.lower()
    score = 0
    if is_control_plane_noise(lowered):
        return -4
    if role == "user":
        score += 1
    if len(text) >= 48:  # T3-4: Unified to 48 (was 40 in session_continuity.py)
        score += 1
    if any(term in lowered for term in _HIGH_SIGNAL_TERMS):
        score += 3
    if _CODE_PATH_RE.search(text):
        score += 2
    if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in _LOW_SIGNAL_PATTERNS):
        score -= 4
    return score


def build_continuity_summary_text(
    messages: list[Mapping[str, Any]] | list[dict[str, Any]],
    identity: RoleContextIdentity | None = None,
    *,
    focus: str = "",
    max_items: int = 8,
    max_chars: int = 1600,
) -> str:
    """Build a deterministic continuity summary for older conversation turns.

    The summary intentionally keeps only high-signal snippets from earlier turns
    so resumed sessions do not re-inject stale social/meta chatter as if it were
    current task context.
    """
    normalized_records: list[dict[str, Any]] = []
    for index, item in enumerate(messages or []):
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = _normalize_continuity_text(item.get("content") or item.get("message") or "")
        if not role or not content:
            continue
        score = _continuity_signal_score(role, content)
        normalized_records.append(
            {
                "index": index,
                "role": role,
                "content": content,
                "score": score,
            }
        )

    if not normalized_records:
        return ""

    selected = [item for item in normalized_records if int(item.get("score") or 0) > 0]
    if not selected:
        selected = normalized_records[-min(max_items, len(normalized_records)) :]
    elif len(selected) > max_items:
        selected = selected[-max_items:]

    lines: list[str] = ["Context continuity summary"]
    if identity is not None:
        role_type = str(identity.role_type or "").strip()
        goal = _normalize_continuity_text(identity.goal)
        if role_type:
            lines.append(f"Role: {role_type}")
        if goal:
            lines.append(f"Goal: {_trim_continuity_snippet(goal, max_chars=180)}")
        if identity.acceptance_criteria:
            acceptance = "; ".join(
                _trim_continuity_snippet(_normalize_continuity_text(item), max_chars=120)
                for item in identity.acceptance_criteria[:4]
                if _normalize_continuity_text(item)
            )
            if acceptance:
                lines.append(f"Acceptance: {acceptance}")
    focus_text = _normalize_continuity_text(focus)
    if focus_text:
        lines.append(f"Focus: {_trim_continuity_snippet(focus_text, max_chars=180)}")

    lines.append("Persisted signal from earlier turns:")
    role_labels = {
        "user": "User",
        "assistant": "Assistant",
        "system": "System",
        "tool": "Tool",
    }
    for item in selected:
        label = role_labels.get(str(item["role"]), str(item["role"]).title())
        snippet = _trim_continuity_snippet(str(item["content"]), max_chars=220)
        lines.append(f"- {label}: {snippet}")

    omitted = max(0, len(normalized_records) - len(selected))
    if omitted:
        lines.append(f"Omitted low-signal or redundant turns: {omitted}")

    summary = "\n".join(lines).strip()
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rstrip() + "..."
    return summary


@dataclass
class CompactSnapshot:
    """Snapshot of context before/after compression.

    Universal data structure for tracking context compression across all roles.
    """

    timestamp: float
    original_tokens: int
    compressed_tokens: int
    original_hash: str
    summary_hash: str
    method: str  # "micro" | "truncate" | "llm"
    transcript_path: str | None = None
    role_name: str | None = None  # Track which role performed compression


@dataclass
class RoleContextIdentity:
    """Role context identity for context preservation during compression.

    This is a universal identity model that any role can use to maintain
    context continuity after compression.
    """

    role_id: str = ""  # Unique identifier for this role instance
    role_type: str = "unknown"  # Type of role: "PM", "Director", "QA", "Architect", etc.
    goal: str = ""  # Current goal/task objective
    acceptance_criteria: list[str] = field(default_factory=list)
    scope: list[str] = field(default_factory=list)  # Working scope (files, paths, domains)
    current_phase: str = "unknown"
    # Legacy aliases retained as the same semantic values.
    task_id: str = ""
    write_scope: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)  # Role-specific data

    def __post_init__(self) -> None:
        # Keep new/legacy identity fields synchronized.
        if not self.role_id and self.task_id:
            self.role_id = str(self.task_id)
        if not self.task_id and self.role_id:
            self.task_id = str(self.role_id)
        if not self.scope and self.write_scope:
            self.scope = list(self.write_scope)
        if not self.write_scope and self.scope:
            self.write_scope = list(self.scope)

    @classmethod
    def from_task(cls, task: dict[str, Any], role_type: str = "unknown") -> RoleContextIdentity:
        """Create identity from task data (backward compatible with Director)."""
        return cls(
            role_id=task.get("id", "unknown"),
            task_id=task.get("id", "unknown"),
            role_type=role_type,
            goal=task.get("goal", task.get("subject", "")),
            acceptance_criteria=task.get("acceptance_criteria", []) or task.get("soft_checks", []),
            scope=task.get("write_scope", task.get("scope_paths", [])),
            write_scope=task.get("write_scope", task.get("scope_paths", [])),
            current_phase=task.get("current_phase", "unknown"),
            metadata={
                k: v
                for k, v in task.items()
                if k
                not in {
                    "id",
                    "goal",
                    "subject",
                    "acceptance_criteria",
                    "soft_checks",
                    "write_scope",
                    "scope_paths",
                    "current_phase",
                }
            },
        )

    @classmethod
    def from_role_state(
        cls,
        role_name: str,
        goal: str,
        scope: list[str],
        current_task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RoleContextIdentity:
        """Create identity from current role state."""
        resolved_id = current_task_id or f"{role_name}_{int(time.time())}"
        return cls(
            role_id=resolved_id,
            task_id=resolved_id,
            role_type=role_name,
            goal=goal,
            acceptance_criteria=[],
            scope=scope,
            write_scope=scope,
            current_phase="active",
            metadata=metadata or {},
        )


class RoleContextCompressor:
    """Universal context compression system for all Role Agents.

    Three-layer compression: micro_compact -> auto_compact -> llm_summary
    Can be used by any role (Director, PM, QA, Architect, etc.) to manage
    conversation context and prevent token overflow.

    Usage:
        # In any RoleAgent subclass
        compressor = RoleContextCompressor(self.workspace, self.agent_name)
        compressed_messages, snapshot = compressor.compact_if_needed(
            messages, identity
        )
    """

    # Default thresholds - can be overridden per role
    MICRO_COMPACT_KEEP_RECENT = 3  # Keep last N tool results
    TOKEN_THRESHOLD = 50000  # Trigger auto_compact
    MAX_CHARS_PER_TOKEN = 4  # Rough estimate
    TRANSCRIPT_MAX_SIZE = 100000  # Max chars to send to LLM

    def __init__(
        self,
        workspace: str,
        role_name: str = "ContextCompressor",
        llm_client: "LLMClient | None" = None,
        model: str = "",
        transcript_service: TranscriptServicePort | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Initialize context compressor.

        Args:
            workspace: Workspace path
            role_name: Name of the role (for storage organization)
            llm_client: LLM client for summarization
            model: Model name for LLM
            transcript_service: Optional transcript service
            config: Optional configuration overrides
        """
        self.workspace = Path(workspace)
        self.role_name = role_name
        self.llm_client = llm_client
        self.model = model
        self._transcript_service = transcript_service

        # Apply config overrides
        config = config or {}
        self.micro_compact_keep = config.get("micro_compact_keep", self.MICRO_COMPACT_KEEP_RECENT)
        self.token_threshold = config.get("token_threshold", self.TOKEN_THRESHOLD)
        self.max_chars_per_token = config.get("max_chars_per_token", self.MAX_CHARS_PER_TOKEN)
        self.transcript_max_size = config.get("transcript_max_size", self.TRANSCRIPT_MAX_SIZE)

        from polaris.kernelone.storage import resolve_runtime_path

        self.transcript_dir = Path(resolve_runtime_path(str(self.workspace), f"runtime/transcripts/{role_name}"))
        self.index_path = Path(
            resolve_runtime_path(str(self.workspace), f"runtime/evidence/{role_name}_context_compact.index.jsonl")
        )

        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)

        self.snapshots: list[CompactSnapshot] = []

    def _record_transcript_message(self, **payload: Any) -> None:
        """Write a transcript message through the available transcript contract.

        Transitional note:
        Older implementations may still expose ``append_message`` while the
        canonical transcript service uses ``record_message``.
        """
        if not self._transcript_service:
            return
        record_message = getattr(self._transcript_service, "record_message", None)
        if callable(record_message):
            record_message(**payload)
            return
        append_message = getattr(self._transcript_service, "append_message", None)
        if callable(append_message):
            append_message(**payload)

    def create_identity_from_task(self, task_data: dict[str, Any]) -> RoleContextIdentity:
        """Build a context identity from a task payload."""
        return RoleContextIdentity.from_task(task_data, role_type=self.role_name)

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Token estimation using unified estimator."""
        estimator = ServiceLocator.get_token_estimator()
        if estimator is not None:
            return estimator.estimate_messages_tokens(messages)
        # Fallback
        return len(str(messages)) // self.max_chars_per_token

    def _compute_hash(self, content: str) -> str:
        """Compute content hash for integrity tracking."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def _append_index(self, entry: dict[str, Any]) -> None:
        """Append to evidence index."""
        with open(self.index_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def micro_compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Layer 1: Replace old tool results with placeholders.

        Silent operation, runs every turn.

        T3-7: Fix input mutation - create copies of tool results before modifying
        to avoid side effects on the caller's data.
        """
        # Find all tool_result entries
        tool_results = []
        for msg_idx, msg in enumerate(messages):
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for part_idx, part in enumerate(msg["content"]):
                    if isinstance(part, dict) and part.get("type") == "tool_result":
                        tool_results.append((msg_idx, part_idx, part))

        if len(tool_results) <= self.micro_compact_keep:
            return messages

        # Build tool_use_id -> tool_name map from assistant messages
        tool_name_map = {}
        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_name_map[block.get("id", "")] = block.get("name", "unknown")

        # Clear old results - T3-7: Create copies before modifying to avoid input mutation
        to_clear = tool_results[: -self.micro_compact_keep]
        compacted_tools = []
        for _msg_idx, _part_idx, result in to_clear:
            content = result.get("content", "")
            if isinstance(content, str) and len(content) > 100:
                tool_id = result.get("tool_use_id", "")
                tool_name = tool_name_map.get(tool_id, "unknown")
                # T3-7: Create a copy of the result dict before modifying
                result_copy = dict(result)
                status = result.get("status")
                if status == "success":
                    status_str = "ok"
                elif status:
                    status_str = str(status)
                else:
                    status_str = ""
                result_copy["content"] = (
                    f"[Previous: used {tool_name}({status_str})]" if status_str else f"[Previous: used {tool_name}]"
                )
                result_copy["_compacted"] = True
                result_copy["_original_length"] = len(content)
                # Find the original message and tool_results list to update
                for mi, m in enumerate(messages):
                    if m.get("role") == "user" and isinstance(m.get("content"), list):
                        for pi, p in enumerate(m["content"]):
                            if p is result:
                                messages[mi]["content"][pi] = result_copy
                                messages[mi]["_compacted"] = True
                                break
                compacted_tools.append(tool_name)

        # Record to transcript if available
        if compacted_tools:
            self._record_transcript_message(
                role="system",
                content=f"Micro-compacted {len(compacted_tools)} tool results: {', '.join(set(compacted_tools))}",
                metadata={"type": "context_micro_compact", "tools": list(set(compacted_tools)), "role": self.role_name},
            )

        return messages

    def truncate_compact(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], CompactSnapshot]:
        """Fallback: Truncate oldest messages."""
        original_tokens = self.estimate_tokens(messages)
        original_hash = self._compute_hash(str(messages))

        # Keep system + last N messages
        keep_count = max(10, len(messages) // 3)
        truncated = messages[:1] + messages[-keep_count:] if len(messages) > keep_count else messages

        # Add truncation notice
        truncated.insert(
            1,
            {
                "role": "user",
                "content": f"[Context truncated: {len(messages) - keep_count} older messages removed by {self.role_name}]",
            },
        )

        compressed_tokens = self.estimate_tokens(truncated)

        snapshot = CompactSnapshot(
            timestamp=time.time(),
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            original_hash=original_hash,
            summary_hash=self._compute_hash(str(truncated)),
            method="truncate",
            role_name=self.role_name,
        )

        return truncated, snapshot

    def llm_compact(
        self,
        messages: list[dict[str, Any]],
        identity: RoleContextIdentity,
        focus: str = "",
    ) -> tuple[list[dict[str, Any]], CompactSnapshot]:
        """Layer 2/3: LLM-based continuity summary.

        Saves full transcript, asks LLM to summarize, replaces messages.
        """
        original_tokens = self.estimate_tokens(messages)
        original_hash = self._compute_hash(str(messages))

        # Save full transcript
        transcript_path = self.transcript_dir / f"transcript_{int(time.time())}.jsonl"
        with open(transcript_path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, default=str) + "\n")

        # Prepare conversation for LLM summary
        conversation_text = json.dumps(messages, default=str)[: self.transcript_max_size]

        # Build summary prompt with identity context
        prompt = self._build_summary_prompt(identity, focus, conversation_text)

        summary_method = "llm"
        try:
            if not self.llm_client:
                raise RuntimeError("llm_client_unavailable")
            response = self.llm_client.messages.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.3,
            )
            summary = str(response.content[0].text or "").strip()
            if not summary:
                raise RuntimeError("empty_llm_summary")
        except (RuntimeError, ValueError) as e:
            summary_method = "deterministic"
            summary = build_continuity_summary_text(
                messages,
                identity,
                focus=focus or f"Compression fallback after {type(e).__name__}",
            )
            if not summary:
                summary = "[Deterministic continuity summary unavailable. Proceed with the most recent context only.]"

        # Build compressed message list
        compressed = [
            {"role": "user", "content": self._build_reinjection_prompt(identity, summary, transcript_path)},
            {
                "role": "assistant",
                "content": "Understood. I have the context from the summary and will continue with the task.",
            },
        ]

        compressed_tokens = self.estimate_tokens(compressed)

        snapshot = CompactSnapshot(
            timestamp=time.time(),
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            original_hash=original_hash,
            summary_hash=self._compute_hash(summary),
            method=summary_method,
            transcript_path=str(transcript_path),
            role_name=self.role_name,
        )

        # Log to evidence
        self._append_index(
            {
                "type": "context_compact",
                "timestamp": snapshot.timestamp,
                "method": summary_method,
                "original_tokens": original_tokens,
                "compressed_tokens": compressed_tokens,
                "original_hash": original_hash,
                "summary_hash": snapshot.summary_hash,
                "transcript_path": str(transcript_path),
                "role_id": identity.role_id,
                "role_type": identity.role_type,
            }
        )

        # Record to transcript service if available
        if self._transcript_service:
            reduction = (1 - compressed_tokens / original_tokens) * 100 if original_tokens > 0 else 0
            self._record_transcript_message(
                role="system",
                content=f"Context compressed by {self.role_name}: {original_tokens} -> {compressed_tokens} tokens ({reduction:.1f}% reduction)",
                metadata={
                    "type": "context_compact",
                    "role": self.role_name,
                    "role_id": identity.role_id,
                    "method": summary_method,
                    "original_tokens": original_tokens,
                    "compressed_tokens": compressed_tokens,
                    "reduction_percent": round(reduction, 1),
                    "transcript_path": str(transcript_path),
                },
            )
            # Also record the summary as an artifact
            self._record_transcript_message(
                role="assistant",
                content=summary[:500] + "..." if len(summary) > 500 else summary,
                metadata={
                    "type": "context_summary",
                    "role": self.role_name,
                    "role_id": identity.role_id,
                    "method": summary_method,
                },
            )

        return compressed, snapshot

    def _build_summary_prompt(self, identity: RoleContextIdentity, focus: str, conversation: str) -> str:
        """Build the LLM summary prompt with structured output."""
        return f"""Summarize this conversation for continuity preservation.

ROLE CONTEXT:
- Role: {identity.role_type}
- Role ID: {identity.role_id}
- Goal: {identity.goal}
- Current Phase: {identity.current_phase}
- Scope: {", ".join(identity.scope)}
{f"- Focus: {focus}" if focus else ""}

ACCEPTANCE CRITERIA:
{chr(10).join(f"- {c}" for c in identity.acceptance_criteria) if identity.acceptance_criteria else "- None defined"}

Provide a structured summary with these sections:

## Accomplished
What has been completed so far?

## Current State
What is the current situation? Files modified, tests run, pending decisions.

## Open Risks/Blockers
What might cause problems? What is uncertain?

## Next Steps
What should happen next? Prioritized list.

## Key References
Important file paths, command outputs, or decisions to remember.

CONVERSATION TO SUMMARIZE:
{conversation}
"""

    def _build_reinjection_prompt(self, identity: RoleContextIdentity, summary: str, transcript_path: Path) -> str:
        """Build the re-injection prompt with identity anchor."""
        return f"""[Context compressed for continuity]

Full transcript saved to: {transcript_path}

=== ROLE IDENTITY (PRESERVE THESE) ===
Role: {identity.role_type}
Role ID: {identity.role_id}
Goal: {identity.goal}
Acceptance Criteria:
{chr(10).join(f"- {c}" for c in identity.acceptance_criteria) if identity.acceptance_criteria else "- None defined"}

Scope: {", ".join(identity.scope)}
Current Phase: {identity.current_phase}

=== CONTINUITY SUMMARY ===
{summary}

Continue with the task based on this summary. Do not deviate from the goal or acceptance criteria."""

    @staticmethod
    def _find_tool_pair_indices(messages: list[dict[str, Any]]) -> set[int]:
        """Return indices that form tool_use/tool_result pairs.

        These indices cannot be individually removed — they must be
        dropped together to avoid breaking the LLM API contract
        (every tool_use must have a matching tool_result and vice versa).
        """
        protected: set[int] = set()
        for i, msg in enumerate(messages):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_id = block.get("id")
                if not tool_id:
                    continue
                # Find matching tool_result in the next few user messages
                for j in range(i + 1, min(i + 5, len(messages))):
                    if messages[j].get("role") != "user":
                        continue
                    parts = messages[j].get("content", [])
                    if not isinstance(parts, list):
                        continue
                    for part in parts:
                        if isinstance(part, dict) and part.get("tool_use_id") == tool_id:
                            protected.add(i)
                            protected.add(j)
        return protected

    def auto_compact(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int,
    ) -> list[dict[str, Any]]:
        """Auto-compact: deterministic truncation within token budget.

        This is the MIDDLE layer between micro_compact and llm_compact.
        Called when tokens slightly exceed threshold but LLM summarization
        is not yet warranted.

        Strategy: truncate oldest non-essential messages from the middle,
        keeping newest and oldest messages to preserve context at both ends.
        Tool-use/tool-result pairs are always removed together to avoid
        breaking the LLM API contract.

        Args:
            messages: Messages to compact
            target_tokens: Target token count to reach

        Returns:
            Compacted messages
        """
        current_tokens = self.estimate_tokens(messages)
        if current_tokens <= target_tokens:
            return messages

        # Separate system and non-system messages
        system = [m for m in messages if str(m.get("role", "")).lower() == "system"]
        non_system = [m for m in messages if str(m.get("role", "")).lower() != "system"]

        # Identify tool_use/tool_result pairs that must not be split
        protected = self._find_tool_pair_indices(non_system)

        # Build a list of removable indices (not at edges, not in protected pairs)
        while self.estimate_tokens(system + non_system) > target_tokens and len(non_system) > 4:
            removable = [idx for idx in range(1, len(non_system) - 1) if idx not in protected]
            if not removable:
                # All remaining middle messages are protected pairs — stop
                break
            # Remove the first removable message; if it's part of a newly
            # exposed pair boundary, the pair will be caught next iteration
            remove_idx = removable[0]
            non_system.pop(remove_idx)
            # Rebuild protected set after removal (indices shifted)
            protected = self._find_tool_pair_indices(non_system)

        return system + non_system

    def compact_if_needed(
        self,
        messages: list[dict[str, Any]],
        identity: RoleContextIdentity,
        force_compact: bool = False,
        focus: str = "",
        target_tokens: int | None = None,
    ) -> tuple[list[dict[str, Any]], CompactSnapshot | None]:
        """Main entry: Apply compression if over threshold or forced.

        Three-layer compression strategy:
        1. micro_compact: Replace old tool results with placeholders (every turn)
        2. auto_compact: Deterministic truncation within token budget (first resort)
        3. llm_compact: LLM summarization (fallback for larger overflows)

        Args:
            messages: Messages to potentially compact
            identity: Role context identity for LLM summarization
            force_compact: Force compaction regardless of token count
            focus: Focus topic for LLM summarization
            target_tokens: Override for token threshold (optional)
        """
        # Always apply micro compact first (Layer 1)
        messages = self.micro_compact(messages)

        # Check if compaction needed
        tokens = self.estimate_tokens(messages)
        effective_threshold = target_tokens or self.token_threshold

        if not force_compact and tokens < effective_threshold:
            return messages, None

        # Layer 2: Try auto_compact first (deterministic, no LLM needed)
        # Only try if within 20% of threshold - larger overflows need LLM summarization
        if tokens < effective_threshold * 1.2:
            auto_result = self.auto_compact(messages, effective_threshold)
            if self.estimate_tokens(auto_result) <= effective_threshold:
                return auto_result, None

        # Layer 3: Fall back to LLM summarization for larger overflows
        compressed, snapshot = self.llm_compact(messages, identity, focus)
        self.snapshots.append(snapshot)

        return compressed, snapshot


__all__ = [
    "CompactSnapshot",
    "RoleContextCompressor",
    "RoleContextIdentity",
    "build_continuity_summary_text",
]
