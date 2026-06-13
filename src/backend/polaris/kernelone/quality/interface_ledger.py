"""Cross-file interface ledger (组合律 / assume-guarantee across parents).

When a multi-task plan has tasks that legitimately share files (a base task
plus an enhancement task — the common incremental-development shape), each PM
task is fissioned by the CE in isolation. With no shared contract, every parent
invents its own interface identifiers for the same artifact: live I3-r14 had one
parent name a canvas ``id="game"`` and a sibling parent name the same canvas
``id="gameCanvas"``. The enhancement step then clobbered the base step's
markers, the base step's QA verify failed, and the product shipped non-running
(``main.js`` called ``getElementById('game')`` while ``index.html`` exposed
``id="gameCanvas"``).

Local per-file ``grep`` verify is structurally blind to that drift — every step
passes its own clauses while the composition is broken. This ledger establishes
ONE interface contract per file: the first parent to declare a file's public
identifiers freezes them, and later parents reuse the exact names.

Language-agnostic by construction: the ledger only ever stores the CE's own
declared ``interface_names``/``signatures`` strings, never any HTML/JS-specific
parsing — honoring the "no business code in Polaris" rule. It is best-effort
prevention (raises coherence probability for the weak cloud CE); QA and the
claim-time punch list remain the backstop.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.storage.io_paths import resolve_artifact_path

logger = logging.getLogger(__name__)

_LEDGER_REL_PATH = "runtime/contracts/interface_ledger.json"
_SCHEMA_VERSION = "interface-ledger/1"


def _normalize_target(raw: Any) -> str:
    """Mirror normalize_construction_step's target_file shaping (./ + backslash)."""
    target = str(raw or "").strip().replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    return target


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    rows: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if token and token not in seen:
            seen.add(token)
            rows.append(token)
    return rows


def _ledger_path(workspace: str, cache_root: str) -> str:
    return resolve_artifact_path(workspace, cache_root, _LEDGER_REL_PATH)


def _load(workspace: str, cache_root: str) -> dict[str, Any]:
    path = _ledger_path(workspace, cache_root)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"schema_version": _SCHEMA_VERSION, "files": {}}
    if not isinstance(data, dict):
        return {"schema_version": _SCHEMA_VERSION, "files": {}}
    files = data.get("files")
    if not isinstance(files, dict):
        data["files"] = {}
    return data


def _merge_names(existing: list[str], incoming: list[str]) -> list[str]:
    """First-writer-wins union: keep existing order, append genuinely new names."""
    seen = set(existing)
    merged = list(existing)
    for name in incoming:
        if name not in seen:
            seen.add(name)
            merged.append(name)
    return merged


def record_declared_interfaces(
    workspace: str,
    cache_root: str,
    steps: list[dict[str, Any]],
) -> None:
    """Accumulate each step's declared interface into the per-file ledger.

    Best-effort: a ledger write failure must never abort the fission path, so
    OSError is swallowed (logged). Called after the CE step gate passes, before
    the steps are published to the market.
    """
    if not steps:
        return
    ledger = _load(workspace, cache_root)
    files: dict[str, Any] = ledger["files"]
    changed = False
    for step in steps:
        target = _normalize_target(step.get("target_file"))
        if not target:
            continue
        identifiers = _string_list(step.get("interface_names"))
        signatures = _string_list(step.get("signatures"))
        if not identifiers and not signatures:
            continue
        entry = files.get(target)
        if not isinstance(entry, dict):
            entry = {"identifiers": [], "signatures": [], "declared_by": []}
        entry["identifiers"] = _merge_names(_string_list(entry.get("identifiers")), identifiers)
        entry["signatures"] = _merge_names(_string_list(entry.get("signatures")), signatures)
        step_id = str(step.get("step_id") or "").strip()
        if step_id:
            entry["declared_by"] = _merge_names(_string_list(entry.get("declared_by")), [step_id])
        files[target] = entry
        changed = True
    if not changed:
        return
    ledger["schema_version"] = _SCHEMA_VERSION
    try:
        write_json_atomic(_ledger_path(workspace, cache_root), ledger)
    except OSError as exc:
        logger.warning("interface ledger write failed (non-fatal): %s", exc)


def read_declared_interfaces(
    workspace: str,
    cache_root: str,
    target_files: list[str],
) -> dict[str, dict[str, Any]]:
    """Return ledger entries for the given target files (only those present)."""
    wanted = {_normalize_target(tf) for tf in target_files if _normalize_target(tf)}
    if not wanted:
        return {}
    ledger = _load(workspace, cache_root)
    files: dict[str, Any] = ledger["files"]
    declared: dict[str, dict[str, Any]] = {}
    for target in wanted:
        entry = files.get(target)
        if not isinstance(entry, dict):
            continue
        identifiers = _string_list(entry.get("identifiers"))
        signatures = _string_list(entry.get("signatures"))
        if not identifiers and not signatures:
            continue
        declared[target] = {"identifiers": identifiers, "signatures": signatures}
    return declared


def render_assume_contract(declared: dict[str, dict[str, Any]]) -> str:
    """Render the frozen cross-file interface contract for the fission prompt.

    Returns "" when nothing is declared, so the caller appends nothing.
    """
    if not declared:
        return ""
    lines = [
        "\n## 跨文件接口契约(前序任务已定名,必须复用)",
        "以下文件已由前序任务声明这些公开标识符。你的步骤必须复用完全相同的名字;"
        "新增元素可用新名字,但严禁重命名或删除既有标识符——否则会破坏其它文件对它们的引用,"
        "导致产物虽通过单文件检查却整体无法运行。",
    ]
    for target in sorted(declared):
        entry = declared[target]
        identifiers = entry.get("identifiers") or []
        signatures = entry.get("signatures") or []
        if identifiers:
            lines.append(f"- {target} 已公开标识符: {', '.join(identifiers)}")
        if signatures:
            lines.append(f"  {target} 既有签名: {'; '.join(signatures)}")
    return "\n".join(lines)


__all__ = [
    "read_declared_interfaces",
    "record_declared_interfaces",
    "render_assume_contract",
]
