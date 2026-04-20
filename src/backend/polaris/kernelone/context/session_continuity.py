"""KernelOne session continuity engine.

This module owns reusable session/history/context continuity projection logic.
It intentionally does not own role session storage or role orchestration.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from polaris.kernelone.utils.time_utils import utc_now_iso as _utc_now_iso

from .compaction import RoleContextIdentity, build_continuity_summary_text
from .context_os import (
    ContextOSProjection,
    ContextOSSnapshot,
    StateFirstContextOS,
    validate_context_os_persisted_projection,
)
from .context_os.policies import (
    CollectionLimitsPolicy,
    ContextWindowPolicy,
    StateFirstContextOSPolicy,
)
from .context_os.rehydration import rehydrate_persisted_context_os_payload
from .control_plane_noise import is_control_plane_noise, is_signal_role
from .runtime_feature_flags import resolve_context_os_enabled

_DEFAULT_HISTORY_WINDOW_MESSAGES = 8
_MAX_HISTORY_WINDOW_MESSAGES = 24
_MAX_CONTINUITY_SOURCE_MESSAGES = 40
_DEFAULT_SUMMARY_FOCUS = "Preserve active engineering context and omit stale social or identity chatter."
_CONTEXT_OS_KEY = "state_first_context_os"
_RESERVED_CONTEXT_KEYS = frozenset(
    {
        "role",
        "host_kind",
        "governance_scope",
        "capability_profile",
        "session_id",
        "history",
        "workspace",
    }
)
_HIGH_SIGNAL_TERMS = (
    # Core engineering intent terms only.
    # Removed infrastructure/continuity noise (tool, stream, output, kernel,
    # workspace, project, code, test, session, history, context, etc.)
    # to prevent system messages and tool receipts from being promoted to
    # stable_facts / open_loops.
    "error",
    "failure",
    "failed",
    "fix",
    "bug",
    "refactor",
    "patch",
    "diff",
    "verification",
    "governance",
    "错误",
    "失败",
    "修复",
    "重构",
    "补丁",
    "验证",
    "治理",
)
_LOW_SIGNAL_PATTERNS = (
    r"^(hi|hello|hey|你好|您好|嗨|thanks|thank you|谢谢|ok|好的|收到|稍等|bye|再见)\b",
    r"(换个名字|改名字|改名|叫我|叫你|你是什么模型|what model are you|who are you)",
)
_OPEN_LOOP_PATTERNS = (
    re.compile(r"(继续|开始|开工|实现|重构|修复|补|验证|测试|运行|排查|处理|收口|抽离|落地|总结|写计划|写蓝图)"),
    re.compile(
        r"\b(continue|start|implement|refactor|fix|add|update|verify|test|run|summari[sz]e|ship)\b",
        re.IGNORECASE,
    ),
)
_COMMITMENT_PATTERNS = (
    re.compile(r"(我会|我将|会继续|将继续|下一步|接下来)"),
    re.compile(r"\b(i will|i'll|next step|going to)\b", re.IGNORECASE),
)
_CODE_PATH_RE = re.compile(
    r"([A-Za-z]:\\|[/\\]|`[^`]+\.(py|md|ya?ml|json|toml|ts|tsx|js|jsx|sql|sh|ps1)`|\b[\w.-]+\.(py|md|ya?ml|json|toml|ts|tsx|js|jsx|sql|sh|ps1)\b)",
    re.IGNORECASE,
)
_PROTOCOL_TAG_RE = re.compile(
    r"</?(assistant|system|thinking|antthinking|tool_result|tool_call|analysis|final|user)(?:\s[^>]*)?>",
    re.IGNORECASE,
)
_GENERIC_TAG_RE = re.compile(r"</?[a-z][a-z0-9_-]{1,31}(?:\s[^>]*)?>", re.IGNORECASE)
_LONG_REPEAT_RE = re.compile(r"(.)\1{79,}")


def _copy_mapping(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _safe_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _message_sequence(message: Mapping[str, Any], fallback: int) -> int:
    return _safe_int(message.get("sequence"), default=fallback)


def _normalize_text(value: Any) -> str:
    raw = str(value or "")
    sanitized = _PROTOCOL_TAG_RE.sub(" ", raw)
    sanitized = _GENERIC_TAG_RE.sub(" ", sanitized)
    text = " ".join(sanitized.replace("\r", " ").replace("\n", " ").split())
    return text.strip()


def _trim_text(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max(1, int(max_chars * 0.72))
    tail = max(1, max_chars - head - 14)
    return f"{text[:head].rstrip()} ...[snip]... {text[-tail:].lstrip()}"


def _is_low_signal(text: str) -> bool:
    lowered = text.lower()
    if is_control_plane_noise(lowered):
        return True
    if _LONG_REPEAT_RE.search(lowered):
        return True
    if len(lowered) >= 200:
        unique_ratio = len(set(lowered)) / max(1, len(lowered))
        if unique_ratio < 0.08:
            return True
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in _LOW_SIGNAL_PATTERNS)


def _sanitize_pack_summary(summary: str) -> str:
    lines = [_normalize_text(line) for line in str(summary or "").splitlines()]
    lines = [line for line in lines if line and not is_control_plane_noise(line)]
    return "\n".join(line for line in lines if line.strip()).strip()


def _sanitize_pack_items(items: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        item for item in (_normalize_text(value) for value in items) if item and not is_control_plane_noise(item)
    )


def _sanitize_prior_summary(summary: str) -> str:
    text = _normalize_text(summary)
    if not text:
        return ""
    # Prevent recursive "summary of summary" growth across turns.
    while True:
        cleaned = re.sub(
            r"(?i)\b(previous continuity summary|prior continuity signal)\s*:\s*",
            "",
            text,
        )
        if cleaned == text:
            break
        text = cleaned
    text = re.sub(r"(?i)\bcontext continuity summary\b", "", text)
    return _trim_text(" ".join(text.split()), max_chars=280)


def _signal_score(role: str, text: str) -> int:
    """Calculate signal score for message filtering.

    T3-4: Unified threshold from 40 to 48 (conservative threshold) to match
    compaction.py._continuity_signal_score for consistent behavior.
    """
    lowered = text.lower()
    score = 0
    if role == "user":
        score += 1
    if len(text) >= 48:  # T3-4: Unified to 48 (was 40)
        score += 1
    if any(term in lowered for term in _HIGH_SIGNAL_TERMS):
        score += 3
    if _CODE_PATH_RE.search(text):
        score += 2
    if _is_low_signal(text):
        score -= 4
    return score


def _iter_normalized_messages(
    messages: Sequence[Mapping[str, Any]] | Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(messages or ()):
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = _normalize_text(item.get("content") or item.get("message") or "")
        if not role or not content:
            continue
        entry: dict[str, Any] = {
            "role": role,
            "content": content,
            "sequence": _message_sequence(item, index),
        }
        # SSOT: Preserve full event metadata from turn_events for ContextOS event sourcing.
        # These fields are needed by _merge_transcript to rebuild TranscriptEvent objects
        # with proper event_id, kind, route, dialog_act, source_turns, artifact_id, created_at.
        if "event_id" in item:
            entry["event_id"] = item["event_id"]
        if "metadata" in item and isinstance(item["metadata"], dict):
            entry["metadata"] = dict(item["metadata"])
        if "kind" in item:
            entry["kind"] = item["kind"]
        if "route" in item:
            entry["route"] = item["route"]
        if "source_turns" in item:
            entry["source_turns"] = item["source_turns"]
        if "artifact_id" in item:
            entry["artifact_id"] = item["artifact_id"]
        if "created_at" in item:
            entry["created_at"] = item["created_at"]
        normalized.append(entry)
    return normalized


def history_pairs_to_messages(
    history: Sequence[tuple[str, str]] | tuple[tuple[str, str], ...],
    *,
    start_sequence: int = 0,
) -> tuple[dict[str, Any], ...]:
    messages: list[dict[str, Any]] = []
    sequence = int(start_sequence)
    for role, content in history or ():
        role_token = str(role or "").strip().lower()
        content_token = _normalize_text(content)
        if not role_token or not content_token:
            continue
        messages.append(
            {
                "sequence": sequence,
                "role": role_token,
                "content": content_token,
            }
        )
        sequence += 1
    return tuple(messages)


def messages_to_history_pairs(
    messages: Sequence[Mapping[str, Any]] | Sequence[dict[str, Any]],
) -> tuple[tuple[str, str], ...]:
    history: list[tuple[str, str]] = []
    for item in messages or ():
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "").strip()
        content = _normalize_text(item.get("content") or item.get("message") or "")
        if role and content:
            history.append((role, content))
    return tuple(history)


def _dedupe_preserve_order(items: Sequence[str], *, max_items: int) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        token = _normalize_text(item)
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(token)
    if max_items <= 0:
        return ()
    return tuple(result[-max_items:])


@dataclass(frozen=True, slots=True)
class SessionContinuityPack:
    version: int = 2
    mode: str = "session_continuity_engine_v1"
    summary: str = ""
    stable_facts: tuple[str, ...] = ()
    open_loops: tuple[str, ...] = ()
    omitted_low_signal_count: int = 0
    generated_at: str = ""
    compacted_through_seq: int = -1
    source_message_count: int = 0
    recent_window_messages: int = 0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> SessionContinuityPack | None:
        if not isinstance(payload, Mapping):
            return None
        return cls(
            version=_safe_int(payload.get("version"), default=2),
            mode=str(payload.get("mode") or "session_continuity_engine_v1").strip(),
            summary=_sanitize_pack_summary(str(payload.get("summary") or "").strip()),
            stable_facts=_sanitize_pack_items(payload.get("stable_facts", [])),
            open_loops=_sanitize_pack_items(payload.get("open_loops", [])),
            omitted_low_signal_count=max(0, _safe_int(payload.get("omitted_low_signal_count"), default=0)),
            generated_at=str(payload.get("generated_at") or "").strip(),
            compacted_through_seq=_safe_int(payload.get("compacted_through_seq"), default=-1),
            source_message_count=max(0, _safe_int(payload.get("source_message_count"), default=0)),
            recent_window_messages=max(0, _safe_int(payload.get("recent_window_messages"), default=0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": int(self.version),
            "mode": self.mode,
            "summary": self.summary,
            "stable_facts": list(self.stable_facts),
            "open_loops": list(self.open_loops),
            "omitted_low_signal_count": int(self.omitted_low_signal_count),
            "generated_at": self.generated_at,
            "compacted_through_seq": int(self.compacted_through_seq),
            "source_message_count": int(self.source_message_count),
            "recent_window_messages": int(self.recent_window_messages),
        }


@dataclass(frozen=True, slots=True)
class SessionContinuityPolicy:
    default_history_window_messages: int = _DEFAULT_HISTORY_WINDOW_MESSAGES
    max_history_window_messages: int = _MAX_HISTORY_WINDOW_MESSAGES
    max_continuity_source_messages: int = _MAX_CONTINUITY_SOURCE_MESSAGES
    max_summary_items: int = 8
    max_summary_chars: int = 1500
    max_stable_facts: int = 6
    max_open_loops: int = 6
    reserved_context_keys: frozenset[str] = field(default_factory=lambda: frozenset(_RESERVED_CONTEXT_KEYS))
    summary_focus: str = _DEFAULT_SUMMARY_FOCUS


@dataclass(frozen=True, slots=True)
class SessionContinuityRequest:
    session_id: str
    role: str
    workspace: str
    session_title: str = ""
    messages: tuple[dict[str, Any], ...] = ()
    # SSOT: Full event metadata for ContextOS event sourcing.
    # When provided, these events preserve kind, route, dialog_act, source_turns,
    # artifact_id, and created_at. Falls back to messages tuple for backward compat.
    turn_events: tuple[dict[str, Any], ...] = ()
    session_context_config: Mapping[str, Any] | None = None
    incoming_context: Mapping[str, Any] | None = None
    history_limit: int | None = None
    focus: str = ""


@dataclass(frozen=True, slots=True)
class SessionContinuityProjection:
    recent_messages: tuple[dict[str, Any], ...]
    prompt_context: dict[str, Any]
    persisted_context_config: dict[str, Any]
    continuity_pack: SessionContinuityPack | None = None
    context_os_projection: ContextOSProjection | None = None
    changed: bool = False


class SessionContinuityEngine:
    """Canonical continuity projector for resumed role sessions."""

    def __init__(self, policy: SessionContinuityPolicy | None = None) -> None:
        self.policy = policy or SessionContinuityPolicy()
        self._context_os_cache: dict[str, StateFirstContextOS] = {}

    def resolve_history_window(self, history_limit: int | None) -> int:
        if history_limit is None or history_limit < 0:
            return self.policy.default_history_window_messages
        return max(1, min(int(history_limit), self.policy.max_history_window_messages))

    def build_prompt_context(
        self,
        *,
        session_context_config: Mapping[str, Any] | None,
        incoming_context: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        prompt_context = _copy_mapping(session_context_config)
        prompt_context.update(_copy_mapping(incoming_context))
        for key in self.policy.reserved_context_keys:
            prompt_context.pop(key, None)
        prompt_context.pop(_CONTEXT_OS_KEY, None)
        return prompt_context

    async def build_pack(
        self,
        messages: Sequence[Mapping[str, Any]] | Sequence[dict[str, Any]],
        *,
        identity: RoleContextIdentity | None = None,
        existing_pack: SessionContinuityPack | Mapping[str, Any] | None = None,
        focus: str = "",
        recent_window_messages: int = 0,
        context_os_enabled: bool = True,
        context_os_domain: str | None = None,
    ) -> SessionContinuityPack | None:
        normalized_messages = _iter_normalized_messages(messages)
        if not normalized_messages:
            return None

        existing = (
            existing_pack
            if isinstance(existing_pack, SessionContinuityPack)
            else SessionContinuityPack.from_mapping(existing_pack)
        )
        context_os_projection = None
        if context_os_enabled:
            context_os_projection = await self._build_context_os(domain=context_os_domain).project(
                messages=[dict(item) for item in normalized_messages],
                recent_window_messages=max(
                    1,
                    int(recent_window_messages or self.policy.default_history_window_messages),
                ),
                focus=focus,
            )
        compacted_through_seq = max(item["sequence"] for item in normalized_messages)
        summary_source: list[dict[str, Any]] = []
        if existing is not None and existing.summary:
            prior_summary = _sanitize_prior_summary(existing.summary)
            if prior_summary:
                summary_source.append(
                    {
                        "role": "system",
                        "content": f"Prior continuity signal: {prior_summary}",
                        "sequence": existing.compacted_through_seq,
                    }
                )
        summary_source.extend(normalized_messages)
        summary_source = summary_source[-self.policy.max_continuity_source_messages :]

        summary_text = build_continuity_summary_text(
            summary_source,
            identity,
            focus=_normalize_text(focus) or self.policy.summary_focus,
            max_items=self.policy.max_summary_items,
            max_chars=self.policy.max_summary_chars,
        )
        if not summary_text:
            return None

        existing_facts = existing.stable_facts if existing is not None else ()
        existing_loops = existing.open_loops if existing is not None else ()
        state_first_facts = self._extract_state_first_stable_facts(context_os_projection)
        lexical_facts = self._extract_stable_facts(normalized_messages)
        derived_facts = _dedupe_preserve_order(
            [*state_first_facts, *lexical_facts],
            max_items=self.policy.max_stable_facts,
        )
        state_first_loops = self._extract_state_first_open_loops(context_os_projection)
        lexical_loops = self._extract_open_loops(normalized_messages)
        derived_loops = _dedupe_preserve_order(
            [*state_first_loops, *lexical_loops],
            max_items=self.policy.max_open_loops,
        )
        stable_facts = self._merge_prior_items(
            existing_facts,
            derived_facts,
            max_items=self.policy.max_stable_facts,
        )
        open_loops = self._merge_prior_items(
            existing_loops,
            derived_loops,
            max_items=self.policy.max_open_loops,
        )
        omitted_low_signal_count = sum(
            1 for item in normalized_messages if _is_low_signal(str(item.get("content") or ""))
        )
        return SessionContinuityPack(
            summary=summary_text,
            stable_facts=stable_facts,
            open_loops=open_loops,
            omitted_low_signal_count=omitted_low_signal_count,
            generated_at=_utc_now_iso(),
            compacted_through_seq=compacted_through_seq,
            source_message_count=len(normalized_messages),
            recent_window_messages=max(0, int(recent_window_messages)),
        )

    async def project(self, request: SessionContinuityRequest) -> SessionContinuityProjection:
        # SSOT: Prefer turn_events over messages for ContextOS event sourcing.
        # turn_events preserve full metadata (kind, route, dialog_act, source_turns, etc.)
        if request.turn_events:
            messages = _iter_normalized_messages(request.turn_events)
        else:
            messages = _iter_normalized_messages(request.messages)
        recent_window = self.resolve_history_window(request.history_limit)
        recent_messages = tuple(dict(item) for item in messages[-recent_window:] if recent_window)
        persisted_context_config = _copy_mapping(request.session_context_config)
        prompt_context = self.build_prompt_context(
            session_context_config=request.session_context_config,
            incoming_context=request.incoming_context,
        )
        changed = False
        persisted_turn_events = (
            persisted_context_config.get("session_turn_events")
            if isinstance(persisted_context_config.get("session_turn_events"), Sequence)
            else None
        )
        existing_context_os_payload = rehydrate_persisted_context_os_payload(
            persisted_context_config.get(_CONTEXT_OS_KEY)
            if isinstance(persisted_context_config.get(_CONTEXT_OS_KEY), Mapping)
            else None,
            session_turn_events=[dict(item) for item in (persisted_turn_events or ()) if isinstance(item, Mapping)],
        )
        context_os_enabled = resolve_context_os_enabled(
            incoming_context=request.incoming_context,
            session_context_config=request.session_context_config,
            default=True,
        )
        existing_context_os = ContextOSSnapshot.from_mapping(existing_context_os_payload)
        context_os_projection = None
        if context_os_enabled and messages:
            context_os_projection = await self._build_context_os(
                domain=self._resolve_context_os_domain(
                    request=request,
                    existing_snapshot=existing_context_os,
                )
            ).project(
                messages=[dict(item) for item in messages],
                existing_snapshot=existing_context_os,
                recent_window_messages=recent_window,
                focus=_normalize_text(request.focus) or _normalize_text(request.session_title),
            )
            prompt_context[_CONTEXT_OS_KEY] = self._build_context_os_prompt_payload(context_os_projection)
            context_os_persisted_payload = self._build_context_os_persisted_payload(context_os_projection)
            if context_os_persisted_payload != existing_context_os_payload:
                persisted_context_config[_CONTEXT_OS_KEY] = context_os_persisted_payload
                changed = True
        elif existing_context_os_payload is not None:
            persisted_context_config.pop(_CONTEXT_OS_KEY, None)
            changed = True
        if not context_os_enabled:
            prompt_context.pop(_CONTEXT_OS_KEY, None)

        existing_pack = SessionContinuityPack.from_mapping(
            persisted_context_config.get("session_continuity")
            if isinstance(persisted_context_config.get("session_continuity"), Mapping)
            else None
        )
        older_messages = list(messages[:-recent_window]) if recent_window and len(messages) > recent_window else []
        if not older_messages:
            # Preserve existing continuity pack if it has a valid summary
            if existing_pack is not None and existing_pack.summary:
                persisted_context_config["session_continuity"] = existing_pack.to_dict()
                prompt_context["session_continuity"] = existing_pack.to_dict()
                return SessionContinuityProjection(
                    recent_messages=recent_messages,
                    prompt_context=prompt_context,
                    persisted_context_config=persisted_context_config,
                    continuity_pack=existing_pack,
                    context_os_projection=context_os_projection,
                    changed=True,
                )
            # Try to build continuity from recent_messages if no valid existing pack
            if recent_messages:
                incremental = list(recent_messages)[-self.policy.max_continuity_source_messages :]
                identity = RoleContextIdentity.from_role_state(
                    role_name=request.role,
                    goal=_normalize_text(request.session_title) or "ongoing_role_session",
                    scope=[request.workspace],
                    current_task_id=request.session_id,
                    metadata={
                        "session_id": request.session_id,
                        "source": "kernelone.session_continuity",
                    },
                )
                pack = await self.build_pack(
                    incremental,
                    identity=identity,
                    existing_pack=existing_pack,
                    focus=request.focus,
                    recent_window_messages=recent_window,
                    context_os_enabled=context_os_enabled,
                )
                if pack is not None and pack.summary:
                    return SessionContinuityProjection(
                        recent_messages=recent_messages,
                        prompt_context=prompt_context,
                        persisted_context_config=persisted_context_config,
                        continuity_pack=pack,
                        context_os_projection=context_os_projection,
                        changed=True,
                    )
            # Cannot build continuity: return with no pack
            return SessionContinuityProjection(
                recent_messages=recent_messages,
                prompt_context=prompt_context,
                persisted_context_config=persisted_context_config,
                continuity_pack=None,
                context_os_projection=context_os_projection,
                changed=changed,
            )

        older_max_seq = max((item["sequence"] for item in older_messages), default=0)
        should_rebuild = (
            existing_pack is None or not existing_pack.summary or older_max_seq > existing_pack.compacted_through_seq
        )
        pack = existing_pack
        if should_rebuild:
            incremental_messages = (
                [
                    item
                    for item in older_messages
                    if item["sequence"] > (existing_pack.compacted_through_seq if existing_pack else -1)
                ]
                if existing_pack is not None
                else older_messages
            )
            if not incremental_messages:
                incremental_messages = older_messages[-self.policy.max_continuity_source_messages :]
            identity = RoleContextIdentity.from_role_state(
                role_name=request.role,
                goal=_normalize_text(request.session_title) or "ongoing_role_session",
                scope=[request.workspace],
                current_task_id=request.session_id,
                metadata={
                    "session_id": request.session_id,
                    "source": "kernelone.session_continuity",
                },
            )
            pack = await self.build_pack(
                incremental_messages,
                identity=identity,
                existing_pack=existing_pack,
                focus=request.focus,
                recent_window_messages=recent_window,
                context_os_enabled=context_os_enabled,
            )
            if pack is not None:
                pack = SessionContinuityPack(
                    version=pack.version,
                    mode=pack.mode,
                    summary=pack.summary,
                    stable_facts=pack.stable_facts,
                    open_loops=pack.open_loops,
                    omitted_low_signal_count=pack.omitted_low_signal_count,
                    generated_at=pack.generated_at,
                    compacted_through_seq=older_max_seq,
                    source_message_count=len(older_messages),
                    recent_window_messages=recent_window,
                )

        if pack is not None and pack.summary:
            prompt_context["session_continuity"] = pack.to_dict()
            if pack != existing_pack:
                persisted_context_config["session_continuity"] = pack.to_dict()
                changed = True
        elif existing_pack is not None:
            persisted_context_config.pop("session_continuity", None)
            prompt_context.pop("session_continuity", None)
            changed = True

        return SessionContinuityProjection(
            recent_messages=recent_messages,
            prompt_context=prompt_context,
            persisted_context_config=persisted_context_config,
            continuity_pack=pack,
            context_os_projection=context_os_projection,
            changed=changed,
        )

    def _build_context_os(self, *, domain: str | None = None) -> StateFirstContextOS:
        key = domain or "generic"
        if key not in self._context_os_cache:
            self._context_os_cache[key] = StateFirstContextOS(
                policy=StateFirstContextOSPolicy(
                    context_window=ContextWindowPolicy(
                        default_history_window_messages=self.policy.default_history_window_messages,
                        max_active_window_messages=self.policy.max_history_window_messages,
                    ),
                    collection_limits=CollectionLimitsPolicy(
                        max_open_loops=self.policy.max_open_loops,
                        max_stable_facts=self.policy.max_stable_facts,
                    ),
                ),
                domain=key,
            )
        return self._context_os_cache[key]

    def _resolve_context_os_domain(
        self,
        *,
        request: SessionContinuityRequest,
        existing_snapshot: ContextOSSnapshot | None,
    ) -> str:
        for payload in (request.incoming_context, request.session_context_config):
            if not isinstance(payload, Mapping):
                continue
            for key in ("context_os_domain", "context_domain", "domain"):
                token = str(payload.get(key) or "").strip().lower()
                if token:
                    return token
        if existing_snapshot is not None and existing_snapshot.adapter_id:
            return existing_snapshot.adapter_id
        return "generic"

    def _build_context_os_prompt_payload(self, projection: ContextOSProjection) -> dict[str, Any]:
        snapshot = projection.snapshot
        run_card = projection.run_card
        # Extract attention runtime fields from run_card
        pending_followup = snapshot.pending_followup

        result: dict[str, Any] = {
            "version": int(snapshot.version),
            "mode": snapshot.mode,
            "adapter_id": snapshot.adapter_id,
            "head_anchor": projection.head_anchor,
            "tail_anchor": projection.tail_anchor,
            "run_card": projection.run_card.to_dict() if projection.run_card is not None else None,
            "context_slice_plan": (
                projection.context_slice_plan.to_dict() if projection.context_slice_plan is not None else None
            ),
            "task_state": snapshot.working_state.task_state.to_dict(),
            "decision_log": [item.to_dict() for item in snapshot.working_state.decision_log],
            "active_entities": [item.value for item in snapshot.working_state.active_entities],
            "active_artifacts": list(snapshot.working_state.active_artifacts),
            "artifact_stubs": [item.to_stub() for item in projection.artifact_stubs],
            "episode_cards": [
                {
                    "episode_id": item.episode_id,
                    "intent": item.intent,
                    "outcome": item.outcome,
                    "digest_64": item.digest_64,
                    "artifact_refs": list(item.artifact_refs),
                    "reopen_conditions": list(item.reopen_conditions),
                }
                for item in projection.episode_cards
            ],
            # === Attention Runtime Fields ===
            "latest_user_intent": run_card.latest_user_intent if run_card else "",
            "pending_followup": pending_followup.to_dict() if pending_followup else None,
            "last_turn_outcome": run_card.last_turn_outcome if run_card else "",
        }

        # Include budget_plan for CLI context display (model_context_window, current_input_tokens)
        # This flows through to event metadata for CLI without requiring separate config reading
        if snapshot.budget_plan is not None:
            result["budget_plan"] = {
                "model_context_window": snapshot.budget_plan.model_context_window,
                "current_input_tokens": snapshot.budget_plan.current_input_tokens,
            }

        return result

    def _build_context_os_persisted_payload(self, projection: ContextOSProjection) -> dict[str, Any]:
        snapshot = projection.snapshot
        # SSOT: Persist the full transcript_log so downstream consumers
        # (ContextOSSnapshot.from_mapping, ToolLoopController, ContextGateway)
        # can read it directly without depending on session_turn_events.
        # The previous BLOAT FIX using transcript_log_index caused a format drift
        # where consumers expecting transcript_log saw an empty transcript.
        payload = {
            "version": int(snapshot.version),
            "mode": snapshot.mode,
            "adapter_id": snapshot.adapter_id,
            "transcript_log": [item.to_dict() for item in snapshot.transcript_log],
            "working_state": snapshot.working_state.to_dict(),
            "artifact_store": [
                {
                    "artifact_id": item.artifact_id,
                    "artifact_type": item.artifact_type,
                    "mime_type": item.mime_type,
                    "token_count": int(item.token_count),
                    "char_count": int(item.char_count),
                    "peek": item.peek,
                    "keys": list(item.keys),
                    "source_event_ids": list(item.source_event_ids),
                    "restore_tool": item.restore_tool,
                    "metadata": dict(item.metadata),
                }
                for item in snapshot.artifact_store
            ],
            "episode_store": [item.to_dict() for item in snapshot.episode_store],
            "budget_plan": snapshot.budget_plan.to_dict() if snapshot.budget_plan is not None else None,
            "updated_at": snapshot.updated_at,
            # === Attention Runtime: Persist pending_followup state ===
            "pending_followup": snapshot.pending_followup.to_dict() if snapshot.pending_followup else None,
        }
        # Persisted Context OS payload is a derived projection, never raw turn truth.
        validated = validate_context_os_persisted_projection(payload)
        return dict(validated or {})

    def _extract_state_first_stable_facts(
        self,
        projection: ContextOSProjection | None,
    ) -> tuple[str, ...]:
        if projection is None:
            return ()
        values: list[str] = []
        goal = projection.snapshot.working_state.task_state.current_goal
        if goal is not None:
            values.append(goal.value)
        values.extend(item.value for item in projection.snapshot.working_state.user_profile.persistent_facts)
        values.extend(item.summary for item in projection.snapshot.working_state.decision_log)
        values.extend(item.value for item in projection.snapshot.working_state.active_entities)
        values.extend(item.value for item in projection.snapshot.working_state.task_state.deliverables)
        return _dedupe_preserve_order(values, max_items=self.policy.max_stable_facts)

    def _extract_state_first_open_loops(
        self,
        projection: ContextOSProjection | None,
    ) -> tuple[str, ...]:
        if projection is None:
            return ()
        values = [item.value for item in projection.snapshot.working_state.task_state.open_loops]
        values.extend(item.value for item in projection.snapshot.working_state.task_state.blocked_on)
        values.extend(item.value for item in projection.snapshot.working_state.task_state.deliverables)
        return _dedupe_preserve_order(values, max_items=self.policy.max_open_loops)

    def _merge_prior_items(
        self,
        existing_items: Sequence[str],
        new_items: Sequence[str],
        *,
        max_items: int,
    ) -> tuple[str, ...]:
        return _dedupe_preserve_order([*existing_items, *new_items], max_items=max_items)

    def _extract_stable_facts(
        self,
        messages: Sequence[Mapping[str, Any]] | Sequence[dict[str, Any]],
    ) -> tuple[str, ...]:
        candidates: list[tuple[int, int, str]] = []
        for index, item in enumerate(messages):
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = _normalize_text(item.get("content") or "")
            if not content or not is_signal_role(role) or is_control_plane_noise(content):
                continue
            score = _signal_score(role, content)
            if score <= 0:
                continue
            candidates.append((score, _message_sequence(item, index), _trim_text(content, max_chars=180)))
        if not candidates:
            return ()
        selected = sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)[: self.policy.max_stable_facts]
        selected.sort(key=lambda item: item[1])
        return _dedupe_preserve_order(
            [snippet for _score, _seq, snippet in selected], max_items=self.policy.max_stable_facts
        )

    def _extract_open_loops(
        self,
        messages: Sequence[Mapping[str, Any]] | Sequence[dict[str, Any]],
    ) -> tuple[str, ...]:
        results: list[str] = []
        for item in reversed(list(messages)):
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = _normalize_text(item.get("content") or "")
            if not content or not is_signal_role(role) or _is_low_signal(content):
                continue
            if (role == "user" and any(pattern.search(content) for pattern in _OPEN_LOOP_PATTERNS)) or (
                role == "assistant" and any(pattern.search(content) for pattern in _COMMITMENT_PATTERNS)
            ):
                results.append(_trim_text(content, max_chars=160))
            if len(results) >= self.policy.max_open_loops:
                break
        results.reverse()
        return _dedupe_preserve_order(results, max_items=self.policy.max_open_loops)


async def build_session_continuity_pack(
    messages: Sequence[Mapping[str, Any]] | Sequence[dict[str, Any]],
    identity: RoleContextIdentity | None = None,
    *,
    existing_pack: SessionContinuityPack | Mapping[str, Any] | None = None,
    focus: str = "",
    recent_window_messages: int = 0,
    policy: SessionContinuityPolicy | None = None,
) -> SessionContinuityPack | None:
    engine = SessionContinuityEngine(policy=policy)
    return await engine.build_pack(
        messages,
        identity=identity,
        existing_pack=existing_pack,
        focus=focus,
        recent_window_messages=recent_window_messages,
        context_os_enabled=resolve_context_os_enabled(default=True),
    )


__all__ = [
    "SessionContinuityEngine",
    "SessionContinuityPack",
    "SessionContinuityPolicy",
    "SessionContinuityProjection",
    "SessionContinuityRequest",
    "build_session_continuity_pack",
    "history_pairs_to_messages",
    "messages_to_history_pairs",
]
