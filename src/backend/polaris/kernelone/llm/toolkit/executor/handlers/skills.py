"""Skill tool handlers for AgentAccelToolExecutor.

Registers load_skill and skill_manifest tools so the agent can discover
and load skill definitions at runtime.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from polaris.kernelone.single_agent.skill_system import (
    SkillLoader,
    SkillToolInterface,
    install_default_skills,
)

logger = logging.getLogger(__name__)


# Cache per workspace to avoid reloading on every tool call
_skill_loaders: dict[str, SkillLoader] = {}
_skill_interfaces: dict[str, SkillToolInterface] = {}


def _get_skill_interface(workspace: str) -> SkillToolInterface:
    """Get or create SkillToolInterface for a workspace."""
    if workspace not in _skill_interfaces:
        # Ensure default skills are installed (idempotent)
        with contextlib.suppress(RuntimeError):
            install_default_skills(workspace, explicit=True)

        loader = SkillLoader(workspace)
        _skill_loaders[workspace] = loader
        _skill_interfaces[workspace] = SkillToolInterface(loader)
    return _skill_interfaces[workspace]


def _handle_load_skill(executor: Any, *, name: str, **kwargs: Any) -> dict[str, Any]:
    """Handle load_skill tool call."""
    workspace = getattr(executor, "workspace", ".")
    interface = _get_skill_interface(workspace)
    return interface.load_skill(name)


def _handle_skill_manifest(executor: Any, *, role: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Handle skill_manifest tool call."""
    workspace = getattr(executor, "workspace", ".")
    interface = _get_skill_interface(workspace)
    return interface.list_skills()


def register_handlers() -> dict[str, Any]:
    """Return skill handlers for ToolHandlerRegistry."""
    return {
        "load_skill": _handle_load_skill,
        "skill_manifest": _handle_skill_manifest,
    }
