"""Forced-write command intent normalization.

This module handles one narrow weak-model habit: a Director may emit an
``execute_command`` heredoc that clearly writes a file while the runtime has
already escalated the turn to a forced ``write_file`` retry. We normalize only
that explicit intent and leave every ambiguous command fail-closed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    extract_invocation_tool_name,
    normalize_path_token,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)

_PATH_TOKEN = r'(?:"(?P<{name}_dq>[^"]+)"|\'(?P<{name}_sq>[^\']+)\'|(?P<{name}_bare>[^\s;&|<>]+))'
_DELIMITER_TOKEN = r"['\"]?(?P<{name}>[A-Za-z_][A-Za-z0-9_]*)['\"]?"
_OPTIONAL_MKDIR_PREFIX = rf"^\s*(?:mkdir\s+-p\s+{_PATH_TOKEN.format(name='mkdir')}\s*&&\s*)?"

_CAT_HEREDOC_THEN_REDIRECT_RE = re.compile(
    _OPTIONAL_MKDIR_PREFIX
    + rf"cat\s+<<\s*{_DELIMITER_TOKEN.format(name='delim')}\s*>\s*{_PATH_TOKEN.format(name='path')}"
    + r"\s*\r?\n(?P<body>.*?)\r?\n(?P=delim)\s*$",
    re.DOTALL,
)

_CAT_REDIRECT_THEN_HEREDOC_RE = re.compile(
    _OPTIONAL_MKDIR_PREFIX
    + rf"cat\s*>\s*{_PATH_TOKEN.format(name='path')}\s*<<\s*{_DELIMITER_TOKEN.format(name='delim')}"
    + r"\s*\r?\n(?P<body>.*?)\r?\n(?P=delim)\s*$",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class CommandWriteIntent:
    file: str
    content: str
    pattern: str


def normalize_forced_write_command_decision(
    decision: TurnDecision,
    *,
    allowed_tool_names: Set[str],
) -> tuple[TurnDecision, tuple[dict[str, Any], ...]]:
    """Convert safe execute_command heredoc writes to write_file invocations.

    The conversion is intentionally scoped to forced-write retry contexts. It
    requires ``write_file`` to be allowed by the current tool set and refuses
    every command that is not a single, parseable heredoc file write.
    """

    if "write_file" not in {str(name) for name in allowed_tool_names}:
        return decision, ()
    if decision.kind != TurnDecisionKind.TOOL_BATCH or decision.tool_batch is None:
        return decision, ()

    converted: list[ToolInvocation] = []
    events: list[dict[str, Any]] = []
    changed = False

    for invocation in decision.tool_batch.invocations:
        if extract_invocation_tool_name(invocation) != "execute_command":
            converted.append(invocation)
            continue

        intent = extract_command_write_intent(invocation)
        if intent is None:
            converted.append(invocation)
            continue

        changed = True
        converted_invocation = ToolInvocation(
            call_id=_coerce_tool_call_id(invocation),
            tool_name="write_file",
            arguments={"file": intent.file, "content": intent.content, "encoding": "utf-8"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        converted.append(converted_invocation)
        events.append(
            {
                "type": "forced_write_command_normalized",
                "from_tool": "execute_command",
                "to_tool": "write_file",
                "file": intent.file,
                "pattern": intent.pattern,
            }
        )

    if not changed:
        return decision, ()

    tool_batch = _rebuild_tool_batch(decision.tool_batch.batch_id, converted)
    metadata = dict(decision.metadata or {})
    existing_events = metadata.get("tool_intent_normalizations")
    if isinstance(existing_events, list):
        metadata["tool_intent_normalizations"] = [*existing_events, *events]
    else:
        metadata["tool_intent_normalizations"] = events

    normalized_decision = TurnDecision(
        turn_id=TurnId(str(decision.turn_id)),
        kind=decision.kind,
        visible_message=decision.visible_message,
        reasoning_summary=decision.reasoning_summary,
        tool_batch=tool_batch,
        finalize_mode=decision.finalize_mode or FinalizeMode.NONE,
        domain=decision.domain,
        metadata=metadata,
    )
    return normalized_decision, tuple(events)


def extract_command_write_intent(invocation: Any) -> CommandWriteIntent | None:
    command = _extract_command_string(invocation)
    if not command:
        return None

    for pattern_name, pattern in (
        ("cat_heredoc_then_redirect", _CAT_HEREDOC_THEN_REDIRECT_RE),
        ("cat_redirect_then_heredoc", _CAT_REDIRECT_THEN_HEREDOC_RE),
    ):
        match = pattern.match(command)
        if match is None:
            continue
        path = _select_group(match, "path")
        content = str(match.group("body") or "")
        normalized_path = _normalize_safe_write_path(path)
        if normalized_path is None:
            return None
        mkdir_path = _select_group(match, "mkdir")
        if mkdir_path and not _mkdir_matches_target_parent(mkdir_path, normalized_path):
            return None
        return CommandWriteIntent(file=normalized_path, content=content, pattern=pattern_name)
    return None


def _extract_command_string(invocation: Any) -> str:
    arguments = (
        invocation.get("arguments") if isinstance(invocation, Mapping) else getattr(invocation, "arguments", None)
    )
    if not isinstance(arguments, Mapping):
        return ""
    command = arguments.get("command") or arguments.get("cmd") or arguments.get("script") or ""
    return str(command or "").strip()


def _select_group(match: re.Match[str], name: str) -> str:
    for suffix in ("dq", "sq", "bare"):
        value = match.groupdict().get(f"{name}_{suffix}")
        if value is not None:
            return str(value)
    return ""


def _normalize_safe_write_path(path: str) -> str | None:
    normalized = normalize_path_token(path)
    if not normalized:
        return None
    if normalized.startswith("/") or normalized.startswith("~"):
        return None
    if "\x00" in normalized or "\n" in normalized or "\r" in normalized:
        return None
    parts = [part for part in normalized.split("/") if part]
    if any(part == ".." for part in parts):
        return None
    if any(token in normalized for token in ("`", "$(", "${", ";", "|", "&")):
        return None
    return normalized


def _mkdir_matches_target_parent(mkdir_path: str, target_file: str) -> bool:
    normalized_dir = _normalize_safe_write_path(mkdir_path)
    if normalized_dir is None:
        return False
    parent = target_file.rsplit("/", 1)[0] if "/" in target_file else "."
    return normalized_dir.rstrip("/") == parent.rstrip("/")


def _coerce_tool_call_id(invocation: Any) -> ToolCallId:
    if isinstance(invocation, Mapping):
        raw = invocation.get("call_id") or invocation.get("id") or "normalized-write"
    else:
        raw = getattr(invocation, "call_id", None) or getattr(invocation, "id", None) or "normalized-write"
    return ToolCallId(str(raw))


def _rebuild_tool_batch(batch_id: BatchId, invocations: Sequence[ToolInvocation]) -> ToolBatch:
    invocation_list = list(invocations)
    return ToolBatch(
        batch_id=batch_id,
        invocations=invocation_list,
        parallel_readonly=[inv for inv in invocation_list if inv.execution_mode == ToolExecutionMode.READONLY_PARALLEL],
        readonly_serial=[inv for inv in invocation_list if inv.execution_mode == ToolExecutionMode.READONLY_SERIAL],
        serial_writes=[inv for inv in invocation_list if inv.execution_mode == ToolExecutionMode.WRITE_SERIAL],
        async_receipts=[inv for inv in invocation_list if inv.execution_mode == ToolExecutionMode.ASYNC_RECEIPT],
    )
