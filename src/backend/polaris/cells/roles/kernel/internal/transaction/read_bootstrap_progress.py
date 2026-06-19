"""读取 bootstrap 进度界 — F24 progress-aware read-loop bound。

本模块是 ``_READ_BOOTSTRAP_PROGRESS`` 这一**唯一可变模块级缓存**的归属地（the
stall ceiling，live-incident-critical）。所有读取/写入该缓存的函数都引用此处的
单一实例，绝不复制。

职责:
- 工作区物化指纹（``_workspace_materialization_fingerprint``）
- 读循环停滞判定（``_read_bootstrap_makes_no_progress``）与重置
- 原始 read-only 违约批次是否可执行的决策（``_should_bootstrap_original_read_batch``）
- F24 指纹 / forced-write targeting 的工作区解析（``_resolve_materialization_workspace``）
"""

from __future__ import annotations

import logging
import os
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    extract_invocation_tool_name,
    is_safe_readonly_bootstrap_invocations,
)
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    extract_latest_user_message,
)

logger = logging.getLogger(__name__)


# F24 (2026-06-16): progress-aware read-loop bound — the CORRECT version of the
# reverted F21. F21's raw count-based ceiling regressed L2 because it could not
# tell normal read-then-write convergence (L2: reads 3-5x, but WRITES files
# between reads) from a pathological stall (L3-14/L4-19: reads forever, 0 new
# files). F24 only forces the write escalation after consecutive read-only
# bootstraps that materialise NO new bytes — normal flows change the fingerprint
# and never trip it; a genuine stall does. Safe default: if the workspace cannot
# be measured, never force (== original always-bootstrap behaviour, no regression).
_MAX_STALLED_READ_BOOTSTRAPS = 2
_READ_BOOTSTRAP_PROGRESS: dict[str, tuple[tuple[int, int] | None, int]] = {}
_READ_BOOTSTRAP_PROGRESS_MAX_KEYS = 512
_WRITE_ONLY_SINGLE_TARGET_REPAIR_MARKER = "[director_quality_repair:write_only_single_target]"
_FINGERPRINT_SOURCE_EXTS = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".vue",
    ".svelte",
    ".go",
    ".rs",
    ".json",
)
_FINGERPRINT_SKIP_DIRS = frozenset(
    {".git", ".polaris", "runtime", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
)


def _workspace_materialization_fingerprint(workspace: str) -> tuple[int, int] | None:
    """Return (source_file_count, total_bytes), or None when unmeasurable.

    The 'did the Director write anything new' signal. None (CWD-style ``.`` or a
    missing dir) means the caller must NOT force — measuring the wrong tree is
    worse than indulging another read.
    """
    ws = str(workspace or "").strip()
    if not ws or ws == "." or not os.path.isdir(ws):
        return None
    file_count = 0
    total_bytes = 0
    try:
        for current_root, dirnames, filenames in os.walk(ws):
            dirnames[:] = [d for d in dirnames if d not in _FINGERPRINT_SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                if not name.endswith(_FINGERPRINT_SOURCE_EXTS):
                    continue
                file_count += 1
                try:
                    total_bytes += os.path.getsize(os.path.join(current_root, name))
                except OSError:
                    continue
            if file_count > 5000:
                break
    except OSError:
        return None
    return (file_count, total_bytes)


def _read_bootstrap_makes_no_progress(turn_id: str, workspace: str) -> bool:
    """True when read-only bootstraps for a step have stalled (no new materialization).

    Returns True only after ``_MAX_STALLED_READ_BOOTSTRAPS`` consecutive read-only
    bootstraps with an UNCHANGED workspace fingerprint. Any new materialization
    resets the streak, so normal read-then-write convergence never trips it.
    Unmeasurable workspace -> always False (safe default == original behaviour).
    """
    fingerprint = _workspace_materialization_fingerprint(workspace)
    if fingerprint is None:
        return False
    if len(_READ_BOOTSTRAP_PROGRESS) > _READ_BOOTSTRAP_PROGRESS_MAX_KEYS:
        _READ_BOOTSTRAP_PROGRESS.clear()
    last_fingerprint, stalled = _READ_BOOTSTRAP_PROGRESS.get(turn_id, (None, 0))
    stalled = stalled + 1 if (last_fingerprint is not None and fingerprint == last_fingerprint) else 0
    _READ_BOOTSTRAP_PROGRESS[turn_id] = (fingerprint, stalled)
    return stalled >= _MAX_STALLED_READ_BOOTSTRAPS


def _clear_read_bootstrap_progress(turn_id: str) -> None:
    """Reset a step's read-bootstrap progress once it converges to a write."""
    _READ_BOOTSTRAP_PROGRESS.pop(turn_id, None)


def _requires_write_only_single_target_repair(context: list[dict]) -> bool:
    """Return True for Director quality repair of exactly one missing target."""
    latest_user_request = extract_latest_user_message(context).lower()
    return _WRITE_ONLY_SINGLE_TARGET_REPAIR_MARKER in latest_user_request


def _should_bootstrap_original_read_batch(
    *,
    context: list[dict],
    turn_id: str,
    config: Any,
    original_bootstrap_invocations: list[Any],
) -> bool:
    """Decide whether an original read-only violating batch may be executed."""
    if not original_bootstrap_invocations:
        return False
    if not is_safe_readonly_bootstrap_invocations(original_bootstrap_invocations):
        return False
    if _requires_write_only_single_target_repair(context):
        logger.warning(
            "mutation-contract READ-ONLY bootstrap blocked by single-target write-only repair: turn_id=%s tools=%s",
            turn_id,
            [extract_invocation_tool_name(inv) for inv in original_bootstrap_invocations],
        )
        return False
    progress_workspace = _resolve_materialization_workspace(config)
    if _read_bootstrap_makes_no_progress(turn_id, progress_workspace):
        logger.warning(
            "mutation-contract READ-ONLY bootstrap stalled (no new materialization) "
            "-> forcing write escalation: turn_id=%s",
            turn_id,
        )
        return False
    return True


def _resolve_materialization_workspace(config: Any) -> str:
    """Resolve the workspace for the F24 fingerprint / forced-write targeting.

    F32 (2026-06-16): ``config.workspace`` is sometimes the literal CWD marker
    ``"."`` (truthy), which short-circuited the ``or KERNELONE_WORKSPACE``
    fallback in the old inline resolution. ``"."`` is UNMEASURABLE
    (``_workspace_materialization_fingerprint`` returns None), so F24 silently
    never fired and read-only bootstraps ran unbounded — factory-bench L4-23:
    6 consecutive read-only bootstraps, SAME turn_id, NO interspersed writes,
    yet ``_read_bootstrap_makes_no_progress`` returned False every time and the
    write escalation never engaged. Treat ``"."``/empty as unset so the real
    workspace (``KERNELONE_WORKSPACE``) is used and the fingerprint is measurable.
    """
    cfg_ws = str(getattr(config, "workspace", "") or "").strip()
    if cfg_ws and cfg_ws != ".":
        return cfg_ws
    return str(os.environ.get("KERNELONE_WORKSPACE", "") or "").strip() or "."
