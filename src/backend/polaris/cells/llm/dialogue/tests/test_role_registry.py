"""Tests for dialogue role registry alignment."""

from __future__ import annotations

from polaris.cells.llm.dialogue.public import get_registered_roles
from polaris.cells.roles.kernel.public.prompt_templates_facade import ROLE_PROMPT_TEMPLATES


def test_registered_roles_include_resident_agi() -> None:
    roles = get_registered_roles()

    assert "resident_agi" in roles


def test_resident_agi_prompt_uses_shared_role_foundation() -> None:
    prompt = ROLE_PROMPT_TEMPLATES["resident_agi"]

    assert "Resident AGI Supervisor" in prompt
    assert "RoleRuntime / ContextOS / TransactionKernel" in prompt
    assert "PM、Chief Engineer、Director、QA" in prompt
    assert "平台级可访问权限和受控操作权限" in prompt
    assert "能力目录、决策边界和 canonical contract" in prompt
    assert "不能绕过角色链路" in prompt
    assert "软件工程师" not in prompt
