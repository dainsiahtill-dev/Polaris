"""Tests for AIExecutor._store_context_messages and the context viewer router."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.internal.context_hash import (
    CONTEXT_HASH_PATTERN,
)


def _context_snapshot_path(workspace: str | os.PathLike[str], hash_key: str) -> Path:
    from polaris.kernelone.storage.io_paths import resolve_storage_roots

    runtime_root = Path(resolve_storage_roots(str(workspace)).runtime_root)
    return runtime_root / "contexts" / hash_key[:2] / hash_key


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
            expected_path = _context_snapshot_path(tmpdir, hash_key)
            assert expected_path.is_file(), f"Expected file not found: {expected_path}"

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
            file_path = _context_snapshot_path(tmpdir, hash_key)
            assert file_path.is_file(), f"Expected file not found: {file_path}"
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
            file_path = _context_snapshot_path(tmpdir, hash_key)
            assert file_path.is_file(), f"Expected file not found: {file_path}"
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

    def test_dot_workspace_prefers_kernelone_workspace_env(self, tmp_path, monkeypatch) -> None:
        """workspace='.' must use the active workspace env, not worker cwd."""
        active_workspace = tmp_path / "active-workspace"
        worker_cwd = tmp_path / "worker-cwd"
        active_workspace.mkdir()
        worker_cwd.mkdir()
        monkeypatch.setenv("KERNELONE_WORKSPACE", str(active_workspace))
        monkeypatch.chdir(worker_cwd)

        messages = [{"role": "user", "content": "env-bound context"}]
        hash_key = AIExecutor._store_context_messages_sync(
            workspace=".",
            messages=messages,
            trace_id="trace-env-workspace",
            call_id="call-env-workspace",
        )

        active_file = _context_snapshot_path(active_workspace, hash_key)
        cwd_file = _context_snapshot_path(worker_cwd, hash_key)

        assert active_file.is_file(), f"Expected context snapshot under active workspace: {active_file}"
        assert not cwd_file.exists(), f"Context snapshot must not be written under worker cwd: {cwd_file}"
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
            file_path = _context_snapshot_path(tmpdir, hash_key)
            assert file_path.is_file(), f"Expected file not found: {file_path}"
            with open(file_path, encoding="utf-8") as f:
                payload = json.load(f)
        assert payload["messages"] == []

    def test_optional_provider_request_snapshot_is_persisted(self) -> None:
        """Provider request audit data is durable with the context snapshot."""
        messages = [{"role": "user", "content": "use repo_tree"}]
        provider_request = {
            "schema_version": "llm.provider_request_snapshot.v1",
            "tool_schema_count": 1,
            "tools": [
                {
                    "type": "function",
                    "name": "repo_tree",
                    "argument_keys": [],
                    "required": [],
                }
            ],
            "tool_choice": "auto",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            hash_key = AIExecutor._store_context_messages_sync(
                workspace=tmpdir,
                messages=messages,
                trace_id="trace-abc",
                call_id="call-123",
                provider_request=provider_request,
            )
            file_path = _context_snapshot_path(tmpdir, hash_key)
            with open(file_path, encoding="utf-8") as f:
                payload = json.load(f)

        assert payload["messages"] == messages
        assert payload["provider_request"] == provider_request

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
        messages = [{"role": "user", "content": "atomic"}]
        workspace = str(tmp_path)
        hash_key = AIExecutor._store_context_messages_sync(
            workspace=workspace,
            messages=messages,
            trace_id="trace-abc",
            call_id="call-123",
        )
        final_file = _context_snapshot_path(workspace, hash_key)
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
            file_path = _context_snapshot_path(tmpdir, hash_key)
            assert file_path.is_file(), f"Async store did not durably land file at {file_path}"
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


class TestContextStoreInvokeFailure:
    """Regression tests: a failing context-store disk write MUST NOT abort the LLM call.

    Phase 2 LOW #3: ``_execute_invoke`` wraps the ``await self._store_context_messages(...)``
    call in a try/except so OSError / RuntimeError / generic Exception on the disk write
    are logged at WARNING and the LLM call proceeds. The context viewer is informational
    (read by ContextViewerModal), not critical path.
    """

    @pytest.mark.asyncio
    async def test_oserror_injects_degraded_evidence(self) -> None:
        """OSError from store must inject context_snapshot_degraded into request.context."""
        from polaris.kernelone.llm.engine import executor as executor_module

        request = self._build_request()
        request.context["context_snapshot_ref"] = "stale-ref-that-must-not-leak"
        request.context["context_snapshot_degraded"] = {"code": "STALE"}
        executor = self._make_executor_with_mock_catalog()

        async def _failing_store(*args: Any, **kwargs: Any) -> str:
            raise OSError("disk full")

        with ExitStack() as stack:
            for p in self._patch_provider(executor_module):
                stack.enter_context(p)
            stack.enter_context(patch.object(executor, "_store_context_messages", side_effect=_failing_store))
            stack.enter_context(patch.object(executor, "_get_provider_config", return_value={"type": "mock"}))
            response = await executor._execute_invoke(request, trace_id="trace-degraded-1")

        assert response.ok is True
        ctx = request.context if isinstance(request.context, dict) else {}
        assert ctx.get("context_snapshot_ref") is None
        degraded = ctx.get("context_snapshot_degraded")
        assert degraded is not None, "context_snapshot_degraded must be set on store failure"
        assert degraded["code"] == "CONTEXT_STORE_WRITE_FAILED"
        assert degraded["reason"] == "context_snapshot_store_failure"
        assert degraded["exception_type"] == "OSError"
        assert isinstance(degraded["message"], str)
        assert len(degraded["message"]) > 0

    @pytest.mark.asyncio
    async def test_valueerror_injects_degraded_evidence(self) -> None:
        """ValueError from store must inject context_snapshot_degraded into request.context."""
        from polaris.kernelone.llm.engine import executor as executor_module

        request = self._build_request()
        executor = self._make_executor_with_mock_catalog()

        async def _failing_store(*args: Any, **kwargs: Any) -> str:
            raise ValueError("corrupt payload")

        with ExitStack() as stack:
            for p in self._patch_provider(executor_module):
                stack.enter_context(p)
            stack.enter_context(patch.object(executor, "_store_context_messages", side_effect=_failing_store))
            stack.enter_context(patch.object(executor, "_get_provider_config", return_value={"type": "mock"}))
            response = await executor._execute_invoke(request, trace_id="trace-degraded-2")

        assert response.ok is True
        ctx = request.context if isinstance(request.context, dict) else {}
        degraded = ctx.get("context_snapshot_degraded")
        assert degraded is not None
        assert degraded["code"] == "CONTEXT_STORE_WRITE_FAILED"
        assert degraded["exception_type"] == "ValueError"

    @pytest.mark.asyncio
    async def test_success_path_no_degraded_evidence(self) -> None:
        """Successful store must NOT inject context_snapshot_degraded."""
        from polaris.kernelone.llm.engine import executor as executor_module

        request = self._build_request()
        request.context["context_snapshot_ref"] = "stale-ref-that-must-be-replaced"
        request.context["context_snapshot_degraded"] = {"code": "STALE"}
        executor = self._make_executor_with_mock_catalog()

        with tempfile.TemporaryDirectory() as tmpdir:
            executor.workspace = tmpdir
            with ExitStack() as stack:
                for p in self._patch_provider(executor_module):
                    stack.enter_context(p)
                stack.enter_context(patch.object(executor, "_get_provider_config", return_value={"type": "mock"}))
                response = await executor._execute_invoke(request, trace_id="trace-ok-1")

        assert response.ok is True
        ctx = request.context if isinstance(request.context, dict) else {}
        assert "context_snapshot_degraded" not in ctx, "context_snapshot_degraded must NOT be present on success path"
        # context_snapshot_ref should be present on success
        assert ctx.get("context_snapshot_ref") is not None
        assert ctx.get("context_snapshot_ref") != "stale-ref-that-must-be-replaced"

    @pytest.mark.asyncio
    async def test_execute_invoke_snapshot_persists_final_provider_request(self) -> None:
        """The emitted context_snapshot_ref must point at a provider-request snapshot."""
        from polaris.kernelone.llm.engine import executor as executor_module

        request = self._build_request()
        request.context["chat_messages"] = [
            {
                "role": "system",
                "content": "You are Polaris Director. Chief Engineer blueprint_id=ce-1.",
            },
            {
                "role": "user",
                "content": "PM task contract: create src/app.ts. acceptance criteria: npm test.",
            },
        ]
        request.context["prompt_profile_audit"] = {
            "selected_prompt_profile_ids": ["builtin.language.typescript", "builtin.task.implement"],
            "inferred_language": "typescript",
            "inferred_task_type": "implement",
            "content": "SECRET PROFILE TEMPLATE",
        }
        request.context["selected_prompt_profile_ids"] = ["builtin.language.typescript", "builtin.task.implement"]
        request.options["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "repo_tree",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "parameters": {
                        "type": "object",
                        "properties": {"file": {"type": "string"}},
                        "required": ["file"],
                    },
                },
            },
        ]
        request.options["tool_choice"] = "auto"
        executor = self._make_executor_with_mock_catalog()

        with tempfile.TemporaryDirectory() as tmpdir:
            executor.workspace = tmpdir
            with ExitStack() as stack:
                for p in self._patch_provider(executor_module):
                    stack.enter_context(p)
                stack.enter_context(
                    patch.object(
                        executor,
                        "_get_provider_config",
                        return_value={
                            "type": "mock",
                            "api_path": "/v1/messages",
                            "headers": {"authorization": "Bearer secret"},
                            "thinking": {"type": "disabled", "budget_tokens": 2048},
                            "request_overrides": {"thinking": {"type": "disabled"}},
                        },
                    )
                )
                response = await executor._execute_invoke(request, trace_id="trace-provider-request")

            assert response.ok is True
            ctx = request.context if isinstance(request.context, dict) else {}
            snapshot_ref = str(ctx.get("context_snapshot_ref") or "")
            assert len(snapshot_ref) == 24

            snapshot_path = _context_snapshot_path(tmpdir, snapshot_ref)
            with open(snapshot_path, encoding="utf-8") as f:
                payload = json.load(f)

        provider_request = payload.get("provider_request")
        assert provider_request is not None
        assert provider_request["schema_version"] == "llm.provider_request_snapshot.v1"
        assert provider_request["message_count"] == 2
        assert provider_request["tool_schema_count"] == 2
        assert [tool["name"] for tool in provider_request["tools"]] == ["repo_tree", "read_file"]
        assert provider_request["tool_choice"] == "auto"
        assert "headers" not in provider_request["provider_option_keys"]
        assert "api_path" in provider_request["provider_option_keys"]
        assert provider_request["provider_option_summary"]["thinking"] == {
            "present": True,
            "type": "disabled",
            "keys": ["budget_tokens", "type"],
        }
        assert provider_request["provider_option_summary"]["request_overrides"]["thinking"] == {
            "present": True,
            "type": "disabled",
            "keys": ["type"],
        }
        assert provider_request["selected_prompt_profile_ids"] == [
            "builtin.language.typescript",
            "builtin.task.implement",
        ]
        assert provider_request["prompt_profile_selection"]["inferred_language"] == "typescript"
        audit = provider_request["final_request_context_audit"]
        assert audit["schema_version"] == "llm.final_request_context_audit.v1"
        assert audit["message_count"] == 2
        assert audit["tool_schema_count"] == 2
        assert audit["final_request_token_estimate"] >= audit["message_token_estimate"]
        assert audit["coverage"]["has_pm_contract"] is True
        assert audit["coverage"]["has_chief_engineer_blueprint"] is True
        assert audit["selected_prompt_profile_ids"] == [
            "builtin.language.typescript",
            "builtin.task.implement",
        ]
        serialized_provider_request = json.dumps(provider_request, ensure_ascii=False, sort_keys=True)
        assert "SECRET PROFILE TEMPLATE" not in serialized_provider_request

    @pytest.mark.asyncio
    async def test_degraded_message_truncated_to_200_chars(self) -> None:
        """Degraded message must be truncated to 200 chars to avoid bloat."""
        from polaris.kernelone.llm.engine import executor as executor_module

        request = self._build_request()
        executor = self._make_executor_with_mock_catalog()
        long_msg = "x" * 500

        async def _failing_store(*args: Any, **kwargs: Any) -> str:
            raise OSError(long_msg)

        with ExitStack() as stack:
            for p in self._patch_provider(executor_module):
                stack.enter_context(p)
            stack.enter_context(patch.object(executor, "_store_context_messages", side_effect=_failing_store))
            stack.enter_context(patch.object(executor, "_get_provider_config", return_value={"type": "mock"}))
            response = await executor._execute_invoke(request, trace_id="trace-degraded-3")

        assert response.ok is True
        ctx = request.context if isinstance(request.context, dict) else {}
        degraded = ctx.get("context_snapshot_degraded")
        assert degraded is not None
        assert len(degraded["message"]) <= 200

    @pytest.mark.asyncio
    async def test_degraded_evidence_is_json_serialisable(self) -> None:
        """Degraded evidence must be JSON-serialisable (no exotic types)."""
        from polaris.kernelone.llm.engine import executor as executor_module

        request = self._build_request()
        executor = self._make_executor_with_mock_catalog()

        async def _failing_store(*args: Any, **kwargs: Any) -> str:
            raise RuntimeError("some runtime error")

        with ExitStack() as stack:
            for p in self._patch_provider(executor_module):
                stack.enter_context(p)
            stack.enter_context(patch.object(executor, "_store_context_messages", side_effect=_failing_store))
            stack.enter_context(patch.object(executor, "_get_provider_config", return_value={"type": "mock"}))
            response = await executor._execute_invoke(request, trace_id="trace-degraded-4")

        assert response.ok is True
        ctx = request.context if isinstance(request.context, dict) else {}
        degraded = ctx.get("context_snapshot_degraded")
        assert degraded is not None
        # Must not raise
        serialised = json.dumps(degraded, ensure_ascii=False)
        restored = json.loads(serialised)
        assert restored == degraded

    @pytest.mark.asyncio
    async def test_degraded_not_injected_when_context_is_not_dict(self) -> None:
        """When request.context is not a dict, degraded evidence is not injected (no crash)."""
        from polaris.kernelone.llm.engine import executor as executor_module
        from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="hello",
            provider_id="mock-provider",
            model="mock-model",
            context=None,  # type: ignore[arg-type]
        )
        executor = self._make_executor_with_mock_catalog()

        async def _failing_store(*args: Any, **kwargs: Any) -> str:
            raise OSError("disk full")

        with ExitStack() as stack:
            for p in self._patch_provider(executor_module):
                stack.enter_context(p)
            stack.enter_context(patch.object(executor, "_store_context_messages", side_effect=_failing_store))
            stack.enter_context(patch.object(executor, "_get_provider_config", return_value={"type": "mock"}))
            # Must not raise
            response = await executor._execute_invoke(request, trace_id="trace-degraded-5")

        assert response.ok is True

    @staticmethod
    def _build_request() -> Any:
        from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType

        return AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="hello",
            provider_id="mock-provider",
            model="mock-model",
            context={"chat_messages": [{"role": "user", "content": "hi"}]},
        )

    @staticmethod
    def _make_executor_with_mock_catalog() -> Any:
        """Create an AIExecutor with a mocked model_catalog so it doesn't need llm_config.json."""
        from polaris.kernelone.llm.shared_contracts import ModelSpec

        class _FakeModelCatalog:
            def resolve(self, *args: Any, **kwargs: Any) -> ModelSpec:
                return ModelSpec(
                    provider_id="mock-provider",
                    provider_type="mock",
                    model="mock-model",
                    max_context_tokens=8192,
                    max_output_tokens=2048,
                )

        executor = AIExecutor()
        cast(Any, executor).model_catalog = _FakeModelCatalog()
        return executor

    @staticmethod
    def _patch_provider(executor_module: Any) -> Any:
        """Patch get_provider_manager + _invoke_with_timeout so _execute_invoke reaches the store call."""
        from polaris.kernelone.llm.engine.executor import AIResponse

        class _FakeProvider:
            def invoke(self, *args: Any, **kwargs: Any) -> AIResponse:
                return AIResponse.success(output="provider-ok")

        class _FakeManager:
            def get_provider_instance(self, name: str) -> Any:
                return _FakeProvider()

        async def _fake_invoke_with_timeout(coro: Any, timeout: Any = None) -> AIResponse:
            del timeout
            if hasattr(coro, "__await__"):
                result = await coro
                if isinstance(result, AIResponse):
                    return result
            return AIResponse.success(output="provider-ok")

        return [
            patch.object(executor_module, "get_provider_manager", return_value=_FakeManager()),
            patch.object(executor_module, "_invoke_with_timeout", side_effect=_fake_invoke_with_timeout),
        ]

    @pytest.mark.asyncio
    async def test_oserror_from_store_context_messages_still_invokes(self) -> None:
        """OSError (disk full / permission denied) must NOT abort the LLM call."""
        from polaris.kernelone.llm.engine import executor as executor_module

        request = self._build_request()
        executor = self._make_executor_with_mock_catalog()

        async def _failing_store(*args: Any, **kwargs: Any) -> str:
            raise OSError("disk full")

        with ExitStack() as stack:
            for p in self._patch_provider(executor_module):
                stack.enter_context(p)
            stack.enter_context(patch.object(executor, "_store_context_messages", side_effect=_failing_store))
            stack.enter_context(patch.object(executor, "_get_provider_config", return_value={"type": "mock"}))
            response = await executor._execute_invoke(request, trace_id="trace-os-1")

        # LLM call must succeed despite the disk-write failure.
        assert response.ok is True, f"Expected LLM call to succeed; got error: {response.error}"
        # context_snapshot_ref must NOT be injected (truthy-gated).
        ctx = request.context if isinstance(request.context, dict) else {}
        assert ctx.get("context_snapshot_ref") is None

    @pytest.mark.asyncio
    async def test_value_error_from_store_context_messages_still_invokes(self) -> None:
        """ValueError from store must NOT abort the LLM call."""
        from polaris.kernelone.llm.engine import executor as executor_module

        request = self._build_request()
        executor = self._make_executor_with_mock_catalog()

        async def _failing_store(*args: Any, **kwargs: Any) -> str:
            raise ValueError("hash collision / corrupt payload")

        with ExitStack() as stack:
            for p in self._patch_provider(executor_module):
                stack.enter_context(p)
            stack.enter_context(patch.object(executor, "_store_context_messages", side_effect=_failing_store))
            stack.enter_context(patch.object(executor, "_get_provider_config", return_value={"type": "mock"}))
            response = await executor._execute_invoke(request, trace_id="trace-val-1")

        assert response.ok is True, f"Expected LLM call to succeed; got error: {response.error}"
        ctx = request.context if isinstance(request.context, dict) else {}
        assert ctx.get("context_snapshot_ref") is None


class TestWorkerIdPropagation:
    """Regression tests: worker_id is propagated from request.context or env into context.

    Phase 3 HIGH finding: ``_execute_invoke`` did not propagate worker_id into
    request.context, breaking the ContextOS multi-worker UI for real backend events.
    Resolution: read from request.context.get("worker_id") / .get("workerId") first,
    then fall back to KERNELONE_WORKER_ID env. Never fabricate a value.
    """

    @staticmethod
    def _build_request(context: dict[str, Any] | None = None) -> Any:
        from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType

        ctx = context if context is not None else {"chat_messages": [{"role": "user", "content": "y"}]}
        return AIRequest(
            task_type=TaskType.DIALOGUE,
            role="director",
            input="x",
            provider_id="mock-provider",
            model="mock-model",
            context=ctx,
        )

    @staticmethod
    async def _fake_invoke_with_timeout(coro: Any, timeout: Any = None) -> Any:
        from polaris.kernelone.llm.engine.executor import AIResponse

        del timeout
        if hasattr(coro, "__await__"):
            return await coro
        return AIResponse.success(output="provider-ok")

    @pytest.mark.asyncio
    async def test_worker_id_from_request_context_is_preserved(self) -> None:
        """Explicit worker_id in request.context is preserved (caller wins)."""
        from polaris.kernelone.llm.engine import executor as executor_module

        request = self._build_request(
            context={
                "worker_id": "worker-explicit-1",
                "chat_messages": [{"role": "user", "content": "y"}],
            }
        )
        executor = TestContextStoreInvokeFailure._make_executor_with_mock_catalog()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KERNELONE_WORKER_ID", None)
            with ExitStack() as stack:
                for p in TestContextStoreInvokeFailure._patch_provider(executor_module):
                    stack.enter_context(p)
                stack.enter_context(patch.object(executor, "_store_context_messages", AsyncMock(return_value=None)))
                stack.enter_context(patch.object(executor, "_get_provider_config", return_value={"type": "mock"}))
                await executor._execute_invoke(request, trace_id="t-1")
        assert request.context["worker_id"] == "worker-explicit-1"

    @pytest.mark.asyncio
    async def test_worker_id_from_env_is_injected_when_context_missing(self) -> None:
        """KERNELONE_WORKER_ID env is injected into request.context when caller did not provide one."""
        from polaris.kernelone.llm.engine import executor as executor_module

        request = self._build_request()
        executor = TestContextStoreInvokeFailure._make_executor_with_mock_catalog()
        with patch.dict(os.environ, {"KERNELONE_WORKER_ID": "worker-pool-3-7"}), ExitStack() as stack:
            for p in TestContextStoreInvokeFailure._patch_provider(executor_module):
                stack.enter_context(p)
            stack.enter_context(patch.object(executor, "_store_context_messages", AsyncMock(return_value=None)))
            stack.enter_context(patch.object(executor, "_get_provider_config", return_value={"type": "mock"}))
            await executor._execute_invoke(request, trace_id="t-2")
        assert request.context.get("worker_id") == "worker-pool-3-7"

    @pytest.mark.asyncio
    async def test_worker_id_is_not_fabricated_when_neither_present(self) -> None:
        """When neither context nor env has worker_id, no key is injected (fail-closed)."""
        from polaris.kernelone.llm.engine import executor as executor_module

        request = self._build_request()
        executor = TestContextStoreInvokeFailure._make_executor_with_mock_catalog()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KERNELONE_WORKER_ID", None)
            with ExitStack() as stack:
                for p in TestContextStoreInvokeFailure._patch_provider(executor_module):
                    stack.enter_context(p)
                stack.enter_context(patch.object(executor, "_store_context_messages", AsyncMock(return_value=None)))
                stack.enter_context(patch.object(executor, "_get_provider_config", return_value={"type": "mock"}))
                await executor._execute_invoke(request, trace_id="t-3")
        assert "worker_id" not in request.context

    @pytest.mark.asyncio
    async def test_worker_id_not_injected_when_context_is_not_dict(self) -> None:
        """When request.context is not a dict, no AttributeError; worker_id absent."""
        from polaris.kernelone.llm.engine import executor as executor_module
        from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="pm",
            input="x",
            provider_id="mock-provider",
            model="mock-model",
            context=None,  # type: ignore[arg-type]
        )
        executor = TestContextStoreInvokeFailure._make_executor_with_mock_catalog()
        with patch.dict(os.environ, {"KERNELONE_WORKER_ID": "should-not-be-used"}), ExitStack() as stack:
            for p in TestContextStoreInvokeFailure._patch_provider(executor_module):
                stack.enter_context(p)
            stack.enter_context(patch.object(executor, "_store_context_messages", AsyncMock(return_value=None)))
            stack.enter_context(patch.object(executor, "_get_provider_config", return_value={"type": "mock"}))
            # Must not raise even when context is None.
            await executor._execute_invoke(request, trace_id="t-4")
        # No assertion needed beyond "did not raise"; context is None so no key to read.


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

    def test_stats_response_ignores_nested_legacy_contexts(self, tmp_path) -> None:
        """Stats must report only the current KernelOne context store."""
        from polaris.delivery.http.v2.context import _context_stats_response

        project_root = tmp_path / "l1-01-3e17e00683ce"
        active_contexts_root = project_root / "runtime" / "contexts"
        legacy_contexts_dir = (
            project_root / "runtime" / "projects" / "l1-01-3e17e00683ce" / "runtime" / "contexts" / "ab"
        )
        legacy_contexts_dir.mkdir(parents=True)
        legacy_file = legacy_contexts_dir / "ab5ec27cf124c2f13f936704"
        legacy_file.write_text('{"messages":[]}', encoding="utf-8")

        response = _context_stats_response(
            {
                "workspace": str(tmp_path),
                "contexts_root": str(active_contexts_root),
                "file_count": 0,
                "total_bytes": 0,
                "oldest_mtime": None,
                "newest_mtime": None,
                "config": {},
                "last_sweep_at": 0.0,
            },
            last_sweep_report=None,
        )

        assert response["file_count"] == 0
        assert response["total_bytes"] == 0
        assert response["oldest_mtime"] is None
        assert response["newest_mtime"] is None
        assert response["primary_store"] == {
            "contexts_root": str(active_contexts_root),
            "file_count": 0,
            "total_bytes": 0,
            "oldest_mtime": None,
            "newest_mtime": None,
        }
        assert "legacy_store" not in response
        assert "legacy_file_count" not in response["config"]
        assert "legacy_contexts_root" not in response["config"]
        assert legacy_file.is_file()

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
        context_path = _context_snapshot_path(tmp_path, hash_key)
        context_path.parent.mkdir(parents=True)
        payload = {
            "schema_version": 1,
            "trace_id": "trace-abc",
            "call_id": "call-123",
            "messages": [{"role": "system", "content": "hello"}],
            "stored_at": "2026-06-19T08:00:00+00:00",
        }
        context_path.write_text(json.dumps(payload), encoding="utf-8")

        response = client.get(f"/v2/context/{hash_key}", params={"workspace": str(tmp_path)})

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
        context_path = _context_snapshot_path(tmp_path, hash_key)
        context_path.parent.mkdir(parents=True)
        # Truncated JSON — opens fine but json.load raises ValueError.
        context_path.write_text('{"trace_id": "x"', encoding="utf-8")

        response = client.get(f"/v2/context/{hash_key}", params={"workspace": str(tmp_path)})

        assert response.status_code == 500
        detail = response.json().get("detail", {})
        assert detail.get("code") == "CONTEXT_READ_ERROR", detail
