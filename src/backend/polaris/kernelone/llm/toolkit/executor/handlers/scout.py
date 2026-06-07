"""Scout reconnaissance tool handler (scout_probe).

This KernelOne handler must stay below Cells in the dependency graph. It exposes
the read-only ``scout_probe`` tool with a small local scanner and never imports
role Cells or business-role services.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor

_VALID_MODES = {"locate", "boundary"}
_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
_MAX_FILES_SCANNED = 500
_MAX_FILE_BYTES = 256_000


def register_handlers() -> dict[str, Any]:
    """Return a dict of scout_* handler names to handler functions."""
    return {
        "scout_probe": _handle_scout_probe,
    }


def _terms(query: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in re.findall(r"[A-Za-z0-9_]+", query) if len(token) > 1)


def _relative_path(workspace: Path, path: Path) -> str:
    return path.relative_to(workspace).as_posix()


def _iter_candidate_files(workspace: Path) -> tuple[list[Path], int]:
    files: list[Path] = []
    skipped = 0
    for path in workspace.rglob("*"):
        if len(files) >= _MAX_FILES_SCANNED:
            break
        if any(part in _SKIP_DIRS for part in path.parts):
            skipped += 1
            continue
        try:
            if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            skipped += 1
            continue
        files.append(path)
    return files, skipped


def _line_matches(line: str, query: str, terms: tuple[str, ...]) -> bool:
    lowered = line.lower()
    if query.lower() in lowered:
        return True
    return bool(terms) and all(term in lowered for term in terms)


def _content_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _run_kernelone_probe(workspace: str, query: str, mode: str, max_findings: int) -> dict[str, Any]:
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {tuple(sorted(_VALID_MODES))}, got {mode!r}")

    workspace_root = Path(workspace).resolve()
    findings: list[dict[str, Any]] = []
    terms = _terms(query)
    files, skipped = _iter_candidate_files(workspace_root)

    for path in files:
        if len(findings) >= max_findings:
            break
        rel = _relative_path(workspace_root, path)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            skipped += 1
            continue

        matched = False
        for line_number, line in enumerate(lines, start=1):
            if _line_matches(line, query, terms):
                findings.append(
                    {
                        "path": rel,
                        "snippet": line.strip(),
                        "symbol": None,
                        "line": line_number,
                        "why_relevant": "query matched file content",
                        "confidence": 0.9,
                    }
                )
                matched = True
                break
        if matched or len(findings) >= max_findings:
            continue

        rel_lowered = rel.lower()
        if query.lower() in rel_lowered or any(term in rel_lowered for term in terms):
            findings.append(
                {
                    "path": rel,
                    "snippet": "",
                    "symbol": None,
                    "line": None,
                    "why_relevant": "query matched file path",
                    "confidence": 0.55,
                }
            )

    summary = (
        f"Found {len(findings)} scout finding(s) for {query!r}."
        if findings
        else f"No scout findings found for {query!r}."
    )
    coverage = {
        "tools_used": ("kernelone.scout_probe",),
        "files_scanned": len(files),
        "files_skipped": skipped,
        "mode": mode,
        "truncated": len(files) >= _MAX_FILES_SCANNED,
    }
    payload = {
        "summary": summary,
        "findings": findings,
        "coverage": coverage,
        "confidence": max((float(finding["confidence"]) for finding in findings), default=0.0),
    }
    payload["content_hash"] = _content_hash(payload)
    return payload


def _handle_scout_probe(self: AgentAccelToolExecutor, **kwargs: Any) -> dict[str, Any]:
    """Handle scout_probe tool call.

    Read-only code/symbol reconnaissance. Returns a distilled summary plus ranked
    findings. On bad input returns ``{"ok": False, "error": ...}``.
    """
    query = kwargs.get("query") or kwargs.get("q")
    query_str = str(query or "").strip()
    if not query_str:
        return {"ok": False, "error": "Missing required parameter: query"}

    mode = str(kwargs.get("mode") or "locate").strip() or "locate"
    max_findings = max(1, min(int(kwargs.get("max_findings") or 12), 50))

    workspace = str(self.workspace)

    try:
        report = _run_kernelone_probe(workspace, query_str, mode, max_findings)
    except ValueError as exc:
        # e.g. invalid mode or empty query rejected by the contract.
        return {"ok": False, "error": f"scout_probe rejected input: {exc}"}
    except Exception as exc:  # noqa: BLE001 - fail-soft boundary for one tool dispatch
        logger.warning("scout_probe failed: %s", exc)
        return {"ok": False, "error": f"scout_probe failed: {type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "stdout": report["summary"],
        "findings": report["findings"],
        "content_hash": report["content_hash"],
        "coverage": report["coverage"],
        "confidence": report["confidence"],
    }
