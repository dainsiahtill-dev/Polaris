"""Tests for polaris.delivery.ws.endpoints.channel_utils."""

from __future__ import annotations

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
    def test_llm_exact(self) -> None:
        assert is_llm_channel("llm") is True

    def test_llm_suffix(self) -> None:
        assert is_llm_channel("pm_llm") is True
        assert is_llm_channel("director_llm") is True

    def test_non_llm(self) -> None:
        assert is_llm_channel("system") is False
        assert is_llm_channel("process") is False
        assert is_llm_channel("pm_report") is False


class TestIsProcessChannel:
    def test_system(self) -> None:
        assert is_process_channel("system") is True

    def test_process(self) -> None:
        assert is_process_channel("process") is True

    def test_pm_subprocess(self) -> None:
        assert is_process_channel("pm_subprocess") is True

    def test_non_process(self) -> None:
        assert is_process_channel("llm") is False
        assert is_process_channel("pm_llm") is False


class TestChannelMaxChars:
    def test_llm_channel(self) -> None:
        assert channel_max_chars("llm") == 500000
        assert channel_max_chars("pm_llm") == 500000

    def test_non_llm_channel(self) -> None:
        assert channel_max_chars("system") == 20000
        assert channel_max_chars("process") == 20000
        assert channel_max_chars("pm_report") == 20000


class TestWantsRole:
    def test_empty_roles(self) -> None:
        assert wants_role(set(), "pm") is True
        assert wants_role(set(), "director") is True

    def test_matching_role(self) -> None:
        assert wants_role({"pm"}, "pm") is True
        assert wants_role({"pm", "director"}, "director") is True

    def test_non_matching_role(self) -> None:
        assert wants_role({"pm"}, "director") is False
        assert wants_role({"qa"}, "pm") is False


class TestNormalizeRoles:
    def test_empty(self) -> None:
        assert normalize_roles(None) == set()
        assert normalize_roles("") == set()

    def test_single_role(self) -> None:
        assert normalize_roles("pm") == {"pm"}

    def test_multiple_roles(self) -> None:
        assert normalize_roles("pm,director,qa") == {"pm", "director", "qa"}

    def test_invalid_roles_filtered(self) -> None:
        assert normalize_roles("pm,invalid,qa") == {"pm", "qa"}

    def test_whitespace_trimmed(self) -> None:
        assert normalize_roles("  pm  ,  director  ") == {"pm", "director"}

    def test_case_normalized(self) -> None:
        assert normalize_roles("PM,Director") == {"pm", "director"}


class TestResolveCurrentRunId:
    def test_no_file(self, tmp_path) -> None:
        cache_root = str(tmp_path)
        assert resolve_current_run_id(cache_root) == ""

    def test_valid_file(self, tmp_path) -> None:
        cache_root = str(tmp_path)
        import json

        (tmp_path / "latest_run.json").write_text(json.dumps({"run_id": "run-123"}), encoding="utf-8")
        assert resolve_current_run_id(cache_root) == "run-123"

    def test_invalid_json(self, tmp_path) -> None:
        cache_root = str(tmp_path)
        (tmp_path / "latest_run.json").write_text("not json", encoding="utf-8")
        assert resolve_current_run_id(cache_root) == ""

    def test_non_dict_payload(self, tmp_path) -> None:
        cache_root = str(tmp_path)
        import json

        (tmp_path / "latest_run.json").write_text(json.dumps("string"), encoding="utf-8")
        assert resolve_current_run_id(cache_root) == ""


class TestResolveChannelPath:
    @patch("polaris.delivery.ws.endpoints.channel_utils.resolve_current_run_id")
    def test_system_channel(self, mock_resolve_run, tmp_path) -> None:
        mock_resolve_run.return_value = "run-123"
        cache_root = str(tmp_path)
        result = resolve_channel_path("/workspace", cache_root, "system")
        expected = str(tmp_path / "runs" / "run-123" / "logs" / "journal.norm.jsonl")
        assert result == expected

    @patch("polaris.delivery.ws.endpoints.channel_utils.resolve_current_run_id")
    def test_system_channel_no_run_id(self, mock_resolve_run, tmp_path) -> None:
        mock_resolve_run.return_value = ""
        cache_root = str(tmp_path)
        result = resolve_channel_path("/workspace", cache_root, "system")
        assert result == ""

    @patch("polaris.cells.runtime.projection.public.service.resolve_artifact_path")
    def test_known_channel(self, mock_resolve_artifact, tmp_path) -> None:
        mock_resolve_artifact.return_value = str(tmp_path / "artifact.txt")
        cache_root = str(tmp_path)
        result = resolve_channel_path("/workspace", cache_root, "pm_report")
        assert result == str(tmp_path / "artifact.txt")

    @patch("polaris.cells.runtime.projection.public.service.resolve_artifact_path")
    def test_unknown_channel(self, mock_resolve_artifact, tmp_path) -> None:
        mock_resolve_artifact.return_value = ""
        cache_root = str(tmp_path)
        result = resolve_channel_path("/workspace", cache_root, "unknown")
        assert result == ""
