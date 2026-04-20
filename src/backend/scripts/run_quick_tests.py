"""Quick test runner for new tests."""

import sys

sys.path.insert(0, r"/")

import asyncio
import tempfile

from polaris.kernelone.context.engine.cache import ContextCache
from polaris.kernelone.context.engine.engine import ContextEngine
from polaris.kernelone.context.engine.models import ContextBudget, ContextItem, ContextPack, ContextRequest
from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType


def test_message_bus_basic():
    """Basic message bus test."""
    print("Testing message bus...")
    bus = MessageBus()

    async def handler(msg):
        print(f"Received: {msg.sender}")

    async def main():
        await bus.subscribe(MessageType.TASK_SUBMITTED, handler)
        await bus.publish(Message(type=MessageType.TASK_SUBMITTED, sender="test"))
        await asyncio.sleep(0.1)

    asyncio.run(main())
    print("Message bus test PASSED")


def test_context_cache():
    """Basic context cache test."""
    print("Testing context cache...")
    cache = ContextCache()

    pack = ContextPack(
        request_hash="test_key",
        items=[ContextItem(id="item1", kind="code", content_or_pointer="test content", size_est=10)],
        total_tokens=10,
        total_chars=10,
    )

    cache.cache_pack(pack)
    retrieved = cache.get_cached_pack("test_key")

    assert retrieved is not None
    assert retrieved.request_hash == "test_key"
    print("Context cache test PASSED")


def test_context_engine():
    """Basic context engine test."""
    print("Testing context engine...")
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = ContextEngine(project_root=tmpdir)

        request = ContextRequest(
            run_id="test_run",
            step=1,
            role="developer",
            mode="edit",
            query="test query",
            budget=ContextBudget(max_tokens=1000, max_chars=5000),
        )

        pack = engine.build_context(request)

        assert isinstance(pack, ContextPack)
        assert pack.request_hash
        print("Context engine test PASSED")


if __name__ == "__main__":
    print("Running quick tests...")
    try:
        test_message_bus_basic()
        test_context_cache()
        test_context_engine()
        print("\n=== ALL QUICK TESTS PASSED ===")
    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
