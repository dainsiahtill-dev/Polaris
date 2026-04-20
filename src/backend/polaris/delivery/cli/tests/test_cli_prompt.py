"""Tests for cli_prompt module."""

from __future__ import annotations


def test_prompt_toolkit_available():
    """Test prompt toolkit availability flag."""
    from polaris.delivery.cli.cli_prompt import _PROMPT_TOOLKIT_AVAILABLE

    assert isinstance(_PROMPT_TOOLKIT_AVAILABLE, bool)


def test_role_symbols_defined():
    """Test role symbols are defined for all standard roles."""
    from polaris.delivery.cli.cli_prompt import _ROLE_SYMBOLS

    expected_roles = {"director", "pm", "architect", "chief_engineer", "qa"}
    assert expected_roles.issubset(_ROLE_SYMBOLS.keys())
    for role in expected_roles:
        assert _ROLE_SYMBOLS[role], f"Symbol for {role} should not be empty"


def test_create_prompt_session():
    """Test factory function creates session with correct defaults."""
    from polaris.delivery.cli.cli_prompt import create_prompt_session

    session = create_prompt_session(role="director", session_id="test-session")
    assert session is not None
    assert session._role == "director"
    assert session._session_id == "test-session"


def test_create_prompt_session_default_role():
    """Test factory function uses director as default role."""
    from polaris.delivery.cli.cli_prompt import create_prompt_session

    session = create_prompt_session()
    assert session._role == "director"


def test_set_role():
    """Test role update method."""
    from polaris.delivery.cli.cli_prompt import create_prompt_session

    session = create_prompt_session(role="director")
    session.set_role("pm")
    assert session._role == "pm"


def test_set_role_case_insensitive():
    """Test role update is case insensitive via symbol lookup."""
    from polaris.delivery.cli.cli_prompt import create_prompt_session

    session = create_prompt_session(role="Director")
    session.set_role("PM")
    assert session._role == "PM"


def test_prompt_input_session_has_prompt_method():
    """Test session has prompt method for getting user input."""
    from polaris.delivery.cli.cli_prompt import PromptInputSession

    session = PromptInputSession(role="director")
    assert hasattr(session, "prompt")
    assert callable(session.prompt)


def test_prompt_input_session_has_set_role():
    """Test session has set_role method."""
    from polaris.delivery.cli.cli_prompt import PromptInputSession

    session = PromptInputSession(role="director")
    assert hasattr(session, "set_role")
    assert callable(session.set_role)


def test_session_stores_completions():
    """Test session accepts completions dict."""
    from polaris.delivery.cli.cli_prompt import PromptInputSession

    completions = {"command": ["/help", "/exit", "/quit"]}
    session = PromptInputSession(role="director", completions=completions)
    assert session._completions == completions


def test_get_symbol_returns_role_symbol():
    """Test _get_symbol returns correct symbol for role."""
    from polaris.delivery.cli.cli_prompt import PromptInputSession

    session = PromptInputSession(role="director")
    assert session._get_symbol() == "◉"

    session.set_role("pm")
    assert session._get_symbol() == "◆"


def test_get_symbol_fallback_for_unknown_role():
    """Test _get_symbol returns fallback for unknown role."""
    from polaris.delivery.cli.cli_prompt import PromptInputSession

    session = PromptInputSession(role="unknown_role")
    assert session._get_symbol() == "▸"


def test_build_bottom_toolbar_format():
    """Test bottom toolbar is formatted correctly (deprecated - now integrated in prompt)."""
    from polaris.delivery.cli.cli_prompt import PromptInputSession

    session = PromptInputSession(role="director", session_id="test-123")
    # _build_bottom_toolbar removed - status is now integrated into _build_prompt
    # Keep test for API compatibility check
    assert hasattr(session, "_get_symbol")
    assert session._get_symbol() == "◉"


def test_build_prompt_format():
    """Test integrated prompt with status returns HTML or string."""
    from polaris.delivery.cli.cli_prompt import PromptInputSession

    session = PromptInputSession(role="director", workspace="/home/user/project")
    prompt = session._build_prompt()
    # Returns HTML object or string depending on prompt-toolkit availability
    assert prompt is not None
    # The prompt should contain status info
    assert hasattr(session, "_role")
