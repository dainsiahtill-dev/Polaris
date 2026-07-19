"""Append-only platform Run Ledger writer."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_EVENT_ID_LENGTH = 256
_MAX_RECORDED_AT_LENGTH = 64
_MAX_PROJECTION_ROWS = 4096
_MAX_PROJECTION_BYTES = 8 * 1024 * 1024
_PROJECTION_CORRUPT = "run_ledger_projection_corrupt"


def stable_json(value: Any) -> str:
    """Serialize ledger content into deterministic UTF-8 JSON text."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    """Return the stable content hash for a ledger payload."""

    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _canonical_stable_json(value: Any) -> str:
    """Serialize canonical identity data while rejecting non-finite numbers."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except ValueError as exc:
        raise ValueError("canonical_non_finite") from exc


def _canonical_stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_stable_json(value).encode("utf-8")).hexdigest()


def _projection_corrupt(reason: str) -> ValueError:
    return ValueError(f"{_PROJECTION_CORRUPT}:{reason}")


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for key, value in pairs:
        if key in parsed:
            raise _projection_corrupt("duplicate_json_key")
        parsed[key] = value
    return parsed


def _reject_nonfinite_constant(_value: str) -> Any:
    raise _projection_corrupt("non_finite_json_number")


def _strict_projection_row(line: str) -> dict[str, Any]:
    try:
        parsed = json.loads(
            line,
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_nonfinite_constant,
        )
    except ValueError as exc:
        if _PROJECTION_CORRUPT in str(exc):
            raise
        raise _projection_corrupt("invalid_json") from exc
    if type(parsed) is not dict:
        raise _projection_corrupt("non_object_row")
    return parsed


def _validate_hash_id(value: Any, *, field: str) -> str:
    if type(value) is not str or _LOWER_HEX_64.fullmatch(value) is None:
        raise ValueError(f"invalid_{field}")
    return value


def _validate_event_id(value: Any) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > _MAX_EVENT_ID_LENGTH:
        raise ValueError("invalid_event_id")
    return value


def _validate_recorded_at(value: Any) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > _MAX_RECORDED_AT_LENGTH:
        raise ValueError("invalid_recorded_at")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("invalid_recorded_at") from exc
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid_recorded_at") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("invalid_recorded_at")
    return value


def _event_content_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in event.items() if key not in {"append_id", "content_id", "event_id", "recorded_at"}
    }


def _safe_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip())
    return cleaned.strip("-") or "unknown"


class RunLedger:
    """Append-only JSONL ledger for platform control-plane evidence."""

    def __init__(self, workspace: Path, *, run_id: str) -> None:
        self.workspace = Path(workspace)
        self._canonical_run_id_input = run_id
        self.run_id = str(run_id or "unknown").strip() or "unknown"
        safe_run_id = _safe_token(self.run_id)
        self.path = self.workspace / "runtime" / "control_plane" / "ledger" / f"{safe_run_id}.ndjson"

    def _validate_canonical_run_id(self) -> str:
        raw_run_id = self._canonical_run_id_input
        if (
            type(raw_run_id) is not str
            or raw_run_id != self.run_id
            or raw_run_id != _safe_token(raw_run_id)
            or len(f"{raw_run_id}.ndjson".encode()) > 255
        ):
            raise ValueError("invalid_canonical_run_id: run_id must be an exact safe ledger token")
        return raw_run_id

    def prepare_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Build the deterministic projection row without writing it."""

        payload = dict(event)
        payload.setdefault("schema_version", 1)
        payload.setdefault("content_id", stable_hash(_event_content_payload(payload)))
        payload.setdefault("event_id", payload["content_id"])
        recorded_at = datetime.now(timezone.utc).isoformat()
        payload.setdefault("recorded_at", recorded_at)
        payload.setdefault(
            "append_id",
            stable_hash(
                {
                    "content_id": payload["content_id"],
                    "ledger_path": str(self.path),
                    "recorded_at": payload["recorded_at"],
                }
            ),
        )
        return payload

    def prepare_idempotent_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Build stable canonical identity without inventing projection time."""

        self._validate_canonical_run_id()
        payload = dict(event)
        payload.pop("recorded_at", None)
        payload.setdefault("schema_version", 1)
        computed_content_id = _canonical_stable_hash(_event_content_payload(payload))
        if "content_id" in payload:
            supplied_content_id = _validate_hash_id(payload["content_id"], field="content_id")
        else:
            supplied_content_id = ""
        if supplied_content_id and supplied_content_id != computed_content_id:
            raise ValueError("content_id does not match semantic content")
        payload["content_id"] = computed_content_id

        event_id = _validate_event_id(payload["event_id"]) if "event_id" in payload else computed_content_id
        payload["event_id"] = event_id

        computed_append_id = _canonical_stable_hash(
            {
                "run_id": self.run_id,
                "event_id": event_id,
                "content_id": computed_content_id,
            }
        )
        if "append_id" in payload:
            supplied_append_id = _validate_hash_id(payload["append_id"], field="append_id")
        else:
            supplied_append_id = ""
        if supplied_append_id and supplied_append_id != computed_append_id:
            raise ValueError("append_id does not match canonical logical identity")
        payload["append_id"] = computed_append_id
        return payload

    def fact_idempotency_key(self, event: dict[str, Any]) -> str:
        """Return stable fact identity; semantic drift then conflicts fail-closed."""

        payload = self.prepare_idempotent_event(event)
        return "run-ledger:" + _canonical_stable_hash(
            {
                "run_id": self.run_id,
                "event_id": payload["event_id"],
            }
        )

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Append one rebuildable projection row and return its receipt."""

        payload = self.prepare_event(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(stable_json(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return {"ledger_path": str(self.path), "event": payload}

    def _scan_projection_locked(
        self,
        handle: Any,
        canonical_requested: dict[str, Any],
    ) -> tuple[int, int, dict[str, Any] | None, int, bytes, tuple[dict[str, Any], ...]]:
        """Strictly scan complete binary rows and detach any incomplete tail."""

        file_size = os.fstat(handle.fileno()).st_size
        if file_size > _MAX_PROJECTION_BYTES:
            raise _projection_corrupt("byte_limit_exceeded")
        handle.seek(0)
        projection_bytes = handle.read()
        if type(projection_bytes) is not bytes or len(projection_bytes) != file_size:
            raise _projection_corrupt("binary_short_read")
        final_lf = projection_bytes.rfind(b"\n")
        complete_prefix_size = final_lf + 1
        complete_prefix = projection_bytes[:complete_prefix_size]
        partial_tail = projection_bytes[complete_prefix_size:]
        lines = complete_prefix[:-1].split(b"\n") if complete_prefix else []
        if len(lines) > _MAX_PROJECTION_ROWS:
            raise _projection_corrupt("row_limit_exceeded")

        existing_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        seen_event_ids: set[str] = set()
        seen_append_ids: set[str] = set()
        for raw_line in lines:
            if not raw_line:
                raise _projection_corrupt("empty_row")
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _projection_corrupt("invalid_utf8") from exc
            existing = _strict_projection_row(line)
            try:
                existing_recorded_at = _validate_recorded_at(existing.get("recorded_at"))
                canonical_existing = self.prepare_idempotent_event(existing)
            except ValueError as exc:
                raise _projection_corrupt("invalid_canonical_row") from exc
            expected_existing = dict(canonical_existing)
            expected_existing["recorded_at"] = existing_recorded_at
            if existing != expected_existing:
                raise _projection_corrupt("canonical_row_mismatch")
            existing_event_id = canonical_existing["event_id"]
            if existing_event_id in seen_event_ids:
                raise _projection_corrupt("duplicate_event_id")
            seen_event_ids.add(existing_event_id)
            existing_append_id = canonical_existing["append_id"]
            if existing_append_id in seen_append_ids:
                raise _projection_corrupt("duplicate_append_id")
            seen_append_ids.add(existing_append_id)
            existing_rows.append((existing, canonical_existing))

        matching_existing: dict[str, Any] | None = None
        for existing, canonical_existing in existing_rows:
            if canonical_existing["event_id"] == canonical_requested["event_id"]:
                if canonical_existing != canonical_requested:
                    raise ValueError("event_id already exists with different semantic content")
                matching_existing = existing
            if (
                canonical_existing["append_id"] == canonical_requested["append_id"]
                and canonical_existing != canonical_requested
            ):
                raise ValueError("append_id already exists with different event identity")
        return (
            file_size,
            len(lines),
            matching_existing,
            complete_prefix_size,
            partial_tail,
            tuple(existing for existing, _canonical in existing_rows),
        )

    def _build_canonical_projection_row(
        self,
        event: dict[str, Any],
        *,
        recorded_at: str,
    ) -> tuple[dict[str, Any], bytes]:
        payload = self.prepare_idempotent_event(event)
        payload["recorded_at"] = _validate_recorded_at(recorded_at)
        return payload, (_canonical_stable_json(payload) + "\n").encode("utf-8")

    @staticmethod
    def _ensure_projection_capacity(*, file_size: int, row_count: int, serialized_row: bytes) -> None:
        if row_count >= _MAX_PROJECTION_ROWS:
            raise _projection_corrupt("prospective_row_limit_exceeded")
        if file_size + len(serialized_row) > _MAX_PROJECTION_BYTES:
            raise _projection_corrupt("prospective_byte_limit_exceeded")

    def _write_projection_bytes_locked(self, handle: Any, serialized_row: bytes) -> int:
        return int(handle.write(serialized_row))

    def _flush_projection_locked(self, handle: Any) -> None:
        handle.flush()

    def _fsync_projection_locked(self, handle: Any) -> None:
        os.fsync(handle.fileno())

    def _rollback_projection_locked(self, handle: Any, pre_write_offset: int) -> None:
        """Restore the pre-write prefix durably while caller owns the flock."""

        handle.flush()
        os.ftruncate(handle.fileno(), pre_write_offset)
        os.fsync(handle.fileno())

    def _sync_projection_locked(self, handle: Any) -> None:
        self._flush_projection_locked(handle)
        self._fsync_projection_locked(handle)

    def _append_serialized_row_locked(self, handle: Any, serialized_row: bytes) -> None:
        """Append one row or durably roll back to the exact pre-write offset."""

        pre_write_offset = os.lseek(handle.fileno(), 0, os.SEEK_END)
        try:
            written = self._write_projection_bytes_locked(handle, serialized_row)
            if written != len(serialized_row):
                raise OSError(f"short projection write: expected={len(serialized_row)} actual={written}")
            self._sync_projection_locked(handle)
        except (OSError, RuntimeError, TypeError, ValueError):
            try:
                self._rollback_projection_locked(handle, pre_write_offset)
            except (OSError, RuntimeError, TypeError, ValueError) as rollback_exc:
                raise RuntimeError("run_ledger_projection_write_ambiguous:rollback_failed") from rollback_exc
            raise

    def append_event_once(self, event: dict[str, Any], *, recorded_at: str) -> dict[str, Any]:
        """Append one canonical row, deduplicating under the owning file lock."""

        canonical_payload = self.prepare_idempotent_event(event)
        payload = dict(canonical_payload)
        payload["recorded_at"] = _validate_recorded_at(recorded_at)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b", buffering=0) as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                file_size, row_count, matching_existing, _prefix_size, partial_tail, _complete_rows = (
                    self._scan_projection_locked(
                        handle,
                        canonical_payload,
                    )
                )
                if partial_tail:
                    raise _projection_corrupt("missing_final_newline")
                if matching_existing is not None:
                    return {"ledger_path": str(self.path), "event": matching_existing}
                serialized_row = (_canonical_stable_json(payload) + "\n").encode("utf-8")
                self._ensure_projection_capacity(
                    file_size=file_size,
                    row_count=row_count,
                    serialized_row=serialized_row,
                )
                self._append_serialized_row_locked(handle, serialized_row)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return {"ledger_path": str(self.path), "event": payload}

    def append_event_with_fact_transaction(
        self,
        event: dict[str, Any],
        *,
        append_fact: Callable[[dict[str, Any]], tuple[str, Any]],
        prove_partial_tail_fact: Callable[
            [dict[str, Any], tuple[dict[str, Any], ...], bytes],
            tuple[str, Any] | None,
        ],
    ) -> tuple[dict[str, Any], Any]:
        """Commit Fact then projection while holding the projection's owning flock."""

        canonical_payload = self.prepare_idempotent_event(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b", buffering=0) as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                (
                    file_size,
                    row_count,
                    matching_existing,
                    complete_prefix_size,
                    partial_tail,
                    complete_rows,
                ) = self._scan_projection_locked(handle, canonical_payload)
                if partial_tail:
                    if matching_existing is not None:
                        raise _projection_corrupt("partial_tail_after_existing_event")
                    existing_fact = prove_partial_tail_fact(
                        dict(canonical_payload),
                        complete_rows,
                        partial_tail,
                    )
                    if existing_fact is None:
                        raise _projection_corrupt("partial_tail_fact_missing")
                    fact_recorded_at, fact_receipt = existing_fact
                    payload, serialized_row = self._build_canonical_projection_row(
                        canonical_payload,
                        recorded_at=fact_recorded_at,
                    )
                    if len(partial_tail) >= len(serialized_row) or not serialized_row.startswith(partial_tail):
                        raise _projection_corrupt("partial_tail_mismatch")
                    self._ensure_projection_capacity(
                        file_size=complete_prefix_size,
                        row_count=row_count,
                        serialized_row=serialized_row,
                    )
                    self._rollback_projection_locked(handle, complete_prefix_size)
                    self._append_serialized_row_locked(handle, serialized_row)
                    return {"ledger_path": str(self.path), "event": payload}, fact_receipt

                if matching_existing is None:
                    reserved_payload = dict(canonical_payload)
                    reserved_payload["recorded_at"] = "0" * _MAX_RECORDED_AT_LENGTH
                    self._ensure_projection_capacity(
                        file_size=file_size,
                        row_count=row_count,
                        serialized_row=(_canonical_stable_json(reserved_payload) + "\n").encode("utf-8"),
                    )

                fact_recorded_at, fact_receipt = append_fact(dict(canonical_payload))
                effective_recorded_at = _validate_recorded_at(fact_recorded_at)
                if matching_existing is not None:
                    if matching_existing["recorded_at"] != effective_recorded_at:
                        raise _projection_corrupt("fact_projection_recorded_at_mismatch")
                    self._sync_projection_locked(handle)
                    return {"ledger_path": str(self.path), "event": matching_existing}, fact_receipt

                payload, serialized_row = self._build_canonical_projection_row(
                    canonical_payload,
                    recorded_at=effective_recorded_at,
                )
                self._ensure_projection_capacity(
                    file_size=file_size,
                    row_count=row_count,
                    serialized_row=serialized_row,
                )
                self._append_serialized_row_locked(handle, serialized_row)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return {"ledger_path": str(self.path), "event": payload}, fact_receipt

    def read_events(self) -> list[dict[str, Any]]:
        """Read all events in append order."""

        if not self.path.is_file():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            for line in handle.read().splitlines():
                if not line.strip():
                    continue
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    events.append(parsed)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return events


__all__ = ["RunLedger", "stable_hash", "stable_json"]
