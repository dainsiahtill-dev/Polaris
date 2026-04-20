"""AGENTS.md approval mode handlers for loop-pm."""

import logging
import os
from datetime import datetime
from typing import Any

from polaris.delivery.cli.pm.agents_helpers import _build_fallback, is_usable_agents_content
from polaris.delivery.cli.pm.config import (
    DEFAULT_MANUAL_INTERVENTION_MODE,
    MANUAL_INTERVENTION_MODE_ENV,
    MANUAL_INTERVENTION_MODES,
    MANUAL_INTERVENTION_STATUS,
)
from polaris.infrastructure.compat.io_utils import (
    emit_dialogue,
    ensure_parent_dir,
    pause_flag_path,
    read_file_safe,
    write_json_atomic,
    write_text_atomic,
)

logger = logging.getLogger(__name__)


def resolve_manual_intervention_mode() -> str:
    raw = (
        str(
            os.environ.get(MANUAL_INTERVENTION_MODE_ENV, DEFAULT_MANUAL_INTERVENTION_MODE)
            or DEFAULT_MANUAL_INTERVENTION_MODE
        )
        .strip()
        .lower()
    )
    if raw not in MANUAL_INTERVENTION_MODES:
        return DEFAULT_MANUAL_INTERVENTION_MODE
    return raw


def handle_auto_accept(
    agents_path: str,
    draft_path: str | None,
    pm_state: dict[str, Any],
    pm_state_full: str,
    pm_report_full: str,
    dialogue_full: str,
    run_id: str,
    pm_iteration: int,
    timestamp: str,
) -> bool:
    """Handle auto_accept approval mode."""
    try:
        draft_content = ""
        if draft_path and os.path.isfile(draft_path):
            draft_content = read_file_safe(draft_path)
        if not is_usable_agents_content(draft_content):
            draft_content = _build_fallback(
                docs_text="",
                root_text="",
                feedback_text="",
                error_hint="auto_accept unusable draft",
            )
        write_text_atomic(agents_path, draft_content)
        pm_state["awaiting_agents"] = False
        pm_state.pop("awaiting_agents_since", None)
        pm_state["last_updated_ts"] = timestamp
        write_json_atomic(pm_state_full, pm_state)

        if dialogue_full:
            emit_dialogue(
                dialogue_full,
                speaker="PM",
                type="note",
                text="AGENTS.md missing; draft auto-adopted to avoid idle blocking.",
                summary="AGENTS auto adopted",
                run_id=run_id,
                pm_iteration=pm_iteration,
                refs={"files": ["AGENTS.generated.md", "AGENTS.md"], "phase": "agents"},
                meta={"approval_mode": "auto_accept"},
            )

        ensure_parent_dir(pm_report_full)
        with open(pm_report_full, "a", encoding="utf-8") as handle:
            handle.write(
                f"\n## {timestamp} (iteration {pm_iteration}) - agents\n"
                "Status: AGENTS.md auto-adopted.\n"
                f"Draft: {draft_path or 'AGENTS.generated.md'}\n"
            )
        return True
    except (RuntimeError, ValueError) as exc:
        reason = f"Failed to auto-adopt AGENTS.md draft: {exc}"
        _mark_manual_intervention(
            pm_state,
            pm_state_full,
            os.path.dirname(agents_path),
            run_id,
            pm_iteration,
            "AGENTS_AUTO_APPLY_FAILED",
            reason,
        )
        if dialogue_full:
            emit_dialogue(
                dialogue_full,
                speaker="PM",
                type="warning",
                text=reason,
                summary="AGENTS auto-adopt failed",
                run_id=run_id,
                pm_iteration=pm_iteration,
                refs={"files": ["AGENTS.generated.md", "AGENTS.md"], "phase": "agents"},
                meta={"error_code": "AGENTS_AUTO_APPLY_FAILED"},
            )
        return False


def handle_fail_fast(
    pm_state: dict[str, Any],
    pm_state_full: str,
    workspace_full: str,
    pm_report_full: str,
    dialogue_full: str,
    run_id: str,
    pm_iteration: int,
    timestamp: str,
    draft_path: str | None,
) -> bool:
    """Handle fail_fast approval mode."""
    pause_required = resolve_manual_intervention_mode() == "pause"
    reason = "AGENTS.md missing. Review AGENTS.generated.md and adopt it before PM continues."
    _mark_manual_intervention(
        pm_state,
        pm_state_full,
        workspace_full,
        run_id,
        pm_iteration,
        "AGENTS_MISSING",
        reason,
    )

    if dialogue_full:
        emit_dialogue(
            dialogue_full,
            speaker="PM",
            type="warning",
            text=reason,
            summary="AGENTS.md missing",
            run_id=run_id,
            pm_iteration=pm_iteration,
            refs={"files": ["AGENTS.generated.md", "AGENTS.md"], "phase": "agents"},
            meta={
                "error_code": "AGENTS_MISSING",
                "approval_mode": "fail_fast",
                "pause_required": pause_required,
            },
        )

    ensure_parent_dir(pm_report_full)
    with open(pm_report_full, "a", encoding="utf-8") as handle:
        handle.write(
            f"\n## {timestamp} (iteration {pm_iteration}) - paused\n"
            "Status: manual intervention required before PM can continue.\n"
            f"Reason: AGENTS_MISSING - {reason}\n"
            f"Draft: {draft_path or 'AGENTS.generated.md'}\n"
        )
    return False


def _mark_manual_intervention(
    pm_state: dict[str, Any],
    pm_state_full: str,
    workspace_full: str,
    run_id: str,
    pm_iteration: int,
    reason_code: str,
    reason_detail: str,
) -> None:
    """Mark state as awaiting manual intervention."""
    intervention_mode = resolve_manual_intervention_mode()
    pm_state["awaiting_agents"] = False
    pm_state.pop("awaiting_agents_since", None)
    pm_state["awaiting_manual_intervention"] = True
    pm_state["awaiting_manual_intervention_since"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pm_state["manual_intervention_reason_code"] = str(reason_code or "").strip()
    pm_state["manual_intervention_detail"] = str(reason_detail or "").strip()
    pm_state["manual_intervention_mode"] = intervention_mode
    pm_state["last_director_status"] = "paused"
    pm_state["last_director_error_code"] = MANUAL_INTERVENTION_STATUS
    pm_state["last_director_error_detail"] = str(reason_detail or "").strip()
    write_json_atomic(pm_state_full, pm_state)

    if intervention_mode == "pause":
        pause_full = pause_flag_path(workspace_full)
        try:
            write_text_atomic(
                pause_full,
                (
                    f"run_id={run_id}\n"
                    f"iteration={pm_iteration}\n"
                    f"reason_code={reason_code}\n"
                    f"reason_detail={reason_detail}\n"
                ),
            )
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to write pause flag: {e}")


__all__ = [
    "_mark_manual_intervention",
    "handle_auto_accept",
    "handle_fail_fast",
    "resolve_manual_intervention_mode",
]
