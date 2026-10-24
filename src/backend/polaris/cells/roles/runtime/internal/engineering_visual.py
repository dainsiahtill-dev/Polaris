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


def original_visual_capture(events: Sequence[Mapping[str, Any]], role: str, identity: str) -> dict[str, Any]:
    """Resolve one original same-role capture; reinspection never creates freshness."""
    scoped = [event for event in events if event.get("role") == role]
    observations = [
        event
        for event in scoped
        if event.get("kind") == "tool_observed" and event["payload"].get("call_id") == identity
    ]
    if len(observations) != 1:
        raise ValueError("original visual capture unavailable or ambiguous")
    actual = observations[0]
    value = actual["payload"]
    if value.get("tool") != "browser_observe" or value.get("ok") is not True or value.get("interrupted"):
        raise ValueError("original visual capture unavailable")
    images = [ImageObservation(**raw) for raw in value.get("images", [])]
    if not images:
        raise ValueError("original visual capture has no valid images")
    dispatched = [
        event
        for event in scoped
        if event.get("kind") == "tool_started"
        and event["seq"] < actual["seq"]
        and event["payload"].get("call_id") == identity
        and event["payload"].get("turn_seq") == value.get("turn_seq")
        and event["payload"].get("tool") == "browser_observe"
    ]
    captures = [
        event
        for event in scoped
        if event.get("kind") == "visual_observation_completed"
        and event["seq"] < actual["seq"]
        and event["payload"].get("ok") is True
        and event["payload"].get("requested_by") == [value.get("turn_seq"), identity]
        and event["payload"].get("image_sha256") == [image.sha256 for image in images]
    ]
    if len(dispatched) != 1 or len(captures) != 1 or dispatched[0]["seq"] >= captures[0]["seq"]:
        raise ValueError("original visual capture provenance unavailable")
    return {"observation": actual, "capture": captures[0], "dispatch": dispatched[0], "images": images}


def _delivered(events: Sequence[Mapping[str, Any]], role: str, hashes: list[str], after_turn: int) -> bool:
    """Auxiliary/prepared/failed/unknown sends cannot release mandatory pixels."""
    return any(
        event.get("kind") == "llm_request_observed"
        and event.get("role") == role
        and event["payload"].get("phase") == "terminal"
        and event["payload"].get("status") == "completed"
        and type(event["payload"].get("turn_seq")) is int
        and event["payload"]["turn_seq"] > after_turn
        and event["payload"].get("audit", {}).get("audit_scope") == "provider_native_wire"
        and Counter(hashes) <= Counter(event["payload"].get("audit", {}).get("image_sha256", []))
        for event in events
    )


def _pending_revisits(events: Sequence[Mapping[str, Any]], role: str | None) -> frozenset[str]:
    pending: set[str] = set()
    if role is None:
        return frozenset()
    for event in events:
        value = event["payload"]
        if (
            event.get("role") != role
            or event.get("kind") != "tool_observed"
            or value.get("tool") != "browser_observe"
            or value.get("ok") is not True
            or value.get("interrupted")
        ):
            continue
        try:
            content = json.loads(value.get("content", ""))
        except (ValueError, TypeError):
            continue
        identities = content.get("revisit_call_ids") if isinstance(content, dict) else None
        if not isinstance(identities, list) or not identities:
            continue
        dispatch = [
            item
            for item in events
            if item.get("role") == role
            and item.get("kind") == "tool_started"
            and item["seq"] < event["seq"]
            and item["payload"].get("call_id") == value.get("call_id")
            and item["payload"].get("turn_seq") == value.get("turn_seq")
            and item["payload"].get("arguments") == {"revisit_call_ids": identities}
        ]
        if len(dispatch) != 1:
            continue
        for identity in identities:
            original = original_visual_capture(events, role, identity)
            if not _delivered(events, role, [image.sha256 for image in original["images"]], value["turn_seq"]):
                pending.add(identity)
    return frozenset(pending)


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
    pinned = identity in pinned_call_ids
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
    result.update(
        turn_seq=turn,
        visual_observation_seq=capture["seq"],
        capture_arguments=dispatched[0]["payload"].get("arguments", {}),
        frame_count=len(observation["images"]),
    )
    if previous == current:
        return {**result, "reason": "current_candidate"}
    if pinned:
        return {**result, "reason": "explicitly_pinned"}
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
    pending = _pending_revisits(events, role)
    pinned_call_ids |= pending
    attachment_sets: list[dict[str, Any]] = []
    visual_call_ids: set[str] = set()
    projection: dict[str, Any] = {
        "retained_image_count": 0,
        "omitted_image_count": 0,
        "observations": [],
        "image_sets": [],
        "binding_observation": {"status": "provided" if current_binding is not None else "not_requested"},
    }
    binding_attempted = current_binding is not None

    def flush() -> None:
        if attachments:
            for item in attachment_sets:
                item["message_index"] = len(result)
                projection["image_sets"].append(item)
            result.append({"role": "user", "content": list(attachments)})
            attachments.clear()
            attachment_sets.clear()

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
            delivered = bool(
                role
                and type(selection.get("turn_seq")) is int
                and _delivered(events, role, [image.sha256 for image in validated], selection["turn_seq"])
            )
            # Existing full-binding staleness exclusion precedes delivery
            # selection. It proves obsolescence, never that pixels were seen;
            # explicit pins/reinspection can still restore their exact bytes.
            projection["observations"].append({**selection, "image_sha256": [image.sha256 for image in validated]})
            projection["omitted_image_count" if omitted else "retained_image_count"] += len(validated)
            metadata = []
            positions = []
            for raw, image in zip(images, validated, strict=True):
                metadata.append({key: value for key, value in raw.items() if key != "data_base64"})
                if omitted:
                    continue
                positions.append(len(attachments) + 1)
                attachments.extend(
                    [
                        {
                            "type": "text",
                            "text": f"Browser observation {message.get('tool_call_id')}: {image.label} "
                            f"({image.width}x{image.height}, sha256 {image.sha256}). "
                            f"Original capture arguments: {json.dumps(selection.get('capture_arguments'), ensure_ascii=False)}. "
                            "Recover exact saved pixels with browser_observe(revisit_call_ids=[capture IDs]).",
                        },
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": image.media_type, "data": image.data_base64},
                        },
                    ]
                )
            if positions:
                attachment_sets.append(
                    {
                        **selection,
                        "block_indices": positions,
                        "image_sha256": [image.sha256 for image in validated],
                        "required": not delivered or message.get("tool_call_id") in pinned_call_ids,
                    }
                )
            observation["images"] = metadata
            if omitted:
                observation["visual_projection"] = selection
            message["content"] = json.dumps(observation, ensure_ascii=False)
        result.append(message)
    flush()
    # A new role frame may omit old tool pairs; reinspection adds reference
    # images as a user attachment, never manufactures an unpaired tool result.
    for identity in sorted(pending - visual_call_ids):
        assert role is not None
        original = original_visual_capture(events, role, identity)
        blocks: list[dict[str, Any]] = [
            {"type": "text", "text": f"Saved capture {identity}; original binding, not a new capture."}
        ]
        indices = []
        for image in original["images"]:
            indices.append(len(blocks))
            blocks.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": image.media_type, "data": image.data_base64},
                }
            )
        projection["image_sets"].append(
            {
                "call_id": identity,
                "required": True,
                "reason": "pending_reinspection",
                "message_index": len(result),
                "block_indices": indices,
                "image_sha256": [image.sha256 for image in original["images"]],
            }
        )
        result.append({"role": "user", "content": blocks})
        visual_call_ids.add(identity)
    current = [item for item in projection["image_sets"] if item.get("reason") == "current_candidate"]
    if current:
        max(current, key=lambda item: item["visual_observation_seq"])["required"] = True
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
    events = store.load_events(
        session_id, kinds=("visual_observation_completed", "tool_observed", "llm_request_observed"), role=role
    )
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
