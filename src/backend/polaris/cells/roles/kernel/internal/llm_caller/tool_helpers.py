"""LLM Caller Tool Helpers.

Provides tool schema building and tool call extraction utilities.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# I3-r23 (Prong A): force the write tool on turn 1 for a from-scratch leaf step.
_FIRST_TURN_WRITE_ENV = "KERNELONE_FIRST_TURN_WRITE"
_FIRST_TURN_WRITE_DISABLED = {"off", "none", "disabled", "false", "0"}

# I3-r28 (R7, repair-preserving edit): on a repair/bounce turn whose target file
# already exists, force an ANCHORED edit and forbid the whole-file rewrite verb so
# the weak model fixes the named error in place instead of rewriting smaller.
_REPAIR_PRESERVE_EDIT_ENV = "KERNELONE_REPAIR_PRESERVE_EDIT"
_REPAIR_PRESERVE_EDIT_DISABLED = {"off", "none", "disabled", "false", "0"}

_WEAK_DIRECTOR_SLIM_TOOL_SCHEMA_ENV = "KERNELONE_WEAK_DIRECTOR_SLIM_TOOL_SCHEMA"
_WEAK_DIRECTOR_SLIM_TOOL_SCHEMA_DISABLED = {"off", "none", "disabled", "false", "0"}
_WEAK_DIRECTOR_MODEL_MARKERS = (
    "gemma",
    "glm",
    "ministral",
    "qwen3.6",
    "qwen3",
    "qwen-3",
    "int4",
    "27b-code",
)
_WEAK_DIRECTOR_SLIM_DELIVERY_MODES = {"materialize_changes", "propose_patch"}
_SLIM_PROFILE_VALUES = {"slim", "compact", "minimal", "small", "weak", "local", "edge", "quantized"}
_FULL_PROFILE_VALUES = {"full", "rich", "complete", "standard", "strong", "frontier"}
_SMALL_EXECUTION_CONTEXT_WINDOW_TOKENS = 32_768
_TOOL_SCHEMA_PRESSURE_MIN_TOKENS = 1_500
_TOOL_SCHEMA_PRESSURE_MIN_COUNT = 8
_TOOL_SCHEMA_PRESSURE_RATIO = 0.18


def resolve_tool_call_provider(*, provider_id: str, model: str) -> str:
    """Resolve tool call format provider hint.

    Args:
        provider_id: Provider identifier
        model: Model name

    Returns:
        Provider hint string (anthropic, openai, or auto)
    """
    token = " ".join([str(provider_id or "").strip().lower(), str(model or "").strip().lower()])

    if any(keyword in token for keyword in ("anthropic", "claude", "kimi")):
        return "anthropic"

    if any(keyword in token for keyword in ("openai", "gpt", "codex")):
        return "openai"

    return "auto"


def _coerce_bool_flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on", "enabled", "force"}:
        return True
    if token in _WEAK_DIRECTOR_SLIM_TOOL_SCHEMA_DISABLED:
        return False
    return None


def _profile_model_text(profile: Any) -> str:
    parts = [
        getattr(profile, "provider_id", ""),
        getattr(profile, "provider", ""),
        getattr(profile, "model", ""),
        getattr(profile, "model_id", ""),
        getattr(profile, "model_name", ""),
    ]
    return " ".join(str(part or "").strip().lower() for part in parts if str(part or "").strip())


def _model_catalog_spec(
    *,
    profile: Any,
    workspace: str | None,
) -> Any | None:
    provider_id = str(getattr(profile, "provider_id", "") or "").strip()
    model = str(getattr(profile, "model", "") or "").strip()
    if not model:
        return None
    try:
        from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

        return ModelCatalog(workspace=str(workspace or ".") or ".").resolve(provider_id, model)
    except (ImportError, RuntimeError, TypeError, ValueError):
        return None


def _configured_profile_value(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip().lower()
        if text:
            return text
    return ""


def _tool_schema_pressure(
    tool_definitions: list[dict[str, Any]] | None,
    *,
    context_window_tokens: int,
) -> bool:
    if not isinstance(tool_definitions, list) or not tool_definitions:
        return False
    tool_count = len([item for item in tool_definitions if isinstance(item, dict)])
    schema_tokens = _estimate_tokens_from_chars(_json_chars(tool_definitions))
    if schema_tokens < _TOOL_SCHEMA_PRESSURE_MIN_TOKENS and tool_count < _TOOL_SCHEMA_PRESSURE_MIN_COUNT:
        return False
    if context_window_tokens <= 0:
        return tool_count >= _TOOL_SCHEMA_PRESSURE_MIN_COUNT
    return (
        context_window_tokens <= _SMALL_EXECUTION_CONTEXT_WINDOW_TOKENS
        or schema_tokens / max(1, context_window_tokens) >= _TOOL_SCHEMA_PRESSURE_RATIO
    )


def _json_chars(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        return len(str(value))


def _estimate_tokens_from_chars(char_count: int) -> int:
    return max(0, int(char_count) // 4)


def should_use_weak_director_slim_tool_schema(
    *,
    role: str,
    profile: Any,
    context_override: Any,
    workspace: str | None = None,
    tool_definitions: list[dict[str, Any]] | None = None,
) -> bool:
    """Return True when a weak Director execution turn should get a slim tool list.

    Strong PM/CE/QA turns need rich tool affordances and context. The qwen/int4
    Director execution lane has a different failure mode: native tool schemas can
    consume most of an 8k request before the task facts arrive. This gate only
    trims execution-mode Director requests and never overrides a more precise
    forced tool definition from the caller.
    """
    if os.environ.get(_WEAK_DIRECTOR_SLIM_TOOL_SCHEMA_ENV, "").strip().lower() in (
        _WEAK_DIRECTOR_SLIM_TOOL_SCHEMA_DISABLED
    ):
        return False
    if str(role or "").strip().lower() != "director":
        return False
    if not isinstance(context_override, dict):
        return False
    if isinstance(context_override.get("_transaction_kernel_forced_tool_definitions"), list):
        return False

    explicit = _coerce_bool_flag(context_override.get("director_slim_tool_schema"))
    if explicit is False:
        return False

    delivery_mode = str(context_override.get("delivery_mode") or "").strip().lower()
    execution_like = (
        delivery_mode in _WEAK_DIRECTOR_SLIM_DELIVERY_MODES
        or isinstance(context_override.get("director_quality_repair"), dict)
        or bool(context_override.get("weak_director_execution"))
    )
    if not execution_like:
        return False

    if explicit is True:
        return True

    spec = _model_catalog_spec(profile=profile, workspace=workspace)
    tool_schema_profile = _configured_profile_value(
        context_override.get("tool_schema_profile"),
        getattr(profile, "tool_schema_profile", None),
        getattr(spec, "tool_schema_profile", None),
    )
    if tool_schema_profile in _FULL_PROFILE_VALUES:
        return False
    if tool_schema_profile in _SLIM_PROFILE_VALUES:
        return True

    execution_profile = _configured_profile_value(
        context_override.get("execution_profile"),
        getattr(profile, "execution_profile", None),
        getattr(spec, "execution_profile", None),
    )
    if execution_profile in _FULL_PROFILE_VALUES:
        return False
    if execution_profile in _SLIM_PROFILE_VALUES:
        return True

    context_window_tokens = int(getattr(spec, "max_context_tokens", 0) or 0)
    if _tool_schema_pressure(tool_definitions, context_window_tokens=context_window_tokens):
        return True

    model_text = _profile_model_text(profile)
    return any(marker in model_text for marker in _WEAK_DIRECTOR_MODEL_MARKERS)


# Write tools whose canonical file-path parameter is `file` (tool_spec_registry
# arg_aliases normalize path/filepath/file_path to it). repo_apply_diff is
# excluded: it has no file argument (paths live inside diff headers).
_FILE_PARAM_WRITE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "append_to_file",
        "precision_edit",
        "edit_blocks",
    }
)


def _single_relative_target_variants(target: Any) -> tuple[str, ...]:
    """Return enum-ready variants for one clean relative target path."""

    token = str(target or "").strip()
    if not token:
        return ()
    if any(ch in token for ch in ("*", "?", "[", "]", ",", " ", "\t", "\n", "\\")):
        return ()
    if token.startswith("/") or token.startswith("~") or ".." in token.split("/"):
        return ()
    variants = [token]
    if not token.startswith("./"):
        variants.append(f"./{token}")
    return tuple(dict.fromkeys(variants))


def extract_declared_step_target_files(context_override: Any) -> tuple[str, ...]:
    """Return the construction step's declared target file as enum-ready variants.

    Guided decoding enum-matches the literal string the model emits, so both
    the bare relative path and its ``./``-prefixed form are returned. Empty
    tuple when the turn is not a fission-step execution, or when the declared
    target is not a single clean relative path (glob, comma list, whitespace,
    absolute path) — a CE-authored malformed target must never become a hard
    decode-grammar constraint; refusing to pin is the safe degradation.
    """
    if not isinstance(context_override, dict):
        return ()
    step = context_override.get("construction_step")
    if not isinstance(step, dict):
        return ()
    return _single_relative_target_variants(step.get("target_file"))


def extract_director_quality_repair_target_files(context_override: Any) -> tuple[str, ...]:
    """Return the single Director quality-repair target as enum-ready variants."""

    if not isinstance(context_override, dict):
        return ()
    repair = context_override.get("director_quality_repair")
    if not isinstance(repair, dict):
        return ()
    single_target = repair.get("write_only_single_target")
    if isinstance(single_target, dict):
        variants = _single_relative_target_variants(single_target.get("target_file"))
        if variants:
            return variants
    repair_targets = repair.get("repair_target_files")
    if isinstance(repair_targets, list) and len(repair_targets) == 1:
        return _single_relative_target_variants(repair_targets[0])
    return ()


def extract_write_tool_pin_target_files(context_override: Any) -> tuple[str, ...]:
    """Return the active single-file target for write-tool schema pinning."""

    declared_targets = extract_declared_step_target_files(context_override)
    if declared_targets:
        return declared_targets
    return extract_director_quality_repair_target_files(context_override)


_WRITE_KEEP_TOOLS = _FILE_PARAM_WRITE_TOOLS | {"repo_apply_diff", "execute_command"}


def _tool_name(definition: Any) -> str:
    if not isinstance(definition, dict):
        return ""
    fn = definition.get("function")
    if isinstance(fn, dict):
        return str(fn.get("name") or "").strip()
    return str(definition.get("name") or "").strip()


def tool_definition_names(tool_definitions: list[dict[str, Any]] | None) -> list[str]:
    """Return stable function names from native tool definitions."""

    names: list[str] = []
    for definition in tool_definitions or []:
        name = _tool_name(definition)
        if name:
            names.append(name)
    return names


def _append_required_tool(required: list[str], tool_name: Any, known_tool_map: dict[str, str]) -> None:
    normalized = str(tool_name or "").strip().strip("`'\". ").lower()
    if not normalized:
        return
    canonical = known_tool_map.get(normalized, normalized)
    if canonical not in required:
        required.append(canonical)


def _append_required_tools_from_value(required: list[str], value: Any, known_tool_map: dict[str, str]) -> None:
    if isinstance(value, str):
        for item in re.split(r"[,;\s]+", value):
            _append_required_tool(required, item, known_tool_map)
        return
    if isinstance(value, list | tuple | set):
        for item in value:
            if isinstance(item, list | tuple | set):
                _append_required_tools_from_value(required, item, known_tool_map)
            else:
                _append_required_tool(required, item, known_tool_map)


def _append_required_tools_from_contract(
    required: list[str],
    contract: Any,
    known_tool_map: dict[str, str],
) -> None:
    if not isinstance(contract, dict):
        return
    _append_required_tools_from_value(required, contract.get("required_tools"), known_tool_map)
    for key in ("required_tool_groups", "required_any_groups", "ordered_tool_groups"):
        _append_required_tools_from_value(required, contract.get(key), known_tool_map)


_REQUIRED_TOOL_LINE_MARKERS = (
    "required tool",
    "contract-required",
    "must use",
    "must call",
    "mandatory tool",
    "必须使用",
    "必须调用",
    "强制使用",
    "必需工具",
)


def _append_required_tools_from_text(
    required: list[str],
    content: Any,
    known_tool_names: list[str],
    known_tool_map: dict[str, str],
) -> None:
    for line in str(content or "").splitlines():
        lowered = line.lower()
        if not any(marker in lowered for marker in _REQUIRED_TOOL_LINE_MARKERS):
            continue
        for tool_name in known_tool_names:
            pattern = rf"(?<![\w.-]){re.escape(tool_name)}(?![\w.-])"
            if re.search(pattern, line, flags=re.IGNORECASE):
                _append_required_tool(required, tool_name, known_tool_map)


def extract_prompt_required_tool_names(
    messages: list[dict[str, Any]],
    known_tool_names: list[str],
) -> list[str]:
    """Extract tools that the final prompt/metadata makes mandatory.

    This intentionally stays conservative: it trusts structured
    ``metadata.tool_contract`` first and only treats text as mandatory when the
    line uses explicit required/must-call wording.
    """

    known_tool_map = {name.lower(): name for name in known_tool_names if str(name or "").strip()}
    required: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            _append_required_tools_from_contract(required, metadata.get("tool_contract"), known_tool_map)
            _append_required_tools_from_contract(required, metadata.get("platform_tool_contract"), known_tool_map)
        _append_required_tools_from_text(required, message.get("content"), known_tool_names, known_tool_map)
    return required


def build_tool_filter_audit(
    *,
    filter_reason: str,
    original_tool_definitions: list[dict[str, Any]],
    filtered_tool_definitions: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a final-request audit for tool-schema filtering."""

    original_tool_names = tool_definition_names(original_tool_definitions)
    filtered_tool_names = tool_definition_names(filtered_tool_definitions)
    filtered_set = {name.lower() for name in filtered_tool_names}
    removed_tool_names = [name for name in original_tool_names if name.lower() not in filtered_set]
    prompt_required_tool_names = extract_prompt_required_tool_names(
        messages,
        sorted(set(original_tool_names) | set(filtered_tool_names)),
    )
    removed_prompt_required_tool_names = [
        name for name in prompt_required_tool_names if name.lower() not in filtered_set
    ]
    return {
        "schema_version": "roles.kernel.tool_filter_audit.v1",
        "filter_reason": filter_reason,
        "status": "conflict" if removed_prompt_required_tool_names else "pass",
        "original_tool_names": original_tool_names,
        "filtered_tool_names": filtered_tool_names,
        "removed_tool_names": removed_tool_names,
        "prompt_required_tool_names": prompt_required_tool_names,
        "removed_prompt_required_tool_names": removed_prompt_required_tool_names,
        "fail_closed": bool(removed_prompt_required_tool_names),
    }


def _multi_target_first_turn_write_enabled() -> bool:
    """Default OFF -> byte-identical single-target behaviour. Opt in so a multi-new-
    file task (no single construction_step target, e.g. a validation+README task)
    still injects a first-turn write_file for the first missing declared target,
    avoiding ``no_write_tool_available`` (factory_bench L1-03 TASK-3, 2026-06-29)."""
    return str(os.environ.get("KERNELONE_DIRECTOR_MULTI_TARGET_FIRST_WRITE", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def resolve_from_scratch_write_target(context_override: Any, workspace: str) -> str | None:
    """Return the single target of a FROM-SCRATCH leaf step, else None (I3-r23).

    A from-scratch step is a CE construction step (leaf) whose single declared
    ``target_file`` does NOT yet exist and is NOT an edit-on-prior (cross-parent)
    target. Such a step needs no read — every interface symbol it consumes is
    already named in its construction-step / cross-file contract — so forcing it
    to write immediately avoids the read-first detour that triggers an
    output-starving bootstrap retry (live r23: ``main.js`` dead-lettered after the
    retry's injected file content collapsed the 16k-window output budget).

    Returns None for edit-on-prior steps, existing targets, and non-step turns —
    all of which legitimately read before writing (改建式 / Fix-13). Disabled via
    env ``KERNELONE_FIRST_TURN_WRITE`` ∈ {off,none,disabled,false,0}.
    """
    if os.environ.get(_FIRST_TURN_WRITE_ENV, "").strip().lower() in _FIRST_TURN_WRITE_DISABLED:
        return None
    if not isinstance(context_override, dict):
        return None
    ws = str(workspace or ".").strip() or "."
    step = context_override.get("construction_step")
    if isinstance(step, dict) and step and not step.get("edit_on_prior"):
        target = str(step.get("target_file") or "").strip().replace("\\", "/")
        while target.startswith("./"):
            target = target[2:]
        if target:
            try:
                if not os.path.exists(os.path.join(ws, target)):
                    return target  # single from-scratch leaf step
            except OSError:
                return None
    # FALLBACK (flag-gated, default OFF): a multi-new-file task (e.g. a validation +
    # README task declaring several NEW files) has no single from-scratch
    # construction_step target, so the single-step path above yields None and the
    # turn is left with no write tool (no_write_tool_available, L1-03 TASK-3). When
    # ANY declared target file is still missing, return the first missing one so a
    # scoped first-turn write_file is injected. No-op when no declared targets exist.
    if not _multi_target_first_turn_write_enabled():
        return None
    declared: list[str] = []
    for _key in ("target_files", "scope_paths"):
        _value = context_override.get(_key)
        if isinstance(_value, (list, tuple)):
            declared.extend(str(_item) for _item in _value)
    _task_obj = context_override.get("task")
    if isinstance(_task_obj, dict):
        _value = _task_obj.get("target_files")
        if isinstance(_value, (list, tuple)):
            declared.extend(str(_item) for _item in _value)
    for _raw in declared:
        _candidate = _raw.strip().replace("\\", "/")
        while _candidate.startswith("./"):
            _candidate = _candidate[2:]
        if not _candidate or _candidate.endswith("/"):
            continue
        try:
            if not os.path.exists(os.path.join(ws, _candidate)):
                return _candidate
        except OSError:
            continue
    return None


def restrict_tool_definitions_to_write(tool_definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep a minimal mutation execution set for materialization turns.

    Used for a from-scratch leaf step's first turn (see
    :func:`resolve_from_scratch_write_target`) and weak-Director materialize
    slimming. Bootstrap/ContextOS must collect read context before this stage;
    exposing read/locate tools here lets the model satisfy the turn with
    exploration instead of the required write. If no write tool would survive the
    filter, the original list is returned unchanged so a turn is never stranded
    with zero usable mutation capability.
    """
    kept = [d for d in tool_definitions if _tool_name(d) in _WRITE_KEEP_TOOLS]
    if not any(_tool_name(d) in _FILE_PARAM_WRITE_TOOLS for d in kept):
        return tool_definitions
    return kept


def _native_tool_schema(tool_name: str) -> dict[str, Any] | None:
    try:
        from polaris.kernelone.llm.toolkit.definitions import create_default_registry
    except (ImportError, RuntimeError, ValueError):
        return None
    definition = create_default_registry().get(str(tool_name or "").strip())
    if definition is None:
        return None
    try:
        schema = definition.to_openai_function()
    except (RuntimeError, TypeError, ValueError):
        return None
    return dict(schema) if isinstance(schema, dict) else None


def ensure_director_first_call_materialization_scope(
    *,
    role: str,
    context_override: Any,
    workspace: str,
    tool_definitions: list[dict[str, Any]],
    from_scratch_target: str | None,
    materialize_requested: bool,
    transaction_tools_disabled: bool,
) -> list[dict[str, Any]]:
    """Force a scoped ``write_file`` first-call schema for missing materialization targets."""

    del workspace  # The caller has already resolved ``from_scratch_target`` against workspace.
    if str(role or "").strip().lower() != "director":
        return tool_definitions
    if transaction_tools_disabled or not materialize_requested:
        return tool_definitions
    if not isinstance(context_override, dict):
        return tool_definitions
    target = str(from_scratch_target or "").strip().replace("\\", "/")
    if not target:
        return tool_definitions
    if isinstance(context_override.get("_transaction_kernel_forced_tool_definitions"), list):
        return tool_definitions
    if context_override.get("_transaction_kernel_forced_tool_choice") is not None:
        return tool_definitions

    target_variants = _single_relative_target_variants(target)
    if not target_variants:
        return tool_definitions
    write_schema = _native_tool_schema("write_file")
    if write_schema is None:
        context_override.setdefault(
            "director_first_call_materialization_scope",
            {
                "schema_version": "director.first_call_materialization_scope.v1",
                "source": "roles.kernel.llm_caller.tool_helpers",
                "injected": False,
                "reason": "write_file_schema_unavailable",
                "target_file": target,
                "tool": "write_file",
            },
        )
        return tool_definitions

    forced_definitions = pin_write_tool_file_param_to_targets([write_schema], target_variants)
    context_override["_transaction_kernel_forced_tool_definitions"] = forced_definitions
    context_override["_transaction_kernel_forced_tool_choice"] = {
        "type": "function",
        "function": {"name": "write_file"},
    }
    context_override["_transaction_kernel_force_exact_tools"] = True
    context_override["director_first_call_materialization_scope"] = {
        "schema_version": "director.first_call_materialization_scope.v1",
        "source": "roles.kernel.llm_caller.tool_helpers",
        "injected": True,
        "reason": "declared_scope_incomplete_requires_first_turn_write_tool",
        "target_file": target,
        "target_file_variants": list(target_variants),
        "tool": "write_file",
        "materialize_requested": True,
        "transaction_tools_disabled": False,
    }
    return forced_definitions


# Whole-file rewrite verbs dropped on a repair turn — these let the weak model
# regenerate the file from its (compressed) memory and thereby shrink/degrade it.
_FULL_REWRITE_TOOLS = frozenset({"write_file", "append_to_file"})

# Anchored / partial edit tools. Their formats express only a delta against an
# anchor (Aider-style SEARCH/REPLACE, line-range, unified diff, AST node), so the
# model physically cannot elide untouched code. At least one must survive for the
# repair restriction to engage (else the turn would be left unable to write).
_ANCHORED_EDIT_TOOLS = frozenset(
    {
        "edit_blocks",
        "edit_file",
        "precision_edit",
        "repo_apply_diff",
        "apply_patch",
        "treesitter_replace_node",
        "treesitter_insert_method",
        "treesitter_rename_symbol",
    }
)


def resolve_repair_edit_target(context_override: Any, workspace: str) -> str | None:
    """Return the target of an EDIT-EXISTING turn that must preserve content (R7).

    Fires when the leaf step's single declared ``target_file`` **already exists** on
    disk AND the turn must EDIT it in place rather than rewrite from scratch — in
    either of two cases:

      * a **repair/bounce turn** — non-empty ``last_failure`` from a prior QA /
        syntax-gate rejection (live I3-r28: ``main.js`` regressed 5762B/22 → 3095B/12
        when a repair rewrote it smaller), or
      * an **``edit_on_prior`` step** — a CE-split *fill* (I3-r29) or a cross-parent
        second writer, whose job is to extend an already-created file. The fill MUST
        edit the skeleton's stubs, never ``write_file`` over the accumulated content.

    In both the weak model is restricted to anchored edit tools. Returns None for
    from-scratch turns (file absent → Prong A territory) and non-step turns. Disabled
    via env ``KERNELONE_REPAIR_PRESERVE_EDIT`` ∈ {off,none,disabled,false,0}.
    """
    if os.environ.get(_REPAIR_PRESERVE_EDIT_ENV, "").strip().lower() in _REPAIR_PRESERVE_EDIT_DISABLED:
        return None
    if not isinstance(context_override, dict):
        return None
    step = context_override.get("construction_step")
    if not isinstance(step, dict) or not step:
        return None
    last_failure = context_override.get("last_failure")
    has_failure = isinstance(last_failure, dict) and bool(str(last_failure.get("error_message") or "").strip())
    edit_on_prior = bool(step.get("edit_on_prior"))
    if not has_failure and not edit_on_prior:
        return None
    target = str(step.get("target_file") or "").strip().replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    if not target:
        return None
    ws = str(workspace or ".").strip() or "."
    try:
        if os.path.exists(os.path.join(ws, target)):
            return target  # existing file + (failure OR edit_on_prior) → preserve-and-edit
    except OSError:
        return None
    return None


def restrict_tool_definitions_to_edit(tool_definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the whole-file rewrite verbs on a repair turn, forcing an anchored edit.

    Unlike :func:`restrict_tool_definitions_to_write` (from-scratch minimal
    execution set), the repair restriction is *subtractive*: it removes
    ``write_file`` / ``append_to_file`` while keeping every anchored edit tool AND
    the read/scout tools — the model still needs to read the current file to
    anchor a SEARCH/REPLACE. Fail-open: if no anchored edit tool is present the
    list is returned unchanged, so a turn is never left unable to write.
    Definitions are never mutated in place.
    """
    if not any(_tool_name(d) in _ANCHORED_EDIT_TOOLS for d in tool_definitions):
        return tool_definitions
    return [d for d in tool_definitions if _tool_name(d) not in _FULL_REWRITE_TOOLS]


def pin_write_tool_file_param_to_targets(
    tool_definitions: list[dict[str, Any]],
    declared_targets: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Pin write tools' ``file`` parameter to the declared step targets (enum).

    三层裂变步契约是单文件的；把 target_file 钉进写工具 schema 后, 严格 guided
    decoding 下「写错文件」不可生成, 宽松 provider 下也是最强的 schema 信号 —
    比 EXEC_TARGET_MISSING 事后反弹 (~30min/市场圈, live I3-r9 S1 假阳性) 便宜
    三个数量级。``path``/``filepath``/``file_path`` 别名是 registry 展开出的
    真实可选属性且 normalizer 会在 canonical 缺席时把别名映入 ``file``
    (对抗复核实锤的逃逸口), 故一并钉住。Caveat: edit_blocks 的 SEARCH/REPLACE
    ``blocks`` 字符串内嵌的 ``:filepath`` 钉不住, 由步靶证据门与 QA verify
    兜底。Definitions are copied, never mutated in place.
    """
    if not declared_targets:
        return tool_definitions
    file_property_names = ("file", "path", "filepath", "file_path")
    pinned: list[dict[str, Any]] = []
    for definition in tool_definitions:
        if not isinstance(definition, dict):
            pinned.append(definition)
            continue
        function_payload = definition.get("function")
        name = (
            str(function_payload.get("name") or "").strip()
            if isinstance(function_payload, dict)
            else str(definition.get("name") or "").strip()
        )
        if name not in _FILE_PARAM_WRITE_TOOLS or not isinstance(function_payload, dict):
            pinned.append(definition)
            continue
        parameters = function_payload.get("parameters")
        if not isinstance(parameters, dict):
            pinned.append(definition)
            continue
        properties = parameters.get("properties")
        if not isinstance(properties, dict) or not isinstance(properties.get("file"), dict):
            pinned.append(definition)
            continue
        new_properties = dict(properties)
        for property_name in file_property_names:
            property_schema = new_properties.get(property_name)
            if isinstance(property_schema, dict):
                new_properties[property_name] = {**property_schema, "enum": list(declared_targets)}
        new_parameters = {**parameters, "properties": new_properties}
        pinned.append({**definition, "function": {**function_payload, "parameters": new_parameters}})
    return pinned


# CCR consumer-loop offering gate (Headroom T1-A). Offering context_retrieve to a
# role enlarges its per-turn native tool-schema list, which mutates the cacheable
# tool-definition prefix on the hot path -> floor-sensitive. Default OFF so the
# emitted tool set stays byte-identical to historical behaviour until an L2-floor
# bench validates enabling it. The handler/cache (producer side) are already wired;
# this flag only controls whether the model is OFFERED the retrieve tool.
_CCR_RETRIEVE_OFFER_ENV = "KERNELONE_CCR_RETRIEVE"
_CCR_RETRIEVE_OFFER_ENABLED = {"1", "true", "yes", "on"}


def _ccr_retrieve_offering_enabled() -> bool:
    """Return True when context_retrieve should be offered to the model (flag-gated)."""
    return os.environ.get(_CCR_RETRIEVE_OFFER_ENV, "").strip().lower() in _CCR_RETRIEVE_OFFER_ENABLED


def build_native_tool_schemas(profile: Any) -> list[dict[str, Any]]:
    """Build OpenAI-format tool schemas from profile tool whitelist.

    Args:
        profile: Role profile with tool_policy.whitelist

    Returns:
        List of OpenAI-format tool schemas
    """
    whitelist = list(getattr(getattr(profile, "tool_policy", None), "whitelist", []) or [])
    if not whitelist:
        return []

    tool_policy = getattr(profile, "tool_policy", None)
    policy_opt_in = bool(getattr(tool_policy, "ccr_retrieve_opt_in", False))

    if _ccr_retrieve_offering_enabled() and policy_opt_in and "context_retrieve" not in whitelist:
        # Flag-gated CCR consumer-loop closure (T1-A): ensure the spec is registered
        # so the create_default_registry() lookup below resolves it, then offer the
        # tool to the model. Inert unless KERNELONE_CCR_RETRIEVE is set AND the
        # role's tool policy has ccr_retrieve_opt_in=True. This fail-closed gate
        # prevents the env flag from silently overriding per-role tool policy
        # (a role that did not opt in will not see context_retrieve).
        try:
            from polaris.kernelone.llm.toolkit.executor.handlers.context_retrieve import (
                ensure_context_retrieve_spec_registered,
            )

            ensure_context_retrieve_spec_registered()
            whitelist.append("context_retrieve")
        except (ImportError, RuntimeError, ValueError):  # pragma: no cover - defensive
            logger.debug("context_retrieve offering skipped: spec registration failed")

    try:
        from polaris.kernelone.llm.toolkit.definitions import create_default_registry
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name
        from polaris.kernelone.tool_execution import contracts as tool_contracts
    except (RuntimeError, ValueError):
        return []

    registry = create_default_registry()
    tool_schemas: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw_name in whitelist:
        normalized_name = normalize_tool_name(raw_name)
        if not normalized_name or normalized_name in seen:
            continue

        definition = registry.get(normalized_name)
        if definition is not None:
            seen.add(normalized_name)
            tool_schemas.append(definition.to_openai_function())
            continue

        contract_schema = _build_contract_native_tool_schema(normalized_name, tool_contracts=tool_contracts)
        if contract_schema is None:
            continue

        schema_name = str(
            (contract_schema.get("function") or {}).get("name") if isinstance(contract_schema, dict) else ""
        ).strip()

        if not schema_name or schema_name in seen:
            continue

        seen.add(schema_name)
        tool_schemas.append(contract_schema)

    return tool_schemas


def _build_contract_native_tool_schema(
    tool_name: str,
    *,
    tool_contracts: Any,
) -> dict[str, Any] | None:
    """Build tool schema from tool_contracts.

    Args:
        tool_name: Canonical tool name
        tool_contracts: Tool contracts module

    Returns:
        OpenAI-format tool schema or None
    """
    canonical_name = str(
        tool_contracts.canonicalize_tool_name(tool_name, keep_unknown=False)
        if hasattr(tool_contracts, "canonicalize_tool_name")
        else ""
    ).strip()

    if not canonical_name:
        return None

    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    spec = ToolSpecRegistry.get_all_specs().get(canonical_name)
    if not isinstance(spec, dict):
        return None

    def _build_param_schema(arg_spec: Any) -> dict[str, Any]:
        token = (
            str((arg_spec or {}).get("type") if isinstance(arg_spec, dict) else "string").strip().lower() or "string"
        )
        schema: dict[str, Any] = {"type": token}
        default_value = (arg_spec or {}).get("default") if isinstance(arg_spec, dict) else None
        if default_value is not None:
            schema["default"] = default_value
        if token == "array":
            schema["items"] = {"type": "string"}
        return schema

    arguments = list(spec.get("arguments") or [])
    arg_index: dict[str, dict[str, Any]] = {}
    properties: dict[str, Any] = {}
    required: list[str] = []

    for argument in arguments:
        if not isinstance(argument, dict):
            continue
        name = str(argument.get("name") or "").strip()
        if not name:
            continue
        arg_index[name] = argument
        param_schema = _build_param_schema(argument)
        properties[name] = param_schema
        if bool(argument.get("required")):
            required.append(name)

    alias_map = spec.get("arg_aliases")
    if isinstance(alias_map, dict):
        for alias_name, canonical_arg in alias_map.items():
            alias_token = str(alias_name or "").strip()
            canonical_token = str(canonical_arg or "").strip()
            if not alias_token or not canonical_token or alias_token in properties or canonical_token not in arg_index:
                continue
            alias_schema = dict(_build_param_schema(arg_index[canonical_token]))
            alias_schema["description"] = f"Alias of `{canonical_token}` for compatibility."
            properties[alias_token] = alias_schema

    if not properties:
        properties = {}

    parameters: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required

    description = str(spec.get("description") or "").strip() or f"Tool `{canonical_name}`."

    return {
        "type": "function",
        "function": {
            "name": canonical_name,
            "description": description,
            "parameters": parameters,
        },
    }


def extract_native_tool_calls(
    raw_payload: dict[str, Any],
    *,
    provider_id: str,
    model: str,
    response_text: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Extract native tool calls from LLM response.

    Supports three extraction layers:
    1. OpenAI format: tool_calls at top level or in choices
    2. Anthropic format: content[].tool_use blocks
    3. Text format fallback: JSON tool calls in response text

    Args:
        raw_payload: Raw response payload from provider
        provider_id: Provider identifier
        model: Model name
        response_text: Optional response text for fallback parsing

    Returns:
        Tuple of (tool_calls list, provider hint string)
    """
    if not isinstance(raw_payload, dict):
        return [], "auto"

    provider_hint = resolve_tool_call_provider(provider_id=provider_id, model=model)

    openai_calls: list[dict[str, Any]] = []
    anthropic_calls: list[dict[str, Any]] = []

    # Layer 1: OpenAI format at top level
    top_level_calls = raw_payload.get("tool_calls")
    if isinstance(top_level_calls, list):
        openai_calls.extend([item for item in top_level_calls if isinstance(item, dict)])

    # Layer 1: OpenAI format in choices
    choices = raw_payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            message_calls = message.get("tool_calls")
            if isinstance(message_calls, list):
                openai_calls.extend([item for item in message_calls if isinstance(item, dict)])

    # Layer 1: OpenAI format in message
    top_level_message = raw_payload.get("message")
    if isinstance(top_level_message, dict):
        message_calls = top_level_message.get("tool_calls")
        if isinstance(message_calls, list):
            openai_calls.extend([item for item in message_calls if isinstance(item, dict)])

    # Layer 2: Anthropic format
    content_blocks = raw_payload.get("content")
    if isinstance(content_blocks, list):
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").strip().lower() == "tool_use":
                anthropic_calls.append(block)

    # Return native tool calls if found
    if openai_calls:
        return openai_calls, "openai"
    if anthropic_calls:
        return anthropic_calls, "anthropic"

    # Layer 3: Text format fallback
    #
    # The text fallback exists only for models that return explicit JSON tool
    # calls as plain text. It must not inspect proposal/delivery payloads such
    # as fenced full-file blocks, because real source files like package.json
    # commonly contain a top-level "name" field that is not a tool name.
    if response_text:
        text_calls = _extract_tool_calls_from_text(response_text, provider_hint=provider_hint)
        if text_calls:
            logger.debug("[LLMCaller] Fallback: extracted %d tool calls from text", len(text_calls))
            return text_calls, "text_fallback"

    return [], provider_hint


def _looks_like_file_or_patch_delivery(text: str) -> bool:
    """Return True for source-code delivery formats that are not tool calls."""
    token = str(text or "")
    if not token.strip():
        return False
    lowered = token.lower()
    if "patch_file:" in lowered or "delete_file:" in lowered:
        return True
    if "<<<<<<< search" in lowered and ">>>>>>> replace" in lowered:
        return True
    if re.search(r"(?:^|\n)\s*```\s*file\s*:\s*\S+", token, flags=re.IGNORECASE):
        return True
    return bool(
        re.search(r"(?:^|\n)\s*(?:file|create)\s*[:\s]+\S+", token, flags=re.IGNORECASE)
        and re.search(r"\n\s*end\s+(?:file|create)\s*(?:\n|$)", token, flags=re.IGNORECASE)
    )


def _extract_tool_calls_from_text(text: str, *, provider_hint: str = "auto") -> list[dict[str, Any]]:
    """Extract tool calls from plain text response.

    Args:
        text: Response text that may contain JSON tool calls
        provider_hint: Provider hint for parsing

    Returns:
        List of tool calls in OpenAI-like format
    """
    if not text or not isinstance(text, str):
        return []
    gemma_inline_calls = _extract_gemma_inline_tool_calls_from_text(text)
    if gemma_inline_calls:
        return gemma_inline_calls
    if "<|tool_call>" in text and "call:" in text:
        return []
    if _looks_like_file_or_patch_delivery(text):
        return []

    # Simple regex for JSON tool call patterns
    simple_pattern = re.compile(r'\{"[^"]*":\s*"[^"]*"[^}]*\}', re.DOTALL)
    results: list[dict[str, Any]] = []

    # Strategy 1: Parse entire text as JSON
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                tool_call = _convert_json_to_tool_call(parsed)
                if tool_call:
                    results.append(tool_call)
                    return results
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 2: Extract JSON objects that look like tool calls
    try:
        from polaris.kernelone.llm.toolkit.parsers.json_based import JSONToolParser

        parser = JSONToolParser()
        parsed_calls = parser.parse(text)
        for call in parsed_calls:
            name = str(getattr(call, "name", "") or "").strip()
            arguments = getattr(call, "arguments", {})
            if name and isinstance(arguments, dict):
                results.append(
                    {
                        "id": str(uuid.uuid4()),
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                )
    except (RuntimeError, ValueError):
        # Fallback to simple regex
        for match in simple_pattern.finditer(text):
            json_str = match.group(0)
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, dict):
                    tool_call = _convert_json_to_tool_call(parsed)
                    if tool_call:
                        results.append(tool_call)
            except (json.JSONDecodeError, TypeError):
                continue

    return results


def _extract_gemma_inline_tool_calls_from_text(text: str) -> list[dict[str, Any]]:
    """Extract Gemma-style inline tool calls from plain response text.

    ADR-0090 W1.6: delegates to the kernelone textual-recovery parser — the
    single tolerant implementation — instead of a stricter parallel regex that
    required the ``<tool_call|>`` close marker and ``<|"|>``-quoted values
    (silently dropping e.g. unquoted ``n:50``). One parser, one behavior.
    """
    from polaris.kernelone.llm.toolkit.parsers.textual_tool_recovery import (
        recover_textual_tool_calls,
    )

    results: list[dict[str, Any]] = []
    for recovered in recover_textual_tool_calls(text):
        name = str(recovered.get("tool") or "").strip()
        if not name:
            continue
        arguments = recovered.get("arguments")
        results.append(
            {
                "id": str(uuid.uuid4()),
                "type": "function",
                "function": {
                    "name": name.replace("-", "_"),
                    "arguments": json.dumps(
                        arguments if isinstance(arguments, dict) else {},
                        ensure_ascii=False,
                    ),
                },
            }
        )

    return results


def _convert_json_to_tool_call(data: dict[str, Any]) -> dict[str, Any] | None:
    """Convert JSON dict to OpenAI tool call format.

    Args:
        data: Parsed JSON dictionary

    Returns:
        Tool call dict or None if invalid
    """
    if not isinstance(data, dict):
        return None

    # Normalize keys to lowercase
    data_lower = {k.lower(): v for k, v in data.items()}

    # Extract tool name
    name = None
    for key in ("name", "tool", "function", "action"):
        value = data_lower.get(key)
        if isinstance(value, str) and value.strip():
            name = value.strip()
            break

    if not name:
        return None

    # Validate name format
    if not re.match(r"^[a-z][a-z0-9_]{0,63}$", name, re.IGNORECASE):
        return None

    # Extract arguments. A bare {"name": "..."} object is often ordinary
    # data such as package.json metadata, not a tool call.
    has_arguments_key = any(key in data_lower for key in ("arguments", "args", "params", "parameters"))
    if not has_arguments_key:
        return None

    arguments = {}
    for key in ("arguments", "args", "params", "parameters"):
        value = data_lower.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            arguments = value
            break
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    arguments = parsed
                    break
            except (json.JSONDecodeError, TypeError):
                pass

    return {
        "id": str(uuid.uuid4()),
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments) if isinstance(arguments, dict) else "{}",
        },
    }


__all__ = [
    "build_native_tool_schemas",
    "extract_native_tool_calls",
    "resolve_tool_call_provider",
]
