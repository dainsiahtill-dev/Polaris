"""突变重试的提示上下文构建。

负责把 retry 约束、bootstrap read 收据组织成 LLM 上下文：

- ``build_contract_retry_context`` / ``append_retry_enforcement_hint``
- bootstrap follow-up write 阶段的上下文（含 REAL file content 注入预算）
- 从 bootstrap 收据提取读取失败的文件
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.constants import ACTIVE_WRITE_TOOLS
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    extract_allowed_scope_paths_from_message,
    extract_target_files_from_message,
    filter_scope_paths_for_explicit_targets,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_latest_user_message,
)


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


def _extract_role_definition_block(context: list[dict]) -> str:
    """Return the first role identity block from the original system context."""
    for message in context:
        if not isinstance(message, Mapping):
            continue
        if str(message.get("role") or "").strip().lower() != "system":
            continue
        content = str(message.get("content") or "")
        match = re.search(r"<role_definition>.*?</role_definition>", content, re.DOTALL)
        if match:
            return match.group(0).strip()
    return ""


def _extract_authorized_scope_paths(context: list[dict], target_file_tokens: list[str]) -> list[str]:
    """Extract retry-safe scope paths from the original context."""
    raw_context = "\n".join(str(item.get("content") or "") for item in context if isinstance(item, Mapping))
    scope_paths = extract_allowed_scope_paths_from_message(raw_context)
    return filter_scope_paths_for_explicit_targets(scope_paths, target_file_tokens)


def build_contract_retry_context(
    context: list[dict],
    tool_definitions: list[dict],
    *,
    forced_write_tool_name: str | None = None,
) -> list[dict]:
    """构建突变合约违反后的 retry 上下文。"""
    role_definition = _extract_role_definition_block(context)
    latest_user = extract_latest_user_message(context)
    latest_assistant = _extract_latest_assistant_message(context)
    raw_target_file_tokens = extract_target_files_from_message(latest_user)
    target_file_tokens: list[str] = []
    for token in raw_target_file_tokens:
        normalized = token.replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if "/" not in normalized and suffix == "js" and name[:1].isupper():
            continue
        target_file_tokens.append(token)
    authorized_scope_paths = _extract_authorized_scope_paths(context, target_file_tokens)
    write_candidates = set(ACTIVE_WRITE_TOOLS)
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
            "Do not guess exact-match edit search text. "
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
        if forced_write_tool_name == "write_file":
            retry_lines.append(
                "For create-file, missing-target, scaffold, or whole-file replacement repairs, call write_file "
                "directly with args.file and args.content; do not emit execute_command/read/list-only batches."
            )
    if target_file_tokens:
        target_files_text = ", ".join(target_file_tokens)
        retry_lines.append(
            "Mutation target files detected from user request: "
            + target_files_text
            + ". The emitted tool batch must cover every listed target file required by the current task; "
            "for multi-file create tasks, emit one write/edit call per target file instead of stopping after "
            "the first successful write. "
            "Only write these target files; acceptance criteria, verification commands, test names, and command "
            "output paths are not authorization to create or modify extra files."
        )
        if forced_write_tool_name == "write_file" and len(target_file_tokens) > 1:
            retry_lines.append(
                "MULTI-TARGET WRITE CONVERGENCE: call write_file once for each still-missing target file from "
                "the list above in this same tool batch. Prefer source, entrypoint, test, README, or HTML targets "
                "named by the current task, but do not stop after only one sibling file. Do not modify package.json, "
                "tsconfig.json, or an already-created sibling unless that exact file is one of the listed targets."
            )
    if authorized_scope_paths:
        retry_lines.append(
            "Authorized directory scope_paths retained from the task contract: "
            + json.dumps(authorized_scope_paths, ensure_ascii=False)
            + ". These scope paths authorize files under the listed directories only when the current task "
            "does not already pin a more specific target inside that directory."
        )

    retry_mode_guard = (
        "RETRY MODE ACTIVE: discard only the previous staged execution workflow "
        "(e.g., understand-first/read-first).\n"
        "Do not discard the role identity, workspace, target files, tool schema, or safety constraints.\n"
        "Output a single valid TOOL_BATCH immediately under the constraints below.\n"
        "Do not emit plain-text-only response."
    )
    system_content_parts = []
    if role_definition:
        system_content_parts.append(role_definition)
        system_content_parts.append(
            "RETRY IDENTITY GUARD: keep the role above authoritative for this retry; "
            "do not infer or switch to another Polaris role."
        )
    system_content_parts.extend([retry_mode_guard, *retry_lines])
    retry_context: list[dict[str, str]] = [
        {
            "role": "system",
            "content": "\n".join(system_content_parts),
        }
    ]
    if latest_user:
        user_content = latest_user
        if authorized_scope_paths:
            user_content = (
                user_content.rstrip()
                + "\nKERNELONE_AUTHORIZED_SCOPE_PATHS: "
                + json.dumps({"scope_paths": authorized_scope_paths}, ensure_ascii=False)
            )
        retry_context.append({"role": "user", "content": user_content})
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
    forced_tool_detail = ""
    if forced_write_tool_name == "write_file":
        forced_tool_detail = (
            "\nwrite_file requires args.file and args.content. "
            "The content value must be the non-empty complete file body; "
            "do not put code in prose, analysis, or reasoning text."
        )
    elif forced_write_tool_name == "edit_blocks":
        forced_tool_detail = (
            "\nedit_blocks requires non-empty args. Easiest valid form: "
            '{"file": "<target file>", "start": <first line>, "end": <last line>, '
            '"replace": "<complete replacement source lines>"}. '
            "Do not emit empty arguments, prose, analysis, or a whole new filename plus full file body."
        )
    reason_text = str(reason or "")
    reason_lower = reason_text.lower()
    if forced_write_tool_name == "edit_blocks" and "validation failed" in reason_lower and "search" in reason_lower:
        forced_tool_detail += (
            " Previous SEARCH/REPLACE text did not match the current file. "
            "Do not retry SEARCH/REPLACE blocks; use the line-range form with file/start/end/replace."
        )
    target_file_tokens = extract_target_files_from_message(extract_latest_user_message(retry_context))
    target_file_detail = ""
    if target_file_tokens:
        target_file_detail = (
            "\nOnly write these target files: "
            + ", ".join(target_file_tokens)
            + ". Acceptance criteria, verification commands, test names, and command output paths are "
            "not authorization to create or modify extra files."
        )
        if forced_write_tool_name == "write_file" and len(target_file_tokens) > 1:
            target_file_detail += (
                "\nMULTI-TARGET WRITE CONVERGENCE: select exactly one still-missing target file from this list "
                "and write that complete file body now. Prefer the first missing source, entrypoint, test, README, "
                "or HTML target required by the current task. Do not use package.json, tsconfig.json, or another "
                "already-created sibling as a substitute unless it is the selected target."
            )
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
            + forced_tool_detail
            + target_file_detail
        ),
    }
    return [*retry_context, enforcement_hint]


# Per-file and total budgets for REAL file content carried into the bootstrap
# follow-up write context. The previous 1200-char JSON fragment made correct
# SEARCH/REPLACE transcription impossible by construction — the model was forced
# to write a file it could not see.
#
# I3-r22/r23 (F10): the 16000/9000 defaults were sized for large cloud windows.
# On a 16384-token LOCAL Director they inject ~4000 tokens of verbatim read
# content — the dominant input cost — which fills the window and collapses the
# output budget so the model truncates mid-reasoning and emits no write (live
# main.js empty-output dead-letter). A later Java repair showed the 5000-char
# per-file cap can truncate a medium 5KB source before the compiler error line,
# so keep the cap moderate but high enough for typical single-file syntax fixes.
_DEFAULT_BOOTSTRAP_READ_CONTENT_MAX_CHARS = 9000
_DEFAULT_BOOTSTRAP_READ_CONTENT_TOTAL_CHARS = 12000
_BOOTSTRAP_READ_MAX_CHARS_ENV = "KERNELONE_BOOTSTRAP_READ_MAX_CHARS"
_BOOTSTRAP_READ_TOTAL_CHARS_ENV = "KERNELONE_BOOTSTRAP_READ_TOTAL_CHARS"


def _read_positive_int_env(env_name: str, default: int) -> int:
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (AttributeError, ValueError):
        return default
    return value if value > 0 else default


def _bootstrap_read_content_max_chars() -> int:
    return _read_positive_int_env(_BOOTSTRAP_READ_MAX_CHARS_ENV, _DEFAULT_BOOTSTRAP_READ_CONTENT_MAX_CHARS)


def _bootstrap_read_content_total_chars() -> int:
    return _read_positive_int_env(_BOOTSTRAP_READ_TOTAL_CHARS_ENV, _DEFAULT_BOOTSTRAP_READ_CONTENT_TOTAL_CHARS)


def build_retry_write_after_bootstrap_context(
    *,
    original_context: list[dict],
    bootstrap_receipt: Mapping[str, Any],
    forced_write_tool_name: str | None,
    from_scratch_create: bool = False,
) -> list[dict]:
    latest_user = extract_latest_user_message(original_context)
    summary_lines: list[str] = []
    successful_files: list[str] = []
    failed_files: list[str] = []
    content_chars_used = 0
    bootstrap_total_chars = _bootstrap_read_content_total_chars()
    bootstrap_max_chars = _bootstrap_read_content_max_chars()
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
        if file_content and content_chars_used < bootstrap_total_chars:
            budget = min(
                bootstrap_max_chars,
                bootstrap_total_chars - content_chars_used,
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
    # C3 (2026-06-16 deliberation): on a from-scratch CREATE the real write
    # target does not exist on disk yet, so it can never appear in
    # successful_files (which only collects files the bootstrap successfully
    # READ). Emitting this steer there points a weak Director at the adjacent
    # context files it happened to read instead of the new target it must
    # create -> 0 correct-file output (write-convergence wall). Suppress it for
    # creates; keep it for edit-existing turns where the target IS among the
    # read files. Guard-only + default-False keeps the edit-existing path
    # byte-for-byte (floor-inert). NOTE: deliberately do NOT inject a positive
    # target steer here -- that re-creates the r21 wrong-file clobber
    # (F21/F22/F25 revert class) on multi-file leaf steps whose user message
    # names every sibling file.
    if successful_files and not from_scratch_create:
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
