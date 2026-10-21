"""Derive native image input from the existing durable tool observations."""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts.engineering_run import ImageObservation


def build_visual_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep durable facts intact; attach images after every complete tool batch.

    Only the host attachment field of a successful browser observation is an
    image source. Quoted file contents and arbitrary model text remain text.
    All tool results precede the derived user image message, including batches
    with multiple tool calls. Cold recovery derives exactly the same view.
    """
    result: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []

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
            metadata = []
            for raw in images:
                image = ImageObservation(**raw)
                metadata.append({key: value for key, value in raw.items() if key != "data_base64"})
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
            message["content"] = json.dumps(observation, ensure_ascii=False)
        result.append(message)
    flush()
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
    candidate = {key: value for key, value in current_binding.items() if key != "verification_seq"}
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
            and {key: value for key, value in payload["input_binding"].items() if key != "verification_seq"}
            == candidate
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
        "inspect the returned images in a later model turn, then verify or approve."
    )
