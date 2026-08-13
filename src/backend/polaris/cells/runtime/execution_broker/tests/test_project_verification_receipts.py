"""Attack tests for owner-sealed project verification receipts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.runtime.execution_broker.internal import project_verification_authority as authority_module
from polaris.cells.runtime.execution_broker.public.project_verification import (
    ConsumeProjectVerificationCapabilityCommandV1,
    ProjectArtifactExecutionAuthorityV1,
    ProjectArtifactReceiptV1,
    ProjectVerificationArtifactInputV1,
    ProjectVerificationCapabilityConsumptionV1,
    ProjectVerificationExecutionAuthorityV1,
    ProjectVerificationProcessResultV1,
    ProjectVerificationReceiptV1,
    QueryProjectArtifactReceiptV1,
    QueryProjectVerificationReceiptV1,
    RecordProjectArtifactCommandV1,
    ResolveProjectArtifactAuthorityQueryV1,
    ResolveProjectVerificationAuthorityQueryV1,
    RunProjectVerificationCommandV1,
    authorize_project_verification_command,
    query_project_artifact_receipt,
    query_project_verification_receipt,
    record_project_artifact,
    run_project_verification,
)

_CONTRACT_HASH = "a" * 64


def _authority_hash(*, task_id: str, modality: str, argv: tuple[str, ...], cwd: str) -> str:
    payload = {
        "domain": "polaris.project_completion_verification_command_authority.v1",
        "task_id": task_id,
        "modality": modality,
        "argv": list(argv),
        "cwd": cwd,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _Runner:
    def __init__(self, *, exit_code: int = 0, timed_out: bool = False, output: bytes = b"1 passed in 0.01s\n") -> None:
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.output = output
        self.calls = 0
        self.last_metadata: dict[str, str] = {}
        self._lock = threading.Lock()

    def run(
        self,
        *,
        name: str,
        argv: tuple[str, ...],
        cwd: str,
        timeout_seconds: float,
        log_path: str,
        metadata: dict[str, str],
        on_launched: Callable[[str, int | None, str | None], None],
    ) -> ProjectVerificationProcessResultV1:
        del name, argv, cwd, timeout_seconds
        with self._lock:
            self.calls += 1
            self.last_metadata = dict(metadata)
        on_launched("execution-test", 4242, "fake-process-start")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_bytes(self.output)
        return ProjectVerificationProcessResultV1(
            exit_code=self.exit_code,
            timed_out=self.timed_out,
            output_bytes=self.output,
        )


class _FlakyRunner(_Runner):
    def run(
        self,
        *,
        name: str,
        argv: tuple[str, ...],
        cwd: str,
        timeout_seconds: float,
        log_path: str,
        metadata: dict[str, str],
        on_launched: Callable[[str, int | None, str | None], None],
    ) -> ProjectVerificationProcessResultV1:
        if self.calls == 0:
            self.calls += 1
            raise OSError("transient spawn failure")
        return super().run(
            name=name,
            argv=argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            log_path=log_path,
            metadata=metadata,
            on_launched=on_launched,
        )


class _AuthorityPort:
    def __init__(self, *, authority_hash: str | None = None, timeout_seconds: float = 30.0) -> None:
        self.authority_hash = authority_hash
        self.timeout_seconds = timeout_seconds

    def resolve_project_verification_authority(
        self,
        query: ResolveProjectVerificationAuthorityQueryV1,
    ) -> ProjectVerificationExecutionAuthorityV1:
        argv = ("python", "-m", "pytest", "-q")
        executable_path = Path(sys.executable).absolute()
        executable_realpath = executable_path.resolve(strict=True)
        return ProjectVerificationExecutionAuthorityV1(
            workspace=query.workspace,
            project_id=query.project_id,
            run_id=query.run_id,
            completion_contract_hash=query.completion_contract_hash,
            obligation_id=query.obligation_id,
            owner_task_id="task-1",
            modality="test",
            argv=argv,
            cwd=".",
            command_authority_hash=self.authority_hash
            or _authority_hash(task_id="task-1", modality="test", argv=argv, cwd="."),
            input_artifacts=(ProjectVerificationArtifactInputV1(obligation_id="artifact.main", path="src/main.py"),),
            timeout_seconds=self.timeout_seconds,
            job_token_id="job-token-1",
            job_token_set_hash="c" * 64,
            execution_policy_hash="d" * 64,
            authority_revision="e" * 64,
            policy_profile_id="pytest",
            policy_decision_hash="f" * 64,
            executable_path=str(executable_path),
            executable_realpath=str(executable_realpath),
            executable_hash=hashlib.sha256(executable_realpath.read_bytes()).hexdigest(),
        )

    def resolve_project_artifact_authority(
        self,
        query: ResolveProjectArtifactAuthorityQueryV1,
    ) -> ProjectArtifactExecutionAuthorityV1:
        return ProjectArtifactExecutionAuthorityV1(
            workspace=query.workspace,
            project_id=query.project_id,
            run_id=query.run_id,
            completion_contract_hash=query.completion_contract_hash,
            obligation_id=query.obligation_id,
            owner_task_id="task-1",
            path="src/main.py",
            job_token_id="job-token-1",
            job_token_set_hash="c" * 64,
            execution_policy_hash="d" * 64,
            authority_revision="e" * 64,
        )

    def consume_project_verification_execution_capability(
        self,
        command: ConsumeProjectVerificationCapabilityCommandV1,
    ) -> ProjectVerificationCapabilityConsumptionV1:
        return ProjectVerificationCapabilityConsumptionV1(
            capability_id=authority_module._hash_payload(
                {"effect_key": command.effect_key, "attempt_id": command.attempt_id}
            ),
            effect_key=command.effect_key,
            attempt_id=command.attempt_id,
            authority_revision=command.authority_revision,
            job_token_id=command.job_token_id,
            job_token_set_hash=command.job_token_set_hash,
            execution_policy_hash=command.execution_policy_hash,
            policy_profile_id=command.policy_profile_id,
            policy_decision_hash=command.policy_decision_hash,
        )


class _ChangedAuthorityPort(_AuthorityPort):
    def resolve_project_verification_authority(
        self,
        query: ResolveProjectVerificationAuthorityQueryV1,
    ) -> ProjectVerificationExecutionAuthorityV1:
        authority = super().resolve_project_verification_authority(query)
        argv = ("python", "--version")
        return replace(
            authority,
            argv=argv,
            command_authority_hash=_authority_hash(task_id="task-1", modality="test", argv=argv, cwd="."),
        )


class _RevokedAtConsumePort(_AuthorityPort):
    def consume_project_verification_execution_capability(
        self,
        command: ConsumeProjectVerificationCapabilityCommandV1,
    ) -> ProjectVerificationCapabilityConsumptionV1:
        del command
        raise ValueError("verification capability revoked before consume")


class _MutableAuthorityPort(_AuthorityPort):
    revoked = False

    def resolve_project_verification_authority(
        self,
        query: ResolveProjectVerificationAuthorityQueryV1,
    ) -> ProjectVerificationExecutionAuthorityV1:
        authority = super().resolve_project_verification_authority(query)
        return replace(authority, authority_revision="9" * 64) if self.revoked else authority


class _EntrypointAuthorityPort(_AuthorityPort):
    def resolve_project_verification_authority(
        self,
        query: ResolveProjectVerificationAuthorityQueryV1,
    ) -> ProjectVerificationExecutionAuthorityV1:
        authority = super().resolve_project_verification_authority(query)
        argv = ("python", "-m", "http.server")
        return replace(
            authority,
            modality="entrypoint",
            argv=argv,
            command_authority_hash=_authority_hash(
                task_id=authority.owner_task_id,
                modality="entrypoint",
                argv=argv,
                cwd=authority.cwd,
            ),
            policy_profile_id="python.module_entrypoint",
        )


class _RevokingRunner(_Runner):
    def __init__(self, port: _MutableAuthorityPort) -> None:
        super().__init__()
        self.port = port

    def run(self, **kwargs) -> ProjectVerificationProcessResultV1:  # type: ignore[no-untyped-def]
        result = super().run(**kwargs)
        self.port.revoked = True
        return result


class _ReadyEntrypointRunner(_Runner):
    def run(
        self,
        *,
        name: str,
        argv: tuple[str, ...],
        cwd: str,
        timeout_seconds: float,
        log_path: str,
        metadata: dict[str, str],
        on_launched: Callable[[str, int | None, str | None], None],
    ) -> ProjectVerificationProcessResultV1:
        del name, argv, cwd, timeout_seconds
        self.calls += 1
        self.last_metadata = dict(metadata)
        on_launched("execution-ready", 4242, "ready-process-start")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_bytes(b"listening\n")
        return ProjectVerificationProcessResultV1(
            exit_code=None,
            timed_out=False,
            output_bytes=b"listening\n",
            process_pid=4242,
            process_start_token="ready-process-start",
            readiness_probe_kind="process_liveness",
            readiness_satisfied=True,
            controlled_termination=True,
        )


@pytest.fixture(autouse=True)
def _bind_authority_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(authority_module, "_EXECUTION_AUTHORITY_PORT", _AuthorityPort())


def _bind_runner(monkeypatch: pytest.MonkeyPatch, runner: _Runner) -> None:
    monkeypatch.setattr(authority_module._ExecutionBrokerProjectVerificationRunner, "run", runner.run)


def _artifact_command(workspace: Path) -> RecordProjectArtifactCommandV1:
    return RecordProjectArtifactCommandV1(
        workspace=str(workspace),
        project_id="project-1",
        run_id="run-1",
        completion_contract_hash=_CONTRACT_HASH,
        obligation_id="artifact.main",
        owner_task_id="task-1",
        path="src/main.py",
    )


def _artifact_query(workspace: Path) -> QueryProjectArtifactReceiptV1:
    return QueryProjectArtifactReceiptV1(
        workspace=str(workspace),
        project_id="project-1",
        run_id="run-1",
        completion_contract_hash=_CONTRACT_HASH,
        obligation_id="artifact.main",
        owner_task_id="task-1",
        path="src/main.py",
    )


def _verification_command(workspace: Path) -> RunProjectVerificationCommandV1:
    return authorize_project_verification_command(
        ResolveProjectVerificationAuthorityQueryV1(
            workspace=str(workspace),
            project_id="project-1",
            run_id="run-1",
            completion_contract_hash=_CONTRACT_HASH,
            obligation_id="verify.test",
        )
    )


def _verification_query(command: RunProjectVerificationCommandV1) -> QueryProjectVerificationReceiptV1:
    return QueryProjectVerificationReceiptV1(
        workspace=command.workspace,
        project_id=command.project_id,
        run_id=command.run_id,
        completion_contract_hash=command.completion_contract_hash,
        obligation_id=command.obligation_id,
        owner_task_id=command.owner_task_id,
        modality=command.modality,
        argv=command.argv,
        cwd=command.cwd,
        command_authority_hash=command.command_authority_hash,
        input_artifacts=command.input_artifacts,
        timeout_seconds=command.timeout_seconds,
        job_token_id=command.job_token_id,
        job_token_set_hash=command.job_token_set_hash,
        execution_policy_hash=command.execution_policy_hash,
        authority_revision=command.authority_revision,
        policy_profile_id=command.policy_profile_id,
        policy_decision_hash=command.policy_decision_hash,
        executable_path=command.executable_path,
        executable_realpath=command.executable_realpath,
        executable_hash=command.executable_hash,
    )


def test_artifact_receipt_hashes_real_bytes_and_drift_invalidates(tmp_path: Path) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('one')\n", encoding="utf-8")

    first = record_project_artifact(_artifact_command(tmp_path))
    assert first.artifact_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    assert first.owner_module_id == "runtime.execution_broker"
    assert query_project_artifact_receipt(_artifact_query(tmp_path)) == first

    source.write_text("print('two')\n", encoding="utf-8")

    assert query_project_artifact_receipt(_artifact_query(tmp_path)) is None


def test_artifact_recording_requires_current_owner_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    forged = replace(_artifact_command(tmp_path), path="src/other.py")
    (tmp_path / "src" / "other.py").write_text("print('forged')\n", encoding="utf-8")

    with pytest.raises(ValueError, match="artifact authority"):
        record_project_artifact(forged)


def test_receipt_store_is_platform_owned_not_target_workspace(tmp_path: Path) -> None:
    """Target code must not own or be able to replace authoritative receipts."""

    store_path = authority_module._db_path(str(tmp_path))

    assert not store_path.is_relative_to(tmp_path)


def test_spawn_identity_is_persisted_before_receipt_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    _bind_runner(monkeypatch, _Runner())

    result = run_project_verification(_verification_command(tmp_path))

    assert result.receipt is not None
    connection = authority_module._connect(str(tmp_path))
    try:
        events = authority_module._read_authenticated_events(connection, workspace=str(tmp_path))
    finally:
        connection.close()
    spawned = next(item for item in events if item.get("process_execution_id") == "execution-test")
    assert spawned["process_pid"] == 4242
    assert spawned["process_start_token"] == "fake-process-start"


def test_expired_spawn_is_terminated_before_retry_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect_key = "command:" + "d" * 64
    request_hash = "d" * 64
    event = authority_module._started_event(
        effect_key=effect_key,
        kind="command",
        request_hash=request_hash,
        attempt_number=1,
        lease_seconds=-1.0,
    )
    event.update(
        process_execution_id="execution-orphan",
        process_pid=777,
        process_start_token="orphan-start",
    )
    connection = authority_module._connect(str(tmp_path))
    try:
        connection.execute("BEGIN IMMEDIATE")
        authority_module._append_authenticated_event(connection, workspace=str(tmp_path), payload=event)
        connection.execute("COMMIT")
    finally:
        connection.close()
    terminated: list[tuple[int, str]] = []
    monkeypatch.setattr(
        authority_module,
        "_terminate_fenced_process",
        lambda *, pid, process_start_token: terminated.append((pid, process_start_token)),
        raising=False,
    )

    reservation = authority_module._reserve(
        str(tmp_path),
        effect_key=effect_key,
        kind="command",
        request_hash=request_hash,
    )

    assert terminated == [(777, "orphan-start")]
    assert reservation.state == "reserved"
    assert reservation.attempt_number == 2


def test_process_fence_never_signals_a_reused_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(authority_module, "_process_start_token", lambda _pid: "new-process")
    monkeypatch.setattr(authority_module.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    authority_module._terminate_fenced_process(pid=777, process_start_token="old-process")

    assert signals == []


def test_authenticated_append_only_provenance_rejects_receipt_body_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    command = _verification_command(tmp_path)
    _bind_runner(monkeypatch, _Runner(exit_code=7))
    assert run_project_verification(command).receipt is not None

    connection = sqlite3.connect(authority_module._db_path(str(tmp_path)))
    try:
        row = connection.execute(
            "SELECT sequence, event_json FROM project_verification_receipt_events "
            "WHERE effect_key LIKE 'command:%' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        payload = json.loads(str(row[1]))
        payload["receipt_payload"]["exit_code"] = 0
        payload["receipt_hash"] = authority_module._hash_payload(payload["receipt_payload"])
        connection.execute(
            "UPDATE project_verification_receipt_events SET event_json = ? WHERE sequence = ?",
            (authority_module._canonical_json(payload), int(row[0])),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValueError, match="authenticated receipt provenance"):
        query_project_verification_receipt(_verification_query(command))


def test_query_validates_full_receipt_identity_not_only_input_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    command = _verification_command(tmp_path)
    _bind_runner(monkeypatch, _Runner())
    receipt = run_project_verification(command).receipt
    assert receipt is not None
    forged_payload = {
        "schema_version": "runtime.execution_broker.project_verification_receipt.v1",
        "owner_module_id": "runtime.execution_broker",
        "workspace": receipt.workspace,
        "project_id": "project-other",
        "run_id": receipt.run_id,
        "completion_contract_hash": receipt.completion_contract_hash,
        "obligation_id": receipt.obligation_id,
        "owner_task_id": receipt.owner_task_id,
        "modality": receipt.modality,
        "argv": list(receipt.argv),
        "cwd": receipt.cwd,
        "command_authority_hash": receipt.command_authority_hash,
        "job_token_id": receipt.job_token_id,
        "job_token_set_hash": receipt.job_token_set_hash,
        "execution_policy_hash": receipt.execution_policy_hash,
        "authority_revision": receipt.authority_revision,
        "policy_profile_id": receipt.policy_profile_id,
        "policy_decision_hash": receipt.policy_decision_hash,
        "executable_path": receipt.executable_path,
        "executable_realpath": receipt.executable_realpath,
        "executable_hash": receipt.executable_hash,
        "capability_id": receipt.capability_id,
        "attempt_id": receipt.attempt_id,
        "input_artifacts": [
            {
                "obligation_id": item.obligation_id,
                "path": item.path,
                "artifact_hash": item.artifact_hash,
            }
            for item in receipt.input_artifacts
        ],
        "input_artifact_hash": receipt.input_artifact_hash,
        "timeout_seconds": receipt.timeout_seconds,
        "exit_code": receipt.exit_code,
        "timed_out": receipt.timed_out,
        "output_hash": receipt.output_hash,
        "proof_satisfied": receipt.proof_satisfied,
        "proof_evidence_hash": receipt.proof_evidence_hash,
        "process_pid": receipt.process_pid,
        "process_start_token": receipt.process_start_token,
        "readiness_probe_kind": receipt.readiness_probe_kind,
        "readiness_satisfied": receipt.readiness_satisfied,
        "controlled_termination": receipt.controlled_termination,
    }
    forged_hash = authority_module._hash_payload(forged_payload)

    def _forged_row(workspace: str, effect_key: str):
        del workspace
        return (
            "completed",
            effect_key.removeprefix("command:"),
            forged_hash,
            authority_module._canonical_json(forged_payload),
        )

    monkeypatch.setattr(authority_module, "_read_row", _forged_row)

    assert query_project_verification_receipt(_verification_query(command)) is None


def test_artifact_receipt_direct_construction_and_retag_are_rejected(tmp_path: Path) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    receipt = record_project_artifact(_artifact_command(tmp_path))

    kwargs = {
        field: getattr(receipt, field)
        for field in (
            "workspace",
            "project_id",
            "run_id",
            "completion_contract_hash",
            "obligation_id",
            "owner_task_id",
            "path",
            "artifact_hash",
            "job_token_id",
            "job_token_set_hash",
            "execution_policy_hash",
            "authority_revision",
            "receipt_hash",
            "receipt_ref",
        )
    }
    with pytest.raises(ValueError, match="owner-sealed"):
        ProjectArtifactReceiptV1(**kwargs)
    with pytest.raises(ValueError, match="owner-sealed"):
        replace(receipt, project_id="project-other")


def test_arbitrary_public_command_and_runner_injection_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    runner = _Runner()
    _bind_runner(monkeypatch, runner)
    argv = ("rm", "-rf", ".")

    with pytest.raises(ValueError, match="owner-authorized"):
        RunProjectVerificationCommandV1(
            workspace=str(tmp_path),
            project_id="project-1",
            run_id="run-1",
            completion_contract_hash=_CONTRACT_HASH,
            obligation_id="verify.test",
            owner_task_id="task-1",
            modality="test",
            argv=argv,
            cwd=".",
            command_authority_hash=_authority_hash(task_id="task-1", modality="test", argv=argv, cwd="."),
            input_artifacts=(ProjectVerificationArtifactInputV1(obligation_id="artifact.main", path="src/main.py"),),
            timeout_seconds=30.0,
            job_token_id="forged",
            job_token_set_hash="c" * 64,
            execution_policy_hash="d" * 64,
        )
    command = _verification_command(tmp_path)
    with pytest.raises(ValueError, match="owner-sealed"):
        ConsumeProjectVerificationCapabilityCommandV1(
            **{name: getattr(command, name) for name in ProjectVerificationExecutionAuthorityV1.__dataclass_fields__},
            effect_key="command:" + "1" * 64,
            attempt_id="2" * 64,
        )
    with pytest.raises(TypeError):
        run_project_verification(command, runner=runner)  # type: ignore[call-arg]

    assert runner.calls == 0


def test_authority_hash_mismatch_fails_before_physical_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    runner = _Runner()
    _bind_runner(monkeypatch, runner)
    monkeypatch.setattr(authority_module, "_EXECUTION_AUTHORITY_PORT", _AuthorityPort(authority_hash="b" * 64))
    query = ResolveProjectVerificationAuthorityQueryV1(
        workspace=str(tmp_path),
        project_id="project-1",
        run_id="run-1",
        completion_contract_hash=_CONTRACT_HASH,
        obligation_id="verify.test",
    )

    with pytest.raises(ValueError, match="command_authority_hash"):
        authorize_project_verification_command(query)

    assert runner.calls == 0


def test_authority_change_between_authorize_and_spawn_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    command = _verification_command(tmp_path)
    runner = _Runner()
    _bind_runner(monkeypatch, runner)
    monkeypatch.setattr(authority_module, "_EXECUTION_AUTHORITY_PORT", _ChangedAuthorityPort())

    with pytest.raises(ValueError, match="authority changed before spawn"):
        run_project_verification(command)

    assert runner.calls == 0


def test_revocation_at_atomic_capability_consume_prevents_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    command = _verification_command(tmp_path)
    runner = _Runner()
    _bind_runner(monkeypatch, runner)
    monkeypatch.setattr(authority_module, "_EXECUTION_AUTHORITY_PORT", _RevokedAtConsumePort())

    with pytest.raises(ValueError, match="revoked before consume"):
        run_project_verification(command)

    assert runner.calls == 0


def test_authority_change_after_process_run_prevents_receipt_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    port = _MutableAuthorityPort()
    monkeypatch.setattr(authority_module, "_EXECUTION_AUTHORITY_PORT", port)
    command = _verification_command(tmp_path)
    runner = _RevokingRunner(port)
    _bind_runner(monkeypatch, runner)

    with pytest.raises(ValueError, match="authority changed during physical execution"):
        run_project_verification(command)

    assert runner.calls == 1
    assert query_project_verification_receipt(_verification_query(command)) is None


def test_receipt_query_revalidates_current_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    port = _MutableAuthorityPort()
    monkeypatch.setattr(authority_module, "_EXECUTION_AUTHORITY_PORT", port)
    command = _verification_command(tmp_path)
    _bind_runner(monkeypatch, _Runner())
    assert run_project_verification(command).receipt is not None

    port.revoked = True

    assert query_project_verification_receipt(_verification_query(command)) is None


def test_ready_long_lived_entrypoint_is_controlled_and_receipted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(authority_module, "_EXECUTION_AUTHORITY_PORT", _EntrypointAuthorityPort())
    command = _verification_command(tmp_path)
    runner = _ReadyEntrypointRunner()
    _bind_runner(monkeypatch, runner)

    receipt = run_project_verification(command).receipt

    assert receipt is not None
    assert receipt.exit_code is None
    assert receipt.timed_out is False
    assert receipt.process_pid == 4242
    assert receipt.process_start_token == "ready-process-start"
    assert receipt.readiness_probe_kind == "process_liveness"
    assert receipt.readiness_satisfied is True
    assert receipt.controlled_termination is True
    assert receipt.proof_satisfied is True
    assert receipt.succeeded is True


@pytest.mark.asyncio
async def test_entrypoint_runner_probes_liveness_then_controls_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.runtime.execution_broker.public import service as broker_service_module
    from polaris.cells.runtime.execution_broker.public.contracts import (
        ExecutionProcessHandleV1,
        ExecutionProcessLaunchResultV1,
        ExecutionProcessStatusV1,
        ExecutionProcessWaitResultV1,
    )

    handle = ExecutionProcessHandleV1(
        execution_id="entrypoint-ready",
        pid=os.getpid(),
        name="entrypoint",
        workspace=str(tmp_path),
    )

    class _ReadyService:
        wait_calls = 0
        terminated = False

        async def launch_process(self, command):  # type: ignore[no-untyped-def]
            assert command.log_path is not None
            path = Path(command.log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"listening\n")
            return ExecutionProcessLaunchResultV1(success=True, handle=handle)

        async def wait_process(self, _handle, *, timeout_seconds):  # type: ignore[no-untyped-def]
            del timeout_seconds
            self.wait_calls += 1
            if self.wait_calls == 1:
                return ExecutionProcessWaitResultV1(
                    handle=handle,
                    status=ExecutionProcessStatusV1.RUNNING,
                    success=False,
                    timed_out=True,
                )
            return ExecutionProcessWaitResultV1(
                handle=handle,
                status=ExecutionProcessStatusV1.CANCELLED,
                success=False,
                exit_code=None,
                timed_out=False,
            )

        async def terminate_process(self, _handle):  # type: ignore[no-untyped-def]
            self.terminated = True
            return True

    service = _ReadyService()
    monkeypatch.setattr(broker_service_module, "get_execution_broker_service", lambda: service)
    launches: list[tuple[str, int | None, str | None]] = []
    result = await authority_module._ExecutionBrokerProjectVerificationRunner()._run_async(
        name="entrypoint",
        argv=(str(Path(sys.executable).absolute()), "-m", "http.server"),
        cwd=str(tmp_path),
        timeout_seconds=30.0,
        log_path=str(tmp_path / ".polaris" / "entrypoint.log"),
        metadata={"modality": "entrypoint"},
        on_launched=lambda execution_id, pid, token: launches.append((execution_id, pid, token)),
    )

    assert launches and launches[0][0] == "entrypoint-ready"
    assert service.wait_calls == 2
    assert service.terminated is True
    assert result.timed_out is False
    assert result.readiness_probe_kind == "process_liveness"
    assert result.readiness_satisfied is True
    assert result.controlled_termination is True


@pytest.mark.parametrize("timeout_seconds", [float("nan"), float("inf"), -1.0, 3600.1])
def test_non_finite_or_excessive_timeout_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timeout_seconds: float,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(
        authority_module,
        "_EXECUTION_AUTHORITY_PORT",
        _AuthorityPort(timeout_seconds=timeout_seconds),
    )
    with pytest.raises(ValueError, match="finite"):
        _verification_command(tmp_path)


def test_command_receipt_binds_exact_command_input_and_physical_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    command = _verification_command(tmp_path)
    runner = _Runner(exit_code=7, output=b"FAILED test_main\n")
    _bind_runner(monkeypatch, runner)

    result = run_project_verification(command)
    receipt = result.receipt

    assert receipt is not None
    assert receipt.argv == command.argv
    assert receipt.cwd == "."
    assert receipt.exit_code == 7
    assert receipt.timed_out is False
    assert receipt.succeeded is False
    assert receipt.output_hash == hashlib.sha256(b"FAILED test_main\n").hexdigest()
    assert receipt.input_artifact_hash
    assert runner.last_metadata["capability_id"] == receipt.capability_id
    assert runner.last_metadata["attempt_id"] == receipt.attempt_id
    assert runner.last_metadata["policy_decision_hash"] == receipt.policy_decision_hash
    assert query_project_verification_receipt(_verification_query(command)) == receipt

    # A physical failure is present+failed, never reclassified as missing.
    assert result.code == "project_verification_executed"
    assert result.receipt is not None


def test_command_receipt_direct_construction_replace_and_lookalike_query_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    command = _verification_command(tmp_path)
    _bind_runner(monkeypatch, _Runner())
    receipt = run_project_verification(command).receipt
    assert receipt is not None

    kwargs = {
        field: getattr(receipt, field)
        for field in (
            "workspace",
            "project_id",
            "run_id",
            "completion_contract_hash",
            "obligation_id",
            "owner_task_id",
            "modality",
            "argv",
            "cwd",
            "command_authority_hash",
            "job_token_id",
            "job_token_set_hash",
            "execution_policy_hash",
            "authority_revision",
            "policy_profile_id",
            "policy_decision_hash",
            "executable_path",
            "executable_realpath",
            "executable_hash",
            "capability_id",
            "attempt_id",
            "input_artifacts",
            "input_artifact_hash",
            "timeout_seconds",
            "exit_code",
            "timed_out",
            "output_hash",
            "proof_satisfied",
            "proof_evidence_hash",
            "process_pid",
            "process_start_token",
            "readiness_probe_kind",
            "readiness_satisfied",
            "controlled_termination",
            "receipt_hash",
            "receipt_ref",
        )
    }
    with pytest.raises(ValueError, match="owner-sealed"):
        ProjectVerificationReceiptV1(**kwargs)
    with pytest.raises(ValueError, match="owner-sealed"):
        replace(receipt, run_id="run-other")
    with pytest.raises(TypeError, match="exact QueryProjectVerificationReceiptV1"):
        query_project_verification_receipt(object())  # type: ignore[arg-type]


def test_artifact_change_invalidates_command_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('one')\n", encoding="utf-8")
    command = _verification_command(tmp_path)
    _bind_runner(monkeypatch, _Runner())
    assert run_project_verification(command).receipt is not None

    source.write_text("print('two')\n", encoding="utf-8")

    assert query_project_verification_receipt(_verification_query(command)) is None


def test_zero_exit_without_profile_proof_is_a_failed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    command = _verification_command(tmp_path)
    _bind_runner(monkeypatch, _Runner(output=b"no tests ran in 0.01s\n"))

    result = run_project_verification(command)

    assert result.receipt is not None
    assert result.receipt.proof_satisfied is False
    assert result.receipt.succeeded is False


def test_explicit_run_retries_failed_proof_and_preserves_receipt_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    command = _verification_command(tmp_path)
    runner = _Runner(output=b"no tests ran in 0.01s\n")
    _bind_runner(monkeypatch, runner)

    failed = run_project_verification(command)
    assert failed.receipt is not None
    assert failed.receipt.proof_satisfied is False

    runner.output = b"1 passed in 0.01s\n"
    recovered = run_project_verification(command)

    assert runner.calls == 2
    assert recovered.spawned is True
    assert recovered.receipt is not None
    assert recovered.receipt.succeeded is True
    assert recovered.receipt.receipt_hash != failed.receipt.receipt_hash
    assert query_project_verification_receipt(_verification_query(command)) == recovered.receipt

    connection = authority_module._connect(str(tmp_path))
    try:
        events = authority_module._read_authenticated_events(connection, workspace=str(tmp_path))
    finally:
        connection.close()
    completed = tuple(event for event in events if event.get("kind") == "command" and event.get("state") == "completed")
    assert tuple(event.get("attempt_number") for event in completed) == (1, 2)
    assert tuple(event.get("receipt_hash") for event in completed) == (
        failed.receipt.receipt_hash,
        recovered.receipt.receipt_hash,
    )


def test_concurrent_same_effect_executes_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    command = _verification_command(tmp_path)
    runner = _Runner()
    _bind_runner(monkeypatch, runner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: run_project_verification(command), range(2)))

    assert runner.calls == 1
    assert any(item.receipt is not None for item in results)
    assert {item.code for item in results}.issubset(
        {
            "project_verification_executed",
            "project_verification_receipt_reused",
            "project_verification_in_progress",
        }
    )


def test_transient_runner_failure_allows_one_bounded_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    command = _verification_command(tmp_path)
    runner = _FlakyRunner()
    _bind_runner(monkeypatch, runner)

    with pytest.raises(OSError, match="transient spawn failure"):
        run_project_verification(command)
    retried = run_project_verification(command)

    assert runner.calls == 2
    assert retried.spawned is True
    assert retried.receipt is not None
