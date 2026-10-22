"""Request-local QA observations derived from the existing engineering journal."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

QA_WORKING_STATE_EVENT_KINDS = (
    "run_updated",
    "role_started",
    "tool_started",
    "tool_observed",
    "verification_completed",
)


def qa_working_state_message(
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    frame_seq: int,
    observed_at: float,
    deadline: float | None,
) -> dict[str, Any]:
    """Describe recorded facts only; do not rank, deny, repeat or decide actions.

    The caller holds the existing role/workspace ownership. No derived state is
    persisted. Counts use paired observations after the exact QA frame and the
    last journal revision transition; absent binding is UNKNOWN, never success.
    """
    revision, candidate_start = (0, 0)
    frame_known = False
    verification_seq = None
    for event in events:
        payload = event["payload"]
        if event["kind"] == "run_updated" and type(payload.get("workspace_revision")) is int:
            if payload["workspace_revision"] != revision:
                candidate_start = event["seq"]
            revision = payload["workspace_revision"]
        if event["seq"] == frame_seq and event["kind"] == "role_started" and (event["role"] == "qa"):
            frame_known = True
        if event["kind"] == "verification_completed" and payload.get("workspace_revision") == run["workspace_revision"]:
            verification_seq = event["seq"]
    binding_known = frame_known and revision == run["workspace_revision"]
    cutoff = max(frame_seq, candidate_start)
    dispatches: dict[tuple[int, str], Mapping[str, Any]] = {}
    observed: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    if binding_known:
        for event in events:
            if event["seq"] <= cutoff or event["role"] != "qa":
                continue
            payload = event["payload"]
            if event["kind"] not in {"tool_started", "tool_observed"}:
                continue
            key = (payload["turn_seq"], payload["call_id"])
            if event["kind"] == "tool_started":
                dispatches[key] = event
            elif key in dispatches:
                observed.append((dispatches[key], event))

    def succeeded(event: Mapping[str, Any]) -> bool:
        payload = event["payload"]
        return (
            payload.get("ok") is True
            and payload.get("interrupted") is False
            and (payload.get("timed_out", False) is False)
        )

    def visible(event: Mapping[str, Any]) -> dict[str, Any]:
        payload = event["payload"]
        return {
            "tool": payload["tool"],
            "ok": payload.get("ok"),
            "content": payload.get("content"),
            "exit_code": payload.get("exit_code"),
            "interrupted": payload.get("interrupted"),
            "timed_out": payload.get("timed_out", False),
        }

    def exact(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    last_probe = None
    if observed:
        dispatch, last = observed[-1]
        result = visible(last)
        arguments = dispatch["payload"]["arguments"]
        result_key, arguments_key = (exact(result), exact(arguments))
        visible_matches = [(d, e) for d, e in observed if exact(visible(e)) == result_key]
        last_probe = {
            "tool": result["tool"],
            "observation_seq": last["seq"],
            "exact_arguments_sha256": hashlib.sha256(arguments_key.encode("utf-8")).hexdigest(),
            "result_content_sha256": hashlib.sha256(result["content"].encode("utf-8")).hexdigest()
            if isinstance(result["content"], str)
            else None,
            "exact_same_arguments_and_result_count": sum(
                (exact(d["payload"]["arguments"]) == arguments_key for d, _ in visible_matches)
            ),
            "same_tool_and_visible_result_count": len(visible_matches),
        }
    successful = [event for _, event in observed if succeeded(event)]
    reports = [event for _, event in observed if event["payload"]["tool"] == "report_review"]
    closed = {(event["payload"]["turn_seq"], event["payload"]["call_id"]) for _, event in observed}
    facts = {
        "run_id": run["session_id"],
        "workspace": run["workspace"],
        "as_of_selected_event_seq": max((event["seq"] for event in events), default=0),
        "source_candidate": {
            "workspace_revision": run["workspace_revision"],
            "verification": run["verification"],
            "verification_event_seq": verification_seq,
            "candidate_binding_known": binding_known,
        },
        "qa_frame": {
            "started_seq": frame_seq,
            "successful_source_read_count": sum(
                e["payload"]["tool"] == "read_file"
                or (e["payload"]["tool"] == "repo_rg" and bool(str(e["payload"].get("content", "")).strip()))
                for e in successful
            )
            if binding_known
            else None,
            "source_read_count_scope": "Successful observed read_file or nonempty repo_rg in this QA frame and journal revision; command-based reads excluded.",
            "own_browser_capture_count": sum(
                (
                    succeeded(e)
                    and e["payload"]["tool"] == "browser_observe"
                    and bool(e["payload"].get("images"))
                    and ("revisit_call_ids" not in d["payload"]["arguments"])
                    for d, e in observed
                )
            )
            if binding_known
            else None,
            "report_review_count": len(reports) if binding_known else None,
            "successful_report_review_count": sum(succeeded(e) for e in reports) if binding_known else None,
            "unobserved_tool_dispatch_count": len(dispatches.keys() - closed) if binding_known else None,
        },
        "last_probe": last_probe,
        "visible_result_count_scope": "Same QA frame and journal revision; tool/ok/content/exit_code/interrupted/timed_out fields only; image equality not inferred. Arguments ignored; same visible result does not imply same command, effect identity or redundant purpose.",
        "original_deadline_epoch_seconds": deadline,
        "observed_at_epoch_seconds": observed_at,
        "approximate_remaining_seconds": max(0.0, deadline - observed_at) if deadline is not None else None,
        "timing_scope": "Observation at request preparation; remaining time may be lower when received. Unknown deadline remains null.",
        "authority": "Recorded observations only, not acceptance or instructions for the next decision; saved captures retain their original bindings.",
    }
    return {
        "role": "user",
        "content": "Host QA working-state observation (request preparation): "
        + json.dumps(facts, ensure_ascii=False, allow_nan=False),
    }
