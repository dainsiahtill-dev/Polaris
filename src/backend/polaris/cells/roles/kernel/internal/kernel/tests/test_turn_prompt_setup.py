"""Tests for the shared role-turn prompt setup owner."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.turn_prompt_setup import (
    RoleTurnSetupError,
    RoleTurnSetupStage,
    build_role_turn_prompt_setup,
    format_role_turn_setup_error,
)
from polaris.cells.roles.profile.public.service import PromptFingerprint, RoleProfile, RoleTurnRequest


class _Registry:
    def __init__(self, profile: RoleProfile | None = None, error: Exception | None = None) -> None:
        self._profile = profile
        self._error = error
        self.requested_role = ""

    def get_profile_or_raise(self, role: str) -> RoleProfile:
        self.requested_role = role
        if self._error is not None:
            raise self._error
        if self._profile is None:
            raise ValueError("missing profile")
        return self._profile


class _PromptBuilder:
    def __init__(self, *, fingerprint_error: Exception | None = None) -> None:
        self._fingerprint_error = fingerprint_error
        self.fingerprint_appendix = ""
        self.system_prompt_appendix = ""

    def build_fingerprint(self, profile: RoleProfile, prompt_appendix: str) -> PromptFingerprint:
        if self._fingerprint_error is not None:
            raise self._fingerprint_error
        self.fingerprint_appendix = prompt_appendix
        return PromptFingerprint(
            core_hash=f"core:{profile.role_id}",
            appendix_hash=prompt_appendix,
            profile_fingerprint=profile.profile_fingerprint,
        )

    def build_system_prompt(
        self,
        profile: RoleProfile,
        prompt_appendix: str,
        **_: Any,
    ) -> str:
        self.system_prompt_appendix = prompt_appendix
        return f"system:{profile.role_id}:{prompt_appendix}"


class _Kernel:
    def __init__(self, *, registry: _Registry, prompt_builder: _PromptBuilder, workspace: str = ".") -> None:
        self.registry = registry
        self.workspace = workspace
        self._injected_prompt_builder = prompt_builder


def _profile() -> RoleProfile:
    return RoleProfile(
        role_id="director",
        display_name="Director",
        description="Executes governed implementation turns.",
        model="gpt-test",
    )


def test_build_role_turn_prompt_setup_projects_shared_prompt_inputs() -> None:
    profile = _profile()
    registry = _Registry(profile)
    prompt_builder = _PromptBuilder()
    kernel = _Kernel(registry=registry, prompt_builder=prompt_builder)
    request = RoleTurnRequest(
        message="plain chat",
        prompt_appendix="appendix",
        context_override={},
    )

    setup = build_role_turn_prompt_setup(
        kernel=kernel,
        role="director",
        request=request,
    )

    assert registry.requested_role == "director"
    assert setup.profile is profile
    assert setup.prompt_appendix == "appendix"
    assert setup.prompt_builder is prompt_builder
    assert setup.fingerprint.full_hash
    assert setup.system_prompt == "system:director:appendix"
    assert prompt_builder.fingerprint_appendix == "appendix"
    assert prompt_builder.system_prompt_appendix == "appendix"


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("profile", "角色加载失败: broken"),
        ("request_appendix", "参数处理失败: broken"),
        ("fingerprint", "提示词构建失败: broken"),
        ("system_prompt", "系统提示词构建失败: broken"),
    ],
)
def test_format_role_turn_setup_error_uses_public_stage_messages(
    stage: RoleTurnSetupStage,
    expected: str,
) -> None:
    assert format_role_turn_setup_error(RoleTurnSetupError(stage, "broken")) == expected


def test_build_role_turn_prompt_setup_wraps_profile_failures() -> None:
    kernel = _Kernel(
        registry=_Registry(error=ValueError("missing profile")),
        prompt_builder=_PromptBuilder(),
    )

    with pytest.raises(RoleTurnSetupError) as exc_info:
        build_role_turn_prompt_setup(
            kernel=kernel,
            role="director",
            request=RoleTurnRequest(message="plain chat"),
        )

    assert exc_info.value.stage == "profile"
    assert exc_info.value.reason == "missing profile"


def test_build_role_turn_prompt_setup_wraps_fingerprint_failures() -> None:
    kernel = _Kernel(
        registry=_Registry(_profile()),
        prompt_builder=_PromptBuilder(fingerprint_error=RuntimeError("fingerprint failed")),
    )

    with pytest.raises(RoleTurnSetupError) as exc_info:
        build_role_turn_prompt_setup(
            kernel=kernel,
            role="director",
            request=RoleTurnRequest(message="plain chat"),
        )

    assert exc_info.value.stage == "fingerprint"
    assert exc_info.value.reason == "fingerprint failed"
