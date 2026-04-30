"""Generic domain adapter for State-First Context OS."""

from __future__ import annotations

import json
import re

from polaris.kernelone.context._token_estimator import estimate_tokens as _estimate_tokens
from polaris.kernelone.context.control_plane_noise import is_control_plane_noise

from ..helpers import _normalize_text, _trim_text, get_metadata_value
from ..models_v2 import (
    ArtifactRecordV2 as ArtifactRecord,
    EpisodeCardV2 as EpisodeCard,
    PendingFollowUpV2 as PendingFollowUp,
    RoutingClassEnum as RoutingClass,
    TranscriptEventV2 as TranscriptEvent,
    WorkingStateV2 as WorkingState,
)
from ..policies import StateFirstContextOSPolicy
from .contracts import ContextDomainAdapter, DomainRoutingDecision, DomainStatePatchHints

_LOW_SIGNAL_PATTERNS = (
    r"^(hi|hello|hey|你好|您好|嗨|thanks|thank you|谢谢|ok|好的|收到|稍等|bye|再见)\b",
    r"(换个名字|改名字|改名|叫我|叫你|你是什么模型|what model are you|who are you)",
)
_GOAL_PATTERNS = (
    re.compile(r"(实现|继续|开工|落地|抽离|统一|改造|补测试|写蓝图|写文档|排查|修复|完成)"),
    re.compile(
        r"\b(implement|continue|start|ship|build|write|document|stabilize|fix|finish)\b",
        re.IGNORECASE,
    ),
)
_PLAN_PATTERNS = (re.compile(r"(计划|蓝图|方案|步骤|拆解|roadmap|plan|blueprint)", re.IGNORECASE),)
_DECISION_PATTERNS = (
    re.compile(r"(改成|采用|决定|就按|必须|统一为|canonical|直接走|choose|use|adopt|decision)", re.IGNORECASE),
)
_DELIVERABLE_PATTERNS = (re.compile(r"(测试|文档|artifact|验收|验证|receipt|deliverable|spec)", re.IGNORECASE),)
_BLOCKED_PATTERNS = (re.compile(r"(blocked|阻塞|卡住|依赖|等待|waiting)", re.IGNORECASE),)
_PREFERENCE_PATTERNS = (
    re.compile(
        r"^(请|不要|必须|希望|只要|优先|尽量|please|must|must not|do not|don't|keep|preserve|want)", re.IGNORECASE
    ),
)
_OPEN_LOOP_PATTERNS = (
    re.compile(r"(继续|开始|开工|实现|重构|修复|补|运行|排查|处理|收口|抽离|落地|总结|写计划)"),
    re.compile(
        r"\b(continue|start|implement|refactor|fix|add|update|run|ship)\b",
        re.IGNORECASE,
    ),
)
_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2}|20\d{2}/\d{2}/\d{2}|20\d{2}\.\d{2}\.\d{2})\b")
_AFFIRMATIVE_PATTERNS = (
    re.compile(r"^(需要|要|可以|行|好|好的|继续|开始|确认|是|是的|要的|请继续|请开始|嗯|对)$"),
    re.compile(r"^(yes|y|ok|okay|sure|go ahead|please do|do it|continue|start)$", re.IGNORECASE),
)


def _is_low_signal(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in _LOW_SIGNAL_PATTERNS)


def _looks_like_large_payload(text: str, *, policy: StateFirstContextOSPolicy) -> bool:
    token = str(text or "")
    if not token:
        return False
    if len(token) >= policy.artifact_char_threshold:
        return True
    if _estimate_tokens(token) >= policy.artifact_token_threshold:
        return True
    stripped = token.lstrip()
    if stripped.startswith(("{", "[", "<html", "<!doctype", "```")):
        return True
    return token.count("\n") >= 18


def _extract_json_keys(text: str) -> tuple[str, ...]:
    try:
        payload = json.loads(text)
    except (RuntimeError, ValueError):
        return ()
    if isinstance(payload, dict):
        return tuple(str(key).strip() for key in list(payload.keys())[:8] if str(key).strip())
    return ()


def _guess_artifact_type(text: str) -> str:
    stripped = str(text or "").lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "tool_result"
    if stripped.startswith("<"):
        return "markup"
    if "```" in stripped:
        return "snippet"
    return "evidence"


def _guess_mime(text: str) -> str:
    stripped = str(text or "").lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "application/json"
    if stripped.startswith("<html") or stripped.startswith("<!doctype"):
        return "text/html"
    if stripped.startswith("<"):
        return "application/xml"
    return "text/plain"


def _unique(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        token = _normalize_text(item)
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(token)
    return tuple(result)


def _is_followup_confirmation(content: str) -> bool:
    token = _normalize_text(content)
    if not token:
        return False
    return any(pattern.fullmatch(token) for pattern in _AFFIRMATIVE_PATTERNS)


class GenericContextDomainAdapter(ContextDomainAdapter):
    """Generic adapter with no code-domain assumptions."""

    adapter_id = "generic"

    # Phase 1 Fix: Heuristic routing rules for tool results
    # Prevents small but critical tool results from being archived
    _SMALL_FILE_THRESHOLD_BYTES = 5 * 1024  # 5KB threshold for "small file"
    _ERROR_KEYWORDS = frozenset(
        {
            "error",
            "exception",
            "timeout",
            "failed",
            "failure",
            "crash",
            "abort",
            # Search/edit failures (critical for edit_file fallback guidance visibility)
            "not found",
            "no match",
            "no matches",
            "syntax error",
            "invalid",
            "cannot",
            "unable",
            "refused",
            "denied",
        }
    )
    _CODE_LINE_THRESHOLD = 100  # Lines above this trigger SUMMARIZE instead of ARCHIVE

    def _contains_error_keywords(self, content: str) -> bool:
        """Check if content contains error-related keywords (case-insensitive)."""
        content_lower = content.lower()
        return any(keyword in content_lower for keyword in self._ERROR_KEYWORDS)

    def _estimate_content_size(self, content: str) -> int:
        """Estimate content size in bytes (UTF-8 encoding)."""
        return len(content.encode("utf-8"))

    def _count_lines(self, content: str) -> int:
        """Count lines in content."""
        return content.count("\n") + 1

    def classify_event(
        self,
        event: TranscriptEvent,
        *,
        policy: StateFirstContextOSPolicy,
    ) -> DomainRoutingDecision:
        if _is_low_signal(event.content):
            return DomainRoutingDecision(
                route=RoutingClass.CLEAR,
                confidence=0.99,
                reasons=("low_signal",),
            )

        # Phase 1 Fix: Heuristic rules for tool_result routing
        # Prevents LLM from losing visibility into small but critical tool results
        if event.kind == "tool_result":
            content_size = self._estimate_content_size(event.content)

            # Rule 1: Small files (<5KB) should NOT be archived
            # These are typically read_file results that LLM needs to see
            if content_size < self._SMALL_FILE_THRESHOLD_BYTES:
                # Check for error keywords - error content is high priority
                if self._contains_error_keywords(event.content):
                    return DomainRoutingDecision(
                        route=RoutingClass.PATCH,
                        confidence=0.95,
                        reasons=("tool_result_small_with_errors",),
                    )
                return DomainRoutingDecision(
                    route=RoutingClass.PATCH,
                    confidence=0.88,
                    reasons=("tool_result_small_file",),
                )

            # Rule 2: Content with errors should be PATCHed for visibility
            if self._contains_error_keywords(event.content):
                return DomainRoutingDecision(
                    route=RoutingClass.PATCH,
                    confidence=0.92,
                    reasons=("tool_result_with_errors",),
                )

            # Rule 3: Large code files use SUMMARIZE instead of ARCHIVE
            # This keeps structure visible while reducing tokens
            line_count = self._count_lines(event.content)
            if line_count > self._CODE_LINE_THRESHOLD:
                return DomainRoutingDecision(
                    route=RoutingClass.SUMMARIZE,
                    confidence=0.85,
                    reasons=("tool_result_large_code",),
                )

            # Default: tool_result that didn't match above rules -> ARCHIVE
            return DomainRoutingDecision(
                route=RoutingClass.ARCHIVE,
                confidence=0.90,
                reasons=("tool_result_large_payload",),
            )

        if _looks_like_large_payload(event.content, policy=policy):
            return DomainRoutingDecision(
                route=RoutingClass.ARCHIVE,
                confidence=0.97,
                reasons=("large_payload",),
            )
        if any(pattern.search(event.content) for pattern in (_GOAL_PATTERNS + _PLAN_PATTERNS + _DECISION_PATTERNS)):
            return DomainRoutingDecision(
                route=RoutingClass.PATCH,
                confidence=0.86,
                reasons=("state_signal",),
            )
        return DomainRoutingDecision(
            route=RoutingClass.SUMMARIZE,
            confidence=0.62,
            reasons=("default_narrative",),
        )

    def build_artifact(
        self,
        event: TranscriptEvent,
        *,
        artifact_id: str,
        policy: StateFirstContextOSPolicy,
    ) -> ArtifactRecord | None:
        decision = self.classify_event(event, policy=policy)
        if decision.route != RoutingClass.ARCHIVE:
            return None
        return ArtifactRecord(
            artifact_id=artifact_id,
            artifact_type=_guess_artifact_type(event.content),
            mime_type=_guess_mime(event.content),
            token_count=_estimate_tokens(event.content),
            char_count=len(event.content),
            peek=_trim_text(event.content, max_chars=180),
            keys=_extract_json_keys(event.content),
            content=event.content,
            source_event_ids=(event.event_id,),
            metadata=(
                ("role", event.role),
                ("sequence", event.sequence),
                ("adapter_id", self.adapter_id),
            ),
        )

    def extract_state_hints(self, event: TranscriptEvent) -> DomainStatePatchHints:
        content = _normalize_text(event.content)
        if not content or event.role in {"tool", "system"} or is_control_plane_noise(content):
            return DomainStatePatchHints()
        followup_action = _normalize_text(get_metadata_value(event.metadata, "followup_action"))
        followup_confirmed = str(get_metadata_value(event.metadata, "followup_confirmed") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if not followup_confirmed and followup_action and event.role == "user":
            followup_confirmed = _is_followup_confirmation(content)
        goals: list[str] = []
        accepted_plan: list[str] = []
        open_loops: list[str] = []
        blocked_on: list[str] = []
        deliverables: list[str] = []
        preferences: list[str] = []
        style: list[str] = []
        persistent_facts: list[str] = []
        temporal_facts: list[str] = []
        entities: list[str] = []
        decisions: list[str] = []

        if event.role == "user" and any(pattern.search(content) for pattern in _GOAL_PATTERNS):
            goals.append(content)
        # Preferences/style are orthogonal to task classification — they express
        # user constraints ("不要", "必须") that feed into hard_constraints.
        # Must be checked independently, NOT in the elif chain below.
        if event.role == "user" and _PREFERENCE_PATTERNS[0].search(content):
            if any(token in content.lower() for token in ("concise", "detailed", "简洁", "详细")):
                style.append(content)
            else:
                preferences.append(content)
        # Mutually exclusive task classification (priority: blocked > plan > deliverable > open_loop)
        # Previously used independent if-checks causing 3x redundancy when patterns overlapped
        # (e.g., "蓝图" matched both PLAN and DELIVERABLE, "验证" matched both OPEN_LOOP and DELIVERABLE)
        # Only classify if NOT already captured as a preference/constraint above.
        classification_match = False
        if not preferences:
            if any(pattern.search(content) for pattern in _BLOCKED_PATTERNS):
                blocked_on.append(content)
                classification_match = True
            elif any(pattern.search(content) for pattern in _PLAN_PATTERNS):
                accepted_plan.append(content)
                classification_match = True
            elif any(pattern.search(content) for pattern in _DELIVERABLE_PATTERNS):
                deliverables.append(content)
                classification_match = True
            elif any(pattern.search(content) for pattern in _OPEN_LOOP_PATTERNS):
                open_loops.append(content)
                classification_match = True
        # Fallback: if content wasn't captured by any classification pattern,
        # AND it's not a short affirmation, treat it as a goal
        # BUT: don't add if it was already captured as a goal by the goal patterns above
        # Also check that content matched something - don't add short content that matches
        # open_loop patterns unless we want it as a goal (e.g., "总结" is open_loop, not a goal)
        if (
            not classification_match
            and not goals
            and content
            and event.role == "user"
            and not _is_followup_confirmation(content)
        ):
            goals.append(content)
        if event.role == "user" and followup_action and followup_confirmed:
            goals.append(followup_action)
            open_loops.append(followup_action)
            decisions.append(_trim_text(f"user_confirmed_followup: {followup_action}", max_chars=220))
        if any(pattern.search(content) for pattern in _DECISION_PATTERNS):
            decisions.append(_trim_text(content, max_chars=220))
        for match in _DATE_RE.findall(content):
            temporal_facts.append(match)
        return DomainStatePatchHints(
            goals=_unique(goals),
            accepted_plan=_unique(accepted_plan),
            open_loops=_unique(open_loops),
            blocked_on=_unique(blocked_on),
            deliverables=_unique(deliverables),
            preferences=_unique(preferences),
            style=_unique(style),
            persistent_facts=_unique(persistent_facts),
            temporal_facts=_unique(temporal_facts),
            entities=_unique(entities),
            decisions=_unique(decisions),
        )

    def should_seal_episode(
        self,
        *,
        closed_events: tuple[TranscriptEvent, ...],
        active_window: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
    ) -> bool:
        if not closed_events:
            return False
        if len(closed_events) >= 4:
            return True
        if any(event.route == RoutingClass.PATCH for event in closed_events):
            return True
        return bool(working_state.decision_log)

    def classify_assistant_followup(
        self,
        event: TranscriptEvent,
        *,
        policy: StateFirstContextOSPolicy,
    ) -> DomainRoutingDecision | None:
        """Classify if an assistant event is a followup confirmation.

        Returns None by default, allowing other classifiers to handle it.
        """
        return None

    def on_event_created(self, event: TranscriptEvent) -> None:
        """Lifecycle hook called when a new event is created.

        Default empty implementation - domain adapters may override.
        """
        pass

    def on_pending_followup_resolved(self, followup: PendingFollowUp) -> None:
        """Lifecycle hook called when a pending follow-up is resolved.

        Default empty implementation - domain adapters may override.
        """
        pass

    def on_artifact_built(self, artifact: ArtifactRecord) -> None:
        """Lifecycle hook called when an artifact is built.

        Default empty implementation - domain adapters may override.
        """
        pass

    def on_episode_sealed(self, episode: EpisodeCard) -> None:
        """Lifecycle hook called when an episode is sealed.

        Default empty implementation - domain adapters may override.
        """
        pass
