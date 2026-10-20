"""Single SQLite owner for engineering runs and process-scoped workspace locks.

The database and lock live in the host control domain. Model-facing tools must
exclude this directory from both direct writes and command sandbox mounts.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from threading import RLock
from typing import Any, cast

from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
)

logger = logging.getLogger(__name__)


class EngineeringSessionConflictError(ValueError):
    """A request identity was reused for different content or tool execution."""


class WorkspaceBusyError(BlockingIOError):
    """Another live writer owns the workspace OS lock."""


class EngineeringSessionStore:
    """Persist exactly one workspace's engineering runs; never write legacy state."""

    def __init__(
        self, workspace: Path, *, on_committed_events: Callable[[tuple[dict[str, Any], ...]], None] | None = None
    ) -> None:
        self._on_committed_events = on_committed_events
        self._transaction_events: dict[sqlite3.Connection, list[dict[str, Any]]] = {}
        self._commit_lock = RLock()
        self.event_delivery_error: str | None = None
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
            columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            if "permission_profile_id" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN permission_profile_id TEXT")
            if "require_qa" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN require_qa INTEGER NOT NULL DEFAULT 0")
            if "verification_output_roots" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN verification_output_roots TEXT")
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
        # The lock orders post-commit callbacks from capture and runtime threads.
        # The batch exists only during this call; SQLite owns all durable facts.
        with self._commit_lock:
            batch: list[dict[str, Any]] = []
            with self._connection() as connection:
                self._transaction_events[connection] = batch
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    self._transaction_events.pop(connection, None)
            if batch and self._on_committed_events is not None:
                try:
                    self._on_committed_events(tuple(batch))
                except Exception as error:  # noqa: BLE001 - delivery cannot undo committed effects or cause replay.
                    self.event_delivery_error = f"{type(error).__name__}: {error}"
                    logger.error("Committed engineering event delivery failed: %s", self.event_delivery_error)

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
        permission_profile_id: str | None = None,
        require_qa: bool = False,
        verification_output_roots: tuple[Path, ...] | None = None,
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
            "permission_profile_id": permission_profile_id,
            "require_qa": int(require_qa),
            "verification_output_roots": self._json([str(root) for root in verification_output_roots])
            if verification_output_roots is not None
            else None,
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

    def find_request(self, request_key: str) -> str | None:
        """Read the existing idempotency owner without creating another run."""
        with self._connection() as connection:
            row = connection.execute("SELECT session_id FROM runs WHERE request_key=?", (request_key,)).fetchone()
        return str(row["session_id"]) if row is not None else None

    def load_run(self, session_id: str, *, workspace: Path) -> dict[str, Any]:
        self._assert_workspace(workspace)
        with self._connection() as connection:
            connection.execute("BEGIN")
            row = dict(self._require_run(connection, session_id))
            acceptance = self._acceptance(connection, row)
            commands = self._verification_commands(connection, row)
        if row["workspace"] != str(self.workspace):
            raise PermissionError("stored engineering session workspace mismatch")
        for field in ("required_checks", "scope", "limits", "checks"):
            row[field] = json.loads(row[field])
        row.update(acceptance)
        row["verification_commands"] = commands
        row["verification_output_roots"] = (
            json.loads(row["verification_output_roots"]) if row["verification_output_roots"] is not None else None
        )
        return row

    def load_snapshot(self, session_id: str, *, workspace: Path, event_limit: int = 200) -> dict[str, Any]:
        """Read row, history window and its watermark from one SQLite snapshot."""
        self._assert_workspace(workspace)
        if not 1 <= event_limit <= 1000:
            raise ValueError("event_limit must be between 1 and 1000")
        with self._connection() as connection:
            connection.execute("BEGIN")
            row = dict(self._require_run(connection, session_id))
            if row["workspace"] != str(self.workspace):
                raise PermissionError("stored engineering session workspace mismatch")
            last_seq = int(
                connection.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM events WHERE session_id=?", (session_id,)
                ).fetchone()[0]
            )
            events = connection.execute(
                "SELECT seq,kind,role,payload,created_at FROM events WHERE session_id=? ORDER BY seq DESC LIMIT ?",
                (session_id, event_limit),
            ).fetchall()
            acceptance = self._acceptance(connection, row)
            commands = self._verification_commands(connection, row)
            roles = connection.execute(
                "SELECT seq,kind,role,payload,created_at FROM events WHERE session_id=? AND kind IN ('role_started','role_finished') ORDER BY seq",
                (session_id,),
            ).fetchall()
        for field in ("required_checks", "scope", "limits", "checks"):
            row[field] = json.loads(row[field])
        row.update(acceptance)
        row["verification_commands"] = commands
        row["verification_output_roots"] = (
            json.loads(row["verification_output_roots"]) if row["verification_output_roots"] is not None else None
        )
        return {
            "run": row,
            "events": [{**dict(event), "payload": json.loads(event["payload"])} for event in reversed(events)],
            "role_events": [{**dict(event), "payload": json.loads(event["payload"])} for event in roles],
            "last_seq": last_seq,
        }

    def _append_event(
        self, connection: sqlite3.Connection, session_id: str, *, kind: str, role: str, payload: Mapping[str, object]
    ) -> int:
        self._require_run(connection, session_id)
        seq = int(
            connection.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE session_id=?", (session_id,)
            ).fetchone()[0]
        )
        created_at = time.time()
        encoded = self._json(dict(payload))
        connection.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?)",
            (session_id, seq, kind, role, encoded, created_at),
        )
        self._transaction_events[connection].append(
            {
                "session_id": session_id,
                "workspace": str(self.workspace),
                "seq": seq,
                "kind": kind,
                "role": role,
                "payload": json.loads(encoded),
                "created_at": created_at,
            }
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

    def load_message_history(
        self, session_id: str, *, role: str | None = None, after_seq: int = 0
    ) -> list[dict[str, Any]]:
        return [
            event["payload"]["message"]
            for event in self.load_events(session_id)
            if event["kind"] == "message" and event["seq"] > after_seq and (role is None or event["role"] == role)
        ]

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
            self._record_tool_observation(connection, session_id, turn_seq=turn_seq, observation=observation, role=role)

    def _record_tool_observation(
        self, connection: sqlite3.Connection, session_id: str, *, turn_seq: int, observation: ToolObservation, role: str
    ) -> None:
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

    @staticmethod
    def _valid_commands(commands: object, *, allow_empty: bool = True) -> bool:
        return (
            isinstance(commands, (list, tuple))
            and (allow_empty or bool(commands))
            and all(isinstance(command, str) and command.strip() and "\0" not in command for command in commands)
        )

    @staticmethod
    def _merge_commands(required: list[str], selected: list[str] | tuple[str, ...]) -> list[str]:
        commands = list(required)
        for command in selected:
            if command not in commands:
                commands.append(command)
        return commands

    def _verification_commands(self, connection: sqlite3.Connection, row: Mapping[str, Any] | sqlite3.Row) -> list[str]:
        required = json.loads(row["required_checks"])
        selected = connection.execute(
            "SELECT payload FROM events WHERE session_id=? AND kind='verification_started' ORDER BY seq DESC LIMIT 1",
            (row["session_id"],),
        ).fetchone()
        if selected is None:
            return required
        commands = json.loads(selected["payload"]).get("input_binding", {}).get("commands")
        if not self._valid_commands(commands) or list(commands[: len(required)]) != required:
            raise ValueError("saved verification commands do not preserve original required checks")
        return list(commands)

    def _has_pending_tools(
        self, connection: sqlite3.Connection, session_id: str, *, ignore: tuple[int, str] | None = None
    ) -> bool:
        reconciled = {
            (payload["turn_seq"], payload["call_id"])
            for event in connection.execute(
                "SELECT payload FROM events WHERE session_id=? AND kind='tool_reconciled'", (session_id,)
            )
            for payload in (json.loads(event["payload"]),)
        }
        return any(
            (row["turn_seq"], row["call_id"]) != ignore and (row["turn_seq"], row["call_id"]) not in reconciled
            for row in connection.execute(
                "SELECT turn_seq,call_id FROM tool_calls WHERE session_id=? AND status='started'", (session_id,)
            )
        )

    def _verification_parent(
        self, connection: sqlite3.Connection, session_id: str, requested_by: object, commands: list[str]
    ) -> sqlite3.Row | None:
        if (
            not isinstance(requested_by, (list, tuple))
            or len(requested_by) != 2
            or type(requested_by[0]) is not int
            or requested_by[0] < 1
            or not isinstance(requested_by[1], str)
            or not requested_by[1]
        ):
            return None
        parent = connection.execute(
            "SELECT * FROM tool_calls WHERE session_id=? AND turn_seq=? AND call_id=?",
            (session_id, *requested_by),
        ).fetchone()
        if parent is None or parent["tool"] != "verify_project":
            return None
        arguments = json.loads(parent["arguments"])
        if set(arguments) != {"commands"} or not self._valid_commands(arguments["commands"], allow_empty=False):
            return None
        required = json.loads(self._require_run(connection, session_id)["required_checks"])
        if self._merge_commands(required, arguments["commands"]) != commands:
            return None
        event = connection.execute(
            "SELECT role,payload FROM events WHERE session_id=? AND kind='tool_started' ORDER BY seq", (session_id,)
        ).fetchall()
        if not any(
            item["role"] == "director"
            and json.loads(item["payload"]).get("turn_seq") == requested_by[0]
            and json.loads(item["payload"]).get("call_id") == requested_by[1]
            for item in event
        ):
            return None
        return parent

    def begin_verification(
        self,
        session_id: str,
        *,
        commands: tuple[str, ...],
        workspace_revision: int,
        input_binding: Mapping[str, object],
        requested_by: tuple[int, str] | None = None,
    ) -> dict[str, Any]:
        """Commit accepted selection and invalidate old current evidence before effects."""
        if not self._valid_commands(commands, allow_empty=requested_by is None):
            raise ValueError("verification commands must be nonempty command strings")
        with self._transaction() as connection:
            row = self._require_run(connection, session_id)
            if type(workspace_revision) is not int or row["workspace_revision"] != workspace_revision:
                raise ValueError("verification workspace revision changed")
            effective = (
                self._merge_commands(json.loads(row["required_checks"]), commands)
                if requested_by is not None
                else list(commands)
            )
            if requested_by is not None:
                parent = self._verification_parent(connection, session_id, requested_by, effective)
                if parent is None or parent["status"] != "started":
                    raise ValueError("verification requires its exact active Director tool dispatch")
                if any(
                    json.loads(event["payload"]).get("input_binding", {}).get("requested_by") == list(requested_by)
                    for event in connection.execute(
                        "SELECT payload FROM events WHERE session_id=? AND kind='verification_started'", (session_id,)
                    )
                ):
                    raise ValueError("verification dispatch already has an attempt; recover its durable result")
            elif effective != self._verification_commands(connection, row):
                raise ValueError("verification commands do not match saved checks")
            if self._has_pending_tools(connection, session_id, ignore=requested_by):
                raise ValueError("unobserved tool effects block new verification")
            attempt = int(
                connection.execute(
                    "SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE session_id=?", (session_id,)
                ).fetchone()[0]
            )
            binding = {
                **input_binding,
                "commands": effective,
                "workspace_revision": workspace_revision,
                "attempt_seq": attempt,
            }
            if requested_by is not None:
                binding["requested_by"] = list(requested_by)
            connection.execute(
                "UPDATE runs SET verification='not_run',checks='[]',updated_at=? WHERE session_id=?",
                (time.time(), session_id),
            )
            current = dict(self._require_run(connection, session_id))
            summary = self._acceptance(connection, current, verification_starting=True)
            self._append_event(
                connection,
                session_id,
                kind="verification_started",
                role="director",
                payload={
                    "input_binding": binding,
                    "verification_commands": effective,
                    "verification": "not_run",
                    "checks": [],
                    **summary,
                },
            )
        return binding

    def _completion_observed(self, connection: sqlite3.Connection, session_id: str, payload: Mapping[str, Any]) -> bool:
        """Check exact attempt identities, never borrow a previous call-id receipt."""
        binding = payload.get("input_binding", {})
        checks = payload.get("checks", [])
        commands = binding.get("commands", [])
        if not self._valid_commands(commands, allow_empty=False) or not isinstance(checks, list):
            return False
        attempt = binding.get("attempt_seq")
        if attempt is None and checks:
            # Read-only compatibility for old completed Verifier events.
            parts = str(checks[0].get("call_id", "")).split("-")
            if len(parts) == 3 and parts[0] == "verification" and parts[1].isdigit():
                attempt = int(parts[1])
        if type(attempt) is not int or attempt < 1:
            return False
        started = connection.execute(
            "SELECT payload FROM events WHERE session_id=? AND seq=? AND kind='verification_started'",
            (session_id, attempt),
        ).fetchone()
        if started is None:
            return False
        start_binding = json.loads(started["payload"]).get("input_binding", {})
        if any(binding.get(key) != value for key, value in start_binding.items()):
            return False
        children = connection.execute(
            "SELECT * FROM tool_calls WHERE session_id=? AND turn_seq=?", (session_id, attempt)
        ).fetchall()
        if len(children) != len(checks) or len(checks) > len(commands):
            return False
        by_id = {child["call_id"]: child for child in children}
        for index, (command, check) in enumerate(zip(commands, checks, strict=False)):
            child = by_id.get(f"verification-{attempt}-{index}")
            if (
                not isinstance(check, dict)
                or child is None
                or child["tool"] != "execute_command"
                or child["status"] != "observed"
                or child["call_id"] != check.get("call_id")
                or json.loads(child["arguments"]).get("command") != command
                or json.loads(child["observation"]) != check
            ):
                return False
        status = payload.get("verification")
        if status in {"passed", "failed"}:
            if len(checks) != len(commands) or any(c.get("exit_code") is None or c.get("interrupted") for c in checks):
                return False
            passed = all(c.get("ok") is True and c.get("exit_code") == 0 for c in checks)
            if (status == "passed") != passed:
                return False
        return True

    def verification_completion_is_observed(self, session_id: str, payload: Mapping[str, Any]) -> bool:
        with self._connection() as connection:
            return self._completion_observed(connection, session_id, payload)

    def reconcile_verification_calls(self, session_id: str, *, max_output_bytes: int) -> None:
        """Recover only durable composite responses; never execute or infer a check."""
        from polaris.cells.roles.runtime.internal.engineering_verification import verification_observation

        with self._transaction() as connection:
            self._require_run(connection, session_id)
            completed = connection.execute(
                "SELECT seq,payload FROM events WHERE session_id=? AND kind='verification_completed' ORDER BY seq",
                (session_id,),
            ).fetchall()
            for event in completed:
                payload = json.loads(event["payload"])
                binding = payload.get("input_binding", {})
                requested_by = binding.get("requested_by")
                parent = self._verification_parent(connection, session_id, requested_by, binding.get("commands", []))
                if parent is None or not self._completion_observed(connection, session_id, payload):
                    continue
                observation = verification_observation(parent["call_id"], payload, max_output_bytes)
                if parent["status"] == "started":
                    self._record_tool_observation(
                        connection, session_id, turn_seq=parent["turn_seq"], observation=observation, role="director"
                    )
                elif parent["status"] != "observed" or json.loads(parent["observation"]) != asdict(observation):
                    continue
                messages = connection.execute(
                    "SELECT payload FROM events WHERE session_id=? AND kind='message' AND seq>?",
                    (session_id, parent["turn_seq"]),
                ).fetchall()
                present = False
                for message in messages:
                    value = json.loads(message["payload"]).get("message", {})
                    if value.get("tool_call_id") == parent["call_id"]:
                        with suppress(ValueError, TypeError):
                            present = json.loads(value.get("content", "")) == asdict(observation)
                        if present:
                            break
                if not present:
                    self._append_event(
                        connection,
                        session_id,
                        kind="message",
                        role="director",
                        payload={
                            "turn_seq": parent["turn_seq"],
                            "recovery_basis": "durable_verification_completed",
                            "message": {
                                "role": "tool",
                                "tool_call_id": parent["call_id"],
                                "content": self._json(asdict(observation)),
                            },
                        },
                    )

    def record_verification(
        self,
        session_id: str,
        *,
        workspace_revision: int,
        checks: tuple[ToolObservation, ...],
        verification: str,
        detail: str,
        input_binding: Mapping[str, object],
    ) -> bool:
        """Bind actual check observations and their current-input projection atomically."""
        if verification not in {"passed", "failed", "not_run", "unknown"}:
            raise ValueError("invalid engineering verification status")
        with self._transaction() as connection:
            row = self._require_run(connection, session_id)
            current = row["workspace_revision"] == workspace_revision
            projected = verification if current else "unknown"
            commands = self._verification_commands(connection, row)
            if projected in {"passed", "failed"} and (
                not commands or input_binding.get("commands") != commands or len(checks) != len(commands)
            ):
                raise ValueError("passed verification requires every saved check")
            payload = {
                "workspace_revision": workspace_revision,
                "verification": projected,
                "detail": detail if current else "Workspace revision changed during verification.",
                "input_binding": dict(input_binding),
                "checks": [asdict(check) for check in checks],
                "verification_commands": list(cast(list[str], input_binding.get("commands", commands))),
            }
            if projected in {"passed", "failed"} and not self._completion_observed(connection, session_id, payload):
                raise ValueError("verification requires actual matching observed commands from its exact attempt")
            connection.execute(
                "UPDATE runs SET verification=?,checks=?,updated_at=? WHERE session_id=?",
                (projected, self._json(payload["checks"]), time.time(), session_id),
            )
            self._append_event(connection, session_id, kind="verification_completed", role="director", payload=payload)
        return current

    def continue_role(self, session_id: str, *, role: str, feedback: str) -> None:
        """Reopen the same role with complete native history and ordinary feedback."""
        with self._transaction() as connection:
            self._require_run(connection, session_id)
            events = connection.execute(
                "SELECT seq,kind,payload FROM events WHERE session_id=? AND role=? ORDER BY seq", (session_id, role)
            ).fetchall()
            starts = [event for event in events if event["kind"] == "role_started"]
            if not starts:
                raise ValueError("role continuation has no prior role frame")
            last = starts[-1]
            payload = json.loads(last["payload"])
            self._append_event(connection, session_id, kind="role_started", role=role, payload=payload)
            for event in events:
                if event["seq"] > last["seq"] and event["kind"] == "message":
                    self._append_event(
                        connection, session_id, kind="message", role=role, payload=json.loads(event["payload"])
                    )
            self._append_event(
                connection,
                session_id,
                kind="message",
                role=role,
                payload={"message": {"role": "user", "content": feedback}},
            )

    def record_write_reconciliation(
        self,
        session_id: str,
        *,
        turn_seq: int,
        call_id: str,
        role: str,
        readback: ToolObservation,
    ) -> None:
        """Close only a specific continuation blocker; the original effect stays unknown."""
        if readback.tool != "read_file" or not readback.ok or readback.interrupted:
            raise ValueError("write reconciliation requires an actual successful readback")
        with self._transaction() as connection:
            row = self._require_run(connection, session_id)
            original = connection.execute(
                "SELECT tool,arguments,status FROM tool_calls WHERE session_id=? AND turn_seq=? AND call_id=?",
                (session_id, turn_seq, call_id),
            ).fetchone()
            observed = connection.execute(
                "SELECT arguments,observation FROM tool_calls WHERE session_id=? AND call_id=? AND status='observed'",
                (session_id, readback.call_id),
            ).fetchall()
            from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments_from_snapshot
            from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

            if original is None or ToolSpecRegistry.get_canonical(original["tool"]) != "write_file":
                raise ValueError("write reconciliation cannot waive other unknown tools")
            arguments = dict(
                normalize_tool_arguments_from_snapshot(
                    ToolSpecRegistry.capture_effective_spec(original["tool"]), json.loads(original["arguments"])
                )
            )
            if set(arguments) != {"file", "content"} or arguments["content"] != readback.content:
                raise ValueError("write reconciliation requires exact requested current content")
            if (
                original is None
                or original["status"] != "started"
                or not any(
                    json.loads(item["observation"]) == asdict(readback)
                    and json.loads(item["arguments"]).get("file") == arguments["file"]
                    for item in observed
                )
            ):
                raise ValueError("write reconciliation lacks its original dispatch or durable readback")
            payload = {
                "turn_seq": turn_seq,
                "call_id": call_id,
                "original_effect": "unknown",
                "continuation_basis": "current_file_matches_requested_write",
                "readback": asdict(readback),
            }
            self._append_event(connection, session_id, kind="tool_reconciled", role=role, payload=payload)
            self._append_event(
                connection,
                session_id,
                kind="message",
                role=role,
                payload={
                    "message": {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": self._json({"reconciliation": payload}),
                    }
                },
            )
            connection.execute(
                "UPDATE runs SET workspace_revision=?,verification='not_run',checks='[]',updated_at=? WHERE session_id=?",
                (row["workspace_revision"] + 1, time.time(), session_id),
            )

    def pending_tool_calls(self, session_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            self._require_run(connection, session_id)
            rows = connection.execute(
                "SELECT * FROM tool_calls WHERE session_id=? AND status='started' ORDER BY started_at,turn_seq,call_id",
                (session_id,),
            ).fetchall()
        reconciled = {
            (event["payload"]["turn_seq"], event["payload"]["call_id"])
            for event in self.load_events(session_id)
            if event["kind"] == "tool_reconciled"
        }
        return [
            {**dict(row), "arguments": json.loads(row["arguments"])}
            for row in rows
            if (row["turn_seq"], row["call_id"]) not in reconciled
        ]

    def _acceptance(
        self, connection: sqlite3.Connection, row: Mapping[str, Any], *, verification_starting: bool = False
    ) -> dict[str, object]:
        """Project existing durable checks and QA facts; no separate acceptance ledger."""
        events = connection.execute(
            "SELECT seq,kind,payload FROM events WHERE session_id=? AND kind IN ('verification_started','verification_completed','qa_review_started','qa_review_completed') ORDER BY seq",
            (row["session_id"],),
        ).fetchall()
        verification_seq = 0
        start_seq = 0
        verification_payload: dict[str, Any] = {}
        verdict = "not_run"
        review_binding = None
        for event in events:
            payload = json.loads(event["payload"])
            if event["kind"] == "verification_started":
                start_seq = event["seq"]
            elif event["kind"] == "verification_completed":
                verification_seq = event["seq"]
                verification_payload = payload
            elif event["kind"] == "qa_review_started":
                verdict = "unknown"
                review_binding = None
            else:
                binding = payload.get("input_binding", {})
                review_binding = binding
                verdict = (
                    payload.get("verdict", "unknown")
                    if (
                        binding.get("workspace_revision") == row["workspace_revision"]
                        and binding.get("verification_seq") == verification_seq
                    )
                    else "unknown"
                )
        if review_binding is not None and review_binding.get("verification_seq") != verification_seq:
            verdict = "unknown"
        if (verification_starting or start_seq > verification_seq) and verdict != "not_run":
            verdict = "unknown"
        required = bool(row["require_qa"])
        if verdict == "approved" and row["verification"] != "passed":
            verdict = "unknown"
        required_checks = self._verification_commands(connection, row)
        checks = json.loads(row["checks"])
        evidence = (
            bool(required_checks)
            and row["verification"] == "passed"
            and verification_payload.get("verification") == "passed"
            and verification_payload.get("workspace_revision") == row["workspace_revision"]
            and verification_payload.get("input_binding", {}).get("commands") == required_checks
            and len(checks) == len(required_checks)
            and all(
                check.get("ok") is True and check.get("exit_code") == 0 and not check.get("interrupted")
                for check in checks
            )
            and not self._has_pending_tools(connection, str(row["session_id"]))
        )
        accepted = row["status"] == "completed" and evidence and (not required or verdict == "approved")
        outcome = (
            "accepted"
            if accepted
            else "rejected"
            if required and verdict == "rejected"
            else "unknown"
            if row["verification"] == "unknown" or (required and verdict == "unknown")
            else "not_accepted"
        )
        return {
            "require_qa": required,
            "qa_verdict": verdict,
            "project_outcome": outcome,
            "acceptance_basis": ("independent_qa" if required else "checks_only") if accepted else None,
        }

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
            current = dict(self._require_run(connection, session_id))
            roles = dict.fromkeys(("pm", "chief_engineer", "director", "qa"), "not_called")
            for event in connection.execute(
                "SELECT kind,role,payload FROM events WHERE session_id=? AND kind IN ('role_started','role_finished') ORDER BY seq",
                (session_id,),
            ):
                if event["role"] in roles:
                    roles[event["role"]] = (
                        "running"
                        if event["kind"] == "role_started"
                        else json.loads(event["payload"]).get("status", "unknown")
                    )
            state = {
                key: current[key]
                for key in ("status", "text", "verification", "workspace_revision", "role", "workflow")
            }
            if current["status"] != "running":
                for role, status in roles.items():
                    if status == "running":
                        roles[role] = "unknown" if current["status"] == "completed" else str(current["status"])
            if current["verification"] == "passed" and current["workspace_revision"] != row["workspace_revision"]:
                # The historical check remains in SQL, but cannot certify the
                # newly written revision projected by this exact event.
                state["verification"] = "unknown"
            state.update(
                {
                    "roles": roles,
                    "active_roles": [role for role, status in roles.items() if status == "running"]
                    if current["status"] == "running"
                    else [],
                    "checks": json.loads(current["checks"]),
                    "required_checks": json.loads(current["required_checks"]),
                    "verification_commands": self._verification_commands(connection, current),
                }
            )
            state.update(self._acceptance(connection, {**current, "verification": state["verification"]}))
            self._append_event(connection, session_id, kind="run_updated", role=str(current["role"]), payload=state)

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
