"""TTL + capacity cleanup for ``runtime/contexts/``.

The per-LLM context viewer (see ``AIExecutor._store_context_messages``)
writes a new content-addressed JSON file to ``runtime/contexts/<shard>/<hash>``
on every LLM call. Because the payload is content-addressed by SHA-256, the
files are immutable — once nobody references a hash, the only cost is disk.

This module provides a deterministic, testable retention policy for the
``runtime/contexts/`` tree. The policy applies three caps in order, all
oldest-first:

1. **TTL** — drop files where ``now - mtime >= ttl_seconds``.
2. **max_files** — after the TTL pass, if the file count still exceeds
   ``max_files`` drop oldest until under the cap.
3. **max_total_bytes** — after both caps, if the total bytes still exceed
   ``max_total_bytes`` drop oldest until under the cap.

The implementation mirrors the fail-closed pattern of
``polaris.kernelone.akashic.hybrid_memory._cleanup_rotated_files``: every
``os.*`` call is wrapped in ``try/except OSError`` that logs and
continues, and the entire sweep never raises to the caller.

Scope is strict: only files under ``storage.get_path('runtime',
'contexts')`` are touched. Any path that resolves outside that subtree is
rejected via a ``commonpath`` check. The atomic counter file
``runtime/contexts/.sweep_state.json`` is the cheapest cross-process
state — it records ``last_sweep_at`` and the last ``_gate_state`` snapshot
so a follow-up ``sweep_if_needed()`` can decide whether to run a full
sweep without scanning the directory tree at all when the cap thresholds
are intact.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

logger = logging.getLogger(__name__)

SWEEP_STATE_FILENAME = ".sweep_state.json"


@dataclass
class ContextStoreRetentionConfig:
    """Configuration for ``ContextStoreRetention``.

    Attributes:
        ttl_seconds: Wall-clock TTL. Files older than this are dropped first.
        max_total_bytes: Hard cap on the total bytes in ``runtime/contexts/``.
            After TTL, the oldest files are dropped until the tree is under
            this cap.
        max_files: Hard cap on the number of files in ``runtime/contexts/``.
            After TTL, the oldest files are dropped until the tree is under
            this cap.
        sweep_min_interval_seconds: Minimum wall-clock interval between full
            sweeps. ``sweep_if_needed()`` will skip the full sweep when the
            last sweep was within this window AND the gate thresholds are
            all clean.
        enabled: Master switch — when ``False``, ``on_read_gate()`` and
            ``sweep_if_needed()`` are no-ops.
    """

    ttl_seconds: int = 604800  # 7 days
    max_total_bytes: int = 524288000  # 500MB
    max_files: int = 20000
    sweep_min_interval_seconds: int = 300  # 5 minutes
    enabled: bool = True


@dataclass
class SweepReport:
    """Result of a single sweep pass.

    Attributes:
        scanned_files: Number of candidate files visited during the sweep.
        removed_files: Number of files actually removed.
        removed_bytes: Cumulative size of removed files.
        kept_files: Number of candidate files left in the store after sweep.
        total_bytes_after: Total bytes under ``contexts_root`` after sweep.
        elapsed_ms: Wall-clock duration of the sweep in milliseconds.
        triggers: Tags describing why the sweep was triggered.
            Always includes ``"manual"`` for admin-driven sweeps, plus
            ``"ttl"``, ``"max_files"``, ``"max_total_bytes"`` when the
            corresponding cap fired.
    """

    scanned_files: int
    removed_files: int
    removed_bytes: int
    kept_files: int
    total_bytes_after: int
    elapsed_ms: int
    triggers: list[str] = field(default_factory=list)


class ContextStoreRetention:
    """Manage TTL and capacity caps for ``runtime/contexts/``.

    The retention class is intentionally cheap to instantiate: every
    public method is stateless with respect to the in-memory state of the
    instance (apart from the cached paths and the counter file). The
    ``sweep_min_interval_seconds`` throttle and the counter file
    ``.sweep_state.json`` together provide a cheap cross-process gate so
    the hot read path is one ``os.scandir`` over at most a few hundred
    files.

    Args:
        workspace: Workspace path or ``None`` (falls back to current
            working directory).
        config: Optional ``ContextStoreRetentionConfig`` — defaults applied
            when ``None``.
        runtime_base: Optional explicit runtime base. Defaults to
            ``build_cache_root("", workspace)`` so the same root the
            store uses is used by the reaper.
    """

    def __init__(
        self,
        workspace: str | None = None,
        config: ContextStoreRetentionConfig | None = None,
        runtime_base: str | None = None,
    ) -> None:
        # Local imports to avoid import cycles (storage is heavy).
        from pathlib import Path

        from polaris.kernelone.storage import StorageLayout
        from polaris.kernelone.storage.io_paths import resolve_storage_roots

        self._workspace = workspace or "."
        self._config = config or ContextStoreRetentionConfig()
        if runtime_base:
            layout = StorageLayout(workspace=self._workspace, runtime_base=runtime_base)
            contexts_root = layout.get_path("runtime", "contexts")
            runtime_root = layout.runtime_root
        else:
            roots = resolve_storage_roots(self._workspace)
            runtime_root = Path(roots.runtime_root)
            contexts_root = runtime_root / "contexts"
        # ``contexts_root`` is a Path — we want string paths for os.scandir.
        self._contexts_root = str(contexts_root.resolve())
        self._runtime_root = str(runtime_root.resolve())
        self._sweep_state_path = os.path.join(self._contexts_root, SWEEP_STATE_FILENAME)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def config(self) -> ContextStoreRetentionConfig:
        return self._config

    @property
    def contexts_root(self) -> str:
        return self._contexts_root

    @property
    def runtime_root(self) -> str:
        return self._runtime_root

    @property
    def sweep_state_path(self) -> str:
        return self._sweep_state_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _is_within_contexts_root(self, path: str) -> bool:
        """Return True when ``path`` resolves inside ``contexts_root``."""
        try:
            resolved = os.path.realpath(path)
            root = self._contexts_root
            common = os.path.commonpath([resolved, root])
            return common == root
        except (OSError, ValueError):
            return False

    def _iter_candidate_files(self) -> Iterator[tuple[str, float, int]]:
        """Yield ``(path, mtime, size)`` for every regular file under
        ``contexts_root``.

        Skips the ``.sweep_state.json`` counter file itself, and any file
        that does not pass the within-root guard. The iteration uses
        ``os.scandir`` so stat data is fetched in batched syscalls rather
        than as one round-trip per file.

        Yields:
            ``(absolute_path, mtime, size)`` tuples. ``mtime`` is a
            ``float`` seconds-since-epoch. ``size`` is an ``int`` in
            bytes.
        """
        if not os.path.isdir(self._contexts_root):
            return
        try:
            with os.scandir(self._contexts_root) as shard_iter:
                for shard_entry in shard_iter:
                    if not shard_entry.is_dir(follow_symlinks=False):
                        continue
                    try:
                        with os.scandir(shard_entry.path) as file_iter:
                            for file_entry in file_iter:
                                if not file_entry.is_file(follow_symlinks=False):
                                    continue
                                if file_entry.name == SWEEP_STATE_FILENAME:
                                    continue
                                try:
                                    stat = file_entry.stat(follow_symlinks=False)
                                except OSError as exc:
                                    logger.debug(
                                        "context_retention: stat failed for %s: %s",
                                        file_entry.path,
                                        exc,
                                    )
                                    continue
                                if not self._is_within_contexts_root(file_entry.path):
                                    logger.warning(
                                        "context_retention: skipping out-of-root file %s",
                                        file_entry.path,
                                    )
                                    continue
                                yield (file_entry.path, stat.st_mtime, stat.st_size)
                    except OSError as exc:
                        logger.debug(
                            "context_retention: scan failed for shard %s: %s",
                            shard_entry.path,
                            exc,
                        )
                        continue
        except OSError as exc:
            logger.debug(
                "context_retention: top-level scan failed for %s: %s",
                self._contexts_root,
                exc,
            )

    def _gate_state(self) -> dict[str, Any]:
        """Return a cheap state snapshot of the contexts tree.

        Uses ``os.scandir`` for batched stat retrieval — no file content
        is read. The returned dict has the shape ``{"file_count": int,
        "total_bytes": int, "oldest_mtime": float | None}`` suitable for
        cheap cross-process persistence in the counter file.
        """
        file_count = 0
        total_bytes = 0
        oldest_mtime: float | None = None
        for _path, mtime, size in self._iter_candidate_files():
            file_count += 1
            total_bytes += size
            if oldest_mtime is None or mtime < oldest_mtime:
                oldest_mtime = mtime
        return {
            "file_count": file_count,
            "total_bytes": total_bytes,
            "oldest_mtime": oldest_mtime,
        }

    def _read_sweep_state(self) -> dict[str, Any]:
        """Read the ``.sweep_state.json`` counter file, tolerant of
        absence / corruption. Always returns a dict with the keys
        ``last_sweep_at`` (float) and ``last_gate_state`` (dict)."""
        fallback: dict[str, Any] = {"last_sweep_at": 0.0, "last_gate_state": {}}
        if not os.path.isfile(self._sweep_state_path):
            return fallback
        try:
            with open(self._sweep_state_path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.debug(
                "context_retention: failed to read sweep state %s: %s",
                self._sweep_state_path,
                exc,
            )
            return fallback
        if not isinstance(raw, dict):
            return fallback
        last_sweep_at_raw = raw.get("last_sweep_at", 0.0)
        try:
            last_sweep_at = float(last_sweep_at_raw)
        except (TypeError, ValueError):
            last_sweep_at = 0.0
        last_gate_state = raw.get("last_gate_state", {})
        if not isinstance(last_gate_state, dict):
            last_gate_state = {}
        return {"last_sweep_at": last_sweep_at, "last_gate_state": last_gate_state}

    def _write_sweep_state(self, last_sweep_at: float, last_gate_state: dict[str, Any]) -> None:
        """Persist ``last_sweep_at`` and ``last_gate_state`` atomically.

        Uses the same temp-file + ``os.replace`` pattern as the context
        store itself, so a crash mid-write never leaves a half-written
        counter file. All errors are swallowed (logged at debug) — the
        gate is best-effort, not load-bearing.
        """
        payload = {
            "last_sweep_at": last_sweep_at,
            "last_gate_state": last_gate_state,
        }
        tmp_path = self._sweep_state_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp_path, self._sweep_state_path)
        except OSError as exc:
            logger.debug(
                "context_retention: failed to write sweep state %s: %s",
                self._sweep_state_path,
                exc,
            )
            # Best-effort cleanup of the temp file.
            try:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    def get_stats(self) -> dict[str, Any]:
        """Return the current stats snapshot (cheap path)."""
        state = self._gate_state()
        counter = self._read_sweep_state()
        return {
            "workspace": str(self._workspace),
            "contexts_root": self._contexts_root,
            "file_count": state["file_count"],
            "total_bytes": state["total_bytes"],
            "oldest_mtime": state["oldest_mtime"],
            "newest_mtime": self._newest_mtime(),
            "config": {
                "ttl_seconds": self._config.ttl_seconds,
                "max_total_bytes": self._config.max_total_bytes,
                "max_files": self._config.max_files,
                "sweep_min_interval_seconds": self._config.sweep_min_interval_seconds,
                "enabled": self._config.enabled,
            },
            "last_sweep_at": counter["last_sweep_at"],
        }

    def _newest_mtime(self) -> float | None:
        """Return the newest mtime across all candidate files, or None.

        Used by the admin stats endpoint. O(n) over candidate files, but
        a single scandir pass is fine for the admin path.
        """
        newest: float | None = None
        for _path, mtime, _size in self._iter_candidate_files():
            if newest is None or mtime > newest:
                newest = mtime
        return newest

    def sweep(self, triggers: list[str] | None = None) -> SweepReport:
        """Run a full sweep pass, applying TTL → max_files →
        max_total_bytes in order.

        Args:
            triggers: Optional tag list to attach to the report. When
                omitted, defaults to ``["manual"]``.

        Returns:
            A :class:`SweepReport` describing the pass. The function
            never raises — every ``os.*`` call is OSError-guarded.
        """
        start = time.monotonic()
        triggers = list(triggers) if triggers else ["manual"]
        scanned = 0
        removed = 0
        removed_bytes = 0
        kept_bytes = 0

        candidates: list[tuple[float, str, int]] = []
        for path, mtime, size in self._iter_candidate_files():
            scanned += 1
            candidates.append((mtime, path, size))
            kept_bytes += size

        # Sort by mtime ascending so the oldest is first.
        candidates.sort(key=lambda item: item[0])

        now = time.time()
        ttl_cutoff = now - float(self._config.ttl_seconds)
        max_files = int(self._config.max_files)
        max_bytes = int(self._config.max_total_bytes)
        kept_count = len(candidates)

        def _remove(idx: int) -> None:
            nonlocal removed, removed_bytes, kept_count
            _mtime, path, size = candidates[idx]
            try:
                if not self._is_within_contexts_root(path):
                    logger.warning(
                        "context_retention: refusing to remove out-of-root path %s",
                        path,
                    )
                    return
                os.remove(path)
                removed += 1
                removed_bytes += size
                kept_count -= 1
            except FileNotFoundError:
                # Already gone — counting as not-removed is fine.
                pass
            except OSError as exc:
                logger.warning(
                    "context_retention: failed to remove %s: %s",
                    path,
                    exc,
                )

        # Phase 1: TTL
        ttl_fired = False
        idx = 0
        for idx in range(len(candidates)):
            mtime = candidates[idx][0]
            if mtime >= ttl_cutoff:
                break
            ttl_fired = True
        else:
            idx = len(candidates)
        if ttl_fired and "ttl" not in triggers:
            triggers.append("ttl")
        # Remove all indices [0, idx) where mtime < cutoff.
        for _i in range(idx - 1, -1, -1):
            _remove(_i)

        # Recompute state after TTL pass; cheapest approach is to filter
        # the candidate list in place. We re-derive from scratch using
        # a scan since removal is best-effort (racing deletes are OK).
        candidates = [item for item in candidates if (item[1] and os.path.isfile(item[1]))]
        # Re-sort by mtime ascending (preserving oldest-first invariant).
        candidates.sort(key=lambda item: item[0])

        # Phase 2: max_files cap
        if len(candidates) > max_files:
            if "max_files" not in triggers:
                triggers.append("max_files")
            excess = len(candidates) - max_files
            for _i in range(excess):
                _remove(0)
                candidates.pop(0)
                if not candidates:
                    break

        # Phase 3: max_total_bytes cap
        if candidates:
            total_bytes = sum(size for _mtime, _path, size in candidates)
            if total_bytes > max_bytes:
                if "max_total_bytes" not in triggers:
                    triggers.append("max_total_bytes")
                idx = 0
                while total_bytes > max_bytes and idx < len(candidates):
                    _mtime, path, size = candidates[idx]
                    if not os.path.isfile(path):
                        # Race: already gone, drop from candidate list.
                        candidates.pop(idx)
                        continue
                    _remove(idx)
                    candidates.pop(idx)
                    total_bytes -= size
                    if not candidates:
                        break

        kept_bytes_after = sum(size for _mtime, _path, size in candidates)
        kept_count = len(candidates)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Persist counter file last (cheap, never raises).
        try:
            self._write_sweep_state(
                last_sweep_at=now,
                last_gate_state={
                    "file_count": kept_count,
                    "total_bytes": kept_bytes_after,
                    "oldest_mtime": candidates[0][0] if candidates else None,
                },
            )
        except OSError as exc:
            logger.debug(
                "context_retention: counter write outer guard: %s",
                exc,
            )

        report = SweepReport(
            scanned_files=scanned,
            removed_files=removed,
            removed_bytes=removed_bytes,
            kept_files=kept_count,
            total_bytes_after=kept_bytes_after,
            elapsed_ms=elapsed_ms,
            triggers=triggers,
        )

        if removed > 0:
            logger.info(
                "context_retention: sweep removed=%d kept=%d bytes_removed=%d elapsed_ms=%d triggers=%s",
                removed,
                kept_count,
                removed_bytes,
                elapsed_ms,
                ",".join(triggers),
            )

        return report

    def sweep_if_needed(self) -> SweepReport | None:
        """Run ``sweep`` only when the cheap gate says it's needed.

        The gate fires when **any** of the following is true:

        - ``file_count > max_files``
        - ``total_bytes > max_total_bytes``
        - ``oldest_mtime < now - ttl_seconds``
        - ``last_sweep_at < now - sweep_min_interval_seconds``
          (i.e. the throttle window has elapsed — guarantees the gate
          runs at least once every ``sweep_min_interval_seconds``)

        Otherwise this method is a no-op and returns ``None``.

        The whole method never raises; OSError paths log and skip.
        """
        if not self._config.enabled:
            return None
        now = time.time()
        try:
            state = self._gate_state()
        except OSError as exc:
            logger.debug("context_retention: gate scan failed: %s", exc)
            return None
        counter = self._read_sweep_state()
        try:
            last_sweep_at = float(counter.get("last_sweep_at", 0.0))
        except (TypeError, ValueError):
            last_sweep_at = 0.0
        throttle_window = float(self._config.sweep_min_interval_seconds)
        within_throttle = (now - last_sweep_at) < throttle_window

        triggers: list[str] = []
        file_count = int(state.get("file_count", 0))
        total_bytes = int(state.get("total_bytes", 0))
        oldest_mtime_raw = state.get("oldest_mtime")
        try:
            oldest_mtime = float(oldest_mtime_raw) if oldest_mtime_raw is not None else None
        except (TypeError, ValueError):
            oldest_mtime = None

        if file_count > int(self._config.max_files):
            triggers.append("max_files")
        if total_bytes > int(self._config.max_total_bytes):
            triggers.append("max_total_bytes")
        if oldest_mtime is not None and oldest_mtime < (now - float(self._config.ttl_seconds)):
            triggers.append("ttl")

        if not triggers and within_throttle:
            return None

        return self.sweep(triggers=triggers)

    def on_read_gate(self) -> dict[str, Any]:
        """Cheap hot-path entry point invoked after every store.

        Calls :meth:`sweep_if_needed` and returns the current gate state.
        Never raises to the caller.
        """
        try:
            self.sweep_if_needed()
        except (OSError, RuntimeError, ValueError, TypeError) as exc:  # pragma: no cover - belt + braces
            logger.debug("context_retention: on_read_gate sweep failed: %s", exc)
        try:
            return self._gate_state()
        except OSError as exc:
            logger.debug("context_retention: on_read_gate state failed: %s", exc)
            return {"file_count": 0, "total_bytes": 0, "oldest_mtime": None}


# ---------------------------------------------------------------------------
# Module-level lazy singleton cache
# ---------------------------------------------------------------------------
_RETENTION_CACHE: dict[str, ContextStoreRetention] = {}


def get_retention(workspace: str | None = None) -> ContextStoreRetention:
    """Return a per-workspace cached :class:`ContextStoreRetention`.

    The retention is layout-scoped to ``storage.get_path('runtime',
    'contexts')``, so caching by workspace is correct. Test code that
    needs a fresh instance (with a different config) should construct
    ``ContextStoreRetention(...)`` directly.
    """
    key = str(workspace or ".")
    cached = _RETENTION_CACHE.get(key)
    if cached is not None:
        return cached
    instance = ContextStoreRetention(workspace=key)
    _RETENTION_CACHE[key] = instance
    return instance


def clear_retention_cache() -> None:
    """Reset the per-workspace cache (used in tests)."""
    _RETENTION_CACHE.clear()


__all__ = [
    "ContextStoreRetention",
    "ContextStoreRetentionConfig",
    "SweepReport",
    "clear_retention_cache",
    "get_retention",
]
