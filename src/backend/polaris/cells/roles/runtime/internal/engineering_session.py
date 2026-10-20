"""Single SQLite owner for engineering runs and process-scoped workspace locks.

The database and lock live in the host control domain. Model-facing tools must
exclude this directory from both direct writes and command sandbox mounts.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import stat
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
)


class EngineeringSessionConflictError(ValueError):
    """A request identity was reused for different content or tool execution."""


class WorkspaceBusyError(BlockingIOError):
    """Another live writer owns the workspace OS lock."""


class EngineeringSessionStore:
    """Persist exactly one workspace's engineering runs; never write legacy state."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        self.control_root = self.workspace / ".polaris" / "runtime" / "engineering"
        self.database_path = self.control_root / "sessions.sqlite3"
        self.lock_path = self.control_root / "workspace.lock"
        self._control_identities: dict[Path, tuple[int, int]] = {}
        current = self.workspace
        for part in (".polaris", "runtime", "engineering"):
            current /= part
            with suppress(FileExistsError):
                current.mkdir(mode=0o700)
            metadata = current.lstat()
            if not stat.S_ISDIR(metadata.st_mode):
                raise PermissionError(f"engineering control directory is not a real directory: {current}")
            self._control_identities[current] = (metadata.st_dev, metadata.st_ino)
        self.control_root.chmod(0o700)
        self._database_identity = self._prepare_control_file(self.database_path)
        self._lock_identity = self._prepare_control_file(self.lock_path)
        with self._transaction() as connection:
            connection.execute("PRAGMA encoding='UTF-8'")
            connection.execute("""CREATE TABLE IF NOT EXISTS runs (
                session_id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
                role TEXT NOT NULL, instruction TEXT NOT NULL, workflow TEXT NOT NULL,
                required_checks TEXT NOT NULL, scope TEXT NOT NULL, limits TEXT NOT NULL,
                engine_version TEXT NOT NULL, request_key TEXT UNIQUE,
                status TEXT NOT NULL, text TEXT NOT NULL, verification TEXT NOT NULL,
                checks TEXT NOT NULL, workspace_revision INTEGER NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS events (
                session_id TEXT NOT NULL REFERENCES runs(session_id), seq INTEGER NOT NULL,
                kind TEXT NOT NULL, role TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY(session_id, seq)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS tool_calls (
                session_id TEXT NOT NULL REFERENCES runs(session_id), turn_seq INTEGER NOT NULL,
                call_id TEXT NOT NULL, tool TEXT NOT NULL, arguments TEXT NOT NULL,
                status TEXT NOT NULL, observation TEXT, started_at REAL NOT NULL, observed_at REAL,
                PRIMARY KEY(session_id, turn_seq, call_id)
            )""")

    def _assert_control_directory(self) -> None:
        for path, identity in self._control_identities.items():
            metadata = path.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != identity:
                raise PermissionError(f"engineering control directory changed: {path}")

    def _prepare_control_file(self, path: Path) -> tuple[int, int]:
        self._assert_control_directory()
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except OSError as error:
            raise PermissionError(f"unsafe engineering control file: {path}") from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise PermissionError(f"unsafe engineering control file: {path}")
            os.fchmod(descriptor, 0o600)
            return metadata.st_dev, metadata.st_ino
        finally:
            os.close(descriptor)

    def _assert_workspace(self, workspace: Path) -> None:
        if Path(workspace).expanduser().resolve() != self.workspace:
            raise PermissionError("engineering session workspace mismatch")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._assert_control_directory()
        metadata = self.database_path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != self._database_identity:
            raise PermissionError("engineering control database changed")
        connection = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _require_run(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM runs WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown engineering session: {session_id}")
        return row

    def create_run(
        self,
        command: RoleRunCommand,
        *,
        scope: PermissionScope,
        limits: RunLimits,
        engine_version: str,
        request_key: str | None,
    ) -> str:
        self._assert_workspace(command.workspace)
        if not command.session_id.strip() or not command.instruction.strip():
            raise ValueError("session_id and instruction must be non-empty")
        if command.role not in {"pm", "chief_engineer", "director", "qa"}:
            raise ValueError("unsupported engineering role")
        if command.workflow not in {"direct", "collaborative"}:
            raise ValueError("unsupported engineering workflow")
        if not engine_version.strip() or (request_key is not None and not request_key.strip()):
            raise ValueError("engine_version and supplied request_key must be non-empty")
        if limits.deadline is not None and not math.isfinite(limits.deadline):
            raise ValueError("deadline must be a finite absolute epoch")
        if limits.max_output_bytes <= 0 or limits.max_tool_output_bytes <= 0:
            raise ValueError("output limits must be positive")
        payload = {
            "workspace": str(self.workspace),
            "role": command.role,
            "instruction": command.instruction,
            "workflow": command.workflow,
            "required_checks": self._json(command.required_checks),
            "scope": self._json(
                {
                    "read_roots": [str(Path(p).expanduser().resolve()) for p in scope.read_roots],
                    "write_roots": [str(Path(p).expanduser().resolve()) for p in scope.write_roots],
                    "enabled_tools": sorted(scope.enabled_tools),
                    "network_allowed": scope.network_allowed,
                }
            ),
            "limits": self._json(asdict(limits)),
            "engine_version": engine_version,
        }
        with self._transaction() as connection:
            identity_row = connection.execute("SELECT * FROM runs WHERE session_id=?", (command.session_id,)).fetchone()
            if identity_row is not None and not all(identity_row[key] == value for key, value in payload.items()):
                raise EngineeringSessionConflictError("engineering session_id conflict")
            if request_key is not None:
                existing = connection.execute("SELECT * FROM runs WHERE request_key=?", (request_key,)).fetchone()
                if existing is not None:
                    if all(existing[key] == value for key, value in payload.items()):
                        return str(existing["session_id"])
                    raise EngineeringSessionConflictError("engineering request_key conflict")
            existing = connection.execute("SELECT * FROM runs WHERE session_id=?", (command.session_id,)).fetchone()
            if existing is not None:
                if existing["request_key"] == request_key and all(
                    existing[key] == value for key, value in payload.items()
                ):
                    return command.session_id
                raise EngineeringSessionConflictError("engineering session_id conflict")
            now = time.time()
            values = {
                "session_id": command.session_id,
                **payload,
                "request_key": request_key,
                "status": "pending",
                "text": "",
                "verification": "not_run",
                "checks": "[]",
                "workspace_revision": 0,
                "created_at": now,
                "updated_at": now,
            }
            connection.execute(
                "INSERT INTO runs (" + ",".join(values) + ") VALUES (" + ",".join("?" for _ in values) + ")",
                tuple(values.values()),
            )
        return command.session_id

    def load_run(self, session_id: str, *, workspace: Path) -> dict[str, Any]:
        self._assert_workspace(workspace)
        with self._connection() as connection:
            row = dict(self._require_run(connection, session_id))
        if row["workspace"] != str(self.workspace):
            raise PermissionError("stored engineering session workspace mismatch")
        for field in ("required_checks", "scope", "limits", "checks"):
            row[field] = json.loads(row[field])
        return row

    def _append_event(
        self, connection: sqlite3.Connection, session_id: str, *, kind: str, role: str, payload: Mapping[str, object]
    ) -> int:
        self._require_run(connection, session_id)
        seq = int(
            connection.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE session_id=?", (session_id,)
            ).fetchone()[0]
        )
        connection.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?)",
            (session_id, seq, kind, role, self._json(dict(payload)), time.time()),
        )
        return seq

    def append_event(self, session_id: str, *, kind: str, role: str, payload: Mapping[str, object]) -> int:
        with self._transaction() as connection:
            return self._append_event(connection, session_id, kind=kind, role=role, payload=payload)

    def load_events(self, session_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            self._require_run(connection, session_id)
            rows = connection.execute(
                "SELECT seq,kind,role,payload,created_at FROM events WHERE session_id=? ORDER BY seq", (session_id,)
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def append_message(self, session_id: str, *, role: str, message: Mapping[str, object]) -> int:
        return self.append_event(session_id, kind="message", role=role, payload={"message": dict(message)})

    def load_message_history(self, session_id: str) -> list[dict[str, Any]]:
        return [event["payload"]["message"] for event in self.load_events(session_id) if event["kind"] == "message"]

    def record_tool_dispatch(
        self,
        session_id: str,
        *,
        turn_seq: int,
        call_id: str,
        tool: str,
        arguments: Mapping[str, object],
        role: str,
    ) -> None:
        if turn_seq < 1 or not call_id or not tool:
            raise ValueError("tool dispatch requires a positive turn_seq, call_id and tool")
        with self._transaction() as connection:
            self._require_run(connection, session_id)
            try:
                connection.execute(
                    "INSERT INTO tool_calls VALUES (?,?,?,?,?,'started',NULL,?,NULL)",
                    (session_id, turn_seq, call_id, tool, self._json(dict(arguments)), time.time()),
                )
            except sqlite3.IntegrityError as error:
                raise EngineeringSessionConflictError("engineering tool dispatch conflict") from error
            self._append_event(
                connection,
                session_id,
                kind="tool_started",
                role=role,
                payload={"turn_seq": turn_seq, "call_id": call_id, "tool": tool, "arguments": dict(arguments)},
            )

    def record_tool_observation(
        self,
        session_id: str,
        *,
        turn_seq: int,
        observation: ToolObservation,
        role: str,
    ) -> None:
        with self._transaction() as connection:
            self._require_run(connection, session_id)
            row = connection.execute(
                "SELECT tool,status FROM tool_calls WHERE session_id=? AND turn_seq=? AND call_id=?",
                (session_id, turn_seq, observation.call_id),
            ).fetchone()
            if row is None or row["tool"] != observation.tool:
                raise ValueError("tool observation has no matching dispatch")
            if row["status"] != "started":
                raise ValueError("tool dispatch was already observed")
            payload = asdict(observation)
            connection.execute(
                "UPDATE tool_calls SET status='observed',observation=?,observed_at=? WHERE session_id=? AND turn_seq=? AND call_id=?",
                (self._json(payload), time.time(), session_id, turn_seq, observation.call_id),
            )
            self._append_event(
                connection, session_id, kind="tool_observed", role=role, payload={"turn_seq": turn_seq, **payload}
            )

    def pending_tool_calls(self, session_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            self._require_run(connection, session_id)
            rows = connection.execute(
                "SELECT * FROM tool_calls WHERE session_id=? AND status='started' ORDER BY started_at,turn_seq,call_id",
                (session_id,),
            ).fetchall()
        return [{**dict(row), "arguments": json.loads(row["arguments"])} for row in rows]

    def update_run(self, session_id: str, **fields: object) -> None:
        allowed = {"status", "text", "verification", "checks", "workspace_revision"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("unsupported engineering run update")
        if "status" in fields and fields["status"] not in {
            "pending",
            "running",
            "completed",
            "failed",
            "cancelled",
            "interrupted",
        }:
            raise ValueError("invalid engineering run status")
        if "verification" in fields and fields["verification"] not in {"passed", "failed", "not_run", "unknown"}:
            raise ValueError("invalid engineering verification status")
        updates = dict(fields)
        if "checks" in updates:
            checks = updates["checks"]
            if not isinstance(checks, (tuple, list)):
                raise ValueError("checks must be an observation sequence")
            updates["checks"] = self._json(
                [asdict(item) if isinstance(item, ToolObservation) else item for item in checks]
            )
        with self._transaction() as connection:
            row = self._require_run(connection, session_id)
            if "workspace_revision" in updates:
                revision = updates["workspace_revision"]
                if type(revision) is not int or revision < row["workspace_revision"]:
                    raise ValueError("workspace_revision must be a monotonic integer")
            updates["updated_at"] = time.time()
            connection.execute(
                "UPDATE runs SET " + ",".join(f"{key}=?" for key in updates) + " WHERE session_id=?",
                (*updates.values(), session_id),
            )

    @contextmanager
    def acquire_workspace(self, workspace: Path) -> Iterator[None]:
        """Acquire nonblocking Linux/WSL flock; process death releases ownership."""
        self._assert_workspace(workspace)
        try:
            import fcntl
        except ImportError as error:
            raise NotImplementedError("engineering workspace locking requires Linux/WSL flock") from error
        self._assert_control_directory()
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            metadata = os.fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino) != self._lock_identity:
                raise PermissionError("engineering control workspace lock changed")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WorkspaceBusyError("engineering workspace already has an active writer") from error
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
