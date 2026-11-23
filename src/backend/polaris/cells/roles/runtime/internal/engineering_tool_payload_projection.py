"""Request-local folding of tool payloads: result cards and native calls.

Two planes carry model-visible payloads and both grow without bound:

- result plane: ``role == "tool"`` messages holding a FileCard or CommandCard;
- call plane:   assistant ``tool_calls`` whose arguments embed a durable content
                 body — ``write_file``/``edit_file`` put the document itself in
                 the argument.

Folding replaces a payload with an explanation plus a recovery path. It never
touches the native envelope: the assistant ``tool_calls`` id/name and the
matching ``tool`` result stay paired, so the provider protocol stays valid. A
fold happens only when the payload is at least the fold byte floor, the
activity is outside the fold window, the payload is not the current edit, the
current reasoning or an acceptance clause, and the projected message is
strictly smaller than the original.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_semantic_advice import (
    file_node_id,
    summary_is_informative,
    write_node_id,
)

# Tools whose arguments embed a durable workspace body. Only these are folded:
# the body is recoverable with read_file, so a fold loses no unique evidence.
# A long shell script is deliberately excluded — its text exists only here.
FOLDABLE_CALL_TOOLS = frozenset({"write_file", "edit_file"})

_PROTECTED_PIN_REASONS = frozenset({"current_edit", "current_reasoning", "acceptance_clause"})

_DUPLICATE_REASONS = frozenset({"superseded_by_newer_card", "superseded_by_newer_payload"})


def configured_inactive_fold_activities() -> int:
    return max(0, int(os.environ.get("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "24")))


def _rehydration_grace_activities() -> int:
    return max(0, int(os.environ.get("KERNELONE_CONTEXTOS_REHYDRATION_GRACE_ACTIVITIES", "2")))


def configured_fold_min_bytes() -> int:
    return max(0, int(os.environ.get("KERNELONE_CONTEXTOS_FOLD_MIN_BYTES", "1024")))


def configured_fold_grace_activities() -> int:
    """How much recent context the budget tier may never fold."""

    return max(0, int(os.environ.get("KERNELONE_CONTEXTOS_FOLD_GRACE_ACTIVITIES", "2")))


# The distiller has to run *ahead* of the fold it feeds: a summary that lands
# after the payload was already replaced is wasted work, and a summary of a
# payload the fold never touches is equally wasted. Both thresholds therefore
# derive from the fold knobs instead of being restated here.
DISTILL_LEAD_ACTIVITIES = 14
DISTILL_BYTE_FLOOR_DIVISOR = 2


def distill_min_activities() -> int:
    return max(1, configured_inactive_fold_activities() - DISTILL_LEAD_ACTIVITIES)


def distill_min_bytes() -> int:
    return max(256, configured_fold_min_bytes() // DISTILL_BYTE_FLOOR_DIVISOR)


# A target is only useful below the window it must fit; half the window is the
# ceiling, so a small model window tightens the target with it.
_WORKING_SET_WINDOW_FRACTION = 0.5


def working_set_target_tokens(window: int | None = None) -> int:
    """Target size for the retained working set; the budget tier folds to it."""

    configured = max(2048, int(os.environ.get("KERNELONE_CONTEXTOS_FOLD_WORKING_SET_TOKENS", "32768")))
    if window is None or window <= 0:
        return configured
    return max(2048, min(configured, int(window * _WORKING_SET_WINDOW_FRACTION)))


# Reason code for a fold driven by the working-set budget rather than by age.
REASON_PRESSURE = "working_set_pressure"


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


def _parse_arguments(raw: object) -> dict[str, Any] | None:
    """Parse a native tool_call argument string without trusting its shape."""

    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _byte_size(text: str) -> int:
    return len(text.encode("utf-8"))


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
        if summary_is_informative(text, original):
            return str(text), kind
    structural = _structural_summary(card, original)
    if structural is not None:
        return structural, "structural"
    return None


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
    if not _apply_fold_summary(card, summaries, file_node_id(path), original=original):
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
            if not isinstance(function, dict) or function.get("name") not in FOLDABLE_CALL_TOOLS:
                continue
            arguments = _parse_arguments(function.get("arguments"))
            path = (arguments or {}).get("file") or (arguments or {}).get("path")
            if isinstance(path, str) and path:
                targets.add(path)
    return targets


def _call_identity(tool: str, arguments: Mapping[str, Any] | None) -> str:
    """The workspace path a content call writes, used for dedup and pinning."""

    if tool not in FOLDABLE_CALL_TOOLS or not arguments:
        return ""
    path = arguments.get("file") or arguments.get("path")
    return path if isinstance(path, str) and path else ""


def _call_recovery(tool: str, identity: str) -> str:
    if tool in FOLDABLE_CALL_TOOLS and identity:
        return f"read_file {identity} 取回当前正文；历史参数可按 tool_call_id 在运行日志中核查。"
    return "历史参数可按 tool_call_id 在运行日志中核查。"


def _call_structural_summary(tool: str, arguments: Mapping[str, Any], size: int) -> str | None:
    """Deterministic explanation for a folded content call.

    The call already executed, so the summary states the effect and where the
    current body lives rather than describing the replaced text.
    """

    identity = _call_identity(tool, arguments)
    if tool == "write_file" and identity:
        return (
            f"写入 {identity}：完整正文 {_byte_size(str(arguments.get('content') or ''))} 字节已折叠；"
            f"该调用已执行，当前正文见工作区文件 {identity}。"
        )
    if tool == "edit_file" and identity:
        search = arguments.get("search")
        replace = arguments.get("replace")
        return (
            f"编辑 {identity}：search {_byte_size(str(search) if isinstance(search, str) else '')} 字节 / "
            f"replace {_byte_size(str(replace) if isinstance(replace, str) else '')} 字节已折叠；"
            f"该调用已执行，结果已落在 {identity}。"
        )
    return None


def _choose_call_summary(
    tool: str,
    arguments: Mapping[str, Any],
    summaries: Mapping[str, Mapping[str, Any]] | None,
    node_key: str,
    *,
    original: str,
) -> tuple[str, str] | None:
    # Only the write plane has distiller coverage; an edit call keeps its
    # deterministic envelope rather than borrowing some other payload's summary.
    summary = (summaries or {}).get(node_key) if node_key else None
    if isinstance(summary, dict) and summary_is_informative(summary.get("summary"), original):
        return str(summary["summary"]), "model_inference"
    structural = _call_structural_summary(tool, arguments, len(original))
    if structural is not None:
        return structural, "structural"
    return None


def _pin_reason(
    identity: str,
    *,
    targets: set[str],
    reasoning_text: str,
    instruction_text: str,
) -> str:
    if not identity:
        return ""
    if identity in targets:
        return "current_edit"
    if identity in reasoning_text:
        return "current_reasoning"
    if identity in instruction_text:
        return "acceptance_clause"
    return ""


def _latest_identities(messages: Sequence[Mapping[str, Any]]) -> dict[str, tuple[int, int]]:
    """Last occurrence of each workspace path across both planes."""

    latest: dict[str, tuple[int, int]] = {}
    for index, message in enumerate(messages):
        if message.get("role") == "assistant":
            for call_index, call in enumerate(message.get("tool_calls") or []):
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict):
                    continue
                arguments = _parse_arguments(function.get("arguments"))
                identity = _call_identity(str(function.get("name") or ""), arguments)
                if identity:
                    latest[identity] = (index, call_index)
        elif message.get("role") == "tool":
            try:
                payload = json.loads(str(message.get("content") or "{}"))
            except json.JSONDecodeError:
                continue
            card_payload, _nested = _card_payload(payload)
            if card_payload is None:
                continue
            for node_kind in ("file_card", "command_card"):
                card = card_payload.get(node_kind)
                if not isinstance(card, dict):
                    continue
                path = card.get("path") if node_kind == "file_card" else card.get("command")
                if isinstance(path, str) and path:
                    latest[path] = (index, -1)
    return latest


class _FoldPolicy:
    """Everything the folding decision needs, derived once per request."""

    def __init__(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        semantic_summaries: Mapping[str, Mapping[str, Any]] | None,
        inactive_fold_activities: int | None,
        force_fold_paths: Sequence[str],
        working_set_tokens: int | None,
        message_cost: Callable[[Mapping[str, Any]], int] | None = None,
    ) -> None:
        self.fold_window = (
            configured_inactive_fold_activities()
            if inactive_fold_activities is None
            else max(0, int(inactive_fold_activities))
        )
        self.grace = configured_fold_grace_activities()
        self.min_bytes = configured_fold_min_bytes()
        self.forced_paths = {str(path) for path in (force_fold_paths or ()) if str(path).strip()}
        self.summaries = semantic_summaries
        self.budget_tokens = (
            working_set_target_tokens() if working_set_tokens is None else max(0, int(working_set_tokens))
        )
        self.latest = _latest_identities(messages)
        self.targets = _write_targets(messages)
        self.reasoning_text = json.dumps(
            [message for message in messages[-8:] if message.get("role") == "assistant"], ensure_ascii=False
        )
        self.instruction_text = json.dumps(
            [
                message
                for message in messages[:8]
                if message.get("role") in {"system", "user"} and not message.get("tool_calls")
            ],
            ensure_ascii=False,
        )
        self.activity_indices = _activity_indices(messages)
        self.current_activity = self.activity_indices[-1] if self.activity_indices else 0
        self.message_cost = message_cost

    def age_of(self, activity_index: int) -> int:
        return self.current_activity - activity_index

    def pin_reason(self, identity: str) -> str:
        return _pin_reason(
            identity,
            targets=self.targets,
            reasoning_text=self.reasoning_text,
            instruction_text=self.instruction_text,
        )

    def reason_for(self, *, duplicate: bool, forced: bool, age: int, duplicate_reason: str) -> str | None:
        """Pick the semantic reason, or the pressure reason under a tight budget."""

        if duplicate and age >= self.fold_window:
            return duplicate_reason
        if forced:
            return "stalled_repeated_write"
        if age >= self.fold_window:
            return "outside_current_focus"
        if age >= self.grace:
            return REASON_PRESSURE
        return None


class _FoldAction:
    """One planned fold: how much it saves, why, and how to apply it."""

    __slots__ = ("_apply", "age", "reason", "shrink_tokens")

    def __init__(self, *, age: int, shrink_tokens: int, reason: str, apply: Callable[[], int]) -> None:
        self.age = age
        # The budget tier accounts in tokens, so the estimate must be tokens too.
        self.shrink_tokens = shrink_tokens
        self.reason = reason
        self._apply = apply

    def apply(self) -> int:
        return max(0, int(self._apply()))


def fold_tool_payload_previews(
    messages: Sequence[Mapping[str, Any]],
    *,
    semantic_summaries: Mapping[str, Mapping[str, Any]] | None = None,
    inactive_fold_activities: int | None = None,
    force_fold_paths: Sequence[str] = (),
    working_set_tokens: int | None = None,
    message_cost: Callable[[Mapping[str, Any]], int] | None = None,
) -> list[dict[str, Any]]:
    """Fold inactive result cards and content tool calls under one policy.

    Two tiers, hardest protection first:

    - **Age tier** — a payload older than the fold window (default 24 activities)
      and at least the minimum size (default 1024 bytes) folds with a semantic
      reason. This is the deterministic rule that needs no budget.
    - **Budget tier** — if the projected working set still exceeds the fold
      budget (default 32k tokens), fold further candidates oldest-and-largest
      first, down to a short grace window, until the set fits.

    Both tiers refuse to fold the current edit, the current reasoning, an
    acceptance clause, an unresolved failure, or a freshly rehydrated card, and
    both require the projected message to be strictly smaller with an
    informative explanation in place of the payload.
    """

    policy = _FoldPolicy(
        messages,
        semantic_summaries=semantic_summaries,
        inactive_fold_activities=inactive_fold_activities,
        force_fold_paths=force_fold_paths,
        working_set_tokens=working_set_tokens,
        message_cost=message_cost,
    )
    projected = [copy.deepcopy(dict(message)) for message in messages]
    actions: list[_FoldAction] = []
    for index, message in enumerate(projected):
        if message.get("role") == "assistant":
            actions.extend(_plan_message_calls(message, index=index, policy=policy))
        elif message.get("role") == "tool":
            actions.extend(_plan_message_cards(message, index=index, policy=policy))

    for action in actions:
        if action.reason != REASON_PRESSURE and action.age >= policy.fold_window:
            action.apply()
    if policy.budget_tokens > 0:
        _fold_for_working_set(projected, actions, policy)
    return projected


def _fold_for_working_set(
    projected: Sequence[Mapping[str, Any]],
    actions: Sequence[_FoldAction],
    policy: _FoldPolicy,
) -> None:
    """Spend the oldest, largest remaining payloads until the set fits."""

    remaining = _projected_tokens(projected, policy.message_cost)
    if remaining <= policy.budget_tokens:
        return
    candidates = sorted(
        (action for action in actions if action.reason == REASON_PRESSURE and action.age >= policy.grace),
        key=lambda action: (-action.age, -action.shrink_tokens),
    )
    for action in candidates:
        if remaining <= policy.budget_tokens:
            break
        applied = action.apply()
        if applied <= 0:
            continue
        remaining -= action.shrink_tokens


def _estimate_tokens(text: str) -> int:
    from polaris.kernelone.llm.engine.token_estimator import TokenEstimator

    return TokenEstimator.estimate(text)


def _projected_tokens(
    messages: Sequence[Mapping[str, Any]],
    message_cost: Callable[[Mapping[str, Any]], int] | None = None,
) -> int:
    if message_cost is not None:
        return sum(max(0, int(message_cost(message))) for message in messages)
    return sum(_estimate_tokens(json.dumps(message, ensure_ascii=False)) for message in messages)


def _plan_message_calls(
    message: dict[str, Any],
    *,
    index: int,
    policy: _FoldPolicy,
) -> list[_FoldAction]:
    actions: list[_FoldAction] = []
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return actions
    activity_index = policy.activity_indices[index]
    for call_index, call in enumerate(calls):
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict) or function.get("name") not in FOLDABLE_CALL_TOOLS:
            continue
        tool = str(function.get("name") or "")
        raw = str(function.get("arguments") or "")
        size = _byte_size(raw)
        if size < policy.min_bytes:
            continue
        arguments = _parse_arguments(raw)
        if arguments is None:
            continue
        identity = _call_identity(tool, arguments)
        if policy.pin_reason(identity):
            continue
        age = policy.age_of(activity_index)
        duplicate = bool(identity) and policy.latest.get(identity) != (index, call_index)
        reason = policy.reason_for(
            duplicate=duplicate,
            forced=bool(identity) and identity in policy.forced_paths,
            age=age,
            duplicate_reason="superseded_by_newer_payload",
        )
        if reason is None:
            continue
        node_key = write_node_id(identity) if tool == "write_file" else ""
        chosen = _choose_call_summary(tool, arguments, policy.summaries, node_key, original=raw)
        if chosen is None:
            continue
        summary, kind = chosen
        projected_arguments = json.dumps(
            _folded_call_envelope(
                tool,
                arguments,
                identity,
                reason=reason,
                summary=summary,
                kind=kind,
                size=size,
                activity_seq=activity_index,
            ),
            ensure_ascii=False,
        )
        if size - _byte_size(projected_arguments) <= 0:
            continue

        def apply(
            function: dict[str, Any] = function,
            projected: str = projected_arguments,
            size: int = size,
        ) -> int:
            if function.get("arguments") == projected:
                return 0
            function["arguments"] = projected
            return size - _byte_size(projected)

        actions.append(
            _FoldAction(
                age=age,
                shrink_tokens=max(1, _estimate_tokens(raw) - _estimate_tokens(projected_arguments)),
                reason=reason,
                apply=apply,
            )
        )
    return actions


def _folded_call_envelope(
    tool: str,
    arguments: Mapping[str, Any],
    identity: str,
    *,
    reason: str,
    summary: str,
    kind: str,
    size: int,
    activity_seq: int,
) -> dict[str, Any]:
    return {
        "fold_state": "FOLDED",
        "fold_reason": reason,
        "fold_tool": tool,
        "file": identity,
        "bytes": size,
        "summary": summary,
        "summary_kind": kind,
        "recovery": _call_recovery(tool, identity),
        "last_fold_activity_seq": activity_seq,
    }


def _plan_message_cards(
    message: dict[str, Any],
    *,
    index: int,
    policy: _FoldPolicy,
) -> list[_FoldAction]:
    try:
        payload = json.loads(str(message.get("content") or "{}"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    card_payload, nested = _card_payload(payload)
    if card_payload is None:
        return []
    command_card = card_payload.get("command_card")
    if isinstance(command_card, dict):
        return _plan_command_card(
            command_card,
            card_payload=card_payload,
            payload=payload,
            nested=nested,
            message=message,
            index=index,
            policy=policy,
        )
    card = card_payload.get("file_card")
    if not (isinstance(card, dict) and isinstance(card.get("path"), str)):
        return []
    return _plan_file_card(
        card,
        path=card["path"],
        card_payload=card_payload,
        payload=payload,
        nested=nested,
        message=message,
        index=index,
        policy=policy,
    )


def _commit_card(
    message: dict[str, Any],
    payload: dict[str, Any],
    card_payload: dict[str, Any],
    nested: bool,
) -> None:
    if nested:
        payload["content"] = json.dumps(card_payload, ensure_ascii=False)
    message["content"] = json.dumps(payload, ensure_ascii=False)


def _plan_command_card(
    command_card: dict[str, Any],
    *,
    card_payload: dict[str, Any],
    payload: dict[str, Any],
    nested: bool,
    message: dict[str, Any],
    index: int,
    policy: _FoldPolicy,
) -> list[_FoldAction]:
    reference = command_card.get("content_ref")
    activity_index = policy.activity_indices[index]
    age = policy.age_of(activity_index)
    if reference and str(reference) in policy.reasoning_text:
        command_card["pin_reason"] = "current_reasoning"
    elif command_card.get("exit_code") != 0 and age < policy.fold_window:
        command_card["pin_reason"] = "unresolved_failure"
    if command_card.get("fold_state") == "FOLDED":
        _commit_card(message, payload, card_payload, nested)
        return []
    payload_text = str(command_card.get("stdout_preview") or "") + "\n" + str(command_card.get("stderr_preview") or "")
    reason: str | None = None
    if not command_card.get("pin_reason") and _byte_size(payload_text) >= policy.min_bytes:
        reason = policy.reason_for(duplicate=False, forced=False, age=age, duplicate_reason="superseded_by_newer_card")
        if reason is not None and not _choose_fold_summary(
            command_card, policy.summaries, str(reference), original=payload_text
        ):
            reason = None
    command_card["fold_state"] = "ACTIVE"
    _commit_card(message, payload, card_payload, nested)
    if reason is None:
        return []

    def apply() -> int:
        if not _apply_fold_summary(command_card, policy.summaries, str(reference), original=payload_text):
            command_card["fold_state"] = "ACTIVE"
            command_card["fold_reason"] = None
            _commit_card(message, payload, card_payload, nested)
            return 0
        before = _byte_size(payload_text)
        command_card["stdout_preview"] = ""
        command_card["stderr_preview"] = ""
        command_card["fold_state"] = "FOLDED"
        command_card["fold_reason"] = reason
        command_card["last_fold_activity_seq"] = activity_index
        _commit_card(message, payload, card_payload, nested)
        return before

    return [_FoldAction(age=age, shrink_tokens=max(1, _estimate_tokens(payload_text)), reason=reason, apply=apply)]


def _plan_file_card(
    card: dict[str, Any],
    *,
    path: str,
    card_payload: dict[str, Any],
    payload: dict[str, Any],
    nested: bool,
    message: dict[str, Any],
    index: int,
    policy: _FoldPolicy,
) -> list[_FoldAction]:
    pin_reason = policy.pin_reason(path)
    if pin_reason:
        card["pin_reason"] = pin_reason
    activity_index = policy.activity_indices[index]
    age = policy.age_of(activity_index)
    preview = str(card.get("content_preview") or "")
    if card.get("fold_state") == "FOLDED":
        _commit_card(message, payload, card_payload, nested)
        return []
    protected = pin_reason in _PROTECTED_PIN_REASONS
    duplicate = policy.latest.get(path) != (index, -1)
    reason: str | None = None
    if _byte_size(preview) >= policy.min_bytes:
        reason = policy.reason_for(
            duplicate=duplicate,
            forced=path in policy.forced_paths,
            age=age,
            duplicate_reason="superseded_by_newer_card",
        )
        if (
            reason is None
            or protected
            or (card.get("recovered_from") is not None and age < _rehydration_grace_activities())
        ):
            reason = None
    card["fold_state"] = "ACTIVE"
    _commit_card(message, payload, card_payload, nested)
    if reason is None:
        return []

    def apply() -> int:
        if not _fold_file_card(card, path, summaries=policy.summaries, reason=reason, original=preview):
            card["fold_state"] = "ACTIVE"
            card["fold_reason"] = None
            _commit_card(message, payload, card_payload, nested)
            return 0
        card["last_fold_activity_seq"] = activity_index
        _commit_card(message, payload, card_payload, nested)
        return _byte_size(preview)

    return [_FoldAction(age=age, shrink_tokens=max(1, _estimate_tokens(preview)), reason=reason, apply=apply)]


def folded_payload_projections(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Describe folded payloads for realtime UI events and idempotent provenance."""

    projections: list[dict[str, Any]] = []
    activity_indices = _activity_indices(messages)
    current_activity = activity_indices[-1] if activity_indices else 0
    for index, message in enumerate(messages):
        if message.get("role") == "assistant":
            projections.extend(
                _call_projections(
                    message, index=index, current_activity=current_activity, activity_indices=activity_indices
                )
            )
            continue
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
                    "plane": "result",
                    "node_kind": node_kind,
                    "path": card.get("path") or card.get("command") or reference,
                    "projection_ref": reference,
                    "fold_state": "FOLDED",
                    "fold_reason_code": "DUPLICATE_PAYLOAD" if reason in _DUPLICATE_REASONS else "INACTIVE_PAYLOAD",
                    "fold_reason": reason,
                    "activity_seq": card.get("last_fold_activity_seq"),
                    "activity_age": current_activity - activity_indices[index],
                    "summary": card.get("summary") or "",
                }
            )
    return projections


def _call_projections(
    message: Mapping[str, Any],
    *,
    index: int,
    current_activity: int,
    activity_indices: Sequence[int],
) -> list[dict[str, Any]]:
    projections: list[dict[str, Any]] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict):
            continue
        arguments = _parse_arguments(function.get("arguments"))
        if not isinstance(arguments, dict) or arguments.get("fold_state") != "FOLDED":
            continue
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id:
            continue
        reason = str(arguments.get("fold_reason") or "outside_current_focus")
        projections.append(
            {
                "plane": "call",
                "node_kind": f"{function.get('name')}_call",
                "path": arguments.get("file") or arguments.get("fold_tool") or "",
                "projection_ref": f"toolcall:{call_id}",
                "fold_state": "FOLDED",
                "fold_reason_code": "DUPLICATE_PAYLOAD" if reason in _DUPLICATE_REASONS else "INACTIVE_PAYLOAD",
                "fold_reason": reason,
                "activity_seq": arguments.get("last_fold_activity_seq"),
                "activity_age": current_activity - activity_indices[index],
                "summary": arguments.get("summary") or "",
            }
        )
    return projections
