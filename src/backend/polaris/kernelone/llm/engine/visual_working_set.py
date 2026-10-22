"""Pure optional-image admission over a copied native input projection.

Host origins authorize omission, never text/native-history rewriting. Costs are
serialization proxies, not measured or bounded provider vision-token usage.
"""

from __future__ import annotations

import base64
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
    """Keep mandatory pixels; greedily admit whole optional sets most recent first."""
    original = copy.deepcopy([dict(item) for item in messages])
    optional: list[tuple[str, frozenset[tuple[int, int]]]] = []
    owned: set[tuple[int, int]] = set()
    for origin in origins:
        if origin.get("required") is not False:
            continue
        mi, indices, hashes = origin.get("message_index"), origin.get("block_indices"), origin.get("image_sha256")
        if type(mi) is not int or not isinstance(indices, list) or not isinstance(hashes, list):
            raise ValueError("invalid_host_visual_origin")
        if not 0 <= mi < len(original) or not isinstance(original[mi].get("content"), list):
            raise ValueError("invalid_host_visual_origin")
        if not indices or len(indices) != len(hashes) or any(type(index) is not int for index in indices):
            raise ValueError("invalid_host_visual_origin")
        positions: set[tuple[int, int]] = set()
        for index, expected in zip(indices, hashes, strict=True):
            content = original[mi]["content"]
            if not 0 <= index < len(content) or not isinstance(content[index], dict):
                raise ValueError("invalid_host_visual_origin")
            data = _image_data(content[index])
            if data is None or hashlib.sha256(base64.b64decode(data, validate=True)).hexdigest() != expected:
                raise ValueError("host_visual_origin_integrity_mismatch")
            position = (mi, index)
            if position in owned:
                raise ValueError("ambiguous_host_visual_origin")
            positions.add(position)
            owned.add(position)
        optional.append((str(origin.get("call_id", "")), frozenset(positions)))

    def project(removed: set[tuple[int, int]]) -> list[dict[str, Any]]:
        result = copy.deepcopy(original)
        for mi, message in enumerate(result):
            if isinstance(message.get("content"), list):
                message["content"] = [block for bi, block in enumerate(message["content"]) if (mi, bi) not in removed]
                omitted_here = [
                    identity
                    for identity, positions in optional
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
        "selection_reason": "resolved_model_budget_whole_capture_sets",
    }
