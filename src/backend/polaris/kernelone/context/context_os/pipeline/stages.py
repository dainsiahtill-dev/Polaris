"""Seven stage processors for StateFirstContextOS projection pipeline."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.context_os.classifier import DialogActClassifier
from polaris.kernelone.context.context_os.helpers import (
    _artifact_id,
    _clamp_confidence,
    _dedupe_state_entries,
    _estimate_tokens,
    _event_id,
    _normalize_text,
    _slug,
    _StateAccumulator,
    _trim_text,
    _utc_now_iso,
    get_metadata_value,
)
from polaris.kernelone.context.context_os.model_utils import validated_replace
from polaris.kernelone.context.context_os.models_v2 import (
    ArtifactRecordV2 as ArtifactRecord,
    BudgetPlanV2 as BudgetPlan,
    ContextSlicePlanV2 as ContextSlicePlan,
    ContextSliceSelectionV2 as ContextSliceSelection,
    DecisionEntryV2 as DecisionEntry,
    DialogAct,
    DialogActResultV2 as DialogActResult,
    EpisodeCardV2 as EpisodeCard,
    PendingFollowUpV2 as PendingFollowUp,
    RoutingClassEnum as RoutingClass,
    RunCardV2 as RunCard,
    StateEntryV2 as StateEntry,
    TaskStateViewV2 as TaskStateView,
    TranscriptEventV2 as TranscriptEvent,
    UserProfileStateV2 as UserProfileState,
    WorkingStateV2 as WorkingState,
)
from polaris.kernelone.context.context_os.patterns import (
    _AFFIRMATIVE_RESPONSE_PATTERNS,
    _ASSISTANT_FOLLOWUP_PATTERNS,
    _CONSTRAINT_PREFIX_RE,
    _NEGATIVE_RESPONSE_PATTERNS,
)
from polaris.kernelone.context.context_os.policies import StateFirstContextOSPolicy
from polaris.kernelone.telemetry.debug_stream import emit_debug_event

from .contracts import (
    ArtifactSelectorOutput,
    BudgetPlannerOutput,
    CanonicalizerOutput,
    EpisodeSealerOutput,
    PipelineInput,
    StatePatcherOutput,
    TranscriptMergerOutput,
    WindowCollectorOutput,
)

if TYPE_CHECKING:
    from polaris.kernelone.context.context_os.domain_adapters import ContextDomainAdapter

logger = logging.getLogger(__name__)

# Artifact offloading constants
MAX_INLINE_CHARS: int = 500
MAX_STUB_CHARS: int = 200


def _extract_assistant_followup_action(text: str) -> str:
    content = _normalize_text(text)
    if not content:
        return ""
    for pattern in _ASSISTANT_FOLLOWUP_PATTERNS:
        match = pattern.search(content)
        if match is None:
            continue
        action = _normalize_text(match.group("action"))
        if not action:
            continue
        action = re.sub(r"^[,\uFF0C\u3002:\uFF1A;\-\s]+", "", action).strip()
        action = re.sub(r"[?\uFF1F!\uFF01\u3002]+$", "", action).strip()
        if action:
            return _trim_text(action, max_chars=220)
    return ""


def _is_negative_response(text: str) -> bool:
    content = _normalize_text(text)
    if not content:
        return False
    return any(pattern.fullmatch(content) for pattern in _NEGATIVE_RESPONSE_PATTERNS)


def _is_affirmative_response(text: str) -> bool:

    content = _normalize_text(text)
    if not content:
        return False
    return any(pattern.fullmatch(content) for pattern in _AFFIRMATIVE_RESPONSE_PATTERNS)


def _decision_kind(summary: str) -> str:
    lowered = _normalize_text(summary).lower()
    if not lowered:
        return "decision"
    if any(token in lowered for token in ("plan", "blueprint", "方案", "计划", "蓝图")):
        return "accepted_plan"
    if any(token in lowered for token in ("must", "must not", "do not", "禁止", "不要", "必须")):
        return "constraint"
    if any(token in lowered for token in ("blocked", "阻塞", "等待", "依赖")):
        return "blocked_on"
    return "decision"


def _extract_hard_constraints(working_state: WorkingState) -> tuple[str, ...]:
    values: list[str] = []
    for collection in (
        working_state.user_profile.preferences,
        working_state.user_profile.persistent_facts,
        working_state.task_state.blocked_on,
    ):
        for item in collection:
            if _CONSTRAINT_PREFIX_RE.search(item.value):
                values.append(item.value)
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return tuple(deduped[:6])


# ---------------------------------------------------------------------------
# Stage 1: TranscriptMerger
# ---------------------------------------------------------------------------


class TranscriptMerger:
    """Stage 1: Merge existing transcript with new incoming messages."""

    def __init__(self, domain_adapter: ContextDomainAdapter | None = None) -> None:
        self._domain_adapter = domain_adapter
        self._content_store: Any | None = None

    def _get_content_store(self) -> Any:
        if self._content_store is None:
            from polaris.kernelone.context.context_os.content_store import ContentStore

            self._content_store = ContentStore()
        return self._content_store

    def process(self, inp: PipelineInput) -> TranscriptMergerOutput:
        existing = inp.existing_snapshot_transcript
        messages = inp.messages

        merged: dict[str, TranscriptEvent] = {item.event_id: item for item in existing}
        next_sequence = max((item.sequence for item in existing), default=-1) + 1

        # First pass: emit tool_call events before main message loop
        pending_tool_calls: list[tuple[int, dict[str, Any], str]] = []
        for _fallback, raw in enumerate(messages or ()):
            if not isinstance(raw, dict):
                continue
            metadata: dict[str, Any] = dict(raw.get("metadata") or {})
            tool_calls = metadata.get("tool_calls")
            if not isinstance(tool_calls, (list, tuple)) or not tool_calls:
                continue

            sequence_token = str(raw.get("sequence") or "").strip()
            seq = int(sequence_token) if sequence_token.isdigit() else next_sequence

            source_event_id = str(raw.get("event_id") or "").strip()
            if not source_event_id:
                source_event_id = _event_id(seq, "assistant", "tool_call_batch")

            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                pending_tool_calls.append((seq, tc, source_event_id))

        # Emit tool_call events with sequential sub-indices
        for _idx, (_seq, tool_call, source_event_id) in enumerate(pending_tool_calls):
            call_sequence = next_sequence
            next_sequence += 1

            tool_name = str(tool_call.get("name") or tool_call.get("tool") or "unknown").strip()
            call_id = str(
                tool_call.get("id") or tool_call.get("call_id") or _event_id(int(call_sequence), "tool_call", tool_name)
            ).strip()
            args = tool_call.get("arguments") or tool_call.get("args") or {}
            if not isinstance(args, dict):
                args = {"raw": str(args)}

            tc_metadata: dict[str, Any] = {
                "tool_name": tool_name,
                "tool_call_id": call_id,
                "tool_args": args,
                "source_event_id": source_event_id,
                "event_kind": "tool_call",
            }
            for key, value in tool_call.items():
                if key not in ("name", "tool", "id", "call_id", "arguments", "args"):
                    tc_metadata[key] = value

            event_id = _event_id(int(call_sequence), "tool_call", f"{tool_name}:{call_id}")
            tc_content = f"tool_call: {tool_name}({args})"
            content_ref = None
            with suppress(Exception):
                tc_content_ref = self._get_content_store().intern(tc_content)
                content_ref = tc_content_ref
            event = TranscriptEvent(
                event_id=event_id,
                sequence=int(call_sequence),
                role="assistant",
                kind="tool_call",
                route="",
                content=tc_content,
                source_turns=(f"t{int(call_sequence)}",),
                artifact_id=None,
                created_at=_utc_now_iso(),
                metadata=tc_metadata,  # type: ignore[arg-type]
                content_ref_hash=content_ref.hash if content_ref else "",
                content_ref_size=int(content_ref.size) if content_ref else 0,
                content_ref_mime=content_ref.mime if content_ref else "",
            )
            merged[event.event_id] = event
            self._notify_event_created(event)

        # Second pass: process all messages
        for _fallback, raw in enumerate(messages or ()):
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or "").strip().lower()
            content = _normalize_text(raw.get("content") or raw.get("message") or "")

            metadata = dict(raw.get("metadata") or {})
            tool_calls_in_msg = metadata.get("tool_calls")

            if not role or (not content and not tool_calls_in_msg):
                continue

            sequence_token = str(raw.get("sequence") or "").strip()
            if sequence_token.isdigit():
                sequence = int(sequence_token)
                next_sequence = max(next_sequence, sequence + 1)
            else:
                sequence = next_sequence
                next_sequence += 1

            event_id = str(raw.get("event_id") or "").strip() or _event_id(sequence, role, content)
            kind = str(raw.get("kind") or "").strip() or ("tool_result" if role == "tool" else f"{role}_turn")
            route = str(raw.get("route") or "").strip()
            source_turns_raw = raw.get("source_turns")
            if isinstance(source_turns_raw, (list, tuple)) and source_turns_raw:
                source_turns = tuple(str(s) for s in source_turns_raw)
            else:
                source_turns = (f"t{sequence}",)
            artifact_id = raw.get("artifact_id")
            if artifact_id and isinstance(artifact_id, str) and artifact_id.strip():
                artifact_id = artifact_id.strip()
            else:
                artifact_id = None
            created_at = str(raw.get("created_at") or "").strip() or _utc_now_iso()

            # v2.1 dual-write: intern content
            content_ref = None
            if content:
                with suppress(Exception):
                    content_ref = self._get_content_store().intern(content)

            event = TranscriptEvent(
                event_id=event_id,
                sequence=sequence,
                role=role,
                kind=kind,
                route=route,
                content=content,
                source_turns=source_turns,
                artifact_id=artifact_id,
                created_at=created_at,
                metadata=metadata,  # type: ignore[arg-type]
                content_ref_hash=content_ref.hash if content_ref else "",
                content_ref_size=int(content_ref.size) if content_ref else 0,
                content_ref_mime=content_ref.mime if content_ref else "",
            )
            merged[event.event_id] = event
            self._notify_event_created(event)

        transcript = tuple(sorted(merged.values(), key=lambda item: (item.sequence, item.event_id)))
        return TranscriptMergerOutput(transcript=transcript)

    def _notify_event_created(self, event: TranscriptEvent) -> None:
        if self._domain_adapter is None:
            return
        notify = getattr(self._domain_adapter, "on_event_created", None)
        if callable(notify):
            notify(event)


# ---------------------------------------------------------------------------
# Stage 2: Canonicalizer
# ---------------------------------------------------------------------------


class Canonicalizer:
    """Stage 2: Dialog act classification, routing, artifact offload, follow-up resolution."""

    def __init__(
        self,
        policy: StateFirstContextOSPolicy,
        domain_adapter: ContextDomainAdapter | None = None,
    ) -> None:
        self._policy = policy
        self._domain_adapter = domain_adapter
        self._dialog_act_classifier: DialogActClassifier | None = None
        # Phase 2: Lazy-initialized tiered summarizer for SUMMARIZE routing
        self._summarizer: Any | None = None

    @property
    def _classifier(self) -> DialogActClassifier:
        if self._dialog_act_classifier is None:
            self._dialog_act_classifier = DialogActClassifier()
        return self._dialog_act_classifier

    def _get_summarizer(self) -> Any | None:
        """Lazy initialization of tiered summarizer for SUMMARIZE routing."""
        if self._summarizer is None:
            try:
                from polaris.kernelone.context.context_os.summarizers import TieredSummarizer

                self._summarizer = TieredSummarizer()
            except ImportError:
                logger.debug("TieredSummarizer not available")
        return self._summarizer

    def _infer_content_type(self, item: TranscriptEvent) -> str:
        """Infer content type for summarization strategy selection.

        Args:
            item: The transcript event to analyze.

        Returns:
            Content type string (log, code, json, dialogue, text).
        """
        if item.kind == "tool_result":
            content = item.content.strip()
            # Check for error indicators
            if any(kw in content.lower() for kw in ("error", "exception", "traceback", "failed")):
                return "log"
            # Check for code
            if content.startswith("def ") or content.startswith("class ") or "```" in content:
                return "code"
            # Check for JSON
            if content.startswith("{") or content.startswith("["):
                return "json"
            return "text"

        if item.kind in ("user_turn", "assistant_turn"):
            return "dialogue"

        return "text"

    def process(
        self,
        inp: PipelineInput,
        merger_out: TranscriptMergerOutput,
    ) -> CanonicalizerOutput:
        transcript = merger_out.transcript
        # Convert legacy dataclass TranscriptEvent -> Pydantic TranscriptEventV2
        if transcript and not hasattr(transcript[0], "model_copy"):
            import dataclasses

            transcript = tuple(TranscriptEvent.model_validate(dataclasses.asdict(e)) for e in transcript)
        existing_artifacts = inp.existing_snapshot_artifacts
        current_pending_followup = inp.current_pending_followup

        artifact_by_id = {item.artifact_id: item for item in existing_artifacts}
        updated_events: list[TranscriptEvent] = []

        # Track pending follow-up
        pending_followup: PendingFollowUp | None = None
        pending_followup_action = ""
        pending_followup_event_id = ""
        pending_followup_sequence = 0

        if (
            current_pending_followup
            and current_pending_followup.action
            and current_pending_followup.status == "pending"
        ):
            pending_followup = current_pending_followup
            pending_followup_action = current_pending_followup.action
            pending_followup_event_id = current_pending_followup.source_event_id
            pending_followup_sequence = current_pending_followup.source_sequence

        for item in transcript:
            # Classify dialog act
            dialog_act_result: DialogActResult | None = None
            if item.role in ("user", "assistant"):
                dialog_act_result = self._classifier.classify(item.content, role=item.role)

            if item.role == "assistant":
                inferred_action = _extract_assistant_followup_action(item.content)

                if (
                    not inferred_action
                    and self._domain_adapter is not None
                    and hasattr(self._domain_adapter, "classify_assistant_followup")
                ):
                    domain_decision = self._domain_adapter.classify_assistant_followup(item, policy=self._policy)
                    if domain_decision and domain_decision.reasons:
                        for reason in domain_decision.reasons:
                            if reason.startswith("code_followup_"):
                                inferred_action = reason.replace("code_followup_", "")
                                break

                if inferred_action:
                    pending_followup_action = inferred_action
                    pending_followup_event_id = item.event_id
                    pending_followup_sequence = item.sequence

            followup_metadata: dict[str, Any] = {}
            followup_confirmed = False
            dialog_act_resolved = False
            dialog_act: str = DialogAct.UNKNOWN
            resolved_followup_status: str | None = None

            if item.role == "user" and pending_followup_action:
                if dialog_act_result:
                    dialog_act = dialog_act_result.act
                    if dialog_act in (DialogAct.AFFIRM, DialogAct.COMMIT):
                        followup_confirmed = True
                        dialog_act_resolved = True
                        resolved_followup_status = "confirmed"
                    elif dialog_act == DialogAct.DENY:
                        followup_confirmed = False
                        dialog_act_resolved = True
                        resolved_followup_status = "denied"
                    elif dialog_act == DialogAct.CANCEL:
                        dialog_act_resolved = True
                        resolved_followup_status = "denied"
                    elif dialog_act == DialogAct.PAUSE:
                        dialog_act_resolved = True
                        resolved_followup_status = "paused"
                    elif dialog_act == DialogAct.REDIRECT:
                        dialog_act_resolved = True
                        resolved_followup_status = "redirected"

                if not dialog_act_resolved:
                    if _is_affirmative_response(item.content):
                        followup_confirmed = True
                        dialog_act = DialogAct.AFFIRM
                        dialog_act_resolved = True
                        resolved_followup_status = "confirmed"
                    elif _is_negative_response(item.content):
                        dialog_act = DialogAct.DENY
                        dialog_act_resolved = True
                        resolved_followup_status = "denied"

                if dialog_act_resolved:
                    followup_metadata = {
                        "followup_action": pending_followup_action,
                        "followup_confirmed": str(followup_confirmed).lower(),
                        "followup_source_sequence": str(pending_followup_sequence),
                        "dialog_act": dialog_act,
                    }
                    final_status = (
                        resolved_followup_status
                        if resolved_followup_status in ("confirmed", "denied", "paused", "redirected", "expired")
                        else "expired"
                    )
                    pending_followup = PendingFollowUp(
                        action=pending_followup_action,
                        source_event_id=pending_followup_event_id,
                        source_sequence=pending_followup_sequence,
                        status=final_status,
                        updated_at=_utc_now_iso(),
                    )
                    self._notify_pending_followup_resolved(pending_followup)
                    pending_followup_action = ""
                    pending_followup_event_id = ""
                    pending_followup_sequence = 0
                else:
                    if pending_followup and pending_followup.status == "pending":
                        pending_followup = PendingFollowUp(
                            action=pending_followup.action,
                            source_event_id=pending_followup.source_event_id,
                            source_sequence=pending_followup.source_sequence,
                            status="expired",
                            updated_at=_utc_now_iso(),
                        )
                        self._notify_pending_followup_resolved(pending_followup)
                    pending_followup_action = ""
                    pending_followup_event_id = ""
                    pending_followup_sequence = 0

            forced_route = str(get_metadata_value(item.metadata, "forced_route") or "").strip().lower()
            if forced_route:
                route = forced_route
                decision_metadata = {}
                routing_confidence = _clamp_confidence(
                    get_metadata_value(item.metadata, "routing_confidence"),
                    default=1.0,
                )
                routing_reasons = tuple(
                    str(value).strip()
                    for value in (get_metadata_value(item.metadata, "routing_reasons") or [])
                    if str(value).strip()
                ) or ("manual_reclassification",)
            elif followup_confirmed:
                route = RoutingClass.PATCH
                decision_metadata = dict(followup_metadata)
                routing_confidence = 0.94
                routing_reasons = ("assistant_followup_confirmation",)
            else:
                decision = (
                    self._domain_adapter.classify_event(item, policy=self._policy) if self._domain_adapter else None
                )
                route = decision.route if decision else RoutingClass.SUMMARIZE
                if route is None:
                    route = RoutingClass.SUMMARIZE
                decision_metadata = {
                    **(dict(decision.metadata) if decision else {}),
                    **followup_metadata,
                }
                routing_confidence = _clamp_confidence(decision.confidence if decision else 0.5, default=0.5)
                routing_reasons = tuple(decision.reasons or ()) if decision else ()

            artifact_id = item.artifact_id if route == RoutingClass.ARCHIVE else None
            if route == RoutingClass.ARCHIVE:
                artifact_id = artifact_id or _artifact_id(item.event_id)
                existing_artifact = artifact_by_id.get(artifact_id)
                if (
                    existing_artifact is None or (not existing_artifact.content and item.content)
                ) and self._domain_adapter:
                    artifact = self._domain_adapter.build_artifact(item, artifact_id=artifact_id, policy=self._policy)
                    if artifact is not None:
                        artifact_by_id[artifact_id] = artifact
                        self._notify_artifact_built(artifact)

            # Phase 2: Apply intelligent summarization for SUMMARIZE routing
            summarized_content: str | None = None
            if route == RoutingClass.SUMMARIZE:
                summarizer = self._get_summarizer()
                if summarizer and len(item.content) > 500:  # Only summarize substantial content
                    try:
                        content_type = self._infer_content_type(item)
                        summarized_content = summarizer.summarize(
                            content=item.content,
                            max_tokens=300,
                            content_type=content_type,
                        )
                        decision_metadata["summarized"] = True
                        decision_metadata["original_length"] = len(item.content)
                        decision_metadata["summary_length"] = len(summarized_content)
                    except (RuntimeError, ValueError) as e:
                        logger.debug(f"Summarization failed for {item.event_id}: {e}")
                        decision_metadata["summarized"] = False
                        decision_metadata["summary_error"] = str(e)

            dialog_act_metadata: dict[str, Any] = {}
            if dialog_act_result is not None:
                dialog_act_metadata = {
                    "dialog_act": dialog_act_result.act,
                    "dialog_act_confidence": dialog_act_result.confidence,
                    "dialog_act_triggers": list(dialog_act_result.triggers),
                    "dialog_act_is_high_priority": DialogAct.is_high_priority(dialog_act_result.act),
                }

            # Use summarized content if available (SUMMARIZE routing)
            final_content = summarized_content if summarized_content is not None else item.content

            updated_events.append(
                validated_replace(
                    item,
                    route=route,
                    artifact_id=artifact_id,
                    content=final_content,
                    metadata={
                        **dict(item.metadata),
                        **decision_metadata,
                        **dialog_act_metadata,
                        "routing_confidence": routing_confidence,
                        "routing_reasons": list(routing_reasons),
                        "routing_adapter_id": getattr(self._domain_adapter, "adapter_id", ""),
                    },
                )
            )

        # Handle unresolved pending follow-up created in this turn
        if pending_followup_action and not pending_followup:
            pending_followup = PendingFollowUp(
                action=pending_followup_action,
                source_event_id=pending_followup_event_id,
                source_sequence=pending_followup_sequence,
                status="pending",
                updated_at=_utc_now_iso(),
            )

        artifacts = tuple(sorted(artifact_by_id.values(), key=lambda item: item.artifact_id))
        return CanonicalizerOutput(
            transcript=tuple(updated_events),
            artifacts=artifacts,
            resolved_followup=pending_followup,
        )

    def _notify_pending_followup_resolved(self, pending_followup: PendingFollowUp) -> None:
        if self._domain_adapter is None:
            return
        notify = getattr(self._domain_adapter, "on_pending_followup_resolved", None)
        if callable(notify):
            notify(pending_followup)

    def _notify_artifact_built(self, artifact: Any) -> None:
        if self._domain_adapter is None:
            return
        notify = getattr(self._domain_adapter, "on_artifact_built", None)
        if callable(notify):
            notify(artifact)


# ---------------------------------------------------------------------------
# Stage 3: StatePatcher
# ---------------------------------------------------------------------------


class StatePatcher:
    """Stage 3: Extract state hints from events and build WorkingState."""

    def __init__(self, policy: StateFirstContextOSPolicy, domain_adapter: ContextDomainAdapter | None = None) -> None:
        self._policy = policy
        self._domain_adapter = domain_adapter

    def process(self, canon_out: CanonicalizerOutput) -> StatePatcherOutput:
        transcript = canon_out.transcript
        artifacts = canon_out.artifacts

        acc = _StateAccumulator()
        decisions: list[DecisionEntry] = []
        last_decision_by_kind: dict[str, DecisionEntry] = {}
        current_goal_candidates: list[StateEntry] = []
        accepted_plan: list[StateEntry] = []
        open_loops: list[StateEntry] = []
        blocked_on: list[StateEntry] = []
        deliverables: list[StateEntry] = []
        preferences: list[StateEntry] = []
        style: list[StateEntry] = []
        persistent_facts: list[StateEntry] = []
        temporal_facts: list[StateEntry] = []
        active_entities: list[StateEntry] = []

        for item in transcript:
            if item.route == RoutingClass.CLEAR:
                continue
            turns = item.source_turns or (f"t{item.sequence}",)
            hints = self._domain_adapter.extract_state_hints(item) if self._domain_adapter else None
            if hints is None:
                continue

            for value in hints.goals:
                entry = acc.add(path="task_state.current_goal", value=value, source_turns=turns, confidence=0.96)
                if entry is not None:
                    current_goal_candidates.append(entry)
            for value in hints.accepted_plan:
                entry = acc.add(
                    path=f"task_state.accepted_plan::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.90,
                )
                if entry is not None:
                    accepted_plan.append(entry)
            for value in hints.open_loops:
                entry = acc.add(
                    path=f"task_state.open_loops::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.88,
                )
                if entry is not None:
                    open_loops.append(entry)
            for value in hints.blocked_on:
                entry = acc.add(
                    path=f"task_state.blocked_on::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.84,
                )
                if entry is not None:
                    blocked_on.append(entry)
            for value in hints.deliverables:
                entry = acc.add(
                    path=f"task_state.deliverables::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.82,
                )
                if entry is not None:
                    deliverables.append(entry)
            for value in hints.preferences:
                entry = acc.add(
                    path=f"user_profile.preferences::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.86,
                )
                if entry is not None:
                    preferences.append(entry)
            for value in hints.style:
                entry = acc.add(
                    path=f"user_profile.style::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.86,
                )
                if entry is not None:
                    style.append(entry)
            for value in hints.temporal_facts:
                entry = acc.add(
                    path=f"temporal_facts::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.80,
                )
                if entry is not None:
                    temporal_facts.append(entry)
            for value in hints.entities:
                entry = acc.add(
                    path=f"active_entities::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.78,
                )
                if entry is not None:
                    active_entities.append(entry)
            for value in hints.persistent_facts:
                entry = acc.add(
                    path=f"user_profile.persistent_facts::{_slug(value)}",
                    value=value,
                    source_turns=turns,
                    confidence=0.80,
                )
                if entry is not None:
                    persistent_facts.append(entry)
            for summary in hints.decisions:
                key = summary.lower()
                if not any(existing.summary.lower() == key for existing in decisions):
                    kind = _decision_kind(summary)
                    previous = last_decision_by_kind.get(kind)
                    decision = DecisionEntry(
                        decision_id=f"dec_{len(decisions) + 1}",
                        summary=summary,
                        source_turns=turns,
                        updated_at=_utc_now_iso(),
                        kind=kind,
                        supersedes=previous.decision_id if previous is not None else None,
                        basis_refs=tuple(item.value for item in active_entities[-2:]),
                    )
                    decisions.append(decision)
                    last_decision_by_kind[kind] = decision

        active_artifacts = tuple(item.artifact_id for item in artifacts[-self._policy.max_artifact_stubs :])

        # Convert dataclass StateEntry -> Pydantic StateEntryV2 for domain_adapter results
        def _se(entries: tuple[Any, ...]) -> tuple[StateEntry, ...]:
            return tuple(StateEntry.model_validate(e.to_dict()) if hasattr(e, "to_dict") else e for e in entries)

        def _de(entries: tuple[Any, ...]) -> tuple[DecisionEntry, ...]:
            return tuple(DecisionEntry.model_validate(e.to_dict()) if hasattr(e, "to_dict") else e for e in entries)

        working_state = WorkingState(
            user_profile=UserProfileState(
                preferences=_se(_dedupe_state_entries(preferences, limit=self._policy.max_open_loops)),
                style=_se(_dedupe_state_entries(style, limit=self._policy.max_open_loops)),
                persistent_facts=_se(_dedupe_state_entries(persistent_facts, limit=self._policy.max_stable_facts)),
            ),
            task_state=TaskStateView(
                current_goal=_se((current_goal_candidates[-1],))[0] if current_goal_candidates else None,
                accepted_plan=_se(_dedupe_state_entries(accepted_plan, limit=self._policy.max_open_loops)),
                open_loops=_se(_dedupe_state_entries(open_loops, limit=self._policy.max_open_loops)),
                blocked_on=_se(_dedupe_state_entries(blocked_on, limit=self._policy.max_open_loops)),
                deliverables=_se(_dedupe_state_entries(deliverables, limit=self._policy.max_open_loops)),
            ),
            decision_log=_de(tuple(decisions[-self._policy.max_decisions :])),
            active_entities=_se(_dedupe_state_entries(active_entities, limit=self._policy.max_stable_facts)),
            active_artifacts=active_artifacts,
            temporal_facts=_se(_dedupe_state_entries(temporal_facts, limit=self._policy.max_stable_facts)),
            state_history=_se(acc.entries),
        )
        return StatePatcherOutput(working_state=working_state)


# ---------------------------------------------------------------------------
# Stage 4: BudgetPlanner
# ---------------------------------------------------------------------------


class BudgetPlanner:
    """Stage 4: Compute token budgets and validate invariants."""

    def __init__(self, policy: StateFirstContextOSPolicy, resolved_context_window: int) -> None:
        self._policy = policy
        self._resolved_context_window = resolved_context_window

    def process(self, patcher_out: StatePatcherOutput, canon_out: CanonicalizerOutput) -> BudgetPlannerOutput:
        transcript = canon_out.transcript
        artifacts = canon_out.artifacts

        window = max(4096, self._resolved_context_window)
        ratio_based = int(window * self._policy.output_reserve_ratio)
        output_reserve = max(self._policy.output_reserve_min, ratio_based)
        tool_reserve = max(
            self._policy.tool_reserve_min,
            int(window * self._policy.tool_reserve_ratio),
        )
        safety_margin = max(self._policy.safety_margin_min, int(window * self._policy.safety_margin_ratio))
        input_budget = max(1024, window - output_reserve - tool_reserve - safety_margin)
        retrieval_budget = min(
            max(256, int(input_budget * self._policy.retrieval_ratio)),
            max(256, int(self._policy.planned_retrieval_tokens)),
        )
        current_input_tokens = sum(_estimate_tokens(item.content) for item in transcript)
        current_input_tokens += sum(min(item.token_count, 128) for item in artifacts)
        expected_next_input_tokens = (
            current_input_tokens + int(self._policy.p95_tool_result_tokens) + retrieval_budget + output_reserve
        )

        validation_error = ""
        if expected_next_input_tokens > window:
            overrun = expected_next_input_tokens - window
            validation_error = (
                f"BudgetPlan invariant violated: expected_next_input_tokens "
                f"({expected_next_input_tokens}) exceeds model_context_window "
                f"({window}) by {overrun} tokens"
            )

        budget_plan = BudgetPlan(
            model_context_window=window,
            output_reserve=output_reserve,
            tool_reserve=tool_reserve,
            safety_margin=safety_margin,
            input_budget=input_budget,
            retrieval_budget=retrieval_budget,
            soft_limit=max(512, int(input_budget * 0.55)),
            hard_limit=max(768, int(input_budget * 0.72)),
            emergency_limit=max(1024, int(input_budget * 0.85)),
            current_input_tokens=current_input_tokens,
            expected_next_input_tokens=expected_next_input_tokens,
            p95_tool_result_tokens=int(self._policy.p95_tool_result_tokens),
            planned_retrieval_tokens=int(self._policy.planned_retrieval_tokens),
            validation_error=validation_error,
        )

        # Validate invariants - raises BudgetExceededError if invalid
        budget_plan.validate_invariants()

        return BudgetPlannerOutput(budget_plan=budget_plan)


# ---------------------------------------------------------------------------
# Stage 5: WindowCollector
# ---------------------------------------------------------------------------


class WindowCollector:
    """Stage 5: Collect pinned active window events based on budget."""

    def __init__(
        self,
        policy: StateFirstContextOSPolicy,
        summarizer: Any | None = None,
    ) -> None:
        self._policy = policy
        self._summarizer = summarizer
        self._lazy_summarizer: Any | None = None

    def _get_summarizer(self) -> Any | None:
        """Lazy initialization of TieredSummarizer for JIT compression."""
        if self._summarizer is not None:
            return self._summarizer
        if self._lazy_summarizer is not None:
            return self._lazy_summarizer
        try:
            from polaris.kernelone.context.context_os.summarizers import TieredSummarizer

            self._lazy_summarizer = TieredSummarizer()
        except ImportError:
            logger.debug("TieredSummarizer not available for WindowCollector")
        return self._lazy_summarizer

    @staticmethod
    def _infer_content_type(item: TranscriptEvent) -> str:
        """Infer content type for summarization strategy selection."""
        if item.kind in ("user_turn", "assistant_turn"):
            return "dialogue"
        content = item.content.strip()
        lower = content.lower()
        # Error / log detection (tool results, stderr, etc.)
        if any(kw in lower for kw in ("error", "exception", "traceback", "failed", "stderr", "warning", "fatal")):
            return "log"
        # JSON detection
        if (content.startswith("{") and content.endswith("}")) or (content.startswith("[") and content.endswith("]")):
            return "json"
        # Code detection: heuristic based on common syntactic markers
        code_markers = (
            "def ",
            "class ",
            "import ",
            "from ",
            "function",
            "const ",
            "let ",
            "# ",
            "// ",
            "/*",
            "*/",
            "::",
            "->",
            "=>",
            "self.",
            "__init__",
            "print(",
            "console.log",
            "```",
        )
        if any(m in content for m in code_markers):
            return "code"
        return "text"

    def process(
        self,
        budget_out: BudgetPlannerOutput,
        patcher_out: StatePatcherOutput,
        canon_out: CanonicalizerOutput,
        inp: PipelineInput,
        decision_log: Any | None = None,  # ContextDecisionLog
    ) -> WindowCollectorOutput:
        transcript = canon_out.transcript
        working_state = patcher_out.working_state
        budget_plan = budget_out.budget_plan
        recent_window_messages = inp.recent_window_messages

        if not transcript:
            return WindowCollectorOutput(active_window=())

        # Import decision log types if logging is enabled
        _log_decisions = decision_log is not None
        if _log_decisions:
            from polaris.kernelone.context.context_os.decision_log import (
                ContextDecisionType,
                ReasonCode,
                create_decision,
            )

        min_recent_floor = max(1, int(self._policy.min_recent_messages_pinned or 1))
        min_recent_floor = min(self._policy.max_active_window_messages, min_recent_floor)
        recent_limit = max(min_recent_floor, int(recent_window_messages or 1))
        recent_limit = max(1, min(self._policy.max_active_window_messages, recent_limit))
        recent_candidates = list(transcript[-recent_limit:])
        forced_recent_ids = {item.event_id for item in transcript[-min_recent_floor:]}
        pinned_sequences: set[int] = {item.sequence for item in recent_candidates}

        if working_state.task_state.current_goal is not None:
            pinned_sequences.update(self._sequences_from_turns(working_state.task_state.current_goal.source_turns))

        for collection in (
            working_state.task_state.accepted_plan,
            working_state.task_state.open_loops,
            working_state.task_state.blocked_on,
            working_state.task_state.deliverables,
            working_state.active_entities,
        ):
            for entry in collection:
                pinned_sequences.update(self._sequences_from_turns(entry.source_turns))

        active_artifact_ids = set(working_state.active_artifacts)
        pinned_events: dict[str, TranscriptEvent] = {}
        active_window_ratio = getattr(self._policy, "active_window_budget_ratio", 0.45)
        token_budget = max(512, min(budget_plan.soft_limit, int(budget_plan.input_budget * active_window_ratio)))
        token_count = 0

        for item in reversed(transcript):
            if item.route == RoutingClass.CLEAR and item.event_id not in forced_recent_ids:
                # Log: excluded due to CLEAR route
                if _log_decisions:
                    decision_log.record(
                        create_decision(
                            decision_type=ContextDecisionType.EXCLUDE,
                            target_event_id=item.event_id,
                            reason="route_cleared",
                            reason_codes=(ReasonCode.ROUTE_CLEARED,),
                            token_budget_before=token_budget,
                            token_budget_after=token_budget,
                            explanation="Event excluded because route is CLEAR and not in forced recent IDs.",
                        )
                    )
                continue

            is_reopened = bool(str(get_metadata_value(item.metadata, "reopen_hold") or "").strip())
            is_root = (
                item.sequence in pinned_sequences
                or (item.artifact_id in active_artifact_ids)
                or is_reopened
                or item.event_id in forced_recent_ids
            )

            # Determine reason codes for root status
            root_reasons: list[ReasonCode] = []
            if item.sequence in pinned_sequences:
                root_reasons.append(ReasonCode.PINNED_BY_SYSTEM)
            if item.artifact_id in active_artifact_ids:
                root_reasons.append(ReasonCode.ACTIVE_ARTIFACT)
            if is_reopened:
                root_reasons.append(ReasonCode.OPEN_LOOP_REFERENCE)
            if item.event_id in forced_recent_ids:
                root_reasons.append(ReasonCode.FORCED_RECENT)

            can_add = is_root or len(pinned_events) < self._policy.max_active_window_messages
            if not can_add:
                # Log: excluded due to max window messages reached
                if _log_decisions:
                    decision_log.record(
                        create_decision(
                            decision_type=ContextDecisionType.EXCLUDE,
                            target_event_id=item.event_id,
                            reason="max_window_messages_reached",
                            reason_codes=(ReasonCode.NOT_IN_ACTIVE_WINDOW,),
                            token_budget_before=token_budget,
                            token_budget_after=token_budget,
                            explanation=f"Non-root event excluded because max_active_window_messages ({self._policy.max_active_window_messages}) reached.",
                        )
                    )
                continue

            item_content = item.content
            estimated = _estimate_tokens(item_content)

            compressed_via_jit = False
            compression_reason: ReasonCode | None = None
            if token_count + estimated > token_budget and is_root:
                remaining_budget = token_budget - token_count
                remaining_chars = max(512, remaining_budget * 4)
                if remaining_chars < len(item_content):
                    summarizer = self._get_summarizer()
                    # Tier 1: JIT semantic compression (content substantial + summarizer ready)
                    if summarizer is not None and len(item_content) > 300:
                        try:
                            content_type = self._infer_content_type(item)
                            target_tokens = max(50, remaining_budget)
                            item_content = summarizer.summarize(
                                item_content,
                                max_tokens=target_tokens,
                                content_type=content_type,
                            )
                            compressed_via_jit = True
                            compression_reason = ReasonCode.JIT_SEMANTIC_COMPRESSION
                            logger.info(
                                "JIT semantic compression: event_id=%s "
                                "original=%d chars → compressed=%d chars "
                                "(type=%s, target_tokens=%d)",
                                item.event_id,
                                len(item.content),
                                len(item_content),
                                content_type,
                                target_tokens,
                            )
                        except (
                            RuntimeError,
                            ValueError,
                            TimeoutError,
                        ):
                            logger.debug("JIT summarization failed, fallback to trim", exc_info=True)

                    # Tier 2: brute-force truncation (fallback or short content)
                    if not compressed_via_jit:
                        item_content = _trim_text(item_content, max_chars=remaining_chars)
                        truncated_tokens = _estimate_tokens(item_content)
                        while truncated_tokens > remaining_budget and remaining_chars > 128:
                            remaining_chars = int(remaining_chars * 0.8)
                            item_content = _trim_text(item_content, max_chars=remaining_chars)
                            truncated_tokens = _estimate_tokens(item_content)
                        compression_reason = ReasonCode.BRUTE_FORCE_TRUNCATION
                        logger.warning(
                            "Root event content truncated due to token budget: event_id=%s, "
                            "original_tokens=%d, truncated_to=%d, token_budget=%d",
                            item.event_id,
                            estimated,
                            truncated_tokens,
                            token_budget,
                        )
                estimated = _estimate_tokens(item_content)

            if token_count + estimated > token_budget:
                if is_root:
                    logger.warning(
                        "Token budget exceeded for root event (after compression): event_id=%s, "
                        "sequence=%d, estimated_tokens=%d, current_tokens=%d, token_budget=%d",
                        item.event_id,
                        item.sequence,
                        estimated,
                        token_count,
                        token_budget,
                    )
                    # Log: root event still over budget after compression
                    if _log_decisions:
                        decision_log.record(
                            create_decision(
                                decision_type=ContextDecisionType.INCLUDE_FULL,
                                target_event_id=item.event_id,
                                reason="root_over_budget",
                                reason_codes=tuple(root_reasons),
                                token_budget_before=token_budget,
                                token_budget_after=token_budget,
                                token_cost=estimated,
                                explanation="Root event included despite exceeding budget (after compression).",
                            )
                        )
                else:
                    logger.debug(
                        "Skipping non-root event due to token budget: event_id=%s, sequence=%d, "
                        "estimated_tokens=%d, current_tokens=%d, token_budget=%d",
                        item.event_id,
                        item.sequence,
                        estimated,
                        token_count,
                        token_budget,
                    )
                    # Log: excluded due to token budget
                    if _log_decisions:
                        decision_log.record(
                            create_decision(
                                decision_type=ContextDecisionType.EXCLUDE,
                                target_event_id=item.event_id,
                                reason="token_budget_exceeded",
                                reason_codes=(ReasonCode.TOKEN_BUDGET_EXCEEDED,),
                                token_budget_before=token_budget,
                                token_budget_after=token_budget,
                                token_cost=estimated,
                                explanation="Non-root event excluded because token budget exceeded.",
                            )
                        )
                    continue

            if item.event_id in pinned_events:
                continue

            # Apply content mutation + optional JIT compression metadata
            if item_content != item.content or compressed_via_jit:
                metadata_updates: dict[str, Any] = {}
                if compressed_via_jit:
                    metadata_updates["jit_compressed"] = True
                    metadata_updates["compression_strategy"] = "tiered_slm"
                pinned_item = validated_replace(
                    item,
                    content=item_content,
                    metadata={**dict(item.metadata), **metadata_updates},
                )
            else:
                pinned_item = item
            pinned_events[item.event_id] = pinned_item
            token_count += estimated

            # Log: included event
            if _log_decisions:
                decision_type = ContextDecisionType.INCLUDE_FULL
                if compressed_via_jit or compression_reason == ReasonCode.BRUTE_FORCE_TRUNCATION:
                    decision_type = ContextDecisionType.COMPRESS

                decision_log.record(
                    create_decision(
                        decision_type=decision_type,
                        target_event_id=item.event_id,
                        reason="included_in_active_window",
                        reason_codes=tuple(root_reasons) if root_reasons else (ReasonCode.NOT_IN_ACTIVE_WINDOW,),
                        token_budget_before=token_budget,
                        token_budget_after=token_budget,
                        token_cost=estimated,
                        content_source=item.kind or "unknown",
                        resolution_used="full" if not compressed_via_jit else "compressed",
                        explanation=f"Event included in active window. Root={is_root}, Compressed={compressed_via_jit}.",
                    )
                )

        active_window = tuple(sorted(pinned_events.values(), key=lambda item: (item.sequence, item.event_id)))
        return WindowCollectorOutput(active_window=active_window)

    @staticmethod
    def _sequences_from_turns(turns: tuple[str, ...]) -> set[int]:
        result: set[int] = set()
        for turn in turns:
            token = str(turn).strip().lower()
            if token.startswith("t") and token[1:].isdigit():
                result.add(int(token[1:]))
        return result


# ---------------------------------------------------------------------------
# Stage 6: EpisodeSealer
# ---------------------------------------------------------------------------


class EpisodeSealer:
    """Stage 6: Seal closed episodes based on active window."""

    def __init__(self, policy: StateFirstContextOSPolicy, domain_adapter: ContextDomainAdapter | None = None) -> None:
        self._policy = policy
        self._domain_adapter = domain_adapter

    def process(
        self,
        window_out: WindowCollectorOutput,
        patcher_out: StatePatcherOutput,
        canon_out: CanonicalizerOutput,
        inp: PipelineInput,
    ) -> EpisodeSealerOutput:
        transcript = canon_out.transcript
        active_window = window_out.active_window
        artifacts = canon_out.artifacts
        working_state = patcher_out.working_state
        existing_episodes = inp.existing_snapshot_episodes
        resolved_followup = canon_out.resolved_followup

        active_ids = {item.event_id for item in active_window}
        last_sealed_sequence = max(
            (item.to_sequence for item in existing_episodes if item.status == "sealed"),
            default=-1,
        )
        closed_events = tuple(
            item
            for item in transcript
            if item.route != RoutingClass.CLEAR
            and item.sequence > last_sealed_sequence
            and item.event_id not in active_ids
            and not str(get_metadata_value(item.metadata, "reopen_hold") or "").strip()
        )

        # Seal Guard: block sealing if pending follow-up exists
        if (
            self._policy.enable_seal_guard
            and self._policy.prevent_seal_on_pending
            and resolved_followup
            and resolved_followup.status == "pending"
        ):
            emit_debug_event(
                category="attention",
                label="seal_blocked",
                source="context_os.pipeline.episode_sealer",
                payload={
                    "reason": "pending_followup_unresolved",
                    "pending_action": resolved_followup.action,
                    "pending_status": resolved_followup.status,
                },
            )
            return EpisodeSealerOutput(episode_store=existing_episodes)

        if self._domain_adapter is None or not self._domain_adapter.should_seal_episode(
            closed_events=closed_events,
            active_window=active_window,
            working_state=working_state,
        ):
            return EpisodeSealerOutput(episode_store=existing_episodes)

        if not closed_events:
            return EpisodeSealerOutput(episode_store=existing_episodes)

        artifact_ids = tuple(
            item.artifact_id
            for item in closed_events
            if item.artifact_id and any(artifact.artifact_id == item.artifact_id for artifact in artifacts)
        )
        combined = "\n".join(item.content for item in closed_events)
        intent = (
            working_state.task_state.current_goal.value
            if working_state.task_state.current_goal is not None
            else _trim_text(closed_events[0].content, max_chars=96)
        )
        outcome = (
            working_state.decision_log[-1].summary
            if working_state.decision_log
            else _trim_text(closed_events[-1].content, max_chars=160)
        )

        episode = EpisodeCard(
            episode_id=f"ep_{len(existing_episodes) + 1}",
            from_sequence=closed_events[0].sequence,
            to_sequence=closed_events[-1].sequence,
            intent=intent,
            outcome=outcome,
            decisions=tuple(item.summary for item in working_state.decision_log[-self._policy.max_decisions :]),
            facts=tuple(
                item.value for item in working_state.user_profile.persistent_facts[-self._policy.max_stable_facts :]
            ),
            artifact_refs=tuple(dict.fromkeys(artifact_ids)),
            entities=tuple(item.value for item in working_state.active_entities[-self._policy.max_stable_facts :]),
            reopen_conditions=tuple(
                item.value for item in working_state.task_state.open_loops[-self._policy.max_open_loops :]
            ),
            source_spans=(f"t{closed_events[0].sequence}:t{closed_events[-1].sequence}",),
            digest_64=_trim_text(combined, max_chars=64),
            digest_256=_trim_text(combined, max_chars=256),
            digest_1k=_trim_text(combined, max_chars=1000),
            sealed_at=time.time(),
            status="sealed",
        )

        self._notify_episode_sealed(episode)
        return EpisodeSealerOutput(episode_store=(*tuple(existing_episodes), episode))

    def _notify_episode_sealed(self, episode: EpisodeCard) -> None:
        if self._domain_adapter is None:
            return
        notify = getattr(self._domain_adapter, "on_episode_sealed", None)
        if callable(notify):
            notify(episode)


# ---------------------------------------------------------------------------
# Stage 7: ArtifactSelector
# ---------------------------------------------------------------------------


class ArtifactSelector:
    """Stage 7: Select artifacts and episodes for prompt injection."""

    def __init__(self, policy: StateFirstContextOSPolicy) -> None:
        self._policy = policy

    def process(
        self,
        episode_out: EpisodeSealerOutput,
        patcher_out: StatePatcherOutput,
        window_out: WindowCollectorOutput,
        budget_out: BudgetPlannerOutput,
        canon_out: CanonicalizerOutput,
        inp: PipelineInput,
    ) -> ArtifactSelectorOutput:
        artifacts = canon_out.artifacts
        working_state = patcher_out.working_state
        episode_store = episode_out.episode_store
        active_window = window_out.active_window
        budget_plan = budget_out.budget_plan
        focus = inp.focus

        artifact_stubs = self._select_artifacts(artifacts, working_state)
        episode_cards = self._select_episodes(episode_store, working_state, focus)
        head_anchor = self._build_head_anchor(working_state, artifact_stubs, episode_cards)
        tail_anchor = self._build_tail_anchor(active_window, working_state)
        run_card = self._build_run_card(
            working_state=working_state,
            transcript=canon_out.transcript,
            pending_followup=canon_out.resolved_followup,
        )
        context_slice_plan = self._build_context_slice_plan(
            patcher_out.working_state,
            active_window,
            artifact_stubs,
            episode_cards,
            budget_plan,
        )

        return ArtifactSelectorOutput(
            artifact_stubs=artifact_stubs,
            episode_cards=episode_cards,
            head_anchor=head_anchor,
            tail_anchor=tail_anchor,
            run_card=run_card,
            context_slice_plan=context_slice_plan,
        )

    def _select_artifacts(
        self,
        artifacts: tuple[ArtifactRecord, ...],
        working_state: WorkingState,
    ) -> tuple[ArtifactRecord, ...]:
        if not artifacts:
            return ()

        active_ids = set(working_state.active_artifacts)
        ordered: list[ArtifactRecord] = []
        seen: set[str] = set()

        # First add active artifacts (always include)
        for artifact_id in active_ids:
            artifact = next((item for item in artifacts if item.artifact_id == artifact_id), None)
            if artifact is None or artifact.artifact_id in seen:
                continue
            seen.add(artifact.artifact_id)
            if len(artifact.content) > MAX_INLINE_CHARS:
                stub_content = (
                    artifact.content[:MAX_STUB_CHARS] + f"\n...[truncated, full content at {artifact.artifact_id}]..."
                )
                artifact = validated_replace(
                    artifact,
                    content=stub_content,
                    metadata={
                        **dict(artifact.metadata or ()),
                        "truncated": True,
                        "full_id": artifact.artifact_id,
                    },
                )
            ordered.append(artifact)

        # Add remaining artifacts up to max limit
        for artifact in reversed(artifacts):
            if len(ordered) >= self._policy.max_artifact_stubs:
                break
            if artifact.artifact_id in seen:
                continue
            seen.add(artifact.artifact_id)
            if len(artifact.content) > MAX_INLINE_CHARS:
                stub_content = (
                    artifact.content[:MAX_STUB_CHARS] + f"\n...[truncated, full content at {artifact.artifact_id}]..."
                )
                artifact = validated_replace(
                    artifact,
                    content=stub_content,
                    metadata={
                        **dict(artifact.metadata or ()),
                        "truncated": True,
                        "full_id": artifact.artifact_id,
                    },
                )
            ordered.append(artifact)

        return tuple(ordered[: self._policy.max_artifact_stubs])

    def _select_episodes(
        self,
        episodes: tuple[Any, ...],
        working_state: WorkingState,
        focus: str,
    ) -> tuple[Any, ...]:
        if not episodes:
            return ()

        focus_terms = {
            token.lower() for token in re.findall(r"[A-Za-z0-9_.:/\\-]+", _normalize_text(focus)) if len(token) >= 2
        }
        ranked: list[tuple[float, Any]] = []

        max_seq = max((ep.to_sequence for ep in episodes), default=1)

        for episode in episodes:
            if episode.status != "sealed":
                continue
            text = " ".join((episode.intent, episode.outcome, episode.digest_256)).lower()
            lexical = 0.0
            if focus_terms:
                lexical = sum(1 for term in focus_terms if term in text) / max(1, len(focus_terms))
            recency = max(0.0, episode.to_sequence / max(1, max_seq))
            open_loop_bonus = (
                0.25 if any(loop.value.lower() in text for loop in working_state.task_state.open_loops) else 0.0
            )
            ranked.append((lexical + recency + open_loop_bonus, episode))

        ranked.sort(key=lambda item: (item[0], item[1].to_sequence), reverse=True)
        return tuple(item[1] for item in ranked[: self._policy.max_episode_cards])

    def _build_head_anchor(
        self,
        working_state: WorkingState,
        artifact_stubs: tuple[ArtifactRecord, ...],
        episode_cards: tuple[Any, ...],
    ) -> str:
        lines: list[str] = []
        goal = working_state.task_state.current_goal.value if working_state.task_state.current_goal is not None else ""
        if goal:
            lines.append(f"Current goal: {goal}")
        loops = [item.value for item in working_state.task_state.open_loops[-self._policy.max_open_loops :]]
        if loops:
            lines.append("Open loops: " + "; ".join(loops))
        blocked = [item.value for item in working_state.task_state.blocked_on[: self._policy.max_open_loops]]
        if blocked:
            lines.append("Blocked on: " + "; ".join(blocked))
        decisions = [item.summary for item in working_state.decision_log[: self._policy.max_decisions]]
        if decisions:
            lines.append("Recent decisions: " + "; ".join(decisions))
        entities = [item.value for item in working_state.active_entities[: self._policy.max_stable_facts]]
        if entities:
            lines.append("Active entities: " + "; ".join(entities))
        if artifact_stubs:
            lines.append(
                "Artifacts: "
                + "; ".join(f"{item.artifact_id}<{item.artifact_type}> {item.peek}" for item in artifact_stubs)
            )
        if episode_cards:
            lines.append("Recent episodes: " + "; ".join(item.digest_64 for item in episode_cards))
        return "\n".join(lines).strip()

    def _build_tail_anchor(
        self,
        active_window: tuple[Any, ...],
        working_state: WorkingState,
    ) -> str:
        if not active_window:
            return ""
        last_event = active_window[-1]
        parts = [f"Last event: {last_event.role} -> {_trim_text(last_event.content, max_chars=180)}"]
        if working_state.task_state.open_loops:
            parts.append(f"Next focus: {working_state.task_state.open_loops[-1].value}")
        elif working_state.task_state.deliverables:
            parts.append(f"Next deliverable: {working_state.task_state.deliverables[0].value}")
        return "\n".join(parts).strip()

    def _build_run_card(
        self,
        *,
        working_state: WorkingState,
        transcript: tuple[Any, ...] | None = None,
        pending_followup: Any | None = None,
    ) -> Any:
        current_goal = (
            working_state.task_state.current_goal.value if working_state.task_state.current_goal is not None else ""
        )
        open_loops = tuple(item.value for item in working_state.task_state.open_loops[-self._policy.max_open_loops :])
        active_entities = tuple(item.value for item in working_state.active_entities[: self._policy.max_stable_facts])
        recent_decisions = tuple(item.summary for item in working_state.decision_log[-self._policy.max_decisions :])
        next_action_hint = ""
        if open_loops:
            next_action_hint = open_loops[-1]
        elif working_state.task_state.deliverables:
            next_action_hint = working_state.task_state.deliverables[0].value

        latest_user_intent = ""
        last_turn_outcome = ""
        latest_user_event: Any | None = None
        if transcript:
            ordered_transcript = sorted(
                transcript,
                key=lambda item: (item.sequence, item.created_at, item.event_id),
            )
            for event in reversed(ordered_transcript):
                if event.role == "user":
                    latest_user_event = event
                    latest_user_intent = event.content
                    break
            for event in reversed(ordered_transcript):
                if event.role == "assistant":
                    last_turn_outcome = str(get_metadata_value(event.metadata, "dialog_act") or "assistant_response")
                    break
                if event.role == "tool":
                    last_turn_outcome = "tool_execution"
                    break
                if event.role == "user":
                    last_turn_outcome = str(get_metadata_value(event.metadata, "dialog_act") or DialogAct.UNKNOWN)
                    break

        visible_followup = pending_followup
        if visible_followup is not None and visible_followup.status != "pending":
            latest_resolved_now = bool(
                latest_user_event
                and str(get_metadata_value(latest_user_event.metadata, "followup_action") or "").strip()
            )
            if not latest_resolved_now:
                visible_followup = None

        return RunCard(
            current_goal=current_goal,
            hard_constraints=_extract_hard_constraints(working_state),
            open_loops=open_loops,
            active_entities=active_entities,
            active_artifacts=tuple(working_state.active_artifacts),
            recent_decisions=recent_decisions,
            next_action_hint=next_action_hint,
            latest_user_intent=latest_user_intent,
            pending_followup_action=visible_followup.action if visible_followup else "",
            pending_followup_status=visible_followup.status if visible_followup else "",
            last_turn_outcome=last_turn_outcome,
        )

    def _build_context_slice_plan(
        self,
        working_state: WorkingState,
        active_window: tuple[Any, ...],
        artifact_stubs: tuple[ArtifactRecord, ...],
        episode_cards: tuple[Any, ...],
        budget_plan: Any,
    ) -> Any:
        included: list[ContextSliceSelection] = []
        excluded: list[ContextSliceSelection] = []
        roots = [
            "latest_user_turn",
            "current_goal",
            "open_loops",
        ]
        if _extract_hard_constraints(working_state):
            roots.append("hard_constraints")
        if artifact_stubs:
            roots.append("active_artifacts")

        if working_state.task_state.current_goal is not None:
            included.append(
                ContextSliceSelection(
                    selection_type="state",
                    ref="task_state.current_goal",
                    reason="root",
                )
            )
        for open_loop_entry in working_state.task_state.open_loops[-self._policy.max_open_loops :]:
            included.append(
                ContextSliceSelection(
                    selection_type="state",
                    ref=open_loop_entry.path,
                    reason="open_loop",
                )
            )
        for artifact_stub in artifact_stubs:
            included.append(
                ContextSliceSelection(
                    selection_type="artifact",
                    ref=artifact_stub.artifact_id,
                    reason="active_artifact"
                    if artifact_stub.artifact_id in working_state.active_artifacts
                    else "recent_artifact",
                )
            )
        for episode_card in episode_cards:
            included.append(
                ContextSliceSelection(
                    selection_type="episode",
                    ref=episode_card.episode_id,
                    reason="episode_recall",
                )
            )
        for window_event in active_window:
            reason = "recent_window"
            max_seq = max((event.sequence for event in active_window), default=window_event.sequence)
            if window_event.sequence == max_seq:
                reason = "latest_turn"
            elif (
                working_state.task_state.current_goal is not None
                and window_event.sequence
                in WindowCollector._sequences_from_turns(working_state.task_state.current_goal.source_turns)
            ):
                reason = "goal_root"
            included.append(
                ContextSliceSelection(
                    selection_type="event",
                    ref=window_event.event_id,
                    reason=reason,
                )
            )

        included_refs = {item.ref for item in included}
        # We don't have access to full transcript here - return plan with what we have
        # The full excluded list is built in runtime
        pressure_level = "normal"
        if budget_plan.expected_next_input_tokens >= budget_plan.emergency_limit:
            pressure_level = "emergency"
        elif budget_plan.expected_next_input_tokens >= budget_plan.hard_limit:
            pressure_level = "hard"
        elif budget_plan.expected_next_input_tokens >= budget_plan.soft_limit:
            pressure_level = "soft"

        return ContextSlicePlan(
            plan_id=f"slice_{hashlib.sha256('|'.join(included_refs or {'empty'}).encode('utf-8')).hexdigest()[:10]}",
            budget_tokens=budget_plan.input_budget,
            roots=tuple(roots),
            included=tuple(included),
            excluded=tuple(excluded[: max(12, self._policy.max_active_window_messages)]),
            pressure_level=pressure_level,
        )
