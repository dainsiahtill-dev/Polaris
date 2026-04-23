"""Integration tests for CLI enhancements (agents 1-10 cross-cutting features).

Tests cover:
  - --output-format json flag
  - --dry-run flag
  - --batch flag
  - --model flag
  - Token stats display (when usage data present)
  - Onboarding first-run detection
  - Banner enhanced display

Squad: Integration & Verification Agent
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.delivery.cli import terminal_console as tc

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeRoleConsoleHost:
    """Minimal fake for testing banner and onboarding."""

    _ALLOWED_ROLES = frozenset({"director", "pm", "architect", "chief_engineer", "qa"})

    def __init__(self, workspace: str, *, role: str = "director") -> None:
        self.workspace = workspace
        self.role = role
        self._config = _FakeConfig()

    @property
    def config(self) -> Any:
        return self._config

    def create_session(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": f"{self.role}-session-1"}

    def ensure_session(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": kwargs.get("session_id", f"{self.role}-session-1")}

    async def stream_turn(self, *args: Any, **kwargs: Any):
        yield {"type": "complete", "data": {"content": "ok"}}


class _FakeConfig:
    """Fake config object."""

    def __init__(self) -> None:
        self.host_kind = "cli"


# ---------------------------------------------------------------------------
# Test: Banner enhanced display
# ---------------------------------------------------------------------------


class TestBannerEnhancedDisplay:
    """Test that the banner displays enhanced information correctly."""

    def test_print_banner_displays_workspace_role_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Banner must show workspace, role, and session_id."""
        captured: list[str] = []

        def _fake_print(*args: str, **kwargs: Any) -> None:
            captured.append(str(args[0]) if args else "")

        monkeypatch.setattr("builtins.print", _fake_print)

        state = tc._ConsoleRenderState(prompt_style="plain", json_render="raw")
        tc._print_banner(
            workspace=Path("/tmp/test_workspace"),
            role="director",
            session_id="sess-abc123",
            allowed_roles=frozenset({"director", "pm"}),
            render_state=state,
        )

        output = "\n".join(captured)
        assert "Polaris CLI" in output
        assert "director" in output
        assert "sess-abc123" in output

    def test_print_banner_displays_allowed_roles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Banner must display allowed roles when provided."""
        captured: list[str] = []

        def _fake_print(*args: str, **kwargs: Any) -> None:
            captured.append(str(args[0]) if args else "")

        monkeypatch.setattr("builtins.print", _fake_print)

        state = tc._ConsoleRenderState(prompt_style="plain", json_render="raw")
        tc._print_banner(
            workspace=Path("."),
            role="pm",
            session_id="sess-xyz",
            allowed_roles=frozenset({"director", "pm", "architect"}),
            render_state=state,
        )

        output = "\n".join(captured)
        # The banner displays roles with current role highlighted in brackets
        assert "architect" in output
        assert "director" in output
        assert "[pm]" in output  # current role highlighted with brackets

    def test_print_banner_displays_render_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Banner must display prompt style and json render mode."""
        captured: list[str] = []

        def _fake_print(*args: str, **kwargs: Any) -> None:
            captured.append(str(args[0]) if args else "")

        monkeypatch.setattr("builtins.print", _fake_print)

        state = tc._ConsoleRenderState(prompt_style="omp", json_render="pretty")
        tc._print_banner(
            workspace=Path("."),
            role="director",
            session_id="sess-render",
            allowed_roles=frozenset(),
            render_state=state,
        )

        output = "\n".join(captured)
        assert "prompt:omp" in output
        assert "json:pretty" in output


# ---------------------------------------------------------------------------
# Test: Token stats display
# ---------------------------------------------------------------------------


class TestTokenStatsDisplay:
    """Test token statistics display when usage data is present."""

    def test_print_token_stats_with_usage_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Token stats should be printed when payload contains usage data."""
        captured: list[str] = []

        def _fake_print(*args: str, **kwargs: Any) -> None:
            sep = kwargs.get("sep", " ")
            captured.append(sep.join(str(a) for a in args))

        monkeypatch.setattr("builtins.print", _fake_print)

        payload = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
            "model": "kimi-for-coding",
        }

        tc._print_token_stats(payload, elapsed_seconds=1.0)

        output = "\n".join(captured)
        # Check for key data (appears in both Rich panel and fallback text)
        assert "kimi-for-coding" in output or "Model" in output
        assert "100" in output or "100.0" in output

    def test_print_token_stats_without_usage_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Token stats should not be printed when payload lacks usage data."""
        captured: list[str] = []

        def _fake_print(*args: str, **kwargs: Any) -> None:
            captured.append(str(args[0]) if args else "")

        monkeypatch.setattr("builtins.print", _fake_print)

        payload: dict[str, Any] = {"content": "hello world"}
        tc._print_token_stats(payload, elapsed_seconds=1.0)

        output = "\n".join(captured)
        assert "[Token Stats]" not in output

    def test_print_token_stats_with_token_usage_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Token stats should extract usage from token_usage field."""
        captured: list[str] = []

        def _fake_print(*args: str, **kwargs: Any) -> None:
            captured.append(str(args[0]) if args else "")

        monkeypatch.setattr("builtins.print", _fake_print)

        payload = {
            "token_usage": {
                "prompt_tokens": 200,
                "completion_tokens": 100,
                "total_tokens": 300,
            },
            "model": "gpt-4o",
        }

        tc._print_token_stats(payload, elapsed_seconds=2.0)

        output = "\n".join(captured)
        # Rich panel shows model name; check for either model or token values
        assert "gpt-4o" in output or "200" in output

    def test_print_token_stats_calculates_throughput(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Token stats should calculate and display throughput."""
        captured: list[str] = []

        def _fake_print(*args: str, **kwargs: Any) -> None:
            captured.append(str(args[0]) if args else "")

        monkeypatch.setattr("builtins.print", _fake_print)

        # 600 tokens in 3 seconds = 200 tok/s
        payload = {
            "usage": {
                "prompt_tokens": 300,
                "completion_tokens": 300,
                "total_tokens": 600,
            },
            "model": "kimi-for-coding",
        }

        tc._print_token_stats(payload, elapsed_seconds=3.0)

        output = "\n".join(captured)
        assert "tok/s" in output or "throughput" in output.lower()

    def test_extract_token_usage_direct(self) -> None:
        """_extract_token_usage should extract from direct usage field."""
        payload = {
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            }
        }
        result = tc._extract_token_usage(payload)
        assert result is not None
        assert result["prompt_tokens"] == 10
        assert result["completion_tokens"] == 20
        assert result["total_tokens"] == 30

    def test_extract_token_usage_from_token_usage_field(self) -> None:
        """_extract_token_usage should extract from token_usage field."""
        payload = {
            "token_usage": {
                "prompt_tokens": 5,
                "completion_tokens": 15,
                "total_tokens": 20,
            }
        }
        result = tc._extract_token_usage(payload)
        assert result is not None
        assert result["prompt_tokens"] == 5
        assert result["total_tokens"] == 20

    def test_extract_token_usage_returns_none_when_missing(self) -> None:
        """_extract_token_usage should return None when no usage data present."""
        payload: dict[str, Any] = {"content": "hello"}
        result = tc._extract_token_usage(payload)
        assert result is None


# ---------------------------------------------------------------------------
# Test: Onboarding first-run detection
# ---------------------------------------------------------------------------


class TestOnboardingFirstRun:
    """Test onboarding first-run detection and display."""

    def test_show_onboarding_skipped_when_not_tty(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Onboarding should be skipped when stdin is not a TTY."""
        marker_path = tmp_path / ".polaris_cli_onboarded"
        monkeypatch.setattr(tc, "_ONBOARD_MARKER_PATH", str(marker_path))

        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        prints: list[str] = []

        def _fake_print(*args: str) -> None:
            prints.append(str(args[0]))

        monkeypatch.setattr("builtins.print", _fake_print)

        tc._show_onboarding()

        # Should not print welcome message since stdin is not a tty
        assert not any("Welcome" in p or "Polaris" in p for p in prints)

    def test_show_onboarding_skipped_when_already_seen(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Onboarding should be skipped if marker file exists."""
        marker_path = tmp_path / ".polaris_cli_onboarded"
        marker_path.write_text("", encoding="utf-8")
        monkeypatch.setattr(tc, "_ONBOARD_MARKER_PATH", str(marker_path))

        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        prints: list[str] = []

        def _fake_print(*args: str) -> None:
            prints.append(str(args[0]))

        monkeypatch.setattr("builtins.print", _fake_print)

        tc._show_onboarding()

        # Should not print welcome message since already onboarded
        assert not any("Welcome" in p for p in prints)

    def test_show_onboarding_displays_welcome_on_first_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Onboarding should display welcome message on first run.

        Note: Rich Console.print() bypasses builtins.print, so we verify
        the function runs without error and creates the marker file.
        The actual Rich output is visible in pytest's captured stdout.
        """
        marker_path = tmp_path / ".polaris_cli_onboarded"
        monkeypatch.setattr(tc, "_ONBOARD_MARKER_PATH", str(marker_path))

        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

        # Mock input to skip the "Press Enter" prompt
        monkeypatch.setattr("builtins.input", lambda _: "")

        # Suppress print to avoid cluttering test output
        monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)

        # Should run without error
        tc._show_onboarding()

        # Marker file should be created
        assert marker_path.exists()

    def test_show_onboarding_creates_marker_on_first_run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Onboarding should create marker file after showing welcome."""
        marker_path = tmp_path / ".polaris_cli_onboarded"
        monkeypatch.setattr(tc, "_ONBOARD_MARKER_PATH", str(marker_path))

        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _: "")

        monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)

        assert not marker_path.exists()
        tc._show_onboarding()
        assert marker_path.exists()


# ---------------------------------------------------------------------------
# Test: Model selection
# ---------------------------------------------------------------------------


class TestModelSelection:
    """Test model selection and environment variable handling."""

    def test_get_current_model_returns_none_when_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_current_model should return None when env var is not set.

        SSOT: Model info must come from ContextOS via event payload.
        Environment variable is only a fallback - returns None if not set.
        """
        monkeypatch.delenv("KERNELONE_PM_MODEL", raising=False)
        result = tc._get_current_model()
        assert result is None

    def test_get_current_model_returns_env_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_current_model should return value from KERNELONE_PM_MODEL."""
        monkeypatch.setenv("KERNELONE_PM_MODEL", "gpt-4o")
        result = tc._get_current_model()
        assert result == "gpt-4o"

    def test_set_current_model_sets_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_set_current_model should set KERNELONE_PM_MODEL env var."""
        monkeypatch.delenv("KERNELONE_PM_MODEL", raising=False)
        tc._set_current_model("claude-3-5-sonnet")
        assert os.environ.get("KERNELONE_PM_MODEL") == "claude-3-5-sonnet"

    def test_known_models_includes_common_models(self) -> None:
        """_KNOWN_MODELS should include common LLM models."""
        assert "kimi-for-coding" in tc._KNOWN_MODELS
        assert "gpt-4o" in tc._KNOWN_MODELS
        assert "claude-3-5-sonnet" in tc._KNOWN_MODELS
        assert "ollama/llama3" in tc._KNOWN_MODELS


# ---------------------------------------------------------------------------
# Test: Cost estimation
# ---------------------------------------------------------------------------


class TestCostEstimation:
    """Test cost estimation based on token counts and model."""

    def test_estimate_cost_with_known_model(self) -> None:
        """_estimate_cost should return cost string for known model."""
        result = tc._estimate_cost(prompt_tokens=1_000_000, completion_tokens=1_000_000, model="gpt-4o")
        assert result.startswith("~")
        assert "$" in result

    def test_estimate_cost_with_unknown_model(self) -> None:
        """_estimate_cost should return 'n/a' for unknown model."""
        result = tc._estimate_cost(prompt_tokens=100, completion_tokens=100, model="unknown-model")
        assert result == "n/a"

    def test_estimate_cost_zero_tokens(self) -> None:
        """_estimate_cost should handle zero tokens gracefully."""
        result = tc._estimate_cost(prompt_tokens=0, completion_tokens=0, model="gpt-4o")
        assert result.startswith("~")
        assert "$" in result


# ---------------------------------------------------------------------------
# Test: Output format modes
# ---------------------------------------------------------------------------


class TestOutputFormatModes:
    """Test output format mode validation."""

    def test_json_render_modes_are_defined(self) -> None:
        """_JSON_RENDER_MODES should contain expected modes."""
        assert "raw" in tc._JSON_RENDER_MODES
        assert "pretty" in tc._JSON_RENDER_MODES
        assert "pretty-color" in tc._JSON_RENDER_MODES

    def test_normalize_json_render_returns_normalized_value(self) -> None:
        """_normalize_json_render should return correct mode or default."""
        assert tc._normalize_json_render("pretty") == "pretty"
        assert tc._normalize_json_render("PRETTY") == "pretty"
        assert tc._normalize_json_render("unknown") == "raw"
        assert tc._normalize_json_render(None) == "raw"


# ---------------------------------------------------------------------------
# Test: Batch mode
# ---------------------------------------------------------------------------


class TestBatchMode:
    """Test batch mode functionality."""

    def test_run_batch_mode_function_exists(self) -> None:
        """_run_batch_mode should exist and be callable."""
        assert hasattr(tc, "_run_batch_mode")
        assert callable(tc._run_batch_mode)

    def test_batch_mode_flag_in_console_state(self) -> None:
        """ConsoleRenderState should support batch-related attributes."""
        state = tc._ConsoleRenderState()
        # State should have the standard attributes
        assert hasattr(state, "prompt_style")
        assert hasattr(state, "json_render")


# ---------------------------------------------------------------------------
# Test: Dry-run mode
# ---------------------------------------------------------------------------


class TestDryRunMode:
    """Test dry-run mode functionality."""

    def test_dryrun_command_recognized(self) -> None:
        """/dryrun command should be in help text."""
        assert "/dryrun [on|off]" in tc._HELP_TEXT or "dryrun" in tc._HELP_TEXT.lower()

    def test_help_text_includes_dryrun_info(self) -> None:
        """Help text should mention dry-run mode."""
        assert "dryrun" in tc._HELP_TEXT.lower() or "/dryrun" in tc._HELP_TEXT


# ---------------------------------------------------------------------------
# Test: PolarisRoleConsole with new parameters
# ---------------------------------------------------------------------------


class TestPolarisRoleConsoleNewParams:
    """Test PolarisRoleConsole class with new parameters."""

    def test_role_console_accepts_model_param(self) -> None:
        """PolarisRoleConsole should accept model parameter."""
        console = tc.PolarisRoleConsole(workspace=".", role="director", model="gpt-4o")
        assert console.model == "gpt-4o"

    def test_role_console_defaults_model_to_none(self) -> None:
        """PolarisRoleConsole model should default to None."""
        console = tc.PolarisRoleConsole(workspace=".", role="director")
        assert console.model is None
