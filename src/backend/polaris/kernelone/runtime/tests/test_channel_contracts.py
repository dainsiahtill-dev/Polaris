"""Runtime channel contract convergence tests."""

from __future__ import annotations

from polaris.domain.director import constants as director_constants
from polaris.kernelone.runtime import channel_contracts, defaults


def test_channel_contracts_are_single_source_for_runtime_and_director_reexports() -> None:
    assert defaults.CHANNEL_FILES is channel_contracts.CHANNEL_FILES
    assert director_constants.CHANNEL_FILES is channel_contracts.CHANNEL_FILES
    assert defaults.NEW_CHANNEL_METADATA is channel_contracts.NEW_CHANNEL_METADATA
    assert director_constants.NEW_CHANNEL_METADATA is channel_contracts.NEW_CHANNEL_METADATA


def test_channel_contracts_keep_historical_aliases_readable_and_canonical_channels_primary() -> None:
    assert channel_contracts.RUNTIME_V2_JOURNAL_PATH == "runtime/runs/{run_id}/logs/journal.norm.jsonl"
    assert channel_contracts.CANONICAL_RUNTIME_V2_CHANNEL_FILES == {
        "system": channel_contracts.RUNTIME_V2_JOURNAL_PATH,
        "process": channel_contracts.RUNTIME_V2_JOURNAL_PATH,
        "llm": channel_contracts.RUNTIME_V2_JOURNAL_PATH,
    }
    assert channel_contracts.HISTORICAL_CHANNEL_FILES["runtime_events"] == "runtime/events/runtime.events.jsonl"
    assert channel_contracts.CHANNEL_FILES["runtime_events"] == "runtime/events/runtime.events.jsonl"
    assert channel_contracts.CHANNEL_FILES["system"] == channel_contracts.RUNTIME_V2_JOURNAL_PATH
