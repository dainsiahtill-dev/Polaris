"""Low-level filesystem IO: transactional write primitives + path resolution.

Leaf module for ``filesystem.py``: the temp -> verify -> atomic-rename write
primitives, the staged (verify-only) half of a multi-file commit, and the
path-resolution / did-you-mean helpers that turn a tool-supplied path into a
workspace-relative path with teaching errors. Depends on no other
``filesystem_*`` sibling, so it sits at the foundation of the import graph.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.executor.utils import (
    resolve_workspace_path,
    to_workspace_relative_path,
)
from polaris.kernelone.tool_execution.code_validator import (
    PostWriteVerification,
    verify_written_code,
)

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor


def _verify_written_code(filepath: str, expected: str) -> PostWriteVerification:
    """Resolve ``verify_written_code`` through the canonical ``filesystem`` module.

    The transactional write primitives historically lived in ``filesystem.py`` and
    consulted the module-global ``verify_written_code``; tests rely on patching it
    there (``monkeypatch.setattr(filesystem, "verify_written_code", ...)``). Keeping
    the resolution pointed at the canonical module preserves that contract verbatim
    after the primitives moved here, while the import-time edge stays one-directional
    (``filesystem`` -> ``filesystem_io``): this lazy lookup runs only at write time,
    long after both modules are loaded, so it introduces no import cycle.
    """
    from polaris.kernelone.llm.toolkit.executor.handlers import filesystem

    verifier = getattr(filesystem, "verify_written_code", verify_written_code)
    result: PostWriteVerification = verifier(filepath, expected)
    return result


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
_JS_TO_TS_SOURCE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    ".js": (".ts", ".tsx", ".mts", ".cts"),
    ".mjs": (".mts", ".ts"),
    ".cjs": (".cts", ".ts"),
    ".jsx": (".tsx", ".ts"),
}


def _source_basename_candidates(basename: str) -> set[str]:
    """Return accepted basename variants for source-map-like JS import paths.

    TypeScript ESM source often imports ``./Foo.js`` while the editable source
    file is ``Foo.ts``.  When an LLM asks to read the import path inside the
    source workspace, the not-found response should teach the real source path
    instead of letting it create out-of-scope ``.js`` files.
    """
    normalized = str(basename or "").strip().lower()
    if not normalized:
        return set()
    stem, ext = os.path.splitext(normalized)
    candidates = {normalized}
    for companion_ext in _JS_TO_TS_SOURCE_EXTENSIONS.get(ext, ()):
        candidates.add(f"{stem}{companion_ext}")
    return candidates


def _path_parts_equivalent_for_suggestion(requested_part: str, candidate_part: str) -> bool:
    if requested_part == candidate_part:
        return True
    return candidate_part in _source_basename_candidates(requested_part)


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
    accepted_basenames = _source_basename_candidates(basename)
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
                if filename.lower() not in accepted_basenames:
                    continue
                absolute = os.path.join(current_root, filename)
                rel_path = os.path.relpath(absolute, workspace_root).replace("\\", "/")
                candidate_parts = [part.lower() for part in rel_path.split("/") if part]
                # Count consecutive matching components from the end (basename inclusive).
                overlap = 0
                for req_part, cand_part in zip(reversed(requested_parts), reversed(candidate_parts), strict=False):
                    if not _path_parts_equivalent_for_suggestion(req_part, cand_part):
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

        verify_result = _verify_written_code(tmp_path, content)
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


def _stage_temp_verify(
    target_path: str,
    content: str,
    *,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Stage half of a transactional write: temp -> verify (NO rename yet).

    Used by multi-file commits that must verify EVERY target before any file is
    moved into place, so a later failure never leaves earlier files half-applied.
    On success returns ``{"ok": True, "tmp_path": str, "bytes_written": int}``; the
    caller is responsible for committing (``os.replace``) or removing the temp.
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

        verify_result = _verify_written_code(tmp_path, content)
        if not verify_result.success:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            return {
                "ok": False,
                "error": f"Post-write verification failed: {verify_result.error}",
            }

        return {
            "ok": True,
            "tmp_path": tmp_path,
            "bytes_written": len(content.encode(encoding, errors="replace")),
        }
    except OSError as exc:
        if tmp_path:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
        return {"ok": False, "error": f"Failed to write file: {exc}"}
