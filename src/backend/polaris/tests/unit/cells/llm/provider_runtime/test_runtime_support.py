from __future__ import annotations

from polaris.cells.llm.provider_runtime.public.service import is_role_runtime_supported, role_runtime_support_issue


def test_director_rejects_deepseek_anthropic_tool_choice_incompatible_runtime() -> None:
    assert (
        is_role_runtime_supported(
            "director",
            "anthropic_compat-deepseek",
            {
                "type": "anthropic_compat",
                "base_url": "https://api.deepseek.com/anthropic",
                "model": "deepseek-v4-pro",
            },
        )
        is False
    )


def test_director_rejects_explicitly_disabled_tool_choice_runtime() -> None:
    assert (
        is_role_runtime_supported(
            "director",
            "anthropic_compat-custom",
            {
                "type": "anthropic_compat",
                "base_url": "https://api.example.com/anthropic",
                "disable_tool_choice": True,
            },
        )
        is False
    )


def test_director_rejects_unverified_minimax_runtime() -> None:
    assert (
        is_role_runtime_supported(
            "director",
            "minimax-1",
            {
                "type": "minimax",
                "base_url": "https://api.minimaxi.com/v1",
                "model": "MiniMax-M2.7-highspeed",
            },
        )
        is False
    )


def test_director_accepts_contract_verified_minimax_runtime() -> None:
    assert (
        is_role_runtime_supported(
            "director",
            "minimax-verified",
            {
                "type": "minimax",
                "base_url": "https://api.minimaxi.com/v1",
                "model": "MiniMax-M2.7-highspeed",
                "director_tool_contract_supported": True,
            },
        )
        is True
    )


def test_non_director_roles_keep_generic_runtime_support() -> None:
    assert (
        is_role_runtime_supported(
            "pm",
            "anthropic_compat-deepseek",
            {
                "type": "anthropic_compat",
                "base_url": "https://api.deepseek.com/anthropic",
                "model": "deepseek-v4-pro",
            },
        )
        is True
    )


def test_director_accepts_standard_tool_choice_runtime() -> None:
    assert (
        is_role_runtime_supported(
            "director",
            "anthropic_compat-claude",
            {
                "type": "anthropic_compat",
                "base_url": "https://api.anthropic.com",
                "model": "claude-3-5-sonnet",
            },
        )
        is True
    )


def test_director_rejects_codex_cli_read_only_sandbox() -> None:
    cfg = {
        "type": "codex_cli",
        "codex_exec": {"sandbox": "read-only"},
    }

    assert is_role_runtime_supported("director", "codex_cli", cfg) is False
    assert role_runtime_support_issue("director", "codex_cli", cfg) == "director_codex_read_only_sandbox"


def test_director_rejects_codex_cli_missing_sandbox_as_read_only_default() -> None:
    cfg = {"type": "codex_cli"}

    assert is_role_runtime_supported("director", "codex_cli", cfg) is False
    assert role_runtime_support_issue("director", "codex_cli", cfg) == "director_codex_read_only_sandbox"


def test_director_accepts_codex_cli_workspace_write_sandbox() -> None:
    assert (
        is_role_runtime_supported(
            "director",
            "codex_cli",
            {
                "type": "codex_cli",
                "codex_exec": {"sandbox": "workspace-write"},
            },
        )
        is True
    )


def test_director_accepts_codex_cli_danger_full_access_sandbox() -> None:
    assert (
        is_role_runtime_supported(
            "director",
            "codex_cli",
            {
                "type": "codex_cli",
                "codex_exec": {"sandbox": "danger-full-access"},
            },
        )
        is True
    )
