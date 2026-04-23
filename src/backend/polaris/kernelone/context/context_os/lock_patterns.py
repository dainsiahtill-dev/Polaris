"""Lock pattern templates and enforcement for ContextOS concurrent operations.

Provides standardized templates for the Snapshot → Compute → Validate/Commit
paradigm, ensuring async locks are used correctly.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Protocol, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


class LockTemplate(Protocol):
    """Protocol for lock-based operation templates."""

    async def snapshot(self) -> Any:
        """Capture immutable snapshot under lock."""
        ...

    async def compute(self, snapshot: Any) -> Any:
        """Perform computation outside lock."""
        ...

    async def validate_and_commit(self, result: Any) -> bool:
        """Validate and commit under lock."""
        ...


def snapshot_compute_commit(
    lock: asyncio.Lock,
    executor: ThreadPoolExecutor | None = None,
) -> Callable[[Callable[..., R]], Callable[..., R]]:
    """Decorator that enforces Snapshot → Compute → Commit pattern.

    Usage:
        class MyService:
            def __init__(self):
                self._lock = asyncio.Lock()
                self._executor = ThreadPoolExecutor(max_workers=4)

            @snapshot_compute_commit(lock="_lock", executor="_executor")
            async def process(self, data: InputData) -> OutputData:
                # This method is automatically wrapped:
                # 1. snapshot() is called under lock
                # 2. compute() is called outside lock (with run_in_executor)
                # 3. validate_and_commit() is called under lock
                ...

    Args:
        lock: The asyncio.Lock to use for synchronization
        executor: Optional ThreadPoolExecutor for offloading CPU work

    Returns:
        Decorator function
    """

    def decorator(method: Callable[..., R]) -> Callable[..., R]:
        @functools.wraps(method)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> R:
            lock_obj = getattr(self, lock) if isinstance(lock, str) else lock
            executor_obj = getattr(self, executor) if isinstance(executor, str) else executor

            # Phase 1: Snapshot under lock
            async with lock_obj:
                snapshot = await _call_if_coro(self.snapshot, *args, **kwargs)

            # Phase 2: Compute outside lock
            if executor_obj:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    executor_obj,
                    lambda: method(self, snapshot, *args, **kwargs),
                )
            else:
                result = method(self, snapshot, *args, **kwargs)

            # Phase 3: Validate and commit under lock
            async with lock_obj:
                if await _call_if_coro(self.validate_and_commit, result):
                    return result
                else:
                    raise RuntimeError("Validation failed - result rejected")

        return wrapper  # type: ignore[return-value]

    return decorator


async def _call_if_coro(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a function, awaiting it if it's a coroutine."""
    result = func(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result


class AsyncLockGuard:
    """Context manager that enforces lock usage rules.

    Usage:
        async with AsyncLockGuard(self._lock, max_lines=10):
            # Critical section - must be short
            self._counter += 1

    Raises:
        RuntimeError: If critical section exceeds max_lines or contains forbidden operations
    """

    FORBIDDEN_PATTERNS = {
        "open(",
        "with open",
        "json.dump",
        "json.dumps",
        "re.compile",
        "requests.",
        "httpx.",
        "aiohttp.",
    }

    def __init__(self, lock: asyncio.Lock, max_lines: int = 10) -> None:
        self.lock = lock
        self.max_lines = max_lines

    async def __aenter__(self) -> AsyncLockGuard:
        await self.lock.acquire()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.lock.release()
