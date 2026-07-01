"""Prompt setup owner for RoleExecutionKernel turns.

RoleExecutionKernel is the public facade for role turns. This module owns the
shared profile/prompt/fingerprint setup used by both non-streaming and streaming
turn entrypoints so prompt setup does not drift between modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from polaris.cells.roles.kernel.internal.kernel.prompt_assembly import (
    append_prompt_profiles_for_request,
    build_system_prompt_for_request,
)
from polaris.cells.roles.kernel.internal.kernel.prompt_builder_provider import get_prompt_builder
from polaris.cells.roles.kernel.internal.kernel.request_appendix import build_prompt_appendix_from_request
from polaris.cells.roles.profile.public.service import PromptFingerprint, RoleProfile, RoleTurnRequest

RoleTurnSetupStage = Literal[
    "profile",
    "request_appendix",
    "fingerprint",
    "system_prompt",
]

_SETUP_ERROR_PREFIX: dict[RoleTurnSetupStage, str] = {
    "profile": "角色加载失败",
    "request_appendix": "参数处理失败",
    "fingerprint": "提示词构建失败",
    "system_prompt": "系统提示词构建失败",
}


@dataclass(frozen=True)
class RoleTurnPromptSetup:
    """Prepared prompt setup needed before TransactionKernel execution."""

    profile: RoleProfile
    prompt_appendix: str
    prompt_builder: Any
    fingerprint: PromptFingerprint
    system_prompt: str


class RoleTurnSetupError(RuntimeError):
    """Raised when role-turn prompt setup fails before TransactionKernel starts."""

    def __init__(self, stage: RoleTurnSetupStage, reason: str) -> None:
        self.stage = stage
        self.reason = reason
        super().__init__(reason)


def format_role_turn_setup_error(error: RoleTurnSetupError) -> str:
    """Return the public non-streaming error message for a setup failure."""
    prefix = _SETUP_ERROR_PREFIX[error.stage]
    return f"{prefix}: {error.reason}"


def build_role_turn_prompt_setup(
    *,
    kernel: Any,
    role: str,
    request: RoleTurnRequest,
) -> RoleTurnPromptSetup:
    """Build shared profile and prompt inputs for a role turn.

    Boundary:
        This function performs deterministic setup only. It does not build
        ContextOS requests, execute LLM calls, run tools, mutate turn state, or
        emit runtime events.

    Complexity:
        O(p) time and memory where ``p`` is the prompt appendix size.
    """
    workspace = str(getattr(kernel, "workspace", "") or "")
    try:
        profile = kernel.registry.get_profile_or_raise(role)
    except (RuntimeError, ValueError) as exc:
        raise RoleTurnSetupError("profile", str(exc)) from exc

    try:
        prompt_appendix = build_prompt_appendix_from_request(request)
    except (RuntimeError, ValueError) as exc:
        raise RoleTurnSetupError("request_appendix", str(exc)) from exc

    prompt_appendix = append_prompt_profiles_for_request(
        profile=profile,
        request=request,
        prompt_appendix=prompt_appendix,
        context_override=getattr(request, "context_override", None),
        message=str(getattr(request, "message", "") or ""),
        workspace=workspace,
    )

    try:
        prompt_builder = get_prompt_builder(kernel)
        fingerprint = prompt_builder.build_fingerprint(profile, prompt_appendix)
    except (RuntimeError, ValueError) as exc:
        raise RoleTurnSetupError("fingerprint", str(exc)) from exc

    try:
        system_prompt = build_system_prompt_for_request(
            prompt_builder=prompt_builder,
            profile=profile,
            request=request,
            prompt_appendix=prompt_appendix,
            workspace=workspace,
        )
    except (RuntimeError, ValueError) as exc:
        raise RoleTurnSetupError("system_prompt", str(exc)) from exc

    return RoleTurnPromptSetup(
        profile=profile,
        prompt_appendix=prompt_appendix,
        prompt_builder=prompt_builder,
        fingerprint=fingerprint,
        system_prompt=system_prompt,
    )


__all__ = [
    "RoleTurnPromptSetup",
    "RoleTurnSetupError",
    "build_role_turn_prompt_setup",
    "format_role_turn_setup_error",
]
