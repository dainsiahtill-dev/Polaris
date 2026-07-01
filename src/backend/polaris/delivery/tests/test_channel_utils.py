"""Tests for canonical runtime WebSocket channel utilities."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from polaris.delivery.ws.endpoints.channel_utils import (
    RUNTIME_OBSERVABLE_ROLE_TOKENS,
    channel_max_chars,
    is_llm_channel,
    is_process_channel,
    normalize_roles,
    resolve_current_run_id,
    wants_role,
)


class TestCanonicalChannelClassification:
    """Runtime WS channels are canonical runtime.v2 tokens only."""

    def test_exact_llm_channel(self) -> None:
        assert is_llm_channel("llm") is True

    def test_historical_llm_suffixes_are_not_runtime_channels(self) -> None:
        assert is_llm_channel("pm_llm") is False
        assert is_llm_channel("director_llm") is False
        assert is_llm_channel("my_llm") is False

    def test_process_channels_are_canonical_journal_tokens(self) -> None:
        assert is_process_channel("system") is True
        assert is_process_channel("process") is True
        assert is_process_channel("pm_subprocess") is False
        assert is_process_channel("director_console") is False
        assert is_process_channel("pm_report") is False

    def test_channel_size_limits_follow_canonical_llm_only(self) -> None:
        assert channel_max_chars("llm") == 500000
        assert channel_max_chars("pm_llm") == 20000
        assert channel_max_chars("system") == 20000


class TestWantsRole:
    def test_empty_set_includes_all(self) -> None:
        assert wants_role(set(), "pm") is True
        assert wants_role(set(), "director") is True
        assert wants_role(set(), "qa") is True

    def test_role_in_set(self) -> None:
        assert wants_role({"pm", "director"}, "pm") is True

    def test_role_not_in_set(self) -> None:
        assert wants_role({"pm", "director"}, "qa") is False


class TestNormalizeRoles:
    def test_none_input(self) -> None:
        assert normalize_roles(None) == set()

    def test_valid_roles_are_normalized(self) -> None:
        assert normalize_roles(" PM , director , resident_agi ") == {
            "pm",
            "director",
            "resident_agi",
        }

    def test_invalid_roles_are_filtered(self) -> None:
        assert normalize_roles("pm,invalid,qa") == {"pm", "qa"}

    def test_observable_roles_include_resident_agi(self) -> None:
        assert "resident_agi" in RUNTIME_OBSERVABLE_ROLE_TOKENS


class TestResolveCurrentRunId:
    def test_no_file_returns_empty(self, tmp_path: Path) -> None:
        assert resolve_current_run_id(str(tmp_path)) == ""

    def test_valid_file_returns_run_id(self, tmp_path: Path) -> None:
        latest_file = tmp_path / "latest_run.json"
        latest_file.write_text('{"run_id": "run-123"}', encoding="utf-8")

        with patch("polaris.cells.runtime.projection.public.service.read_json") as mock_read:
            mock_read.return_value = {"run_id": "run-123"}
            assert resolve_current_run_id(str(tmp_path)) == "run-123"

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        latest_file = tmp_path / "latest_run.json"
        latest_file.write_text("not json", encoding="utf-8")

        with patch("polaris.cells.runtime.projection.public.service.read_json") as mock_read:
            mock_read.side_effect = ValueError("bad json")
            assert resolve_current_run_id(str(tmp_path)) == ""

    def test_non_dict_payload_returns_empty(self, tmp_path: Path) -> None:
        latest_file = tmp_path / "latest_run.json"
        latest_file.write_text("[1, 2, 3]", encoding="utf-8")

        with patch("polaris.cells.runtime.projection.public.service.read_json") as mock_read:
            mock_read.return_value = [1, 2, 3]
            assert resolve_current_run_id(str(tmp_path)) == ""


class TestModuleExports:
    def test_all_exports_are_canonical(self) -> None:
        from polaris.delivery.ws.endpoints.channel_utils import __all__

        assert "channel_max_chars" in __all__
        assert "is_llm_channel" in __all__
        assert "is_process_channel" in __all__
        assert "normalize_roles" in __all__
        assert "resolve_channel_path" not in __all__
        assert "resolve_current_run_id" in __all__
        assert "wants_role" in __all__
