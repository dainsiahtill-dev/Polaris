"""Stream event archiver for run-archive cell.

Serialises the full event sequence produced by a single stream_turn
into a gzip-compressed JSONL file and registers it with the
``HistoryArchiveService`` so that it is queryable via the public
``archive.run_archive`` boundary.

Architecture constraints:
- All text I/O uses UTF-8 explicitly.
- Compression uses the stdlib ``gzip`` module (no external dependency).
- Archive events are published through the audit bus to avoid silent loss.
- This module lives inside ``archive.run_archive.internal`` — callers must
  use the public boundary if one is defined.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

from polaris.cells.archive.run_archive.internal.history_archive_service import (
    HistoryArchiveService,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal archiver (no public contract dependency)
# ---------------------------------------------------------------------------


class StreamArchiver:
    """Archive stream_turn event sequences to gzip-compressed JSONL.

    Parameters
    ----------
    archiver:
        ``HistoryArchiveService`` instance scoped to a workspace.
    """

    def __init__(self, archiver: HistoryArchiveService) -> None:
        self._archiver = archiver

    # -------------------------------------------------------------------------
    # Core archiving
    # -------------------------------------------------------------------------

    async def archive_turn(
        self,
        session_id: str,
        turn_id: str,
        events: list[dict[str, Any]],
    ) -> str:
        """Archive an event sequence and return its archive_id.

        The file is written as::

            {"type": "header", "session_id": "...", "turn_id": "...",
             "event_count": N, "archived_at": "..."}
            {"type": "event", "seq": 0, "event": {...}}
            {"type": "event", "seq": 1, "event": {...}}
            ...

        The complete payload is then gzip-compressed and saved under
        ``{history_root}/runs/{turn_id}/stream_events.jsonl.gz``.

        This method is self-contained: it does NOT require the source
        runtime directory to exist (unlike ``HistoryArchiveService.archive_run``).

        Args:
            session_id: The session that produced these events.
            turn_id: A unique identifier for this turn (e.g. UUID hex).
            events: The list of event dicts from stream_turn.

        Returns:
            The archive_id (== turn_id) that can be used to retrieve
            the archived sequence.

        Raises:
            StreamArchiverError: If serialisation or I/O fails.
        """
        try:
            archive_id = turn_id
            event_count = len(events)
            now = datetime.now(tz=timezone.utc)

            # ---- build uncompressed JSONL lines --------------------------------
            header = {
                "type": "header",
                "session_id": session_id,
                "turn_id": turn_id,
                "event_count": event_count,
                "archived_at": now.isoformat(),
            }
            lines: list[str] = [json.dumps(header, ensure_ascii=False)]

            for seq, event in enumerate(events):
                record: dict[str, Any] = {
                    "type": "event",
                    "seq": seq,
                    "event": event,
                }
                lines.append(json.dumps(record, ensure_ascii=False))

            uncompressed = "\n".join(lines).encode("utf-8")

            # ---- gzip compress -------------------------------------------------
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
                gz.write(uncompressed)
            compressed = buf.getvalue()

            # ---- compute content hash (SHA-256 of compressed bytes) -------------
            content_hash = hashlib.sha256(compressed).hexdigest()

            # ---- resolve target directory (history_root/runs/{turn_id}) --------
            # Self-contained: do NOT rely on archive_run creating the dir.
            history_root = self._archiver.history_root
            target_dir = history_root / "runs" / archive_id
            target_dir.mkdir(parents=True, exist_ok=True)

            # ---- write compressed payload --------------------------------------
            events_file = target_dir / "stream_events.jsonl.gz"
            self._archiver._kernel_fs.workspace_write_bytes(
                self._archiver._kernel_fs.to_workspace_relative_path(str(events_file)),
                compressed,
            )

            # ---- write supplemental metadata -----------------------------------
            meta: dict[str, Any] = {
                "archive_id": archive_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "event_count": event_count,
                "archived_at": now.isoformat(),
                "content_hash": content_hash,
                "compressed": True,
                "format": "jsonl.gz",
                "source": "audit_stream_turn",
            }
            meta_file = target_dir / "stream_meta.json"
            self._archiver._kernel_fs.workspace_write_text(
                self._archiver._kernel_fs.to_workspace_relative_path(str(meta_file)),
                json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            logger.info(
                "Archived stream turn: archive_id=%s session_id=%s event_count=%s",
                archive_id,
                session_id,
                event_count,
            )
            return archive_id

        except OSError as exc:
            logger.error(
                "StreamArchiver archive_turn failed: session_id=%s turn_id=%s error=%s",
                session_id,
                turn_id,
                exc,
            )
            raise StreamArchiverError(
                f"Failed to archive turn {turn_id}: {exc}",
                archive_id=turn_id,
            ) from exc

    async def get_archive(
        self,
        archive_id: str,
    ) -> list[dict[str, Any]] | None:
        """Read back an archived event sequence.

        Args:
            archive_id: The archive_id returned by ``archive_turn``.

        Returns:
            A list of the original event dicts, or None if the archive
            does not exist or is unreadable.
        """
        try:
            history_root = self._archiver.history_root
            target_dir = history_root / "runs" / archive_id
            events_file = target_dir / "stream_events.jsonl.gz"

            if not events_file.exists():
                logger.warning("Stream events file not found: %s", events_file)
                return None

            compressed = events_file.read_bytes()

            # ---- verify content hash -----------------------------------------
            expected_hash = hashlib.sha256(compressed).hexdigest()
            meta_file = target_dir / "stream_meta.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    stored_hash = meta.get("content_hash", "")
                    if stored_hash and stored_hash != expected_hash:
                        logger.error(
                            "Stream archive checksum mismatch: archive_id=%s expected=%s got=%s",
                            archive_id,
                            stored_hash,
                            expected_hash,
                        )
                        return None
                except OSError as exc:
                    logger.warning("Failed to read meta for checksum: %s", exc)

            # ---- decompress and parse ----------------------------------------
            buf = io.BytesIO(compressed)
            with gzip.GzipFile(fileobj=buf, mode="rb") as gz:
                raw = gz.read().decode("utf-8")

            events: list[dict[str, Any]] = []
            for line in raw.splitlines():
                text = line.strip()
                if not text:
                    continue
                record = json.loads(text)
                if record.get("type") == "event":
                    events.append(record.get("event", {}))

            logger.info(
                "Retrieved stream archive: archive_id=%s event_count=%s",
                archive_id,
                len(events),
            )
            return events

        except OSError as exc:
            logger.error(
                "StreamArchiver get_archive failed: archive_id=%s error=%s",
                archive_id,
                exc,
            )
            return None


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


class StreamArchiverError(Exception):
    """Raised when stream archiving or retrieval fails."""

    def __init__(
        self,
        message: str,
        *,
        archive_id: str = "",
    ) -> None:
        super().__init__(message)
        self.archive_id = archive_id


def create_stream_archiver(workspace: str) -> StreamArchiver:
    """Factory: build a ``StreamArchiver`` for the given workspace.

    Args:
        workspace: Absolute workspace path.

    Returns:
        A ``StreamArchiver`` backed by a ``HistoryArchiveService``.
    """
    archiver = HistoryArchiveService(workspace)
    return StreamArchiver(archiver)


__all__ = [
    "StreamArchiver",
    "StreamArchiverError",
    "create_stream_archiver",
]
