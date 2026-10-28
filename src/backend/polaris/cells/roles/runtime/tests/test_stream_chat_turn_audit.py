"""The retired role stream refuses execution before kernel or audit effects.

Current engineering execution belongs to EngineeringRunService. Historical
journal-reader tests live in audit.diagnosis/internal/tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService


def _forbid_legacy_collaborators(service: RoleRuntimeService, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("retired streaming reached a legacy execution or persistence collaborator")

    for name in ("_get_kernel", "_build_session_request", "_persist_session_turn_state", "emit_strategy_receipt"):
        monkeypatch.setattr(service, name, forbidden)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["pm", "chief_engineer", "director", "qa"])
async def test_retired_stream_reports_error_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    service = RoleRuntimeService()
    _forbid_legacy_collaborators(service, monkeypatch)
    command = ExecuteRoleSessionCommandV1(
        role=role, workspace=str(tmp_path), session_id=f"retired-{role}", user_message="Run this task", stream=True
    )

    events = [event async for event in service.stream_chat_turn(command)]

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["error_code"] == "legacy_engine_retired"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_run_identity_cannot_reactivate_legacy_stream_or_rewrite_journals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
    from polaris.kernelone.fs import KernelFileSystem

    fs = KernelFileSystem(str(tmp_path), LocalFileSystemAdapter())
    journal = ".polaris/runtime/runs/historical/logs/journal.norm.jsonl"
    original = '{"type":"historical","content":"保留旧记录"}\n'
    fs.workspace_write_text(journal, original, encoding="utf-8")
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    service = RoleRuntimeService()
    _forbid_legacy_collaborators(service, monkeypatch)
    command = ExecuteRoleSessionCommandV1(
        role="director",
        workspace=str(tmp_path),
        session_id="retired-session",
        run_id="historical",
        user_message="Continue the old run",
        stream=True,
    )

    events = [event async for event in service.stream_chat_turn(command)]

    assert len(events) == 1
    assert events[0]["error_code"] == "legacy_engine_retired"
    assert fs.workspace_read_text(journal, encoding="utf-8") == original
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before


@pytest.mark.asyncio
async def test_repeated_legacy_stream_requests_do_not_prepare_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = RoleRuntimeService()
    _forbid_legacy_collaborators(service, monkeypatch)
    command = ExecuteRoleSessionCommandV1(
        role="director", workspace=str(tmp_path), session_id="retired-session", user_message="Retry", stream=True
    )

    attempts = []
    for _ in range(2):
        attempts.append([event async for event in service.stream_chat_turn(command)])

    assert [events[0]["error_code"] for events in attempts] == ["legacy_engine_retired", "legacy_engine_retired"]
    assert all(len(events) == 1 for events in attempts)
    assert list(tmp_path.iterdir()) == []
