"""Execute a read plan via a ReadToolPort and collect findings (UTF-8).

Read tools return heterogeneous payloads; this module normalizes them into a
single flat ``ScoutFinding`` stream while preserving coverage/error/truncation
signals. Supported result shapes (after unwrapping a nested ``result`` key):

- ``repo_rg``:            ``{"results"|"hits": [{"file", "line", "snippet"|"text"}]}``
- ``repo_symbols_index``: ``{"symbols": [{"file", "line", "name", "kind"}]}``
- ``repo_tree``:          ``{"entries": [{"path", "type"}]}`` (path-only findings)

Path normalization
------------------
Each tool reports paths on its own scale: ``repo_rg`` hits and
``repo_symbols_index`` symbols are already workspace-relative, while
``repo_tree`` entries are relative to the directory that was queried. Findings
are always emitted workspace-relative so a multi-root probe does not report a
bare file name that could be mistaken for a workspace-root path.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.scout.internal.ports import ReadToolPort
from polaris.cells.roles.scout.public.contracts import ScoutFinding


def retrieve(
    port: ReadToolPort,
    plan: list[tuple[str, dict[str, Any]]],
) -> tuple[list[ScoutFinding], dict[str, Any]]:
    """Run each ``(tool, args)``; collect findings + coverage. Never raises per-call."""
    findings: list[ScoutFinding] = []
    tools_used: list[str] = []
    errors: list[str] = []
    truncated = False
    successful_calls = 0

    for tool, args in plan:
        if tool not in tools_used:
            tools_used.append(tool)
        try:
            raw = port.run(tool, args)
        except (RuntimeError, ValueError, OSError) as exc:
            errors.append(f"{tool}({args}): {exc}")
            continue
        result = _unwrap(raw)
        if not result.get("ok", False):
            errors.append(f"{tool}: {result.get('error') or 'not ok'}")
            continue
        successful_calls += 1
        if result.get("truncated"):
            truncated = True
        findings.extend(_parse_findings(result, tool=tool, args=args))

    coverage = {
        "tools_used": tools_used,
        "errors": errors,
        "truncated": truncated,
        "raw_findings": len(findings),
        "successful_calls": successful_calls,
    }
    return findings, coverage


def _unwrap(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten the canonical ``{"ok": True, "result": {...}}`` envelope.

    Adapters should already normalize this, but unwrap defensively so retrieval
    works against either shape and keeps the ``ok``/``error`` flags intact.
    """
    inner = raw.get("result")
    if isinstance(inner, dict):
        merged: dict[str, Any] = {"ok": raw.get("ok", True)}
        if "error" in raw:
            merged["error"] = raw["error"]
        merged.update(inner)
        return merged
    return raw


def _parse_findings(
    result: dict[str, Any],
    *,
    tool: str,
    args: dict[str, Any],
) -> list[ScoutFinding]:
    """Translate one normalized tool result into findings across known shapes."""
    out: list[ScoutFinding] = []

    # Text-search style: repo_rg ("results") or legacy ("hits"). The reported
    # file path is already workspace-relative.
    for hit in _as_list(result.get("results")) + _as_list(result.get("hits")):
        if not isinstance(hit, dict):
            continue
        path = str(hit.get("file") or hit.get("path") or "")
        if not path:
            continue
        snippet = str(hit.get("snippet") or hit.get("text") or "").strip()
        out.append(ScoutFinding(path=path, line=_as_int(hit.get("line")), snippet=snippet))

    # Symbol-index style: repo_symbols_index ("symbols"). Workspace-relative.
    for sym in _as_list(result.get("symbols")):
        if not isinstance(sym, dict):
            continue
        path = str(sym.get("file") or "")
        if not path:
            continue
        name = str(sym.get("name") or "").strip()
        kind = str(sym.get("kind") or "").strip()
        snippet = " ".join(part for part in (kind, name) if part)
        out.append(
            ScoutFinding(
                path=path,
                line=_as_int(sym.get("line")),
                snippet=snippet,
                symbol=name or None,
            )
        )

    # Tree style: repo_tree ("entries") — path-only structural findings. Entry
    # paths are relative to the queried directory, so they are rebased onto the
    # queried root to keep findings workspace-relative.
    root = _tree_root(args)
    for entry in _as_list(result.get("entries")):
        if not isinstance(entry, dict):
            continue
        if entry.get("type") not in (None, "file"):
            continue
        path = str(entry.get("path") or entry.get("name") or "")
        if not path:
            continue
        out.append(ScoutFinding(path=_rebase(path, root), line=None, snippet=""))

    return out


def _tree_root(args: dict[str, Any]) -> str:
    """The directory a ``repo_tree`` step queried."""
    raw = args.get("path") or args.get("root") or args.get("dir") or "."
    return str(raw or ".").replace("\\", "/").strip("/") or "."


def _rebase(path: str, root: str) -> str:
    """Make a tool-reported path workspace-relative."""
    normalized = str(path or "").replace("\\", "/").lstrip("./")
    if root in ("", "."):
        return normalized
    return f"{root}/{normalized}" if normalized else root


def _as_list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except (TypeError, ValueError):
        return None
