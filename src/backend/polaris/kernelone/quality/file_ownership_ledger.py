"""Cross-parent file-ownership ledger (one file = one owner).

When a multi-parent plan has parents that legitimately share an output file (a
base parent plus an enhancement parent — the common incremental shape), the CE
fissions each parent in isolation and the market claims leaf steps purely by
their declared ``depends_on``. Two leaf steps that both write the same file with
empty ``depends_on`` are then claimed and executed independently — last writer
wins — so the enhancement clobbers the base (live I3-r18: ``PM-0001-1-S4``
"game loop" and ``PM-0001-2-step-2`` "multi-level progression" both wrote
``main.js`` with no link, and the product did not run).

This ledger establishes ONE owner per file: the FIRST parent's step to declare a
file owns it permanently. A later parent's step targeting an already-owned file
is given a serializing ``depends_on`` on the owner plus an EDIT-on-prior
instruction, so it builds ON the owner's content instead of erasing it. The
market's ``_exec_claim_ready`` then serializes the two writers for free.

Language-agnostic: the ledger only stores the CE's own ``step_id`` /
``parent_task_id`` strings keyed by ``target_file`` — no per-language parsing,
honoring the "no business code in Polaris" rule. Best-effort persistence (a
write failure never aborts fission); the load-bearing guarantee is the
``depends_on`` serialization computed in-process at publish.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.storage.io_paths import resolve_artifact_path

logger = logging.getLogger(__name__)

_LEDGER_REL_PATH = "runtime/contracts/file_ownership_ledger.json"
_SCHEMA_VERSION = "file-ownership-ledger/1"


def _normalize_target(raw: Any) -> str:
    """Mirror normalize_construction_step's target_file shaping (./ + backslash)."""
    target = str(raw or "").strip().replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    return target


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
    if not isinstance(data.get("files"), dict):
        data["files"] = {}
    return data


def record_file_owners(
    workspace: str,
    cache_root: str,
    steps: list[dict[str, Any]],
    parent_task_id: str,
) -> None:
    """Claim each step's target_file for this parent — FIRST writer wins.

    A file already owned (by any earlier parent's step) is NEVER reassigned, so
    ownership is stable across the run. Best-effort: a ledger write failure must
    never abort fission, so OSError is swallowed (logged).
    """
    if not steps:
        return
    ledger = _load(workspace, cache_root)
    files: dict[str, Any] = ledger["files"]
    parent = str(parent_task_id or "").strip()
    changed = False
    for step in steps:
        target = _normalize_target(step.get("target_file"))
        step_id = str(step.get("step_id") or "").strip()
        if not target or not step_id:
            continue
        entry = files.get(target)
        if isinstance(entry, dict) and str(entry.get("owner_step_id") or "").strip():
            continue  # first-writer-wins: never reassign an owned file
        files[target] = {"owner_step_id": step_id, "owner_parent": parent}
        changed = True
    if not changed:
        return
    ledger["schema_version"] = _SCHEMA_VERSION
    try:
        write_json_atomic(_ledger_path(workspace, cache_root), ledger)
    except OSError as exc:
        logger.warning("file ownership ledger write failed (non-fatal): %s", exc)


def read_file_owners(
    workspace: str,
    cache_root: str,
    target_files: list[str],
) -> dict[str, dict[str, str]]:
    """Return ``{normalized_target: {owner_step_id, owner_parent}}`` for owned files."""
    wanted = {_normalize_target(tf) for tf in target_files if _normalize_target(tf)}
    if not wanted:
        return {}
    ledger = _load(workspace, cache_root)
    files: dict[str, Any] = ledger["files"]
    owners: dict[str, dict[str, str]] = {}
    for target in wanted:
        entry = files.get(target)
        if not isinstance(entry, dict):
            continue
        owner_step_id = str(entry.get("owner_step_id") or "").strip()
        if not owner_step_id:
            continue
        owners[target] = {
            "owner_step_id": owner_step_id,
            "owner_parent": str(entry.get("owner_parent") or "").strip(),
        }
    return owners


def render_edit_contract(owned_by_other: dict[str, dict[str, str]]) -> str:
    """Render the EDIT-on-prior contract for the fission prompt.

    ``owned_by_other`` maps a target_file to the owner recorded by a DIFFERENT
    parent. Returns "" when nothing is owned elsewhere, so the caller appends
    nothing. This explicitly OVERRIDES the depends_on-minimization rule for these
    files (the minimization default actively discourages the very same-file edit
    dependency this fix requires).
    """
    if not owned_by_other:
        return ""
    lines = [
        "\n## 跨父文件归属契约(已被前序任务创建,必须在其基础上修改而非重写)",
        "以下文件已由前序步骤创建并拥有。对这些文件,你必须先用 read_file 读取既有内容,"
        "在其基础上 EDIT/扩展(严禁 write_file 从零重写,否则会抹掉前序逻辑导致产物整体无法运行)。"
        "市场会自动把本步排在属主步骤之后执行,你无需(也不要)在 depends_on 里手写跨父属主步——"
        "depends_on 只能引用本父任务内部的 step_id。",
    ]
    for target in sorted(owned_by_other):
        owner = owned_by_other[target].get("owner_step_id", "")
        lines.append(f"- {target}(已由属主步骤 {owner} 创建):read 后在其上修改,严禁重写。")
    return "\n".join(lines)


__all__ = [
    "read_file_owners",
    "record_file_owners",
    "render_edit_contract",
]
