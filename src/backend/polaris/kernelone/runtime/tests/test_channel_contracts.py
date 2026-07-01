"""Runtime channel contract convergence tests."""

from __future__ import annotations

from polaris.domain.director import constants as director_constants
from polaris.kernelone.runtime import channel_contracts, defaults


def test_channel_contracts_are_single_source_for_runtime_and_director_reexports() -> None:
    assert defaults.CHANNEL_FILES is channel_contracts.CHANNEL_FILES
    assert defaults.HISTORICAL_CHANNEL_FILES is channel_contracts.HISTORICAL_CHANNEL_FILES
    assert director_constants.CHANNEL_FILES is channel_contracts.CHANNEL_FILES
    assert director_constants.HISTORICAL_CHANNEL_FILES is channel_contracts.HISTORICAL_CHANNEL_FILES
    assert defaults.NEW_CHANNEL_METADATA is channel_contracts.NEW_CHANNEL_METADATA
    assert director_constants.NEW_CHANNEL_METADATA is channel_contracts.NEW_CHANNEL_METADATA


def test_channel_contracts_keep_archive_paths_explicit_and_channel_files_canonical() -> None:
    assert channel_contracts.RUNTIME_V2_JOURNAL_PATH == "runtime/runs/{run_id}/logs/journal.norm.jsonl"
    assert channel_contracts.CANONICAL_RUNTIME_V2_CHANNEL_FILES == {
        "system": channel_contracts.RUNTIME_V2_JOURNAL_PATH,
        "process": channel_contracts.RUNTIME_V2_JOURNAL_PATH,
        "llm": channel_contracts.RUNTIME_V2_JOURNAL_PATH,
    }
    assert channel_contracts.HISTORICAL_CHANNEL_FILES["runtime_events"] == "runtime/events/runtime.events.jsonl"
    assert "runtime_events" not in channel_contracts.CHANNEL_FILES
    assert "pm_report" not in channel_contracts.CHANNEL_FILES
    assert channel_contracts.CHANNEL_FILES["system"] == channel_contracts.RUNTIME_V2_JOURNAL_PATH
