"""Pure optional-image admission over a copied native input projection.

Host origins authorize omission, never text/native-history rewriting. Costs are
serialization proxies, not measured or bounded provider vision-token usage.
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .token_estimator import TokenEstimator


def _image_data(block: Mapping[str, Any]) -> str | None:
    source = block.get("source")
    if block.get("type") == "image" and isinstance(source, dict) and source.get("type") == "base64":
        value = source.get("data")
        return value if isinstance(value, str) else None
    url = block.get("image_url")
    if block.get("type") == "image_url" and isinstance(url, dict):
        value = url.get("url")
        if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
            return value.split(",", 1)[1]
    return None


def _image_digest(block: Mapping[str, Any]) -> str | None:
    """sha256 of an image block's bytes, or None when the block is not decodable."""

    data = _image_data(block)
    if data is None:
        return None
    try:
        return hashlib.sha256(base64.b64decode(data, validate=True)).hexdigest()
    except (ValueError, binascii.Error):
        return None


def _match_image_sequence(blocks: Sequence[Mapping[str, Any]], expected: Sequence[str]) -> list[int] | None:
    """Indices of the first in-order image-block run matching ``expected``.

    Frames of the same capture can be byte-identical, so a digest-set lookup is
    ambiguous; the host always emits attachment images in capture order, so an
    ordered subsequence match is the honest rebinding.
    """

    indexed = [(index, digest) for index, block in enumerate(blocks) if (digest := _image_digest(block)) is not None]
    if len(indexed) < len(expected):
        return None
    digests = [digest for _index, digest in indexed]
    for start in range(len(digests) - len(expected) + 1):
        if digests[start : start + len(expected)] == list(expected):
            return [indexed[offset][0] for offset in range(start, start + len(expected))]
    return None


def rebind_visual_input_sets(
    messages: Sequence[Mapping[str, Any]],
    image_sets: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Re-locate host visual origins in the final request messages.

    The host may prepend system reminders, or drop a repeated injection, after
    the visual projection was computed. Passing those now-stale indices to
    ``select_visual_working_set`` reads the wrong message. Origins are therefore
    re-bound by their image bytes. Returns ``(rebound, dropped)``; a dropped
    origin is reported, never fatal.
    """

    rebound: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for item in image_sets:
        expected = [str(value) for value in (item.get("image_sha256") or []) if isinstance(value, str)]
        call_id = str(item.get("call_id", ""))
        located: tuple[int, list[int]] | None = None
        if expected:
            for message_index, message in enumerate(messages):
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                indices = _match_image_sequence(content, expected)
                if indices is not None:
                    located = (message_index, indices)
                    break
        if located is None:
            dropped.append({"call_id": call_id, "reason": "origin_not_in_final_request"})
            continue
        message_index, block_indices = located
        rebound.append({**item, "message_index": message_index, "block_indices": block_indices})
    return rebound, dropped


def _proxy(messages: Sequence[Mapping[str, Any]]) -> int:
    structured = sum(
        TokenEstimator.estimate(
            item.get("content", "")
            if isinstance(item.get("content"), str)
            else json.dumps(item.get("content", ""), ensure_ascii=False)
        )
        + 8
        + (TokenEstimator.estimate(json.dumps(item["tool_calls"], ensure_ascii=False)) if item.get("tool_calls") else 0)
        for item in messages
    )
    flat = TokenEstimator.estimate("\n".join(str(item.get("content") or "") for item in messages))
    return max(structured, flat)


def visual_payload_cost(messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Separate text from the unchanged conservative serialization proxy."""
    without = copy.deepcopy(list(messages))
    count = size = 0
    for message in without:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            data = _image_data(block)
            if data is None:
                continue
            count += 1
            size += len(base64.b64decode(data, validate=True))
            if block.get("type") == "image":
                block["source"]["data"] = ""
            else:
                block["image_url"]["url"] = block["image_url"]["url"].split(",", 1)[0] + ","
    total, text = _proxy(messages), _proxy(without)
    return {
        "admission_proxy_tokens": total,
        "text_token_estimate": text,
        "image_serialization_proxy_tokens": max(0, total - text),
        "image_count": count,
        "image_bytes": size,
        "actual_visual_tokens": None,
        "visual_token_status": "unknown" if count else "not_applicable",
        "cost_scope": "conservative_serialization_proxy_not_actual_vision_tokens",
    }


def select_visual_working_set(
    messages: Sequence[Mapping[str, Any]],
    origins: Sequence[Mapping[str, Any]],
    *,
    hard_allowance: int,
    optional_allowance: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep mandatory pixels; greedily admit whole optional sets most recent first.

    When the mandatory pixels alone exceed the model window the request can
    never be built, so the oldest mandatory capture sets are demoted: their
    exact original pixels stay inspectable on demand
    (``browser_observe(revisit_call_ids=[capture IDs])``) instead of killing
    the run on its own evidence.

    An origin that no longer resolves against ``messages`` is dropped and
    reported in the audit. Visual evidence is optional input: a stale index must
    never end the run that produced it.
    """
    original = copy.deepcopy([dict(item) for item in messages])
    optional: list[tuple[str, frozenset[tuple[int, int]]]] = []
    mandatory: list[tuple[str, frozenset[tuple[int, int]]]] = []
    owned: set[tuple[int, int]] = set()
    claimed: set[tuple[int, int]] = set()
    dropped: list[dict[str, Any]] = []
    for origin in origins:
        mi, indices, hashes = origin.get("message_index"), origin.get("block_indices"), origin.get("image_sha256")
        call_id = str(origin.get("call_id", ""))
        required = origin.get("required") is not False
        if type(mi) is not int or not isinstance(indices, list) or not isinstance(hashes, list):
            dropped.append({"call_id": call_id, "reason": "malformed_origin", "required": required})
            continue
        if not 0 <= mi < len(original) or not isinstance(original[mi].get("content"), list):
            dropped.append({"call_id": call_id, "reason": "origin_message_absent", "required": required})
            continue
        if not indices or len(indices) != len(hashes) or any(type(index) is not int for index in indices):
            dropped.append({"call_id": call_id, "reason": "origin_block_mismatch", "required": required})
            continue
        positions: set[tuple[int, int]] = set()
        reason: str | None = None
        for index, expected in zip(indices, hashes, strict=True):
            content = original[mi]["content"]
            if not 0 <= index < len(content) or not isinstance(content[index], dict):
                reason = "origin_block_absent"
                break
            digest = _image_digest(content[index])
            if digest is None:
                reason = "origin_block_not_image"
                break
            if digest != expected:
                reason = "origin_integrity_mismatch"
                break
            position = (mi, index)
            if position in claimed:
                reason = "ambiguous_host_visual_origin"
                break
            positions.add(position)
            claimed.add(position)
        if reason is not None:
            dropped.append({"call_id": call_id, "reason": reason, "required": required})
            continue
        if required:
            mandatory.append((call_id, frozenset(positions)))
        else:
            optional.append((call_id, frozenset(positions)))
            owned |= positions

    def project(removed: set[tuple[int, int]]) -> list[dict[str, Any]]:
        result = copy.deepcopy(original)
        for mi, message in enumerate(result):
            if isinstance(message.get("content"), list):
                message["content"] = [block for bi, block in enumerate(message["content"]) if (mi, bi) not in removed]
                omitted_here = [
                    identity
                    for identity, positions in (*optional, *mandatory)
                    if positions <= removed and any(index == mi for index, _block in positions)
                ]
                if omitted_here:
                    message["content"].append(
                        {
                            "type": "text",
                            "text": (
                                "Saved capture pixels omitted from this request by the resolved model budget: "
                                + json.dumps(omitted_here)
                                + ". Inspect exact original pixels with browser_observe(revisit_call_ids=[capture IDs])."
                            ),
                        }
                    )
        return result

    removed = set(owned)
    selected = project(removed)
    cost = visual_payload_cost(selected)
    demoted: list[str] = []
    if cost["admission_proxy_tokens"] > hard_allowance:
        # The mandatory pixels alone do not fit the model window.  Demote the
        # oldest mandatory capture sets until the request fits: the newest
        # capture stays mandatory and the demoted ones stay inspectable.
        for identity, mandatory_positions in mandatory:
            if cost["admission_proxy_tokens"] <= hard_allowance:
                break
            removed |= set(mandatory_positions)
            selected = project(removed)
            cost = visual_payload_cost(selected)
            demoted.append(identity)
        if cost["admission_proxy_tokens"] > hard_allowance:
            raise ValueError(
                "required_visual_input_budget_exceeded: " + json.dumps({"cost": cost, "hard_allowance": hard_allowance})
            )
    omitted = {identity for identity, _positions in optional}
    soft = min(hard_allowance, optional_allowance)
    for identity, candidate_positions in reversed(optional):
        candidate = project(removed - candidate_positions)
        candidate_cost = visual_payload_cost(candidate)
        if candidate_cost["admission_proxy_tokens"] <= soft:
            removed.difference_update(candidate_positions)
            selected, cost = candidate, candidate_cost
            omitted.discard(identity)
    return selected, {
        "cost": cost,
        "hard_allowance": hard_allowance,
        "optional_allowance": soft,
        "omitted_call_ids": [identity for identity, _positions in optional if identity in omitted],
        "demoted_call_ids": demoted,
        "dropped_origins": dropped,
        "dropped_mandatory_call_ids": [item["call_id"] for item in dropped if item["required"]],
        "selection_reason": "resolved_model_budget_whole_capture_sets",
    }
