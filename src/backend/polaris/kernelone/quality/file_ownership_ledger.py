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
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.fs.jsonl.locking import file_lock
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.storage.io_paths import resolve_artifact_path

logger = logging.getLogger(__name__)

_LEDGER_REL_PATH = "runtime/contracts/file_ownership_ledger.json"
_SCHEMA_VERSION = "file-ownership-ledger/1"
_HANDOFF_REQUEST_SCHEMA_VERSION = "file-ownership-handoff-request/1"

# In-process serialization. The cross-process file lock below is keyed by a lock
# FILE created with O_CREAT|O_EXCL, which serializes other PROCESSES but does not
# serialize threads in THIS process (the first thread creates the lock file, then
# every sibling thread sees FileExistsError and spins until timeout). A thread lock
# is therefore required in addition, to serialize the concurrent CEConsumer fission
# threads (KERNELONE_TASK_MARKET_ROLE_POOLS includes chief_engineer + concurrency>1).
_PROCESS_LOCK = threading.Lock()


def task_identifier_token_aliases(value: Any) -> tuple[str, ...]:
    """Return stable aliases for PM/task identifiers used by owner routing.

    The ownership ledger produces owner handoff facts, so identifier
    normalization belongs here rather than in every consumer. Numeric task ids
    and ``TASK-N`` ids are equivalent routing tokens; non-standard identifiers
    are preserved as-is.
    """

    token = str(value or "").strip()
    if not token:
        return ()
    aliases = {token}
    task_match = re.fullmatch(r"TASK-(?P<number>\d+)", token, flags=re.IGNORECASE)
    if task_match:
        number = str(int(task_match.group("number")))
        aliases.add(number)
        aliases.add(f"TASK-{number}")
    elif token.isdigit():
        aliases.add(f"TASK-{int(token)}")
    return tuple(sorted(aliases))


def owner_task_identifier_token_aliases(owner_step_id: Any, owner_parent: Any) -> tuple[str, ...]:
    """Return aliases that identify a file-owner task row.

    Legacy ownership facts store ``owner_parent`` and ``owner_step_id`` in
    separate fields. TaskBoard rows often expose the composed identifier
    (for example ``PM-0001-1-S4``). This helper is the single place that
    bridges those equivalent forms for owner-routing projections.
    """

    step = str(owner_step_id or "").strip()
    parent = str(owner_parent or "").strip()
    values: list[str] = []
    if step:
        values.append(step)
    if parent:
        values.append(parent)
    if step and parent and not step.casefold().startswith(parent.casefold()):
        values.append(f"{parent}-{step}")
    return tuple(_task_identifier_tokens(*values))


def _task_identifier_tokens(*values: Any) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for value in values:
        for token in task_identifier_token_aliases(value):
            if token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return tokens


@dataclass(frozen=True)
class FileOwnershipHandoffRequest:
    """Read-only routing projection for an out-of-scope file repair target.

    The file ownership ledger remains the single source of truth. This object is
    only a machine-readable request for the orchestration layer to route a
    deferred repair target back to its owning task or CE planning layer.
    """

    target_file: str
    requesting_task_id: str
    reason: str
    owner_step_id: str = ""
    owner_parent: str = ""

    def to_dict(self) -> dict[str, Any]:
        owner_found = bool(self.owner_step_id or self.owner_parent)
        return {
            "schema_version": _HANDOFF_REQUEST_SCHEMA_VERSION,
            "target_file": self.target_file,
            "requesting_task_id": self.requesting_task_id,
            "reason": self.reason,
            "owner_step_id": self.owner_step_id,
            "owner_parent": self.owner_parent,
            "owner_task_identifier_tokens": list(
                owner_task_identifier_token_aliases(self.owner_step_id, self.owner_parent)
            ),
            "requesting_task_identifier_tokens": _task_identifier_tokens(self.requesting_task_id),
            "owner_found": owner_found,
            "recommended_route": "owner_task_retry" if owner_found else "scope_authority_resolution",
            "status": "owner_found" if owner_found else "owner_unknown",
        }


@contextmanager
def _ledger_write_lock(ledger_path: str) -> Iterator[None]:
    """Serialize the whole load-modify-write of the ledger keyed by its path.

    Guards against concurrent fissions (CEConsumer threads, possibly across
    processes) racing on read-modify-write: ``write_json_atomic`` makes the FILE
    write atomic but NOT the read-modify-write, so without this guard two concurrent
    writers both load the same baseline and the later write clobbers the earlier's
    first-writer-wins entries (lost write). The thread lock serializes threads in
    this process; the cross-process ``file_lock`` serializes other processes.

    The cross-process lock uses a DISTINCT ``.rmw.lock`` sidecar — NOT the
    ``{path}.lock`` that ``write_json_atomic`` acquires internally — so the inner
    atomic write can still take its own lock without self-deadlocking. Best-effort:
    a failed cross-process acquisition still runs the body (degrading to the prior
    behaviour) rather than aborting fission.
    """
    with _PROCESS_LOCK, file_lock(f"{ledger_path}.rmw.lock") as acquired:
        if not acquired:
            logger.warning("file ownership ledger rmw lock not acquired (non-fatal); proceeding")
        yield


def normalize_file_ownership_target(raw: Any) -> str:
    """Return the canonical ledger key for a task target path.

    File-ownership, interface-freezing, and scope-authority handoff routing must
    agree on this normalization. The helper is intentionally lexical: it does
    not touch the filesystem and therefore cannot authorize or deny writes by
    itself.
    """

    target = str(raw or "").strip().replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    return target


def _normalize_target(raw: Any) -> str:
    """Backward-compatible private alias for existing in-module callers."""

    return normalize_file_ownership_target(raw)


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
    parent = str(parent_task_id or "").strip()
    ledger_path = _ledger_path(workspace, cache_root)
    # Re-read UNDER the lock and add only still-absent keys at write time: the
    # entire load-modify-write must be serialized, otherwise two concurrent
    # fissions both load the same baseline and the later write clobbers the
    # earlier's first-writer-wins entries (lost write).
    with _ledger_write_lock(ledger_path):
        ledger = _load(workspace, cache_root)
        files: dict[str, Any] = ledger["files"]
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
            write_json_atomic(ledger_path, ledger)
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


def build_file_ownership_handoff_requests(
    workspace: str,
    cache_root: str,
    target_files: list[str],
    *,
    requesting_task_id: str,
    reason: str,
) -> tuple[dict[str, Any], ...]:
    """Build ordered, JSON-safe handoff requests for deferred repair targets.

    The function never mutates the ledger. It normalizes and deduplicates target
    paths, reads the existing owner facts, and emits one request per target. An
    unknown owner is still represented explicitly so downstream projections can
    distinguish "needs owner routing" from "scope evidence was never produced".
    """
    normalized_targets: list[str] = []
    seen: set[str] = set()
    for raw_target in target_files:
        target = _normalize_target(raw_target)
        if not target or target in seen:
            continue
        seen.add(target)
        normalized_targets.append(target)
    if not normalized_targets:
        return ()

    owners = read_file_owners(workspace, cache_root, normalized_targets)
    task_id = str(requesting_task_id or "").strip()
    request_reason = str(reason or "").strip()
    requests: list[dict[str, Any]] = []
    for target in normalized_targets:
        owner = owners.get(target, {})
        requests.append(
            FileOwnershipHandoffRequest(
                target_file=target,
                requesting_task_id=task_id,
                reason=request_reason,
                owner_step_id=str(owner.get("owner_step_id") or "").strip(),
                owner_parent=str(owner.get("owner_parent") or "").strip(),
            ).to_dict()
        )
    return tuple(requests)


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
        "以下文件已由前序步骤创建并拥有。你当前只是拆分 construction_steps,不得调用 read_file、"
        "repo_tree 或任何工具；必须只输出 JSON。对这些文件,请在生成的步骤 title/verify 中声明"
        "由后续 Director 先读取既有内容再 EDIT/扩展,严禁从零重写,否则会抹掉前序逻辑导致产物整体无法运行。"
        "市场会自动把本步排在属主步骤之后执行,你无需(也不要)在 depends_on 里手写跨父属主步——"
        "depends_on 只能引用本父任务内部的 step_id。",
    ]
    for target in sorted(owned_by_other):
        owner = owned_by_other[target].get("owner_step_id", "")
        lines.append(
            f"- {target}(已由属主步骤 {owner} 创建):在 construction_steps 中要求后续 Director "
            "read 后在其上 edit/扩展,严禁重写。"
        )
    return "\n".join(lines)


__all__ = [
    "FileOwnershipHandoffRequest",
    "build_file_ownership_handoff_requests",
    "normalize_file_ownership_target",
    "owner_task_identifier_token_aliases",
    "read_file_owners",
    "record_file_owners",
    "render_edit_contract",
    "task_identifier_token_aliases",
]
