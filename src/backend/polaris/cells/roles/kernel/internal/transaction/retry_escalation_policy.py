"""ADR-0090 重试升级策略 — 模型覆盖、升级温度与解码窄化。

纯策略模块（无副作用，仅读取环境变量）：

- 重试模型覆盖解析（``KERNELONE_TRANSACTION_KERNEL_RETRY_MODELS``）
- ``edit_blocks`` schema 行区间窄化（guided decoding）
- 升级温度 / 输出 floor 解析
- API 级升级阶梯（write-only -> forced tool name）
"""

from __future__ import annotations

import os
from typing import Any


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

_VERIFICATION_TOOLS_DURING_FORCED_WRITE = frozenset({"execute_command"})


def remove_verification_tools_for_forced_write(tool_definitions: list[dict]) -> list[dict]:
    """Drop verification-only tools once API-level tool_choice forces a write tool."""
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
        if name in _VERIFICATION_TOOLS_DURING_FORCED_WRITE:
            continue
        narrowed.append(definition)
    return narrowed


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
        narrowed_parameters = dict(_LINE_RANGE_EDIT_BLOCKS_PARAMETERS)
        # Fix-11 composability: a step-pinned `file` enum (single-target fission
        # step) must survive the wholesale line-range rewrite — losing it at the
        # most-forced attempt would reopen the wrong-file escape exactly when
        # guided decoding is strictest.
        source_parameters = function_payload.get("parameters") if isinstance(function_payload, dict) else None
        source_properties = source_parameters.get("properties") if isinstance(source_parameters, dict) else None
        source_file_schema = source_properties.get("file") if isinstance(source_properties, dict) else None
        if isinstance(source_file_schema, dict) and isinstance(source_file_schema.get("enum"), list):
            narrowed_properties = dict(narrowed_parameters["properties"])
            narrowed_properties["file"] = {
                **narrowed_properties["file"],
                "enum": list(source_file_schema["enum"]),
            }
            narrowed_parameters["properties"] = narrowed_properties
        rewritten_function["parameters"] = narrowed_parameters
        rewritten["function"] = rewritten_function
        narrowed.append(rewritten)
    return narrowed


# ADR-0090 W2.6: escalation phase boundary — attempts below this index keep the
# profile decoding defaults (prompt steering only); attempts at/after it are the
# "transcribe what you already decided" phase (write-only tools, forced names).
_ESCALATION_START_ATTEMPT_INDEX = 2
_RETRY_ESCALATION_TEMPERATURE_ENV = "KERNELONE_RETRY_ESCALATION_TEMPERATURE"
_DEFAULT_RETRY_ESCALATION_TEMPERATURE = 0.2


_RETRY_OUTPUT_FLOOR_ENV = "KERNELONE_RETRY_OUTPUT_FLOOR_TOKENS"
_DEFAULT_RETRY_OUTPUT_FLOOR_TOKENS = 2500

_RETRY_CREATE_OUTPUT_FLOOR_ENV = "KERNELONE_RETRY_CREATE_OUTPUT_FLOOR_TOKENS"
_DEFAULT_RETRY_CREATE_OUTPUT_FLOOR_TOKENS = 7000


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


def resolve_retry_output_floor() -> int | None:
    """I3-r22 (F10): reserved reasoning-sized OUTPUT floor for retry/re-ask calls.

    The mutation-contract retry re-injects up to 16000 chars of bootstrap file
    content into a 16384-token local-Director window. With only the default
    ``max_tokens`` and no reserved-output floor, the prompt fills the window and
    ``clamp_output_tokens_to_window`` collapses the generation budget toward its
    256-token floor — a reasoning model then exhausts the budget mid-thought
    (live r22 main.js: ``finish_reason=length, reasoning_chars=633``) and emits
    no visible write. Passing this floor as the retry call's requested output
    tokens makes ``TokenBudgetManager.enforce`` RESERVE it and COMPRESS the
    (bulky, mostly-verbatim) prompt to fit, instead of starving the output.

    Retry-path-local: it rides the same context_override channel the temperature
    override uses, so the main-turn budget is untouched. Respects the model's
    hard window by shrinking the input, never raising the window.

    Env ``KERNELONE_RETRY_OUTPUT_FLOOR_TOKENS``: positive int; ``off``/``none``/
    ``disabled``/empty/non-positive disables the floor (returns ``None`` → legacy
    behavior). Unset → 2500.
    """
    raw = os.environ.get(_RETRY_OUTPUT_FLOOR_ENV)
    if raw is None:
        return _DEFAULT_RETRY_OUTPUT_FLOOR_TOKENS
    text = raw.strip().lower()
    if text in {"", "off", "none", "disabled", "false"}:
        return None
    try:
        value = int(text)
    except ValueError:
        return _DEFAULT_RETRY_OUTPUT_FLOOR_TOKENS
    if value <= 0:
        return None
    return value


def resolve_retry_create_output_floor() -> int | None:
    """F16 follow-up (Wall 2, 2026-06-15): a LARGER reserved output floor for a
    pure-create forced write.

    A from-scratch create has nothing to read, so the retry prompt is small — but
    the model must emit a COMPLETE file body in ONE shot. The standard floor (sized
    for an edit re-ask, ~2500) is shared with reasoning_content and truncates a
    several-hundred-line body (finish_reason=length) → the write lands empty/partial
    → director_no_materialized_changes. Selected ONLY at the pure-create site (there
    is no injected read content to evict), so reserving more output is safe. Env
    ``KERNELONE_RETRY_CREATE_OUTPUT_FLOOR_TOKENS``; ``off``/``none``/``disabled``/
    empty/non-positive disables (falls back to the standard floor). Unset → 7000.
    """
    raw = os.environ.get(_RETRY_CREATE_OUTPUT_FLOOR_ENV)
    if raw is None:
        return _DEFAULT_RETRY_CREATE_OUTPUT_FLOOR_TOKENS
    text = raw.strip().lower()
    if text in {"", "off", "none", "disabled", "false"}:
        return None
    try:
        value = int(text)
    except ValueError:
        return _DEFAULT_RETRY_CREATE_OUTPUT_FLOOR_TOKENS
    if value <= 0:
        return None
    return value


def resolve_retry_temperature_override(*, attempt_index: int, force_write_immediately: bool = False) -> float | None:
    """Phase-aware decoding: low temperature only for escalated retry attempts.

    Attempts 1-2 (index 0-1) keep the profile temperature — they still choose
    tools freely and benefit from exploration. From the escalation phase on
    (write-only set / forced tool name) the task is deterministic transcription.

    F16/F37: from-scratch creates (``force_write_immediately``) start the
    escalation immediately, so the low-temp transcription phase starts at the
    first retry and stays aligned with ``resolve_retry_escalation``.
    """
    escalation_start = 0 if force_write_immediately else _ESCALATION_START_ATTEMPT_INDEX
    if attempt_index < escalation_start:
        return None
    return resolve_escalation_temperature()


def resolve_retry_escalation(
    *,
    attempt_index: int,
    max_retry_attempts: int,
    strict_tool_definitions: list[dict] | None,
    forced_write_tool_name: str | None,
    force_write_immediately: bool = False,
    force_required_tool_choice: bool = False,
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

    F16/F37: a from-scratch create (``force_write_immediately``) never gets weak
    local models to emit the write tool spontaneously. Live L1-01 Q6 forensics
    showed the previous "one free exploration retry" still returning natural
    language/read-first intent after several minutes and no code landed. Creation
    retries therefore narrow to write-only AND force the selected write tool from
    the first retry attempt (index 0). Edit-existing steps are byte-for-byte
    unchanged (``force_write_immediately`` defaults False).

    Returns ``(tool_definitions_override, tool_choice_override)`` — ``None``
    members mean "keep the attempt's defaults".
    """
    escalation_start = 0 if force_write_immediately else _ESCALATION_START_ATTEMPT_INDEX
    if attempt_index < escalation_start or not strict_tool_definitions:
        return None, None
    definitions_override = strict_tool_definitions
    tool_choice_override: Any | None = None
    force_from_index = escalation_start if force_write_immediately else max_retry_attempts - 1
    if attempt_index >= force_from_index and force_required_tool_choice:
        tool_choice_override = "required"
        return definitions_override, tool_choice_override
    if attempt_index >= force_from_index and forced_write_tool_name:
        tool_choice_override = {
            "type": "function",
            "function": {"name": forced_write_tool_name},
        }
        definitions_override = remove_verification_tools_for_forced_write(definitions_override)
        if forced_write_tool_name == "edit_blocks":
            definitions_override = narrow_edit_blocks_schema_to_line_range(definitions_override)
    return definitions_override, tool_choice_override
