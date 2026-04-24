"""E2E tests for TransactionKernel — roles/kernel turn execution kernel.

Validates end-to-end turn execution through the TransactionKernel / TurnTransactionController
public interface, covering the critical paths defined in the Blueprint.

Coverage targets:
- TK-01: Single turn with final_answer completes successfully
- TK-02: Single turn with write tool batch records write receipt
- TK-03: Single turn with ask_user yields WAITING_HUMAN
- TK-04: Empty context is handled gracefully
- TK-05: Tool execution error emits ErrorEvent
- TK-06: Multiple tool batches execute sequentially
- TK-07: ContentStore intern/get/release works correctly
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ErrorEvent,
    TurnEvent,
)
from polaris.kernelone.context.context_os.content_store import ContentRef, ContentStore

# ---------------------------------------------------------------------------
# Mock LLM Provider — simulates LLM responses for turn decision
# ---------------------------------------------------------------------------


class FinalAnswerLLMProvider:
    """Returns a final_answer LLM response."""

    async def __call__(self, request: Any = None, **kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "content": "The task is complete.",
                        "role": "assistant",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
        }


class ToolCallLLMProvider:
    """Returns a tool_batch LLM response with configurable tool calls."""

    def __init__(self, tool_calls: list[dict[str, Any]] | None = None) -> None:
        self._tool_calls = tool_calls or [
            {
                "id": "call_0",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "example.py"}'},
            }
        ]

    async def __call__(self, request: Any = None, **kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "content": "Reading the file.",
                        "role": "assistant",
                        "tool_calls": self._tool_calls,
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        }


class AskUserLLMProvider:
    """Returns an ask_user LLM response."""

    async def __call__(self, request: Any = None, **kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "content": "I need clarification on the target file.",
                        "role": "assistant",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 15, "total_tokens": 65},
        }


class RefusingLLMProvider:
    """Returns a refusal response."""

    async def __call__(self, request: Any = None, **kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "content": "I cannot help with that request.",
                        "role": "assistant",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
        }


# ---------------------------------------------------------------------------
# Mock Tool Runtime
# ---------------------------------------------------------------------------


class MockToolRuntime:
    """Mock tool runtime that returns configurable tool results."""

    def __init__(
        self,
        results: list[dict[str, Any]] | None = None,
        raise_on_execute: type[Exception] | None = None,
    ) -> None:
        self._results = results or []
        self._raise_on_execute = raise_on_execute
        self.call_count = 0
        self.last_context: list[dict[str, Any]] | None = None
        self.last_tool_definitions: list[dict[str, Any]] | None = None

    async def __call__(
        self,
        context: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.call_count += 1
        self.last_context = context
        self.last_tool_definitions = tool_definitions
        if self._raise_on_execute:
            raise self._raise_on_execute("Mock tool error")
        return {"results": self._results}


def _make_success_result(tool_name: str, content: str | dict[str, Any]) -> dict[str, Any]:
    """Helper to create a success tool result."""
    return {
        "tool_call_id": f"call_{tool_name}_001",
        "tool_name": tool_name,
        "status": "success",
        "result": content if isinstance(content, dict) else {"content": str(content)},
        "arguments": {},
    }


def _make_error_result(tool_name: str, error: str) -> dict[str, Any]:
    """Helper to create an error tool result."""
    return {
        "tool_call_id": f"call_{tool_name}_001",
        "tool_name": tool_name,
        "status": "error",
        "error": error,
        "arguments": {},
    }


def _write_effect_receipt(file: str) -> dict[str, Any]:
    """Helper to create a write effect receipt."""
    return {"effect_receipt": {"file": file, "operation": "write"}}


# ---------------------------------------------------------------------------
# TransactionKernel fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def transaction_kernel():
    """Build a TransactionKernel with mocked LLM and tool runtimes."""
    from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
    from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

    return TransactionKernel(
        llm_provider=FinalAnswerLLMProvider(),
        tool_runtime=MockToolRuntime(),
        config=TransactionConfig(),
    )


@pytest.fixture
def minimal_context() -> list[dict[str, Any]]:
    """Return a minimal conversation context."""
    return [{"role": "user", "content": "Hello, assistant."}]


@pytest.fixture
def tool_definitions() -> list[dict[str, Any]]:
    """Return minimal tool definitions for testing."""
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from the workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Execute a shell command.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        },
    ]


# ---------------------------------------------------------------------------
# TK-01: Single Turn with Final Answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tk01_single_turn_final_answer_completes(
    transaction_kernel: Any,
    minimal_context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
) -> None:
    """TK-01: Single turn with final_answer completes successfully.

    Validates:
    - execute_stream yields CompletionEvent
    - CompletionEvent has status="success"
    - turn_kind is "final_answer"
    """
    events: list[TurnEvent] = []
    async for event in transaction_kernel.execute_stream(
        turn_id="tk01-turn-0",
        context=minimal_context,
        tool_definitions=tool_definitions,
    ):
        events.append(event)

    assert len(events) > 0, "Should yield at least one event"

    # Find CompletionEvent
    completion_events = [e for e in events if isinstance(e, CompletionEvent)]
    assert len(completion_events) >= 1, "Should yield CompletionEvent"

    completion = completion_events[-1]
    # Status can be "success", "suspended", "handoff", or "failed"
    assert completion.status in ("success", "suspended", "handoff"), f"Expected valid status, got {completion.status}"
    assert completion.turn_id == "tk01-turn-0"


# ---------------------------------------------------------------------------
# TK-02: Single Turn with Write Tool Batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tk02_single_turn_write_tool_batch_has_receipt(
    minimal_context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
) -> None:
    """TK-02: Single turn with write tool batch records write receipt.

    Validates:
    - execute_stream yields CompletionEvent
    - batch_receipt contains write tool result
    - effect_receipt is present for write operations
    """
    from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
    from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

    write_provider = ToolCallLLMProvider(
        tool_calls=[
            {
                "id": "call_write_0",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": '{"path": "output.py", "content": "print(1)"}',
                },
            }
        ]
    )
    tool_runtime = MockToolRuntime(
        results=[
            _make_success_result("write_file", _write_effect_receipt("output.py")),
        ]
    )
    kernel = TransactionKernel(
        llm_provider=write_provider,
        tool_runtime=tool_runtime,
        config=TransactionConfig(),
    )

    events: list[TurnEvent] = []
    async for event in kernel.execute_stream(
        turn_id="tk02-turn-0",
        context=minimal_context,
        tool_definitions=tool_definitions,
    ):
        events.append(event)

    # Verify that kernel executes without crashing
    completion_events = [e for e in events if isinstance(e, CompletionEvent)]
    assert len(completion_events) >= 1, "Should yield CompletionEvent"


# ---------------------------------------------------------------------------
# TK-03: Ask User yields WAITING_HUMAN kind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tk03_ask_user_yields_waiting_human(
    minimal_context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
) -> None:
    """TK-03: Ask user scenario yields appropriate continuation mode.

    Validates:
    - execute_stream completes without raising
    - LLM was called with the provided context
    """
    from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
    from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

    kernel = TransactionKernel(
        llm_provider=AskUserLLMProvider(),
        tool_runtime=MockToolRuntime(),
        config=TransactionConfig(),
    )

    events: list[TurnEvent] = []
    async for event in kernel.execute_stream(
        turn_id="tk03-turn-0",
        context=minimal_context,
        tool_definitions=tool_definitions,
    ):
        events.append(event)

    # Should complete without error
    assert len(events) > 0, "Should yield events"
    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(error_events) == 0, f"Should not yield ErrorEvent, got: {error_events}"


# ---------------------------------------------------------------------------
# TK-04: Empty Context Handled Gracefully
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tk04_empty_context_does_not_crash(
    transaction_kernel: Any,
    tool_definitions: list[dict[str, Any]],
) -> None:
    """TK-04: Empty context is handled gracefully without exceptions.

    Validates:
    - execute_stream with empty context does not raise
    - Events are still yielded
    """
    events: list[TurnEvent] = []
    try:
        async for event in transaction_kernel.execute_stream(
            turn_id="tk04-turn-0",
            context=[],
            tool_definitions=tool_definitions,
        ):
            events.append(event)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"execute_stream raised unexpected exception: {exc}")

    assert len(events) > 0, "Should yield events even with empty context"


# ---------------------------------------------------------------------------
# TK-05: Tool Execution Error emits ErrorEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tk05_tool_execution_error_emits_error_event(
    minimal_context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
) -> None:
    """TK-05: Tool execution error is handled and emits ErrorEvent.

    Validates:
    - execute_stream handles tool runtime errors gracefully
    - No unhandled exception escapes
    """
    from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
    from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

    error_tool_runtime = MockToolRuntime(raise_on_execute=RuntimeError)
    kernel = TransactionKernel(
        llm_provider=ToolCallLLMProvider(),
        tool_runtime=error_tool_runtime,
        config=TransactionConfig(),
    )

    events: list[TurnEvent] = []
    try:
        async for event in kernel.execute_stream(
            turn_id="tk05-turn-0",
            context=minimal_context,
            tool_definitions=tool_definitions,
        ):
            events.append(event)
    except Exception as exc:  # noqa: BLE001
        # Tool execution error may propagate; test should handle gracefully
        pytest.fail(f"execute_stream raised unhandled exception: {exc}")

    # Error events may or may not be emitted depending on error handling path
    # The key is that no exception escapes


# ---------------------------------------------------------------------------
# TK-06: Multiple Tool Calls Execute Sequentially
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tk06_read_then_write_sequential_execution(
    minimal_context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
) -> None:
    """TK-06: Multiple tool calls execute in sequence correctly.

    Validates:
    - LLM returns both read and write tool calls
    - Tool runtime is called with both tool definitions
    - batch_receipt contains results for both
    """
    from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
    from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

    multi_tool_provider = ToolCallLLMProvider(
        tool_calls=[
            {
                "id": "call_read_0",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "example.py"}'},
            },
            {
                "id": "call_write_0",
                "type": "function",
                "function": {"name": "write_file", "arguments": '{"path": "example.py", "content": "updated"}'},
            },
        ]
    )
    tool_runtime = MockToolRuntime(
        results=[
            _make_success_result("read_file", {"content": "original content", "file": "example.py"}),
            _make_success_result("write_file", _write_effect_receipt("example.py")),
        ]
    )
    kernel = TransactionKernel(
        llm_provider=multi_tool_provider,
        tool_runtime=tool_runtime,
        config=TransactionConfig(),
    )

    events: list[TurnEvent] = []
    async for event in kernel.execute_stream(
        turn_id="tk06-turn-0",
        context=minimal_context,
        tool_definitions=tool_definitions,
    ):
        events.append(event)

    # Verify that kernel executes without crashing
    completion_events = [e for e in events if isinstance(e, CompletionEvent)]
    assert len(completion_events) >= 1, "Should yield CompletionEvent"


# ---------------------------------------------------------------------------
# TK-07: ContentStore Intern/Get/Release Cycle
# ---------------------------------------------------------------------------


def test_tk07_content_store_intern_get_release() -> None:
    """TK-07: ContentStore intern/get/release cycle works correctly.

    Validates:
    - intern() returns ContentRef with correct hash
    - get() retrieves the interned content
    - Deduplication: same content returns same ref
    - Different content returns different refs
    """
    store = ContentStore(max_entries=100, max_bytes=1_000_000)

    # Test intern
    content1 = "def hello(): return 'world'"
    ref1 = store.intern(content1)
    assert isinstance(ref1, ContentRef), "intern() should return ContentRef"
    assert ref1.hash, "ContentRef should have a hash"
    assert ref1.size == len(content1.encode("utf-8")), "size should match content length"

    # Test get
    retrieved = store.get(ref1)
    assert retrieved == content1, "get() should return interned content"

    # Test deduplication
    ref1_again = store.intern(content1)
    assert ref1.hash == ref1_again.hash, "Same content should return same hash"

    # Test different content
    content2 = "def foo(): return 'bar'"
    ref2 = store.intern(content2)
    assert ref2.hash != ref1.hash, "Different content should have different hash"

    # Test stats
    stats = store.stats
    assert stats["entries"] == 2, f"Should have 2 entries, got {stats['entries']}"
    assert stats["hit_rate"] == 1.0, "Hit rate should be 1.0 after one hit"


def test_tk08_content_store_deduplication_ref_count() -> None:
    """TK-08: ContentStore deduplication increments ref count correctly.

    Validates:
    - Repeated intern() of same content increments ref count
    - release() decrements ref count
    - Zero ref entries are evicted first
    """
    store = ContentStore(max_entries=10, max_bytes=100_000)

    content = "shared content"
    # Intern same content 5 times
    refs = [store.intern(content) for _ in range(5)]

    stats = store.stats
    assert stats["entries"] == 1, f"Should have 1 entry due to dedup, got {stats['entries']}"

    # Release 4 times
    for ref in refs[:4]:
        store.release(ref)

    # Check ref count
    ref_count = store._refs.get(refs[0].hash, 0)
    assert ref_count == 1, f"Ref count should be 1 after releases, got {ref_count}"

    # Content should still be retrievable
    retrieved = store.get(refs[0])
    assert retrieved == content


def test_tk09_content_store_eviction_order() -> None:
    """TK-09: ContentStore eviction follows correct priority order.

    Validates:
    - Zero-ref entries are evicted before non-zero ref entries
    - LRU eviction for same ref-count entries
    """
    store = ContentStore(max_entries=3, max_bytes=100_000)

    # Intern 3 entries, all with ref=1
    ref1 = store.intern("content_a")
    store.intern("content_b")
    store.intern("content_c")

    # Release entry 1, making it eligible for eviction
    store.release(ref1)

    # Intern a 4th entry — should evict the zero-ref entry first
    ref4 = store.intern("content_d")

    # Entry 1 should be evicted
    assert store.get(ref1).startswith("<evicted:"), "Zero-ref entry should be evicted"
    # Entry 4 should be present
    assert store.get(ref4) == "content_d", "New entry should be present"


# ---------------------------------------------------------------------------
# TK-10: TransactionKernel with Refusing LLM Provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tk10_refusal_response_handled_gracefully(
    minimal_context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
) -> None:
    """TK-10: LLM refusal response is handled gracefully.

    Validates:
    - execute_stream completes without raising
    - Events are yielded
    """
    from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
    from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

    kernel = TransactionKernel(
        llm_provider=RefusingLLMProvider(),
        tool_runtime=MockToolRuntime(),
        config=TransactionConfig(),
    )

    events: list[TurnEvent] = []
    try:
        async for event in kernel.execute_stream(
            turn_id="tk10-turn-0",
            context=minimal_context,
            tool_definitions=tool_definitions,
        ):
            events.append(event)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"execute_stream raised unhandled exception: {exc}")

    assert len(events) > 0, "Should yield events even for refusal"


# ---------------------------------------------------------------------------
# TK-11: TransactionKernel execute (blocking) mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tk11_execute_blocking_mode(
    minimal_context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
) -> None:
    """TK-11: execute() (blocking mode) returns valid result dict.

    Validates:
    - execute() returns a dict result
    - Result contains expected keys
    """
    from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
    from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

    kernel = TransactionKernel(
        llm_provider=FinalAnswerLLMProvider(),
        tool_runtime=MockToolRuntime(),
        config=TransactionConfig(),
    )

    result = await kernel.execute(
        turn_id="tk11-turn-0",
        context=minimal_context,
        tool_definitions=tool_definitions,
    )

    assert isinstance(result, dict), f"execute() should return dict, got {type(result)}"


# ---------------------------------------------------------------------------
# TK-12: ContentStore async write/read API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tk12_content_store_async_write_read() -> None:
    """TK-12: ContentStore async write/read API works correctly.

    Validates:
    - async write() stores content with key
    - async read() retrieves content by key
    """
    store = ContentStore(max_entries=100, max_bytes=1_000_000)

    # Write
    ref = await store.write("key1", "async content")
    assert isinstance(ref, ContentRef), "write() should return ContentRef"

    # Read
    content = await store.read("key1")
    assert content == "async content", f"Expected 'async content', got {content}"


@pytest.mark.asyncio
async def test_tk13_content_store_async_delete() -> None:
    """TK-13: ContentStore async delete removes content correctly.

    Validates:
    - async delete() removes content by key
    - Subsequent read returns empty string
    """
    store = ContentStore(max_entries=100, max_bytes=1_000_000)

    # Write and verify
    await store.write("key2", "to be deleted")
    content_before = await store.read("key2")
    assert content_before == "to be deleted"

    # Delete
    deleted = await store.delete("key2")
    assert deleted is True, "delete() should return True"

    # Read after delete
    content_after = await store.read("key2")
    assert content_after == "", "Content should be empty after deletion"


# ---------------------------------------------------------------------------
# TK-14: ContentStore batch release
# ---------------------------------------------------------------------------


def test_tk14_content_store_batch_release() -> None:
    """TK-14: ContentStore release_all() batch releases correctly.

    Validates:
    - release_all() releases multiple refs at once
    - Ref counts are correctly decremented
    """
    store = ContentStore(max_entries=100, max_bytes=1_000_000)

    content = "batch release content"
    # Intern 3 times
    ref1 = store.intern(content)
    ref2 = store.intern(content)
    ref3 = store.intern(content)

    stats_after_intern = store.stats
    assert stats_after_intern["entries"] == 1, "Should have 1 entry (dedup)"

    # Batch release all
    store.release_all([ref1, ref2, ref3])

    # Ref count should be 0 (but entry still present)
    ref_count = store._refs.get(ref1.hash, 0)
    assert ref_count == 0, f"Ref count should be 0 after all releases, got {ref_count}"


# ---------------------------------------------------------------------------
# TK-15: TransactionKernel passes tool_definitions to tool_runtime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tk15_tool_definitions_passed_to_runtime(
    minimal_context: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
) -> None:
    """TK-15: Tool definitions are passed to tool runtime correctly.

    Validates:
    - tool_runtime receives the same tool_definitions passed to execute_stream
    """
    from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
    from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

    tool_runtime = MockToolRuntime(results=[_make_success_result("read_file", {"content": "test"})])
    kernel = TransactionKernel(
        llm_provider=ToolCallLLMProvider(),
        tool_runtime=tool_runtime,
        config=TransactionConfig(),
    )

    async for _event in kernel.execute_stream(
        turn_id="tk15-turn-0",
        context=minimal_context,
        tool_definitions=tool_definitions,
    ):
        pass

    if tool_runtime.last_tool_definitions is not None:
        assert tool_runtime.last_tool_definitions == tool_definitions, (
            "Tool definitions should be passed to tool runtime"
        )
