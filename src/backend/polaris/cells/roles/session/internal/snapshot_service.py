"""Session Snapshot Service — persists turn-level session snapshots as JSONL.

Architecture constraint: no file-IO side-effects during session writes beyond
JSONL append. Snapshot is called from RoleConsoleHost.stream_turn() complete event.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from polaris.cells.roles.session.internal.storage_paths import (
    resolve_preferred_logical_prefix,
)
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.utils.time_utils import utc_now as _utc_now

logger = logging.getLogger(__name__)

_MAX_SNAPSHOTS_PER_SESSION = 50


def _compute_fingerprint(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]


@dataclass
class SessionSnapshot:
    """Turn-level snapshot of a role session."""

    session_id: str
    messages: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    fingerprints: list[str]
    timestamp: str
    snapshot_id: str = field(default_factory=lambda: "")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionSnapshot:
        return cls(**data)


class SnapshotService:
    """Persist session snapshots as newline-delimited JSONL.

    Stores one `.jsonl` file per session under
    `runtime/role_sessions/<session_id>/snapshots.jsonl`.

    TTL policy: after each append, trim the file to the most recent
    _MAX_SNAPSHOTS_PER_SESSION entries so disk usage stays bounded.
    """

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace).resolve()
        self._kernel_fs = KernelFileSystem(str(self._workspace), LocalFileSystemAdapter())
        self._base_rel = resolve_preferred_logical_prefix(
            self._kernel_fs,
            runtime_prefix="runtime/role_sessions",
            workspace_fallback_prefix="workspace/runtime/role_sessions",
        )

    # -------------------------------------------------------------------------
    # Path helpers
    # -------------------------------------------------------------------------

    def _snapshots_rel(self, session_id: str) -> str:
        return f"{self._base_rel}/{session_id}/snapshots.jsonl"

    def _snapshots_path(self, session_id: str) -> Path:
        return self._kernel_fs.resolve_path(self._snapshots_rel(session_id))

    # -------------------------------------------------------------------------
    # Core snapshot API
    # -------------------------------------------------------------------------

    def snapshot(self, session_id: str) -> SessionSnapshot:
        """Capture a point-in-time snapshot of a session and persist it.

        Reads current messages/artifacts from the session DB via RoleSessionService,
        then appends a single JSONL record. Trims to _MAX_SNAPSHOTS_PER_SESSION.

        Args:
            session_id: The session to snapshot.

        Returns:
            The created SessionSnapshot (with populated snapshot_id and timestamp).
        """
        normalized = str(session_id or "").strip()
        if not normalized:
            logger.warning("snapshot called with empty session_id")
            return SessionSnapshot(
                session_id="",
                messages=[],
                artifacts=[],
                fingerprints=[],
                timestamp=_utc_now().isoformat(),
                snapshot_id="",
            )

        messages: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        fingerprints: list[str] = []

        # Pull messages from the session DB.
        try:
            from polaris.cells.roles.session.internal.conversation import (
                ConversationMessage,
                get_session_local,
            )

            session_local = get_session_local()
            with session_local() as db:
                from sqlalchemy import select

                stmt = (
                    select(ConversationMessage)
                    .where(ConversationMessage.conversation_id == normalized)
                    .order_by(ConversationMessage.sequence.asc())
                )
                rows = db.execute(stmt).scalars().all()

                for row in rows:
                    try:
                        msg_dict: dict[str, Any] = {
                            "id": str(row.id or ""),
                            "role": str(row.role or ""),
                            "content": str(row.content or ""),
                            "thinking": str(row.thinking or "") if row.thinking else "",
                            "sequence": int(row.sequence or 0),
                            "created_at": (row.created_at.isoformat() if row.created_at else ""),
                            "meta": {},
                        }
                    except (RuntimeError, ValueError) as exc:
                        logger.debug(
                            "Failed to serialise message row %s: %s",
                            getattr(row, "id", "?"),
                            exc,
                        )
                        continue

                    messages.append(msg_dict)
                    fingerprints.append(_compute_fingerprint(str(msg_dict["content"])))

            # Collect artifact content for fingerprints.
            for msg in messages:
                if msg.get("meta") and isinstance(msg["meta"], dict):
                    meta_artifacts = msg["meta"].get("artifacts") or []
                    if isinstance(meta_artifacts, list):
                        for art in meta_artifacts:
                            art_content = str(art.get("content") or "")
                            artifacts.append(
                                {
                                    "artifact_id": str(art.get("artifact_id") or art.get("id", "")),
                                    "title": str(art.get("title", "")),
                                    "content": art_content[:2000],  # cap for fingerprinting
                                }
                            )

        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to load session %s messages for snapshot: %s",
                normalized,
                exc,
            )

        now = _utc_now()
        snapshot_id = hashlib.sha256(f"{normalized}:{now.isoformat()}".encode()).hexdigest()[:16]

        snap = SessionSnapshot(
            session_id=normalized,
            messages=messages,
            artifacts=artifacts,
            fingerprints=fingerprints,
            timestamp=now.isoformat(),
            snapshot_id=snapshot_id,
        )

        self._append_snapshot(snap)
        self._trim_snapshots(normalized)

        logger.debug(
            "Snapshot %s created for session %s (%d messages)",
            snapshot_id,
            normalized,
            len(messages),
        )
        return snap

    def list_snapshots(self, session_id: str, *, limit: int = 50) -> list[SessionSnapshot]:
        """Return the most recent snapshots for a session.

        Args:
            session_id: Session to query.
            limit: Maximum entries to return (capped at _MAX_SNAPSHOTS_PER_SESSION).

        Returns:
            List of SessionSnapshot objects, newest-first.
        """
        normalized = str(session_id or "").strip()
        if not normalized:
            return []

        rel_path = self._snapshots_rel(normalized)
        if not self._kernel_fs.exists(rel_path):
            return []

        try:
            raw = self._kernel_fs.read_text(rel_path, encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read snapshots file %s: %s", rel_path, exc)
            return []

        results: list[SessionSnapshot] = []
        for line in raw.splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                data = json.loads(text)
                if not isinstance(data, dict):
                    continue
                results.append(SessionSnapshot.from_dict(data))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.debug("Malformed snapshot line in %s: %s", rel_path, exc)

        safe_limit = max(1, min(int(limit), _MAX_SNAPSHOTS_PER_SESSION))
        return results[-safe_limit:] if len(results) > safe_limit else results

    def get_snapshot(self, snapshot_id: str) -> SessionSnapshot | None:
        """Retrieve a specific snapshot by its snapshot_id.

        Performs a linear scan of all session snapshot files.

        Args:
            snapshot_id: The snapshot UUID (first 16 hex chars).

        Returns:
            SessionSnapshot or None if not found.
        """
        token = str(snapshot_id or "").strip()
        if not token:
            return None

        # Scan all session snapshot files.
        base = self._kernel_fs.resolve_path(self._base_rel)
        if not base.is_dir():
            return None

        for session_dir in base.iterdir():
            if not session_dir.is_dir():
                continue
            snap_file = session_dir / "snapshots.jsonl"
            if not snap_file.is_file():
                continue
            try:
                snap_rel = self._kernel_fs.to_logical_path(str(snap_file))
                text = self._kernel_fs.read_text(snap_rel, encoding="utf-8")
            except OSError as exc:
                logger.debug("Cannot read %s: %s", snap_file, exc)
                continue

            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                    if isinstance(data, dict) and data.get("snapshot_id", "")[:16] == token[:16]:
                        return SessionSnapshot.from_dict(data)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue

        return None

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _append_snapshot(self, snap: SessionSnapshot) -> None:
        """Append one snapshot record as a JSONL line."""
        rel_path = self._snapshots_rel(snap.session_id)
        line = json.dumps(snap.to_dict(), ensure_ascii=False)
        try:
            self._kernel_fs.append_text(rel_path, line + "\n", encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Failed to append snapshot for session %s: %s",
                snap.session_id,
                exc,
            )

    def _trim_snapshots(self, session_id: str) -> None:
        """Keep only the most recent _MAX_SNAPSHOTS_PER_SESSION entries."""
        rel_path = self._snapshots_rel(session_id)
        if not self._kernel_fs.exists(rel_path):
            return

        try:
            raw = self._kernel_fs.read_text(rel_path, encoding="utf-8")
        except OSError as exc:
            logger.warning("Trim read failed for %s: %s", rel_path, exc)
            return

        all_lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if len(all_lines) <= _MAX_SNAPSHOTS_PER_SESSION:
            return

        # Keep the most recent entries.
        kept = all_lines[-_MAX_SNAPSHOTS_PER_SESSION:]
        try:
            self._kernel_fs.write_text(
                rel_path,
                "\n".join(kept) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "Failed to trim snapshots for session %s: %s",
                session_id,
                exc,
            )


__all__ = ["SessionSnapshot", "SnapshotService"]
