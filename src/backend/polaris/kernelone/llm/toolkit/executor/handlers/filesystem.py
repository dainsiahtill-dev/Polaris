"""Filesystem tool handlers.

Handles file operations: read_file, write_file, edit_file, search_replace, append_to_file.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
from typing import TYPE_CHECKING, Any

from polaris.kernelone.editing.editblock_engine import (
    parse_edit_blocks,
    validate_edit_blocks,
)
from polaris.kernelone.llm.toolkit.executor.utils import (
    BudgetExceededError,
    get_budget_remaining_lines,
    resolve_workspace_path,
    to_workspace_relative_path,
)
from polaris.kernelone.tool_execution.code_validator import (
    format_validation_error,
    validate_code_syntax,
    verify_written_code,
)
from polaris.kernelone.tool_execution.suggestions.precise_matcher import fuzzy_replace

if TYPE_CHECKING:
    from polaris.kernelone.fs import KernelFileSystem
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor

    _KernelFileSystem = KernelFileSystem

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Post-write syntax gate (Phase-1 A5, factory-bench L2-09 live evidence:
# 167 lines of working snake-game JS killed by one `;` where a `,` belonged —
# object literal at game.js:54. Zero-LLM checkers run AFTER a successful
# write; the diagnostic rides back in the tool RESULT so the model fixes it
# next turn. Writes are never blocked (progressive drafts stay legal) and
# checker selection is extension-driven — no project-specific logic (§8).
# ---------------------------------------------------------------------------


def _syntax_check_file(absolute_path: str) -> dict[str, Any] | None:
    """Delegate to the kernelone.quality single source of truth (shared with
    the materialization artifact-quality scan)."""
    from polaris.kernelone.quality import check_source_file_syntax

    return check_source_file_syntax(absolute_path)


def attach_post_write_syntax_check(result: dict[str, Any], absolute_path: str) -> dict[str, Any]:
    """Attach syntax diagnostics to a SUCCESSFUL write/edit result."""
    if not result.get("ok"):
        return result
    check = _syntax_check_file(absolute_path)
    if check is None:
        return result
    if check.get("ok"):
        result["syntax_check"] = "passed"
        return result
    result["syntax_check"] = "failed"
    error_text = str(check.get("error", ""))
    result["syntax_error"] = error_text
    if _looks_like_output_truncation(error_text):
        # The write was cut by the model's own output-token limit — a rewrite
        # at the same limit truncates at the same place forever (live
        # factory-bench L2-11 r6: index.html rewritten three times, 6.8-7.8KB,
        # every copy truncated). Only appending the remainder converges.
        result["suggestion"] = (
            "The file was CUT OFF by the output limit — do NOT rewrite it. "
            "Call append_to_file with ONLY the remaining content, continuing "
            f"exactly after the file's current end: {error_text}"
        )
    else:
        result["suggestion"] = (
            "The file was written BUT it has a syntax error — fix it now with a "
            "narrow edit_blocks line-range edit before doing anything else: "
            f"{error_text}"
        )
    return result


def _looks_like_output_truncation(error_text: str) -> bool:
    """Truncation signatures from the kernelone.quality SSOT checker."""
    lowered = str(error_text or "").lower()
    return (
        "unexpected end of input" in lowered
        or "truncated/incomplete html" in lowered
        or "was never closed" in lowered
        or "unexpected eof" in lowered
    )


def register_handlers() -> dict[str, Any]:
    """Return a dict of handler names to handler methods.

    This is used by the executor core to register all filesystem handlers.
    """
    return {
        "write_file": _handle_write_file,
        "read_file": _handle_read_file,
        "edit_file": _handle_edit_file,
        "edit_blocks": _handle_edit_blocks,
        "search_replace": _handle_search_replace,
        "append_to_file": _handle_append_to_file,
    }


# Did-you-mean path suggestion bounds (weak-model ergonomics: a wrong guessed
# path must come back with the correct candidates in the SAME error, otherwise
# imprecise models loop on path guesses until the failure budget locks them out).
_SUGGEST_MAX_FILES = 30000
_SUGGEST_MAX_RESULTS = 5
# Relevance gate (run20 forensics, 2026-06-11): a suggestion whose ONLY link
# to the request is the basename routinely redirects weak models into editing
# real but unrelated files — 10/18 run20 instances shipped exactly such a
# suggested file as their final (wrong) patch. A candidate must corroborate
# the request structurally: multi-component requests need at least one
# directory component to match beyond the basename; bare-name requests only
# accept shallow candidates (a bare conventional name means "the top-level
# file", not a deep unrelated subtree). Zero relevant candidates falls back
# to repo_rg/repo_tree exploration guidance — for a weak model, no hint beats
# a wrong hint.
_SUGGEST_BARE_NAME_MAX_DEPTH = 2

# Destructive-shrink gate (Phase-1 A4 slice; run20 539→32-line overwrite,
# phase1smoke5 live: 1403 lines of django expressions.py replaced by 17).
# A bug fix is a NARROW edit; deleting a large block and writing back a
# fraction of it guts real files while still "applying" cleanly. Thresholds
# mirror the failure labeler's destructive_overwrite definition so the gate
# and the metric agree. fail-closed: the model gets a retryable teaching
# error telling it to narrow the range.
_DESTRUCTIVE_SHRINK_MIN_REMOVED_LINES = 100
_DESTRUCTIVE_SHRINK_MAX_ADD_RATIO = 0.4

# Wall 2 (2026-06-15): a write_file whose ``content`` is blank/whitespace on a
# content-bearing target silently produced a 0-byte file that passed as a
# successful write — .css/.html were never syntax-gated and an empty .js passes
# the bracket check — so the step died ``director_no_materialized_changes`` with
# NO recovery. Guard these extensions; the teaching error is recognised by
# ``contract_guards`` as an argument-shape failure so the escalation/re-ask
# ladder forces a real-content write instead of dead-lettering.
_EMPTY_WRITE_GUARD_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyw",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".go",
        ".rs",
        ".css",
        ".scss",
        ".less",
        ".html",
        ".htm",
        ".md",
        ".json",
        ".vue",
        ".svelte",
    }
)
# Files that may LEGITIMATELY be empty — never flag these.
_EMPTY_WRITE_SENTINEL_BASENAMES: frozenset[str] = frozenset({"__init__.py", "py.typed", ".gitkeep"})


def is_empty_write_content_violation(rel: str, content: str) -> bool:
    """True when a blank write to a content-bearing target is a Wall-2 violation.

    The weak Director narrates the file body in prose/reasoning and emits
    ``write_file`` with an empty ``content`` argument; that 0-byte write was
    being accepted as authoritative. Sentinel files (``__init__.py`` /
    ``py.typed`` / ``.gitkeep``) may legitimately be empty.
    """
    if content.strip():
        return False
    normalized = rel.replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    if basename in _EMPTY_WRITE_SENTINEL_BASENAMES:
        return False
    return any(normalized.endswith(ext) for ext in _EMPTY_WRITE_GUARD_EXTENSIONS)


def is_blank_sentinel_write(rel: str, content: str) -> bool:
    """True when a blank write targets a legitimately-empty sentinel file.

    An empty ``__init__.py`` / ``py.typed`` / ``.gitkeep`` is valid and often
    REQUIRED (the Python package marker). The PreWriteGuard's EmptyCode syntax
    check must skip these blanks, otherwise the marker never lands, the
    materialization quality gate reports it "missing", and the weak Director
    burns its budget in a repair read-loop and dead-letters (factory-bench
    L4-19: empty ``backend/__init__.py`` blocked -> 0/3 successes). A NON-empty
    sentinel still validates normally.
    """
    if content.strip():
        return False
    basename = rel.replace("\\", "/").rsplit("/", 1)[-1]
    return basename in _EMPTY_WRITE_SENTINEL_BASENAMES


# Line-anchored insertion directives a weak model sometimes emits as the ENTIRE
# write_file content — treating a full-file write like an incremental edit. The
# canonical live failure (L2-08 world-clock, 2026-06-16): app.js written as
# "// 第 70 行之后添加\n}" — a 28-byte syntactically-broken stub that the syntax
# gate rejects but the Director kept re-emitting (6x SyntaxError, no convergence).
_EDIT_FRAGMENT_DIRECTIVE_RE = re.compile(
    r"第\s*\d+\s*行\s*(之后|之前|后面|前面|后|前)?\s*(添加|插入|修改|替换)"
    r"|在\s*第?\s*\d+\s*行"
    r"|(?:insert|add|append|replace|change|modify)\b[^\n]{0,30}\bline\s+\d+"
    r"|after\s+line\s+\d+"
    r"|line\s+\d+\b[^\n]{0,20}(?:添加|插入|insert|add|append)",
    re.IGNORECASE,
)


def is_edit_fragment_write_violation(rel: str, content: str) -> bool:
    """True when write_file content is an edit fragment, not a complete file.

    Weak models sometimes emit a line-anchored insertion directive (e.g.
    ``// 第 70 行之后添加\\n}``) as the ENTIRE write_file content, treating a
    full-file write like an incremental edit — producing a broken stub. Catch
    only the unambiguous case: short content for a code target whose body is
    dominated by such a directive. A real file body is far longer and is not an
    insertion instruction; a false positive is recoverable (the Director simply
    re-emits the full content), so the guard favours catching the failure.
    """
    text = (content or "").strip()
    if not text or len(text) > 400:
        return False
    normalized = rel.replace("\\", "/")
    if not any(normalized.endswith(ext) for ext in _EMPTY_WRITE_GUARD_EXTENSIONS):
        return False
    return _EDIT_FRAGMENT_DIRECTIVE_RE.search(text) is not None


def _destructive_shrink_error(target: str, removed_lines: int, added_lines: int, *, tool_hint: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            f"Destructive shrink rejected: this edit would replace {removed_lines} lines of "
            f"{target} with only {added_lines} line(s). Large deletions disguised as edits "
            "destroy working code."
        ),
        "suggestion": tool_hint,
        "error_type": "destructive_shrink",
        "retryable": True,
    }


def _suggest_similar_paths(self: AgentAccelToolExecutor, requested: str) -> list[str]:
    """Find existing workspace files whose basename matches a not-found path.

    Bounded ``os.walk`` basename scan (reuses the search fallback skip-dirs).
    Candidates are ranked by how many trailing path components they share with
    the requested path, so a request for ``src/django/core/checks/model_checks.py``
    ranks the real ``django/core/checks/model_checks.py`` first.

    Returns:
        Workspace-relative paths (``/`` separators), at most ``_SUGGEST_MAX_RESULTS``.
    """
    from polaris.kernelone.llm.toolkit.executor.handlers.search import _FALLBACK_SKIP_DIRS

    normalized = str(requested).replace("\\", "/").strip().strip("/")
    basename = normalized.rsplit("/", 1)[-1].strip().lower()
    if not basename:
        return []
    requested_parts = [part.lower() for part in normalized.split("/") if part and part != "."]

    matches: list[tuple[int, int, str]] = []
    scanned = 0
    workspace_root = self.workspace
    try:
        for current_root, dirnames, filenames in os.walk(workspace_root):
            dirnames[:] = [d for d in dirnames if d not in _FALLBACK_SKIP_DIRS]
            for filename in filenames:
                scanned += 1
                if scanned > _SUGGEST_MAX_FILES:
                    raise StopIteration
                if filename.lower() != basename:
                    continue
                absolute = os.path.join(current_root, filename)
                rel_path = os.path.relpath(absolute, workspace_root).replace("\\", "/")
                candidate_parts = [part.lower() for part in rel_path.split("/") if part]
                # Count consecutive matching components from the end (basename inclusive).
                overlap = 0
                for req_part, cand_part in zip(reversed(requested_parts), reversed(candidate_parts), strict=False):
                    if req_part != cand_part:
                        break
                    overlap += 1
                matches.append((-overlap, len(candidate_parts), rel_path))
    except StopIteration:
        pass
    except OSError:
        return []

    matches.sort()
    multi_component = len(requested_parts) >= 2
    relevant: list[str] = []
    for neg_overlap, depth, rel_path in matches:
        if multi_component:
            if -neg_overlap >= 2:
                relevant.append(rel_path)
        elif depth <= _SUGGEST_BARE_NAME_MAX_DEPTH:
            relevant.append(rel_path)
    return relevant[:_SUGGEST_MAX_RESULTS]


def _resolve_case_variant_rel(workspace_root: str, rel: str) -> str | None:
    """Return an existing same-directory path that differs from ``rel`` only by case.

    RC-B (2026-06-16, L3-14 React-SPA live audit): weak models conflate file
    casing and create case-variant duplicates — e.g. write ``src/App.jsx`` while
    ``src/app.jsx`` already exists. That forks a split-brain pair on
    case-sensitive Linux (and is outright unrepresentable on macOS/Windows). When
    the exact path is absent but a case-only sibling exists in the same
    directory, callers redirect the write to the existing file instead.

    Returns the existing workspace-relative path (``/`` separators), or None.
    """
    normalized = str(rel).replace("\\", "/").strip().strip("/")
    if not normalized:
        return None
    head, _, tail = normalized.rpartition("/")
    if not tail:
        return None
    parent_abs = os.path.join(workspace_root, head) if head else workspace_root
    try:
        entries = os.listdir(parent_abs)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None
    tail_lower = tail.lower()
    for entry in entries:
        if entry != tail and entry.lower() == tail_lower:
            try:
                if os.path.isfile(os.path.join(parent_abs, entry)):
                    return f"{head}/{entry}" if head else entry
            except OSError:
                return None
    return None


def _not_found_error(self: AgentAccelToolExecutor, requested: str) -> dict[str, Any]:
    """Build a file-not-found error payload that hands the model corrected paths.

    The 'Did you mean' candidates live in the error text itself so they survive
    every downstream wrapper (failure budget, receipts, retry contexts).
    """
    candidates = _suggest_similar_paths(self, requested)
    error = f"File not found: {requested}"
    if candidates:
        joined = ", ".join(candidates)
        error += f". Did you mean: {joined}?"
        suggestion = f"Call the tool again with one of these EXACT existing paths: {joined}"
    else:
        stem = str(requested).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].strip()
        search_hint = f'repo_rg("{stem}") or repo_tree()' if stem else "repo_tree() or repo_rg()"
        suggestion = (
            f"This path does not exist in the workspace. Use {search_hint} to locate "
            "the right file first. Do not assume files exist - always verify with "
            "exploration tools."
        )
    return {"ok": False, "error": error, "suggestion": suggestion}


def _resolve_workspace_rel(self: AgentAccelToolExecutor, raw_path: str) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve a tool-supplied path to workspace-relative, with teaching errors.

    Weak models hallucinate FOREIGN absolute paths (live capture, Qwen3.6:
    ``/Users/joey/workspace/polaris/main.py`` on a Linux host) — the fs layer
    raises ``UNSUPPORTED_PATH_PREFIX`` deep inside, which used to surface as an
    unclassified error that burned the read failure budget and collateral-blocked
    correct-path reads. Convert it into a did-you-mean not-found (basename scan)
    so the model self-corrects in one step.

    Returns:
        ``(relative_path, None)`` on success, ``(None, error_payload)`` otherwise.
    """
    try:
        target = resolve_workspace_path(self._kernel_fs, str(raw_path))
        return to_workspace_relative_path(self._kernel_fs, target), None
    except ValueError as exc:
        payload = _not_found_error(self, str(raw_path))
        if "UNSUPPORTED_PATH_PREFIX" in str(exc):
            payload["error"] = (
                f"Unsupported absolute path: {raw_path}. "
                "Use a WORKSPACE-RELATIVE path (e.g. 'subdir/module.py'). " + str(payload["error"])
            )
        return None, payload


def _write_temp_verify_rename(
    target_path: str,
    content: str,
    *,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Transactional write: temp -> verify -> atomic rename.

    Returns:
        {"ok": True, "bytes_written": int} on success.
        {"ok": False, "error": str} on failure (original file untouched).
    """
    parent = os.path.dirname(target_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    suffix = f".{os.path.basename(target_path)}.tmp"
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            suffix=suffix,
            dir=parent or ".",
            delete=False,
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        verify_result = verify_written_code(tmp_path, content)
        if not verify_result.success:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            return {
                "ok": False,
                "error": f"Post-write verification failed: {verify_result.error}",
            }

        os.replace(tmp_path, target_path)
        return {"ok": True, "bytes_written": len(content.encode(encoding, errors="replace"))}
    except OSError as exc:
        if tmp_path:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
        return {"ok": False, "error": f"Failed to write file: {exc}"}


def _read_workspace_agents_policy_text(self: AgentAccelToolExecutor, rel: str = "") -> str:
    """Read root and nested AGENTS.md files that apply to a workspace-relative path."""
    normalized_rel = str(rel or "").replace("\\", "/").strip("/")
    candidates = ["AGENTS.md"]
    parent_parts = [part for part in normalized_rel.split("/")[:-1] if part]
    for index in range(1, len(parent_parts) + 1):
        candidates.append("/".join([*parent_parts[:index], "AGENTS.md"]))

    texts: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            if self._kernel_fs.workspace_exists(candidate) and self._kernel_fs.workspace_is_file(candidate):
                texts.append(self._kernel_fs.workspace_read_text(candidate, encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
    return "\n".join(texts)


def _coerce_policy_scope_list(value: Any) -> list[str]:
    """Normalize an optional scope-like tool argument into a string list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item or "").strip()]
    return []


def _director_write_allowed_scope(tool_kwargs: dict[str, Any] | None) -> list[str]:
    """Extract an explicit Director write scope if the call carries one."""
    kwargs = tool_kwargs or {}
    for key in (
        "allowed_scope",
        "allowed_scope_paths",
        "scope_paths",
        "target_files",
        "pm_target_files",
        "act_files",
    ):
        scope = _coerce_policy_scope_list(kwargs.get(key))
        if scope:
            return scope
    return []


def _validate_director_policy_for_write(
    self: AgentAccelToolExecutor,
    *,
    rel: str,
    old_content: str,
    new_content: str,
    operation: str,
    tool_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a pending workspace write and return structured policy evidence."""
    from polaris.kernelone.llm.toolkit.write_policy import validate_tool_write_policy

    normalized_rel = str(rel or "").replace("\\", "/").strip("/")
    package_write = normalized_rel == "package.json" or normalized_rel.endswith("/package.json")
    verdict = validate_tool_write_policy(
        changed_files=[normalized_rel] if normalized_rel else [],
        allowed_scope=_director_write_allowed_scope(tool_kwargs),
        agents_md=_read_workspace_agents_policy_text(self, normalized_rel),
        operation=operation,
        package_before=old_content if package_write else None,
        package_after=new_content if package_write else None,
        require_change=True,
    )
    evidence = verdict.to_dict()
    if verdict.allowed:
        return {"ok": True, "director_policy": evidence}

    reason = "; ".join(verdict.reasons) or "Director write policy denied the write"
    return {
        "ok": False,
        "error": f"Director write policy denied: {reason}",
        "error_type": "director_write_policy_denied",
        "blocked": True,
        "director_policy": evidence,
    }


def _attach_director_policy_evidence(result: dict[str, Any], evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Attach policy evidence to a tool result and its effect receipt."""
    if not evidence:
        return result
    result["director_policy"] = evidence
    receipt = result.get("effect_receipt")
    if isinstance(receipt, dict):
        receipt["director_policy"] = evidence
    return result


def _is_placeholder_search_text(search_text: str) -> bool:
    """Return true when SEARCH is a model shorthand for a placeholder file."""
    lines = [line.strip() for line in str(search_text or "").splitlines() if line.strip()]
    if not lines or len(lines) > 3:
        return False
    compact = " ".join(lines).lower()
    if len(compact) > 180:
        return False
    return bool(
        re.search(
            r"\b(todo|fixme|placeholder|not\s+implemented|implement(?:ation)?|stub|scaffold)\b",
            compact,
        )
    )


def _looks_like_complete_file_replacement(replace_text: str, rel: str) -> bool:
    """Conservatively detect a complete file body rather than a tiny fragment."""
    content = str(replace_text or "").strip()
    if len(content) < 200:
        return False
    lines = [line for line in content.splitlines() if line.strip()]
    if len(lines) < 8:
        return False
    suffix = os.path.splitext(rel)[1].lower()
    if suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return bool(
            re.search(
                r"^\s*(import|export|type|interface|class|function|const|let|var)\b",
                content,
                flags=re.MULTILINE,
            )
        )
    if suffix in {".py", ".pyw"}:
        return bool(re.search(r"^\s*(from|import|class|def)\b", content, flags=re.MULTILINE))
    return True


def _should_use_whole_file_placeholder_replacement(
    *,
    search_text: str,
    replace_text: str,
    rel: str,
    block_count: int,
) -> bool:
    """Allow a controlled whole-file replacement for common LLM edit-block shorthand."""
    return (
        block_count == 1
        and _is_placeholder_search_text(search_text)
        and _looks_like_complete_file_replacement(replace_text, rel)
    )


def _normalize_edit_block_text(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _drop_final_content_line(text: str) -> str:
    candidate = text[:-1] if text.endswith("\n") else text
    if "\n" not in candidate:
        return ""
    head, tail = candidate.rsplit("\n", 1)
    if not tail.strip():
        return ""
    return f"{head}\n"


def _prefix_search_candidates(search_text: str) -> list[str]:
    normalized = _normalize_edit_block_text(search_text)
    variants = [normalized]
    marker_index = normalized.find("[truncated]")
    if marker_index >= 0:
        variants.append(normalized[:marker_index])

    candidates: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        for candidate in (variant, _drop_final_content_line(variant)):
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def _has_sufficient_whole_file_prefix_evidence(prefix: str) -> bool:
    stripped = prefix.strip()
    if len(stripped) < 200:
        return False
    non_empty_lines = [line for line in prefix.splitlines() if line.strip()]
    return len(non_empty_lines) >= 8


def _should_use_whole_file_prefix_replacement(
    *,
    current_text: str,
    search_text: str,
    replace_text: str,
    rel: str,
    block_count: int,
) -> bool:
    """Allow whole-file replacement when SEARCH is a verified file-prefix snapshot."""
    suffix = os.path.splitext(rel)[1].lower()
    if suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".pyw"}:
        return False
    if block_count != 1 or not _looks_like_complete_file_replacement(replace_text, rel):
        return False

    current = _normalize_edit_block_text(current_text).lstrip("\ufeff")
    for candidate in _prefix_search_candidates(search_text):
        prefix = candidate.lstrip("\ufeff")
        if _has_sufficient_whole_file_prefix_evidence(prefix) and current.startswith(prefix):
            return True
    return False


def _handle_write_file(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle write_file tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    from polaris.kernelone.llm.toolkit.tool_normalization import (
        normalize_patch_like_write_content,
    )

    file = kwargs.get("file")
    path = kwargs.get("path")
    filepath = kwargs.get("filepath")
    content = kwargs.get("content", "")
    encoding = kwargs.get("encoding", "utf-8")

    target_path = file or path or filepath
    if not target_path:
        return {"ok": False, "error": "Missing file path"}

    if str(encoding or "utf-8").lower() != "utf-8":
        return {"ok": False, "error": "Only utf-8 encoding is supported"}

    file_path = str(target_path).strip()
    if not file_path:
        return {"ok": False, "error": "Missing file path"}

    if "\n" in file_path or "\r" in file_path:
        return {"ok": False, "error": f"Invalid file path contains newline: {file_path!r}"}

    if re.match(r"^(table|index)\s+if\s+not\s+exists\b", file_path, re.IGNORECASE):
        return {"ok": False, "error": f"Invalid file path resembles SQL statement: {file_path}"}

    try:
        target = resolve_workspace_path(self._kernel_fs, file_path)
    except ValueError as exc:
        if "UNSUPPORTED_PATH_PREFIX" in str(exc):
            return {
                "ok": False,
                "error": (
                    f"Unsupported absolute path: {file_path}. Use a WORKSPACE-RELATIVE path (e.g. 'subdir/module.py')."
                ),
            }
        raise
    allowed_extensionless = {
        "makefile",
        "dockerfile",
        "readme",
        "gitignore",
        "gitattributes",
        "dockerignore",
        "env",
        "editorconfig",
        "prettierrc",
        "eslintrc",
        "bashrc",
        "zshrc",
        "profile",
        "toml",
        "ini",
    }
    # Strip leading dot for comparison (e.g., ".gitignore" -> "gitignore")
    target_name_lower = target.name.lower().lstrip(".")
    if not target.suffix and target_name_lower not in allowed_extensionless:
        return {"ok": False, "error": f"Invalid file path missing extension: {file_path}"}

    rel = to_workspace_relative_path(self._kernel_fs, target)

    # RC-B: redirect a case-variant duplicate onto the existing file so weak
    # models cannot fork ``App.jsx`` / ``app.jsx`` split-brain pairs.
    if not self._kernel_fs.workspace_exists(rel):
        case_variant = _resolve_case_variant_rel(self.workspace, rel)
        if case_variant is not None and case_variant != rel:
            logger.warning("write_file case-variant redirect: %s -> %s", rel, case_variant)
            rel = case_variant
            target = resolve_workspace_path(self._kernel_fs, rel)

    old_content = ""
    operation = "create"

    if self._kernel_fs.workspace_exists(rel):
        if not self._kernel_fs.workspace_is_file(rel):
            return {"ok": False, "error": f"Path is not a file: {file_path}"}
        operation = "modify"
        try:
            old_content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
        except UnicodeDecodeError:
            try:
                old_content = self._kernel_fs.workspace_read_bytes(rel).decode("utf-8", errors="replace")
            except OSError:
                old_content = ""
        except OSError:
            old_content = ""
        old_lines = old_content.count("\n") + (1 if old_content and not old_content.endswith("\n") else 0)
        new_text = str(content or "")
        new_lines = new_text.count("\n") + (1 if new_text and not new_text.endswith("\n") else 0)
        if (
            old_lines >= _DESTRUCTIVE_SHRINK_MIN_REMOVED_LINES
            and new_lines <= old_lines * _DESTRUCTIVE_SHRINK_MAX_ADD_RATIO
        ):
            return _destructive_shrink_error(
                file_path,
                old_lines,
                new_lines,
                tool_hint=(
                    "write_file replaces the WHOLE file. To change part of an existing file use "
                    "edit_blocks (line-range form: file + start + end + replace) so untouched "
                    "code is preserved."
                ),
            )

    normalized = normalize_patch_like_write_content(
        rel,
        content,
        existing_content=old_content if operation == "modify" else None,
    )

    if normalized.error:
        return {"ok": False, "error": normalized.error}

    text = str(normalized.content or "")
    if is_empty_write_content_violation(rel, text):
        return {
            "ok": False,
            "error_type": "empty_write_content",
            "error": (
                f"Empty write content: write_file for {rel} received blank content. "
                "The COMPLETE file body must go in the `content` argument — do not "
                "narrate it in prose or reasoning. Re-emit write_file with the full file content."
            ),
        }
    if is_edit_fragment_write_violation(rel, text):
        return {
            "ok": False,
            "error_type": "edit_fragment_write",
            "error": (
                f"Edit-fragment write: write_file for {rel} received an edit fragment "
                "(a line-anchored insertion directive such as '在第 N 行之后添加'), not the "
                "complete file. write_file REPLACES the whole file, so its `content` must be the "
                "ENTIRE file body. To change part of an existing file use edit_file/precision_edit; "
                "to (re)write the file, emit write_file with the full, self-contained content."
            ),
        }
    policy_result = _validate_director_policy_for_write(
        self,
        rel=rel,
        old_content=old_content,
        new_content=text,
        operation=f"write_file:{operation}",
        tool_kwargs=kwargs,
    )
    if not policy_result.get("ok"):
        return policy_result

    # ========================================================================
    # PRE-WRITE VALIDATION GATE - Validate code syntax before writing
    # Auto-fix hallucinations if possible
    # ========================================================================
    # Only validate for code files (Python, JS, TS, etc.)
    code_extensions = {".py", ".pyw", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs"}
    # A blank sentinel file (__init__.py / py.typed / .gitkeep) is legitimately
    # empty — the EmptyCode syntax check must NOT block it, or the package marker
    # never lands -> the materialization quality gate reports it "missing" -> the
    # Director burns its budget in a repair read-loop and dead-letters
    # (factory-bench L4-19: empty backend/__init__.py blocked -> 0/3 successes).
    # A NON-empty sentinel still validates normally. Mirrors the Wall-2 sentinel
    # exemption (is_empty_write_wall2_violation, _EMPTY_WRITE_SENTINEL_BASENAMES).
    if not is_blank_sentinel_write(rel, text) and any(rel.endswith(ext) for ext in code_extensions):
        validation_result = validate_code_syntax(text, rel)
        if not validation_result.is_valid:
            error_msg = format_validation_error(validation_result, rel)
            logger.warning(
                "[PreWriteGuard] Blocked write to %s due to syntax errors: %s",
                rel,
                error_msg[:200],
            )
            return {
                "ok": False,
                "error": f"Code syntax validation failed:\n{error_msg}",
                "suggestion": (
                    "Use read_file() to inspect the exact file, then call edit_blocks "
                    "with line-range arguments: file, start, end, replace. "
                    "Change only the syntax-error lines; keep surrounding code intact. "
                    "Do not append new code for syntax repair."
                ),
                "validation_errors": [
                    {"line": e.line, "column": e.column, "message": e.message} for e in (validation_result.errors or [])
                ],
            }
        # Auto-fix: use fixed code if validation result contains fixes
        if validation_result.fixed_code is not None:
            original_text = text
            text = validation_result.fixed_code
            logger.info(
                "[PreWriteGuard] Auto-fixed hallucinations in %s: %s ->\n%s",
                rel,
                original_text[:100],
                text[:100],
            )

    full_path = str(self._kernel_fs.resolve_workspace_path(rel))
    write_result = _write_temp_verify_rename(full_path, text, encoding="utf-8")
    if not write_result.get("ok"):
        return write_result

    _emit_file_written_event(
        self,
        file_path=rel,
        operation=operation,
        old_content=old_content,
        new_content=text,
    )

    result = {
        "ok": True,
        "file": rel,
        "bytes_written": int(write_result.get("bytes_written", 0)),
        "effect_receipt": {
            "file": rel,
            "bytes_written": int(write_result.get("bytes_written", 0)),
            "operation": operation,
        },
    }
    if normalized.normalized_patch_like:
        result["normalized_patch_like_write"] = True

    result = attach_post_write_syntax_check(result, str(target))
    return _attach_director_policy_evidence(result, policy_result.get("director_policy"))


def _handle_read_file(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle read_file with budget-aware downgrade strategy.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict or raises BudgetExceededError
    """
    file = kwargs.get("file")
    path = kwargs.get("path")
    filepath = kwargs.get("filepath")
    max_bytes = kwargs.get("max_bytes", 200000)
    start_line = kwargs.get("start_line")
    end_line = kwargs.get("end_line")
    range_required = kwargs.get("range_required", False)

    try:
        normalized_start_line = int(start_line) if start_line is not None else None
    except (TypeError, ValueError):
        return {"ok": False, "error": "start_line must be an integer"}

    try:
        normalized_end_line = int(end_line) if end_line is not None else None
    except (TypeError, ValueError):
        return {"ok": False, "error": "end_line must be an integer"}

    target_path = file or path or filepath
    if not target_path:
        return {"ok": False, "error": "Missing file path"}

    rel, resolve_error = _resolve_workspace_rel(self, str(target_path))
    if rel is None:
        return resolve_error or {"ok": False, "error": f"Invalid path: {target_path}"}

    if not self._kernel_fs.workspace_exists(rel) or not self._kernel_fs.workspace_is_file(rel):
        return _not_found_error(self, str(target_path))

    # First pass: read raw bytes for size estimation
    safe_max_bytes = max(1024, min(int(max_bytes), 2_000_000))
    raw = self._kernel_fs.workspace_read_bytes(rel)

    # Estimate line count
    estimated_line_count = raw.count(b"\n") + 1

    # Budget check
    get_budget_remaining_lines(self._budget_state)
    has_range = normalized_start_line is not None or normalized_end_line is not None

    # Handle range_required enforcement
    if range_required and estimated_line_count > self._READ_WARN_LINES and not has_range:
        raise BudgetExceededError(
            f"read_file requires a range parameter for files >{self._READ_WARN_LINES} lines "
            f"(this file has ~{estimated_line_count} lines).",
            tool="read_file",
            file=rel,
            line_count=estimated_line_count,
            limit=self._READ_WARN_LINES,
            suggestion=(
                f"Use repo_read_head(file='{rel}', n=50) to read the first 50 lines, "
                f"or repo_read_slice(file='{rel}', start=1, end=200) to read a specific range. "
                f"Always specify start_line and end_line for large files."
            ),
        )

    requested_line_span: int | None = None
    if has_range:
        effective_start = max(1, normalized_start_line or 1)
        if normalized_end_line is not None:
            effective_end = max(effective_start, normalized_end_line)
            requested_line_span = max(1, effective_end - effective_start + 1)
        else:
            requested_line_span = max(1, estimated_line_count - effective_start + 1)

    # Hard limit check:
    # - full-file reads are blocked for oversized files
    # - ranged reads are allowed only when requested span is within hard limit
    if estimated_line_count > self._READ_HARD_LIMIT and (
        not has_range or (requested_line_span is not None and requested_line_span > self._READ_HARD_LIMIT)
    ):
        if has_range and requested_line_span is not None:
            message = (
                f"Requested range spans {requested_line_span} lines, exceeds hard limit of "
                f"{self._READ_HARD_LIMIT} for oversized file ({estimated_line_count} lines)"
            )
            suggestion = (
                f"Narrow the requested range to <= {self._READ_HARD_LIMIT} lines. "
                f"Example: repo_read_head(file='{rel}', n=50) or "
                f"repo_read_slice(file='{rel}', start=1, end=200)."
            )
        else:
            message = f"File has {estimated_line_count} lines, exceeds hard limit of {self._READ_HARD_LIMIT}"
            suggestion = (
                f"File is too large to read at once. Use repo_read_head(file='{rel}', n=50) to read the first 50 lines, "
                f"or repo_read_slice(file='{rel}', start=1, end=200) to read a specific range. "
                f"For large files, always specify start_line and end_line parameters."
            )
        raise BudgetExceededError(
            message,
            tool="read_file",
            file=rel,
            line_count=estimated_line_count,
            limit=self._READ_HARD_LIMIT,
            suggestion=suggestion,
        )

    # Decode and apply line range
    content_str = raw.decode("utf-8", errors="replace")
    lines = content_str.splitlines(keepends=True)

    truncated_by_range = False
    if has_range:
        total_lines = len(lines)
        start_idx = max(0, (normalized_start_line - 1) if normalized_start_line else 0)
        end_idx = min(total_lines, normalized_end_line if normalized_end_line else total_lines)
        start_idx = max(0, min(start_idx, total_lines - 1))
        end_idx = max(start_idx + 1, min(end_idx, total_lines))

        if start_idx > 0 or end_idx < total_lines:
            truncated_by_range = True
            lines = lines[start_idx:end_idx]
            actual_line_count = len(lines)
        else:
            actual_line_count = len(lines)
    else:
        actual_line_count = len(lines)

    start_offset = start_idx if has_range else 0
    formatted_lines = []
    for i, line_content in enumerate(lines):
        line_num = start_offset + 1 + i
        formatted_lines.append(f"{line_num} | {line_content}")

    content_str = "".join(formatted_lines)
    truncated = len(content_str.encode("utf-8")) > safe_max_bytes
    if truncated:
        content_str = content_str[:safe_max_bytes]

    result: dict[str, Any] = {
        "ok": True,
        "file": rel,
        "content": content_str,
        "truncated": truncated or truncated_by_range,
        "line_count": actual_line_count,
    }

    if has_range:
        result["range_used"] = {"start_line": normalized_start_line, "end_line": normalized_end_line}
    if range_required:
        result["range_required"] = True

    # Warning for large files without range
    if not has_range and estimated_line_count > self._READ_WARN_LINES:
        result["warnings"] = [
            f"Large file ({estimated_line_count} lines). "
            f"For targeted reading, use read_file with start_line and end_line parameters."
        ]

    return result


def _handle_search_replace(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle search_replace tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    import re

    file = kwargs.get("file")
    search = kwargs.get("search")
    replace = kwargs.get("replace", "")
    regex = kwargs.get("regex", False)
    replace_all = kwargs.get("replace_all", False)

    if not file or not isinstance(file, str):
        return {"ok": False, "error": "Missing or invalid file path"}
    if search is None:
        return {"ok": False, "error": "Missing search parameter"}

    rel, resolve_error = _resolve_workspace_rel(self, file)
    if rel is None:
        return resolve_error or {"ok": False, "error": f"Invalid path: {file}"}

    if not self._kernel_fs.workspace_exists(rel):
        return _not_found_error(self, str(file))
    if not self._kernel_fs.workspace_is_file(rel):
        return {"ok": False, "error": f"Path is not a file: {file}"}

    try:
        content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"Failed to read file: {e}"}

    search_text = str(search)
    replace_text = str(replace) if replace is not None else ""

    if regex:
        try:
            if replace_all:
                new_content = re.sub(search_text, replace_text, content)
                replacements = len(re.findall(search_text, content))
            else:
                new_content, replacements = re.subn(search_text, replace_text, content, count=1)
        except re.error as e:
            return {"ok": False, "error": f"Invalid regex pattern: {e}"}
    elif replace_all:
        replacements = content.count(search_text)
        new_content = content.replace(search_text, replace_text)
    elif search_text in content:
        new_content = content.replace(search_text, replace_text, 1)
        replacements = 1
    else:
        new_content = content
        replacements = 0

    if replacements == 0:
        from polaris.kernelone.tool_execution.suggestions.fuzzy import (
            _build_no_match_suggestion,
        )

        suggestion = _build_no_match_suggestion(content, search_text)
        return {
            "ok": False,
            "file": file,
            "replacements_count": 0,
            "error": "No matches found",
            "suggestion": suggestion,
        }

    policy_result = _validate_director_policy_for_write(
        self,
        rel=rel,
        old_content=content,
        new_content=new_content,
        operation="search_replace",
        tool_kwargs=kwargs,
    )
    if not policy_result.get("ok"):
        return policy_result

    full_path = str(self._kernel_fs.resolve_workspace_path(rel))
    write_result = _write_temp_verify_rename(full_path, new_content, encoding="utf-8")
    if not write_result.get("ok"):
        return write_result

    _emit_file_written_event(
        self,
        file_path=rel,
        operation="modify",
        old_content=content,
        new_content=new_content,
    )

    return _attach_director_policy_evidence(
        {
            "ok": True,
            "file": rel,
            "replacements_count": replacements,
            "effect_receipt": {
                "file": rel,
                "replacements_count": replacements,
                "operation": "modify",
            },
        },
        policy_result.get("director_policy"),
    )


def _handle_edit_file(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle edit_file tool call (line range or text replace mode).

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    file = kwargs.get("file")
    start_line = kwargs.get("start_line")
    end_line = kwargs.get("end_line")
    content = kwargs.get("content")
    search = kwargs.get("search")
    replace = kwargs.get("replace")
    regex = kwargs.get("regex", False)

    if not file or not isinstance(file, str):
        return {"ok": False, "error": "Missing or invalid file path"}

    rel, resolve_error = _resolve_workspace_rel(self, file)
    if rel is None:
        return resolve_error or {"ok": False, "error": f"Invalid path: {file}"}

    if not self._kernel_fs.workspace_exists(rel):
        return _not_found_error(self, str(file))
    if not self._kernel_fs.workspace_is_file(rel):
        return {"ok": False, "error": f"Path is not a file: {file}"}

    try:
        file_content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
        lines = file_content.splitlines(keepends=True)
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"Failed to read file: {e}"}

    # Line range mode
    if start_line is not None or end_line is not None:
        return _edit_file_line_mode(self, rel, lines, start_line, end_line, content or "", tool_kwargs=kwargs)

    # Text replace mode
    if search is not None:
        return _edit_file_replace_mode(self, rel, file_content, search, replace or "", regex, tool_kwargs=kwargs)

    return {"ok": False, "error": "Must specify either line range (start_line/end_line) or search/replace"}


def _normalize_block_input(text: Any) -> str:
    """Make weak-model edit payloads parseable.

    Two common low-precision-model artifacts are repaired:
    - the whole payload wrapped in a Markdown code fence (```/```python);
    - real newlines collapsed into literal ``\\n``/``\\t`` escapes (single-line JSON).
    """
    if not isinstance(text, str):
        return ""
    s = text
    stripped = s.strip()
    if stripped.startswith("```"):
        fence_lines = stripped.splitlines()
        if fence_lines and fence_lines[0].lstrip().startswith("```"):
            fence_lines = fence_lines[1:]
        if fence_lines and fence_lines[-1].strip().startswith("```"):
            fence_lines = fence_lines[:-1]
        s = "\n".join(fence_lines)
    # Only unescape when the payload has no real newlines but does carry escaped ones —
    # never mangle a payload that already contains genuine newlines (e.g. literal "\n"
    # inside a string the model intends to write).
    if "\n" not in s and "\\n" in s:
        s = s.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    return s


def _has_search_replace_markers(text: str) -> bool:
    """True when the text already looks like SEARCH/REPLACE block(s)."""
    if not text:
        return False
    return any(re.match(r"^\s*<{3,}\s*(SEARCH|ORIGINAL|SOURCE)\b", line) for line in text.splitlines())


def _coerce_line_no(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _synthesize_line_range_block(
    self: AgentAccelToolExecutor,
    file: str | None,
    start: Any,
    end: Any,
    replacement: Any,
) -> tuple[str | None, dict[str, Any] | None]:
    """Build a SEARCH/REPLACE block from a line range (weak-model affordance).

    Low-precision models reliably express edits as "replace lines start..end of FILE
    with NEW CODE" but cannot reproduce exact SEARCH text. We read the EXACT current
    lines ourselves (so the SEARCH text is guaranteed to match) and hand the synthesized
    block to the normal validation/apply path. Returns (blocks_text, None) on success or
    (None, error_dict) otherwise.
    """
    if not file:
        return None, {"ok": False, "error": "line-range edit requires a 'file' argument."}
    rel, resolve_error = _resolve_workspace_rel(self, str(file))
    if rel is None:
        return None, (resolve_error or {"ok": False, "error": f"Invalid path: {file}"})
    if not self._kernel_fs.workspace_exists(rel):
        return None, _not_found_error(self, str(file))
    if not self._kernel_fs.workspace_is_file(rel):
        return None, {"ok": False, "error": f"Path is not a file: {file}"}
    try:
        content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, {"ok": False, "error": f"Failed to read {file}: {exc}"}

    lines = content.splitlines(keepends=True)
    total = len(lines)
    start_no = _coerce_line_no(start)
    end_no = _coerce_line_no(end)
    if start_no is None or end_no is None:
        return None, {"ok": False, "error": "line-range edit requires integer start and end line numbers."}
    if start_no < 1 or end_no < 1 or start_no > end_no or start_no > total:
        return None, {
            "ok": False,
            "error": f"Invalid line range [{start_no},{end_no}] for {file} (file has {total} lines).",
        }
    end_no = min(end_no, total)
    search_text = "".join(lines[start_no - 1 : end_no])

    repl = "" if replacement is None else str(replacement)
    if not repl.strip():
        return None, {
            "ok": False,
            "error": (
                f"line-range edit for {file}[{start_no}:{end_no}] has no replacement code. "
                "Provide the new code for those lines via 'replace' (or 'new_text'/'content')."
            ),
            "suggestion": "Pass the actual replacement source for the range; an empty replacement is rejected.",
        }
    # Preserve the trailing-newline shape of the replaced slice.
    if search_text.endswith("\n") and not repl.endswith("\n"):
        repl = repl + "\n"
    removed_lines = end_no - start_no + 1
    added_lines = len(repl.splitlines())
    if (
        removed_lines >= _DESTRUCTIVE_SHRINK_MIN_REMOVED_LINES
        and added_lines <= removed_lines * _DESTRUCTIVE_SHRINK_MAX_ADD_RATIO
    ):
        return None, _destructive_shrink_error(
            f"{file}[{start_no}:{end_no}]",
            removed_lines,
            added_lines,
            tool_hint=(
                "Narrow start/end to ONLY the lines you are actually changing (a bug fix is "
                "usually < 30 lines) and keep all surrounding code intact. Make several small "
                "line-range edits if multiple spots need changes."
            ),
        )
    block = f"<<<< SEARCH:{file}\n{search_text}====\n{repl}>>>> REPLACE\n"
    return block, None


_JSON_EDIT_FILE_KEYS = (
    "file",
    "path",
    "file_path",
    "filepath",
    "filename",
    "target_file",
    "target_path",
)
_JSON_EDIT_REPLACE_KEYS = ("replace", "new_text", "new_content", "replacement", "code", "content")


def _synthesize_blocks_from_json_payload(
    self: AgentAccelToolExecutor,
    blocks_text: str,
    default_file: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Recognize a JSON-encoded line-range edit (list or object) inside ``blocks``.

    Live capture (phase1smoke6, qwen3.6): the model emitted a fully-correct
    STRUCTURED edit as JSON inside the blocks argument —
    ``[{"start_line":1019,"end_line":1020,"file":"django/db/models/expressions.py",
    "replace":"..."}]`` — and the prose guard rejected it. Punishing structure
    is link-level self-harm: normalize it into synthesized SEARCH/REPLACE blocks
    through the SAME line-range path (shrink gate included).

    Returns ``(blocks, None)`` on success, ``(None, error)`` when the payload is
    line-range-shaped but invalid, and ``(None, None)`` when the payload is not
    JSON-edit shaped at all (caller falls through to the normal parser).
    """
    candidate = blocks_text.strip()
    # Live shape #4 (factory-bench L1-05): a leading YAML-ish label before the
    # JSON — 'blocks: [ {"path": ..., "start": 1, ...} ]'. Strip one leading
    # `word:` tag when JSON follows so the structured intent is recognized.
    label_match = re.match(r"^[A-Za-z_]{1,16}\s*:\s*(?=[\[{])", candidate)
    if label_match:
        candidate = candidate[label_match.end() :].strip()
    if not candidate or candidate[0] not in "[{":
        return None, None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None, None
    items: list[Any] = parsed if isinstance(parsed, list) else [parsed]
    if not items:
        return None, None
    synthesized: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            return None, None
        start = item.get("start", item.get("start_line"))
        end = item.get("end", item.get("end_line"))
        if start is None or end is None:
            # Nested-parameter-name shape (factory-bench L1-01 live capture):
            # [{"blocks": "<payload>"}] — the model wrapped the ARGUMENT NAME
            # inside the JSON. Unwrap single-item payloads and let the caller
            # re-enter the normal pipeline (marker parse / prose guard).
            inner = item.get("blocks")
            if len(items) == 1 and isinstance(inner, str) and inner.strip():
                return None, {"__unwrap_blocks__": inner, "__unwrap_file__": item.get("file") or default_file}
            return None, None
        item_file = next(
            (str(item[key]) for key in _JSON_EDIT_FILE_KEYS if item.get(key)),
            None,
        ) or (str(default_file) if default_file else None)
        replacement = next(
            (item[key] for key in _JSON_EDIT_REPLACE_KEYS if item.get(key) is not None),
            None,
        )
        block, err = _synthesize_line_range_block(self, item_file, start, end, replacement)
        if err is not None:
            return None, err
        synthesized.append(block or "")
    return "".join(synthesized), None


def _handle_edit_blocks(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle edit_blocks tool call (SEARCH/REPLACE block format).

    Implements two-phase commit (validation + execution) for atomic multi-file edits.
    Also accepts a weak-model-friendly line-range form (file + start/end + replacement),
    and normalizes code-fenced / escaped-newline payloads.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    file = kwargs.get("file") or kwargs.get("path") or kwargs.get("file_path") or kwargs.get("filepath")
    raw_blocks_value = kwargs.get("blocks") or kwargs.get("content") or kwargs.get("edits") or kwargs.get("diff")
    blocks_text = _normalize_block_input(raw_blocks_value)

    # Weak-model line-range affordance: when start/end line numbers are supplied and the
    # payload is not already a SEARCH/REPLACE block, synthesize one from the exact file
    # lines. This removes the hardest task for low-precision models — reproducing SEARCH
    # text byte-for-byte — while reusing the same validation/apply path below.
    start = kwargs.get("start", kwargs.get("start_line"))
    end = kwargs.get("end", kwargs.get("end_line"))
    if start is not None and end is not None and not _has_search_replace_markers(blocks_text):
        replacement = (
            kwargs.get("replace")
            if kwargs.get("replace") is not None
            else kwargs.get("new_text")
            if kwargs.get("new_text") is not None
            else kwargs.get("new_content")
            if kwargs.get("new_content") is not None
            else kwargs.get("replacement")
            if kwargs.get("replacement") is not None
            else kwargs.get("code")
            if kwargs.get("code") is not None
            else (blocks_text or None)
        )
        synth, err = _synthesize_line_range_block(self, file, start, end, replacement)
        if err is not None:
            return err
        blocks_text = synth or ""
    elif blocks_text and not _has_search_replace_markers(blocks_text):
        # JSON-in-blocks affordance: a structured line-range edit hiding inside
        # the blocks argument is normalized, not rejected as prose. Parse the
        # RAW value first — _normalize_block_input's escape repair corrupts
        # valid JSON (the \n inside quoted strings must stay escaped).
        json_blocks: str | None = None
        json_err: dict[str, Any] | None = None
        if isinstance(raw_blocks_value, str):
            json_blocks, json_err = _synthesize_blocks_from_json_payload(self, raw_blocks_value, default_file=file)
        if json_blocks is None and json_err is None:
            json_blocks, json_err = _synthesize_blocks_from_json_payload(self, blocks_text, default_file=file)
        if json_err is not None and "__unwrap_blocks__" in json_err:
            blocks_text = _normalize_block_input(json_err["__unwrap_blocks__"])
            file = json_err.get("__unwrap_file__") or file
            json_err = None
        if json_err is not None:
            return json_err
        if json_blocks is not None:
            blocks_text = json_blocks

    if not blocks_text or not isinstance(blocks_text, str):
        return {
            "ok": False,
            "error": (
                "Missing edit payload. EASIEST: call edit_blocks with file + start + end + replace "
                "(replace lines [start,end] with the new code). Alternatively provide SEARCH/REPLACE "
                "formatted blocks."
            ),
        }

    # Parse edit blocks (with file argument as default_filepath fallback)
    try:
        blocks = parse_edit_blocks(blocks_text, default_filepath=file)
    except (ValueError, TypeError, AttributeError, KeyError, IndexError) as e:
        logger.warning("Failed to parse edit blocks: %s (%s)", type(e).__name__, e)
        return {
            "ok": False,
            "error": f"Failed to parse edit blocks: {e}",
            "suggestion": "Ensure blocks follow the format: <<<< SEARCH[:filepath]\\n<original>\\n====\\n<new>\\n>>>> REPLACE",
        }

    if not blocks:
        # Weak-model teaching error: live capture (Qwen3.6, 2026-06-10) showed models
        # passing their narration ("Let me first read the file...") as 'blocks'. Echo
        # what was received and show BOTH complete forms — imprecise models imitate
        # concrete examples far better than they follow format descriptions.
        preview = " ".join(blocks_text.strip().split())[:120]
        target_missing = False
        if file:
            rel_probe, _probe_err = _resolve_workspace_rel(self, str(file))
            target_missing = rel_probe is not None and not self._kernel_fs.workspace_exists(rel_probe)
        if target_missing:
            # Factory-bench live capture: models try to CREATE new files through
            # edit_blocks by stuffing whole-file code into 'blocks'. There is
            # nothing to search/replace in a nonexistent file — teach the right
            # tool instead of sending them to read_file.
            error = f"edit_blocks cannot create new files ({file} does not exist). Use write_file to create it."
            return {
                "ok": False,
                "error": error,
                "suggestion": (
                    f'Call write_file with the COMPLETE file content: {{"file": "{file}", '
                    '"content": "<the full source code>"}}. edit_blocks is only for '
                    "modifying lines of EXISTING files."
                ),
                "error_type": "new_file_via_edit_blocks",
                "retryable": True,
            }
        # Filename-plus-fenced-content shape (factory-bench L1-01 README task):
        # blocks = "README.md ```markdown <full file>```" — unambiguous
        # whole-file-write intent through the wrong tool. Teach write_file with
        # the exact filename instead of the generic prose lecture.
        stripped_lines = [line.strip() for line in blocks_text.strip().splitlines() if line.strip()]
        leading_name = stripped_lines[0].split()[0].strip("*`'\"") if stripped_lines else ""
        looks_like_filename = bool(re.fullmatch(r"[\w./-]+\.[A-Za-z0-9]{1,8}", leading_name))
        if looks_like_filename and "```" in blocks_text:
            return {
                "ok": False,
                "error": (
                    f"edit_blocks received a filename plus full file content for {leading_name}. "
                    "That is a whole-file write, not an edit."
                ),
                "suggestion": (
                    f'Call write_file instead: {{"file": "{leading_name}", '
                    '"content": "<the full file content WITHOUT the ``` fence>"}}.'
                ),
                "error_type": "whole_file_via_edit_blocks",
                "retryable": True,
            }
        if not _has_search_replace_markers(blocks_text):
            error = (
                "edit_blocks received prose/narration instead of edit content "
                f"(got: '{preview}'). The 'blocks' argument must contain ONLY the edit itself — "
                "never explanations, plans, or intentions."
            )
        else:
            error = "No valid edit blocks found in input"
        return {
            "ok": False,
            "error": error,
            "suggestion": (
                "Two accepted forms. LINE-RANGE (easiest): "
                '{"file": "path/to/file.py", "start": <first line>, "end": <last line>, '
                '"replace": "<the complete new code for those lines>"} — no SEARCH text needed. '
                "SEARCH/REPLACE: pass 'blocks' exactly like:\n"
                "<<<< SEARCH:path/to/file.py\n"
                "<exact existing lines copied from the file>\n"
                "====\n"
                "<new lines>\n"
                ">>>> REPLACE\n"
                "If you have not read the file yet, call read_file first, then transcribe the edit. "
                "Prose descriptions are NOT executable."
            ),
        }

    # Validate blocks
    validation_errors = validate_edit_blocks(blocks)
    if validation_errors:
        return {
            "ok": False,
            "error": f"Invalid edit blocks: {'; '.join(validation_errors)}",
        }

    # Filter out no-op blocks (search == replace — LLM hallucination pattern)
    noop_count = sum(1 for b in blocks if b.search_text == b.replace_text)
    if noop_count:
        blocks = [b for b in blocks if b.search_text != b.replace_text]
        logger.info("Filtered %d no-op edit blocks (search == replace)", noop_count)

    if not blocks:
        return {
            "ok": False,
            "error": f"All {noop_count} edit block(s) had identical search and replace text (no-op). "
            "This usually means the content was not actually modified. "
            "Please ensure the REPLACE section contains the actual changes you want to make.",
        }

    # Determine file from blocks or args
    target_file = file
    if not target_file and blocks:
        # Use first block's filepath
        target_file = blocks[0].filepath

    if not target_file:
        return {
            "ok": False,
            "error": "No file path specified. Either provide 'file' argument or specify path in SEARCH header (<<<< SEARCH:path/to/file)",
        }

    rel, resolve_error = _resolve_workspace_rel(self, str(target_file))
    if rel is None:
        return resolve_error or {"ok": False, "error": f"Invalid path: {target_file}"}

    # Check file exists
    if not self._kernel_fs.workspace_exists(rel):
        return _not_found_error(self, str(target_file))

    if not self._kernel_fs.workspace_is_file(rel):
        return {"ok": False, "error": f"Path is not a file: {target_file}"}

    # ========================================================================
    # PHASE 1: VALIDATION - Dry run all blocks to ensure they can be applied
    # ========================================================================
    file_contents: dict[str, tuple[str, str]] = {}  # rel -> (original_content, new_content)
    validation_results = []
    all_valid = True

    for i, block in enumerate(blocks):
        block_file = block.filepath or target_file
        block_rel, block_resolve_error = _resolve_workspace_rel(self, str(block_file))
        if block_rel is None:
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": False,
                    "error": str((block_resolve_error or {}).get("error", "Invalid path")),
                }
            )
            all_valid = False
            continue

        # Check file exists
        if not self._kernel_fs.workspace_exists(block_rel):
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": False,
                    "error": str(_not_found_error(self, str(block_file)).get("error", "File not found")),
                }
            )
            all_valid = False
            continue

        if not self._kernel_fs.workspace_is_file(block_rel):
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": False,
                    "error": "Path is not a file",
                }
            )
            all_valid = False
            continue

        # Read content (or use cached)
        if block_rel not in file_contents:
            try:
                content = self._kernel_fs.workspace_read_text(block_rel, encoding="utf-8")
                file_contents[block_rel] = (content, content)  # (original, current)
            except (OSError, UnicodeDecodeError) as e:
                validation_results.append(
                    {
                        "index": i,
                        "file": block_file,
                        "valid": False,
                        "error": f"Failed to read: {e}",
                    }
                )
                all_valid = False
                continue

        # Try to apply block (dry run)
        original, current = file_contents[block_rel]

        # ========================================================================
        # PRE-REPLACE VALIDATION - Validate replacement text syntax
        # Auto-fix hallucinations if possible
        # ========================================================================
        code_extensions = {".py", ".pyw", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs"}
        is_code_file = any(block_rel.endswith(ext) for ext in code_extensions)

        new_content, metadata = fuzzy_replace(current, block.search_text, block.replace_text)

        if metadata.get("success"):
            # Validate the RESULTING file, not the replace fragment. A SEARCH/REPLACE
            # replacement is legitimately partial code (leading indentation, an open
            # construct closed by surrounding lines), so parsing it standalone raises
            # spurious "unexpected indent"/"unbalanced" errors that previously rejected
            # valid edits. Checking the full post-apply content still catches edits that
            # actually break file syntax.
            if is_code_file and block.replace_text:
                file_validation = validate_code_syntax(new_content, block_rel)
                # Gate on the EDIT's effect, not the file's pre-existing state: reject
                # only when this edit INTRODUCES a syntax error (pre-edit content was
                # valid, post-edit content is not). If the pre-edit file already failed
                # validation, the edit is not the cause, so blocking it would force a
                # hardcoded bypass. This keeps the gate fail-closed without rejecting
                # legitimate edits to files that merely trip a heuristic.
                introduced_error = not file_validation.is_valid and validate_code_syntax(current, block_rel).is_valid
                if introduced_error:
                    error_msg = format_validation_error(file_validation, block_rel)
                    validation_results.append(
                        {
                            "index": i,
                            "file": block_file,
                            "valid": False,
                            "error": f"Edit introduces syntax errors: {error_msg[:200]}",
                            "search_preview": block.search_text[:100] if block.search_text else "",
                        }
                    )
                    all_valid = False
                    continue
            # Update current content for next block targeting same file
            file_contents[block_rel] = (original, new_content)
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": True,
                    "similarity": metadata.get("similarity", 1.0),
                    "fixes": metadata.get("fixes_applied", []),
                }
            )
        elif _should_use_whole_file_placeholder_replacement(
            search_text=block.search_text,
            replace_text=block.replace_text,
            rel=block_rel,
            block_count=len(blocks),
        ):
            file_contents[block_rel] = (original, block.replace_text)
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": True,
                    "mode": "whole_file_placeholder_replacement",
                    "search_preview": block.search_text[:100] if block.search_text else "",
                }
            )
        elif _should_use_whole_file_prefix_replacement(
            current_text=current,
            search_text=block.search_text,
            replace_text=block.replace_text,
            rel=block_rel,
            block_count=len(blocks),
        ):
            file_contents[block_rel] = (original, block.replace_text)
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": True,
                    "mode": "whole_file_prefix_replacement",
                    "search_preview": block.search_text[:100] if block.search_text else "",
                }
            )
        else:
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": False,
                    "error": "No match found",
                    "search_preview": block.search_text[:100] if block.search_text else "",
                }
            )
            all_valid = False

    # If validation failed, return error without modifying any files
    if not all_valid:
        failed = [r for r in validation_results if not r["valid"]]
        return {
            "ok": False,
            "error": f"Validation failed for {len(failed)} block(s). No files were modified.",
            "failed_blocks": failed,
            "suggestion": "Check that SEARCH text exactly matches file content (including whitespace). Use repo_read_slice to verify exact content.",
        }

    policy_evidence_by_file: dict[str, dict[str, Any]] = {}
    policy_denials: list[dict[str, Any]] = []
    for block_rel, (original, new_content) in file_contents.items():
        if original == new_content:
            continue
        policy_result = _validate_director_policy_for_write(
            self,
            rel=block_rel,
            old_content=original,
            new_content=new_content,
            operation="edit_blocks",
            tool_kwargs=kwargs,
        )
        if policy_result.get("ok"):
            policy_evidence = policy_result.get("director_policy")
            if isinstance(policy_evidence, dict):
                policy_evidence_by_file[block_rel] = policy_evidence
            continue
        policy_denials.append(
            {
                "file": block_rel,
                "error": policy_result.get("error", "Director write policy denied"),
                "director_policy": policy_result.get("director_policy"),
            }
        )

    if policy_denials:
        return {
            "ok": False,
            "error": f"Director write policy denied {len(policy_denials)} file(s). No files were modified.",
            "error_type": "director_write_policy_denied",
            "blocked": True,
            "director_policy_denials": policy_denials,
            "validation_results": validation_results,
        }

    # ========================================================================
    # PHASE 2: EXECUTION - All blocks valid, now actually write files
    # ========================================================================
    results = []
    write_errors = []

    for block_rel, (original, new_content) in file_contents.items():
        if original == new_content:
            continue  # No changes needed

        block_full_path = str(self._kernel_fs.resolve_workspace_path(block_rel))
        write_result = _write_temp_verify_rename(block_full_path, new_content, encoding="utf-8")
        if not write_result.get("ok"):
            write_errors.append(
                {
                    "file": block_rel,
                    "error": str(write_result.get("error", "Unknown write error")),
                }
            )
            continue

        _emit_file_written_event(
            self,
            file_path=block_rel,
            operation="modify",
            old_content=original,
            new_content=new_content,
        )
        results.append(
            {
                "file": block_rel,
                "bytes_changed": len(new_content) - len(original),
                "director_policy": policy_evidence_by_file.get(block_rel),
            }
        )

    # Build response
    response: dict[str, Any] = {
        "ok": len(write_errors) == 0,
        "blocks_total": len(blocks),
        "blocks_applied": len([r for r in validation_results if r.get("valid")]),
        "files_modified": len(results),
        "results": results,
        "validation_results": validation_results,
    }

    if write_errors:
        response["write_errors"] = write_errors
        response["error"] = f"Failed to write {len(write_errors)} file(s)"
        # Note: At this point, some files may have been modified while others failed
        # This is a partial failure scenario that should be handled by the caller
    else:
        response["effect_receipt"] = {
            "files_modified": [r["file"] for r in results],
            "operation": "modify",
            "director_policy": policy_evidence_by_file,
        }

    return response


def _edit_file_line_mode(
    self: AgentAccelToolExecutor,
    rel: str,
    lines: list[str],
    start_line: int | None,
    end_line: int | None,
    content: str,
    *,
    tool_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute line range edit mode."""
    total_lines = len(lines)

    # Handle negative line numbers
    if start_line is not None and start_line < 0:
        start_line = total_lines + start_line + 1
    if end_line is not None and end_line < 0:
        end_line = total_lines + end_line + 1

    # Default: replace entire file or append to end
    start = max(1, start_line) if start_line is not None else 1
    end = min(total_lines, end_line) if end_line is not None else total_lines

    new_lines = [*lines, content] if start > total_lines else [*lines[: start - 1], content, *lines[end:]]

    new_content = "".join(new_lines)
    old_content = "".join(lines)
    policy_result = _validate_director_policy_for_write(
        self,
        rel=rel,
        old_content=old_content,
        new_content=new_content,
        operation="edit_file:line_range",
        tool_kwargs=tool_kwargs,
    )
    if not policy_result.get("ok"):
        return policy_result

    full_path = str(self._kernel_fs.resolve_workspace_path(rel))
    write_result = _write_temp_verify_rename(full_path, new_content, encoding="utf-8")
    if not write_result.get("ok"):
        return write_result

    _emit_file_written_event(
        self,
        file_path=rel,
        operation="modify",
        old_content=old_content,
        new_content=new_content,
    )

    return _attach_director_policy_evidence(
        {
            "ok": True,
            "file": rel,
            "mode": "line_range",
            "lines_affected": end - start + 1 if end >= start else 0,
            "effect_receipt": {
                "file": rel,
                "mode": "line_range",
                "operation": "modify",
            },
        },
        policy_result.get("director_policy"),
    )


def _edit_file_replace_mode(
    self: AgentAccelToolExecutor,
    rel: str,
    content: str,
    search: str,
    replace: str,
    regex: bool,
    *,
    tool_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute text replace edit mode with fuzzy matching fallback."""
    import re

    count = 0
    new_content = content
    fuzzy_metadata = None

    if regex:
        try:
            new_content, count = re.subn(search, replace, content, count=1)
        except re.error as e:
            return {"ok": False, "error": f"Invalid regex pattern: {e}"}
    elif search in content:
        # Exact match
        new_content = content.replace(search, replace, 1)
        count = 1
    else:
        # Try fuzzy matching to handle LLM character-level hallucinations
        # e.g., 'return0' -> 'return 0', wrong indentation, etc.
        new_content, fuzzy_metadata = fuzzy_replace(content, search, replace)
        if fuzzy_metadata.get("success"):
            count = 1
            logger.info(
                "[FuzzyReplace] Applied fuzzy match: similarity=%.2f, fixes=%s",
                fuzzy_metadata.get("similarity", 0),
                fuzzy_metadata.get("fixes_applied", []),
            )

    if count == 0:
        from polaris.kernelone.tool_execution.suggestions.fuzzy import (
            _build_no_match_suggestion,
        )

        suggestion = _build_no_match_suggestion(content, search)
        return {
            "ok": False,
            "file": rel,
            "replacements_count": 0,
            "error": "No matches found",
            "suggestion": suggestion,
        }

    policy_result = _validate_director_policy_for_write(
        self,
        rel=rel,
        old_content=content,
        new_content=new_content,
        operation="edit_file:text_replace",
        tool_kwargs=tool_kwargs,
    )
    if not policy_result.get("ok"):
        return policy_result

    full_path = str(self._kernel_fs.resolve_workspace_path(rel))
    write_result = _write_temp_verify_rename(full_path, new_content, encoding="utf-8")
    if not write_result.get("ok"):
        return write_result

    result: dict[str, Any] = {
        "ok": True,
        "file": rel,
        "mode": "text_replace",
        "replacements_count": count,
        "effect_receipt": {
            "file": rel,
            "mode": "text_replace",
            "replacements_count": count,
            "operation": "modify",
        },
    }

    # Include fuzzy matching info if used
    if fuzzy_metadata and not fuzzy_metadata.get("exact", True):
        result["fuzzy_match"] = {
            "similarity": fuzzy_metadata.get("similarity"),
            "fixes_applied": fuzzy_metadata.get("fixes_applied"),
            "original_matched": fuzzy_metadata.get("original_matched"),
        }

    _emit_file_written_event(
        self,
        file_path=rel,
        operation="modify",
        old_content=content,
        new_content=new_content,
    )

    return _attach_director_policy_evidence(result, policy_result.get("director_policy"))


def _handle_append_to_file(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle append_to_file tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    file = kwargs.get("file")
    content = kwargs.get("content", "")
    ensure_newline = kwargs.get("ensure_newline", True)
    create_if_missing = kwargs.get("create_if_missing", True)

    if not file or not isinstance(file, str):
        return {"ok": False, "error": "Missing or invalid file path"}

    rel, resolve_error = _resolve_workspace_rel(self, file)
    if rel is None:
        return resolve_error or {"ok": False, "error": f"Invalid path: {file}"}
    content_text = str(content) if content is not None else ""

    # File doesn't exist
    if not self._kernel_fs.workspace_exists(rel):
        if not create_if_missing:
            return _not_found_error(self, str(file))
        policy_result = _validate_director_policy_for_write(
            self,
            rel=rel,
            old_content="",
            new_content=content_text,
            operation="append_to_file:create",
            tool_kwargs=kwargs,
        )
        if not policy_result.get("ok"):
            return policy_result
        full_path = str(self._kernel_fs.resolve_workspace_path(rel))
        write_result = _write_temp_verify_rename(full_path, content_text, encoding="utf-8")
        if not write_result.get("ok"):
            return write_result
        _emit_file_written_event(
            self,
            file_path=rel,
            operation="create",
            old_content="",
            new_content=content_text,
        )
        return _attach_director_policy_evidence(
            {
                "ok": True,
                "file": rel,
                "bytes_appended": len(content_text.encode("utf-8")),
                "created": True,
                "effect_receipt": {
                    "file": rel,
                    "bytes_appended": len(content_text.encode("utf-8")),
                    "operation": "create",
                },
            },
            policy_result.get("director_policy"),
        )

    if not self._kernel_fs.workspace_is_file(rel):
        return {"ok": False, "error": f"Path is not a file: {file}"}

    try:
        existing_content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"Failed to read file: {e}"}

    old_content = existing_content
    if ensure_newline and existing_content and not existing_content.endswith("\n"):
        existing_content += "\n"

    new_content = existing_content + content_text
    policy_result = _validate_director_policy_for_write(
        self,
        rel=rel,
        old_content=old_content,
        new_content=new_content,
        operation="append_to_file:modify",
        tool_kwargs=kwargs,
    )
    if not policy_result.get("ok"):
        return policy_result

    full_path = str(self._kernel_fs.resolve_workspace_path(rel))
    write_result = _write_temp_verify_rename(full_path, new_content, encoding="utf-8")
    if not write_result.get("ok"):
        return write_result

    _emit_file_written_event(
        self,
        file_path=rel,
        operation="modify",
        old_content=old_content,
        new_content=new_content,
    )

    return _attach_director_policy_evidence(
        {
            "ok": True,
            "file": rel,
            "bytes_appended": len(content_text.encode("utf-8")),
            "created": False,
            "effect_receipt": {
                "file": rel,
                "bytes_appended": len(content_text.encode("utf-8")),
                "operation": "modify",
            },
        },
        policy_result.get("director_policy"),
    )


def _emit_file_written_event(
    self: AgentAccelToolExecutor,
    *,
    file_path: str,
    operation: str,
    old_content: str,
    new_content: str,
) -> None:
    """Emit FILE_WRITTEN event for observer diff projection."""
    bus = _resolve_message_bus(self)
    if bus is None:
        return

    try:
        from polaris.kernelone.events.file_event_broadcaster import (
            broadcast_file_written,
            calculate_patch,
        )

        normalized_path = str(file_path or "").strip().replace("\\", "/")
        if not normalized_path:
            return
        op = str(operation or "modify").strip().lower()
        if op not in {"create", "modify", "delete"}:
            op = "modify"
        old_text = str(old_content or "")
        new_text = str(new_content or "")
        patch = calculate_patch(old_text, new_text)
        broadcast_file_written(
            file_path=normalized_path,
            operation=op,
            content_size=len(new_text),
            task_id="",
            patch=patch,
            message_bus=bus,
            worker_id=self._worker_id,
        )
    except (ImportError, AttributeError, TypeError) as exc:
        logger.debug("file edit event emit failed for %s: %s", file_path, exc)


def _resolve_message_bus(self: AgentAccelToolExecutor) -> Any | None:
    """Resolve message bus from global registry."""
    if self._message_bus is not None:
        return self._message_bus

    from polaris.kernelone.events import get_global_bus

    bus = get_global_bus()
    if bus is not None:
        self._message_bus = bus
        return bus
    return None
