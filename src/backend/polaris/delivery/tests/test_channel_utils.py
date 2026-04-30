"""Tests for polaris.delivery.ws.endpoints.channel_utils module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from polaris.delivery.ws.endpoints.channel_utils import (
    channel_max_chars,
    is_llm_channel,
    is_process_channel,
    normalize_roles,
    resolve_channel_path,
    resolve_current_run_id,
    wants_role,
)


class TestIsLlmChannel:
    """Tests for is_llm_channel function."""

    def test_exact_llm(self) -> None:
        """Test exact 'llm' channel."""
        assert is_llm_channel("llm") is True

    def test_pm_llm_suffix(self) -> None:
        """Test 'pm_llm' channel."""
        assert is_llm_channel("pm_llm") is True

    def test_director_llm_suffix(self) -> None:
        """Test 'director_llm' channel."""
        assert is_llm_channel("director_llm") is True

    def test_non_llm_channel(self) -> None:
        """Test non-LLM channel."""
        assert is_llm_channel("system") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert is_llm_channel("") is False

    def test_llm_prefix_not_suffix(self) -> None:
        """Test that prefix doesn't match, only suffix."""
        assert is_llm_channel("llm_pm") is False

    def test_case_sensitive(self) -> None:
        """Test case sensitivity."""
        assert is_llm_channel("LLM") is False

    def test_partial_match(self) -> None:
        """Test partial match behavior."""
        assert is_llm_channel("llm_extra") is False  # does not end with _llm
        assert is_llm_channel("my_llm") is True

    def test_just_underscore_llm(self) -> None:
        """Test '_llm' pattern."""
        assert is_llm_channel("_llm") is True


class TestIsProcessChannel:
    """Tests for is_process_channel function."""

    def test_system(self) -> None:
        """Test 'system' is process channel."""
        assert is_process_channel("system") is True

    def test_process(self) -> None:
        """Test 'process' is process channel."""
        assert is_process_channel("process") is True

    def test_pm_subprocess(self) -> None:
        """Test 'pm_subprocess' is process channel."""
        assert is_process_channel("pm_subprocess") is True

    def test_director_console(self) -> None:
        """Test 'director_console' is process channel."""
        assert is_process_channel("director_console") is True

    def test_pm_report(self) -> None:
        """Test 'pm_report' is process channel."""
        assert is_process_channel("pm_report") is True

    def test_pm_log(self) -> None:
        """Test 'pm_log' is process channel."""
        assert is_process_channel("pm_log") is True

    def test_ollama(self) -> None:
        """Test 'ollama' is process channel."""
        assert is_process_channel("ollama") is True

    def test_qa(self) -> None:
        """Test 'qa' is process channel."""
        assert is_process_channel("qa") is True

    def test_runlog(self) -> None:
        """Test 'runlog' is process channel."""
        assert is_process_channel("runlog") is True

    def test_planner(self) -> None:
        """Test 'planner' is process channel."""
        assert is_process_channel("planner") is True

    def test_engine_status(self) -> None:
        """Test 'engine_status' is process channel."""
        assert is_process_channel("engine_status") is True

    def test_non_process_channel(self) -> None:
        """Test non-process channel."""
        assert is_process_channel("llm") is False

    def test_empty_string(self) -> None:
        """Test empty string."""
        assert is_process_channel("") is False

    def test_unknown_channel(self) -> None:
        """Test unknown channel."""
        assert is_process_channel("unknown") is False


class TestChannelMaxChars:
    """Tests for channel_max_chars function."""

    def test_llm_channel(self) -> None:
        """Test LLM channel limit."""
        assert channel_max_chars("llm") == 500000

    def test_pm_llm_channel(self) -> None:
        """Test pm_llm channel limit."""
        assert channel_max_chars("pm_llm") == 500000

    def test_system_channel(self) -> None:
        """Test system channel limit."""
        assert channel_max_chars("system") == 20000

    def test_process_channel(self) -> None:
        """Test process channel limit."""
        assert channel_max_chars("process") == 20000

    def test_unknown_channel(self) -> None:
        """Test unknown channel defaults to 20000."""
        assert channel_max_chars("unknown") == 20000

    def test_empty_string(self) -> None:
        """Test empty string channel."""
        assert channel_max_chars("") == 20000


class TestWantsRole:
    """Tests for wants_role function."""

    def test_empty_set_includes_all(self) -> None:
        """Test empty roles set includes all roles."""
        assert wants_role(set(), "pm") is True
        assert wants_role(set(), "director") is True
        assert wants_role(set(), "qa") is True

    def test_role_in_set(self) -> None:
        """Test role in set returns True."""
        assert wants_role({"pm", "director"}, "pm") is True

    def test_role_not_in_set(self) -> None:
        """Test role not in set returns False."""
        assert wants_role({"pm", "director"}, "qa") is False

    def test_single_role_set(self) -> None:
        """Test single role set."""
        assert wants_role({"pm"}, "pm") is True
        assert wants_role({"pm"}, "director") is False

    def test_empty_set_with_empty_role(self) -> None:
        """Test empty set with empty role string."""
        assert wants_role(set(), "") is True


class TestNormalizeRoles:
    """Tests for normalize_roles function."""

    def test_none_input(self) -> None:
        """Test None input returns empty set."""
        assert normalize_roles(None) == set()

    def test_empty_string(self) -> None:
        """Test empty string returns empty set."""
        assert normalize_roles("") == set()

    def test_single_role(self) -> None:
        """Test single role."""
        assert normalize_roles("pm") == {"pm"}

    def test_multiple_roles(self) -> None:
        """Test multiple roles."""
        assert normalize_roles("pm,director,qa") == {"pm", "director", "qa"}

    def test_whitespace_handling(self) -> None:
        """Test whitespace handling."""
        assert normalize_roles(" pm , director ") == {"pm", "director"}

    def test_case_normalization(self) -> None:
        """Test case normalization to lowercase."""
        assert normalize_roles("PM,DIRECTOR") == {"pm", "director"}

    def test_invalid_roles_filtered(self) -> None:
        """Test invalid roles are filtered out."""
        assert normalize_roles("pm,invalid,qa") == {"pm", "qa"}

    def test_all_invalid_roles(self) -> None:
        """Test all invalid roles returns empty set."""
        assert normalize_roles("invalid1,invalid2") == set()

    def test_mixed_valid_invalid(self) -> None:
        """Test mixed valid and invalid roles."""
        assert normalize_roles("pm,foo,director,bar,qa") == {"pm", "director", "qa"}

    def test_unknown_role_not_included(self) -> None:
        """Test that only pm/director/qa are accepted."""
        assert normalize_roles("admin") == set()


class TestResolveCurrentRunId:
    """Tests for resolve_current_run_id function."""

    def test_no_file_returns_empty(self, tmp_path: Path) -> None:
        """Test missing file returns empty string."""
        assert resolve_current_run_id(str(tmp_path)) == ""

    def test_valid_file_returns_run_id(self, tmp_path: Path) -> None:
        """Test valid latest_run.json returns run_id."""
        latest_file = tmp_path / "latest_run.json"
        latest_file.write_text('{"run_id": "run-123"}')

        with patch("polaris.cells.runtime.projection.public.service.read_json") as mock_read:
            mock_read.return_value = {"run_id": "run-123"}
            result = resolve_current_run_id(str(tmp_path))
            assert result == "run-123"

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        """Test invalid JSON returns empty string."""
        latest_file = tmp_path / "latest_run.json"
        latest_file.write_text("not json")

        with patch("polaris.cells.runtime.projection.public.service.read_json") as mock_read:
            mock_read.side_effect = ValueError("bad json")
            result = resolve_current_run_id(str(tmp_path))
            assert result == ""

    def test_non_dict_payload_returns_empty(self, tmp_path: Path) -> None:
        """Test non-dict payload returns empty string."""
        latest_file = tmp_path / "latest_run.json"
        latest_file.write_text("[1, 2, 3]")

        with patch("polaris.cells.runtime.projection.public.service.read_json") as mock_read:
            mock_read.return_value = [1, 2, 3]
            result = resolve_current_run_id(str(tmp_path))
            assert result == ""

    def test_missing_run_id_returns_empty(self, tmp_path: Path) -> None:
        """Test missing run_id field returns empty string."""
        latest_file = tmp_path / "latest_run.json"
        latest_file.write_text('{"other": "value"}')

        with patch("polaris.cells.runtime.projection.public.service.read_json") as mock_read:
            mock_read.return_value = {"other": "value"}
            result = resolve_current_run_id(str(tmp_path))
            assert result == ""


class TestResolveChannelPath:
    """Tests for resolve_channel_path function."""

    def test_system_channel(self, tmp_path: Path) -> None:
        """Test system channel path resolution."""
        with patch("polaris.delivery.ws.endpoints.channel_utils.resolve_current_run_id") as mock_resolve:
            mock_resolve.return_value = "run-1"
            result = resolve_channel_path(str(tmp_path), str(tmp_path), "system")
            assert "journal.norm.jsonl" in result
            assert "run-1" in result

    def test_process_channel(self, tmp_path: Path) -> None:
        """Test process channel path resolution."""
        with patch("polaris.delivery.ws.endpoints.channel_utils.resolve_current_run_id") as mock_resolve:
            mock_resolve.return_value = "run-1"
            result = resolve_channel_path(str(tmp_path), str(tmp_path), "process")
            assert "journal.norm.jsonl" in result

    def test_llm_channel(self, tmp_path: Path) -> None:
        """Test llm channel path resolution."""
        with patch("polaris.delivery.ws.endpoints.channel_utils.resolve_current_run_id") as mock_resolve:
            mock_resolve.return_value = "run-1"
            result = resolve_channel_path(str(tmp_path), str(tmp_path), "llm")
            assert "journal.norm.jsonl" in result

    def test_no_run_id_returns_empty(self, tmp_path: Path) -> None:
        """Test no run_id returns empty string."""
        with patch("polaris.delivery.ws.endpoints.channel_utils.resolve_current_run_id") as mock_resolve:
            mock_resolve.return_value = ""
            result = resolve_channel_path(str(tmp_path), str(tmp_path), "system")
            assert result == ""

    def test_known_non_journal_channel(self, tmp_path: Path) -> None:
        """Test known non-journal channel uses CHANNEL_FILES."""
        with (
            patch("polaris.cells.runtime.projection.public.service.CHANNEL_FILES", {"custom": "custom.log"}),
            patch("polaris.cells.runtime.projection.public.service.resolve_artifact_path") as mock_resolve,
        ):
            mock_resolve.return_value = str(tmp_path / "custom.log")
            result = resolve_channel_path(str(tmp_path), str(tmp_path), "custom")
            assert result == str(tmp_path / "custom.log")

    def test_unknown_channel_returns_empty(self, tmp_path: Path) -> None:
        """Test unknown channel returns empty string."""
        with patch("polaris.cells.runtime.projection.public.service.CHANNEL_FILES", {}):
            result = resolve_channel_path(str(tmp_path), str(tmp_path), "unknown")
            assert result == ""


class TestModuleExports:
    """Tests for module exports."""

    def test_all_exports(self) -> None:
        """Test __all__ contains expected exports."""
        from polaris.delivery.ws.endpoints.channel_utils import __all__

        assert "channel_max_chars" in __all__
        assert "is_llm_channel" in __all__
        assert "is_process_channel" in __all__
        assert "normalize_roles" in __all__
        assert "resolve_channel_path" in __all__
        assert "resolve_current_run_id" in __all__
        assert "wants_role" in __all__
