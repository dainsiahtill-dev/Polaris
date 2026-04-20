import os
import re as _re
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.constants import RoleId
from polaris.kernelone.events.io_events import emit_event
from polaris.kernelone.fs.text_ops import ensure_parent_dir
from polaris.kernelone.memory.integration import get_anthropomorphic_context
from polaris.kernelone.prompts.meta_prompting import build_meta_prompting_appendix
from polaris.kernelone.runtime.shared_types import normalize_path, unique_preserve
from polaris.kernelone.tool_execution.contracts import render_tool_contract_for_prompt

from .loader import get_template, render_template
from .utils import (
    parse_file_blocks,
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _use_context_engine_v2() -> bool:
    value = (
        str(os.environ.get("KERNELONE_CONTEXT_ENGINE") or os.environ.get("POLARIS_CONTEXT_ENGINE", ""))
        .strip()
        .lower()
    )
    return value in ("v2", "context_v2", "engine_v2", "context-engine-v2")


def _resolve_context_root() -> str:
    override = str(
        os.environ.get("KERNELONE_CONTEXT_ROOT") or os.environ.get("POLARIS_CONTEXT_ROOT", "") or ""
    ).strip()
    if override and os.path.isdir(override):
        return os.path.abspath(override)
    return PROJECT_ROOT


def _get_context_bundle(
    role: str,
    query: str,
    step: int,
    run_id: str,
    phase: str,
    events_path: str = "",
) -> dict[str, Any]:
    context_root = _resolve_context_root()
    return get_anthropomorphic_context(context_root, role, query, step, run_id, phase)


def build_project_prompt(
    plan_text: str,
    memory_summary: str,
    target_note: str,
    step: int = 0,
    run_id: str = "",
    events_path: str = "",
) -> str:
    template = get_template("project_prompt")

    anthro = _get_context_bundle(RoleId.PM, plan_text, step, run_id, "pm.planning", events_path)

    if events_path:
        output = anthro["prompt_context_obj"].model_dump()
        context_pack = anthro.get("context_pack")
        if context_pack is not None:
            output["context_hash"] = getattr(context_pack, "request_hash", "")
            output["context_snapshot"] = getattr(context_pack, "snapshot_path", "")
        emit_event(
            events_path,
            kind="observation",
            actor="PM",
            name="prompt_context",
            refs={"run_id": run_id, "step": step},
            summary="Prompt Context Injection",
            output=output,
        )

    rendered = render_template(
        template,
        {
            "plan_text": plan_text,
            "memory_summary": memory_summary,
            "target_note": target_note,
            "persona_instruction": anthro["persona_instruction"],
            "anthropomorphic_context": anthro["anthropomorphic_context"],
        },
    )
    return rendered + build_meta_prompting_appendix(_resolve_context_root(), RoleId.PM, limit=4)


def build_continuation_prompt(
    plan_text: str,
    last_response: str,
    decision_number: int,
    memory_summary: str,
    target_note: str,
) -> str:
    template = get_template("continuation_prompt")
    return render_template(
        template,
        {
            "plan_text": plan_text,
            "last_response": last_response,
            "decision_number": decision_number,
            "memory_summary": memory_summary,
            "target_note": target_note,
        },
    )


def build_director_continuation_prompt(
    brief: str,
    accumulated_output: str,
    open_block_path: str,
    attempt_index: int,
    truncation_reason: str,
) -> str:
    """Build a continuation prompt specifically for code generation scenarios.

    This prompt is optimized for continuing truncated code outputs, with clear
    instructions to NOT repeat already completed file blocks.

    Args:
        brief: Original task brief (summary)
        accumulated_output: The output accumulated so far (may be truncated)
        open_block_path: Path of the unclosed file block (if any)
        attempt_index: Current continuation attempt (1-based)
        truncation_reason: Reason for truncation (e.g., "length", "unclosed_block")

    Returns:
        Formatted continuation prompt
    """
    template = get_template("director_continuation_prompt")
    return render_template(
        template,
        {
            "brief": brief,
            "accumulated_output": accumulated_output,
            "open_block_path": open_block_path,
            "attempt_index": attempt_index,
            "truncation_reason": truncation_reason,
        },
    )


def build_repair_prompt(plan_text: str, last_response: str, reason: str) -> str:
    template = get_template("repair_prompt")
    return render_template(
        template,
        {
            "plan_text": plan_text,
            "last_response": last_response,
            "reason": reason,
        },
    )


def build_planner_prompt(
    plan_text: str,
    memory_summary: str,
    target_note: str,
    step: int = 0,
    run_id: str = "",
    events_path: str = "",
) -> str:
    template = get_template("planner_prompt")

    anthro = _get_context_bundle(RoleId.DIRECTOR, plan_text, step, run_id, "director.planning", events_path)

    if events_path:
        output = anthro["prompt_context_obj"].model_dump()
        context_pack = anthro.get("context_pack")
        if context_pack is not None:
            output["context_hash"] = getattr(context_pack, "request_hash", "")
            output["context_snapshot"] = getattr(context_pack, "snapshot_path", "")
        emit_event(
            events_path,
            kind="observation",
            actor="Director",
            name="prompt_context",
            refs={"run_id": run_id, "step": step},
            summary="Prompt Context Injection",
            output=output,
        )

    rendered = render_template(
        template,
        {
            "plan_text": plan_text,
            "memory_summary": memory_summary,
            "target_note": target_note,
            "persona_instruction": anthro["persona_instruction"],
            "anthropomorphic_context": anthro["anthropomorphic_context"],
        },
    )
    return rendered + build_meta_prompting_appendix(_resolve_context_root(), RoleId.DIRECTOR, limit=4)


def build_patch_planner_prompt(tool_output_json: str, pm_tasks_json: str) -> str:
    template = get_template("patch_planner_prompt")
    base = render_template(
        template,
        {
            "tool_output_json": tool_output_json,
            "pm_tasks_json": pm_tasks_json,
        },
    )
    return (
        base
        + "\n\n"
        + "Execution contract override (mandatory):\n"
        + "- You are in single-pass mode.\n"
        + "- `need_more_context` MUST be false.\n"
        + "- Do not request additional tool rounds.\n"
        + "- If context seems incomplete, still produce the narrowest valid ACTION payload and explain the missing context in `reason`.\n\n"
        + "Optional coding accelerators:\n"
        + '- `skill_calls`: list of {"name": "<skill_name>"}. Use this to request .skills knowledge injection only.\n'
        + '- `background_commands`: list of {"id": "bg-1", "command": "...", "cwd": ".", "timeout": 1200}.\n'
        + "- Do not use shell redirects in background commands to write runtime state/event files.\n\n"
        + "Director authority boundary (mandatory):\n"
        + "- PM contract is the sole write authority.\n"
        + "- Prefer PM target_files first; expand to adjacent files only when required by compilation/refactor correctness.\n"
        + "- If expanding write scope, include a one-line reason in `brief`.\n"
        + "- Snapshot is backup for reference only, not an automatic rollback mechanism.\n"
        + "- Never emit auto-rollback/reset actions.\n\n"
        + render_tool_contract_for_prompt(include_write_tools=True)
    )


def build_qa_prompt(
    plan_text: str,
    memory_summary: str,
    target_note: str,
    changed_files: list[str],
    planner_output: str,
    ollama_output: str,
    tool_results: str,
    reviewer_summary: str,
    patch_risk: str,
    step: int = 0,
    run_id: str = "",
    events_path: str = "",
) -> str:
    files_list = "\n".join(f"- {path}" for path in changed_files) if changed_files else "- (none)"
    template = get_template("qa_prompt")

    # Context query is related to changes and plan
    query = f"Verify changes in {files_list}. Plan: {plan_text[:200]}"
    anthro = _get_context_bundle(RoleId.QA, query, step, run_id, "qa.review", events_path)

    if events_path:
        output = anthro["prompt_context_obj"].model_dump()
        context_pack = anthro.get("context_pack")
        if context_pack is not None:
            output["context_hash"] = getattr(context_pack, "request_hash", "")
            output["context_snapshot"] = getattr(context_pack, "snapshot_path", "")
        emit_event(
            events_path,
            kind="observation",
            actor="QA",
            name="prompt_context",
            refs={"run_id": run_id, "step": step},
            summary="Prompt Context Injection",
            output=output,
        )

    rendered = render_template(
        template,
        {
            "plan_text": plan_text,
            "memory_summary": memory_summary,
            "target_note": target_note,
            "changed_files_list": files_list,
            "planner_output": planner_output,
            "ollama_output": ollama_output,
            "tool_results": tool_results,
            "reviewer_summary": reviewer_summary,
            "patch_risk": patch_risk,
            "persona_instruction": anthro["persona_instruction"],
            "anthropomorphic_context": anthro["anthropomorphic_context"],
        },
    )
    task_contract = str(target_note or "").strip() or "(missing task contract)"
    return (
        rendered
        + "\n\nCurrent task contract (authoritative):\n"
        + task_contract
        + "\n\nMandatory task-scoping rules:\n"
        + "- Judge PASS/FAIL only against the current task contract above.\n"
        + "- Do NOT fail based on unmet requirements that belong to other tasks, backlog items, or future milestones.\n"
        + "- If returning FAIL, cite at least one concrete mismatch against a specific acceptance point in the current task contract.\n"
        + "\n\nQA acceptance boundary (mandatory):\n"
        + "- Director `hp_run_verify` is self-verification (syntax/type/test), not final acceptance.\n"
        + "- QA must validate against PM task goal + acceptance criteria + evidence.\n"
        + "- QA is read-mostly: do not implement features unless emergency fixes are explicitly authorized.\n"
        + "- QA may write audit artifacts only (review summary, defect ticket, gate decision).\n"
        + "- If acceptance fails, request Director repair first; escalate to FAIL/BLOCKED only after repair-attempt limits are exhausted.\n"
        + build_meta_prompting_appendix(_resolve_context_root(), RoleId.QA, limit=4)
    )


def build_reviewer_prompt(
    plan_text: str,
    memory_summary: str,
    target_note: str,
    changed_files: list[str],
    planner_output: str,
    ollama_output: str,
    tool_results: str,
    patch_risk: str,
) -> str:
    files_list = "\n".join(f"- {path}" for path in changed_files) if changed_files else "- (none)"
    template = get_template("reviewer_prompt")
    rendered = render_template(
        template,
        {
            "plan_text": plan_text,
            "memory_summary": memory_summary,
            "target_note": target_note,
            "changed_files_list": files_list,
            "planner_output": planner_output,
            "ollama_output": ollama_output,
            "tool_results": tool_results,
            "patch_risk": patch_risk,
        },
    )
    task_contract = str(target_note or "").strip() or "(missing task contract)"
    return (
        rendered
        + "\n\nCurrent task contract (authoritative):\n"
        + task_contract
        + "\n\nMandatory review-scoping rules:\n"
        + "- Review only the current task contract above.\n"
        + "- Do NOT raise findings that are outside current task scope or belong to future tasks.\n"
        + "- If reporting a defect, point to the exact mismatch against current task acceptance.\n"
    )


def build_ollama_prompt(brief: str, file_context: str) -> str:
    template = get_template("ollama_prompt")
    return render_template(template, {"brief": brief, "file_context": file_context})


# ============================================================================
# P0: 双协议支持 - SEARCH/REPLACE (协议 A) + FILE blocks (协议 B)
# ============================================================================


@dataclass
class PatchBlock:
    """Single SEARCH/REPLACE edit block."""

    file: str
    search: str
    replace: str
    line_hint: int | None = None  # Optional line number hint for disambiguation


@dataclass
class PatchParseResult:
    """Result of parsing LLM output for patches."""

    blocks: list[PatchBlock]
    protocol: str  # "search_replace" | "full_file" | "mixed" | "none"
    errors: list[str]
    raw_file_blocks: list[dict[str, str]]  # For protocol B fallback


def parse_patch_blocks(text: str) -> PatchParseResult:
    """
    Parse LLM output for all supported edit formats.

    Supports multiple formats simultaneously:
    1. SEARCH/REPLACE blocks (PATCH_FILE format)
    2. FILE/END FILE blocks (full file format)
    3. Simple file creation blocks

    Format A (SEARCH/REPLACE):
        PATCH_FILE: path/to/file.py
        <<<<<<< SEARCH
        old snippet
        =======
        new snippet
        >>>>>>> REPLACE
        END PATCH_FILE

    Format B (Full File):
        FILE: path/to/file.py
        <content>
        END FILE

    Returns PatchParseResult with all detected blocks and files.
    """
    blocks: list[PatchBlock] = []
    errors: list[str] = []
    patch_full_file_blocks: list[dict[str, str]] = []

    if not text or text.strip() == "NO_CHANGES":
        return PatchParseResult(blocks=[], protocol="none", errors=[], raw_file_blocks=[])

    def _looks_like_search_replace_payload(value: str) -> bool:
        if not value:
            return False
        has_search = ("<<<<<<< SEARCH" in value) or bool(_re.search(r"(?m)^SEARCH:?\s*$", value))
        has_replace = (">>>>>>> REPLACE" in value) or bool(_re.search(r"(?m)^REPLACE:?\s*$", value))
        return has_search and has_replace

    def _header_requires_inner_file_path(value: str) -> bool:
        cleaned = str(value or "").strip().lower().replace(" ", "")
        if not cleaned:
            return True
        return cleaned in {
            "search/replace",
            "search_replace",
            "search-replace",
            "searchreplace",
            "sr",
        }

    def _normalize_search_text(value: str) -> str:
        text_value = str(value or "")
        marker = text_value.strip().lower()
        if marker in {"<empty or missing>", "empty or missing", "<empty>", "empty"}:
            return ""
        return text_value

    # Always parse FILE blocks - they work as fallback for any format
    raw_file_blocks = parse_file_blocks(text)

    # Detect protocol A markers (SEARCH/REPLACE)
    has_patch_file = ("PATCH_FILE:" in text) or bool(_re.search(r"(?m)^PATCH_FILE\s+\S+", text))
    has_search_marker = ("<<<<<<< SEARCH" in text) or bool(_re.search(r"(?m)^SEARCH:?\s*$", text))
    has_replace_marker = (">>>>>>> REPLACE" in text) or bool(_re.search(r"(?m)^REPLACE:?\s*$", text))
    protocol_a_present = has_patch_file or (has_search_marker and has_replace_marker)

    # Detect mixed protocol: both SEARCH/REPLACE and standalone FILE blocks present
    # Standalone FILE blocks have "END FILE" marker and are not inside PATCH_FILE blocks
    def _has_standalone_file_blocks(text: str) -> bool:
        """Check for FILE blocks that are not inside PATCH_FILE blocks."""
        lines = text.splitlines()
        in_patch_file = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("PATCH_FILE"):
                in_patch_file = True
            elif stripped == "END PATCH_FILE":
                in_patch_file = False
            elif stripped == "END FILE" and not in_patch_file:
                return True
        return False

    if protocol_a_present and _has_standalone_file_blocks(text):
        errors.append(
            "MIXED_EDIT_PROTOCOL: Cannot mix SEARCH/REPLACE blocks with FILE blocks. Use one format consistently."
        )
        return PatchParseResult(blocks=[], protocol="mixed", errors=errors, raw_file_blocks=raw_file_blocks)

    # If no SEARCH/REPLACE markers, just return FILE blocks
    if not protocol_a_present:
        if raw_file_blocks:
            return PatchParseResult(
                blocks=[],
                protocol="full_file",
                errors=[],
                raw_file_blocks=raw_file_blocks,
            )
        return PatchParseResult(blocks=[], protocol="none", errors=[], raw_file_blocks=[])

    # Parse protocol A: SEARCH/REPLACE blocks
    # Pattern to match PATCH_FILE...END PATCH_FILE sections

    # Parse protocol A: PATCH_FILE blocks
    # Pattern to match PATCH_FILE...END PATCH_FILE sections
    patch_file_pattern = _re.compile(r"PATCH_FILE(?::|\s+)\s*([^\n]*)\n(.*?)(?:END PATCH_FILE|$)", _re.DOTALL)

    # Pattern to match individual SEARCH/REPLACE blocks within a PATCH_FILE
    search_replace_pattern = _re.compile(r"<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE", _re.DOTALL)
    # Compatibility for compact variants generated by some models:
    # SEARCH\n...\nREPLACE\n...
    search_replace_simple_pattern = _re.compile(
        r"(?:^|\n)SEARCH:?\s*\n(.*?)\nREPLACE:?\s*\n(.*?)(?=\nSEARCH:?\s*\n|\nEND PATCH_FILE|\nEND FILE|\Z)",
        _re.DOTALL,
    )

    for match in patch_file_pattern.finditer(text):
        file_path = normalize_path(match.group(1).strip())
        content = match.group(2)

        if _header_requires_inner_file_path(file_path):
            inner_path_match = _re.search(r"(?m)^FILE:\s*(.+?)\s*$", content)
            if inner_path_match:
                file_path = normalize_path(str(inner_path_match.group(1) or "").strip())

        if not file_path:
            errors.append("Empty file path in PATCH_FILE block")
            continue

        # Check for END PATCH_FILE closure
        if "END PATCH_FILE" not in text[match.start() : match.end() + 20]:
            # Allow if it's at the end of the text
            remaining = text[match.end() :].strip()
            if remaining and not remaining.startswith("PATCH_FILE:"):
                errors.append(f"Unclosed PATCH_FILE block for {file_path}")
                continue

        # Find all SEARCH/REPLACE blocks in this PATCH_FILE.
        found = False
        for sr_match in search_replace_pattern.finditer(content):
            found = True
            search_text = _normalize_search_text(sr_match.group(1))
            replace_text = sr_match.group(2)
            blocks.append(
                PatchBlock(
                    file=file_path,
                    search=search_text,
                    replace=replace_text,
                )
            )
        if not found:
            for sr_match in search_replace_simple_pattern.finditer(content):
                found = True
                search_text = _normalize_search_text(sr_match.group(1))
                replace_text = sr_match.group(2)
                blocks.append(
                    PatchBlock(
                        file=file_path,
                        search=search_text,
                        replace=replace_text,
                    )
                )
        if not found:
            # Some models emit full-file payloads inside PATCH_FILE wrappers.
            # Treat that as protocol B fallback instead of hard-failing.
            full_content = str(content or "").strip("\n")
            if full_content:
                patch_full_file_blocks.append({"path": file_path, "content": full_content})
                continue
            errors.append(f"No SEARCH/REPLACE blocks found in PATCH_FILE: {file_path}")
            continue

    # Also try to parse standalone SEARCH/REPLACE blocks (without PATCH_FILE wrapper)
    # This handles simpler formats where file path is on a preceding line
    if not blocks and has_search_marker:
        # Try pattern: file path on line before SEARCH block
        standalone_pattern = _re.compile(
            r"(?:^|\n)([^\n]+\.\w+)\s*\n<<<<<<< SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>> REPLACE",
            _re.DOTALL,
        )
        for match in standalone_pattern.finditer(text):
            file_path = normalize_path(match.group(1).strip())
            if file_path:
                blocks.append(
                    PatchBlock(
                        file=file_path,
                        search=match.group(2),
                        replace=match.group(3),
                    )
                )

    if blocks:
        return PatchParseResult(blocks=blocks, protocol="search_replace", errors=errors, raw_file_blocks=[])

    combined_full_file_blocks: list[dict[str, str]] = []
    if raw_file_blocks:
        combined_full_file_blocks.extend(raw_file_blocks)
    if patch_full_file_blocks:
        combined_full_file_blocks.extend(patch_full_file_blocks)
    if combined_full_file_blocks:
        return PatchParseResult(
            blocks=[],
            protocol="full_file",
            errors=errors,
            raw_file_blocks=combined_full_file_blocks,
        )

    # No valid blocks found
    if errors:
        return PatchParseResult(blocks=[], protocol="search_replace", errors=errors, raw_file_blocks=[])

    return PatchParseResult(blocks=[], protocol="none", errors=[], raw_file_blocks=raw_file_blocks)


@dataclass
class PatchApplyResult:
    """Result of applying patch blocks."""

    ok: bool
    changed_files: list[str]
    errors: list[str]
    applied_count: int
    skipped_count: int


def _sanitize_workspace_patch_path(workspace: str, raw_file_path: str) -> str:
    """Normalize and validate a patch target path under workspace root."""
    workspace_root = os.path.abspath(str(workspace or "."))
    candidate = normalize_path(str(raw_file_path or ""))
    if not candidate:
        raise ValueError("patch target path is empty")
    if os.path.isabs(candidate):
        raise ValueError("absolute patch target path is not allowed")
    parts = [part for part in candidate.split("/") if part]
    if any(part in {"..", "."} for part in parts):
        raise ValueError("patch target path traversal is not allowed")

    full_path = os.path.abspath(os.path.join(workspace_root, candidate))
    if os.path.commonpath([workspace_root, full_path]) != workspace_root:
        raise ValueError("patch target escapes workspace root")
    return candidate


def apply_patch_blocks(
    blocks: list[PatchBlock],
    workspace: str,
    *,
    strict: bool = True,
) -> PatchApplyResult:
    """
    Apply SEARCH/REPLACE patch blocks to files.

    Args:
        blocks: List of PatchBlock to apply
        workspace: Workspace root directory
        strict: If True, fail on any error. If False, continue with other blocks.

    Returns:
        PatchApplyResult with status and details
    """
    changed_files: list[str] = []
    errors: list[str] = []
    applied_count = 0
    skipped_count = 0
    workspace_root = os.path.abspath(str(workspace or "."))

    # Group blocks by file for efficiency
    blocks_by_file: dict[str, list[PatchBlock]] = {}
    for block in blocks:
        try:
            safe_file = _sanitize_workspace_patch_path(workspace_root, block.file)
        except ValueError as exc:
            errors.append(f"Invalid patch target '{block.file}': {exc}")
            skipped_count += 1
            if strict:
                return PatchApplyResult(
                    ok=False,
                    changed_files=changed_files,
                    errors=errors,
                    applied_count=applied_count,
                    skipped_count=skipped_count,
                )
            continue

        normalized_block = PatchBlock(
            file=safe_file,
            search=block.search,
            replace=block.replace,
            line_hint=block.line_hint,
        )
        if safe_file not in blocks_by_file:
            blocks_by_file[safe_file] = []
        blocks_by_file[safe_file].append(normalized_block)

    for file_path, file_blocks in blocks_by_file.items():
        full_path = os.path.join(workspace, file_path)

        # Read current file content
        if os.path.isfile(full_path):
            try:
                with open(full_path, encoding="utf-8") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError) as e:
                errors.append(f"Failed to read {file_path}: {e}")
                skipped_count += len(file_blocks)
                if strict:
                    return PatchApplyResult(
                        ok=False,
                        changed_files=changed_files,
                        errors=errors,
                        applied_count=applied_count,
                        skipped_count=skipped_count,
                    )
                continue
        else:
            # File doesn't exist - only allow if search is empty (create file)
            content = ""

        modified = False
        for block in file_blocks:
            effective_search = block.search
            # Treat empty SEARCH as full-file replacement.
            # This is common for "create-or-overwrite" style model outputs.
            if str(block.search or "") == "":
                if content != block.replace:
                    content = block.replace
                    modified = True
                applied_count += 1
                continue

            # Check for SEARCH match
            if effective_search not in content:
                # Try to locate a stable match even when the model drifts on whitespace/blank lines.
                actual_match = _find_fuzzy_match(content, block.search)
                if actual_match:
                    block = PatchBlock(
                        file=block.file,
                        search=actual_match,
                        replace=block.replace,
                        line_hint=block.line_hint,
                    )
                    effective_search = block.search
                else:
                    error_msg = f"SEARCH block not found in {file_path}"
                    errors.append(error_msg)
                    skipped_count += 1
                    if strict:
                        return PatchApplyResult(
                            ok=False,
                            changed_files=changed_files,
                            errors=errors,
                            applied_count=applied_count,
                            skipped_count=skipped_count,
                        )
                    continue
            # Check for ambiguous matches (multiple occurrences)
            if content.endswith(f"{effective_search}\n") and content.count(effective_search) == 1:
                effective_search = f"{effective_search}\n"
            elif content.endswith(f"{effective_search}\r\n") and content.count(effective_search) == 1:
                effective_search = f"{effective_search}\r\n"

            occurrences = content.count(effective_search)
            if occurrences > 1:
                error_msg = f"Ambiguous SEARCH: {occurrences} matches found in {file_path}"
                errors.append(error_msg)
                skipped_count += 1
                if strict:
                    return PatchApplyResult(
                        ok=False,
                        changed_files=changed_files,
                        errors=errors,
                        applied_count=applied_count,
                        skipped_count=skipped_count,
                    )
                continue

            # Apply the replacement
            content = content.replace(effective_search, block.replace, 1)
            modified = True
            applied_count += 1

        # Write modified content
        if modified:
            try:
                ensure_parent_dir(full_path)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                changed_files.append(file_path)
            except OSError as e:
                errors.append(f"Failed to write {file_path}: {e}")
                if strict:
                    return PatchApplyResult(
                        ok=False,
                        changed_files=changed_files,
                        errors=errors,
                        applied_count=applied_count,
                        skipped_count=skipped_count,
                    )

    return PatchApplyResult(
        ok=len(errors) == 0,
        changed_files=unique_preserve(changed_files),
        errors=errors,
        applied_count=applied_count,
        skipped_count=skipped_count,
    )


def _normalize_whitespace(text: str) -> str:
    """Normalize whitespace for fuzzy matching."""
    # Replace multiple spaces/tabs with single space
    text = _re.sub(r"[ \t]+", " ", text)
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _find_fuzzy_match(content: str, search: str) -> str | None:
    """
    Try to find an exact substring that matches search with minor whitespace differences.
    Returns the actual substring from content if found, None otherwise.
    """
    # Try line-by-line matching
    search_lines = search.splitlines()
    content_lines = content.splitlines()

    if not search_lines:
        return None

    first_search_line = search_lines[0].strip()
    if not first_search_line:
        return None

    for i, line in enumerate(content_lines):
        if line.strip() == first_search_line:
            # Check if subsequent lines match
            if i + len(search_lines) > len(content_lines):
                continue

            match = True
            for j, search_line in enumerate(search_lines):
                if content_lines[i + j].strip() != search_line.strip():
                    match = False
                    break

            if match:
                # Return the actual content substring
                matched_lines = content_lines[i : i + len(search_lines)]
                return "\n".join(matched_lines)

    # Fallback: tolerate blank-line count differences between search and file.
    # Some models omit/insert empty lines while preserving meaningful content lines.
    non_empty_search_lines = [line.strip() for line in search_lines if line.strip()]
    if len(non_empty_search_lines) >= 2:
        pattern = _re.escape(non_empty_search_lines[0])
        for token in non_empty_search_lines[1:]:
            pattern += r"(?:\r?\n[ \t]*)+" + _re.escape(token)
        tolerant_match = _re.search(pattern, content)
        if tolerant_match:
            return str(tolerant_match.group(0))

    return None
