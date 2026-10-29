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

Every read and write goes through the KernelOne logical namespace.  A
refused or unreadable anchor file is *not* retried through a raw open: a
fallback path would reintroduce the exact out-of-namespace read the logical
boundary exists to prevent.  Missing and corrupt payloads remain fail-soft
because that is the documented behaviour of this store; a *refused* read is
reported as an empty set with an audit log, never as a silent fallback.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.guarded_namespace_ops import NamespaceOperationEffectUnknownError
from polaris.kernelone.fs.registry import get_default_adapter

logger = logging.getLogger(__name__)

_ANCHOR_LOGICAL_PATH = "runtime/scout_anchors.json"
_MAX_ANCHORS = 8
_MIN_CONFIDENCE = 0.2
_MAX_CARD_CHARS = 900


def _kernel_fs(workspace: str) -> KernelFileSystem:
    return KernelFileSystem(workspace, get_default_adapter())


def load_scout_anchors(workspace: str) -> list[dict[str, Any]]:
    """Load persisted anchors; missing/corrupt/refused files load as empty.

    The store is fail-soft by contract: a missing file, a corrupt payload and
    a refused read all yield an empty anchor set.  A refusal is logged so the
    audit trail shows that the boundary refused the read rather than the
    file being absent; it is never worked around with a raw open.
    """
    try:
        return _read_scout_anchors(workspace)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug("scout anchor read failed (fail-soft): %s", exc)
        return []


def _read_scout_anchors(workspace: str) -> list[dict[str, Any]]:
    """Keep absent data distinct from a refused read for merge writers."""
    try:
        data = _kernel_fs(workspace).read_json(_ANCHOR_LOGICAL_PATH)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    anchors = data.get("anchors") if isinstance(data, dict) else None
    return [a for a in anchors or [] if isinstance(a, dict) and a.get("path")]


def record_scout_anchors(workspace: str, query: str, findings: list[dict[str, Any]]) -> int | None:
    """Merge findings; return the confirmed count, or None for unknown state.

    Absent or corrupt cache data starts an empty store. A refused initial read
    prevents the merge from overwriting anchors it could not observe. A known
    write refusal preserves the previously observed count; an uncertain effect
    returns None and is never replayed. Storage failures remain fail-soft for
    the read-only probe, whose caller does not consume this count.
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
    try:
        existing = _read_scout_anchors(workspace)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("scout anchor merge skipped: initial state is unknown: %s", exc)
        return None
    if not fresh:
        return len(existing)

    by_path: dict[str, dict[str, Any]] = {}
    for anchor in existing + fresh:
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
    except NamespaceOperationEffectUnknownError as exc:
        # The write may already have landed.  The state is UNKNOWN, so the
        # stored count is not asserted: the caller must not read a confirmed
        # number from this path.
        logger.warning("scout anchor persistence effect is unknown (fail-soft): %s", exc)
        return None
    except (OSError, RuntimeError, ValueError) as exc:
        # A known no-write: the write was refused or failed before it could
        # commit, so the previously stored anchors are still the ones in
        # effect.
        logger.debug("scout anchor persistence failed (fail-soft): %s", exc)
        return len(existing)
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
