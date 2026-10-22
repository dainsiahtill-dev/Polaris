"""Derive native image input from the existing durable tool observations."""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_verification import CandidateObservationUnavailableError
from polaris.cells.roles.runtime.public.contracts.engineering_run import ImageObservation


def _visual_candidate_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    """A read-only verification receipt does not change the rendered candidate."""
    return {key: value for key, value in binding.items() if key != "verification_seq"}


def _complete_candidate_binding(binding: Mapping[str, Any]) -> bool:
    """Only the complete existing host review binding can prove obsolescence."""
    return (
        type(binding.get("workspace_revision")) is int
        and isinstance(binding.get("required_checks"), (list, tuple))
        and all(isinstance(binding.get(key), dict) and binding[key] for key in ("scope", "profile", "inputs"))
    )


def _visual_source_projection(
    message: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    events: Sequence[Mapping[str, Any]],
    role: str | None,
    current_binding: Mapping[str, Any] | None,
    pinned_call_ids: frozenset[str],
) -> dict[str, Any]:
    """Join exact durable facts; missing or ambiguous attribution retains pixels."""
    identity = message.get("tool_call_id")
    result: dict[str, Any] = {"call_id": identity, "status": "retained", "reason": "unattributed"}
    if identity in pinned_call_ids:
        return {**result, "reason": "explicitly_pinned"}
    if role is None or current_binding is None or not _complete_candidate_binding(current_binding):
        return {**result, "reason": "current_binding_unavailable"}
    scoped = [event for event in events if event.get("role") == role]
    messages = [
        event for event in scoped if event.get("kind") == "message" and event["payload"].get("message") == message
    ]
    if not messages:
        return result
    observed = [
        event
        for event in scoped
        if event.get("kind") == "tool_observed"
        and {key: value for key, value in event["payload"].items() if key != "turn_seq"} == observation
        and event["payload"].get("call_id") == identity
    ]
    if len(observed) != 1 or any(observed[0]["seq"] >= message_event["seq"] for message_event in messages):
        return result
    actual = observed[0]
    turn = actual["payload"].get("turn_seq")
    dispatched = [
        event
        for event in scoped
        if event.get("kind") == "tool_started"
        and event["seq"] < actual["seq"]
        and event["payload"].get("turn_seq") == turn
        and event["payload"].get("call_id") == identity
        and event["payload"].get("tool") == "browser_observe"
    ]
    if len(dispatched) != 1:
        return result
    captures = [
        event
        for event in scoped
        if event.get("kind") == "visual_observation_completed"
        and dispatched[0]["seq"] < event["seq"] < actual["seq"]
        and event["payload"].get("requested_by") == [turn, identity]
        and event["payload"].get("ok") is True
        and event["payload"].get("image_sha256") == [image["sha256"] for image in observation["images"]]
    ]
    if len(captures) != 1:
        return result
    capture = captures[0]
    binding = capture["payload"].get("input_binding")
    if not isinstance(binding, dict) or not _complete_candidate_binding(binding):
        return {**result, "reason": "capture_binding_unavailable"}
    previous, current = _visual_candidate_binding(binding), _visual_candidate_binding(current_binding)
    if previous.keys() != current.keys():
        return {**result, "reason": "binding_fields_incomparable"}
    result.update(turn_seq=turn, visual_observation_seq=capture["seq"])
    if previous == current:
        return {**result, "reason": "current_candidate"}
    return {
        **result,
        "status": "pixels_omitted",
        "reason": "stale_candidate",
        "changed_binding_fields": sorted(key for key in current if current[key] != previous[key]),
    }


def build_visual_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    events: Sequence[Mapping[str, Any]] = (),
    role: str | None = None,
    current_binding: Mapping[str, Any] | None = None,
    binding_provider: Callable[[], Mapping[str, Any]] | None = None,
    pinned_call_ids: frozenset[str] = frozenset(),
    audit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Keep durable facts intact; attach images after every complete tool batch.

    Only the host attachment field of a successful browser observation is an
    image source. Quoted file contents and arbitrary model text remain text.
    All tool results precede derived images. A complete authoritative journal
    binding may omit provably obsolete browser pixels, never execution facts.
    Original user/reference blocks and unattributed or pinned captures survive.
    Pins are an explicit host input; historical comparison retrieval is not
    inferred from model text. Without owner context, retain the original view.
    Acquire optional host observations only when browser pixels actually need
    selection. An unavailable observation preserves pixels, never approval.
    """
    result: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    visual_call_ids: set[str] = set()
    projection: dict[str, Any] = {
        "retained_image_count": 0,
        "omitted_image_count": 0,
        "observations": [],
        "binding_observation": {"status": "provided" if current_binding is not None else "not_requested"},
    }
    binding_attempted = current_binding is not None

    def flush() -> None:
        if attachments:
            result.append({"role": "user", "content": list(attachments)})
            attachments.clear()

    for original in messages:
        message = copy.deepcopy(dict(original))
        if message.get("role") != "tool":
            flush()
            result.append(message)
            continue
        content = message.get("content")
        try:
            observation = json.loads(content) if isinstance(content, str) else None
        except ValueError:
            observation = None
        if (
            isinstance(observation, dict)
            and observation.get("tool") == "browser_observe"
            and observation.get("ok") is True
            and not observation.get("interrupted")
            and observation.get("images")
        ):
            images = observation["images"]
            if not isinstance(images, list):
                raise ValueError("invalid durable browser images")
            # Validate every original, including omitted bytes. Obsolescence
            # cannot hide a corrupt durable attachment from the existing guard.
            validated = [ImageObservation(**raw) for raw in images]
            if not binding_attempted and binding_provider is not None:
                binding_attempted = True
                try:
                    current_binding = binding_provider()
                except PermissionError:
                    # PermissionError is an OSError, but host authority failures
                    # must never be softened into optional input observation.
                    raise
                except (CandidateObservationUnavailableError, OSError) as error:
                    projection["binding_observation"] = {
                        "status": "unknown",
                        "reason": "candidate_observation_unavailable",
                        "error_type": type(error).__name__,
                        "detail": str(error),
                    }
                else:
                    projection["binding_observation"] = {"status": "observed"}
            if isinstance(message.get("tool_call_id"), str):
                visual_call_ids.add(message["tool_call_id"])
            selection = _visual_source_projection(
                original,
                observation,
                events=events,
                role=role,
                current_binding=current_binding,
                pinned_call_ids=pinned_call_ids,
            )
            omitted = selection["status"] == "pixels_omitted"
            projection["observations"].append({**selection, "image_sha256": [image.sha256 for image in validated]})
            projection["omitted_image_count" if omitted else "retained_image_count"] += len(validated)
            metadata = []
            for raw, image in zip(images, validated, strict=True):
                metadata.append({key: value for key, value in raw.items() if key != "data_base64"})
                if omitted:
                    continue
                attachments.extend(
                    [
                        {
                            "type": "text",
                            "text": f"Browser observation {message.get('tool_call_id')}: {image.label} "
                            f"({image.width}x{image.height}, sha256 {image.sha256}).",
                        },
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": image.media_type, "data": image.data_base64},
                        },
                    ]
                )
            observation["images"] = metadata
            if omitted:
                observation["visual_projection"] = selection
            message["content"] = json.dumps(observation, ensure_ascii=False)
        result.append(message)
    flush()
    if pinned_call_ids - visual_call_ids:
        raise ValueError("pinned visual input is unavailable in the supplied history")
    if audit is not None:
        audit.update(projection)
    return result


def require_current_visual_input(
    store: EngineeringSessionStore,
    session_id: str,
    *,
    role: str,
    frame_seq: int,
    turn_seq: int,
    current_binding: Mapping[str, Any],
) -> None:
    """Require this role's fresh capture in this turn's final native request.

    This reads the same SQL journal as execution and recovery. A completed
    capture alone is insufficient: its durable tool bytes must match, and a
    later model request must actually contain those bytes. A different role,
    an earlier candidate, and capture plus approval in one batch cannot pass.
    """
    # A later read-only check creates a receipt sequence without changing the
    # rendered candidate. Every actual input, scope and revision still binds.
    candidate = _visual_candidate_binding(current_binding)
    events = store.load_events(session_id)
    physical: Counter[str] = Counter()
    for event in events:
        payload = event["payload"]
        if (
            event["seq"] > frame_seq
            and event["role"] == role
            and event["kind"] == "llm_request_observed"
            and payload.get("turn_seq") == turn_seq
            and payload.get("phase") in {"prepared", "terminal"}
        ):
            audit = payload.get("audit", {})
            hashes = audit.get("image_sha256", []) if isinstance(audit, dict) else []
            if isinstance(hashes, list) and all(isinstance(value, str) for value in hashes):
                # Multiple phases describe the same request, not extra images.
                physical |= Counter(hashes)
    for event in reversed(events):
        payload = event["payload"]
        if not (
            event["seq"] > frame_seq
            and event["role"] == role
            and event["kind"] == "visual_observation_completed"
            and payload.get("ok") is True
            and isinstance(payload.get("input_binding"), dict)
            and _visual_candidate_binding(payload["input_binding"]) == candidate
        ):
            continue
        requested = payload.get("requested_by")
        hashes = payload.get("image_sha256")
        if (
            not isinstance(requested, list)
            or len(requested) != 2
            or type(requested[0]) is not int
            or requested[0] >= turn_seq
            or not isinstance(hashes, list)
            or not hashes
            or any(not isinstance(value, str) for value in hashes)
        ):
            continue
        for durable in events:
            actual = durable["payload"]
            if not (
                durable["seq"] > event["seq"]
                and durable["role"] == role
                and durable["kind"] == "tool_observed"
                and actual.get("tool") == "browser_observe"
                and actual.get("ok") is True
                and not actual.get("interrupted")
                and actual.get("turn_seq") == requested[0]
                and actual.get("call_id") == requested[1]
            ):
                continue
            try:
                images = [ImageObservation(**raw) for raw in actual.get("images", [])]
            except (TypeError, ValueError):
                continue
            actual_hashes = [image.sha256 for image in images]
            if actual_hashes == hashes and Counter(hashes) <= physical:
                return
    raise ValueError(
        "current visual evidence is required: use browser_observe on the current candidate, "
        "inspect the returned images in a later model turn, then complete or approve."
    )
