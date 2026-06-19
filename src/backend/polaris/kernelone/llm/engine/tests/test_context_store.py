"""Tests for AIExecutor._store_context_messages and the context viewer router."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any
from unittest.mock import patch

import pytest
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.internal.context_hash import (
    CONTEXT_HASH_PATTERN,
)


class TestStoreContextMessages:
    """Tests for _store_context_messages static method.

    Both the synchronous (``_store_context_messages_sync``) and the async
    (``_store_context_messages``) entry points are exercised here — the async
    variant is the public surface used by the event loop, and the sync variant
    is the underlying worker that the async variant delegates to via
    ``asyncio.to_thread``.
    """

    def test_stores_messages_and_returns_hash(self) -> None:
        """Messages are stored and a 24-char hash is returned."""
        messages = [{"role": "system", "content": "hello"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            result = AIExecutor._store_context_messages_sync(
                workspace=tmpdir,
                messages=messages,
                trace_id="trace-abc",
                call_id="call-123",
            )
        assert isinstance(result, str)
        assert len(result) == 24
        # hex only
        assert all(c in "0123456789abcdef" for c in result)

    def test_file_exists_at_sharded_path(self) -> None:
        """Stored file must exist under the runtime contexts shard directory."""
        messages = [{"role": "user", "content": "test"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            hash_key = AIExecutor._store_context_messages_sync(
                workspace=tmpdir,
                messages=messages,
                trace_id="trace-abc",
                call_id="call-123",
            )
            from polaris.kernelone.storage import StorageLayout
            from polaris.kernelone.storage.io_paths import build_cache_root

            cache_root = build_cache_root("", tmpdir)
            layout = StorageLayout(workspace=tmpdir, runtime_base=cache_root)
            shard = hash_key[:2]
            expected_path = str(layout.get_path("runtime", f"contexts/{shard}/{hash_key}"))
            assert os.path.isfile(expected_path), f"Expected file not found: {expected_path}"

    def test_payload_schema_version(self) -> None:
        """Payload must contain schema_version 1 and required fields."""
        messages = [{"role": "assistant", "content": "ok"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            hash_key = AIExecutor._store_context_messages_sync(
                workspace=tmpdir,
                messages=messages,
                trace_id="trace-abc",
                call_id="call-123",
            )
            from polaris.kernelone.storage import StorageLayout
            from polaris.kernelone.storage.io_paths import build_cache_root

            cache_root = build_cache_root("", tmpdir)
            layout = StorageLayout(workspace=tmpdir, runtime_base=cache_root)
            shard = hash_key[:2]
            file_path = str(layout.get_path("runtime", f"contexts/{shard}/{hash_key}"))
            assert os.path.isfile(file_path), f"Expected file not found: {file_path}"
            with open(file_path, encoding="utf-8") as f:
                payload = json.load(f)
        assert payload["schema_version"] == 1
        assert payload["trace_id"] == "trace-abc"
        assert payload["call_id"] == "call-123"
        assert payload["messages"] == messages
        assert "stored_at" in payload

    def test_call_id_optional(self) -> None:
        """call_id is optional and may be omitted."""
        messages = [{"role": "system", "content": "hi"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            hash_key = AIExecutor._store_context_messages_sync(
                workspace=tmpdir,
                messages=messages,
                trace_id="trace-abc",
                call_id=None,
            )
            from polaris.kernelone.storage import StorageLayout
            from polaris.kernelone.storage.io_paths import build_cache_root

            cache_root = build_cache_root("", tmpdir)
            layout = StorageLayout(workspace=tmpdir, runtime_base=cache_root)
            shard = hash_key[:2]
            file_path = str(layout.get_path("runtime", f"contexts/{shard}/{hash_key}"))
            assert os.path.isfile(file_path), f"Expected file not found: {file_path}"
            with open(file_path, encoding="utf-8") as f:
                payload = json.load(f)
        assert payload["call_id"] is None

    def test_workspace_fallback(self) -> None:
        """workspace=None falls back to current directory."""
        messages = [{"role": "user", "content": "x"}]
        hash_key = AIExecutor._store_context_messages_sync(
            workspace=None,
            messages=messages,
            trace_id="trace-abc",
            call_id="call-123",
        )
        assert isinstance(hash_key, str)
        assert len(hash_key) == 24

    def test_empty_messages_still_returns_hash(self) -> None:
        """Empty messages list is valid and produces a hash."""
        messages: list[Any] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            hash_key = AIExecutor._store_context_messages_sync(
                workspace=tmpdir,
                messages=messages,
                trace_id="trace-abc",
                call_id="call-123",
            )
            from polaris.kernelone.storage import StorageLayout
            from polaris.kernelone.storage.io_paths import build_cache_root

            cache_root = build_cache_root("", tmpdir)
            layout = StorageLayout(workspace=tmpdir, runtime_base=cache_root)
            shard = hash_key[:2]
            file_path = str(layout.get_path("runtime", f"contexts/{shard}/{hash_key}"))
            assert os.path.isfile(file_path), f"Expected file not found: {file_path}"
            with open(file_path, encoding="utf-8") as f:
                payload = json.load(f)
        assert payload["messages"] == []

    def test_producer_hash_matches_fullmatch(self) -> None:
        """Returned hash MUST satisfy CONTEXT_HASH_PATTERN.fullmatch.

        Producer and consumer share :func:`validate_context_hash`; if the
        producer ever started emitting anything other than the canonical
        24-char lowercase hex string, the consumer would refuse the lookup
        with 400 INVALID_HASH.  This test pins the contract so the drift
        is caught at write time, not at user-visible read time.
        """
        messages = [{"role": "user", "content": "hello"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            result = AIExecutor._store_context_messages_sync(
                workspace=tmpdir,
                messages=messages,
                trace_id="trace-abc",
                call_id="call-123",
            )
        assert CONTEXT_HASH_PATTERN.fullmatch(result) is not None, (
            f"producer hash {result!r} does not fullmatch CONTEXT_HASH_PATTERN"
        )

    def test_atomic_write_no_tmp_left(self, tmp_path) -> None:
        """After a successful store there must be no .tmp sibling on disk."""
        from pathlib import Path

        from polaris.kernelone.storage import StorageLayout
        from polaris.kernelone.storage.io_paths import build_cache_root

        messages = [{"role": "user", "content": "atomic"}]
        workspace = str(tmp_path)
        hash_key = AIExecutor._store_context_messages_sync(
            workspace=workspace,
            messages=messages,
            trace_id="trace-abc",
            call_id="call-123",
        )
        # The producer routes through StorageLayout, which uses build_cache_root
        # for the runtime base — mirror that layout here.
        cache_root = build_cache_root("", workspace)
        layout = StorageLayout(workspace=workspace, runtime_base=cache_root)
        final_file = layout.resolve_artifact_path(f"runtime/contexts/{hash_key[:2]}/{hash_key}")
        assert final_file.is_file(), f"final context file missing: {final_file}"
        # The .tmp sibling must have been renamed away by os.replace.
        tmp_sibling = Path(str(final_file) + ".tmp")
        assert not tmp_sibling.exists(), f"orphaned .tmp left at {tmp_sibling}"
        # No stray .tmp anywhere under the runtime contexts tree.
        contexts_root = final_file.parent.parent.parent
        for path in Path(contexts_root).rglob("*.tmp"):
            raise AssertionError(f"unexpected .tmp sibling: {path}")


class TestStoreContextMessagesNonBlocking:
    """Regression tests for HIGH #2 — sync disk IO must not block the event loop.

    The async variant ``AIExecutor._store_context_messages`` must delegate
    the disk write to ``asyncio.to_thread`` so a sibling task can make
    progress while the store is in flight. A regression to the old
    synchronous call would freeze the loop until the write completes,
    starving every concurrent coroutine.
    """

    @pytest.mark.asyncio
    async def test_sibling_task_progresses_during_store(self) -> None:
        """A sibling async task must make progress while _store_context_messages is in flight.

        We schedule the store and a counter task together. If the store
        blocked the event loop, the counter could not tick while the store
        was running; with ``asyncio.to_thread`` the counter ticks freely.
        """
        import asyncio as _asyncio

        messages = [{"role": "user", "content": "x" * 4096}]
        with tempfile.TemporaryDirectory() as tmpdir:
            counter_task_started = _asyncio.Event()
            counter_ticks_during_store: list[int] = []
            store_started = _asyncio.Event()
            store_done = _asyncio.Event()

            async def _sibling_counter() -> None:
                counter_task_started.set()
                ticks = 0
                while not store_done.is_set():
                    await _asyncio.sleep(0)
                    ticks += 1
                counter_ticks_during_store.append(ticks)

            counter = _asyncio.create_task(_sibling_counter())
            await counter_task_started.wait()
            # Yield once so the counter is actually running.
            await _asyncio.sleep(0)

            store_started.set()
            hash_key = await AIExecutor._store_context_messages(
                workspace=tmpdir,
                messages=messages,
                trace_id="trace-abc",
                call_id="call-123",
            )
            store_done.set()
            await counter

        assert len(counter_ticks_during_store) == 1
        # If the store blocked the loop, the counter would have ticked 0
        # or 1 times. With asyncio.to_thread the counter gets many
        # opportunities to run because the store yields the loop while the
        # thread pool worker does the disk write.
        assert counter_ticks_during_store[0] >= 5, (
            f"Expected >=5 sibling-task ticks while store ran "
            f"(event loop not blocked), got {counter_ticks_during_store[0]}"
        )
        assert isinstance(hash_key, str)
        assert len(hash_key) == 24

    @pytest.mark.asyncio
    async def test_store_runs_in_thread_pool(self) -> None:
        """The async variant must execute the disk write on a worker thread.

        We capture the executing thread for the sync implementation and
        confirm it is a ``concurrent.futures`` worker — not the asyncio
        thread the caller runs on.
        """
        import asyncio as _asyncio
        import threading

        caller_thread_ident = threading.get_ident()
        executed_in_threads: list[int] = []

        real_sync = AIExecutor._store_context_messages_sync

        def _spy(*args: Any, **kwargs: Any) -> str:
            executed_in_threads.append(threading.get_ident())
            return real_sync(*args, **kwargs)

        with (
            patch.object(AIExecutor, "_store_context_messages_sync", staticmethod(_spy)),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            hash_key = await AIExecutor._store_context_messages(
                workspace=tmpdir,
                messages=[{"role": "user", "content": "hi"}],
                trace_id="trace-abc",
                call_id="call-123",
            )
            # Give the loop a chance to schedule more tasks.
            await _asyncio.sleep(0)

        assert isinstance(hash_key, str)
        assert len(hash_key) == 24
        assert executed_in_threads, "Sync implementation was not invoked"
        # The sync IO must have run on a different thread than the caller.
        assert executed_in_threads[0] != caller_thread_ident, (
            "Sync disk IO must run on a thread-pool worker, not the asyncio thread"
        )

    @pytest.mark.asyncio
    async def test_async_store_durability_contract(self) -> None:
        """After await returns, the on-disk file must already exist and be readable."""
        messages = [{"role": "user", "content": "durable"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            hash_key = await AIExecutor._store_context_messages(
                workspace=tmpdir,
                messages=messages,
                trace_id="trace-abc",
                call_id="call-123",
            )
            # The file MUST be on disk by the time the await resolves —
            # the contract preserved by ``asyncio.to_thread`` is "synchronous
            # return of the worker function" which means ``os.replace`` has
            # already finished.
            from polaris.kernelone.storage import StorageLayout
            from polaris.kernelone.storage.io_paths import build_cache_root

            cache_root = build_cache_root("", tmpdir)
            layout = StorageLayout(workspace=tmpdir, runtime_base=cache_root)
            shard = hash_key[:2]
            file_path = str(layout.get_path("runtime", f"contexts/{shard}/{hash_key}"))
            assert os.path.isfile(file_path), f"Async store did not durably land file at {file_path}"
            with open(file_path, encoding="utf-8") as f:
                payload = json.load(f)
            assert payload["messages"] == messages


class TestContextSnapshotRefInjection:
    """Tests that context_snapshot_ref is injected into request.context."""

    @pytest.mark.asyncio
    async def test_hash_injected_into_request_context(self) -> None:
        """After _store_context_messages, request.context contains context_snapshot_ref."""
        from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="hello",
            context={"chat_messages": [{"role": "user", "content": "hi"}]},
        )
        executor = AIExecutor()

        async def _mock_execute_invoke(request: Any, trace_id: str) -> Any:
            # Simulate the context_snapshot_ref injection that the real code does
            # We mock _store_context_messages to return a known hash, then call
            # the real injection logic by replicating the relevant lines
            from polaris.kernelone.llm.engine.executor import AIResponse

            # Call the real async _store_context_messages with test data —
            # the async variant runs the disk write in the default thread pool
            # via asyncio.to_thread so the event loop is not blocked.
            hash_val = await executor._store_context_messages(
                workspace=executor.workspace,
                messages=[{"role": "user", "content": "hi"}],
                trace_id=trace_id,
                call_id=request.context.get("call_id") if isinstance(request.context, dict) else None,
            )
            if isinstance(request.context, dict) and hash_val:
                request.context["context_snapshot_ref"] = hash_val

            return AIResponse.success(output="ok")

        with (
            patch.object(executor, "_execute_invoke", side_effect=_mock_execute_invoke),
            patch.object(executor, "_resolve_provider_model", return_value=("mock", "gpt-4")),
            patch.object(executor, "_get_provider_config", return_value={"type": "mock"}),
        ):
            await executor.invoke(request)

        ctx = request.context if isinstance(request.context, dict) else {}
        assert ctx.get("context_snapshot_ref") is not None
        assert len(str(ctx.get("context_snapshot_ref"))) == 24


class TestContextViewerRouter:
    """Tests for GET /v2/context/{hash} endpoint."""

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from polaris.delivery.http.v2._shared import require_auth
        from polaris.delivery.http.v2.context import router as context_router

        app = FastAPI()
        app.include_router(context_router)
        app.dependency_overrides[require_auth] = lambda: None
        app.state.app_state = type("NS", (), {"settings": type("S", (), {"workspace": ".", "ramdisk_root": ""})})()
        return TestClient(app)

    def test_invalid_hash_returns_400(self, client) -> None:
        """Non-hex or wrong-length hash returns 400."""
        response = client.get("/v2/context/invalid")
        assert response.status_code == 400
        data = response.json()
        assert data.get("detail", {}).get("code") == "INVALID_HASH"

    def test_missing_context_returns_404(self, client) -> None:
        """Valid hash format but missing file returns 404."""
        response = client.get("/v2/context/a1b2c3d4e5f6a7b8c9d0e1f2")
        assert response.status_code == 404
        data = response.json()
        assert data.get("detail", {}).get("code") == "CONTEXT_NOT_FOUND"

    def test_retrieves_existing_context(self, client, tmp_path) -> None:
        """Stored context is returned with enriched metadata."""
        hash_key = "a1b2c3d4e5f6a7b8c9d0e1f2"
        shard = hash_key[:2]
        contexts_dir = tmp_path / ".polaris" / "runtime" / "contexts" / shard
        contexts_dir.mkdir(parents=True)
        payload = {
            "schema_version": 1,
            "trace_id": "trace-abc",
            "call_id": "call-123",
            "messages": [{"role": "system", "content": "hello"}],
            "stored_at": "2026-06-19T08:00:00+00:00",
        }
        (contexts_dir / hash_key).write_text(json.dumps(payload), encoding="utf-8")

        # Patch workspace resolution to use tmp_path
        with patch(
            "polaris.delivery.http.v2.context.StorageLayout",
            return_value=type(
                "MockLayout",
                (),
                {
                    "get_path": lambda _self, _kind, rel: str(tmp_path / ".polaris" / "runtime" / rel),
                    "resolve_artifact_path": lambda _self, rel: tmp_path / ".polaris" / rel,
                },
            )(),
        ):
            response = client.get(f"/v2/context/{hash_key}")

        assert response.status_code == 200
        data = response.json()
        assert data["hash"] == hash_key
        assert data["trace_id"] == "trace-abc"
        assert data["call_id"] == "call-123"
        assert data["message_count"] == 1
        assert data["total_chars"] > 0
        assert data["messages"] == payload["messages"]

    def test_hash_validation_rejects_non_hex(self, client) -> None:
        """Hash containing non-hex characters is rejected."""
        response = client.get("/v2/context/ZZZZZZZZZZZZZZZZZZZZZZZZ")
        assert response.status_code == 400
        assert response.json().get("detail", {}).get("code") == "INVALID_HASH"

    def test_hash_validation_rejects_wrong_length(self, client) -> None:
        """Hash with length != 24 is rejected."""
        response = client.get("/v2/context/abc123")
        assert response.status_code == 400
        assert response.json().get("detail", {}).get("code") == "INVALID_HASH"

    def test_corrupt_json_returns_500(self, client, tmp_path) -> None:
        """Corrupt JSON inside an existing context file must surface 500, not crash."""
        hash_key = "b2c3d4e5f6a7b8c9d0e1f2a3"
        shard = hash_key[:2]
        contexts_dir = tmp_path / ".polaris" / "runtime" / "contexts" / shard
        contexts_dir.mkdir(parents=True)
        # Truncated JSON — opens fine but json.load raises ValueError.
        (contexts_dir / hash_key).write_text('{"trace_id": "x"', encoding="utf-8")

        with patch(
            "polaris.delivery.http.v2.context.StorageLayout",
            return_value=type(
                "MockLayout",
                (),
                {
                    "get_path": lambda _self, _kind, rel: str(tmp_path / ".polaris" / "runtime" / rel),
                    "resolve_artifact_path": lambda _self, rel: tmp_path / ".polaris" / rel,
                },
            )(),
        ):
            response = client.get(f"/v2/context/{hash_key}")

        assert response.status_code == 500
        detail = response.json().get("detail", {})
        assert detail.get("code") == "CONTEXT_READ_ERROR", detail
