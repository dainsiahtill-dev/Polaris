"""Context adapter for `roles.runtime` cell.

This module contains context augmentation and Context OS snapshot loading
logic extracted from RoleRuntimeService.

Wave 2 extraction - E4: Service Layer Lead.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from polaris.kernelone.context.context_os.rehydration import rehydrate_persisted_context_os_payload

logger = logging.getLogger(__name__)


# ── Context Override Merging ───────────────────────────────────────────────────


def merge_context_override(
    base: Mapping[str, Any] | None,
    overlay: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge two context override dicts with special handling for nested keys.

    Special merge rules:
    - `state_first_context_os`: nested merge (existing values preserved unless overridden)
    - `cognitive_runtime_handoff`: overlay values take precedence (reverse merge)

    Args:
        base: Base context override dict.
        overlay: Overlay context override dict (higher priority).

    Returns:
        Merged context override dict.
    """
    merged = dict(base or {})
    for key, value in dict(overlay or {}).items():
        if key == "state_first_context_os":
            existing = merged.get(key)
            if isinstance(existing, Mapping) and isinstance(value, Mapping):
                state_first = dict(value)
                for nested_key, nested_value in dict(existing).items():
                    if isinstance(state_first.get(nested_key), Mapping) and isinstance(
                        nested_value,
                        Mapping,
                    ):
                        nested_merged = dict(state_first.get(nested_key) or {})
                        nested_merged.update(dict(nested_value))
                        state_first[nested_key] = nested_merged
                    else:
                        state_first[nested_key] = nested_value
                merged[key] = state_first
                continue
        if key == "cognitive_runtime_handoff":
            existing = merged.get(key)
            if isinstance(existing, Mapping) and isinstance(value, Mapping):
                handoff_meta = dict(value)
                handoff_meta.update(dict(existing))
                merged[key] = handoff_meta
                continue
        merged[key] = value
    return merged


# ── Context Augmentation ───────────────────────────────────────────────────────


def to_string_list(value: Any) -> list[str]:
    """Convert value to a list of stripped strings.

    Args:
        value: Input value (list/tuple/set, or single value).

    Returns:
        List of stripped non-empty strings.
    """
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    token = str(value or "").strip()
    return [token] if token else []


def augment_context_with_repo_intelligence(
    *,
    workspace: str,
    domain: str,
    context: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Inject repo intelligence summary for code/research domains when hinted.

    Args:
        workspace: Workspace directory path.
        domain: Target execution domain.
        context: Current context override dict.
        metadata: Execution metadata dict.

    Returns:
        Augmented context override dict with `repo_intelligence` key if applicable.
    """
    domain_token = str(domain or "").strip().lower()
    context_override = dict(context or {})
    if domain_token not in {"code", "research"}:
        return context_override

    use_repo_intelligence = bool(context_override.get("use_repo_intelligence") or metadata.get("use_repo_intelligence"))
    chat_files = to_string_list(context_override.get("chat_files") or metadata.get("chat_files"))
    mentioned_idents = to_string_list(context_override.get("mentioned_idents") or metadata.get("mentioned_idents"))
    mentioned_fnames = to_string_list(context_override.get("mentioned_fnames") or metadata.get("mentioned_fnames"))
    if not use_repo_intelligence and not (chat_files or mentioned_idents or mentioned_fnames):
        return context_override

    languages = to_string_list(context_override.get("languages") or metadata.get("languages"))
    try:
        max_files = int(context_override.get("repo_intel_max_files") or metadata.get("repo_intel_max_files") or 20)
    except (RuntimeError, ValueError):
        max_files = 20
    try:
        max_symbols = int(
            context_override.get("repo_intel_max_symbols") or metadata.get("repo_intel_max_symbols") or 40
        )
    except (RuntimeError, ValueError):
        max_symbols = 40

    try:
        from polaris.kernelone.context.repo_intelligence import get_repo_intelligence

        facade = get_repo_intelligence(
            workspace=workspace,
            languages=languages or None,
        )
        repo_map_result = facade.get_repo_map(
            chat_files=chat_files,
            mentioned_idents=mentioned_idents,
            mentioned_fnames=mentioned_fnames,
            max_files=max_files,
            max_symbols=max_symbols,
            include_loi=(domain_token == "code"),
        )
        summary = str(repo_map_result.to_text() or "").strip()
        if not summary:
            return context_override

        max_chars = 1800
        truncated = len(summary) > max_chars
        context_override["repo_intelligence"] = {
            "domain": domain_token,
            "summary": summary[:max_chars],
            "truncated": truncated,
            "ranked_files": len(repo_map_result.ranked_files),
            "ranked_symbols": len(repo_map_result.ranked_symbols),
        }
        return context_override
    except (RuntimeError, ValueError) as exc:
        logger.debug("repo intelligence injection skipped: %s", exc)
        return context_override


def augment_context_with_handoff_rehydration(
    *,
    workspace: str,
    role: str,
    session_id: str | None,
    context: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Inject handoff rehydration context for Cognitive Runtime continuity.

    Args:
        workspace: Workspace directory path.
        role: Role name.
        session_id: Session ID (optional).
        context: Current context override dict.
        metadata: Execution metadata dict.

    Returns:
        Tuple of (augmented_context, augmented_metadata).
    """
    context_override = dict(context or {})
    metadata_out = dict(metadata or {})
    handoff_id = str(context_override.get("handoff_id") or metadata_out.get("handoff_id") or "").strip()
    if not handoff_id:
        handoff_payload = context_override.get("cognitive_runtime_handoff")
        if isinstance(handoff_payload, Mapping):
            handoff_id = str(handoff_payload.get("handoff_id") or "").strip()
    if not handoff_id:
        return context_override, metadata_out
    try:
        from polaris.cells.factory.cognitive_runtime.public.contracts import (
            RehydrateHandoffPackCommandV1,
        )
        from polaris.cells.factory.cognitive_runtime.public.service import (
            get_cognitive_runtime_public_service,
        )

        service = get_cognitive_runtime_public_service()
        try:
            result = service.rehydrate_handoff_pack(
                RehydrateHandoffPackCommandV1(
                    workspace=workspace,
                    handoff_id=handoff_id,
                    target_role=role,
                    target_session_id=session_id,
                )
            )
        finally:
            service.close()
        if not result.ok or result.rehydration is None:
            logger.debug(
                "handoff rehydration skipped: workspace=%s role=%s handoff_id=%s error=%s",
                workspace,
                role,
                handoff_id,
                result.error_code,
            )
            return context_override, metadata_out
        context_override = merge_context_override(
            result.rehydration.context_override,
            context_override,
        )
        metadata_patch = dict(result.rehydration.metadata_patch or {})
        metadata_patch.setdefault("handoff_id", handoff_id)
        metadata_patch.setdefault(
            "handoff_rehydration_id",
            str(result.rehydration.rehydration_id or "").strip(),
        )
        merged_metadata = dict(metadata_patch)
        merged_metadata.update(metadata_out)
        return context_override, merged_metadata
    except (RuntimeError, ValueError) as exc:
        logger.debug("handoff rehydration injection skipped: %s", exc)
        return context_override, metadata_out


# ── Context OS Snapshot Loading ────────────────────────────────────────────────


def load_session_context_os_snapshot(
    *,
    session_id: str,
    workspace: str,
    role: str,
    context_override: dict[str, Any],
) -> dict[str, Any]:
    """Load Context OS snapshot and prior turn_events from session service.

    For EXISTING sessions: load stored state_first_context_os and session_turn_events.
    For NEW SESSIONS: RoleTurnRequest.__init__ handles empty snapshot bootstrap.

    Args:
        session_id: Session identifier.
        workspace: Workspace directory path.
        role: Role name.
        context_override: Current context override dict to augment.

    Returns:
        Augmented context_override with context_os_snapshot and session_turn_events.
    """
    session_id_token = str(session_id or "").strip()
    if not session_id_token:
        return context_override

    context_override = dict(context_override)
    try:
        from polaris.cells.roles.session.public import RoleSessionService

        with RoleSessionService() as svc:
            session = svc.get_session(session_id_token)
            if session is not None:
                session_ctx = svc.get_context_config_dict(session_id_token) or {}
                prior_turn_events = session_ctx.get("session_turn_events")
                state_first_ctx = session_ctx.get("state_first_context_os")
                if isinstance(state_first_ctx, dict) and state_first_ctx:
                    context_override["context_os_snapshot"] = (
                        rehydrate_persisted_context_os_payload(
                            state_first_ctx,
                            session_turn_events=[
                                dict(item) for item in (prior_turn_events or []) if isinstance(item, dict)
                            ],
                        )
                        or state_first_ctx
                    )
                # else: RoleTurnRequest.__init__ will bootstrap empty snapshot
                # SSOT Fix: Load prior turn_events for ContextOS continuity.
                if isinstance(prior_turn_events, list) and prior_turn_events:
                    context_override["session_turn_events"] = prior_turn_events
    except (RuntimeError, ValueError):
        # Non-critical: if session context is unavailable, continue without it
        # RoleTurnRequest.__init__ will bootstrap empty snapshot
        logger.debug(
            "session context os snapshot load skipped: session=%s workspace=%s role=%s",
            session_id_token,
            workspace,
            role,
        )
    return context_override


__all__ = [
    "augment_context_with_handoff_rehydration",
    "augment_context_with_repo_intelligence",
    "load_session_context_os_snapshot",
    "merge_context_override",
    "to_string_list",
]
