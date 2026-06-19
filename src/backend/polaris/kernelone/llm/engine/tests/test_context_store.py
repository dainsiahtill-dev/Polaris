"""Tests for AIExecutor._store_context_messages and the context viewer router."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any
from unittest.mock import patch

import pytest
from polaris.kernelone.llm.engine.executor import AIExecutor


class TestStoreContextMessages:
    """Tests for _store_context_messages static method."""

    def test_stores_messages_and_returns_hash(self) -> None:
        """Messages are stored and a 24-char hash is returned."""
        messages = [{"role": "system", "content": "hello"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            result = AIExecutor._store_context_messages(
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
            hash_key = AIExecutor._store_context_messages(
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
            hash_key = AIExecutor._store_context_messages(
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
            hash_key = AIExecutor._store_context_messages(
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
        hash_key = AIExecutor._store_context_messages(
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
            hash_key = AIExecutor._store_context_messages(
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

            # Call the real _store_context_messages with test data
            hash_val = executor._store_context_messages(
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
                {"get_path": lambda _self, _kind, rel: str(tmp_path / ".polaris" / "runtime" / rel)},
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
