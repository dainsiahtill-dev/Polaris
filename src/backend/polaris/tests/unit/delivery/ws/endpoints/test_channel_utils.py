"""Unit tests for canonical runtime WebSocket channel utilities."""

from __future__ import annotations

import json

from polaris.delivery.ws.endpoints.channel_utils import (
    RUNTIME_OBSERVABLE_ROLE_TOKENS,
    channel_max_chars,
    is_llm_channel,
    is_process_channel,
    normalize_roles,
    resolve_current_run_id,
    wants_role,
)
from polaris.kernelone.constants import RoleId


class TestCanonicalChannelClassification:
    def test_llm_exact(self) -> None:
        assert is_llm_channel("llm") is True

    def test_historical_llm_suffixes_are_not_runtime_channels(self) -> None:
        assert is_llm_channel("pm_llm") is False
        assert is_llm_channel("director_llm") is False

    def test_process_channels(self) -> None:
        assert is_process_channel("system") is True
        assert is_process_channel("process") is True
        assert is_process_channel("pm_subprocess") is False
        assert is_process_channel("pm_report") is False

    def test_channel_max_chars(self) -> None:
        assert channel_max_chars("llm") == 500000
        assert channel_max_chars("pm_llm") == 20000
        assert channel_max_chars("process") == 20000


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

    def test_valid_roles(self) -> None:
        assert normalize_roles("PM,director,qa") == {"pm", "director", "qa"}

    def test_invalid_roles_filtered(self) -> None:
        assert normalize_roles("pm,invalid,qa") == {"pm", "qa"}

    def test_runtime_observable_roles_include_resident_agi(self) -> None:
        assert normalize_roles("chief_engineer,resident_agi") == {
            "chief_engineer",
            "resident_agi",
        }
        assert "resident_agi" in RUNTIME_OBSERVABLE_ROLE_TOKENS

    def test_runtime_observable_roles_do_not_expand_task_consumers(self) -> None:
        assert RoleId.consumer_roles() == (RoleId.PM, RoleId.DIRECTOR, RoleId.QA)


class TestResolveCurrentRunId:
    def test_no_file(self, tmp_path) -> None:
        assert resolve_current_run_id(str(tmp_path)) == ""

    def test_valid_file(self, tmp_path) -> None:
        (tmp_path / "latest_run.json").write_text(json.dumps({"run_id": "run-123"}), encoding="utf-8")
        assert resolve_current_run_id(str(tmp_path)) == "run-123"

    def test_invalid_json(self, tmp_path) -> None:
        (tmp_path / "latest_run.json").write_text("not json", encoding="utf-8")
        assert resolve_current_run_id(str(tmp_path)) == ""

    def test_non_dict_payload(self, tmp_path) -> None:
        (tmp_path / "latest_run.json").write_text(json.dumps("string"), encoding="utf-8")
        assert resolve_current_run_id(str(tmp_path)) == ""
