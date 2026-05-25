from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.delivery.http.v2.llm_event_filters import filter_llm_events_by_workspace


@dataclass
class _Event:
    metadata: dict[str, Any]


def test_filter_llm_events_by_workspace_matches_normalized_windows_style_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "Product"
    workspace.mkdir()
    matching = _Event(metadata={"workspace": str(workspace).upper()})
    matching_extra = _Event(metadata={"extra_fields": {"workspace": workspace.as_posix() + "/"}})
    other = _Event(metadata={"workspace": str(tmp_path / "Other")})

    events = filter_llm_events_by_workspace([matching, matching_extra, other], str(workspace))

    assert events == [matching, matching_extra]


def test_filter_llm_events_by_workspace_requires_explicit_workspace_tag(tmp_path: Path) -> None:
    workspace = tmp_path / "Product"
    workspace.mkdir()
    untagged = _Event(metadata={})
    matching = _Event(metadata={"workspace": str(workspace)})

    events = filter_llm_events_by_workspace([untagged, matching], str(workspace))

    assert events == [matching]
