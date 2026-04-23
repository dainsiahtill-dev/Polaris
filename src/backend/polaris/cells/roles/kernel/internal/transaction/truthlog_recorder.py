"""Turn TruthLog recorder with append-only UTF-8 JSONL persistence.

This module intentionally does not couple with controller orchestration.
It only provides an async recorder primitive for turn-level events.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

_TRUTHLOG_KEYS = (
    "ts_iso",
    "ts_epoch_ms",
    "turn_id",
    "turn_request_id",
    "event_type",
    "payload",
)


@dataclass(frozen=True, slots=True)
class _TruthLogRecord:
    ts_iso: str
    ts_epoch_ms: int
    turn_id: str
    turn_request_id: str
    event_type: str
    payload: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts_iso": self.ts_iso,
            "ts_epoch_ms": self.ts_epoch_ms,
            "turn_id": self.turn_id,
            "turn_request_id": self.turn_request_id,
            "event_type": self.event_type,
            "payload": self.payload,
        }


@dataclass(frozen=True, slots=True)
class _RecordCommand:
    record: _TruthLogRecord


@dataclass(frozen=True, slots=True)
class _FlushCommand:
    ack: asyncio.Future[None]


@dataclass(frozen=True, slots=True)
class _ShutdownCommand:
    ack: asyncio.Future[None]


_WriterCommand = _RecordCommand | _FlushCommand | _ShutdownCommand


class TurnTruthLogRecorder:
    """Append-only JSONL recorder for turn-level truth events."""

    def __init__(self, path: str | Path, *, max_queue_size: int = 0) -> None:
        self._path = Path(path)
        self._queue: asyncio.Queue[_WriterCommand] = asyncio.Queue(maxsize=max_queue_size)
        self._writer_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._shutdown_lock = asyncio.Lock()
        self._accepting_records = True
        self._writer_error: BaseException | None = None

    @property
    def path(self) -> Path:
        return self._path

    async def record(
        self,
        *,
        turn_id: str,
        turn_request_id: str,
        event_type: str,
        payload: Any,
    ) -> None:
        """Enqueue one turn event for append-only JSONL persistence."""
        self._raise_if_not_recordable()
        await self._ensure_writer_started()
        safe_payload = self._normalize_payload(payload)
        now = datetime.now(timezone.utc)
        ts_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        record = _TruthLogRecord(
            ts_iso=ts_iso,
            ts_epoch_ms=int(now.timestamp() * 1000),
            turn_id=str(turn_id),
            turn_request_id=str(turn_request_id),
            event_type=str(event_type),
            payload=safe_payload,
        )
        await self._queue.put(_RecordCommand(record=record))

    async def flush(self) -> None:
        """Flush queued events to disk and fsync current file descriptor."""
        if self._writer_task is None:
            return
        await self._send_sync_command(kind="flush")

    async def shutdown(self) -> None:
        """Gracefully stop writer task and stop accepting new records."""
        async with self._shutdown_lock:
            if not self._accepting_records and self._writer_task is None:
                return
            self._accepting_records = False
            writer_task = self._writer_task
            if writer_task is None:
                return
            await self._send_sync_command(kind="shutdown")
            await writer_task
            self._writer_task = None
            self._raise_if_writer_failed()

    async def _ensure_writer_started(self) -> None:
        self._raise_if_writer_failed()
        if self._writer_task is not None:
            if self._writer_task.done():
                await self._writer_task
                self._writer_task = None
                self._raise_if_writer_failed()
            return
        async with self._start_lock:
            self._raise_if_writer_failed()
            if self._writer_task is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._writer_task = asyncio.create_task(
                    self._writer_loop(),
                    name=f"turn_truthlog_writer:{self._path.name}",
                )

    async def _send_sync_command(self, *, kind: str) -> None:
        self._raise_if_writer_failed()
        writer_task = self._writer_task
        if writer_task is None:
            return
        loop = asyncio.get_running_loop()
        ack: asyncio.Future[None] = loop.create_future()
        if kind == "flush":
            command: _WriterCommand = _FlushCommand(ack=ack)
        elif kind == "shutdown":
            command = _ShutdownCommand(ack=ack)
        else:  # pragma: no cover - defensive guard
            raise ValueError(f"unsupported sync command: {kind}")
        await self._queue.put(command)
        await ack
        self._raise_if_writer_failed()

    async def _writer_loop(self) -> None:
        command: _WriterCommand | None = None
        try:
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                while True:
                    command = await self._queue.get()
                    if isinstance(command, _RecordCommand):
                        self._write_record(handle, command.record)
                        continue
                    if isinstance(command, _FlushCommand):
                        self._flush_to_disk(handle)
                        if not command.ack.done():
                            command.ack.set_result(None)
                        continue
                    if isinstance(command, _ShutdownCommand):
                        self._flush_to_disk(handle)
                        if not command.ack.done():
                            command.ack.set_result(None)
                        break
        except (OSError, RuntimeError, ValueError) as exc:
            self._writer_error = exc
            if isinstance(command, (_FlushCommand, _ShutdownCommand)) and not command.ack.done():
                command.ack.set_exception(exc)
            raise

    def _write_record(self, handle: TextIO, record: _TruthLogRecord) -> None:
        payload = json.dumps(record.as_dict(), ensure_ascii=False, separators=(",", ":"))
        handle.write(payload)
        handle.write("\n")

    def _flush_to_disk(self, handle: TextIO) -> None:
        handle.flush()
        os.fsync(handle.fileno())

    def _normalize_payload(self, payload: Any) -> Any:
        try:
            json.dumps(payload, ensure_ascii=False)
            return payload
        except (TypeError, ValueError):
            return repr(payload)

    def _raise_if_not_recordable(self) -> None:
        self._raise_if_writer_failed()
        if not self._accepting_records:
            raise RuntimeError("truthlog recorder is shut down; refusing new records")

    def _raise_if_writer_failed(self) -> None:
        if self._writer_error is not None:
            raise RuntimeError("truthlog writer loop failed") from self._writer_error


__all__ = ["_TRUTHLOG_KEYS", "TurnTruthLogRecorder"]
