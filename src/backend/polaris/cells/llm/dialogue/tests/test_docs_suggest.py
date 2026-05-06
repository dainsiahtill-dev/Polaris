from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.cells.llm.dialogue.internal import docs_suggest


class _RaisingExecutor:
    def __init__(self, workspace: str = ".") -> None:
        self.workspace = workspace

    async def invoke_stream(self, _request: Any) -> AsyncGenerator[dict[str, Any], None]:
        raise OSError("network stream failed")
        yield {}


@pytest.mark.asyncio
async def test_generate_docs_fields_stream_reports_unexpected_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docs_suggest, "CellAIExecutor", _RaisingExecutor)

    events = [
        event
        async for event in docs_suggest.generate_docs_fields_stream(
            workspace=".",
            settings=MagicMock(),
            fields={"goal": "Build a reliable desktop app"},
        )
    ]

    assert events == [{"type": "error", "error": "network stream failed"}]


def test_build_default_docs_fields_preserves_goal_and_backlog_defaults() -> None:
    fields = docs_suggest.build_default_docs_fields({"goal": "Build audit-ready workflow"})

    assert fields["goal"] == ["Build audit-ready workflow"]
    assert fields["backlog"]
    assert fields["definition_of_done"]
