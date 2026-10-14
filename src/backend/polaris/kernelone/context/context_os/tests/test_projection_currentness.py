"""ContextOS commit currentness through real controlled worker interleavings."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.context.context_os.runtime import StateFirstContextOS
from polaris.kernelone.errors import ValidationError


@pytest.mark.asyncio
async def test_old_compute_cannot_overwrite_new_committed_working_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context_os = StateFirstContextOS(workspace=str(tmp_path))
    old_computed = threading.Event()
    release_old = threading.Event()
    real_compute = context_os._project_via_pipeline_sync

    def controlled(*args: Any, **kwargs: Any) -> Any:
        result = real_compute(*args, **kwargs)
        if args[1][0]["content"] == "OLD INPUT":
            old_computed.set()
            assert release_old.wait(10), "test must release actual worker"
        return result

    monkeypatch.setattr(context_os, "_project_via_pipeline_sync", controlled)
    old_task = asyncio.create_task(context_os.project(messages=[{"role": "user", "content": "OLD INPUT"}]))
    try:
        assert await asyncio.to_thread(old_computed.wait, 10)
        latest = await context_os.project(messages=[{"role": "user", "content": "NEW INPUT"}])
        latest_state = context_os._working_state_manager.snapshot()
        latest_report = context_os.get_last_projection_report()
        assert any(event.content == "NEW INPUT" for event in latest.snapshot.transcript_log)
        release_old.set()
        old_result = (await asyncio.gather(old_task, return_exceptions=True))[0]
        assert context_os._working_state_manager.snapshot() == latest_state
        assert context_os.get_last_projection_report() == latest_report
        assert isinstance(old_result, ValidationError), "an uncommitted old projection must not be returned as current"
    finally:
        release_old.set()
        await asyncio.gather(old_task, return_exceptions=True)
        await context_os.close()


@pytest.mark.asyncio
async def test_serial_projection_remains_current(tmp_path: Path) -> None:
    context_os = StateFirstContextOS(workspace=str(tmp_path))
    try:
        first = await context_os.project(messages=[{"role": "user", "content": "SERIAL FIRST"}])
        first_report = context_os.get_last_projection_report()
        second = await context_os.project(
            messages=[{"role": "user", "content": "SERIAL SECOND"}], existing_snapshot=first.snapshot
        )
        assert any(event.content == "SERIAL SECOND" for event in second.snapshot.transcript_log)
        assert context_os._working_state_manager.current() == second.snapshot.working_state
        assert context_os.get_last_projection_report()["projection_id"] != first_report["projection_id"]
    finally:
        await context_os.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("same_workspace", [False, True])
async def test_projection_versions_are_isolated_between_owner_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_workspace: bool
) -> None:
    first = StateFirstContextOS(workspace=str(tmp_path / "first"))
    second = StateFirstContextOS(workspace=str(tmp_path / ("first" if same_workspace else "second")))
    computed = threading.Event()
    release = threading.Event()
    real_compute = first._project_via_pipeline_sync

    def controlled(*args: Any, **kwargs: Any) -> Any:
        result = real_compute(*args, **kwargs)
        computed.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(first, "_project_via_pipeline_sync", controlled)
    first_task = asyncio.create_task(first.project(messages=[{"role": "user", "content": "FIRST OWNER"}]))
    try:
        assert await asyncio.to_thread(computed.wait, 10)
        second_result = await second.project(messages=[{"role": "user", "content": "SECOND OWNER"}])
        release.set()
        first_result = await first_task
        assert first._working_state_manager.current() == first_result.snapshot.working_state
        assert second._working_state_manager.current() == second_result.snapshot.working_state
        assert any(event.content == "FIRST OWNER" for event in first_result.snapshot.transcript_log)
        assert any(event.content == "SECOND OWNER" for event in second_result.snapshot.transcript_log)
    finally:
        release.set()
        await asyncio.gather(first_task, return_exceptions=True)
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_worker_uses_detached_messages_and_external_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context_os = StateFirstContextOS(workspace=str(tmp_path))
    entered = threading.Event()
    release = threading.Event()
    try:
        seed = await context_os.project(messages=[{"role": "user", "content": "ORIGINAL HISTORY"}])
        external = seed.snapshot.to_dict()
        messages = [{"role": "user", "content": "ORIGINAL INPUT"}]
        real_compute = context_os._project_via_pipeline_sync

        def controlled(*args: Any, **kwargs: Any) -> Any:
            entered.set()
            assert release.wait(10)
            return real_compute(*args, **kwargs)

        monkeypatch.setattr(context_os, "_project_via_pipeline_sync", controlled)
        pending = asyncio.create_task(context_os.project(messages=messages, existing_snapshot=external))
        assert await asyncio.to_thread(entered.wait, 10)
        messages[0]["content"] = "MUTATED INPUT"
        external["transcript_log"][0]["content"] = "MUTATED HISTORY"
        release.set()
        result = await pending
        contents = [event.content for event in result.snapshot.transcript_log]
        assert "ORIGINAL INPUT" in contents and "ORIGINAL HISTORY" in contents
        assert "MUTATED INPUT" not in contents and "MUTATED HISTORY" not in contents
    finally:
        release.set()
        await context_os.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("newer_failure", ["cancel", "exception"])
async def test_failed_newer_compute_has_explicit_old_rejection_and_allows_next_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, newer_failure: str
) -> None:
    context_os = StateFirstContextOS(workspace=str(tmp_path))
    old_ready = threading.Event()
    new_ready = threading.Event()
    release_old = threading.Event()
    release_new = threading.Event()
    real_compute = context_os._project_via_pipeline_sync

    def controlled(*args: Any, **kwargs: Any) -> Any:
        result = real_compute(*args, **kwargs)
        content = args[1][0]["content"]
        if content == "OLDER":
            old_ready.set()
            assert release_old.wait(10)
        elif content == "NEWER":
            new_ready.set()
            if newer_failure == "exception":
                raise RuntimeError("controlled worker failure")
            assert release_new.wait(10)
        return result

    monkeypatch.setattr(context_os, "_project_via_pipeline_sync", controlled)
    before = context_os._working_state_manager.snapshot()
    old = asyncio.create_task(context_os.project(messages=[{"role": "user", "content": "OLDER"}]))
    newer = None
    try:
        assert await asyncio.to_thread(old_ready.wait, 10)
        newer = asyncio.create_task(context_os.project(messages=[{"role": "user", "content": "NEWER"}]))
        assert await asyncio.to_thread(new_ready.wait, 10)
        if newer_failure == "cancel":
            newer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await newer
            release_new.set()
        else:
            with pytest.raises(RuntimeError, match="controlled worker failure"):
                await newer
        release_old.set()
        old_result = (await asyncio.gather(old, return_exceptions=True))[0]
        assert isinstance(old_result, ValidationError)
        assert old_result.constraint == "projection_input_version"
        assert context_os._working_state_manager.snapshot() == before
        recovered = await context_os.project(messages=[{"role": "user", "content": "LATEST RETRY"}])
        assert any(event.content == "LATEST RETRY" for event in recovered.snapshot.transcript_log)
        assert context_os._working_state_manager.current() == recovered.snapshot.working_state
    finally:
        release_old.set()
        release_new.set()
        await asyncio.gather(old, *([newer] if newer is not None else []), return_exceptions=True)
        await context_os.close()


@pytest.mark.asyncio
async def test_report_io_failure_does_not_roll_back_input_version_or_return_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.context.context_os.runtime import engine as engine_module

    context_os = StateFirstContextOS(workspace=str(tmp_path))
    old_ready = threading.Event()
    release_old = threading.Event()
    real_compute = context_os._project_via_pipeline_sync

    def controlled(*args: Any, **kwargs: Any) -> Any:
        result = real_compute(*args, **kwargs)
        if args[1][0]["content"] == "OLD BEFORE IO FAILURE":
            old_ready.set()
            assert release_old.wait(10)
        return result

    def fail_report(*args: Any, **kwargs: Any) -> None:
        raise OSError("controlled report persistence failure")

    monkeypatch.setattr(context_os, "_project_via_pipeline_sync", controlled)
    monkeypatch.setattr(engine_module, "record_contextos_projection_report", fail_report)
    old = asyncio.create_task(context_os.project(messages=[{"role": "user", "content": "OLD BEFORE IO FAILURE"}]))
    try:
        assert await asyncio.to_thread(old_ready.wait, 10)
        with pytest.raises(OSError, match="controlled report persistence failure"):
            await context_os.project(messages=[{"role": "user", "content": "NEW COMMITTED BEFORE REPORT IO"}])
        new_state = context_os._working_state_manager.snapshot()
        assert any(event["content"] == "NEW COMMITTED BEFORE REPORT IO" for event in new_state["transcript_log"])
        release_old.set()
        old_result = (await asyncio.gather(old, return_exceptions=True))[0]
        assert isinstance(old_result, ValidationError)
        assert context_os._working_state_manager.snapshot() == new_state
    finally:
        release_old.set()
        await asyncio.gather(old, return_exceptions=True)
        await context_os.close()
