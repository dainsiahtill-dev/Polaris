"""Context gatherer — deterministic, rule-based context collection for Director.

Migrated from ``polaris.cells.director.execution.internal.context_gatherer``.

Instead of asking an LLM "what should I read?", this module applies fixed rules
to collect just enough context for the CodeWriter LLM to make correct changes.

Design constraints:
  - Zero LLM calls.
  - Uses the same tool wrappers already in director.py.
  - READ-ONLY operations only.
  - Returns a plain dict suitable for JSON serialisation into a prompt.

Cross-boundary dependency:
  ``ExecutionMode`` is currently imported from
  ``polaris.cells.director.execution.internal.existence_gate`` (the Facade cell).
  Once director.runtime is migrated (Phase 4), update this import to:
    ``polaris.cells.director.runtime.internal.existence_gate``
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from polaris.domain.director.context_constants import (
    HEAD_LINES,
    MAX_FILE_CHARS,
    MAX_SIMILAR,
    MAX_TREE_CHARS,
)

# NOTE: Once director.runtime is migrated (Phase 4), update this to:
#   from polaris.cells.director.runtime.internal.existence_gate import ExecutionMode

if TYPE_CHECKING:
    from polaris.cells.director.tasking.public import ExecutionMode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


class GatheredContext:
    """Container for all context collected before the CodeWriter LLM call."""

    def __init__(self) -> None:
        self.mode: ExecutionMode = "modify"
        self.tree: str = ""
        self.target_contents: dict[str, str] = {}  # rel_path -> file text
        self.reference_files: dict[str, str] = {}  # rel_path -> file text (similar / peer files)
        self.package_meta: str = ""  # package.json / pyproject / requirements.txt
        self.extra: dict[str, Any] = {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "tree": self.tree,
            "target_contents": self.target_contents,
            "reference_files": self.reference_files,
            "package_meta": self.package_meta,
            "extra": self.extra,
        }


def gather(
    mode: ExecutionMode,
    target_files: list[str],
    workspace: str,
    *,
    run_tool: Any,  # Callable: run_tool(tool_name, **kwargs) -> dict
    log_fn: Any | None = None,  # Optional: log_fn(msg: str) -> None
) -> GatheredContext:
    """Collect context appropriate for *mode* without any LLM involvement.

    Args:
        mode:         Result of :func:`existence_gate.check_mode`.
        target_files: Relative paths of files the task will touch.
        workspace:    Absolute workspace root.
        run_tool:     Callable that executes a named tool and returns its dict
                      output (same contract as existing director tooling).
        log_fn:       Optional logging function for debug output.

    Returns:
        A :class:`GatheredContext` ready to be serialised into the CodeWriter
        prompt.
    """
    ctx = GatheredContext()
    ctx.mode = mode

    def _log(msg: str) -> None:
        if log_fn:
            try:
                log_fn(msg)
            except (RuntimeError, ValueError) as exc:
                logger.debug("Context gatherer log callback failed: %s", exc)

    # ------------------------------------------------------------------
    # 1. Always: collect a shallow repo tree for structural orientation.
    # ------------------------------------------------------------------
    try:
        tree_result = run_tool("repo_tree", path=".", depth=3)
        raw_tree = str(tree_result.get("stdout") or tree_result.get("tree") or "")
        ctx.tree = raw_tree[:MAX_TREE_CHARS]
        _log(f"[CTX] repo_tree collected ({len(ctx.tree)} chars)")
    except (RuntimeError, ValueError) as exc:
        _log(f"[CTX] repo_tree failed: {exc}")

    # ------------------------------------------------------------------
    # 2. Always: collect lightweight package metadata.
    # ------------------------------------------------------------------
    ctx.package_meta = _read_package_meta(workspace, run_tool, _log)

    # ------------------------------------------------------------------
    # 3. Mode-specific collection.
    # ------------------------------------------------------------------
    if mode == "create":
        _gather_create(ctx, target_files, workspace, run_tool, _log)
    else:
        # "modify" or "mixed" — read existing target files first, then any
        # missing ones get the create treatment.
        _gather_modify(ctx, target_files, workspace, run_tool, _log)

    return ctx


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_package_meta(
    workspace: str,
    run_tool: Any,
    log_fn: Any,
) -> str:
    """Read the first package/dependency manifest found in the workspace."""
    candidates = [
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
    ]
    for rel in candidates:
        full = os.path.join(workspace, rel)
        if os.path.exists(full):
            try:
                result = run_tool("repo_read_head", file=rel, lines=80)
                content = _extract_text(result)
                if content:
                    log_fn(f"[CTX] package meta from {rel} ({len(content)} chars)")
                    return f"# {rel}\n{content}"
            except (RuntimeError, ValueError) as exc:
                log_fn(f"[CTX] could not read {rel}: {exc}")
    return ""


def _gather_create(
    ctx: GatheredContext,
    target_files: list[str],
    workspace: str,
    run_tool: Any,
    log_fn: Any,
) -> None:
    """Context strategy for CREATE mode: find similar existing files as examples."""
    for rel in target_files:
        ext = os.path.splitext(rel)[-1].lower()
        if not ext:
            continue
        sibling_dir = os.path.dirname(rel) or "."
        try:
            # Look for similar files in the same directory.
            tree_result = run_tool("repo_tree", path=sibling_dir, depth=1)
            raw = str(tree_result.get("stdout") or tree_result.get("tree") or "")
            candidates = _extract_files_with_ext(raw, ext, exclude=rel)
            for candidate in candidates[:MAX_SIMILAR]:
                content = _read_file_safe(candidate, workspace, run_tool, log_fn)
                if content:
                    ctx.reference_files[candidate] = content
                    log_fn(f"[CTX] reference file for CREATE: {candidate}")
        except (RuntimeError, ValueError) as exc:
            log_fn(f"[CTX] reference discovery failed for {rel}: {exc}")


def _gather_modify(
    ctx: GatheredContext,
    target_files: list[str],
    workspace: str,
    run_tool: Any,
    log_fn: Any,
) -> None:
    """Context strategy for MODIFY (and MIXED) mode: read the existing targets."""
    for rel in target_files:
        full = os.path.join(workspace, rel)
        if os.path.exists(full):
            content = _read_file_safe(rel, workspace, run_tool, log_fn)
            if content is not None:
                ctx.target_contents[rel] = content
                log_fn(f"[CTX] target file read: {rel} ({len(content)} chars)")
        else:
            log_fn(f"[CTX] target file absent (will be created): {rel}")
            # Missing targets in mixed mode are noted, not read.
            ctx.extra.setdefault("files_to_create", []).append(rel)


def _read_file_safe(
    rel: str,
    workspace: str,
    run_tool: Any,
    log_fn: Any,
) -> str | None:
    """Read a file via the repo_read_head tool; return None on any failure."""
    try:
        result = run_tool("repo_read_head", file=rel, lines=HEAD_LINES)
        return _extract_text(result)
    except (RuntimeError, ValueError) as exc:
        log_fn(f"[CTX] could not read {rel}: {exc}")
        return None


def _extract_text(tool_result: dict[str, Any]) -> str:
    """Pull plain text from a tool result dict (handles content list or stdout str)."""
    if not isinstance(tool_result, dict):
        return ""
    # Structured content list (repo_read_* format)
    content = tool_result.get("content")
    if isinstance(content, list):
        lines = [item.get("t", "") if isinstance(item, dict) else str(item) for item in content]
        return "\n".join(lines)[:MAX_FILE_CHARS]
    # Raw stdout string
    stdout = tool_result.get("stdout") or tool_result.get("text") or ""
    return str(stdout)[:MAX_FILE_CHARS]


def _extract_files_with_ext(tree_text: str, ext: str, exclude: str) -> list[str]:
    """Parse a tree text block and return relative file paths matching *ext*."""
    results: list[str] = []
    for line in tree_text.splitlines():
        stripped = line.strip().lstrip("│├└─ ")
        if stripped.endswith(ext) and stripped != os.path.basename(exclude):
            results.append(stripped)
    return results
