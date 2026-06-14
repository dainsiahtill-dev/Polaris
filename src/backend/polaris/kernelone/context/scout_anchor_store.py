"""Persistent scout localization anchors (Phase-2 A7).

Live evidence (run10a/run20): ``scout_probe`` recalled gold-region files
(django compiler.py), but its findings are one-shot tool results — the main
agent forgets them under mutation-retry pressure and drifts back to
hallucinated paths. This store persists the top-confidence anchors per
workspace so the RoleSignalPlane can re-inject them EVERY turn as
deterministic grounding the model cannot lose.

Storage: ``<workspace>/.polaris/runtime/scout_anchors.json`` — a runtime
artifact (never committed), UTF-8, confidence-ranked, deduplicated by path,
capped so the injected card stays within its token budget.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter

logger = logging.getLogger(__name__)

_ANCHOR_LOGICAL_PATH = "runtime/scout_anchors.json"
_LEGACY_ANCHOR_RELPATH = os.path.join(".polaris", "runtime", "scout_anchors.json")
_MAX_ANCHORS = 8
_MIN_CONFIDENCE = 0.2
_MAX_CARD_CHARS = 900


def _anchor_path(workspace: str) -> str:
    return os.path.join(workspace, _LEGACY_ANCHOR_RELPATH)


def _kernel_fs(workspace: str) -> KernelFileSystem:
    return KernelFileSystem(workspace, get_default_adapter())


def load_scout_anchors(workspace: str) -> list[dict[str, Any]]:
    """Load persisted anchors; missing/corrupt files load as empty (fail-soft)."""
    try:
        data = _kernel_fs(workspace).read_json(_ANCHOR_LOGICAL_PATH)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        path = _anchor_path(workspace)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return []
    anchors = data.get("anchors") if isinstance(data, dict) else None
    return [a for a in anchors or [] if isinstance(a, dict) and a.get("path")]


def record_scout_anchors(workspace: str, query: str, findings: list[dict[str, Any]]) -> int:
    """Merge probe findings into the persistent anchor set.

    Dedup by file path keeping the highest-confidence entry; low-confidence
    findings (< _MIN_CONFIDENCE) are not worth pinning. Returns the number of
    anchors now stored. Never raises (one failed persistence must not break a
    read-only probe).
    """
    fresh: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        path = str(finding.get("path") or "").strip()
        confidence = float(finding.get("confidence") or 0.0)
        if not path or confidence < _MIN_CONFIDENCE:
            continue
        fresh.append(
            {
                "path": path,
                "line": finding.get("line"),
                "symbol": finding.get("symbol"),
                "confidence": confidence,
                "query": str(query or "")[:120],
            }
        )
    if not fresh:
        return len(load_scout_anchors(workspace))

    by_path: dict[str, dict[str, Any]] = {}
    for anchor in load_scout_anchors(workspace) + fresh:
        path = str(anchor.get("path") or "")
        current = by_path.get(path)
        if current is None or float(anchor.get("confidence") or 0.0) > float(current.get("confidence") or 0.0):
            by_path[path] = anchor
    merged = sorted(by_path.values(), key=lambda a: -float(a.get("confidence") or 0.0))[:_MAX_ANCHORS]

    try:
        _kernel_fs(workspace).write_json_atomic(
            _ANCHOR_LOGICAL_PATH,
            {"anchors": merged},
            ensure_ascii=False,
            indent=1,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug("scout anchor persistence failed (fail-soft): %s", exc)
    return len(merged)


def format_anchor_card(anchors: list[dict[str, Any]]) -> str | None:
    """Render the persistent-anchor card injected via the RoleSignalPlane."""
    if not anchors:
        return None
    lines = ["【侦察锚点】(scout 已确认的真实定位,跨回合持久有效)"]
    for anchor in anchors:
        path = str(anchor.get("path") or "")
        line_no = anchor.get("line")
        symbol = anchor.get("symbol")
        confidence = float(anchor.get("confidence") or 0.0)
        location = f"{path}:{line_no}" if line_no else path
        symbol_part = f" ({symbol})" if symbol else ""
        lines.append(f"- {location}{symbol_part} 置信度 {confidence:.2f}")
    lines.append("- 编辑前优先复核这些位置;不要在压力下放弃已确认的定位。")
    card = "\n".join(lines)
    return card[:_MAX_CARD_CHARS]
