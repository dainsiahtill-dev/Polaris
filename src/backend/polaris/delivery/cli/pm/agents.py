"""AGENTS.md workflow management for loop-pm."""

import argparse
import logging
import os
import time
from typing import Any

from polaris.delivery.cli.pm.agents_helpers import (
    _build_fallback,
    _is_failed_draft,
    check_existing_draft,
    gather_docs_context,
    get_agents_draft_path,
    get_agents_feedback_path,
    is_usable_agents_content,
)
from polaris.delivery.cli.pm.agents_modes import (
    _mark_manual_intervention,
    handle_auto_accept,
    handle_fail_fast,
    resolve_manual_intervention_mode,
)
from polaris.delivery.cli.pm.backend import (
    ensure_pm_backend_available,
    invoke_pm_backend,
    resolve_pm_backend_kind,
)
from polaris.delivery.cli.pm.config import (
    AGENTS_APPROVAL_MODE_ENV,
    AGENTS_APPROVAL_MODES,
    AGENTS_APPROVAL_TIMEOUT_ENV,
    DEFAULT_AGENTS_APPROVAL_MODE,
    DEFAULT_AGENTS_APPROVAL_TIMEOUT,
    PmRoleState,
)
from polaris.delivery.cli.pm.utils import _is_interactive_session
from polaris.kernelone.events import emit_dialogue
from polaris.kernelone.fs.control_flags import stop_requested
from polaris.kernelone.fs.text_ops import (
    ensure_parent_dir,
    read_file_safe,
    write_json_atomic,
    write_text_atomic,
)
from polaris.kernelone.storage.io_paths import resolve_artifact_path
from polaris.kernelone.prompts.loader import get_template, render_template
from polaris.kernelone.runtime.shared_types import strip_ansi

logger = logging.getLogger(__name__)


def resolve_agents_approval_mode(args: argparse.Namespace) -> str:
    """Resolve agents approval mode from args or environment."""
    raw = (
        str(
            getattr(args, "agents_approval_mode", "")
            or os.environ.get(AGENTS_APPROVAL_MODE_ENV, "")
            or DEFAULT_AGENTS_APPROVAL_MODE
        )
        .strip()
        .lower()
    )
    if raw not in AGENTS_APPROVAL_MODES:
        raw = DEFAULT_AGENTS_APPROVAL_MODE
    if raw != "auto":
        return raw
    if bool(getattr(args, "loop", False)) or not _is_interactive_session():
        return "auto_accept"
    return "wait"


def resolve_agents_approval_timeout(args: argparse.Namespace) -> int:
    """Resolve agents approval timeout from args or environment."""
    value: Any = getattr(args, "agents_approval_timeout", None)
    if value is None or str(value).strip() == "":
        value = os.environ.get(AGENTS_APPROVAL_TIMEOUT_ENV, DEFAULT_AGENTS_APPROVAL_TIMEOUT)
    try:
        timeout_sec = int(value)
    except (RuntimeError, ValueError):
        timeout_sec = DEFAULT_AGENTS_APPROVAL_TIMEOUT
    return max(timeout_sec, 0)


def _unattended_mode_enabled() -> bool:
    raw = str(os.environ.get("KERNELONE_UNATTENDED_MODE", "1")).strip().lower()
    return raw not in ("0", "false", "no", "off")


def maybe_generate_agents_draft(
    workspace_full: str,
    cache_root_full: str,
    timestamp: str,
    args: argparse.Namespace,
) -> str | None:
    """Generate AGENTS.md draft if needed."""
    workspace_agents = os.path.join(workspace_full, "AGENTS.md")
    if os.path.exists(workspace_agents):
        return None

    draft_full = get_agents_draft_path(workspace_full, cache_root_full)
    feedback_full = get_agents_feedback_path(workspace_full, cache_root_full)
    feedback_text = read_file_safe(feedback_full).strip() if feedback_full else ""

    # Check for existing draft
    existing = check_existing_draft(draft_full, feedback_full, feedback_text)
    if existing:
        return existing

    # Gather context
    docs_text, root_text, docs_context = gather_docs_context(workspace_full, cache_root_full)
    if not docs_text and not root_text and not feedback_text:
        logger.info("[pm] generating AGENTS.md draft using fallback (no README/feedback found)")
        content = _build_fallback(docs_text, root_text, feedback_text, "no README/feedback available")
        write_text_atomic(draft_full, content)
        return draft_full

    # Generate using LLM
    try:
        draft_timeout_raw = os.environ.get("KERNELONE_AGENTS_DRAFT_TIMEOUT", "120")
        try:
            draft_timeout = int(draft_timeout_raw)
        except (RuntimeError, ValueError):
            draft_timeout = 120
        draft_timeout = min(max(draft_timeout, 30), 300)

        template = get_template("agents_prompt")
        prompt = render_template(
            template,
            {
                "docs_context": docs_context,
                "feedback": feedback_text or "(none)",
            },
        )

        draft_output_path = resolve_artifact_path(
            workspace_full,
            cache_root_full,
            "runtime/AGENTS.pm.last_message.md",
        )
        role_state = PmRoleState(
            workspace_full=workspace_full,
            cache_root_full=cache_root_full,
            model=args.model,
            show_output=bool(getattr(args, "pm_show_output", False)),
            timeout=draft_timeout,
            prompt_profile=str(getattr(args, "prompt_profile", "") or ""),
            output_path=draft_output_path,
            events_path="",
            log_path=draft_full,
        )

        backend, llm_cfg = resolve_pm_backend_kind(getattr(args, "pm_backend", "auto"), role_state)
        ensure_pm_backend_available(backend)
        backend_label = backend
        if llm_cfg is not None:
            backend_label = f"{backend}:{llm_cfg.provider_id}"
        logger.info(
            "[pm] generating AGENTS.md draft using %s (timeout=%ds)...",
            backend_label,
            draft_timeout,
        )

        output = invoke_pm_backend(role_state, prompt, backend, args, usage_ctx=None)

        content = strip_ansi(output).strip()
        if not content or _is_failed_draft(content) or not is_usable_agents_content(content):
            content = _build_fallback(
                docs_text,
                root_text,
                feedback_text,
                "empty or invalid model output",
            )

        write_text_atomic(draft_full, content)
        return draft_full
    except (RuntimeError, ValueError) as e:
        logger.error("[pm] error generating AGENTS.md: %s", e)
        content = _build_fallback(docs_text, root_text, feedback_text, str(e))
        write_text_atomic(draft_full, content)
        return draft_full


def wait_for_agents_confirmation(
    workspace_full: str,
    cache_root_full: str,
    pm_state_full: str,
    pm_state: dict[str, Any],
    pm_report_full: str,
    dialogue_full: str,
    run_id: str,
    pm_iteration: int,
    timestamp: str,
    args: argparse.Namespace,
    poll_sec: float = 2.0,
) -> bool:
    """Wait for AGENTS.md confirmation from user."""
    agents_path = os.path.join(workspace_full, "AGENTS.md")
    mode = resolve_agents_approval_mode(args)
    timeout_sec = resolve_agents_approval_timeout(args)

    if os.path.isfile(agents_path):
        pm_state["awaiting_agents"] = False
        pm_state.pop("awaiting_agents_since", None)
        write_json_atomic(pm_state_full, pm_state)
        return True

    draft_path = maybe_generate_agents_draft(workspace_full, cache_root_full, timestamp, args)
    feedback_full = get_agents_feedback_path(workspace_full, cache_root_full)
    last_feedback_mtime = 0.0
    if feedback_full and os.path.isfile(feedback_full):
        try:
            last_feedback_mtime = os.path.getmtime(feedback_full)
        except (RuntimeError, ValueError):
            last_feedback_mtime = 0.0

    # Handle auto_accept mode
    if mode == "auto_accept":
        return handle_auto_accept(
            agents_path,
            draft_path,
            pm_state,
            pm_state_full,
            pm_report_full,
            dialogue_full,
            run_id,
            pm_iteration,
            timestamp,
        )

    # Handle fail_fast mode
    if mode == "fail_fast":
        return handle_fail_fast(
            pm_state,
            pm_state_full,
            workspace_full,
            pm_report_full,
            dialogue_full,
            run_id,
            pm_iteration,
            timestamp,
            draft_path,
        )

    # Handle wait mode
    pm_state["awaiting_agents"] = True
    pm_state["awaiting_agents_since"] = timestamp
    write_json_atomic(pm_state_full, pm_state)

    # Initial dialogue
    if dialogue_full:
        timeout_hint = f" (timeout: {timeout_sec}s)" if timeout_sec > 0 else ""
        emit_dialogue(
            dialogue_full,
            speaker="PM",
            type="warning",
            text="AGENTS.md missing. Please review AGENTS.generated.md and adopt it before PM continues."
            + timeout_hint,
            summary="AGENTS.md missing",
            run_id=run_id,
            pm_iteration=pm_iteration,
            refs={"files": ["AGENTS.generated.md", "AGENTS.md"], "phase": "agents"},
            meta={"error_code": "AGENTS_MISSING", "approval_mode": mode},
        )

    ensure_parent_dir(pm_report_full)
    with open(pm_report_full, "a", encoding="utf-8") as handle:
        handle.write(
            f"\n## {timestamp} (iteration {pm_iteration}) - paused\n"
            f"Status: waiting for AGENTS.md approval (mode={mode}, timeout={timeout_sec}s).\n"
            f"Draft: AGENTS.generated.md\n"
        )

    # Wait loop
    wait_started = time.monotonic()
    while not os.path.isfile(agents_path):
        if stop_requested(workspace_full):
            pm_state["awaiting_agents"] = False
            pm_state.pop("awaiting_agents_since", None)
            write_json_atomic(pm_state_full, pm_state)
            return False

        # Check for feedback updates
        if feedback_full and os.path.isfile(feedback_full):
            try:
                current_mtime = os.path.getmtime(feedback_full)
            except (RuntimeError, ValueError):
                current_mtime = last_feedback_mtime
            if current_mtime > last_feedback_mtime + 0.0001:
                last_feedback_mtime = current_mtime
                updated = maybe_generate_agents_draft(workspace_full, cache_root_full, timestamp, args)
                if updated and dialogue_full:
                    emit_dialogue(
                        dialogue_full,
                        speaker="PM",
                        type="note",
                        text="收到反馈，已重新生成 AGENTS 草稿，请再次确认。",
                        summary="AGENTS draft updated",
                        run_id=run_id,
                        pm_iteration=pm_iteration,
                        refs={"files": ["AGENTS.generated.md"]},
                    )

        # Check timeout
        if timeout_sec > 0 and (time.monotonic() - wait_started) >= float(timeout_sec):
            requested_mode = str(getattr(args, "agents_approval_mode", "") or "").strip().lower()
            if _unattended_mode_enabled() and requested_mode in ("", "auto"):
                if dialogue_full:
                    emit_dialogue(
                        dialogue_full,
                        speaker="PM",
                        type="note",
                        text=(
                            f"AGENTS wait-mode timeout ({timeout_sec}s). "
                            "Unattended mode is enabled; auto-adopting latest draft."
                        ),
                        summary="AGENTS timeout auto-adopt",
                        run_id=run_id,
                        pm_iteration=pm_iteration,
                        refs={
                            "files": ["AGENTS.generated.md", "AGENTS.md"],
                            "phase": "agents",
                        },
                        meta={"approval_mode": mode, "autonomy": "unattended"},
                    )
                return handle_auto_accept(
                    agents_path,
                    draft_path,
                    pm_state,
                    pm_state_full,
                    pm_report_full,
                    dialogue_full,
                    run_id,
                    pm_iteration,
                    timestamp,
                )
            reason = f"AGENTS.md missing after waiting {timeout_sec}s. Review AGENTS.generated.md and resume after manual confirmation."
            _mark_manual_intervention(
                pm_state,
                pm_state_full,
                workspace_full,
                run_id,
                pm_iteration,
                "AGENTS_APPROVAL_TIMEOUT",
                reason,
            )
            if dialogue_full:
                emit_dialogue(
                    dialogue_full,
                    speaker="PM",
                    type="warning",
                    text=reason,
                    summary="AGENTS approval timeout",
                    run_id=run_id,
                    pm_iteration=pm_iteration,
                    refs={
                        "files": ["AGENTS.generated.md", "AGENTS.md"],
                        "phase": "agents",
                    },
                    meta={
                        "error_code": "AGENTS_APPROVAL_TIMEOUT",
                        "pause_required": resolve_manual_intervention_mode() == "pause",
                    },
                )
            return False

        time.sleep(max(poll_sec, 0.5))

    pm_state["awaiting_agents"] = False
    pm_state.pop("awaiting_agents_since", None)
    write_json_atomic(pm_state_full, pm_state)
    return True


__all__ = [
    "maybe_generate_agents_draft",
    "resolve_agents_approval_mode",
    "resolve_agents_approval_timeout",
    "wait_for_agents_confirmation",
]
