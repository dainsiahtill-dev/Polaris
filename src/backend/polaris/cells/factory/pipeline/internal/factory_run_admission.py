"""Durable, cross-process Factory workspace run admission.

The lease record is the single ``factory.pipeline`` authority for admitting a
run to mutate one workspace. A stable OS-locked file serializes record changes;
the lock file is intentionally retained so replacing or deleting a pathname
can never create two independent lock domains.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Callable, Iterator, NoReturn, cast

from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.storage import resolve_storage_roots

if TYPE_CHECKING:
    from polaris.cells.factory.pipeline.public.contracts import (
        FactoryWorkspaceReleaseEvidenceV1,
        FactoryWorkspaceRunLeaseStateV1,
        FactoryWorkspaceRunLeaseV1,
    )

logger = logging.getLogger(__name__)

DEFAULT_FACTORY_WORKSPACE_LEASE_TTL_SECONDS = 180.0
_LEASE_RECORD_NAME = ".workspace_run_lease.json"
_LEASE_LOCK_NAME = ".workspace_run_lease.lock"
_RESTART_REPLAY_FENCE_REASON = "factory_physical_attempt_restart_replay_fence"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_run_id(run_id: str) -> str:
    normalized = str(run_id or "").strip()
    if not normalized:
        raise ValueError("run_id must be a non-empty string")
    return normalized


@contextmanager
def _stable_exclusive_lock(lock_path: Path) -> Iterator[None]:
    """Hold one stable cross-process advisory lock without deleting its file."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle: BinaryIO | None = None
    try:
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\x00")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            module_values = vars(msvcrt)
            locking = cast(Callable[[int, int, int], None], module_values["locking"])
            locking(handle.fileno(), cast(int, module_values["LK_LOCK"]), 1)
        else:
            import fcntl

            module_values = vars(fcntl)
            flock = cast(Callable[[int, int], None], module_values["flock"])
            flock(handle.fileno(), cast(int, module_values["LOCK_EX"]))
    except OSError as exc:
        if handle is not None:
            handle.close()
        from polaris.cells.factory.pipeline.public.contracts import (
            FactoryWorkspaceRunLeaseStorageError,
        )

        raise FactoryWorkspaceRunLeaseStorageError(
            "Failed to acquire Factory workspace admission lock",
            details={"lock_path": str(lock_path), "error": str(exc)},
        ) from exc

    try:
        yield
    finally:
        if handle is not None:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    module_values = vars(msvcrt)
                    locking = cast(Callable[[int, int, int], None], module_values["locking"])
                    locking(handle.fileno(), cast(int, module_values["LK_UNLCK"]), 1)
                else:
                    import fcntl

                    module_values = vars(fcntl)
                    flock = cast(Callable[[int, int], None], module_values["flock"])
                    flock(handle.fileno(), cast(int, module_values["LOCK_UN"]))
            except OSError as exc:
                logger.warning("Failed to unlock Factory workspace admission file %s: %s", lock_path, exc)
            finally:
                handle.close()


class FactoryWorkspaceRunAdmission:
    """Own durable workspace run admission, renewal, draining, and release."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        state_root: str | Path | None = None,
        lease_ttl_seconds: float = DEFAULT_FACTORY_WORKSPACE_LEASE_TTL_SECONDS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        resolved_root = (
            Path(state_root)
            if state_root is not None
            else Path(resolve_storage_roots(str(self.workspace)).runtime_root) / "factory"
        )
        self.state_root = resolved_root.resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.record_path = self.state_root / _LEASE_RECORD_NAME
        self.lock_path = self.state_root / _LEASE_LOCK_NAME
        self._lease_ttl_seconds = self._normalize_ttl(lease_ttl_seconds)
        self._clock = clock

    @staticmethod
    def _normalize_ttl(value: float) -> float:
        ttl = float(value)
        if ttl <= 0:
            raise ValueError("lease_ttl_seconds must be > 0")
        return ttl

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Factory workspace admission clock must return a timezone-aware datetime")
        return now.astimezone(timezone.utc)

    def _ttl(self, lease_ttl_seconds: float | None) -> float:
        if lease_ttl_seconds is None:
            return self._lease_ttl_seconds
        return self._normalize_ttl(lease_ttl_seconds)

    @staticmethod
    def _expires_at(now: datetime, ttl_seconds: float) -> str:
        return (now + timedelta(seconds=ttl_seconds)).isoformat()

    @staticmethod
    def _parse_timestamp(value: str, *, field_name: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError as exc:
            from polaris.cells.factory.pipeline.public.contracts import (
                FactoryWorkspaceRunLeaseStorageError,
            )

            raise FactoryWorkspaceRunLeaseStorageError(
                "Factory workspace lease contains an invalid timestamp",
                details={"field": field_name, "value": str(value)},
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            from polaris.cells.factory.pipeline.public.contracts import (
                FactoryWorkspaceRunLeaseStorageError,
            )

            raise FactoryWorkspaceRunLeaseStorageError(
                "Factory workspace lease timestamp must be timezone-aware",
                details={"field": field_name, "value": str(value)},
            )
        return parsed.astimezone(timezone.utc)

    def _is_expired(self, lease: FactoryWorkspaceRunLeaseV1, now: datetime) -> bool:
        return self._parse_timestamp(lease.expires_at, field_name="expires_at") <= now

    def _read_locked(self) -> FactoryWorkspaceRunLeaseV1 | None:
        if not self.record_path.is_file():
            return None
        try:
            with self.record_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                raise TypeError("lease record must be a JSON object")
            from polaris.cells.factory.pipeline.public.contracts import (
                FactoryWorkspaceRunLeaseV1,
            )

            lease = FactoryWorkspaceRunLeaseV1.from_dict(payload)
            if Path(lease.workspace).resolve() != self.workspace:
                raise ValueError("lease workspace does not match admission workspace")
            return lease
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            from polaris.cells.factory.pipeline.public.contracts import (
                FactoryWorkspaceRunLeaseStorageError,
            )

            raise FactoryWorkspaceRunLeaseStorageError(
                "Factory workspace lease record is unreadable or invalid",
                details={"record_path": str(self.record_path), "error": str(exc)},
            ) from exc

    def _write_locked(self, lease: FactoryWorkspaceRunLeaseV1) -> None:
        try:
            write_json_atomic(str(self.record_path), lease.to_dict(), lock_timeout_sec=None)
        except (OSError, TypeError, ValueError) as exc:
            from polaris.cells.factory.pipeline.public.contracts import (
                FactoryWorkspaceRunLeaseStorageError,
            )

            raise FactoryWorkspaceRunLeaseStorageError(
                "Factory workspace lease record could not be committed",
                details={"record_path": str(self.record_path), "error": str(exc)},
            ) from exc

    @staticmethod
    def _raise_conflict(
        message: str,
        *,
        code: str,
        run_id: str,
        current: FactoryWorkspaceRunLeaseV1 | None,
    ) -> NoReturn:
        from polaris.cells.factory.pipeline.public.contracts import (
            FactoryWorkspaceRunLeaseConflictError,
        )

        raise FactoryWorkspaceRunLeaseConflictError(
            message,
            code=code,
            requested_run_id=run_id,
            current_lease=current,
        )

    def current(self) -> FactoryWorkspaceRunLeaseV1 | None:
        """Return the current durable lease under the stable lock."""

        with _stable_exclusive_lock(self.lock_path):
            return self._read_locked()

    def acquire(
        self,
        run_id: str,
        *,
        lease_ttl_seconds: float | None = None,
    ) -> FactoryWorkspaceRunLeaseV1:
        """Acquire the workspace or return the same active run idempotently."""

        normalized_run_id = _normalize_run_id(run_id)
        ttl = self._ttl(lease_ttl_seconds)
        with _stable_exclusive_lock(self.lock_path):
            now = self._now()
            current = self._read_locked()
            if current is not None and current.state.value in {"active", "draining"}:
                expired = self._is_expired(current, now)
                if current.state.value == "active" and current.run_id == normalized_run_id and not expired:
                    return current
                self._raise_conflict(
                    "Another Factory run owns or is draining this workspace",
                    code=(
                        "factory_workspace_run_expired_owner_conflict" if expired else "factory_workspace_run_conflict"
                    ),
                    run_id=normalized_run_id,
                    current=current,
                )

            from polaris.cells.factory.pipeline.public.contracts import (
                FactoryWorkspaceRunLeaseStateV1,
                FactoryWorkspaceRunLeaseV1,
            )

            version = 1 if current is None else current.version + 1
            fencing_token = 1 if current is None else current.fencing_token + 1
            timestamp = now.isoformat()
            acquired = FactoryWorkspaceRunLeaseV1(
                workspace=str(self.workspace),
                run_id=normalized_run_id,
                state=FactoryWorkspaceRunLeaseStateV1.ACTIVE,
                version=version,
                fencing_token=fencing_token,
                acquired_at=timestamp,
                updated_at=timestamp,
                expires_at=self._expires_at(now, ttl),
            )
            self._write_locked(acquired)
            return acquired

    def claim_lifecycle_operation(
        self,
        run_id: str,
        *,
        operation: str,
        nonce: str,
        acquire_if_available: bool,
        expected_fencing_token: int | None,
        lease_ttl_seconds: float | None = None,
        allow_expired_owner: bool = False,
        replay_fence: bool = False,
    ) -> FactoryWorkspaceRunLeaseV1:
        """Atomically acquire workspace authority and one lifecycle mutation.

        ``expected_fencing_token`` is the caller's proof of authority for an
        existing ACTIVE or DRAINING lease. A missing record accepts only no
        token. A RELEASED record accepts no token or its exact recorded token
        when this operation is allowed to acquire the workspace.
        """

        normalized_run_id = _normalize_run_id(run_id)
        normalized_operation = _normalize_run_id(operation)
        normalized_nonce = _normalize_run_id(nonce)
        if type(allow_expired_owner) is not bool:
            raise TypeError("allow_expired_owner must be a bool")
        if type(replay_fence) is not bool:
            raise TypeError("replay_fence must be a bool")
        if allow_expired_owner and normalized_operation != "recover_stale_workspace_owner":
            raise ValueError("expired owner lifecycle claim is reserved for stale-owner recovery")
        ttl = self._ttl(lease_ttl_seconds)
        with _stable_exclusive_lock(self.lock_path):
            now = self._now()
            current = self._read_locked()
            acquired_workspace = False
            if current is None or current.state.value == "released":
                if allow_expired_owner:
                    self._raise_conflict(
                        "Factory stale-owner recovery requires an expired owner",
                        code="factory_workspace_run_owner_not_stale",
                        run_id=normalized_run_id,
                        current=current,
                    )
                if current is not None and current.lifecycle_operation_claim is not None:
                    self._raise_conflict(
                        "Released Factory workspace still has a lifecycle claim",
                        code="factory_lifecycle_operation_inflight",
                        run_id=normalized_run_id,
                        current=current,
                    )
                if not acquire_if_available:
                    self._raise_conflict(
                        "Factory lifecycle mutation requires existing workspace authority",
                        code="factory_workspace_run_lease_missing",
                        run_id=normalized_run_id,
                        current=current,
                    )
                if current is None and expected_fencing_token is not None:
                    self._raise_conflict(
                        "Factory lifecycle mutation supplied authority for a missing lease",
                        code="factory_workspace_run_fenced",
                        run_id=normalized_run_id,
                        current=None,
                    )
                if (
                    current is not None
                    and expected_fencing_token is not None
                    and expected_fencing_token != current.fencing_token
                ):
                    self._raise_conflict(
                        "Factory lifecycle mutation supplied stale released-lease authority",
                        code="factory_workspace_run_fenced",
                        run_id=normalized_run_id,
                        current=current,
                    )
                from polaris.cells.factory.pipeline.public.contracts import (
                    FactoryWorkspaceRunLeaseStateV1,
                    FactoryWorkspaceRunLeaseV1,
                )

                timestamp = now.isoformat()
                current = FactoryWorkspaceRunLeaseV1(
                    workspace=str(self.workspace),
                    run_id=normalized_run_id,
                    state=FactoryWorkspaceRunLeaseStateV1.ACTIVE,
                    version=1 if current is None else current.version + 1,
                    fencing_token=1 if current is None else current.fencing_token + 1,
                    acquired_at=timestamp,
                    updated_at=timestamp,
                    expires_at=self._expires_at(now, ttl),
                )
                acquired_workspace = True
            else:
                if expected_fencing_token is None:
                    self._raise_conflict(
                        "Factory lifecycle mutation requires caller fencing authority",
                        code="factory_workspace_run_fenced",
                        run_id=normalized_run_id,
                        current=current,
                    )
                current = self._require_owned_locked(
                    run_id=normalized_run_id,
                    fencing_token=expected_fencing_token,
                    now=now,
                    allow_expired=allow_expired_owner,
                )
                if allow_expired_owner and not self._is_expired(current, now):
                    self._raise_conflict(
                        "Factory stale-owner recovery requires an expired owner",
                        code="factory_workspace_run_owner_not_stale",
                        run_id=normalized_run_id,
                        current=current,
                    )

            existing = current.lifecycle_operation_claim
            if existing is not None:
                if existing.operation == normalized_operation and existing.nonce == normalized_nonce:
                    return current
                self._raise_conflict(
                    "Another Factory lifecycle mutation holds the durable claim",
                    code="factory_lifecycle_operation_conflict",
                    run_id=normalized_run_id,
                    current=current,
                )

            from polaris.cells.factory.pipeline.public.contracts import (
                FactoryLifecycleOperationClaimV1,
            )

            sequence = current.lifecycle_claim_sequence + 1
            replay_fencing_token = current.fencing_token + 1 if replay_fence else current.fencing_token
            claimed = replace(
                current,
                state=self._state("draining") if replay_fence else current.state,
                version=current.version + 1,
                fencing_token=replay_fencing_token,
                updated_at=now.isoformat(),
                expires_at=current.expires_at if allow_expired_owner else self._expires_at(now, ttl),
                drain_reason=_RESTART_REPLAY_FENCE_REASON if replay_fence else current.drain_reason,
                lifecycle_claim_sequence=sequence,
                lifecycle_operation_claim=FactoryLifecycleOperationClaimV1(
                    run_id=normalized_run_id,
                    operation=normalized_operation,
                    sequence=sequence,
                    nonce=normalized_nonce,
                    claimed_at=now.isoformat(),
                    acquired_workspace=acquired_workspace,
                ),
            )
            self._write_locked(claimed)
            return claimed

    def release_lifecycle_operation(
        self,
        run_id: str,
        *,
        fencing_token: int,
        operation: str,
        nonce: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        """Release exactly one lifecycle claim without changing ownership."""

        normalized_run_id = _normalize_run_id(run_id)
        normalized_operation = _normalize_run_id(operation)
        normalized_nonce = _normalize_run_id(nonce)
        with _stable_exclusive_lock(self.lock_path):
            now = self._now()
            current = self._require_owned_locked(
                run_id=normalized_run_id,
                fencing_token=fencing_token,
                now=now,
                allow_expired=True,
            )
            existing = current.lifecycle_operation_claim
            if existing is None:
                return current
            if existing.operation != normalized_operation or existing.nonce != normalized_nonce:
                self._raise_conflict(
                    "Factory lifecycle operation claim owner does not match",
                    code="factory_lifecycle_operation_fenced",
                    run_id=normalized_run_id,
                    current=current,
                )
            released = replace(
                current,
                version=current.version + 1,
                updated_at=now.isoformat(),
                lifecycle_operation_claim=None,
            )
            self._write_locked(released)
            return released

    def rollback_lifecycle_operation(
        self,
        run_id: str,
        *,
        fencing_token: int,
        operation: str,
        nonce: str,
        reason: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        """Rollback an operation claim and any workspace acquired only for it."""

        normalized_run_id = _normalize_run_id(run_id)
        normalized_operation = _normalize_run_id(operation)
        normalized_nonce = _normalize_run_id(nonce)
        with _stable_exclusive_lock(self.lock_path):
            now = self._now()
            current = self._require_owned_locked(
                run_id=normalized_run_id,
                fencing_token=fencing_token,
                now=now,
                allow_expired=True,
            )
            existing = current.lifecycle_operation_claim
            if existing is None:
                return current
            if existing.operation != normalized_operation or existing.nonce != normalized_nonce:
                self._raise_conflict(
                    "Factory lifecycle rollback claim owner does not match",
                    code="factory_lifecycle_operation_fenced",
                    run_id=normalized_run_id,
                    current=current,
                )
            if existing.acquired_workspace and current.stage_execution_claim is None:
                from polaris.cells.factory.pipeline.public.contracts import (
                    FactoryWorkspaceReleaseEvidenceV1,
                )

                timestamp = now.isoformat()
                rolled_back = replace(
                    current,
                    state=self._state("released"),
                    version=current.version + 1,
                    updated_at=timestamp,
                    expires_at=timestamp,
                    released_at=timestamp,
                    drain_reason=str(reason or "lifecycle_operation_rollback").strip(),
                    lifecycle_operation_claim=None,
                    release_evidence=FactoryWorkspaceReleaseEvidenceV1(
                        factory_run_id=normalized_run_id,
                        source="factory_lifecycle_acquisition_rollback",
                        observed_at=timestamp,
                        details={"operation": normalized_operation, "reason": str(reason)},
                    ),
                )
            else:
                rolled_back = replace(
                    current,
                    version=current.version + 1,
                    updated_at=now.isoformat(),
                    lifecycle_operation_claim=None,
                )
            self._write_locked(rolled_back)
            return rolled_back

    def _require_owned_locked(
        self,
        *,
        run_id: str,
        fencing_token: int,
        now: datetime,
        allow_expired: bool = False,
    ) -> FactoryWorkspaceRunLeaseV1:
        current = self._read_locked()
        if current is None:
            self._raise_conflict(
                "Factory workspace lease does not exist",
                code="factory_workspace_run_lease_missing",
                run_id=run_id,
                current=None,
            )
        assert current is not None
        if current.run_id != run_id or current.fencing_token != int(fencing_token):
            self._raise_conflict(
                "Factory workspace lease owner has been fenced",
                code="factory_workspace_run_fenced",
                run_id=run_id,
                current=current,
            )
        if not allow_expired and current.state.value != "released" and self._is_expired(current, now):
            self._raise_conflict(
                "Factory workspace lease has expired",
                code="factory_workspace_run_lease_expired",
                run_id=run_id,
                current=current,
            )
        return current

    def assert_active(self, run_id: str, *, fencing_token: int) -> FactoryWorkspaceRunLeaseV1:
        """Validate an unexpired ACTIVE owner without mutating the record."""

        normalized_run_id = _normalize_run_id(run_id)
        with _stable_exclusive_lock(self.lock_path):
            current = self._require_owned_locked(
                run_id=normalized_run_id,
                fencing_token=fencing_token,
                now=self._now(),
            )
            if current.state.value != "active":
                self._raise_conflict(
                    "Factory workspace lease is not active",
                    code="factory_workspace_run_not_active",
                    run_id=normalized_run_id,
                    current=current,
                )
            return current

    def claim_stage(
        self,
        run_id: str,
        *,
        fencing_token: int,
        stage: str,
        nonce: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        """CAS one durable stage claim under the workspace admission lock."""

        normalized_run_id = _normalize_run_id(run_id)
        normalized_stage = _normalize_run_id(stage)
        normalized_nonce = _normalize_run_id(nonce)
        with _stable_exclusive_lock(self.lock_path):
            now = self._now()
            current = self._require_owned_locked(
                run_id=normalized_run_id,
                fencing_token=fencing_token,
                now=now,
            )
            if current.lifecycle_operation_claim is not None:
                self._raise_conflict(
                    "Factory stage execution cannot overlap a lifecycle operation",
                    code="factory_lifecycle_operation_inflight",
                    run_id=normalized_run_id,
                    current=current,
                )
            existing = current.stage_execution_claim
            if existing is not None:
                if existing.stage == normalized_stage and existing.nonce == normalized_nonce:
                    return current
                self._raise_conflict(
                    "Another Factory stage execution already holds the durable claim",
                    code="factory_stage_execution_conflict",
                    run_id=normalized_run_id,
                    current=current,
                )
            if current.state.value != "active":
                self._raise_conflict(
                    "Factory stage execution requires an ACTIVE workspace lease",
                    code="factory_workspace_run_not_active",
                    run_id=normalized_run_id,
                    current=current,
                )
            from polaris.cells.factory.pipeline.public.contracts import (
                FactoryStageExecutionClaimV1,
            )

            sequence = current.stage_claim_sequence + 1
            claimed = replace(
                current,
                version=current.version + 1,
                updated_at=now.isoformat(),
                stage_claim_sequence=sequence,
                stage_execution_claim=FactoryStageExecutionClaimV1(
                    run_id=normalized_run_id,
                    stage=normalized_stage,
                    attempt=sequence,
                    nonce=normalized_nonce,
                    claimed_at=now.isoformat(),
                ),
            )
            self._write_locked(claimed)
            return claimed

    @contextmanager
    def hold_active_lifecycle_operation_claim(
        self,
        run_id: str,
        *,
        fencing_token: int,
        operation: str,
        sequence: int,
        nonce: str,
        allow_expired_owner: bool = False,
    ) -> Iterator[Callable[[], FactoryWorkspaceRunLeaseV1]]:
        """Hold the exact lifecycle claim as a replay/mutation fence.

        The hold is deliberately read-only: it neither renews the workspace
        lease nor changes the lifecycle claim.  Recovery callers use the
        yielded callback immediately before every replay read/CAS write so a
        stale, expired, released, or superseded claim cannot authorize a
        reconstructed physical-attempt coordinator.
        """

        normalized_run_id = _normalize_run_id(run_id)
        normalized_operation = _normalize_run_id(operation)
        normalized_nonce = _normalize_run_id(nonce)
        if type(allow_expired_owner) is not bool:
            raise TypeError("allow_expired_owner must be a bool")
        if allow_expired_owner and normalized_operation != "recover_stale_workspace_owner":
            raise ValueError("expired owner lifecycle hold is reserved for stale-owner recovery")
        if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token < 1:
            raise ValueError("fencing_token must be an int >= 1")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("sequence must be an int >= 1")
        with _stable_exclusive_lock(self.lock_path):

            def revalidate() -> FactoryWorkspaceRunLeaseV1:
                now = self._now()
                current = self._require_owned_locked(
                    run_id=normalized_run_id,
                    fencing_token=fencing_token,
                    now=now,
                    allow_expired=allow_expired_owner,
                )
                replay_fenced = (
                    current.state.value == "draining" and current.drain_reason == _RESTART_REPLAY_FENCE_REASON
                )
                valid_state = current.state.value == "active" or replay_fenced
                if not valid_state:
                    self._raise_conflict(
                        "Factory physical-attempt replay requires an ACTIVE or replay-fenced workspace lease",
                        code="factory_workspace_run_not_active",
                        run_id=normalized_run_id,
                        current=current,
                    )
                if allow_expired_owner and not self._is_expired(current, now):
                    self._raise_conflict(
                        "Factory stale-owner replay requires an expired owner",
                        code="factory_workspace_run_owner_not_stale",
                        run_id=normalized_run_id,
                        current=current,
                    )
                claim = current.lifecycle_operation_claim
                if claim is None:
                    self._raise_conflict(
                        "Factory physical-attempt replay requires a lifecycle operation claim",
                        code="factory_lifecycle_operation_claim_missing",
                        run_id=normalized_run_id,
                        current=current,
                    )
                assert claim is not None
                if (
                    claim.run_id != normalized_run_id
                    or claim.operation != normalized_operation
                    or claim.sequence != sequence
                    or claim.nonce != normalized_nonce
                ):
                    self._raise_conflict(
                        "Factory physical-attempt replay lifecycle claim has been fenced",
                        code="factory_lifecycle_operation_fenced",
                        run_id=normalized_run_id,
                        current=current,
                    )
                return current

            revalidate()
            yield revalidate

    @contextmanager
    def hold_active_stage_claim(
        self,
        run_id: str,
        *,
        fencing_token: int,
        stage: str,
        attempt: int,
        nonce: str,
    ) -> Iterator[Callable[[], FactoryWorkspaceRunLeaseV1]]:
        """Hold the stable admission lock around one exact live stage claim.

        This is a read-only authority boundary.  It does not acquire, renew,
        release, or drain either the workspace lease or the stage claim.  The
        caller may safely reconstruct and append a causal fact while the exact
        ACTIVE fence/claim tuple cannot change in another process.  The yielded
        read-only callback must be invoked at every causal write boundary so a
        lease that expires while the stable lock is held cannot authorize a
        later append, commit, or acknowledgement.  Revalidation never renews
        the lease.
        """

        normalized_run_id = _normalize_run_id(run_id)
        normalized_stage = _normalize_run_id(stage)
        normalized_nonce = _normalize_run_id(nonce)
        if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token < 1:
            raise ValueError("fencing_token must be an int >= 1")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("attempt must be an int >= 1")
        with _stable_exclusive_lock(self.lock_path):

            def revalidate() -> FactoryWorkspaceRunLeaseV1:
                current = self._require_owned_locked(
                    run_id=normalized_run_id,
                    fencing_token=fencing_token,
                    now=self._now(),
                )
                if current.state.value != "active":
                    self._raise_conflict(
                        "Factory role evidence cutoff requires an ACTIVE workspace lease",
                        code="factory_workspace_run_not_active",
                        run_id=normalized_run_id,
                        current=current,
                    )
                if current.lifecycle_operation_claim is not None:
                    self._raise_conflict(
                        "Factory role evidence cutoff cannot overlap a lifecycle operation",
                        code="factory_lifecycle_operation_inflight",
                        run_id=normalized_run_id,
                        current=current,
                    )
                claim = current.stage_execution_claim
                if claim is None:
                    self._raise_conflict(
                        "Factory role evidence cutoff requires a live stage claim",
                        code="factory_stage_execution_claim_missing",
                        run_id=normalized_run_id,
                        current=current,
                    )
                assert claim is not None
                if (
                    claim.run_id != normalized_run_id
                    or claim.stage != normalized_stage
                    or claim.attempt != attempt
                    or claim.nonce != normalized_nonce
                ):
                    self._raise_conflict(
                        "Factory role evidence cutoff stage claim has been fenced",
                        code="factory_stage_execution_fenced",
                        run_id=normalized_run_id,
                        current=current,
                    )
                return current

            revalidate()
            yield revalidate

    def release_stage(
        self,
        run_id: str,
        *,
        fencing_token: int,
        stage: str,
        nonce: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        """Release an exact stage claim after its result is durably projected."""

        normalized_run_id = _normalize_run_id(run_id)
        normalized_stage = _normalize_run_id(stage)
        normalized_nonce = _normalize_run_id(nonce)
        with _stable_exclusive_lock(self.lock_path):
            now = self._now()
            current = self._require_owned_locked(
                run_id=normalized_run_id,
                fencing_token=fencing_token,
                now=now,
                allow_expired=True,
            )
            if current.state.value == "released":
                return current
            existing = current.stage_execution_claim
            if existing is None:
                return current
            if existing.stage != normalized_stage or existing.nonce != normalized_nonce:
                self._raise_conflict(
                    "Factory stage execution claim owner does not match",
                    code="factory_stage_execution_fenced",
                    run_id=normalized_run_id,
                    current=current,
                )
            released = replace(
                current,
                version=current.version + 1,
                updated_at=now.isoformat(),
                stage_execution_claim=None,
            )
            self._write_locked(released)
            return released

    def renew(
        self,
        run_id: str,
        *,
        fencing_token: int,
        lease_ttl_seconds: float | None = None,
    ) -> FactoryWorkspaceRunLeaseV1:
        """Renew an unexpired ACTIVE or DRAINING lease owned by this fence."""

        normalized_run_id = _normalize_run_id(run_id)
        ttl = self._ttl(lease_ttl_seconds)
        with _stable_exclusive_lock(self.lock_path):
            now = self._now()
            current = self._require_owned_locked(
                run_id=normalized_run_id,
                fencing_token=fencing_token,
                now=now,
            )
            if current.state.value not in {"active", "draining"}:
                self._raise_conflict(
                    "Released Factory workspace lease cannot be renewed",
                    code="factory_workspace_run_released",
                    run_id=normalized_run_id,
                    current=current,
                )
            renewed = replace(
                current,
                version=current.version + 1,
                updated_at=now.isoformat(),
                expires_at=self._expires_at(now, ttl),
            )
            self._write_locked(renewed)
            return renewed

    def begin_draining(
        self,
        run_id: str,
        *,
        fencing_token: int,
        reason: str,
        operation_nonce: str = "",
    ) -> FactoryWorkspaceRunLeaseV1:
        """Move an owned ACTIVE lease into DRAINING idempotently."""

        normalized_run_id = _normalize_run_id(run_id)
        with _stable_exclusive_lock(self.lock_path):
            now = self._now()
            current = self._require_owned_locked(
                run_id=normalized_run_id,
                fencing_token=fencing_token,
                now=now,
            )
            operation_claim = current.lifecycle_operation_claim
            if operation_claim is not None and operation_claim.nonce != str(operation_nonce or "").strip():
                self._raise_conflict(
                    "Factory terminal drain does not own the lifecycle claim",
                    code="factory_lifecycle_operation_fenced",
                    run_id=normalized_run_id,
                    current=current,
                )
            if current.state.value in {"draining", "released"}:
                return current
            draining = replace(
                current,
                state=self._state("draining"),
                version=current.version + 1,
                updated_at=now.isoformat(),
                expires_at=self._expires_at(now, self._lease_ttl_seconds),
                drain_reason=str(reason or "").strip(),
            )
            self._write_locked(draining)
            return draining

    def release(
        self,
        run_id: str,
        *,
        fencing_token: int,
        settlement_evidence: FactoryWorkspaceReleaseEvidenceV1 | None = None,
        operation_nonce: str = "",
    ) -> FactoryWorkspaceRunLeaseV1:
        """Release an owned DRAINING lease only with settlement evidence."""

        normalized_run_id = _normalize_run_id(run_id)
        with _stable_exclusive_lock(self.lock_path):
            now = self._now()
            current = self._require_owned_locked(
                run_id=normalized_run_id,
                fencing_token=fencing_token,
                now=now,
                allow_expired=True,
            )
            if current.state.value == "released":
                return current
            if current.state.value != "draining":
                self._raise_conflict(
                    "Factory workspace lease must drain before release",
                    code="factory_workspace_run_not_draining",
                    run_id=normalized_run_id,
                    current=current,
                )
            if current.stage_execution_claim is not None:
                self._raise_conflict(
                    "Factory workspace lease cannot release while a stage claim is active",
                    code="factory_stage_execution_inflight",
                    run_id=normalized_run_id,
                    current=current,
                )
            if settlement_evidence is None or settlement_evidence.factory_run_id != normalized_run_id:
                self._raise_conflict(
                    "Factory workspace release requires matching settlement evidence",
                    code="factory_workspace_release_evidence_missing",
                    run_id=normalized_run_id,
                    current=current,
                )
            operation_claim = current.lifecycle_operation_claim
            normalized_operation_nonce = str(operation_nonce or "").strip()
            if operation_claim is not None and operation_claim.nonce != normalized_operation_nonce:
                self._raise_conflict(
                    "Factory workspace release does not own the lifecycle claim",
                    code="factory_lifecycle_operation_fenced",
                    run_id=normalized_run_id,
                    current=current,
                )
            timestamp = now.isoformat()
            released = replace(
                current,
                state=self._state("released"),
                version=current.version + 1,
                updated_at=timestamp,
                expires_at=timestamp,
                released_at=timestamp,
                lifecycle_operation_claim=None,
                release_evidence=settlement_evidence,
            )
            self._write_locked(released)
            return released

    def assert_stale_owner(
        self,
        run_id: str,
        *,
        fencing_token: int,
    ) -> FactoryWorkspaceRunLeaseV1:
        """Return an expired owner lease without renewing or mutating it."""

        normalized_run_id = _normalize_run_id(run_id)
        with _stable_exclusive_lock(self.lock_path):
            now = self._now()
            current = self._require_owned_locked(
                run_id=normalized_run_id,
                fencing_token=fencing_token,
                now=now,
                allow_expired=True,
            )
            if current.state.value == "released" or not self._is_expired(current, now):
                self._raise_conflict(
                    "Factory workspace owner is not stale",
                    code="factory_workspace_run_owner_not_stale",
                    run_id=normalized_run_id,
                    current=current,
                )
            return current

    def recover_stale_owner(
        self,
        run_id: str,
        *,
        fencing_token: int,
        operation_nonce: str,
        settlement_evidence: FactoryWorkspaceReleaseEvidenceV1,
        reason: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        """Release one expired owner under its exact recovery claim."""

        normalized_run_id = _normalize_run_id(run_id)
        normalized_nonce = _normalize_run_id(operation_nonce)
        with _stable_exclusive_lock(self.lock_path):
            now = self._now()
            current = self._require_owned_locked(
                run_id=normalized_run_id,
                fencing_token=fencing_token,
                now=now,
                allow_expired=True,
            )
            if current.state.value == "released":
                return current
            if not self._is_expired(current, now):
                self._raise_conflict(
                    "Factory stale-owner recovery requires an expired lease",
                    code="factory_workspace_run_owner_not_stale",
                    run_id=normalized_run_id,
                    current=current,
                )
            claim = current.lifecycle_operation_claim
            if (
                claim is None
                or claim.run_id != normalized_run_id
                or claim.operation != "recover_stale_workspace_owner"
                or claim.nonce != normalized_nonce
            ):
                self._raise_conflict(
                    "Factory stale-owner release requires its exact lifecycle claim",
                    code="factory_lifecycle_operation_fenced",
                    run_id=normalized_run_id,
                    current=current,
                )
            if settlement_evidence.factory_run_id != normalized_run_id:
                self._raise_conflict(
                    "Factory stale-owner recovery evidence has the wrong owner",
                    code="factory_workspace_release_evidence_missing",
                    run_id=normalized_run_id,
                    current=current,
                )
            timestamp = now.isoformat()
            released = replace(
                current,
                state=self._state("released"),
                version=current.version + 1,
                updated_at=timestamp,
                expires_at=timestamp,
                released_at=timestamp,
                drain_reason=str(reason or "stale_owner_recovery").strip(),
                stage_execution_claim=None,
                lifecycle_operation_claim=None,
                release_evidence=settlement_evidence,
            )
            self._write_locked(released)
            return released

    @staticmethod
    def _state(value: str) -> FactoryWorkspaceRunLeaseStateV1:
        from polaris.cells.factory.pipeline.public.contracts import (
            FactoryWorkspaceRunLeaseStateV1,
        )

        return FactoryWorkspaceRunLeaseStateV1(value)


__all__ = [
    "DEFAULT_FACTORY_WORKSPACE_LEASE_TTL_SECONDS",
    "FactoryWorkspaceRunAdmission",
]
