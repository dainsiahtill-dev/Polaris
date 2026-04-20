from __future__ import annotations

import asyncio
import atexit
import logging
import os
import threading
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, cast

logger = logging.getLogger(__name__)

# Type alias for Observer - can be the actual class or None
# We use Any for the type annotation to avoid mypy issues with conditional imports
ObserverType: Any = None

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer as _ObserverClass

    ObserverType = _ObserverClass
except (RuntimeError, ValueError):  # pragma: no cover - fallback when watchdog is unavailable
    # Create fallback base class for FileSystemEventHandler
    class FileSystemEventHandler:  # type: ignore[no-redef]
        """Fallback FileSystemEventHandler when watchdog is unavailable."""

        pass


class WatchState(Enum):
    """Watcher lifecycle states."""

    STARTING = auto()
    RUNNING = auto()
    FAILED = auto()
    STOPPING = auto()
    STOPPED = auto()


@dataclass
class WatchEntry:
    """Entry for a watched root path with reference counting."""

    observer: Any = None
    handler: FileSystemEventHandler | None = None
    ref_count: int = 0
    state: WatchState = WatchState.STARTING
    # Creation lock to prevent TOCTOU for concurrent ensure_watch calls
    creation_lock: threading.Lock = field(default_factory=threading.Lock)
    # Condition for waiting on state transitions
    state_condition: threading.Condition = field(default_factory=lambda: threading.Condition(threading.Lock()))
    # Error info if state is FAILED
    error_info: str | None = None


class _WatchHandler(FileSystemEventHandler):  # type: ignore[misc]
    def __init__(self, hub: RealtimeSignalHub, root: str) -> None:
        super().__init__()
        self._hub = hub
        self._root = root

    def on_any_event(self, event) -> None:
        if getattr(event, "is_directory", False):
            return
        path = str(getattr(event, "src_path", "") or "").strip()
        if not path:
            return
        self._hub.notify_from_thread(source="fs", path=path, root=self._root)


class RealtimeSignalHub:
    """Unified realtime signal hub for runtime status and artifact changes.

    This class manages filesystem watchers with proper reference counting
    to prevent TOCTOU races and resource leaks.
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._sequence = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        # Registry of watched roots with reference counting
        self._registry: dict[str, WatchEntry] = {}
        # Workspace context for signal filtering
        self._last_signal_workspace: str = ""
        # Global lock for registry modifications
        self._lock = threading.Lock()
        self._closed = False

    def _ensure_condition_for_running_loop(self) -> None:
        """Rebind asyncio.Condition when calls come from a different event loop.

        Test suites and multi-loop runtimes can access the singleton from
        different loops. asyncio primitives are loop-bound, so we must recreate
        condition objects when loop ownership changes.
        """
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        with self._lock:
            if self._loop is None:
                self._loop = running_loop
                return
            if self._loop is running_loop:
                return
            # Loop changed: reset condition for the new loop while preserving sequence.
            self._loop = running_loop
            self._condition = asyncio.Condition()

    def close(self) -> None:
        """Close signal center, release all resources."""
        with self._lock:
            if self._closed:
                return
            self._closed = True

        # Release all watches
        self._release_all_watches()

    def __del__(self) -> None:
        """析构时确保资源释放."""
        if hasattr(self, "_closed") and not self._closed:
            try:
                self.close()
            except (RuntimeError, ValueError) as exc:
                logger.debug("signal_hub __del__ close failed: %s", exc)

    def _normalize_root(self, root: str) -> str:
        """Normalize root path for consistent registry keys."""
        return os.path.abspath(str(root or "").strip())

    def _release_all_watches(self) -> None:
        """Release all watches from registry."""
        with self._lock:
            entries = list(self._registry.items())
            self._registry.clear()

        for path, entry in entries:
            self._stop_observer(path, entry)

    def _stop_observer(self, path: str, entry: WatchEntry) -> None:
        """Stop a single observer and clean up."""
        with entry.state_condition:
            if entry.state in (WatchState.STOPPING, WatchState.STOPPED):
                return
            entry.state = WatchState.STOPPING

        observer = entry.observer
        if observer is not None:
            try:
                observer.stop()
                observer.join(timeout=5.0)
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Failed to stop observer for {path}: {e}")

        with entry.state_condition:
            entry.state = WatchState.STOPPED
            entry.state_condition.notify_all()

    async def ensure_watch(self, root: str) -> bool:
        """Ensure a filesystem watcher exists for the given runtime root.

        This method uses reference counting - multiple calls with the same root
        will increment the ref_count. Use release_watch() to decrement.

        Args:
            root: The directory root to watch

        Returns:
            True if watcher is ensured (either existed or created successfully)
        """
        # Check for empty/whitespace-only root before normalization
        if not root or not str(root).strip():
            return False

        normalized_root = self._normalize_root(root)
        if not normalized_root:
            return False

        os.makedirs(normalized_root, exist_ok=True)

        # Fast path: check if already running
        with self._lock:
            if self._closed:
                return False
            entry = self._registry.get(normalized_root)
            if entry is not None:
                with entry.state_condition:
                    if entry.state == WatchState.RUNNING:
                        entry.ref_count += 1
                        logger.debug(f"ensure_watch: incremented ref_count for {normalized_root} to {entry.ref_count}")
                        return True
                    elif entry.state == WatchState.STARTING:
                        # Wait for state transition
                        pass
                    elif entry.state in (WatchState.FAILED, WatchState.STOPPED):
                        # Remove failed/stopped entry and recreate
                        self._registry.pop(normalized_root, None)
                        entry = None

        # Slow path: need to create
        if ObserverType is None:
            logger.warning("Watchdog Observer not available")
            return False

        # Use per-entry lock to serialize creators for same root
        entry = None
        with self._lock:
            if self._closed:
                return False
            entry = self._registry.get(normalized_root)
            if entry is None:
                entry = WatchEntry()
                self._registry[normalized_root] = entry

        # Serialize concurrent creation attempts for same root
        with entry.creation_lock:
            # Double-check after acquiring lock
            with entry.state_condition:
                if entry.state == WatchState.RUNNING:
                    entry.ref_count += 1
                    logger.debug(
                        f"ensure_watch: post-lock incremented ref_count for {normalized_root} to {entry.ref_count}"
                    )
                    return True
                if entry.state == WatchState.FAILED:
                    logger.warning(f"ensure_watch: previous attempt failed for {normalized_root}")
                    return False

            # Create observer
            try:
                # ObserverType is guaranteed non-None here (checked above)
                observer_cls = cast("type[Any]", ObserverType)
                observer = observer_cls()
                handler = _WatchHandler(self, normalized_root)
                observer.schedule(handler, normalized_root, recursive=True)
                observer.start()

                with entry.state_condition:
                    entry.observer = observer
                    entry.handler = handler
                    entry.ref_count = 1
                    entry.state = WatchState.RUNNING
                    entry.state_condition.notify_all()

                logger.debug(f"ensure_watch: created new watcher for {normalized_root}")
                return True

            except (RuntimeError, ValueError) as e:
                error_msg = f"{type(e).__name__}: {e}"
                logger.error(f"Failed to create watcher for {normalized_root}: {error_msg}")
                with entry.state_condition:
                    entry.state = WatchState.FAILED
                    entry.error_info = error_msg
                    entry.state_condition.notify_all()
                return False

    def release_watch(self, root: str) -> None:
        """Release a filesystem watcher for the given runtime root.

        Decrements reference count. When count reaches 0, stops and removes the watcher.

        Args:
            root: The directory root to release
        """
        normalized_root = self._normalize_root(root)
        if not normalized_root:
            return

        with self._lock:
            entry = self._registry.get(normalized_root)
            if entry is None:
                return

        with entry.state_condition:
            if entry.state != WatchState.RUNNING:
                return
            entry.ref_count = max(0, entry.ref_count - 1)
            current_count = entry.ref_count
            logger.debug(f"release_watch: decremented ref_count for {normalized_root} to {current_count}")

            if current_count > 0:
                return

        # ref_count reached 0, remove and stop
        with self._lock:
            # Double-check under global lock
            entry = self._registry.pop(normalized_root, None)

        if entry is not None:
            logger.debug(f"release_watch: stopping watcher for {normalized_root}")
            self._stop_observer(normalized_root, entry)

    def get_watch_info(self, root: str) -> dict | None:
        """Get watch information for debugging/observability.

        Returns:
            Dict with state, ref_count, error_info (if failed), or None if not registered
        """
        normalized_root = self._normalize_root(root)
        with self._lock:
            entry = self._registry.get(normalized_root)
            if entry is None:
                return None
            with entry.state_condition:
                return {
                    "root": normalized_root,
                    "state": entry.state.name,
                    "ref_count": entry.ref_count,
                    "error_info": entry.error_info,
                }

    def list_watches(self) -> list[dict]:
        """List all active watches for debugging."""
        result = []
        with self._lock:
            entries = list(self._registry.items())

        for root, entry in entries:
            with entry.state_condition:
                result.append(
                    {
                        "root": root,
                        "state": entry.state.name,
                        "ref_count": entry.ref_count,
                        "error_info": entry.error_info,
                    }
                )
        return result

    async def notify(
        self,
        *,
        source: str = "runtime",
        path: str = "",
        root: str = "",
    ) -> int:
        """Publish an in-process realtime signal.

        Args:
            source: Signal source identifier (e.g., 'fs', 'runtime')
            path: Path of the changed file (if applicable)
            root: Workspace root path for filtering signals
        """
        self._ensure_condition_for_running_loop()

        # Normalize root for consistent workspace identification
        normalized_root = self._normalize_root(root) if root else ""

        async with self._condition:
            self._sequence += 1
            # Store the workspace context for filtering
            self._last_signal_workspace = normalized_root
            self._condition.notify_all()
            return self._sequence

    def notify_from_thread(
        self,
        *,
        source: str = "runtime",
        path: str = "",
        root: str = "",
    ) -> None:
        """Publish a signal from watcher threads."""
        # 使用锁保护 _loop 的访问
        with self._lock:
            loop = self._loop
        if loop is None:
            return
        try:

            def _schedule_notify() -> None:
                try:
                    loop.create_task(self.notify(source=source, path=path, root=root))
                except (RuntimeError, ValueError) as e:
                    logger.debug(f"Failed to schedule notify: {e}")

            loop.call_soon_threadsafe(_schedule_notify)
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to call soon threadsafe: {e}")

    async def wait_for_update(
        self,
        last_seen: int,
        timeout_sec: float | None = None,
        workspace: str = "",
    ) -> int:
        """Wait for a newer signal than `last_seen`.

        Args:
            last_seen: Last seen sequence number
            timeout_sec: Timeout in seconds (None for no timeout)
            workspace: If provided, only return when signal is for this workspace

        Returns:
            Current sequence number
        """
        # Normalize workspace path for consistent comparison
        normalized_workspace = self._normalize_root(workspace) if workspace else ""

        self._ensure_condition_for_running_loop()

        async with self._condition:
            # If we already have a newer signal and it matches our workspace (or no filter), return immediately
            if self._sequence != last_seen and (
                not normalized_workspace or self._last_signal_workspace == normalized_workspace
            ):
                return self._sequence

            # Wait for a signal (with optional timeout)
            try:
                if timeout_sec is None:
                    await self._condition.wait()
                else:
                    await asyncio.wait_for(self._condition.wait(), timeout=timeout_sec)
            except asyncio.TimeoutError:
                return self._sequence

            return self._sequence


REALTIME_SIGNAL_HUB = RealtimeSignalHub()


def _cleanup_realtime_hub() -> None:
    """模块卸载时清理全局实例."""
    try:
        REALTIME_SIGNAL_HUB.close()
    except (RuntimeError, ValueError) as exc:
        logger.debug("atexit signal_hub cleanup failed: %s", exc)


atexit.register(_cleanup_realtime_hub)
