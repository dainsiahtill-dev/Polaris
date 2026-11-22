"""Request-local FileCard folding, deduplication and pinning."""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any


def _inactive_fold_activities() -> int:
    return max(0, int(os.environ.get("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")))


def _rehydration_grace_activities() -> int:
    return max(0, int(os.environ.get("KERNELONE_CONTEXTOS_REHYDRATION_GRACE_ACTIVITIES", "2")))


def _fold_min_bytes() -> int:
    return max(0, int(os.environ.get("KERNELONE_CONTEXTOS_FOLD_MIN_BYTES", "1024")))


def _card_payload(payload: object) -> tuple[dict[str, Any] | None, bool]:
    """Unwrap tool envelopes whose card JSON is nested in content."""

    if not isinstance(payload, dict):
        return None, False
    if isinstance(payload.get("file_card"), dict) or isinstance(payload.get("command_card"), dict):
        return payload, False
    content = payload.get("content")
    if isinstance(content, str):
        try:
            nested = json.loads(content)
        except json.JSONDecodeError:
            return None, False
        if isinstance(nested, dict) and (
            isinstance(nested.get("file_card"), dict) or isinstance(nested.get("command_card"), dict)
        ):
            return nested, True
    return None, False


_STOCK_SUMMARY_MARKERS = {"completed", "done", "ok", "success", "failed", "command failed"}


def _summary_is_informative(text: object, original: str) -> bool:
    """A fold must replace payload with an explanation, never with a fragment.

    Distiller output and card summaries can degenerate into a stock status
    (``completed``) or a verbatim excerpt of the payload. Folding on either
    destroys evidence without buying any understanding, so it is refused.
    """

    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if len(stripped) < 4:
        return False
    lowered = stripped.lower().rstrip(".")
    if lowered in _STOCK_SUMMARY_MARKERS:
        return False
    # "completed successfully" is still a status, not an explanation.
    if len(stripped.split()) <= 3 and any(lowered.startswith(marker) for marker in _STOCK_SUMMARY_MARKERS):
        return False
    normalized = re.sub(r"\s+", " ", stripped).lower()
    original_normalized = re.sub(r"\s+", " ", original).lower()
    return not (original_normalized and normalized in original_normalized)


def _structural_summary(card: dict[str, Any], original: str) -> str | None:
    """Deterministic fallback so folding never depends on the distiller alone.

    Without this, a silent distiller makes folding inert and the working-set cap
    silently drops whole turns instead of compressing them.
    """

    path = card.get("path")
    if isinstance(path, str) and path:
        return (
            f"文件 {path}：{card.get('line_count', '?')} 行，"
            f"{card.get('size_bytes', len(original.encode('utf-8')))} 字节，"
            f"符号 {len(card.get('symbols') or [])} 个；原文可按 content_ref 取回。"
        )
    command = str(card.get("command") or "").strip()
    if not command:
        return None
    return (
        f"命令 `{command[:120]}` 退出码 {card.get('exit_code')}，"
        f"stdout {card.get('stdout_bytes', 0)} 字节 / stderr {card.get('stderr_bytes', 0)} 字节；"
        f"原始输出可按 content_ref 取回。"
    )


def _choose_fold_summary(
    card: dict[str, Any],
    summaries: Mapping[str, Mapping[str, Any]] | None,
    node_id: str,
    *,
    original: str,
) -> tuple[str, str] | None:
    """Prefer the distiller summary; fall back to the card's own only if it explains."""

    summary = (summaries or {}).get(node_id)
    candidates: list[tuple[object, str]] = []
    if isinstance(summary, dict):
        candidates.append((summary.get("summary"), "model_inference"))
    candidates.append((card.get("summary"), "card_default"))
    for text, kind in candidates:
        if _summary_is_informative(text, original):
            return str(text), kind
    structural = _structural_summary(card, original)
    if structural is not None:
        return structural, "structural"
    return None


def _card_bytes(card: Mapping[str, Any]) -> int:
    return len(json.dumps(card, ensure_ascii=False).encode("utf-8"))


def _fold_file_card(
    card: dict[str, Any],
    path: str,
    *,
    summaries: Mapping[str, Mapping[str, Any]] | None,
    reason: str,
    original: str,
) -> bool:
    """Fold only when an informative summary actually buys room.

    A long path or an already-large summary can make the projected card bigger
    than the preview it replaces; folding then costs context without compressing.
    """

    keys = (
        "summary",
        "summary_kind",
        "summary_evidence_refs",
        "summary_uncertain",
        "summary_reason",
        "content_preview",
        "content_bounds",
        "fold_state",
        "fold_reason",
    )
    snapshot = {key: copy.deepcopy(card.get(key)) for key in keys}
    had_source_summary = "source_summary" in card
    before = _card_bytes(card)
    if not _apply_fold_summary(card, summaries, path, original=original):
        return False
    card["content_preview"] = ""
    card["content_bounds"] = {**card.get("content_bounds", {}), "truncated": True}
    card["fold_state"] = "FOLDED"
    card["fold_reason"] = reason
    if _card_bytes(card) >= before:
        for key, value in snapshot.items():
            card[key] = value
        if not had_source_summary:
            card.pop("source_summary", None)
        return False
    return True


def _apply_fold_summary(
    card: dict[str, Any],
    summaries: Mapping[str, Mapping[str, Any]] | None,
    node_id: str,
    *,
    original: str = "",
) -> bool:
    chosen = _choose_fold_summary(card, summaries, node_id, original=original)
    if chosen is None:
        return False
    text, kind = chosen
    entry = (summaries or {}).get(node_id)
    summary: Mapping[str, Any] = entry if isinstance(entry, dict) else {}
    card["source_summary"] = card.get("summary")
    card["summary"] = text
    card["summary_kind"] = kind
    if kind == "model_inference":
        card["summary_evidence_refs"] = summary.get("evidence_refs") or []
        card["summary_uncertain"] = summary.get("uncertain", True)
        card["summary_reason"] = summary.get("reason") or ""
    else:
        # A card-default or deterministic structural fallback must not inherit
        # model provenance: it is not an inference and has no model evidence.
        card["summary_evidence_refs"] = []
        card["summary_uncertain"] = True
        card["summary_reason"] = ""
    return True


def _activity_indices(messages: Sequence[Mapping[str, Any]]) -> list[int]:
    """Map messages to completed logical activities, not transport retries."""

    indices: list[int] = []
    activity = -1
    for _index, message in enumerate(messages):
        if message.get("role") == "assistant":
            activity += 1
        indices.append(max(0, activity))
    return indices


def _write_targets(messages: Sequence[Mapping[str, Any]]) -> set[str]:
    targets: set[str] = set()
    for message in messages[-12:]:
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict) or function.get("name") not in {"write_file", "edit_file"}:
                continue
            try:
                arguments = json.loads(str(function.get("arguments") or "{}"))
            except json.JSONDecodeError:
                continue
            path = arguments.get("file")
            if isinstance(path, str) and path:
                targets.add(path)
    return targets


def folded_card_projections(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Describe folded snapshots for realtime UI events and idempotent provenance."""

    projections: list[dict[str, Any]] = []
    activity_indices = _activity_indices(messages)
    current_activity = activity_indices[-1] if activity_indices else 0
    for index, message in enumerate(messages):
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(str(message.get("content") or "{}"))
        except json.JSONDecodeError:
            continue
        card_payload, _nested = _card_payload(payload)
        if card_payload is None:
            continue
        for node_kind in ("file_card", "command_card"):
            card = card_payload.get(node_kind)
            if not isinstance(card, dict) or card.get("fold_state") != "FOLDED":
                continue
            reference = card.get("content_ref") or card.get("snapshot_ref")
            if not isinstance(reference, str) or not reference:
                continue
            reason = str(card.get("fold_reason") or "outside_current_focus")
            projections.append(
                {
                    "node_kind": node_kind,
                    "path": card.get("path") or card.get("command") or reference,
                    "projection_ref": reference,
                    "fold_state": "FOLDED",
                    "fold_reason_code": "DUPLICATE_PAYLOAD"
                    if reason == "superseded_by_newer_card"
                    else "INACTIVE_PAYLOAD",
                    "fold_reason": reason,
                    "activity_seq": card.get("last_fold_activity_seq"),
                    "activity_age": current_activity - activity_indices[index],
                    "summary": card.get("summary") or "",
                }
            )
    return projections


def fold_file_card_previews(
    messages: Sequence[Mapping[str, Any]],
    *,
    semantic_pins: Mapping[str, str] | None = None,
    semantic_summaries: Mapping[str, Mapping[str, Any]] | None = None,
    inactive_fold_activities: int | None = None,
    force_fold_paths: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Fold duplicate/non-focused FileCards without changing durable observations."""

    fold_window = (
        _inactive_fold_activities() if inactive_fold_activities is None else max(0, int(inactive_fold_activities))
    )
    forced_paths = {str(path) for path in (force_fold_paths or ()) if str(path).strip()}

    latest: dict[str, int] = {}
    for index, message in enumerate(messages):
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(str(message.get("content") or "{}"))
        except json.JSONDecodeError:
            continue
        card_payload, _nested = _card_payload(payload)
        card = card_payload.get("file_card") if card_payload is not None else None
        if isinstance(card, dict) and isinstance(card.get("path"), str):
            latest[card["path"]] = index
    targets = _write_targets(messages)
    reasoning_text = json.dumps(
        [message for message in messages[-8:] if message.get("role") == "assistant"], ensure_ascii=False
    )
    instruction_text = json.dumps(
        [
            message
            for message in messages[:8]
            if message.get("role") in {"system", "user"} and not message.get("tool_calls")
        ],
        ensure_ascii=False,
    )
    folded: list[dict[str, Any]] = []
    activity_indices = _activity_indices(messages)
    current_activity = activity_indices[-1] if activity_indices else 0
    for index, original in enumerate(messages):
        projected_message: dict[str, Any] = copy.deepcopy(dict(original))
        if projected_message.get("role") == "tool":
            try:
                payload = json.loads(str(projected_message.get("content") or "{}"))
            except json.JSONDecodeError:
                payload = None
            card_payload, nested = _card_payload(payload)
            command_card = card_payload.get("command_card") if card_payload is not None else None
            if isinstance(command_card, dict):
                reference = command_card.get("content_ref")
                advisor_view = (semantic_pins or {}).get(str(reference)) if reference else None
                activity_age = current_activity - activity_indices[index]
                if reference and str(reference) in reasoning_text:
                    command_card["pin_reason"] = "current_reasoning"
                elif command_card.get("exit_code") != 0 and activity_age < fold_window:
                    command_card["pin_reason"] = "unresolved_failure"
                elif advisor_view == "semantic_relevance":
                    command_card["pin_reason"] = advisor_view
                already_folded = command_card.get("fold_state") == "FOLDED"
                large_payload = (
                    len(str(command_card.get("stdout_preview") or "").encode("utf-8"))
                    + len(str(command_card.get("stderr_preview") or "").encode("utf-8"))
                    >= _fold_min_bytes()
                )
                if already_folded:
                    command_card["fold_state"] = "FOLDED"
                elif activity_age >= fold_window and large_payload and not command_card.get("pin_reason"):
                    payload_text = (
                        str(command_card.get("stdout_preview") or "")
                        + "\n"
                        + str(command_card.get("stderr_preview") or "")
                    )
                    if _apply_fold_summary(command_card, semantic_summaries, str(reference), original=payload_text):
                        command_card["stdout_preview"] = ""
                        command_card["stderr_preview"] = ""
                        command_card["fold_state"] = "FOLDED"
                        command_card["fold_reason"] = "outside_current_focus"
                        command_card["last_fold_activity_seq"] = activity_indices[index]
                    else:
                        # No explanation available: keep the payload rather than
                        # discarding evidence for no understanding.
                        command_card["fold_state"] = "ACTIVE"
                        command_card["fold_reason"] = None
                else:
                    command_card["fold_state"] = "ACTIVE"
                if nested and isinstance(payload, dict):
                    payload["content"] = json.dumps(card_payload, ensure_ascii=False)
                projected_message["content"] = json.dumps(payload, ensure_ascii=False)
                folded.append(projected_message)
                continue
            card = card_payload.get("file_card") if card_payload is not None else None
            if isinstance(card, dict) and isinstance(card.get("path"), str):
                path = card["path"]
                advisor_view = (semantic_pins or {}).get(path)
                pin_reason = (
                    "current_edit"
                    if path in targets
                    else "current_reasoning"
                    if path in reasoning_text
                    else "acceptance_clause"
                    if path in instruction_text
                    else advisor_view
                )
                if pin_reason:
                    card["pin_reason"] = pin_reason
                activity_age = current_activity - activity_indices[index]
                already_folded = card.get("fold_state") == "FOLDED"
                rehydrated = card.get("recovered_from") is not None
                duplicate = latest[path] != index
                small_payload = len(str(card.get("content_preview") or "").encode("utf-8")) < _fold_min_bytes()
                protected = pin_reason in {
                    "current_edit",
                    "current_reasoning",
                    "acceptance_clause",
                    "semantic_relevance",
                }
                if already_folded:
                    card["fold_state"] = "FOLDED"
                elif small_payload:
                    card["fold_state"] = "ACTIVE"
                elif duplicate and activity_age >= fold_window and not protected:
                    if _fold_file_card(
                        card,
                        path,
                        summaries=semantic_summaries,
                        reason="superseded_by_newer_card",
                        original=str(card.get("content_preview") or ""),
                    ):
                        card["last_fold_activity_seq"] = activity_indices[index]
                    else:
                        card["fold_state"] = "ACTIVE"
                        card["fold_reason"] = None
                elif path in forced_paths and not protected and not small_payload and not already_folded:
                    if _fold_file_card(
                        card,
                        path,
                        summaries=semantic_summaries,
                        reason="stalled_repeated_read",
                        original=str(card.get("content_preview") or ""),
                    ):
                        card["pin_reason"] = "stalled_repeated_read"
                        card["last_fold_activity_seq"] = activity_indices[index]
                    else:
                        card["fold_state"] = "ACTIVE"
                        card["fold_reason"] = None
                elif (rehydrated and activity_age < _rehydration_grace_activities()) or (activity_age < fold_window):
                    card["fold_state"] = "ACTIVE"
                elif not protected:
                    if _fold_file_card(
                        card,
                        path,
                        summaries=semantic_summaries,
                        reason="outside_current_focus",
                        original=str(card.get("content_preview") or ""),
                    ):
                        card["last_fold_activity_seq"] = activity_indices[index]
                    else:
                        card["fold_state"] = "ACTIVE"
                        card["fold_reason"] = None
                else:
                    card["fold_state"] = "ACTIVE"
                if nested and isinstance(payload, dict):
                    payload["content"] = json.dumps(card_payload, ensure_ascii=False)
                projected_message["content"] = json.dumps(payload, ensure_ascii=False)
        folded.append(projected_message)
    return folded
