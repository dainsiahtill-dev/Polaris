"""Command parsing and execution for the interactive CLI loop."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from polaris.delivery.cli.terminal._base import (
    _EXIT_COMMANDS,
    _HELP_COMMANDS,
    _HELP_TEXT,
    _JSON_RENDER_MODES,
    _KNOWN_MODELS,
    _PROMPT_STYLES,
    _apply_keymode,
    _get_current_model,
    _resolve_keymode,
    _safe_text,
    _save_keymode,
    _set_current_model,
)
from polaris.delivery.cli.terminal.layout import _ConsoleRenderState, _PromptRenderer

logger = logging.getLogger(__name__)


def _handle_command(
    message: str,
    *,
    host: Any,
    current_role: str,
    active_session_id: str,
    render_state: _ConsoleRenderState,
    prompt_renderer: _PromptRenderer,
    current_keymode: str,
    current_dry_run: bool,
    allowed_roles: frozenset[str],
    role_sessions: dict[str, str],
    host_kind: str,
    super_mode: bool,
    super_role: str,
) -> tuple[bool, int, str, str, str, bool]:
    """Handle a single CLI command.

    Returns:
        (handled, exit_code, new_role, new_session_id, new_keymode, new_dry_run)
    """
    if message in _EXIT_COMMANDS:
        from polaris.delivery.cli.cli_completion import save_history
        save_history()
        return True, 0, current_role, active_session_id, current_keymode, current_dry_run

    if message in _HELP_COMMANDS:
        print(_HELP_TEXT)
        return True, -1, current_role, active_session_id, current_keymode, current_dry_run

    if message == "/session":
        if super_mode:
            print(f"role={super_role} fallback_role={current_role} session={active_session_id}")
        else:
            print(f"role={current_role} session={active_session_id}")
        return True, -1, current_role, active_session_id, current_keymode, current_dry_run

    if message.startswith("/new"):
        title = _safe_text(message.removeprefix("/new")) or None
        session_payload = host.create_session(
            title=title,
            context_config={
                "role": current_role,
                "host_kind": host_kind,
                "governance_scope": f"role:{current_role}",
            },
            capability_profile=_build_role_capability_profile(
                role=current_role,
                host_kind=host_kind,
            ),
        )
        new_session_id = _safe_text(session_payload.get("id"))
        if not new_session_id:
            raise RuntimeError("failed to create role session")
        role_sessions[current_role] = new_session_id
        if super_mode:
            print(f"role={super_role} fallback_role={current_role} session={new_session_id}")
        else:
            print(f"role={current_role} session={new_session_id}")
        return True, -1, current_role, new_session_id, current_keymode, current_dry_run

    if message.startswith("/role"):
        next_role = _safe_text(message.removeprefix("/role")).lower()
        if not next_role:
            print("[error] role name required after /role", file=sys.stderr)
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        if next_role not in allowed_roles:
            print(
                f"[error] unsupported role={next_role!r}; allowed={', '.join(sorted(allowed_roles))}",
                file=sys.stderr,
            )
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        new_session_id = role_sessions.get(next_role) or _resolve_role_session(
            host,
            role=next_role,
            role_sessions=role_sessions,
            host_kind=host_kind,
        )
        if super_mode:
            print(f"role={super_role} fallback_role={next_role} session={new_session_id}")
        else:
            print(f"role={next_role} session={new_session_id}")
        return True, -1, next_role, new_session_id, current_keymode, current_dry_run

    if message.startswith("/json"):
        next_mode = _safe_text(message.removeprefix("/json")).lower()
        if not next_mode:
            print(f"json_render={render_state.json_render}")
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        if next_mode not in _JSON_RENDER_MODES:
            print(
                f"[error] unsupported json render mode={next_mode!r}; "
                f"allowed={', '.join(sorted(_JSON_RENDER_MODES))}",
                file=sys.stderr,
            )
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        render_state.json_render = next_mode
        print(f"json_render={render_state.json_render}")
        return True, -1, current_role, active_session_id, current_keymode, current_dry_run

    if message.startswith("/prompt"):
        next_style = _safe_text(message.removeprefix("/prompt")).lower()
        if not next_style:
            omp_desc = render_state.omp_config or "-"
            print(f"prompt_style={render_state.prompt_style} omp_config={omp_desc}")
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        if next_style not in _PROMPT_STYLES:
            print(
                f"[error] unsupported prompt style={next_style!r}; allowed={', '.join(sorted(_PROMPT_STYLES))}",
                file=sys.stderr,
            )
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        render_state.prompt_style = next_style
        prompt_renderer.reset()
        print(f"prompt_style={render_state.prompt_style}")
        return True, -1, current_role, active_session_id, current_keymode, current_dry_run

    if message.startswith("/keymode"):
        next_keymode = _safe_text(message.removeprefix("/keymode")).lower()
        if not next_keymode:
            print(f"keymode={current_keymode}")
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        resolved = _resolve_keymode(next_keymode)
        new_keymode = current_keymode
        if resolved != current_keymode:
            new_keymode = resolved
            _apply_keymode(new_keymode)
            _save_keymode(new_keymode)
        print(f"keymode={new_keymode}")
        return True, -1, current_role, active_session_id, new_keymode, current_dry_run

    if message.startswith("/model"):
        next_model = _safe_text(message.removeprefix("/model")).lower()
        if not next_model:
            current_model = _get_current_model()
            print(f"model={current_model or 'not configured via environment'}")
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        # Validate model name
        if next_model not in _KNOWN_MODELS:
            print(f"[error] unknown model={next_model!r}; known models:", file=sys.stderr)
            for m in _KNOWN_MODELS:
                print(f"  {m}", file=sys.stderr)
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        _set_current_model(next_model)
        print(f"[model] Switched to: {next_model}")
        print("[model] Warning: Model switch takes effect on next message")
        return True, -1, current_role, active_session_id, current_keymode, current_dry_run

    if message.startswith("/dryrun"):
        next_dryrun = _safe_text(message.removeprefix("/dryrun")).lower()
        if not next_dryrun:
            print(f"dry_run={current_dry_run}")
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        if next_dryrun == "on":
            new_dry_run = True
            print(f"dry_run={new_dry_run}")
            return True, -1, current_role, active_session_id, current_keymode, new_dry_run
        if next_dryrun == "off":
            new_dry_run = False
            print(f"dry_run={new_dry_run}")
            return True, -1, current_role, active_session_id, current_keymode, new_dry_run
        print("[error] unsupported dryrun value; use /dryrun [on|off]", file=sys.stderr)
        return True, -1, current_role, active_session_id, current_keymode, current_dry_run

    if message.startswith("/skill"):
        skill_cmd = _safe_text(message.removeprefix("/skill")).strip().lower()
        if not skill_cmd or skill_cmd == "list":
            # List available skills
            try:
                from polaris.kernelone.single_agent.skill_system import SkillLoader
                loader = SkillLoader(str(Path(".").resolve()))
                skills = loader.list_skills()
                if skills:
                    print("\nAvailable Skills:")
                    for skill in skills:
                        name = skill.get("name", "unknown")
                        desc = skill.get("description", "No description")
                        tags = skill.get("tags", [])
                        tags_str = f" [{', '.join(tags)}]" if tags else ""
                        print(f"  • {name}: {desc}{tags_str}")
                    print("\nUse '/skill load <name>' to view full content\n")
                else:
                    print("(no skills available)")
            except Exception as exc:  # noqa: BLE001
                print(f"[error] failed to list skills: {exc}", file=sys.stderr)
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        if skill_cmd.startswith("load "):
            skill_name = skill_cmd.removeprefix("load ").strip()
            if not skill_name:
                print("[error] skill name required; use /skill load <name>", file=sys.stderr)
                return True, -1, current_role, active_session_id, current_keymode, current_dry_run
            try:
                from polaris.kernelone.single_agent.skill_system import SkillLoader
                loader = SkillLoader(str(Path(".").resolve()))
                content = loader.load_skill_content(skill_name)
                if content.startswith("Error:"):
                    print(f"[error] {content}", file=sys.stderr)
                else:
                    print(f"\n{content}\n")
            except Exception as exc:  # noqa: BLE001
                print(f"[error] failed to load skill: {exc}", file=sys.stderr)
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        if skill_cmd == "reload":
            try:
                from polaris.kernelone.single_agent.skill_system import SkillLoader
                loader = SkillLoader(str(Path(".").resolve()))
                # Force reload by creating a new instance
                print(f"[skill] Reloaded {len(loader.list_skills())} skills")
            except Exception as exc:  # noqa: BLE001
                print(f"[error] failed to reload skills: {exc}", file=sys.stderr)
            return True, -1, current_role, active_session_id, current_keymode, current_dry_run
        print("[error] unknown skill command; use /skill [list|load <name>|reload]", file=sys.stderr)
        return True, -1, current_role, active_session_id, current_keymode, current_dry_run

    # Not a recognized command
    return False, -1, current_role, active_session_id, current_keymode, current_dry_run


def _resolve_role_session(
    host: Any,
    *,
    role: str,
    role_sessions: dict[str, str],
    host_kind: str,
    session_id: str | None = None,
    session_title: str | None = None,
) -> str:
    capability_profile = _build_role_capability_profile(role=role, host_kind=host_kind)
    explicit_session_id = _safe_text(session_id) or None
    context_config = {
        "role": role,
        "host_kind": host_kind,
        "governance_scope": f"role:{role}",
    }
    if explicit_session_id:
        session_payload = host.ensure_session(
            session_id=explicit_session_id,
            title=_safe_text(session_title) or None,
            context_config=context_config,
            capability_profile=capability_profile,
        )
    else:
        session_payload = host.create_session(
            title=_safe_text(session_title) or None,
            context_config=context_config,
            capability_profile=capability_profile,
        )
    resolved = _safe_text(session_payload.get("id"))
    if not resolved:
        raise RuntimeError(f"failed to resolve role session id for role={role}")
    role_sessions[role] = resolved
    return resolved


def _build_role_capability_profile(*, role: str, host_kind: str) -> dict[str, Any]:
    from polaris.cells.roles.host.public import get_capability_profile
    profile = get_capability_profile(host_kind).to_dict()
    metadata = dict(profile.get("metadata") or {})
    metadata.update(
        {
            "role": role,
            "governance_scope": f"role:{role}",
            "source": "polaris.delivery.cli.terminal_console",
        }
    )
    profile["metadata"] = metadata
    profile["role"] = role
    return profile


def _console_display_role(*, role: str, super_mode: bool, super_role: str = "super") -> str:
    return super_role if super_mode else role
