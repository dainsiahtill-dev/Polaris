"""A009B1 RunService-private cutoff-port injection tests."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityError,
    FactoryRoleEvidenceAuthorityPort,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_source_resolver import (
    CanonicalFactoryRoleEvidenceSourceAuthority,
)
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryRun, StageResult
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    _FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY,
    FactoryConfig,
    FactoryRunService,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
    FactoryRoleEvidenceCutoffRequestV1,
    bind_factory_role_evidence_authority,
    get_factory_role_evidence_authority_binding,
)


class _CutoffProbeExecutor:
    def __init__(self) -> None:
        self.service: FactoryRunService | None = None
        self.ports: list[FactoryRoleEvidenceAuthorityPort] = []
        self.source_errors: list[str] = []
        self.old_port_errors: list[str] = []
        self.captured_authorities: list[tuple[object, ...]] = []
        self.requests: list[FactoryRoleEvidenceCutoffRequestV1] = []

    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        assert self.service is not None
        port = context[_FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY]
        assert isinstance(port, FactoryRoleEvidenceAuthorityPort)
        assert isinstance(port._source_authority, CanonicalFactoryRoleEvidenceSourceAuthority)
        assert port._run_lock is self.service._get_run_lock(run.id)
        lease = self.service._admission.current()
        assert lease is not None and lease.stage_execution_claim is not None
        claim = lease.stage_execution_claim
        authority = port._authority
        self.captured_authorities.append(
            (
                authority.factory_run_id,
                authority.stage,
                authority.workspace_fencing_token,
                authority.stage_claim_attempt,
                authority.stage_claim_nonce,
            )
        )
        assert self.captured_authorities[-1] == (
            run.id,
            stage,
            lease.fencing_token,
            claim.attempt,
            claim.nonce,
        )

        if self.ports:
            try:
                await self.ports[-1].acquire_cutoff(self.requests[-1])
            except FactoryRoleEvidenceAuthorityError as exc:
                self.old_port_errors.append(exc.code)
            else:  # pragma: no cover - fail-closed regression signal
                raise AssertionError("old cutoff port retained authority across a new stage claim")
        self.ports.append(port)
        role = {
            "pm_planning": "pm",
            "chief_engineer_review": "chief_engineer",
        }[stage]
        binding = port.mint_authority_binding(role)
        self.requests.append(
            FactoryRoleEvidenceCutoffRequestV1(
                schema_version=FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
                run_id=f"role-run:{stage}",
                role=binding.role,
                turn_id=f"turn:{stage}",
                call_id=f"call:{stage}",
                request_freeze_id=f"freeze:{stage}",
                semantic_candidate_hash=hashlib.sha256(f"candidate:{stage}".encode()).hexdigest(),
                attempt_budget=binding.attempt_budget,
                execution_authority_hash=binding.execution_authority_hash,
                candidate_refs=(f"candidate:{stage}",),
            )
        )

        return StageResult(
            stage=stage,
            status="success",
            output="cutoff port observed",
            metadata={"probe": "ok"},
        )


class _LeakingCutoffPortExecutor:
    def __init__(self, leak_mode: str) -> None:
        self.leak_mode = leak_mode

    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        port = context[_FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY]
        assert isinstance(port, FactoryRoleEvidenceAuthorityPort)
        old_port = FactoryRoleEvidenceAuthorityPort(
            workspace=port._workspace,
            authority=port._authority,
            run_lock=port._run_lock,
            run_loader=port._run_loader,
            admission=port._admission,
            source_authority=port._source_authority,
            fact_stream=port._facts,
        )
        metadata: dict[object, object]
        artifacts: list[object]
        output: object = "malicious leak"
        if self.leak_mode == "mapping_key":
            metadata = {"nested": {port: "private-authority-key"}}
            artifacts = []
        elif self.leak_mode == "artifacts":
            metadata = {}
            artifacts = [["nested", port]]
        elif self.leak_mode == "output":
            metadata = {}
            artifacts = []
            output = port
        elif self.leak_mode == "old_port":
            metadata = {"retained": old_port}
            artifacts = []
        elif self.leak_mode == "dataclass_wrapper":
            metadata = {"wrapped": _PortDataclassWrapper(old_port)}
            artifacts = []
        elif self.leak_mode == "vars_wrapper":
            metadata = {"wrapped": _PortVarsWrapper({"retained": old_port})}
            artifacts = []
        elif self.leak_mode == "slot_wrapper":
            metadata = {"wrapped": _PortSlotWrapper(old_port)}
            artifacts = []
        elif self.leak_mode == "carrier":
            metadata = {"wrapped": port.mint_authority_binding("pm")}
            artifacts = []
        else:
            metadata = {
                "nested": [
                    {
                        _FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY: port,
                    }
                ]
            }
            artifacts = []
        return StageResult(
            stage=stage,
            status="success",
            output=output,  # type: ignore[arg-type]
            artifacts=artifacts,  # type: ignore[arg-type]
            metadata=metadata,  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class _PortDataclassWrapper:
    value: object


class _PortVarsWrapper:
    def __init__(self, value: object) -> None:
        self.value = value


class _PortSlotWrapper:
    __slots__ = ("value",)

    def __init__(self, value: object) -> None:
        self.value = value


class _NeverCalledExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        del stage, run, context
        self.calls += 1
        raise AssertionError("caller-carried authority must fail before executor")


class _TerminalProbeExecutor:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.port: FactoryRoleEvidenceAuthorityPort | None = None
        self.entered = asyncio.Event()

    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        del run
        port = context[_FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY]
        assert isinstance(port, FactoryRoleEvidenceAuthorityPort)
        self.port = port
        self.entered.set()
        if self.mode == "exception":
            raise RuntimeError("executor-boom")
        if self.mode == "cancel":
            await asyncio.Future()
            raise AssertionError("unreachable")
        return StageResult(
            stage=stage,
            status="failed",
            output="failed but claim remains unsettled",
            metadata={"child_sessions_settled": False, "inflight_run_continues": True},
        )


class _LateBackgroundCutoffExecutor:
    def __init__(self) -> None:
        self.release_child = asyncio.Event()
        self.child_task: asyncio.Task[tuple[object, str]] | None = None
        self.binding: FactoryRoleEvidenceAuthorityBindingV1 | None = None

    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        port = context[_FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY]
        assert isinstance(port, FactoryRoleEvidenceAuthorityPort)
        binding = port.mint_authority_binding("pm")
        self.binding = binding
        request = FactoryRoleEvidenceCutoffRequestV1(
            schema_version=FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
            run_id=f"role-run:{run.id}",
            role=binding.role,
            turn_id="turn:late",
            call_id="call:late",
            request_freeze_id="freeze:late",
            semantic_candidate_hash=hashlib.sha256(b"candidate:late").hexdigest(),
            attempt_budget=binding.attempt_budget,
            execution_authority_hash=binding.execution_authority_hash,
            candidate_refs=("candidate:late",),
        )

        async def late_child() -> tuple[object, str]:
            inherited = get_factory_role_evidence_authority_binding()
            await self.release_child.wait()
            try:
                await port.acquire_cutoff(request)
            except FactoryRoleEvidenceAuthorityError as exc:
                return inherited, exc.code
            raise AssertionError("closed stage port accepted a late background cutoff")

        with bind_factory_role_evidence_authority(binding):
            self.child_task = asyncio.create_task(late_child())
        return StageResult(stage=stage, status="success", output="background child retained")


def _request(*, stage: str) -> FactoryRoleEvidenceCutoffRequestV1:
    slug = stage.replace("_", "-")
    return FactoryRoleEvidenceCutoffRequestV1(
        schema_version=FACTORY_ROLE_EVIDENCE_CUTOFF_REQUEST_SCHEMA,
        run_id=f"role-run:{slug}",
        role="director",
        turn_id=f"turn:{slug}",
        call_id=f"call:{slug}",
        request_freeze_id=f"freeze:{slug}",
        semantic_candidate_hash=hashlib.sha256(f"candidate:{stage}".encode()).hexdigest(),
        attempt_budget=2,
        execution_authority_hash=hashlib.sha256(f"authority:{stage}".encode()).hexdigest(),
        candidate_refs=(f"candidate:{slug}",),
    )


@pytest.mark.asyncio
async def test_run_service_overwrites_private_key_with_new_fenced_canonical_source_port(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=("task_runtime.execution",),
            maintenance_reason="a009b1_run_service_test",
        )
    )
    executor = _CutoffProbeExecutor()
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "runtime",
        executor=executor,
    )
    executor.service = service
    run = await service.create_run(
        FactoryConfig(
            name="a009b1-private-port",
            stages=["pm_planning", "chief_engineer_review"],
        )
    )
    await service.start_run(run.id)

    first = await service.execute_stage(
        run.id,
        "pm_planning",
        {_FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY: "forged-caller-value"},
    )
    second = await service.execute_stage(
        run.id,
        "chief_engineer_review",
        {_FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY: object()},
    )

    assert first.metadata == {"probe": "ok"}
    assert second.metadata == {"probe": "ok"}
    assert len(executor.ports) == 2
    assert executor.ports[0] is not executor.ports[1]
    assert executor.captured_authorities[0][3] == 1
    assert executor.captured_authorities[1][3] == 2
    assert executor.captured_authorities[0][4] != executor.captured_authorities[1][4]
    assert executor.source_errors == []
    assert executor.old_port_errors == ["factory_role_evidence_authority_closed"]
    stored = await service.store.get_run(run.id)
    assert stored is not None
    assert _FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY not in stored.metadata
    persisted_text = (service.store.get_run_dir(run.id) / "run.json").read_text(encoding="utf-8")
    assert _FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY not in persisted_text

    with pytest.raises(FactoryRoleEvidenceAuthorityError, match="authority_closed"):
        await executor.ports[-1].acquire_cutoff(executor.requests[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "leak_mode",
    [
        "metadata_value",
        "mapping_key",
        "artifacts",
        "output",
        "old_port",
        "dataclass_wrapper",
        "vars_wrapper",
        "slot_wrapper",
        "carrier",
    ],
)
async def test_run_service_fails_closed_before_persisting_nested_live_port_leak(
    tmp_path: Path,
    leak_mode: str,
) -> None:
    workspace = tmp_path / "leak-workspace"
    workspace.mkdir()
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / "leak-runtime",
        executor=_LeakingCutoffPortExecutor(leak_mode),
    )
    run = await service.create_run(FactoryConfig(name="a009b1-leak-guard", stages=["pm_planning"]))
    await service.start_run(run.id)

    with pytest.raises(RuntimeError, match="factory_role_evidence_private_port_leaked_to_stage_result"):
        await service.execute_stage(run.id, "pm_planning")

    stored = await service.store.get_run(run.id)
    assert stored is not None
    assert stored.status.value == "failed"
    persisted_result = stored.metadata["stage_results"]["pm_planning"]
    assert persisted_result["status"] == "failed"
    assert persisted_result["metadata"] == {
        "child_sessions_settled": False,
        "inflight_run_continues": True,
        "settlement_source": "factory_stage_wrapper_exception",
    }
    persisted_text = (service.store.get_run_dir(run.id) / "run.json").read_text(encoding="utf-8")
    assert _FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY not in persisted_text
    assert "FactoryRoleEvidenceAuthorityPort" not in persisted_text


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper", ["mapping", "slot"])
async def test_run_service_rejects_nested_caller_carried_authority_before_stage_claim(
    tmp_path: Path,
    wrapper: str,
) -> None:
    workspace = tmp_path / f"caller-{wrapper}"
    workspace.mkdir()
    executor = _NeverCalledExecutor()
    service = FactoryRunService(
        workspace,
        cache_root=tmp_path / f"caller-runtime-{wrapper}",
        executor=executor,
    )
    run = await service.create_run(FactoryConfig(name="caller-authority-guard", stages=["pm_planning"]))
    await service.start_run(run.id)
    fake_port = _PortVarsWrapper(None)

    async def acquire_cutoff(_request: FactoryRoleEvidenceCutoffRequestV1) -> object:
        raise AssertionError("must not call")

    async def resolve_cutoff_proof(_ack: object) -> object:
        raise AssertionError("must not call")

    fake_port.acquire_cutoff = acquire_cutoff  # type: ignore[attr-defined]
    fake_port.resolve_cutoff_proof = resolve_cutoff_proof  # type: ignore[attr-defined]
    carrier = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id=run.id,
        role="pm",
        cutoff_port=fake_port,  # type: ignore[arg-type]
        attempt_budget=32,
        execution_authority_hash="a" * 64,
    )
    context = {"nested": carrier} if wrapper == "mapping" else {"nested": _PortSlotWrapper(carrier)}

    with pytest.raises(RuntimeError, match="factory_role_evidence_private_authority_in_caller_context"):
        await service.execute_stage(run.id, "pm_planning", context)

    assert executor.calls == 0
    lease = service._admission.current()
    assert lease is not None
    assert lease.stage_execution_claim is None


@pytest.mark.asyncio
async def test_failed_result_retains_claim_but_closes_old_authority_port(tmp_path: Path) -> None:
    workspace = tmp_path / "failed-result"
    workspace.mkdir()
    executor = _TerminalProbeExecutor("failed")
    service = FactoryRunService(workspace, cache_root=tmp_path / "failed-runtime", executor=executor)
    run = await service.create_run(FactoryConfig(name="failed-result", stages=["pm_planning"]))
    await service.start_run(run.id)

    result = await service.execute_stage(run.id, "pm_planning")

    assert result.status == "failed"
    assert executor.port is not None and executor.port._closed is True
    lease = service._admission.current()
    assert lease is not None and lease.stage_execution_claim is not None


@pytest.mark.asyncio
async def test_executor_exception_closes_authority_before_failed_result_persistence(tmp_path: Path) -> None:
    workspace = tmp_path / "exception"
    workspace.mkdir()
    executor = _TerminalProbeExecutor("exception")
    service = FactoryRunService(workspace, cache_root=tmp_path / "exception-runtime", executor=executor)
    run = await service.create_run(FactoryConfig(name="exception", stages=["pm_planning"]))
    await service.start_run(run.id)

    with pytest.raises(RuntimeError, match="executor-boom"):
        await service.execute_stage(run.id, "pm_planning")

    assert executor.port is not None and executor.port._closed is True
    stored = await service.store.get_run(run.id)
    assert stored is not None
    assert stored.metadata["stage_results"]["pm_planning"]["status"] == "failed"


@pytest.mark.asyncio
async def test_executor_cancellation_closes_authority_while_claim_remains_unsettled(tmp_path: Path) -> None:
    workspace = tmp_path / "cancel"
    workspace.mkdir()
    executor = _TerminalProbeExecutor("cancel")
    service = FactoryRunService(workspace, cache_root=tmp_path / "cancel-runtime", executor=executor)
    run = await service.create_run(FactoryConfig(name="cancel", stages=["pm_planning"]))
    await service.start_run(run.id)
    task = asyncio.create_task(service.execute_stage(run.id, "pm_planning"))
    await executor.entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert executor.port is not None and executor.port._closed is True
    lease = service._admission.current()
    assert lease is not None and lease.stage_execution_claim is not None


@pytest.mark.asyncio
async def test_stage_close_blocks_late_background_cutoff_after_binding_inheritance(tmp_path: Path) -> None:
    workspace = tmp_path / "late-background"
    workspace.mkdir()
    executor = _LateBackgroundCutoffExecutor()
    service = FactoryRunService(workspace, cache_root=tmp_path / "late-runtime", executor=executor)
    run = await service.create_run(FactoryConfig(name="late-background", stages=["pm_planning"]))
    await service.start_run(run.id)

    result = await service.execute_stage(run.id, "pm_planning")

    assert result.status == "success"
    assert get_factory_role_evidence_authority_binding() is None
    assert executor.binding is not None and executor.child_task is not None
    executor.release_child.set()
    inherited, error_code = await executor.child_task
    assert inherited is executor.binding
    assert error_code == "factory_role_evidence_authority_closed"
    assert get_factory_role_evidence_authority_binding() is None
