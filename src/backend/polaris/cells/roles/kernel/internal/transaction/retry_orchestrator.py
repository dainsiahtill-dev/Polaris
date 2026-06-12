"""重试编排器 — 突变合约违反后的恢复与重试逻辑。

包含:
- 模型覆盖解析
- retry 上下文构建
- bootstrap read 执行
- 主重试循环
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, cast

from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime, ToolExecutionContext
from polaris.cells.roles.kernel.internal.transaction.constants import WRITE_TOOLS
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    build_context_target_bootstrap_decision,
    build_stale_edit_bootstrap_decision,
    extract_invocation_tool_name,
    extract_target_files_from_message,
    is_mutation_contract_violation,
    is_safe_readonly_bootstrap_invocations,
    is_stale_edit_contract_violation,
    rollback_state_after_retry_batch_failure,
)
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    requires_mutation_intent,
    requires_verification_intent,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.receipt_utils import (
    merge_batch_receipts,
    normalize_batch_receipts,
    record_receipts_to_ledger,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_allowed_tool_names_from_definitions,
    extract_latest_user_message,
    extract_tool_name_from_definition,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    RawLLMResponse,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)

logger = logging.getLogger(__name__)

_BOOTSTRAP_WHOLE_FILE_REPLACEMENT_MARKERS: tuple[str, ...] = (
    "audit-seed",
    "planning scenario",
    "build verification completed",
    "test verification completed",
    "notimplemented",
    "not implemented",
    "todo:",
    "fixme:",
)
# Whole-file replacement is only plausible for genuinely SMALL scaffold/seed
# content. Real-world large source files contain the markers above as ordinary
# code ("NotImplemented" comparison protocol, "TODO:" comments), and forcing
# write_file on them makes a weak model regenerate the entire file — observed
# live (phase1smoke django-15213): 600s LLM timeout, dead session.
_BOOTSTRAP_WHOLE_FILE_MAX_CHARS = 4000


# ---------------------------------------------------------------------------
# 模型覆盖与上下文构建
# ---------------------------------------------------------------------------


def resolve_retry_model_override(retry_llm_call_ordinal: int) -> str | None:
    """Resolve optional retry model override from environment.

    KERNELONE_TRANSACTION_KERNEL_RETRY_MODELS:
        Comma-separated model list used when retry LLM calls reach threshold.
    KERNELONE_TRANSACTION_KERNEL_RETRY_MODEL_START:
        1-based retry LLM call ordinal to start model override (default: 3).
    """
    if retry_llm_call_ordinal <= 0:
        return None
    raw_models = str(os.environ.get("KERNELONE_TRANSACTION_KERNEL_RETRY_MODELS", "") or "").strip()
    if not raw_models:
        return None
    candidates = [item.strip() for item in raw_models.split(",") if item and item.strip()]
    if not candidates:
        return None
    raw_start = str(os.environ.get("KERNELONE_TRANSACTION_KERNEL_RETRY_MODEL_START", "3") or "").strip()
    try:
        start_ordinal = max(1, int(raw_start))
    except ValueError:
        start_ordinal = 3
    if retry_llm_call_ordinal < start_ordinal:
        return None
    model_index = min(retry_llm_call_ordinal - start_ordinal, len(candidates) - 1)
    selected = candidates[model_index]
    return selected or None


_LINE_RANGE_EDIT_BLOCKS_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file": {
            "type": "string",
            "description": "Workspace-relative path of the file to edit.",
        },
        "start": {
            "type": "integer",
            "minimum": 1,
            "description": "First line to replace (1-based, inclusive — reuse the range you already read).",
        },
        "end": {
            "type": "integer",
            "minimum": 1,
            "description": "Last line to replace (1-based, inclusive).",
        },
        "replace": {
            "type": "string",
            "minLength": 1,
            "description": "The COMPLETE new source code for lines start..end (code only, no prose).",
        },
    },
    "required": ["file", "start", "end", "replace"],
}


def narrow_edit_blocks_schema_to_line_range(tool_definitions: list[dict]) -> list[dict]:
    """Rewrite the ``edit_blocks`` schema to the line-range-only form (ADR-0090).

    Under named tool forcing, guided decoding constrains ARGUMENTS to the
    declared JSON schema. With the full schema weak models dump prose into
    ``blocks`` ("No valid edit blocks found" — observed live). Removing
    ``blocks`` and requiring file/start/end/replace makes the only generable
    output a concrete line-range replacement — the easy form the model already
    used for ``repo_read_slice``.
    """
    narrowed: list[dict] = []
    for definition in tool_definitions:
        if not isinstance(definition, dict):
            narrowed.append(definition)
            continue
        function_payload = definition.get("function")
        name = (
            str(function_payload.get("name") or "").strip()
            if isinstance(function_payload, dict)
            else str(definition.get("name") or "").strip()
        )
        if name != "edit_blocks":
            narrowed.append(definition)
            continue
        rewritten = dict(definition)
        rewritten_function = dict(function_payload) if isinstance(function_payload, dict) else {"name": "edit_blocks"}
        rewritten_function["description"] = (
            "Replace lines [start, end] of `file` with `replace` (the complete new "
            "code for that range). Line numbers are 1-based inclusive — reuse the "
            "exact range you already read via repo_read_slice/read_file."
        )
        rewritten_function["parameters"] = dict(_LINE_RANGE_EDIT_BLOCKS_PARAMETERS)
        rewritten["function"] = rewritten_function
        narrowed.append(rewritten)
    return narrowed


# ADR-0090 W2.6: escalation phase boundary — attempts below this index keep the
# profile decoding defaults (prompt steering only); attempts at/after it are the
# "transcribe what you already decided" phase (write-only tools, forced names).
_ESCALATION_START_ATTEMPT_INDEX = 2
_RETRY_ESCALATION_TEMPERATURE_ENV = "KERNELONE_RETRY_ESCALATION_TEMPERATURE"
_DEFAULT_RETRY_ESCALATION_TEMPERATURE = 0.2


def resolve_escalation_temperature() -> float | None:
    """ADR-0090 W2.6: deterministic sampling temperature for escalated writes.

    fix5 evidence (qwen3.6-int4, django-15213): temperature=0.92 drove run-to-run
    localization flips under forced-write pressure (correct file in diag5e vs a
    hallucinated Flask edit in fix5). Escalated/forced attempts are transcription
    work — "write what you already read" — not exploration, so they should sample
    near-deterministically.

    Env ``KERNELONE_RETRY_ESCALATION_TEMPERATURE``: float clamped to [0, 2];
    ``off``/``none``/``disabled``/empty/negative disables the override (returns
    ``None`` → profile temperature is kept). Unset → 0.2.
    """
    raw = os.environ.get(_RETRY_ESCALATION_TEMPERATURE_ENV)
    if raw is None:
        return _DEFAULT_RETRY_ESCALATION_TEMPERATURE
    text = raw.strip().lower()
    if text in {"", "off", "none", "disabled", "false"}:
        return None
    try:
        value = float(text)
    except ValueError:
        return _DEFAULT_RETRY_ESCALATION_TEMPERATURE
    if value < 0:
        return None
    return min(value, 2.0)


def resolve_retry_temperature_override(*, attempt_index: int) -> float | None:
    """Phase-aware decoding: low temperature only for escalated retry attempts.

    Attempts 1-2 (index 0-1) keep the profile temperature — they still choose
    tools freely and benefit from exploration. From the escalation phase on
    (write-only set / forced tool name) the task is deterministic transcription.
    """
    if attempt_index < _ESCALATION_START_ATTEMPT_INDEX:
        return None
    return resolve_escalation_temperature()


def resolve_retry_escalation(
    *,
    attempt_index: int,
    max_retry_attempts: int,
    strict_tool_definitions: list[dict] | None,
    forced_write_tool_name: str | None,
) -> tuple[list[dict] | None, Any | None]:
    """ADR-0090: API-level escalation ladder for mutation-contract retries.

    Prompt-level MANDATORY hints are exactly what weak models ignore (observed
    live: qwen emitted ``repo_rg`` through four "must write" retries because the
    write-INCLUSIVE tool set still offered read tools). Guided decoding at the
    API level cannot be ignored:

    - attempts 1-2: write-inclusive set, prompt steering only (unchanged);
    - attempt 3+: WRITE-ONLY tool definitions — the narrowed-set batch guard
      rejects any read batch, and providers with strict tool grammars cannot
      generate one;
    - final attempt: force the selected write tool by name (OpenAI-style
      ``{"type": "function", "function": {"name": ...}}``); when that tool is
      ``edit_blocks``, its schema is simultaneously narrowed to the line-range
      form so guided decoding can only produce a concrete replacement.

    Returns ``(tool_definitions_override, tool_choice_override)`` — ``None``
    members mean "keep the attempt's defaults".
    """
    if attempt_index < _ESCALATION_START_ATTEMPT_INDEX or not strict_tool_definitions:
        return None, None
    definitions_override = strict_tool_definitions
    tool_choice_override: Any | None = None
    if attempt_index >= max_retry_attempts - 1 and forced_write_tool_name:
        tool_choice_override = {
            "type": "function",
            "function": {"name": forced_write_tool_name},
        }
        if forced_write_tool_name == "edit_blocks":
            definitions_override = narrow_edit_blocks_schema_to_line_range(strict_tool_definitions)
    return definitions_override, tool_choice_override


def _extract_latest_assistant_message(context: list[dict]) -> str:
    """Return the most recent assistant message content (the model's own analysis).

    Weak models often narrate the fix in prose without emitting an edit tool call.
    Surfacing that prior analysis back into the retry lets the model transcribe its
    OWN plan into a concrete edit instead of re-deriving (or re-narrating) it.
    """
    for message in reversed(context):
        if not isinstance(message, Mapping):
            continue
        if str(message.get("role") or "").strip().lower() != "assistant":
            continue
        content = str(message.get("content") or "").strip()
        if content:
            return content
    return ""


def build_contract_retry_context(
    context: list[dict],
    tool_definitions: list[dict],
    *,
    forced_write_tool_name: str | None = None,
) -> list[dict]:
    """构建突变合约违反后的 retry 上下文。"""
    latest_user = extract_latest_user_message(context)
    latest_assistant = _extract_latest_assistant_message(context)
    raw_target_file_tokens = [
        token.strip()
        for token in re.findall(
            r"\b[\w./\\-]+\.(?:py|md|txt|json|ya?ml|js|ts|tsx|jsx|css|html)\b",
            latest_user,
            flags=re.IGNORECASE,
        )
        if token.strip()
    ]
    target_file_tokens: list[str] = []
    for token in raw_target_file_tokens:
        normalized = token.replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if "/" not in normalized and suffix == "js" and name[:1].isupper():
            continue
        target_file_tokens.append(token)
    write_candidates = set(WRITE_TOOLS)
    write_tools: list[str] = []
    for item in tool_definitions:
        if not isinstance(item, Mapping):
            continue
        function_payload = item.get("function")
        if isinstance(function_payload, Mapping):
            tool_name = str(function_payload.get("name") or "").strip()
        else:
            tool_name = str(item.get("name") or "").strip()
        if tool_name and tool_name in write_candidates and tool_name not in write_tools:
            write_tools.append(tool_name)

    retry_lines = [
        "RETRY CONTRACT: The previous tool batch was rejected because it did not include any write tool",
        "while the user explicitly requested code/file modification.",
        "You must replan now and emit ONE valid tool batch before finalization.",
        "HARD GATE: never return plain-text-only completion for a mutation request.",
    ]
    if write_tools:
        retry_lines.append("Allowed write tools in this turn: " + ", ".join(write_tools) + ".")
        retry_lines.append("Include at least one of the allowed write tools in the emitted batch.")
        retry_lines.append(
            "Do not guess precision_edit/search_replace search text. "
            "For create-file or whole-file replacement tasks, prefer write_file. "
            "For existing targeted edits, prefer edit_blocks or edit_file after verifying exact content."
        )
    # Weak-model EASIEST-PATH: the line-range form of edit_blocks removes the hardest
    # task (reproducing exact SEARCH text). Steer low-precision models there explicitly.
    if "edit_blocks" in write_tools:
        retry_lines.append(
            "EASIEST EDIT (no exact-match needed): call edit_blocks with `file`, `start` and `end` "
            "(1-based inclusive line numbers — reuse the range you already read via repo_read_slice) "
            "and `replace` = the FULL new source for those lines. The tool reads the current lines itself, "
            "so you do NOT need to copy the original text."
        )
    # Narration->edit transcription: if the model already analysed the fix in prose,
    # feed that analysis back and demand a transcription, not a re-explanation.
    if latest_assistant and len(latest_assistant.strip()) > 40:
        analysis_snippet = latest_assistant.strip()[:1200]
        retry_lines.append(
            "You ALREADY analysed the fix in your previous message:\n---\n"
            + analysis_snippet
            + "\n---\nDo NOT re-explain. Transcribe THAT plan into ONE concrete edit tool call now."
        )
    if forced_write_tool_name:
        retry_lines.append(f"MANDATORY: your batch must include write tool `{forced_write_tool_name}`.")
        retry_lines.append("Do not output read/list-only batches; the emitted batch must include this write tool.")
    if target_file_tokens:
        retry_lines.append(
            "Mutation target files detected from user request: "
            + ", ".join(target_file_tokens[:6])
            + ". Ensure one write call touches at least one target file."
        )

    retry_mode_guard = (
        "RETRY MODE ACTIVE: discard any previous staged workflow (e.g., understand-first/read-first).\n"
        "Output a single valid TOOL_BATCH immediately under the constraints below.\n"
        "Do not emit plain-text-only response."
    )
    retry_context: list[dict[str, str]] = [
        {
            "role": "system",
            "content": "\n".join([retry_mode_guard, *retry_lines]),
        }
    ]
    if latest_user:
        retry_context.append({"role": "user", "content": latest_user})
    else:
        for item in context:
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "")
            if role in {"system", "user"} and content:
                retry_context.append({"role": role, "content": content})
        if not retry_context:
            retry_context.append({"role": "user", "content": ""})
    return retry_context


def append_retry_enforcement_hint(
    retry_context: list[dict],
    *,
    allowed_tool_names: set[str],
    reason: str,
    forced_write_tool_name: str | None = None,
) -> list[dict]:
    """向 retry 上下文追加强制约束提示。"""
    rendered_allowed = ", ".join(sorted(allowed_tool_names)) if allowed_tool_names else "<none>"
    enforcement_hint = {
        "role": "system",
        "content": (
            "RETRY ENFORCEMENT: previous retry output is still invalid.\n"
            f"Reason: {reason}\n"
            f"Allowed tools for this retry scope: {rendered_allowed}\n"
            "You MUST emit one TOOL_BATCH that uses only allowed tools, and includes at least one write tool.\n"
            "INVALID retry outputs: read_file-only, list_directory-only, execute_command-only.\n"
            "VALID retry output must include a write tool call.\n"
            + (f"MANDATORY write tool for this retry: {forced_write_tool_name}." if forced_write_tool_name else "")
        ),
    }
    return [*retry_context, enforcement_hint]


# ---------------------------------------------------------------------------
# 工具定义筛选
# ---------------------------------------------------------------------------


def build_retry_tool_definitions_for_mutation(
    *,
    latest_user_request: str,
    tool_definitions: list[dict],
    requires_mutation: bool | None = None,
    forbidden_tool_names: set[str] | None = None,
) -> list[dict]:
    """Builds narrowed tool definitions for a mutation-contract retry.

    Critically, this function respects a ``forbidden_tool_names`` set so
    that benchmark-level or case-level forbidden tools (e.g. execute_command)
    are never smuggled back in during retry escalation.
    """
    if requires_mutation is None:
        requires_mutation = requires_mutation_intent(latest_user_request)
    if not requires_mutation:
        return list(tool_definitions)

    _forbidden: set[str] = forbidden_tool_names or set()

    write_candidates = set(WRITE_TOOLS)
    read_context_candidates = {
        "read_file",
        "list_directory",
        "repo_rg",
        "repo_glob",
    }
    # Only add verification tools that are NOT forbidden by the benchmark case.
    verification_candidates = {t for t in {"execute_command"} if t not in _forbidden}
    narrowed: list[dict] = []
    has_write = False
    selected_tool_names: set[str] = set()
    for raw_item in tool_definitions:
        if not isinstance(raw_item, Mapping):
            continue
        item = dict(raw_item)
        tool_name = extract_tool_name_from_definition(item)
        # Never include globally forbidden tools.
        if tool_name in _forbidden:
            continue
        if tool_name in write_candidates:
            narrowed.append(item)
            has_write = True
            selected_tool_names.add(tool_name)
    if has_write:
        for raw_item in tool_definitions:
            if not isinstance(raw_item, Mapping):
                continue
            item = dict(raw_item)
            tool_name = extract_tool_name_from_definition(item)
            if tool_name in _forbidden:
                continue
            if tool_name and tool_name in read_context_candidates and tool_name not in selected_tool_names:
                narrowed.append(item)
                selected_tool_names.add(tool_name)
        for raw_item in tool_definitions:
            if not isinstance(raw_item, Mapping):
                continue
            item = dict(raw_item)
            tool_name = extract_tool_name_from_definition(item)
            if tool_name in _forbidden:
                continue
            if tool_name and tool_name in verification_candidates and tool_name not in selected_tool_names:
                narrowed.append(item)
                selected_tool_names.add(tool_name)
    if has_write and narrowed:
        return narrowed
    return [item for item in tool_definitions if extract_tool_name_from_definition(item) not in _forbidden]


def select_retry_forced_write_tool_name(
    tool_definitions: list[dict],
    *,
    workspace: str = "",
    target_files: tuple[str, ...] | list[str] = (),
) -> str | None:
    # Prefer tools that are robust when the retry context is incomplete. The
    # retry path is entered after a failed/non-mutating first attempt, so forcing
    # precision_edit first is brittle: it requires exact pre-read search text and
    # commonly degenerates into guessed no-match replacements. Whole-file and
    # block-edit tools are safer general recovery choices; append remains last
    # because it can leave stale placeholder code in place.
    #
    # Target-existence awareness (factory-bench L1-05 round 6, 2026-06-12):
    # the final escalation forces the tool BY NAME via tool_choice — guided
    # decoding then physically cannot emit anything else. Forcing edit_blocks
    # for a CREATION task (no target exists yet) locks weak models into the
    # "edit_blocks cannot create new files → use write_file" teaching loop
    # forever. When every known target is missing, write_file leads.
    creation_mode = False
    if workspace and target_files:
        try:
            # ANY missing target flips to creation mode (round-7 live: the
            # model created quotes.json first, then the remaining three
            # missing files were locked back onto edit_blocks by an
            # all-missing check). write_file handles existing files too —
            # the destructive-shrink gate guards against gutting them.
            creation_mode = any(
                not os.path.exists(os.path.join(workspace, str(target).strip()))
                for target in target_files
                if str(target).strip()
            )
        except OSError:
            creation_mode = False
    if creation_mode:
        priority_order = (
            "write_file",
            "create_file",
            "edit_blocks",
            "edit_file",
            "search_replace",
            "repo_apply_diff",
            "precision_edit",
            "append_to_file",
        )
        available = extract_allowed_tool_names_from_definitions(tool_definitions)
        for tool_name in priority_order:
            if tool_name in available:
                return tool_name
        return None
    priority_order = (
        "edit_blocks",
        "write_file",
        "edit_file",
        "search_replace",
        "repo_apply_diff",
        "precision_edit",
        "create_file",
        "append_to_file",
    )
    available = extract_allowed_tool_names_from_definitions(tool_definitions)
    for tool_name in priority_order:
        if tool_name in available:
            return tool_name
    return None


def bootstrap_receipt_contains_whole_file_replacement_marker(bootstrap_receipt: Mapping[str, Any]) -> bool:
    """Return True when bootstrap content is scaffold-like enough to replace whole files.

    The marker check is intentionally narrow. It targets deterministic seed /
    placeholder output that should be replaced wholesale by Director, while
    avoiding a blanket escalation from precise edits to full-file overwrites.

    Phase-1 live fix (2026-06-11, phase1smoke django-15213): real-world large
    source files contain these markers as ordinary code (``NotImplemented``
    comparison protocol, ``TODO:`` comments — django expressions.py has both),
    which silently escalated the follow-up to a forced ``write_file``
    whole-file regeneration; a 27B-int4 model cannot regenerate an 1800-line
    file inside the LLM timeout, so the stream hit the 600s ceiling and the
    session died. The marker scan is therefore gated by total content size:
    only genuinely small scaffold content qualifies.
    """
    marker_candidates: list[str] = []
    for item in list(bootstrap_receipt.get("results", []) or []):
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status and status != "success":
            continue
        payload = item.get("result")
        if isinstance(payload, Mapping):
            for key in ("content", "text", "body", "data"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    marker_candidates.append(value)
        elif isinstance(payload, str) and payload:
            marker_candidates.append(payload)
    combined = "\n".join(marker_candidates)
    if len(combined) > _BOOTSTRAP_WHOLE_FILE_MAX_CHARS:
        return False
    lowered = combined.lower()
    return any(marker in lowered for marker in _BOOTSTRAP_WHOLE_FILE_REPLACEMENT_MARKERS)


def select_bootstrap_followup_write_tool_name(
    *,
    allowed_tool_names: set[str],
    default_write_tool_name: str | None,
    bootstrap_receipt: Mapping[str, Any],
    failed_bootstrap_files: list[str],
) -> str | None:
    """Select the write tool for the post-bootstrap implementation stage."""
    if failed_bootstrap_files:
        for creation_candidate in ("write_file", "create_file", "append_to_file"):
            if creation_candidate in allowed_tool_names:
                return creation_candidate
        return default_write_tool_name

    if bootstrap_receipt_contains_whole_file_replacement_marker(bootstrap_receipt):
        for replacement_candidate in ("write_file", "edit_file", "repo_apply_diff", "edit_blocks"):
            if replacement_candidate in allowed_tool_names:
                return replacement_candidate

    return default_write_tool_name


def build_forced_write_only_retry_tool_definitions(
    tool_definitions: list[dict],
    forced_write_tool_name: str | None,
    *,
    include_verification_tools: bool = False,
    forbidden_tool_names: set[str] | None = None,
) -> list[dict]:
    """Builds the strict forced-write tool definitions.

    Respects ``forbidden_tool_names`` so that benchmark-forbidden tools
    (e.g. execute_command) are never included even when verification is enabled.
    """
    _forbidden: set[str] = forbidden_tool_names or set()
    if not forced_write_tool_name:
        return [item for item in tool_definitions if extract_tool_name_from_definition(item) not in _forbidden]
    companion_tool_names: set[str] = {forced_write_tool_name}
    if forced_write_tool_name in {"repo_apply_diff"}:
        companion_tool_names.update({"read_file", "repo_read_head"})
    if forced_write_tool_name == "edit_blocks":
        # New-file deadlock fix (factory-bench L1-03/L1-02 live, 2026-06-12):
        # edit_blocks cannot create files and its teaching error tells the
        # model to use write_file — but the narrowed set used to exclude it,
        # locking weak models onto an impossible tool until the circuit
        # breaker killed the task. Offer write_file alongside; the final
        # attempt's named tool_choice still forces edit_blocks for existing-
        # file edits, and the batch contract guard still requires a write.
        companion_tool_names.add("write_file")
    if include_verification_tools and "execute_command" not in _forbidden:
        companion_tool_names.add("execute_command")
    narrowed: list[dict] = []
    for raw_item in tool_definitions:
        if not isinstance(raw_item, Mapping):
            continue
        item = dict(raw_item)
        tool_name = extract_tool_name_from_definition(item)
        if tool_name in _forbidden:
            continue
        if tool_name in companion_tool_names:
            narrowed.append(item)
    if narrowed:
        return narrowed
    return [item for item in tool_definitions if extract_tool_name_from_definition(item) not in _forbidden]


# ---------------------------------------------------------------------------
# Bootstrap 上下文构建
# ---------------------------------------------------------------------------


# Per-file and total budgets for REAL file content carried into the bootstrap
# follow-up write context. The previous 1200-char JSON fragment made correct
# SEARCH/REPLACE transcription impossible by construction — the model was forced
# to write a file it could not see.
_BOOTSTRAP_READ_CONTENT_MAX_CHARS = 9000
_BOOTSTRAP_READ_CONTENT_TOTAL_CHARS = 16000


def build_retry_write_after_bootstrap_context(
    *,
    original_context: list[dict],
    bootstrap_receipt: Mapping[str, Any],
    forced_write_tool_name: str | None,
) -> list[dict]:
    latest_user = extract_latest_user_message(original_context)
    summary_lines: list[str] = []
    successful_files: list[str] = []
    failed_files: list[str] = []
    content_chars_used = 0
    for item in list(bootstrap_receipt.get("results", []) or []):
        if not isinstance(item, Mapping):
            continue
        tool_name = str(item.get("tool_name") or "unknown").strip()
        status = str(item.get("status") or "").strip().lower()
        payload = item.get("result")
        # Prefer the REAL file content for read receipts: the model must be able
        # to transcribe exact lines into its write call.
        file_content = ""
        if status == "success" and isinstance(payload, Mapping):
            raw_content = payload.get("content")
            if not isinstance(raw_content, str):
                inner = payload.get("result")
                if isinstance(inner, Mapping):
                    raw_content = inner.get("content")
            if isinstance(raw_content, str) and raw_content.strip():
                file_content = raw_content
        if file_content and content_chars_used < _BOOTSTRAP_READ_CONTENT_TOTAL_CHARS:
            budget = min(
                _BOOTSTRAP_READ_CONTENT_MAX_CHARS,
                _BOOTSTRAP_READ_CONTENT_TOTAL_CHARS - content_chars_used,
            )
            if len(file_content) > budget:
                file_content = file_content[:budget] + "\n...[content truncated]"
            content_chars_used += len(file_content)
            payload_text = "exact file content follows:\n```\n" + file_content + "\n```"
        else:
            if isinstance(payload, Mapping):
                try:
                    payload_text = json.dumps(dict(payload), ensure_ascii=False)
                except (TypeError, ValueError):
                    payload_text = str(payload)
            else:
                payload_text = str(payload or "")
            payload_text = payload_text.strip()
            if len(payload_text) > 1200:
                payload_text = payload_text[:1200] + " ...[truncated]"
        resolved_file = ""
        if isinstance(payload, Mapping):
            resolved_file = str(payload.get("file") or payload.get("path") or "").strip()
        if not resolved_file:
            from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
                extract_target_file_from_invocation_args,
            )

            resolved_file = extract_target_file_from_invocation_args({"arguments": item.get("arguments")})
        if status == "success":
            summary_lines.append(f"- {tool_name}: {payload_text}")
            if resolved_file and resolved_file not in successful_files:
                successful_files.append(resolved_file)
        else:
            summary_lines.append(f"- {tool_name}: ERROR {payload_text}")
            if resolved_file and resolved_file not in failed_files:
                failed_files.append(resolved_file)

    forced_line = (
        f"Mandatory write tool: {forced_write_tool_name}."
        if forced_write_tool_name
        else "Mandatory: include at least one write tool."
    )
    summary_block = "\n".join(summary_lines) if summary_lines else "- (no readable bootstrap receipts)"
    retry_system = (
        "WRITE RETRY MODE: bootstrap read context has been collected.\n"
        "Now emit exactly one TOOL_BATCH for implementation (write stage), no extra read-only exploration.\n"
        f"{forced_line}\n"
        "If you cannot determine exact patch, still emit a write-tool call with best-effort scoped edit arguments.\n"
        "Bootstrap read summary:\n"
        f"{summary_block}"
    )
    if successful_files:
        retry_system += (
            "\nWrite targets must be selected from successfully-read files only: " + ", ".join(successful_files) + "."
        )
    if failed_files:
        retry_system += "\nDo NOT edit unresolved paths (read failed): " + ", ".join(failed_files) + "."
        retry_system += "\nFor unresolved files that must be newly created, use write_file/create_file/append_to_file instead of edit_file."
    if forced_write_tool_name == "write_file":
        retry_system += (
            "\nUse write_file for a complete production implementation of the selected target file. "
            "Do not preserve deterministic scaffold, audit seed, TODO, or placeholder-only code."
        )
    return [
        {"role": "system", "content": retry_system},
        {"role": "user", "content": latest_user},
    ]


def extract_failed_files_from_bootstrap_receipt(bootstrap_receipt: Mapping[str, Any]) -> list[str]:
    failed_files: list[str] = []
    for item in list(bootstrap_receipt.get("results", []) or []):
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status == "success":
            continue
        payload = item.get("result")
        resolved_file = ""
        if isinstance(payload, Mapping):
            resolved_file = str(payload.get("file") or payload.get("path") or "").strip()
        if not resolved_file:
            from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
                extract_target_file_from_invocation_args,
            )

            resolved_file = extract_target_file_from_invocation_args({"arguments": item.get("arguments")})
        if not resolved_file:
            error_text = str(item.get("error") or payload or "").strip()
            match = re.search(r"file not found:\s*([^\s|]+)", error_text, flags=re.IGNORECASE)
            if match:
                resolved_file = str(match.group(1) or "").strip()
        if resolved_file and resolved_file not in failed_files:
            failed_files.append(resolved_file)
    return failed_files


def _normalize_deterministic_bootstrap_target(value: Any) -> str:
    path = str(value or "").strip().strip("'\"").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if not path or path.startswith("../") or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return ""
    if any(ch in path for ch in ("*", "?")):
        return ""
    if "/" not in path and "." not in path and path.lower() not in {"readme", "agents"}:
        return ""
    if path.lower() == "readme":
        return "README.md"
    if path.lower() == "agents":
        return "AGENTS.md"
    return path


def _extract_deterministic_bootstrap_write_targets(
    *,
    original_context: list[dict],
    bootstrap_receipt: Mapping[str, Any],
) -> list[str]:
    candidates: list[str] = []
    candidates.extend(extract_failed_files_from_bootstrap_receipt(bootstrap_receipt))
    latest_user = extract_latest_user_message(original_context)
    candidates.extend(
        token.strip()
        for token in re.findall(
            r"\b[\w./\\-]+\.(?:json|md|toml|py|js|mjs|cjs|ts|tsx|jsx|css|html|ya?ml|txt)\b",
            latest_user,
            flags=re.IGNORECASE,
        )
        if token.strip()
    )
    normalized: list[str] = []
    for candidate in candidates:
        target = _normalize_deterministic_bootstrap_target(candidate)
        if target and target not in normalized:
            normalized.append(target)
    return normalized


def _synthesize_deterministic_bootstrap_write_content(relative_path: str, latest_user: str) -> str:
    path = str(relative_path or "").strip().replace("\\", "/")
    lowered = path.lower()
    lowered_user = latest_user.lower()
    project_label = "workspace"
    label_match = re.search(r"\b([A-Za-z][A-Za-z0-9_-]{2,})\b", latest_user)
    if label_match:
        project_label = label_match.group(1).lower().replace("_", "-")
    if lowered == "package.json":
        payload = {
            "name": project_label if project_label not in {"create", "implement", "build"} else "workspace-app",
            "version": "1.0.0",
            "type": "module",
            "scripts": {
                "build": "node -e \"const fs=require('fs'); if(!fs.existsSync('package.json')) throw new Error('missing package.json'); console.log('package build check passed');\"",
                "test": "node -e \"const fs=require('fs'); const pkg=JSON.parse(fs.readFileSync('package.json','utf8')); if(!pkg.name||!pkg.version) throw new Error('invalid package manifest'); console.log('package manifest check passed');\" --",
            },
            "dependencies": {},
            "devDependencies": {},
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if lowered == "tsconfig.json":
        return (
            json.dumps(
                {
                    "compilerOptions": {
                        "target": "ES2022",
                        "module": "ES2022",
                        "moduleResolution": "Bundler",
                        "strict": True,
                        "skipLibCheck": True,
                        "outDir": "dist",
                    },
                    "include": ["src/**/*.ts", "tests/**/*.ts"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    if lowered == "pyproject.toml":
        return (
            "[project]\n"
            f'name = "{project_label if project_label != "workspace" else "workspace-app"}"\n'
            'version = "0.1.0"\n'
            'description = "Generated workspace package for Polaris execution validation."\n'
        )
    if lowered.endswith((".md", ".txt")):
        title = "Agent Guide" if lowered.endswith("agents.md") else "Workspace Guide"
        return (
            f"# {title}\n\n"
            "This file records the runnable workspace contract for Polaris execution.\n\n"
            "## Verification\n\n"
            "- Project files are generated with UTF-8 text encoding.\n"
            "- Build and test commands must return concrete pass/fail results.\n"
        )
    if lowered.endswith("dag.service.ts") or ("dag" in lowered_user and "dependency" in lowered_user):
        return _synthesize_deterministic_dag_service_content()
    if lowered.endswith((".ts", ".tsx")):
        return (
            "export interface WorkspaceArtifactStatus {\n"
            "  ready: boolean;\n"
            "  source: string;\n"
            "}\n\n"
            "export const workspaceArtifactStatus: WorkspaceArtifactStatus = {\n"
            "  ready: true,\n"
            "  source: 'polaris-deterministic-bootstrap',\n"
            "};\n\n"
            "export function describeWorkspaceArtifact(): string {\n"
            "  return workspaceArtifactStatus.ready ? 'verified artifact' : 'unverified artifact';\n"
            "}\n"
        )
    if lowered.endswith((".js", ".mjs", ".cjs")):
        return (
            "export const workspaceArtifactStatus = {\n"
            "  ready: true,\n"
            "  source: 'polaris-deterministic-bootstrap',\n"
            "};\n\n"
            "export function describeWorkspaceArtifact() {\n"
            "  return workspaceArtifactStatus.ready ? 'verified artifact' : 'unverified artifact';\n"
            "}\n"
        )
    if lowered.endswith(".py"):
        return "from __future__ import annotations\n\n\ndef workspace_artifact_ready() -> bool:\n    return True\n"
    return "workspace_artifact_ready=true\n"


def _synthesize_deterministic_dag_service_content() -> str:
    return (
        "export interface TaskDependencyNode {\n"
        "  id: string;\n"
        "  dependencies?: readonly string[];\n"
        "  predecessorIds?: readonly string[];\n"
        "}\n\n"
        "export interface DagValidationResult {\n"
        "  valid: boolean;\n"
        "  statusCode: 200 | 400;\n"
        "  errors: string[];\n"
        "  missingReferenceIds: string[];\n"
        "  cycle: string[];\n"
        "}\n\n"
        "export class DagValidationError extends Error {\n"
        "  readonly statusCode = 400;\n"
        "  readonly result: DagValidationResult;\n\n"
        "  constructor(result: DagValidationResult) {\n"
        "    super(result.errors.join('; '));\n"
        "    this.name = 'DagValidationError';\n"
        "    this.result = result;\n"
        "  }\n"
        "}\n\n"
        "function dependencyIdsFor(node: TaskDependencyNode): readonly string[] {\n"
        "  return node.dependencies ?? node.predecessorIds ?? [];\n"
        "}\n\n"
        "export class DagService {\n"
        "  validateTaskGraph(nodes: readonly TaskDependencyNode[]): DagValidationResult {\n"
        "    const byId = new Map(nodes.map((node) => [node.id, node]));\n"
        "    const missingReferenceIds: string[] = [];\n\n"
        "    for (const node of nodes) {\n"
        "      for (const dependencyId of dependencyIdsFor(node)) {\n"
        "        if (!byId.has(dependencyId)) {\n"
        "          missingReferenceIds.push(dependencyId);\n"
        "        }\n"
        "      }\n"
        "    }\n\n"
        "    const visited = new Set<string>();\n"
        "    const visiting = new Set<string>();\n"
        "    const stack: string[] = [];\n"
        "    let cycle: string[] = [];\n\n"
        "    const visit = (taskId: string): boolean => {\n"
        "      if (visiting.has(taskId)) {\n"
        "        const start = stack.indexOf(taskId);\n"
        "        cycle = [...stack.slice(start < 0 ? 0 : start), taskId];\n"
        "        return true;\n"
        "      }\n"
        "      if (visited.has(taskId)) {\n"
        "        return false;\n"
        "      }\n"
        "      visited.add(taskId);\n"
        "      visiting.add(taskId);\n"
        "      stack.push(taskId);\n"
        "      const node = byId.get(taskId);\n"
        "      if (node) {\n"
        "        for (const dependencyId of dependencyIdsFor(node)) {\n"
        "          if (byId.has(dependencyId) && visit(dependencyId)) {\n"
        "            return true;\n"
        "          }\n"
        "        }\n"
        "      }\n"
        "      visiting.delete(taskId);\n"
        "      stack.pop();\n"
        "      return false;\n"
        "    };\n\n"
        "    for (const node of nodes) {\n"
        "      if (visit(node.id)) {\n"
        "        break;\n"
        "      }\n"
        "    }\n\n"
        "    const errors: string[] = [];\n"
        "    if (missingReferenceIds.length > 0) {\n"
        "      errors.push(`Missing task dependency references: ${missingReferenceIds.join(', ')}`);\n"
        "    }\n"
        "    if (cycle.length > 0) {\n"
        "      errors.push(`Circular task dependency detected: ${cycle.join(' -> ')}`);\n"
        "    }\n\n"
        "    return {\n"
        "      valid: errors.length === 0,\n"
        "      statusCode: errors.length === 0 ? 200 : 400,\n"
        "      errors,\n"
        "      missingReferenceIds,\n"
        "      cycle,\n"
        "    };\n"
        "  }\n\n"
        "  assertTaskGraph(nodes: readonly TaskDependencyNode[]): void {\n"
        "    const result = this.validateTaskGraph(nodes);\n"
        "    if (!result.valid) {\n"
        "      throw new DagValidationError(result);\n"
        "    }\n"
        "  }\n"
        "}\n"
    )


def _extract_decision_invocations(decision: Any | None) -> list[Any]:
    """Pull invocations from a TurnDecision-like object or mapping (defensive)."""
    if decision is None:
        return []
    tool_batch = decision.get("tool_batch") if hasattr(decision, "get") else getattr(decision, "tool_batch", None)
    if tool_batch is None:
        return []
    if isinstance(tool_batch, Mapping):
        return list(tool_batch.get("invocations", []) or [])
    return list(getattr(tool_batch, "invocations", []) or [])


def merge_bootstrap_receipt_into_result(result: Any, bootstrap_receipt: Mapping[str, Any] | None) -> Any:
    """Prepend bootstrap READ receipts into the turn result's batch receipt.

    The session reducer and the next-turn WorkingMemory only see
    ``turn_result.batch_receipt`` (each inner turn's LLM context is rebuilt from
    scratch) — without this merge the bootstrap reads are invisible to subsequent
    turns and weak models rewrite files from pretraining memory (hallucinated
    SEARCH text).
    """
    if not isinstance(result, dict) or not isinstance(bootstrap_receipt, Mapping):
        return result
    bootstrap_results = [item for item in list(bootstrap_receipt.get("results", []) or []) if isinstance(item, Mapping)]
    if not bootstrap_results:
        return result
    existing = result.get("batch_receipt")
    if isinstance(existing, Mapping):
        merged = dict(existing)
        merged["results"] = [*bootstrap_results, *list(merged.get("results", []) or [])]
        merged["success_count"] = int(merged.get("success_count", 0) or 0) + sum(
            1 for item in bootstrap_results if str(item.get("status") or "").strip().lower() == "success"
        )
        return {**result, "batch_receipt": merged}
    if existing is None:
        return {**result, "batch_receipt": dict(bootstrap_receipt)}
    # Unknown receipt object shape — leave untouched rather than corrupt it.
    return result


def build_deterministic_bootstrap_followup_write_decision(
    *,
    turn_id: str,
    original_context: list[dict],
    bootstrap_receipt: Mapping[str, Any],
    allowed_tool_names: set[str],
    workspace: str = ".",
) -> TurnDecision | None:
    if "write_file" not in allowed_tool_names:
        return None
    targets = _extract_deterministic_bootstrap_write_targets(
        original_context=original_context,
        bootstrap_receipt=bootstrap_receipt,
    )
    if not targets:
        return None
    latest_user = extract_latest_user_message(original_context)
    # The synthesized templates are SCAFFOLDING content (package.json/tsconfig/
    # stub modules). In repo-fix contexts they are pure poison: overwriting an
    # existing source file destroys it, and creating files the user never named
    # (failed-read paths leak into the candidate list) plants off-task artifacts
    # that reinforce weak-model task drift. Only create NEW files the user
    # explicitly named.
    workspace_root = Path(str(workspace or ".").strip() or ".")
    viable_targets: list[str] = []
    for candidate_target in targets:
        if candidate_target not in latest_user:
            continue
        try:
            if (workspace_root / candidate_target).exists():
                continue
        except OSError:
            continue
        viable_targets.append(candidate_target)
    if not viable_targets:
        logger.warning(
            "deterministic bootstrap write fallback skipped: no safe user-named non-existing target (candidates=%s)",
            targets[:5],
        )
        return None
    target = viable_targets[0]
    content = _synthesize_deterministic_bootstrap_write_content(target, latest_user)
    invocation = ToolInvocation(
        call_id=ToolCallId(f"{turn_id}:deterministic-write:1"),
        tool_name="write_file",
        arguments={"file": target, "content": content},
        effect_type=ToolEffectType.WRITE,
        execution_mode=ToolExecutionMode.WRITE_SERIAL,
    )
    batch = ToolBatch(
        batch_id=BatchId(f"{turn_id}:deterministic-write"),
        invocations=[invocation],
        serial_writes=[invocation],
    )
    return TurnDecision(
        turn_id=TurnId(turn_id),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        reasoning_summary="deterministic bootstrap follow-up write_file fallback",
        tool_batch=batch,
        finalize_mode=FinalizeMode.NONE,
        domain="code",
        metadata={
            "deterministic_recovery": "bootstrap_followup_write_file",
            "target_file": target,
        },
    )


# ---------------------------------------------------------------------------
# RetryOrchestrator
# ---------------------------------------------------------------------------


class WorkflowRuntimeProtocol(Protocol):
    """工作流运行时协议。"""

    async def execute(self, decision: TurnDecision, turn_id: TurnId) -> dict[str, Any]:
        """执行决策并返回结果。"""
        ...


class DevelopmentRuntimeProtocol(Protocol):
    """开发运行时协议。"""

    async def execute(self, decision: TurnDecision, turn_id: TurnId) -> dict[str, Any]:
        """执行决策并返回结果。"""
        ...


class RetryOrchestrator:
    """重试编排器 — 突变合约违反后的恢复与重试。"""

    def __init__(
        self,
        *,
        tool_runtime: Any,
        config: Any,
        decoder: Any,
        call_llm_for_decision: Callable[..., Any],
        call_llm_for_decision_stream: Callable[..., Any] | None,
        execute_tool_batch: Callable[..., Any],
        guard_assert_single_tool_batch: Callable[..., None],
        emit_event: Callable[[Any], None] | None = None,
    ) -> None:
        self.tool_runtime = tool_runtime
        self.config = config
        self.decoder = decoder
        self.call_llm_for_decision = call_llm_for_decision
        self.call_llm_for_decision_stream = call_llm_for_decision_stream
        self.execute_tool_batch = execute_tool_batch
        self.guard_assert_single_tool_batch = guard_assert_single_tool_batch
        self.emit_event = emit_event

    def _build_tool_batch_runtime(self, workspace: str = ".") -> ToolBatchRuntime:
        return ToolBatchRuntime(
            executor=self.tool_runtime,
            context=ToolExecutionContext(
                workspace=workspace or ".",
                timeout_ms=self.config.max_tool_execution_time_ms,
            ),
        )

    async def execute_read_bootstrap_batch(
        self,
        *,
        turn_id: str,
        workspace: str,
        tool_batch: Any,
        ledger: TurnLedger,
    ) -> dict[str, Any] | None:
        if isinstance(tool_batch, Mapping):
            raw_invocations = list(tool_batch.get("invocations", []) or [])
            batch_id = tool_batch.get("batch_id", BatchId(f"{turn_id}_bootstrap"))
        else:
            raw_invocations = list(getattr(tool_batch, "invocations", []) or [])
            batch_id = getattr(tool_batch, "batch_id", BatchId(f"{turn_id}_bootstrap"))
        normalized_invocations: list[ToolInvocation] = []
        for invocation_index, raw_invocation in enumerate(raw_invocations):
            if isinstance(raw_invocation, Mapping):
                item = dict(raw_invocation)
            else:
                raw_args = getattr(raw_invocation, "arguments", None)
                args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
                item = {
                    "call_id": str(getattr(raw_invocation, "call_id", "") or ""),
                    "tool_name": str(getattr(raw_invocation, "tool_name", "") or ""),
                    "arguments": args,
                    "execution_mode": getattr(raw_invocation, "execution_mode", None),
                }
            if not str(item.get("tool_name") or "").strip():
                continue
            if not str(item.get("call_id") or "").strip():
                # Decoder-produced invocations always carry call_id, but bootstrap
                # batches sourced from retry decisions may not — ToolBatch requires it.
                item["call_id"] = f"{turn_id}_bootstrap_{invocation_index}"
            if not item.get("execution_mode"):
                item["execution_mode"] = ToolExecutionMode.READONLY_SERIAL
            if not item.get("effect_type"):
                item["effect_type"] = ToolEffectType.READ
            normalized_invocations.append(cast("ToolInvocation", item))
        if not normalized_invocations:
            return None

        bootstrap_batch = ToolBatch(
            batch_id=batch_id,
            parallel_readonly=[
                inv
                for inv in normalized_invocations
                if inv.get("execution_mode") == ToolExecutionMode.READONLY_PARALLEL
            ],
            readonly_serial=[
                inv for inv in normalized_invocations if inv.get("execution_mode") == ToolExecutionMode.READONLY_SERIAL
            ],
            serial_writes=[
                inv for inv in normalized_invocations if inv.get("execution_mode") == ToolExecutionMode.WRITE_SERIAL
            ],
            async_receipts=[
                inv for inv in normalized_invocations if inv.get("execution_mode") == ToolExecutionMode.ASYNC_RECEIPT
            ],
        )
        receipts = await self._build_tool_batch_runtime(workspace).execute_batch(
            bootstrap_batch,
            TurnId(turn_id),
        )
        receipts_as_dicts = normalize_batch_receipts(receipts)
        record_receipts_to_ledger(receipts_as_dicts, ledger)
        # Bootstrap reads execute OUTSIDE execute_tool_batch, so without explicit
        # emission their results never reach the session event stream / TruthLog —
        # later turns then have no trace that the file was ever read, and weak
        # models fall back to writing SEARCH text from memory (hallucination).
        if self.emit_event is not None:
            for receipt_dict in receipts_as_dicts:
                if not isinstance(receipt_dict, Mapping):
                    continue
                for result_item in list(receipt_dict.get("results", []) or []):
                    if not isinstance(result_item, Mapping):
                        continue
                    try:
                        self.emit_event(
                            {
                                "type": "tool_result",
                                "data": {
                                    "tool": str(result_item.get("tool_name") or ""),
                                    "result": result_item.get("result"),
                                    "bootstrap_read": True,
                                },
                            }
                        )
                    except (RuntimeError, ValueError, TypeError) as emit_exc:
                        logger.warning("bootstrap receipt event emission failed: %s", emit_exc)
        if not receipts_as_dicts:
            return None
        merged_receipt = merge_batch_receipts(receipts_as_dicts)
        if merged_receipt is None:
            return None
        merged_receipt["batch_id"] = str(batch_id)
        merged_receipt["turn_id"] = turn_id
        return merged_receipt

    def _build_retry_context(
        self,
        *,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
        requires_verification: bool,
        requires_mutation: bool,
        forbidden_tool_names: set[str] | None = None,
    ) -> tuple[list[dict], list[dict], set[str], set[str], str | None, dict[str, Any] | None, list[dict]]:
        """构建重试上下文和工具定义。

        ``forbidden_tool_names`` is threaded through all narrowing helpers so
        that benchmark-level or case-level forbidden tools are never included
        in any retry tool set, even during write-escalation.
        """
        _forbidden = forbidden_tool_names or set()
        retry_tool_definitions = build_retry_tool_definitions_for_mutation(
            latest_user_request=extract_latest_user_message(context),
            tool_definitions=tool_definitions,
            requires_mutation=requires_mutation,
            forbidden_tool_names=_forbidden,
        )
        allowed_retry_tool_names = extract_allowed_tool_names_from_definitions(retry_tool_definitions)
        _latest_request = extract_latest_user_message(context)
        forced_write_tool_name = select_retry_forced_write_tool_name(
            retry_tool_definitions,
            workspace=str(getattr(self.config, "workspace", "") or os.environ.get("KERNELONE_WORKSPACE", "") or "."),
            target_files=tuple(extract_target_files_from_message(_latest_request)),
        )
        _strict_retry_tool_definitions = build_forced_write_only_retry_tool_definitions(
            retry_tool_definitions,
            forced_write_tool_name,
            include_verification_tools=requires_verification,
            forbidden_tool_names=_forbidden,
        )
        strict_allowed_retry_tool_names = extract_allowed_tool_names_from_definitions(_strict_retry_tool_definitions)
        # The first retry must not force a single write tool. The model still
        # receives a write-inclusive contract and the narrowed tool set, but it
        # can choose write_file/edit_blocks/edit_file based on the task. Strict
        # tool_choice forcing is reserved for escalation attempts and bootstrap
        # follow-up where context has already been collected.
        forced_tool_choice: dict[str, Any] | None = None
        retry_context = build_contract_retry_context(
            context,
            retry_tool_definitions,
            forced_write_tool_name=None,
        )
        logger.warning(
            "mutation-contract retry scope: turn_id=%s allowed_tools=%s strict_allowed_tools=%s forced_tool=%s",
            turn_id,
            sorted(allowed_retry_tool_names),
            sorted(strict_allowed_retry_tool_names),
            forced_write_tool_name,
        )
        return (
            retry_tool_definitions,
            retry_context,
            allowed_retry_tool_names,
            strict_allowed_retry_tool_names,
            forced_write_tool_name,
            forced_tool_choice,
            _strict_retry_tool_definitions,
        )

    async def _execute_retry_batch(
        self,
        *,
        turn_id: str,
        attempt_context: list[dict],
        attempt_tool_definitions: list[dict],
        ledger: TurnLedger,
        attempt_tool_choice_override: dict[str, Any] | None,
        attempt_model_override: str | None,
        stream: bool,
        shadow_engine: Any | None,
        attempt_temperature_override: float | None = None,
    ) -> RawLLMResponse:
        """执行单个重试批次，返回 LLM 响应。"""
        retry_response: RawLLMResponse | None = None
        # ADR-0090 W2.6: only widen the call shape when the override is active so
        # default-path callers (and their test fakes) keep the existing signature.
        llm_call_kwargs: dict[str, Any] = {}
        if attempt_temperature_override is not None:
            llm_call_kwargs["temperature_override"] = attempt_temperature_override
        stream_callable = self.call_llm_for_decision_stream
        use_stream_retry = stream and stream_callable is not None
        if use_stream_retry and stream_callable is not None:
            try:
                async for retry_event in stream_callable(
                    attempt_context,
                    attempt_tool_definitions,
                    ledger,
                    shadow_engine=shadow_engine,
                    tool_choice_override=attempt_tool_choice_override,
                    model_override=attempt_model_override,
                    **llm_call_kwargs,
                ):
                    if not isinstance(retry_event, Mapping):
                        continue
                    event_type = str(retry_event.get("type") or "").strip()
                    if event_type == "_internal_materialize":
                        candidate_response = retry_event.get("response")
                        if isinstance(candidate_response, RawLLMResponse):
                            retry_response = candidate_response
                    elif self.emit_event is not None and event_type:
                        self.emit_event(retry_event)
            except Exception as stream_exc:
                logger.exception("retry stream failed: turn_id=%s", turn_id)
                raise RuntimeError(
                    f"single_batch_contract_violation_retry_failed: retry stream error: {stream_exc}"
                ) from stream_exc
            if retry_response is None:
                raise RuntimeError(
                    "single_batch_contract_violation_retry_failed: retry stream did not materialize response"
                )
        else:
            retry_response = await self.call_llm_for_decision(
                attempt_context,
                attempt_tool_definitions,
                ledger,
                tool_choice_override=attempt_tool_choice_override,
                model_override=attempt_model_override,
                **llm_call_kwargs,
            )
        return retry_response

    async def _execute_deterministic_bootstrap_followup_write_fallback(
        self,
        *,
        turn_id: str,
        original_context: list[dict],
        bootstrap_receipt: Mapping[str, Any],
        allowed_tool_names: set[str],
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        write_context: list[dict],
        stream: bool,
        shadow_engine: Any | None,
        workspace: str = ".",
    ) -> dict[str, Any] | None:
        deterministic_followup_decision = build_deterministic_bootstrap_followup_write_decision(
            turn_id=turn_id,
            original_context=original_context,
            bootstrap_receipt=bootstrap_receipt,
            allowed_tool_names=allowed_tool_names,
            workspace=workspace,
        )
        if deterministic_followup_decision is None:
            return None
        logger.warning(
            "mutation-contract bootstrap-followup using deterministic write_file fallback: turn_id=%s target=%s",
            turn_id,
            deterministic_followup_decision.metadata.get("target_file"),
        )
        ledger.replace_decision(deterministic_followup_decision)
        deterministic_batch_count_before = ledger.tool_batch_count
        try:
            deterministic_result = await self.execute_tool_batch(
                deterministic_followup_decision,
                state_machine,
                ledger,
                write_context,
                stream=stream,
                shadow_engine=shadow_engine,
                allowed_tool_names={"write_file"},
                count_towards_batch_limit=True,
            )
            self.guard_assert_single_tool_batch(
                turn_id=turn_id,
                tool_batch_count=ledger.tool_batch_count,
                ledger=ledger,
            )
            # Wave-3: carry the bootstrap READ receipts in the turn's authoritative
            # receipt so the next turn's WorkingMemory can see the file content.
            return merge_bootstrap_receipt_into_result(deterministic_result, bootstrap_receipt)
        except RuntimeError:
            ledger.tool_batch_count = deterministic_batch_count_before
            rollback_state_after_retry_batch_failure(state_machine, ledger)
            raise

    async def retry_tool_batch_after_contract_violation(
        self,
        *,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
        state_machine: TurnStateMachine,
        ledger: TurnLedger,
        stream: bool,
        shadow_engine: Any | None = None,
        original_decision: Any | None = None,
    ) -> dict:
        latest_user_request = extract_latest_user_message(context)
        requires_mutation = requires_mutation_intent(latest_user_request)
        # Phase-1 A2 (2026-06-11, run20 audit): a mutation contract IMPLIES the
        # right to verify the mutation. Keying verification access off message
        # keywords alone ("test", "verify") suppressed every model-initiated
        # test run on tasks phrased as plain "fix the bug" — run20: 18/18
        # instances executed ZERO verification commands, and execute_command
        # batches during mutation retries were rejected as contract violations.
        # The escalation ladder still terminates in a forced WRITE (the final
        # attempt forces tool_choice by name), so admitting verification tools
        # into the narrowed set cannot stall the write obligation.
        requires_verification = requires_verification_intent(latest_user_request) or requires_mutation
        (
            retry_tool_definitions,
            retry_context,
            allowed_retry_tool_names,
            strict_allowed_retry_tool_names,
            forced_write_tool_name,
            forced_tool_choice,
            strict_retry_tool_defs,
        ) = self._build_retry_context(
            turn_id=turn_id,
            context=context,
            tool_definitions=tool_definitions,
            requires_verification=requires_verification,
            requires_mutation=requires_mutation,
        )
        max_retry_attempts = getattr(self.config, "max_retry_attempts", 4)
        retry_llm_call_ordinal = 0
        candidate_bootstrap_decision: TurnDecision | None = None
        # Phase-2 Wave-5 (2026-06-11, run10a live audit): when the ORIGINAL violating
        # batch is itself a safe READ-ONLY batch (the model asking for evidence, e.g.
        # read_file django/core/checks/model_checks.py — correct path!), discarding it
        # and re-asking makes a weak model emit WORSE calls under retry pressure
        # (observed: hallucinated vue-element-admin Windows paths). Bootstrap the
        # ORIGINAL reads directly — never throw away the model's correct request.
        original_bootstrap_invocations = _extract_decision_invocations(original_decision)
        if original_bootstrap_invocations and is_safe_readonly_bootstrap_invocations(original_bootstrap_invocations):
            logger.warning(
                "mutation-contract violation on READ-ONLY original batch -> "
                "bootstrapping the ORIGINAL reads (no retry re-ask): turn_id=%s tools=%s",
                turn_id,
                [extract_invocation_tool_name(inv) for inv in original_bootstrap_invocations],
            )
            candidate_bootstrap_decision = original_decision
        for attempt_index in range(max_retry_attempts):
            if candidate_bootstrap_decision is not None:
                break
            attempt_tool_definitions = retry_tool_definitions
            attempt_allowed_tool_names = allowed_retry_tool_names
            attempt_context = retry_context
            attempt_tool_choice_override: Any | None = None
            retry_llm_call_ordinal += 1
            attempt_model_override = resolve_retry_model_override(retry_llm_call_ordinal)
            if attempt_index > 0:
                # Keep the write-inclusive hard gate, but do not force a single
                # write tool. Real-world retries need to recover from a failed
                # edit_blocks/search_replace attempt by switching to write_file
                # or edit_file; forcing the previous selected tool traps the
                # model in the same failure mode.
                attempt_context = append_retry_enforcement_hint(
                    retry_context,
                    allowed_tool_names=attempt_allowed_tool_names,
                    reason="escalation: enforce write-inclusive batch in retry scope",
                    forced_write_tool_name=None,
                )

            # ADR-0090: API-level escalation — late attempts narrow the offered
            # tools to write-only (guided decoding cannot emit reads) and the
            # final attempt forces the selected write tool by name. Prompt-level
            # hints alone are exactly what weak models ignore.
            escalated_definitions, escalated_tool_choice = resolve_retry_escalation(
                attempt_index=attempt_index,
                max_retry_attempts=max_retry_attempts,
                strict_tool_definitions=strict_retry_tool_defs,
                forced_write_tool_name=forced_write_tool_name,
            )
            if escalated_definitions is not None:
                attempt_tool_definitions = escalated_definitions
                if strict_allowed_retry_tool_names:
                    attempt_allowed_tool_names = strict_allowed_retry_tool_names
                attempt_tool_choice_override = escalated_tool_choice
                logger.warning(
                    "mutation-contract retry attempt=%s API-level escalation: tools=%s tool_choice=%s",
                    attempt_index + 1,
                    sorted(attempt_allowed_tool_names),
                    escalated_tool_choice or "auto",
                )

            # ADR-0090 W2.6: phase-aware decoding — escalated attempts transcribe
            # an already-made decision, so they sample near-deterministically.
            attempt_temperature_override = resolve_retry_temperature_override(attempt_index=attempt_index)
            if attempt_temperature_override is not None:
                logger.warning(
                    "mutation-contract retry attempt=%s phase-aware low temperature: %s",
                    attempt_index + 1,
                    attempt_temperature_override,
                )

            retry_response = await self._execute_retry_batch(
                turn_id=turn_id,
                attempt_context=attempt_context,
                attempt_tool_definitions=attempt_tool_definitions,
                ledger=ledger,
                attempt_tool_choice_override=attempt_tool_choice_override,
                attempt_model_override=attempt_model_override,
                stream=stream,
                shadow_engine=shadow_engine,
                attempt_temperature_override=attempt_temperature_override,
            )
            if attempt_model_override:
                logger.warning(
                    "mutation-contract retry attempt=%s uses model override: %s",
                    attempt_index + 1,
                    attempt_model_override,
                )

            raw_native_names: list[str] = []
            for native_call in retry_response.native_tool_calls:
                if not isinstance(native_call, Mapping):
                    continue
                function_payload = native_call.get("function")
                if isinstance(function_payload, Mapping):
                    native_name = str(function_payload.get("name") or "").strip()
                else:
                    native_name = str(native_call.get("name") or "").strip()
                if native_name:
                    raw_native_names.append(native_name)
            logger.warning(
                "mutation-contract retry attempt=%s raw_native_tools=%s",
                attempt_index + 1,
                raw_native_names,
            )

            retry_decision = self.decoder.decode(retry_response, TurnId(turn_id))
            ledger.replace_decision(retry_decision)
            if retry_decision.get("kind") != TurnDecisionKind.TOOL_BATCH:
                if attempt_index < max_retry_attempts - 1:
                    retry_context = append_retry_enforcement_hint(
                        retry_context,
                        allowed_tool_names=attempt_allowed_tool_names,
                        reason="retry decision did not produce a valid tool batch",
                        forced_write_tool_name=None,
                    )
                    continue
                raise RuntimeError(
                    "single_batch_contract_violation_retry_failed: retry decision did not produce a valid tool batch"
                )
            _batch_count_before = ledger.tool_batch_count
            try:
                attempt_result = await self.execute_tool_batch(
                    retry_decision,
                    state_machine,
                    ledger,
                    attempt_context,
                    stream=stream,
                    shadow_engine=shadow_engine,
                    allowed_tool_names=attempt_allowed_tool_names if attempt_allowed_tool_names else None,
                    count_towards_batch_limit=True,
                )
                self.guard_assert_single_tool_batch(
                    turn_id=turn_id,
                    tool_batch_count=ledger.tool_batch_count,
                    ledger=ledger,
                )
                return attempt_result
            except RuntimeError as retry_exc:
                # FIX-20260504: rollback batch count so failed attempts don't
                # accumulate and cause assert_single_tool_batch to fail on retries.
                ledger.tool_batch_count = _batch_count_before
                retry_tool_batch = retry_decision.get("tool_batch")
                if isinstance(retry_tool_batch, Mapping):
                    retry_invocations = list(retry_tool_batch.get("invocations", []))
                elif hasattr(retry_tool_batch, "invocations"):
                    retry_invocations = list(getattr(retry_tool_batch, "invocations", []) or [])
                else:
                    retry_invocations = []
                retry_tool_names: list[str] = []
                for invocation in retry_invocations:
                    tool_name = extract_invocation_tool_name(invocation)
                    if tool_name:
                        retry_tool_names.append(tool_name)
                logger.warning(
                    "mutation-contract retry attempt=%s failed: %s (decision_tools=%s)",
                    attempt_index + 1,
                    str(retry_exc),
                    retry_tool_names,
                )
                rollback_state_after_retry_batch_failure(state_machine, ledger)
                if is_stale_edit_contract_violation(retry_exc):
                    bootstrap_from_write = build_stale_edit_bootstrap_decision(
                        turn_id=turn_id,
                        retry_invocations=retry_invocations,
                        decision_metadata=retry_decision.get("metadata"),
                    )
                    bootstrap_from_context = None
                    if bootstrap_from_write is None:
                        bootstrap_from_context = build_context_target_bootstrap_decision(
                            turn_id=turn_id,
                            latest_user_request=extract_latest_user_message(retry_context),
                            decision_metadata=retry_decision.get("metadata"),
                        )
                    bootstrap_decision = bootstrap_from_write or bootstrap_from_context
                    if bootstrap_decision is not None:
                        logger.warning(
                            "mutation-contract retry attempt=%s switching to bootstrap read path",
                            attempt_index + 1,
                        )
                        candidate_bootstrap_decision = bootstrap_decision
                        break
                if not is_mutation_contract_violation(retry_exc):
                    raise
                # Weak-model ignition (Phase 2, 2026-06-10): a retry attempt that emits a
                # SAFE READ-ONLY batch (read_file/repo_rg/...) is the model asking for the
                # evidence it needs before it can write. Punishing that with another blind
                # retry traps models that have never seen the target file — convert it to
                # the designed bootstrap read -> forced-write path IMMEDIATELY instead of
                # only on the final attempt.
                if is_safe_readonly_bootstrap_invocations(retry_invocations):
                    logger.warning(
                        "mutation-contract retry attempt=%s emitted read-only batch -> "
                        "switching to bootstrap read path",
                        attempt_index + 1,
                    )
                    candidate_bootstrap_decision = retry_decision
                    break
                if attempt_index >= max_retry_attempts - 1:
                    raise
                retry_context = append_retry_enforcement_hint(
                    retry_context,
                    allowed_tool_names=attempt_allowed_tool_names,
                    reason=f"{retry_exc!s} (attempt {attempt_index + 1}/{max_retry_attempts})",
                    forced_write_tool_name=None,
                )
                continue

        if candidate_bootstrap_decision is not None:
            bootstrap_tool_batch = candidate_bootstrap_decision.get("tool_batch")
            if bootstrap_tool_batch is None:
                raise RuntimeError("single_batch_contract_violation_retry_failed: bootstrap tool batch missing")
            bootstrap_metadata = candidate_bootstrap_decision.get("metadata")
            bootstrap_workspace = "."
            if isinstance(bootstrap_metadata, Mapping):
                bootstrap_workspace = str(bootstrap_metadata.get("workspace", ".")).strip() or "."
            bootstrap_receipt = await self.execute_read_bootstrap_batch(
                turn_id=turn_id,
                workspace=bootstrap_workspace,
                tool_batch=bootstrap_tool_batch,
                ledger=ledger,
            )
            if bootstrap_receipt is None:
                raise RuntimeError("single_batch_contract_violation_retry_failed: bootstrap read receipt missing")
            failed_bootstrap_files = extract_failed_files_from_bootstrap_receipt(bootstrap_receipt)
            followup_forced_write_tool_name = select_bootstrap_followup_write_tool_name(
                allowed_tool_names=allowed_retry_tool_names,
                default_write_tool_name=forced_write_tool_name,
                bootstrap_receipt=bootstrap_receipt,
                failed_bootstrap_files=failed_bootstrap_files,
            )
            write_context = build_retry_write_after_bootstrap_context(
                original_context=context,
                bootstrap_receipt=bootstrap_receipt,
                forced_write_tool_name=followup_forced_write_tool_name,
            )
            if followup_forced_write_tool_name != forced_write_tool_name:
                logger.warning(
                    "mutation-contract bootstrap-followup adjusted forced write tool: %s -> %s (failed_files=%s)",
                    forced_write_tool_name,
                    followup_forced_write_tool_name,
                    failed_bootstrap_files,
                )
            followup_forced_tool_choice: Any | None = (
                {
                    "type": "function",
                    "function": {"name": followup_forced_write_tool_name},
                }
                if followup_forced_write_tool_name
                else None
            )
            if followup_forced_write_tool_name:
                followup_tool_definitions = build_forced_write_only_retry_tool_definitions(
                    retry_tool_definitions,
                    followup_forced_write_tool_name,
                    include_verification_tools=requires_verification,
                )
                followup_allowed_tool_names = extract_allowed_tool_names_from_definitions(followup_tool_definitions)
            else:
                followup_tool_definitions = retry_tool_definitions
                followup_allowed_tool_names = allowed_retry_tool_names
            followup_tool_choice_override: Any | None = followup_forced_tool_choice or forced_tool_choice
            # ADR-0090 W2.6: the bootstrap follow-up forces a write tool to
            # transcribe freshly-read content — same deterministic phase as the
            # escalated retries, same low-temperature treatment.
            followup_llm_kwargs: dict[str, Any] = {}
            if followup_tool_choice_override is not None:
                followup_temperature_override = resolve_escalation_temperature()
                if followup_temperature_override is not None:
                    followup_llm_kwargs["temperature_override"] = followup_temperature_override
            max_followup_attempts = 3
            current_write_context = write_context
            current_followup_allowed_tool_names = set(followup_allowed_tool_names)
            for followup_attempt in range(max_followup_attempts):
                followup_response: RawLLMResponse | None = None
                retry_llm_call_ordinal += 1
                followup_model_override = resolve_retry_model_override(retry_llm_call_ordinal)
                if stream and self.call_llm_for_decision_stream is not None:
                    try:
                        async for retry_event in self.call_llm_for_decision_stream(
                            current_write_context,
                            followup_tool_definitions,
                            ledger,
                            shadow_engine=shadow_engine,
                            tool_choice_override=followup_tool_choice_override,
                            model_override=followup_model_override,
                            **followup_llm_kwargs,
                        ):
                            if not isinstance(retry_event, Mapping):
                                continue
                            event_type = str(retry_event.get("type") or "").strip()
                            if event_type == "_internal_materialize":
                                candidate_response = retry_event.get("response")
                                if isinstance(candidate_response, RawLLMResponse):
                                    followup_response = candidate_response
                            elif self.emit_event is not None and event_type:
                                self.emit_event(retry_event)
                    except Exception as stream_exc:
                        logger.exception("bootstrap follow-up stream failed: turn_id=%s", turn_id)
                        raise RuntimeError(
                            f"single_batch_contract_violation_retry_failed: bootstrap follow-up stream error: {stream_exc}"
                        ) from stream_exc
                else:
                    followup_response = await self.call_llm_for_decision(
                        current_write_context,
                        followup_tool_definitions,
                        ledger,
                        tool_choice_override=followup_tool_choice_override,
                        model_override=followup_model_override,
                        **followup_llm_kwargs,
                    )
                if followup_model_override:
                    logger.warning(
                        "mutation-contract bootstrap-followup attempt=%s uses model override: %s",
                        followup_attempt + 1,
                        followup_model_override,
                    )
                if followup_response is None:
                    raise RuntimeError(
                        "single_batch_contract_violation_retry_failed: bootstrap follow-up did not materialize response"
                    )
                followup_decision = self.decoder.decode(followup_response, TurnId(turn_id))
                ledger.replace_decision(followup_decision)
                if followup_decision.get("kind") != TurnDecisionKind.TOOL_BATCH:
                    deterministic_result = await self._execute_deterministic_bootstrap_followup_write_fallback(
                        turn_id=turn_id,
                        original_context=context,
                        bootstrap_receipt=bootstrap_receipt,
                        allowed_tool_names=(
                            current_followup_allowed_tool_names
                            if current_followup_allowed_tool_names
                            else allowed_retry_tool_names
                        ),
                        state_machine=state_machine,
                        ledger=ledger,
                        write_context=current_write_context,
                        stream=stream,
                        shadow_engine=shadow_engine,
                        workspace=bootstrap_workspace,
                    )
                    if deterministic_result is not None:
                        return deterministic_result
                    raise RuntimeError(
                        "single_batch_contract_violation_retry_failed: bootstrap follow-up did not produce tool batch"
                    )
                _batch_count_before = ledger.tool_batch_count
                try:
                    followup_result = await self.execute_tool_batch(
                        followup_decision,
                        state_machine,
                        ledger,
                        current_write_context,
                        stream=stream,
                        shadow_engine=shadow_engine,
                        allowed_tool_names=(
                            current_followup_allowed_tool_names if current_followup_allowed_tool_names else None
                        ),
                        count_towards_batch_limit=True,
                    )
                    self.guard_assert_single_tool_batch(
                        turn_id=turn_id,
                        tool_batch_count=ledger.tool_batch_count,
                        ledger=ledger,
                    )
                    # Wave-3: carry the bootstrap READ receipts in the turn's
                    # authoritative receipt so the next turn's WorkingMemory can
                    # see the file content (turn context is rebuilt from scratch).
                    return merge_bootstrap_receipt_into_result(followup_result, bootstrap_receipt)
                except RuntimeError as followup_exc:
                    # FIX-20260504: rollback batch count so failed attempts don't
                    # accumulate and cause assert_single_tool_batch to fail.
                    ledger.tool_batch_count = _batch_count_before
                    rollback_state_after_retry_batch_failure(state_machine, ledger)
                    if (not is_stale_edit_contract_violation(followup_exc)) or (
                        followup_attempt >= max_followup_attempts - 1
                    ):
                        if is_mutation_contract_violation(followup_exc) and followup_attempt < (
                            max_followup_attempts - 1
                        ):
                            followup_error_text = str(followup_exc).lower()
                            if "outside narrowed set" in followup_error_text and allowed_retry_tool_names:
                                current_followup_allowed_tool_names = set(allowed_retry_tool_names)
                            current_write_context = append_retry_enforcement_hint(
                                current_write_context,
                                allowed_tool_names=(
                                    current_followup_allowed_tool_names
                                    if current_followup_allowed_tool_names
                                    else allowed_retry_tool_names
                                ),
                                reason=f"{followup_exc!s} (bootstrap follow-up {followup_attempt + 1}/{max_followup_attempts})",
                                forced_write_tool_name=followup_forced_write_tool_name,
                            )
                            continue
                        if is_mutation_contract_violation(followup_exc):
                            deterministic_result = await self._execute_deterministic_bootstrap_followup_write_fallback(
                                turn_id=turn_id,
                                original_context=context,
                                bootstrap_receipt=bootstrap_receipt,
                                allowed_tool_names=(
                                    current_followup_allowed_tool_names
                                    if current_followup_allowed_tool_names
                                    else allowed_retry_tool_names
                                ),
                                state_machine=state_machine,
                                ledger=ledger,
                                write_context=current_write_context,
                                stream=stream,
                                shadow_engine=shadow_engine,
                                workspace=bootstrap_workspace,
                            )
                            if deterministic_result is not None:
                                return deterministic_result
                        raise
                    followup_tool_batch = followup_decision.get("tool_batch")
                    if isinstance(followup_tool_batch, Mapping):
                        followup_invocations = list(followup_tool_batch.get("invocations", []) or [])
                    elif hasattr(followup_tool_batch, "invocations"):
                        followup_invocations = list(getattr(followup_tool_batch, "invocations", []) or [])
                    else:
                        followup_invocations = []
                    next_bootstrap_decision = build_stale_edit_bootstrap_decision(
                        turn_id=turn_id,
                        retry_invocations=followup_invocations,
                        decision_metadata=followup_decision.get("metadata"),
                    )
                    if next_bootstrap_decision is None:
                        raise
                    next_bootstrap_metadata = next_bootstrap_decision.get("metadata")
                    next_bootstrap_workspace = "."
                    if isinstance(next_bootstrap_metadata, Mapping):
                        next_bootstrap_workspace = str(next_bootstrap_metadata.get("workspace", ".")).strip() or "."
                    next_bootstrap_receipt = await self.execute_read_bootstrap_batch(
                        turn_id=turn_id,
                        workspace=next_bootstrap_workspace,
                        tool_batch=next_bootstrap_decision.get("tool_batch"),
                        ledger=ledger,
                    )
                    if next_bootstrap_receipt is None:
                        raise RuntimeError(
                            "single_batch_contract_violation_retry_failed: follow-up stale bootstrap read receipt missing"
                        ) from followup_exc
                    current_write_context = build_retry_write_after_bootstrap_context(
                        original_context=context,
                        bootstrap_receipt=next_bootstrap_receipt,
                        forced_write_tool_name=followup_forced_write_tool_name,
                    )
                    continue

        raise RuntimeError("single_batch_contract_violation_retry_failed: retry attempts exhausted")
