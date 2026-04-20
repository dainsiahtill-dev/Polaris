"""FactoryStore lock + append_event governance (avoids heavy factory pipeline imports)."""

from __future__ import annotations

import logging
import tempfile
from collections import OrderedDict
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore


@pytest.fixture
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def store(temp_workspace):
    return FactoryStore(temp_workspace / ".polaris" / "factory")


@pytest.mark.asyncio
async def test_append_event_failure_is_logged(store, monkeypatch, caplog):
    caplog.set_level(logging.ERROR)
    run_id = "factory_append_fail"
    store.get_run_dir(run_id).mkdir(parents=True, exist_ok=True)

    async def boom(path, line):
        raise OSError("simulated write failure")

    monkeypatch.setattr(store, "_append_file", boom)
    with pytest.raises(OSError):
        await store.append_event(run_id, {"type": "x"})
    assert "append_event failed" in caplog.text
    assert run_id in caplog.text


def test_run_file_lock_table_is_bounded(monkeypatch):
    from polaris.cells.factory.pipeline.internal import factory_store as fs

    backup = OrderedDict(fs._RUN_FILE_LOCKS)
    try:
        with fs._RUN_FILE_LOCKS_GUARD:
            fs._RUN_FILE_LOCKS.clear()
        base = Path(tempfile.mkdtemp())
        try:
            for i in range(fs._MAX_LOCK_ENTRIES + 50):
                p = base / f"lock_probe_{i}.txt"
                fs._get_run_file_lock(p)
            with fs._RUN_FILE_LOCKS_GUARD:
                assert len(fs._RUN_FILE_LOCKS) <= fs._MAX_LOCK_ENTRIES
        finally:
            import shutil

            shutil.rmtree(base, ignore_errors=True)
    finally:
        with fs._RUN_FILE_LOCKS_GUARD:
            fs._RUN_FILE_LOCKS.clear()
            fs._RUN_FILE_LOCKS.update(backup)
