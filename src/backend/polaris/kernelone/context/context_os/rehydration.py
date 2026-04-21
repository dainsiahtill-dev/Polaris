from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.kernelone.context.control_plane_noise import is_control_plane_noise


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            result.append(dict(item))
    return result


def _filter_state_entries(items: Any, *, value_key: str = "value") -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in _dict_list(items):
        value = item.get(value_key)
        if is_control_plane_noise(value):
            continue
        filtered.append(item)
    return filtered


def _sanitize_working_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    working_state = snapshot.get("working_state")
    if not isinstance(working_state, Mapping):
        return snapshot

    sanitized_working = dict(working_state)

    task_state = sanitized_working.get("task_state")
    if isinstance(task_state, Mapping):
        sanitized_task = dict(task_state)
        for field_name in ("accepted_plan", "open_loops", "blocked_on", "deliverables"):
            sanitized_task[field_name] = _filter_state_entries(sanitized_task.get(field_name))
        current_goal = sanitized_task.get("current_goal")
        if isinstance(current_goal, Mapping) and is_control_plane_noise(current_goal.get("value")):
            sanitized_task["current_goal"] = None
        sanitized_working["task_state"] = sanitized_task

    user_profile = sanitized_working.get("user_profile")
    if isinstance(user_profile, Mapping):
        sanitized_profile = dict(user_profile)
        for field_name in ("preferences", "style", "persistent_facts"):
            sanitized_profile[field_name] = _filter_state_entries(sanitized_profile.get(field_name))
        sanitized_working["user_profile"] = sanitized_profile

    sanitized_working["decision_log"] = _filter_state_entries(
        sanitized_working.get("decision_log"), value_key="summary"
    )
    sanitized_working["active_entities"] = _filter_state_entries(sanitized_working.get("active_entities"))
    sanitized_working["temporal_facts"] = _filter_state_entries(sanitized_working.get("temporal_facts"))
    sanitized_working["state_history"] = _filter_state_entries(sanitized_working.get("state_history"))
    snapshot["working_state"] = sanitized_working
    return snapshot


def _rehydrate_transcript_from_index(
    transcript_index: Sequence[Mapping[str, Any]],
    session_turn_events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not transcript_index or not session_turn_events:
        return []

    events_by_id: dict[str, dict[str, Any]] = {}
    events_by_role: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in session_turn_events:
        if not isinstance(event, Mapping):
            continue
        event_dict = dict(event)
        event_id = str(event_dict.get("event_id") or "").strip()
        role = str(event_dict.get("role") or "").strip().lower()
        if event_id:
            events_by_id[event_id] = event_dict
        if role:
            events_by_role[role].append(event_dict)

    restored: list[dict[str, Any]] = []
    consumed_ids: set[str] = set()
    for item in transcript_index:
        if not isinstance(item, Mapping):
            continue
        event_id = str(item.get("event_id") or "").strip()
        role = str(item.get("role") or "").strip().lower()
        matched_event: dict[str, Any] | None = events_by_id.get(event_id)
        if matched_event is None and role:
            role_bucket = events_by_role.get(role) or []
            while role_bucket:
                candidate = role_bucket.pop(0)
                candidate_id = str(candidate.get("event_id") or "").strip()
                if candidate_id and candidate_id in consumed_ids:
                    continue
                matched_event = candidate
                break
        if matched_event is None:
            continue
        candidate_id = str(matched_event.get("event_id") or "").strip()
        if candidate_id:
            consumed_ids.add(candidate_id)
        restored.append(dict(matched_event))
    return restored


def rehydrate_persisted_context_os_payload(
    payload: Mapping[str, Any] | None,
    *,
    session_turn_events: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None

    snapshot = _sanitize_working_state(dict(payload))
    transcript = snapshot.get("transcript_log")
    if isinstance(transcript, list) and transcript:
        return snapshot

    transcript_index = snapshot.get("transcript_log_index")
    if not isinstance(transcript_index, Sequence) or isinstance(transcript_index, (str, bytes)):
        return snapshot

    restored = _rehydrate_transcript_from_index(
        [item for item in transcript_index if isinstance(item, Mapping)],
        [item for item in (session_turn_events or ()) if isinstance(item, Mapping)],
    )
    if restored:
        snapshot["transcript_log"] = restored
    return snapshot


__all__ = ["rehydrate_persisted_context_os_payload"]
