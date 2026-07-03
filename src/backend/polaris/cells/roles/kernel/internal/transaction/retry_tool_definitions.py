"""突变重试的工具定义筛选与窄化。

负责为 mutation-contract 重试构建/挑选工具定义：

- 突变重试的 narrowed 工具集（``build_retry_tool_definitions_for_mutation``）
- 创建模式探测与 forced-write 工具选择
- bootstrap 收据的整文件替换 / edit 错误标记检测
- forced-write-only 工具集（含 scoped write_file 合成）
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.constants import ACTIVE_WRITE_TOOLS
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    requires_mutation_intent,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_allowed_tool_names_from_definitions,
    extract_tool_name_from_definition,
)


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

    write_candidates = set(ACTIVE_WRITE_TOOLS)
    read_context_candidates = {
        "glob",
        "read_file",
        "list_directory",
        "repo_read_head",
        "repo_read_slice",
        "repo_read_tail",
        "repo_read_around",
        "repo_rg",
        "repo_glob",
    }
    # Only add verification tools that are NOT forbidden by the active contract.
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


def detect_creation_mode(
    workspace: str,
    target_files: tuple[str, ...] | list[str] = (),
) -> bool:
    """Return True when at least one target file does not yet exist on disk.

    A from-scratch creation step has nothing to read, so the weak model's
    instinct to explore (``execute_command`` / ``read_file``) before writing is
    pure waste — live forensics (L2-12 brick-breaker, 2026-06-15) show qwen
    never emits the write tool spontaneously for a create, burning the retry
    budget until the circuit breaker dead-letters the step. Callers use this to
    force the write tool by name earlier in the escalation ladder (F16). ANY
    missing target flips to creation mode (a partially-created set must not lock
    the remaining missing files back onto an edit-only tool).
    """
    if not (workspace and target_files):
        return False
    normalized = [stripped for target in target_files if (stripped := str(target).strip())]
    # The message extractor yields BOTH "README.md" and the bare stem "README"
    # for a single mention; the extension-less stem never exists on disk and
    # would wrongly flag an edit-to-existing task as a create (forcing a
    # destructive whole-file overwrite). Drop any stem that another, longer
    # token extends with a file extension — genuinely distinct targets stay.
    candidates = [
        token for token in normalized if not any(other.startswith(f"{token}.") for other in normalized)
    ] or normalized
    try:
        return any(not os.path.exists(os.path.join(workspace, token)) for token in candidates)
    except OSError:
        return False


def select_retry_forced_write_tool_name(
    tool_definitions: list[dict],
    *,
    workspace: str = "",
    target_files: tuple[str, ...] | list[str] = (),
) -> str | None:
    # Prefer tools that are robust when the retry context is incomplete. The
    # retry path is entered after a failed/non-mutating first attempt. Whole-file
    # and block-edit tools are safer general recovery choices; append remains
    # last because it can leave stale placeholder code in place.
    #
    # Target-existence awareness (factory-bench L1-05 round 6, 2026-06-12):
    # the final escalation forces the tool BY NAME via tool_choice — guided
    # decoding then physically cannot emit anything else. Forcing edit_blocks
    # for a CREATION task (no target exists yet) locks weak models into the
    # "edit_blocks cannot create new files → use write_file" teaching loop
    # forever. When every known target is missing, write_file leads.
    # ANY missing target flips to creation mode (round-7 live: the model created
    # quotes.json first, then the remaining three missing files must not be
    # locked back onto edit_blocks). write_file handles existing files too — the
    # destructive-shrink gate guards against gutting them.
    creation_mode = detect_creation_mode(workspace, target_files)
    if creation_mode:
        priority_order = (
            "write_file",
            "create_file",
            "edit_blocks",
            "edit_file",
            "search_replace",
            "repo_apply_diff",
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


def bootstrap_receipt_contains_whole_file_edit_error(bootstrap_receipt: Mapping[str, Any]) -> bool:
    """Return True when edit_blocks rejected a new-file whole-file write."""
    for item in list(bootstrap_receipt.get("results", []) or []):
        if not isinstance(item, Mapping):
            continue
        tool_name = str(item.get("tool_name") or item.get("name") or "").strip().lower()
        payload = item.get("result")
        error_parts: list[str] = []

        if isinstance(payload, Mapping):
            error_type = str(payload.get("error_type") or "").strip().lower()
            if error_type in _BOOTSTRAP_WHOLE_FILE_EDIT_ERROR_TYPES:
                return True
            for key in ("error", "message", "stderr", "details", "reason"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    error_parts.append(value)
        elif isinstance(payload, str) and payload:
            error_parts.append(payload)

        for key in ("error", "message"):
            value = item.get(key)
            if isinstance(value, str) and value:
                error_parts.append(value)

        combined = "\n".join(error_parts).lower()
        if not combined:
            continue
        if "whole-file write, not edit" in combined:
            return True
        if tool_name == "edit_blocks" and all(
            fragment in combined for fragment in ("full file content", "not an edit")
        ):
            return True
        if tool_name == "edit_blocks" and any(
            fragment in combined for fragment in _BOOTSTRAP_WHOLE_FILE_EDIT_ERROR_FRAGMENTS
        ):
            return True
    return False


def _bootstrap_receipt_has_successful_file_content(bootstrap_receipt: Mapping[str, Any]) -> bool:
    for item in list(bootstrap_receipt.get("results", []) or []):
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status and status != "success":
            continue
        payload = item.get("result")
        if not isinstance(payload, Mapping):
            continue
        content = next(
            (value for key in ("content", "text", "body", "data") if isinstance((value := payload.get(key)), str)),
            "",
        )
        if content.strip():
            return True
    return False


def _bootstrap_receipt_failure_indicates_missing_file(bootstrap_receipt: Mapping[str, Any]) -> bool:
    for item in list(bootstrap_receipt.get("results", []) or []):
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status == "success":
            continue
        payload = item.get("result")
        error_parts: list[str] = []
        if isinstance(payload, Mapping):
            error_type = str(payload.get("error_type") or "").strip().lower()
            if error_type in _BOOTSTRAP_MISSING_FILE_ERROR_TYPES:
                return True
            for key in ("error", "message", "stderr", "details", "reason"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    error_parts.append(value)
        elif isinstance(payload, str) and payload:
            error_parts.append(payload)
        for key in ("error", "message"):
            value = item.get(key)
            if isinstance(value, str) and value:
                error_parts.append(value)
        combined = "\n".join(error_parts).lower()
        if combined and any(fragment in combined for fragment in _BOOTSTRAP_MISSING_FILE_ERROR_FRAGMENTS):
            return True
    return False


def _select_existing_content_edit_tool(allowed_tool_names: set[str]) -> str | None:
    for candidate in _BOOTSTRAP_EXISTING_CONTENT_EDIT_TOOL_PRIORITY:
        if candidate in allowed_tool_names:
            return candidate
    return None


def select_bootstrap_followup_write_tool_name(
    *,
    allowed_tool_names: set[str],
    default_write_tool_name: str | None,
    bootstrap_receipt: Mapping[str, Any],
    failed_bootstrap_files: list[str],
) -> str | None:
    """Select the write tool for the post-bootstrap implementation stage."""
    has_successful_file_content = _bootstrap_receipt_has_successful_file_content(bootstrap_receipt)
    if failed_bootstrap_files:
        if not _bootstrap_receipt_failure_indicates_missing_file(bootstrap_receipt):
            return _select_existing_content_edit_tool(allowed_tool_names) if has_successful_file_content else None
        for creation_candidate in _BOOTSTRAP_CREATE_TOOL_NAMES:
            if creation_candidate in allowed_tool_names:
                return creation_candidate
        return default_write_tool_name

    if bootstrap_receipt_contains_whole_file_edit_error(bootstrap_receipt):
        for creation_candidate in _BOOTSTRAP_CREATE_TOOL_NAMES:
            if creation_candidate in allowed_tool_names:
                return creation_candidate
        return default_write_tool_name

    if bootstrap_receipt_contains_whole_file_replacement_marker(bootstrap_receipt):
        for replacement_candidate in ("write_file", "edit_file", "repo_apply_diff", "edit_blocks"):
            if replacement_candidate in allowed_tool_names:
                return replacement_candidate

    if has_successful_file_content and default_write_tool_name in _BOOTSTRAP_CREATE_TOOL_NAMES:
        existing_content_tool = _select_existing_content_edit_tool(allowed_tool_names)
        if existing_content_tool:
            return existing_content_tool

    return default_write_tool_name


def _forced_write_allow_read_companions_enabled() -> bool:
    """Default OFF -> byte-identical write-only forced retry. Opt in via env to
    add read companions so a strong model can read-then-write a cross-file repair
    in the single forced-write batch (the batch contract still requires a write)."""
    raw = str(os.environ.get("KERNELONE_DIRECTOR_FORCED_WRITE_ALLOW_READ", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def build_forced_write_only_retry_tool_definitions(
    tool_definitions: list[dict],
    forced_write_tool_name: str | None,
    *,
    include_verification_tools: bool = False,
    forbidden_tool_names: set[str] | None = None,
    allow_write_file_companion_for_edit_blocks: bool = True,
    include_mutation_companion_tools: bool = False,
) -> list[dict]:
    """Builds the strict forced-write tool definitions.

    Respects ``forbidden_tool_names`` so that contract-forbidden tools
    (e.g. execute_command) are never included even when verification is enabled.
    """
    _forbidden: set[str] = forbidden_tool_names or set()
    if not forced_write_tool_name:
        return [item for item in tool_definitions if extract_tool_name_from_definition(item) not in _forbidden]
    companion_tool_names: set[str] = {forced_write_tool_name}
    if include_mutation_companion_tools and forced_write_tool_name in {"write_file", "edit_file"}:
        companion_tool_names.update({"write_file", "edit_file"})
    if forced_write_tool_name in {"repo_apply_diff"}:
        companion_tool_names.update({"read_file", "repo_read_head"})
    elif _forced_write_allow_read_companions_enabled():
        # MiniMax-M3 / strong models legitimately read the current file before
        # editing it for a cross-file REPAIR; a write-only narrowed set rejects
        # that read ("tools outside narrowed set", 27x on factory-bench L1-04/05,
        # 2026-06-28) and burns the retry budget without a write. Offer read
        # companions so the model can read-then-write inside the single batch;
        # the batch contract guard still requires a write, so a read-only batch
        # is still rejected. Default OFF keeps the standard narrowed-tool set.
        companion_tool_names.update(
            {
                "read_file",
                "file_exists",
                "list_directory",
                "glob",
                "repo_tree",
                "repo_read_head",
                "repo_read_slice",
                "repo_read_around",
                "repo_read_tail",
                "repo_rg",
                "repo_glob",
            }
        )
    if forced_write_tool_name == "edit_blocks" and allow_write_file_companion_for_edit_blocks:
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
    has_write_file_definition = False
    for raw_item in tool_definitions:
        if not isinstance(raw_item, Mapping):
            continue
        item = dict(raw_item)
        tool_name = extract_tool_name_from_definition(item)
        if tool_name in _forbidden:
            continue
        if tool_name in companion_tool_names:
            if tool_name == "write_file":
                has_write_file_definition = True
            narrowed.append(item)
    if "write_file" in companion_tool_names and "write_file" not in _forbidden and not has_write_file_definition:
        narrowed.append(_build_scoped_write_file_tool_definition(tool_definitions))
    if narrowed:
        return narrowed
    return [item for item in tool_definitions if extract_tool_name_from_definition(item) not in _forbidden]


def _extract_file_schema_from_tool_definition(definition: Mapping[str, Any]) -> dict[str, Any] | None:
    function_payload = definition.get("function")
    parameters = (
        function_payload.get("parameters") if isinstance(function_payload, Mapping) else definition.get("parameters")
    )
    if not isinstance(parameters, Mapping):
        return None
    properties = parameters.get("properties")
    if not isinstance(properties, Mapping):
        return None
    file_schema = properties.get("file") or properties.get("path")
    if isinstance(file_schema, Mapping):
        return dict(file_schema)
    return None


def _build_scoped_write_file_tool_definition(tool_definitions: list[dict]) -> dict[str, Any]:
    file_schema: dict[str, Any] = {
        "type": "string",
        "description": "Workspace-relative path of the file to write.",
    }
    for raw_definition in tool_definitions:
        if not isinstance(raw_definition, Mapping):
            continue
        candidate = _extract_file_schema_from_tool_definition(raw_definition)
        if not candidate:
            continue
        enum_values = candidate.get("enum")
        if isinstance(enum_values, list) and enum_values:
            file_schema["enum"] = list(enum_values)
            break
    return {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write the complete UTF-8 file body to the scoped target file. "
                "Use only when replacing a small generated leaf target after reading it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": file_schema,
                    "content": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Complete non-empty UTF-8 file content.",
                    },
                    "encoding": {
                        "type": "string",
                        "enum": ["utf-8"],
                        "default": "utf-8",
                    },
                },
                "required": ["file", "content"],
            },
        },
    }


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
_BOOTSTRAP_WHOLE_FILE_EDIT_ERROR_TYPES = frozenset({"new_file_via_edit_blocks"})
_BOOTSTRAP_WHOLE_FILE_EDIT_ERROR_FRAGMENTS: tuple[str, ...] = (
    "whole-file write, not edit",
    "whole-file write",
)
_BOOTSTRAP_CREATE_TOOL_NAMES: tuple[str, ...] = ("write_file", "create_file", "append_to_file")
_BOOTSTRAP_EXISTING_CONTENT_EDIT_TOOL_PRIORITY: tuple[str, ...] = (
    "edit_blocks",
    "edit_file",
    "repo_apply_diff",
    "search_replace",
)
_BOOTSTRAP_MISSING_FILE_ERROR_TYPES = frozenset(
    {
        "file_not_found",
        "missing_file",
        "path_not_found",
        "not_found",
        "new_file_via_edit_blocks",
    }
)
_BOOTSTRAP_MISSING_FILE_ERROR_FRAGMENTS: tuple[str, ...] = (
    "file not found",
    "no such file",
    "does not exist",
    "doesn't exist",
    "not found",
    "path not found",
    "missing file",
)
