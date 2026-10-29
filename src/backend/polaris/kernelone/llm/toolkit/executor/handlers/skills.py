"""Skill tool handlers for AgentAccelToolExecutor.

Registers load_skill and skill_manifest tools so the agent can discover
and load skill definitions at runtime.

Both handlers are *read* tools.  They never install anything: the hidden
``install_default_skills(workspace, explicit=True)`` call that used to sit
behind ``contextlib.suppress(RuntimeError)`` turned a read into a hidden
writer that materialised the builtin catalogue on first use, even when the
registered adapter refused every write.  The read-only loader already
exposes the builtin catalogue from memory, so the installation is removed
without losing any skill.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from polaris.kernelone.single_agent.skill_system import (
    SkillLoader,
    SkillToolInterface,
)

logger = logging.getLogger(__name__)


# Cache per workspace to avoid reloading on every tool call
_skill_loaders: dict[str, SkillLoader] = {}
_skill_interfaces: dict[str, SkillToolInterface] = {}


def _get_skill_interface(workspace: str) -> SkillToolInterface:
    """Get or create SkillToolInterface for a workspace.

    This is a read-only path: the loader discovers the installed skills and
    the builtin catalogue, and it never writes.  Materialising the builtin
    catalogue on disk is an explicit, host-authorised operation
    (``install_default_skills``), not a side effect of listing skills.
    """
    if workspace not in _skill_interfaces:
        loader = SkillLoader(workspace)
        _skill_loaders[workspace] = loader
        _skill_interfaces[workspace] = SkillToolInterface(loader)
    return _skill_interfaces[workspace]


def _handle_load_skill(executor: Any, *, name: str, **kwargs: Any) -> dict[str, Any]:
    """Handle load_skill tool call."""
    workspace = getattr(executor, "workspace", ".")
    interface = _get_skill_interface(workspace)
    return cast("dict[str, Any]", (interface.load_skill(name)))


def _handle_skill_manifest(executor: Any, *, role: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Handle skill_manifest tool call."""
    workspace = getattr(executor, "workspace", ".")
    interface = _get_skill_interface(workspace)
    return cast("dict[str, Any]", (interface.list_skills()))


def register_handlers() -> dict[str, Any]:
    """Return skill handlers for ToolHandlerRegistry."""
    return {
        "load_skill": _handle_load_skill,
        "skill_manifest": _handle_skill_manifest,
    }
