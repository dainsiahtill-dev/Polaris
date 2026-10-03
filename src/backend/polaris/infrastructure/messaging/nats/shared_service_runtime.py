"""Bounded coordinator for a native NATS-owned, inherited-fence shared service.

Backend attachment is not lifetime ownership. The native process retains its
writer lock until physical exit; lifecycle records are never PID signal authority.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, TextIO, TypeVar

from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.fs import GuardedRegularFileSnapshotError, KernelFileSystem, get_default_adapter
from polaris.kernelone.fs.locked_regular_file import (
    InheritedLockDescriptorsV1,
    LockedRegularFileError,
    LockedRegularFileSetV1,
    StreamLeaseV1,
)
from polaris.kernelone.process.process_tree import isolated_process_group_kwargs
from polaris.kernelone.storage.layout import kernelone_home

from .shared_service_identity import (
    SharedNATSError,
    capture_identity,
    directory_identity,
    incarnation_dead,
    probe_info,
    require_capability,
    validate_identity,
    validate_identity_record,
)

_CONTROL = "runtime/nats/shared-control.jsonl"
_WRITER = "runtime/nats/shared-writer"
_PENDING: dict[int, tuple[subprocess.Popen[bytes], InheritedLockDescriptorsV1]] = {}
_Result = TypeVar("_Result")


def _arguments() -> dict[str, str]:
    home = Path(kernelone_home()).absolute()
    return {
        "runtime_root": str(home / "runtime"),
        "platform_lock_root": str(home / "lock_authorities" / "v1"),
        "storage_identity_token": "shared-nats-" + hashlib.sha256(str(home).encode("utf-8")).hexdigest()[:24],
    }


def _remaining(deadline: float, cancel: threading.Event) -> float:
    if cancel.is_set():
        raise SharedNATSError("shared_nats_startup_cancelled")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SharedNATSError("startup_deadline_exceeded", "shared NATS coordination")
    return remaining


def _control(arguments: dict[str, str], deadline: float, cancel: threading.Event) -> LockedRegularFileSetV1:
    authority = Path(arguments["platform_lock_root"]) / arguments["storage_identity_token"]
    initialize = not authority.exists()
    while True:
        budget = min(0.05, _remaining(deadline, cancel))
        try:
            return LockedRegularFileSetV1.acquire(**arguments, logical_paths=(_CONTROL,), timeout_seconds=budget)
        except LockedRegularFileError as exc:
            if exc.code not in {"lock_authority_missing", "stream_lock_missing", "lock_acquisition_timeout"}:
                raise
            if initialize and exc.code in {"lock_authority_missing", "stream_lock_missing"}:
                try:
                    LockedRegularFileSetV1.provision_authority(
                        **arguments, timeout_seconds=min(0.05, _remaining(deadline, cancel))
                    )
                    LockedRegularFileSetV1.enroll_stream_lock_keys(
                        **arguments,
                        logical_paths=(_CONTROL, _WRITER),
                        timeout_seconds=min(0.05, _remaining(deadline, cancel)),
                    )
                except LockedRegularFileError as provision_error:
                    if provision_error.code != "lock_acquisition_timeout":
                        raise
            cancel.wait(min(0.01, _remaining(deadline, cancel)))


def _read_record(lease: StreamLeaseV1) -> dict[str, Any]:
    if not lease.open_existing(writable=True):
        return {}
    raw = lease.read_tail_bytes(65536)
    try:
        if not raw.endswith(b"\n"):
            raise ValueError("incomplete record")
        record = json.loads(raw.splitlines()[-1].decode("utf-8"))
        if (
            not isinstance(record, dict)
            or record.get("version") != 1
            or record.get("state") not in {"STARTING", "READY", "STOPPED"}
        ):
            raise ValueError("invalid record")
        identity = record.get("identity")
        if not isinstance(identity, dict):
            raise ValueError("missing identity")
        if record["state"] != "STARTING" or "pid" in identity:
            validate_identity_record(identity)
        return record
    except (ValueError, IndexError) as exc:
        raise SharedNATSError("shared_nats_reconciliation_required", "invalid lifecycle record") from exc


def _record(lease: StreamLeaseV1, state: str, identity: dict[str, Any]) -> None:
    lease.append_bytes(
        (
            json.dumps({"version": 1, "state": state, "identity": identity}, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8"),
        fsync_file=True,
        fsync_parent_on_create=True,
    )


def _writer(arguments: dict[str, str], timeout: float = 0.005) -> LockedRegularFileSetV1 | None:
    try:
        return LockedRegularFileSetV1.acquire(**arguments, logical_paths=(_WRITER,), timeout_seconds=timeout)
    except LockedRegularFileError as exc:
        if exc.code == "lock_acquisition_timeout":
            return None
        raise


def _native_ready(identity: dict[str, Any], deadline: float, cancel: threading.Event) -> dict[str, Any]:
    while True:
        remaining = _remaining(deadline, cancel)
        validate_identity(identity)
        info = probe_info(identity["host"], identity["port"], min(0.2, remaining), ping=True)
        if info is not None:
            if info.get("server_name") != identity["generation"]:
                raise SharedNATSError("shared_nats_identity_conflict", "endpoint is not the launched server")
            return info
        cancel.wait(min(0.025, _remaining(deadline, cancel)))


def _drain(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    descriptor = os.pidfd_open(child.pid)
    try:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with contextlib.suppress(ProcessLookupError):
                signal.pidfd_send_signal(descriptor, sig)
            try:
                child.wait(timeout=2)
                return
            except subprocess.TimeoutExpired:
                continue
        raise SharedNATSError("shared_nats_reconciliation_required", "exact child drain not proved")
    finally:
        os.close(descriptor)


def _open_log(path: Path) -> TextIO:
    try:
        return KernelFileSystem(str(path.parent), get_default_adapter()).workspace_open_log_append(path.name)
    except (ValueError, PathSecurityError, GuardedRegularFileSnapshotError) as exc:
        raise SharedNATSError("shared_nats_identity_conflict", "unsafe service log") from exc


def _start(
    arguments: dict[str, str],
    lease: StreamLeaseV1,
    writer: LockedRegularFileSetV1,
    executable: Path,
    store: Path,
    host: str,
    port: int,
    deadline: float,
    cancel: threading.Event,
    migration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ticket = writer.detach_for_inheritance()
    child: subprocess.Popen[bytes] | None = None
    committed = False
    generation = "polaris-shared-" + uuid.uuid4().hex
    try:
        _remaining(deadline, cancel)
        _record(lease, "STARTING", {"generation": generation, "store": str(store), "host": host, "port": port})
        with (
            _open_log(store.parent / "nats-server.stdout.log") as stdout,
            _open_log(store.parent / "nats-server.stderr.log") as stderr,
        ):
            _remaining(deadline, cancel)
            child = subprocess.Popen(
                [
                    str(executable.resolve()),
                    "-js",
                    "-a",
                    host,
                    "-p",
                    str(port),
                    "-sd",
                    str(store),
                    "--name",
                    generation,
                ],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                pass_fds=ticket.pass_fds,
                **isolated_process_group_kwargs(),
            )
        _PENDING[child.pid] = (child, ticket)
        identity = capture_identity(
            child.pid,
            generation=generation,
            executable=executable,
            store=store,
            host=host,
            port=port,
            descriptor_identities=ticket.descriptor_identities,
        )
        if migration is not None:
            identity["migration_from"] = migration
        _record(lease, "STARTING", identity)
        info = _native_ready(identity, deadline, cancel)
        ticket.validate_parent_binding()
        validate_identity(identity)
        identity["server_id"] = info["server_id"]
        _remaining(deadline, cancel)
        _record(lease, "READY", identity)
        # A slow durable append must not turn precommit cancellation/deadline into
        # late successful admission. Control remains held until this check/drain.
        _remaining(deadline, cancel)
        committed = True
        return identity
    finally:
        if child is not None and not committed:
            _drain(child)  # Failure deliberately retains ticket and child in _PENDING.
            if "identity" in locals():
                _record(lease, "STOPPED", identity)
        ticket.close()
        if child is not None:
            _PENDING.pop(child.pid, None)


def _ensure(
    host: str,
    port: int,
    executable: Path,
    store: Path,
    deadline: float,
    cancel: threading.Event,
    migration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require_capability()
    arguments = _arguments()
    if store != Path(arguments["runtime_root"]) / "nats" / "jetstream":
        raise SharedNATSError("shared_nats_identity_conflict", "store alias escapes managed root")
    with _control(arguments, deadline, cancel) as control:
        lease = control.lease(_CONTROL)
        record = _read_record(lease)
        identity = record.get("identity", {})
        writer = _writer(arguments, min(0.005, _remaining(deadline, cancel)))
        if writer is None:
            if not identity or "pid" not in identity:
                raise SharedNATSError("shared_nats_reconciliation_required", "live writer without complete identity")
            if (identity.get("host"), identity.get("port"), identity.get("store")) != (host, port, str(store)):
                raise SharedNATSError("shared_nats_identity_conflict", "live shared endpoint/store binding differs")
            info = _native_ready(identity, deadline, cancel)
            if identity.get("server_id") not in {None, info.get("server_id")}:
                raise SharedNATSError("shared_nats_identity_conflict", "server incarnation differs")
            if record["state"] == "STARTING":
                provisional = dict(identity)
                identity["server_id"] = info["server_id"]
                try:
                    _remaining(deadline, cancel)
                    _record(lease, "READY", identity)
                    _remaining(deadline, cancel)
                except BaseException:
                    # No other coordinator can observe admission until control is
                    # released. Restore provisional head; never kill borrowed writer.
                    _record(lease, "STARTING", provisional)
                    raise
            _remaining(deadline, cancel)
            return identity
        try:
            if identity.get("pid") and not incarnation_dead(identity):
                raise SharedNATSError("shared_nats_identity_conflict", "live recorded process lost writer fence")
            existing_info = probe_info(host, port, min(0.2, _remaining(deadline, cancel)))
            if existing_info is not None:
                raise SharedNATSError("shared_nats_external_service", "existing native endpoint is not owned")
            if store.exists():
                directory_identity(store)
                if any(store.iterdir()) and not identity.get("pid") and migration is None:
                    raise SharedNATSError(
                        "shared_nats_migration_required", "nonempty legacy store requires offline authorization"
                    )
                if migration is not None and migration["store_identity"] != directory_identity(store):
                    raise SharedNATSError("shared_nats_identity_conflict", "migration store changed")
                if identity.get("store_identity") and (
                    identity.get("store") != str(store) or identity["store_identity"] != directory_identity(store)
                ):
                    raise SharedNATSError("shared_nats_identity_conflict", "store binding changed")
            else:
                if record or migration is not None:
                    raise SharedNATSError("shared_nats_identity_conflict", "recorded shared store is missing")
                KernelFileSystem(arguments["runtime_root"], get_default_adapter()).workspace_mkdir(
                    "nats/jetstream", parents=True
                )
                directory_identity(store)
            return _start(arguments, lease, writer, executable, store, host, port, deadline, cancel, migration)
        finally:
            writer.close()


async def ensure_shared_nats_runtime(
    host: str, port: int, executable: Path, store: Path, deadline: float
) -> dict[str, Any]:
    return await _run_coordinator(host, port, executable, store, deadline)


async def _run_coordinator(
    host: str, port: int, executable: Path, store: Path, deadline: float, migration: dict[str, Any] | None = None
) -> dict[str, Any]:
    cancel = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_ensure, host, port, executable, store, deadline, cancel, migration))
    return await _await_coordinator(task, cancel)


async def _await_coordinator(task: asyncio.Task[_Result], cancel: threading.Event) -> _Result:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = exc
            cancel.set()
        except Exception as exc:
            if cancellation is not None:
                raise cancellation from exc
            raise
    if cancellation is not None:
        try:
            task.result()
        except Exception as exc:
            raise cancellation from exc
        raise cancellation
    return task.result()


def _unrelated_endpoint_binding(host: str, port: int, deadline: float, cancel: threading.Event) -> bool:
    """Read existing binding under its control lease; never provision or mutate it."""
    arguments = _arguments()
    while True:
        try:
            control = LockedRegularFileSetV1.acquire(
                **arguments, logical_paths=(_CONTROL,), timeout_seconds=min(0.05, _remaining(deadline, cancel))
            )
            break
        except LockedRegularFileError as exc:
            if exc.code != "lock_acquisition_timeout":
                raise
    with control:
        identity = _read_record(control.lease(_CONTROL)).get("identity", {})
        _remaining(deadline, cancel)
        # Loopback aliases on one port may name the same binding. Only a different
        # valid port proves an unrelated endpoint; same-binding conflicts stay gated.
        recorded_port = identity.get("port")
        return (
            host in {"127.0.0.1", "localhost", "::1"}
            and identity.get("host") in {"127.0.0.1", "localhost", "::1"}
            and type(recorded_port) is int
            and 0 < recorded_port < 65536
            and recorded_port != port
        )


async def ready_endpoint_is_external(host: str, port: int, deadline: float) -> bool:
    """Classify an already observed native endpoint without claiming ownership."""
    if not has_shared_authority():
        return True
    cancel = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_unrelated_endpoint_binding, host, port, deadline, cancel))
    return await _await_coordinator(task, cancel)


async def migrate_shared_nats_runtime(
    expected_legacy_identity: dict[str, Any],
    executable: Path,
    *,
    startup_timeout_seconds: float = 60.0,
    allow_store_migration: bool = False,
) -> dict[str, Any]:
    """Explicit offline migration, retaining physical store; never called on attach.

    Caller must quiesce clients and retain an offline backup before authorization.
    A captured retired process/store identity is required; no adoption of a live
    legacy writer or implicit permission from a populated directory.
    """
    if not allow_store_migration:
        raise SharedNATSError("shared_nats_migration_not_authorized")
    require_capability()
    if not 0 < startup_timeout_seconds <= 90:
        raise ValueError("startup budget must be finite and in (0, 90]")
    old = dict(expected_legacy_identity)
    required = {
        "pid",
        "start_ticks",
        "boot_id",
        "uid",
        "executable",
        "executable_sha256",
        "store",
        "store_identity",
        "host",
        "port",
    }
    if not required <= old.keys() or old["executable"] != str(executable.resolve()) or old["uid"] != os.getuid():
        raise SharedNATSError("shared_nats_identity_conflict", "incomplete legacy process proof")
    if not incarnation_dead(old):
        raise SharedNATSError("shared_nats_legacy_writer_alive")
    store = Path(old["store"])
    expected_store = Path(kernelone_home()).absolute() / "runtime" / "nats" / "jetstream"
    if store != expected_store or directory_identity(store) != old["store_identity"]:
        raise SharedNATSError("shared_nats_identity_conflict", "legacy store binding differs")
    return await _run_coordinator(
        old["host"], old["port"], executable, store, time.monotonic() + startup_timeout_seconds, old
    )


def has_shared_authority() -> bool:
    arguments = _arguments()
    return (Path(arguments["platform_lock_root"]) / arguments["storage_identity_token"]).exists()


def stop_shared_nats_runtime(identity: dict[str, Any], *, allow_service_disruption: bool = False) -> None:
    """Explicit identity-bound maintenance, never normal backend shutdown."""
    if not allow_service_disruption:
        raise SharedNATSError("shared_nats_stop_not_authorized")
    require_capability()
    cancel = threading.Event()
    arguments = _arguments()
    with _control(arguments, time.monotonic() + 5, cancel) as control:
        lease = control.lease(_CONTROL)
        current = _read_record(lease).get("identity")
        if current != identity:
            raise SharedNATSError("shared_nats_identity_conflict", "maintenance generation differs")
        descriptor = os.pidfd_open(int(identity["pid"]))
        try:
            validate_identity(identity)
            for sig in (signal.SIGTERM, signal.SIGKILL):
                with contextlib.suppress(ProcessLookupError):
                    signal.pidfd_send_signal(descriptor, sig)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    writer = _writer(arguments)
                    if writer is not None:
                        writer.close()
                        _record(lease, "STOPPED", identity)
                        return
                    time.sleep(0.01)
            raise SharedNATSError("shared_nats_reconciliation_required", "writer still fenced after stop")
        finally:
            os.close(descriptor)
