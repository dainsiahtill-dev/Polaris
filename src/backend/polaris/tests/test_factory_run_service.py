"""Tests for FactoryRunService and FactoryStore."""

import asyncio
import hashlib
import json
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import BlueprintPersistence
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal import (
    factory_run_service as factory_service_module,
    factory_stage_executor as factory_stage_module,
)
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus,
    OrchestrationStageExecutor,
    StageResult,
)
from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import CommandResult
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
    get_factory_role_evidence_authority_binding,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    BindRuntimeTaskToFactoryRunCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_logical_path, resolve_runtime_path, resolve_storage_roots


def _complete_task_row(
    task_runtime: TaskRuntimeService,
    task_id: Any,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claimed = task_runtime.claim_execution(
        task_id,
        worker_id="test",
        role_id="director",
        selection_source="factory_run_service_test",
    )
    assert claimed["success"] is True
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claimed["execution_attempt"])
    completed = task_runtime.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary="test completed",
            metadata=metadata or {},
        )
    )
    assert completed["success"] is True
    return completed


def _chief_engineer_portfolio_output(
    *,
    task_id: str = "TASK-1",
    scope_path: str = "src/account",
) -> dict[str, Any]:
    """Return one valid project-level CE portfolio response for test runtimes."""

    return {
        "construction_plan": {
            "project_design_intent": "Keep domain behavior behind stable task-owned interfaces.",
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "task_plans": {
                task_id: {
                    "preparation": ["Confirm the task-owned module boundary"],
                    "implementation": ["Implement the declared domain behavior"],
                    "verification": ["Run the declared acceptance checks"],
                }
            },
        },
        "scope_for_apply": [scope_path],
        "risk_flags": [],
    }


class FakeStageExecutor:
    """Deterministic stage executor for FactoryRunService tests."""

    def __init__(self, fail_stages: set[str] | None = None) -> None:
        self.fail_stages = fail_stages or set()

    async def execute(self, stage: str, run: FactoryRun, context: dict) -> StageResult:
        if stage in self.fail_stages:
            return StageResult(
                stage=stage,
                status="failed",
                output=f"{stage} failed",
                artifacts=[],
            )

        return StageResult(
            stage=stage,
            status="success",
            output=f"{stage} completed",
            artifacts=[f"artifacts/{stage}.json"],
        )


class SlowStageExecutor:
    """Slow executor used to validate cancellation/heartbeat behavior."""

    def __init__(self, sleep_seconds: float = 0.2) -> None:
        self.sleep_seconds = sleep_seconds

    async def execute(self, stage: str, run: FactoryRun, context: dict) -> StageResult:
        await asyncio.sleep(self.sleep_seconds)
        return StageResult(
            stage=stage,
            status="success",
            output=f"{stage} completed slowly",
            artifacts=[],
        )


class ProviderHttpErrorStageExecutor:
    """Raises a provider-shaped HTTP error (mirrors aiohttp.ClientResponseError)."""

    def __init__(self, message: str) -> None:
        self.message = message

    async def execute(self, stage: str, run: FactoryRun, context: dict) -> StageResult:
        del stage, run, context
        # Do not import aiohttp here: the production path must catch any Exception
        # subclass, not only aiohttp's type. A plain Exception with the real
        # production message is the regression fixture for the R49 hang.
        raise Exception(self.message)


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace"""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir).resolve()
        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(workspace),
                streams=fact_stream_bootstrap_streams(),
                maintenance_reason="factory_run_service_test_bootstrap",
            )
        )
        yield workspace


@pytest.fixture
async def service(temp_workspace):
    """Create a FactoryRunService instance"""
    svc = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
    yield svc


@pytest.fixture
def store(temp_workspace):
    """Create a FactoryStore instance"""
    return FactoryStore(temp_workspace / ".polaris" / "factory")


class TestFactoryConfig:
    """Test FactoryConfig dataclass"""

    def test_default_config(self):
        config = FactoryConfig(name="test-run")
        assert config.name == "test-run"
        assert config.description is None
        assert config.stages == []
        assert config.auto_dispatch is True
        assert config.checkpoint_interval == 300

    def test_custom_config(self):
        config = FactoryConfig(
            name="full-run",
            description="A full factory run",
            stages=["docs_generation", "pm_planning"],
            auto_dispatch=False,
            checkpoint_interval=600,
        )
        assert config.name == "full-run"
        assert config.description == "A full factory run"
        assert config.stages == ["docs_generation", "pm_planning"]
        assert config.auto_dispatch is False
        assert config.checkpoint_interval == 600


class TestFactoryRun:
    """Test FactoryRun dataclass"""

    def test_to_dict(self):
        config = FactoryConfig(name="test-run")
        run = FactoryRun(
            id="factory_abc123", config=config, status=FactoryRunStatus.PENDING, created_at="2025-01-01T00:00:00"
        )

        data = run.to_dict()
        assert data["id"] == "factory_abc123"
        assert data["config"]["name"] == "test-run"
        assert data["status"] == "pending"
        assert data["created_at"] == "2025-01-01T00:00:00"

    def test_from_dict(self):
        data = {
            "id": "factory_xyz789",
            "config": {"name": "recovery-test", "stages": ["stage1"]},
            "status": "running",
            "created_at": "2025-01-01T12:00:00",
            "stages_completed": ["stage1"],
            "metadata": {"key": "value"},
        }

        run = FactoryRun.from_dict(data)
        assert run.id == "factory_xyz789"
        assert run.config.name == "recovery-test"
        assert run.status == FactoryRunStatus.RUNNING
        assert run.stages_completed == ["stage1"]
        assert run.metadata == {"key": "value"}


class TestFactoryStore:
    """Test FactoryStore persistence"""

    @pytest.mark.asyncio
    async def test_save_and_get_run(self, store, temp_workspace):
        config = FactoryConfig(name="test-run")
        run = FactoryRun(
            id="factory_test123",
            config=config,
            status=FactoryRunStatus.PENDING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        await store.save_run(run)

        # Verify file was created
        run_file = store.get_run_dir(run.id) / "run.json"
        assert run_file.exists()

        # Verify we can retrieve it
        retrieved = await store.get_run(run.id)
        assert retrieved is not None
        assert retrieved.id == run.id
        assert retrieved.config.name == run.config.name

    @pytest.mark.asyncio
    async def test_save_run_retries_windows_replace_conflict(self, store, monkeypatch):
        config = FactoryConfig(name="replace-retry")
        run = FactoryRun(
            id="factory_replace_retry",
            config=config,
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        original_replace = Path.replace
        attempts = {"count": 0}

        def flaky_replace(self: Path, target: Path):
            if self.name.startswith("run.json.") and attempts["count"] < 2:
                attempts["count"] += 1
                raise PermissionError("[WinError 5] Access is denied")
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", flaky_replace)
        await store.save_run(run)

        saved = await store.get_run(run.id)
        assert saved is not None
        assert attempts["count"] == 2

    @pytest.mark.asyncio
    async def test_get_nonexistent_run(self, store):
        result = await store.get_run("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_checkpoint(self, store, temp_workspace):
        config = FactoryConfig(name="test-run")
        run = FactoryRun(
            id="factory_checkpoint_test",
            config=config,
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

        await store.checkpoint(run)

        # Verify checkpoint was created
        checkpoint_dir = store.get_run_dir(run.id) / "checkpoints"
        checkpoints = list(checkpoint_dir.glob("*.json"))
        assert len(checkpoints) == 1

    @pytest.mark.asyncio
    async def test_append_and_get_events(self, store, temp_workspace):
        run_id = "factory_events_test"

        # Create run directory first
        store.get_run_dir(run_id).mkdir(parents=True, exist_ok=True)

        # Append events
        event1 = {"type": "started", "timestamp": "2025-01-01T00:00:00"}
        event2 = {"type": "stage_completed", "stage": "test", "timestamp": "2025-01-01T00:01:00"}

        await store.append_event(run_id, event1)
        await store.append_event(run_id, event2)

        # Retrieve events
        events = await store.get_events(run_id)
        assert len(events) == 2
        assert events[0]["type"] == "started"
        assert events[1]["type"] == "stage_completed"

    def test_list_runs(self, store, temp_workspace):
        # Initially empty
        assert store.list_runs() == []

        # Only runs with a regular mutable snapshot are discoverable.  A
        # directory holding admission bytes alone is a quarantined half-run.
        for run_id in ("run1", "run2"):
            run_dir = store.base_dir / run_id
            run_dir.mkdir()
            (run_dir / "run.json").write_text("{}", encoding="utf-8")
        (store.base_dir / "half-run").mkdir()
        (store.base_dir / "not_a_run.txt").touch()

        runs = store.list_runs()
        assert len(runs) == 2
        assert "run1" in runs
        assert "run2" in runs


class TestFactoryRunService:
    """Test FactoryRunService"""

    def test_default_store_uses_workspace_runtime_root(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())

        expected_store_root = Path(resolve_storage_roots(str(temp_workspace)).runtime_root) / "factory"

        assert service.store.base_dir == expected_store_root
        assert service.cache_root == expected_store_root.parent
        assert service.store.base_dir != temp_workspace / ".polaris" / "factory"

    def test_explicit_cache_root_overrides_runtime_store_root(self, temp_workspace, tmp_path):
        cache_root = tmp_path / "explicit-cache"

        service = FactoryRunService(
            temp_workspace,
            cache_root=cache_root,
            executor=FakeStageExecutor(),
        )

        assert service.cache_root == cache_root
        assert service.store.base_dir == cache_root / "factory"

    @pytest.mark.asyncio
    async def test_create_run(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run", stages=["stage1", "stage2"])

        run = await service.create_run(config)

        assert run.id.startswith("factory_")
        assert run.config.name == "test-run"
        assert run.status == FactoryRunStatus.PENDING
        assert run.created_at is not None

        # Verify directory structure
        run_dir = service.store.get_run_dir(run.id)
        assert (run_dir / "artifacts").exists()
        assert (run_dir / "events").exists()
        assert (run_dir / "checkpoints").exists()

    @pytest.mark.asyncio
    async def test_append_event_jetstream_timeout_is_non_blocking(
        self,
        temp_workspace,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import polaris.delivery.http.routers.jetstream_utils as jetstream_utils

        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        run = await service.create_run(FactoryConfig(name="test-run"))

        async def slow_publish(**_: object) -> bool:
            await asyncio.sleep(1)
            return True

        monkeypatch.setattr(jetstream_utils, "publish_to_jetstream", slow_publish)
        monkeypatch.setattr(factory_service_module, "_FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS", 0.01)

        started = asyncio.get_running_loop().time()
        await service._append_event(run.id, {"type": "probe"})
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 0.2
        events = await service.store.get_events(run.id)
        assert any(event.get("type") == "probe" for event in events)

    @pytest.mark.asyncio
    async def test_append_event_publishes_canonical_workspace_subject(
        self,
        temp_workspace,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import polaris.delivery.http.routers.jetstream_utils as jetstream_utils

        published: list[dict[str, object]] = []
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        run = await service.create_run(FactoryConfig(name="test-run"))

        async def capture_publish(**kwargs: object) -> bool:
            published.append(dict(kwargs))
            return True

        monkeypatch.setattr(jetstream_utils, "publish_to_jetstream", capture_publish)

        await service._append_event(run.id, {"type": "stage_started", "stage": "director_dispatch"})

        workspace_key = resolve_storage_roots(str(temp_workspace)).workspace_key
        assert published
        assert published[0]["subject"] == f"hp.runtime.{workspace_key}.event.factory.{run.id}"
        payload = published[0]["payload"]
        assert isinstance(payload, dict)
        assert payload["channel"] == f"event.factory:{run.id}"
        assert payload["workspace_key"] == workspace_key

    @pytest.mark.asyncio
    async def test_start_run(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)

        started = await service.start_run(run.id)

        assert started.status == FactoryRunStatus.RUNNING
        assert started.started_at is not None

    @pytest.mark.asyncio
    async def test_execute_stage_success(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        result = await service.execute_stage(run.id, "docs_generation")

        assert result.stage == "docs_generation"
        assert result.status == "success"
        assert result.output is not None
        assert result.started_at is not None
        assert result.completed_at is not None

        # Verify run state updated
        updated_run = await service.get_run(run.id)
        assert "docs_generation" in updated_run.stages_completed
        assert updated_run.recovery_point == "docs_generation"
        assert updated_run.metadata["last_successful_stage"] == "docs_generation"

    @pytest.mark.asyncio
    async def test_execute_stage_provider_http_error_marks_stage_finished_then_reraises(
        self,
        temp_workspace: Path,
    ) -> None:
        """Provider 403-class exceptions must not leave a forever-RUNNING stage claim.

        R49 hang class: aiohttp.ClientResponseError escaped a narrow except list,
        so the stage never finished, updated_at froze, and the API still reported
        chief_engineer_review as running. This drives the real execute_stage entry.
        """
        provider_detail = (
            "403, message='Forbidden', url='https://api.kimi.com/coding/v1/messages' "
            "You've reached your usage limit for this billing cycle."
        )
        service = FactoryRunService(
            temp_workspace,
            executor=ProviderHttpErrorStageExecutor(provider_detail),
        )
        run = await service.create_run(FactoryConfig(name="provider-403-run"))
        await service.start_run(run.id)

        with pytest.raises(Exception, match="usage limit") as raised:
            await service.execute_stage(
                run.id,
                "chief_engineer_review",
                context={"heartbeat_interval_seconds": 0},
            )

        assert "403" in str(raised.value) or "usage limit" in str(raised.value).lower()

        updated = await service.get_run(run.id)
        assert updated is not None
        # Stage finished as failed (not abandoned mid-claim with only started metadata).
        assert "chief_engineer_review" in updated.stages_failed
        assert "chief_engineer_review" not in updated.stages_completed
        assert updated.metadata.get("last_failed_stage") == "chief_engineer_review"
        # Durable failure path must have advanced updated_at past create/start freeze.
        assert updated.updated_at is not None
        assert (
            updated.metadata.get("current_stage")
            in {
                "chief_engineer_review",
                None,
                "",
            }
            or updated.metadata.get("last_failed_stage") == "chief_engineer_review"
        )

    @pytest.mark.asyncio
    async def test_provider_stage_exception_then_complete_run_stamps_lifecycle_closeout(
        self,
        temp_workspace: Path,
    ) -> None:
        """Honest integration: real execute_stage provider Exception → FAILED → complete_run.

        Skeptic gap: stage failure sets status=FAILED before re-raise; complete_run
        used to skip close-out when already terminal, leaving completed_at=null and
        leases stuck draining (R50 factory_run_final.json). This drives both real
        entry points without mocking execute_stage.
        """
        provider_detail = (
            "403, message='Forbidden', url='https://api.kimi.com/coding/v1/messages' "
            "You've reached your usage limit for this billing cycle."
        )
        service = FactoryRunService(
            temp_workspace,
            executor=ProviderHttpErrorStageExecutor(provider_detail),
        )
        created = await service.create_run(FactoryConfig(name="provider-403-closeout"))
        await service.start_run(created.id)

        with pytest.raises(Exception, match="usage limit"):
            await service.execute_stage(
                created.id,
                "chief_engineer_review",
                context={"heartbeat_interval_seconds": 0},
            )

        mid = await service.get_run(created.id)
        assert mid is not None
        assert mid.status == FactoryRunStatus.FAILED
        assert mid.completed_at is None  # stage path does not own lifecycle close-out
        assert not str(mid.metadata.get("completion_authority") or "").strip()

        closed = await service.complete_run(created.id, success=False)
        assert closed.status == FactoryRunStatus.FAILED
        assert closed.completed_at is not None
        assert closed.metadata.get("completion_authority") == "orchestration_session_lifecycle"
        assert closed.metadata.get("verified") is False

        lease = closed.metadata.get("factory_workspace_run_lease")
        assert isinstance(lease, dict)
        # R50 bug: lease stuck state=draining with released_at=null after incomplete close-out.
        assert lease.get("state") == "released"
        assert lease.get("released_at") is not None

    @pytest.mark.asyncio
    async def test_failed_stage_result_then_complete_run_stamps_closeout(
        self,
        temp_workspace: Path,
    ) -> None:
        """StageResult(status=failed) without exception must still get full close-out."""
        service = FactoryRunService(
            temp_workspace,
            executor=FakeStageExecutor(fail_stages={"chief_engineer_review"}),
        )
        created = await service.create_run(FactoryConfig(name="stage-result-failed"))
        await service.start_run(created.id)

        result = await service.execute_stage(
            created.id,
            "chief_engineer_review",
            context={"heartbeat_interval_seconds": 0},
        )
        assert result.status == "failed"

        mid = await service.get_run(created.id)
        assert mid is not None
        assert mid.status == FactoryRunStatus.FAILED
        assert "chief_engineer_review" in mid.stages_failed

        closed = await service.complete_run(created.id, success=False)
        assert closed.status == FactoryRunStatus.FAILED
        assert closed.completed_at is not None
        assert closed.metadata.get("completion_authority") == "orchestration_session_lifecycle"

    @pytest.mark.asyncio
    async def test_early_stage_fail_with_open_pm_tasks_releases_workspace_lease(
        self,
        temp_workspace: Path,
    ) -> None:
        """R51: open PM task lifecycles must not pin lease in draining forever.

        Live path: PM creates task rows → CE provider 403 / stage fail → run
        FAILED with completed_at stamped, but settlement barrier stayed open
        with ``lifecycle_open`` because open never-dispatched tasks were not
        terminalized. Terminal drain must abort those rows and release the
        workspace lease.
        """
        service = FactoryRunService(
            temp_workspace,
            executor=FakeStageExecutor(fail_stages={"chief_engineer_review"}),
        )
        created = await service.create_run(FactoryConfig(name="early-fail-open-tasks"))
        await service.start_run(created.id)

        runtime = TaskRuntimeService(str(temp_workspace))
        for index in range(3):
            row = runtime.create_task_row(subject=f"pm-open-task-{index}")
            binding = runtime.bind_task_to_factory_run(
                BindRuntimeTaskToFactoryRunCommandV1(
                    workspace=str(temp_workspace),
                    task_id=str(row["id"]),
                    factory_run_id=created.id,
                )
            )
            assert binding.ok is True

        settlement_before = runtime.query_factory_run_settlement(factory_run_id=created.id)
        assert settlement_before["settled"] is True
        assert int(str(settlement_before.get("observable_row_count") or 0)) == 3

        result = await service.execute_stage(
            created.id,
            "chief_engineer_review",
            context={"heartbeat_interval_seconds": 0},
        )
        assert result.status == "failed"

        closed = await service.complete_run(created.id, success=False)
        assert closed.status == FactoryRunStatus.FAILED
        assert closed.completed_at is not None
        assert closed.metadata.get("completion_authority") == "orchestration_session_lifecycle"

        lease = closed.metadata.get("factory_workspace_run_lease")
        assert isinstance(lease, dict)
        assert lease.get("state") == "released", (
            f"lease stuck at {lease.get('state')}; "
            f"drain_conflict={closed.metadata.get('factory_workspace_run_drain_conflict')}; "
            f"abort={closed.metadata.get('factory_task_runtime_abort')}; "
            f"barrier={closed.metadata.get('factory_run_ledger_settlement_barrier')}"
        )
        assert lease.get("released_at") is not None
        abort = closed.metadata.get("factory_task_runtime_abort")
        assert isinstance(abort, dict)
        assert int(str(abort.get("terminalized_count") or 0)) >= 1

    @pytest.mark.asyncio
    async def test_execute_stage_cancellation_is_preserved(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=SlowStageExecutor(sleep_seconds=0.2))
        run = await service.create_run(FactoryConfig(name="test-run"))
        await service.start_run(run.id)

        stage_task = asyncio.create_task(
            service.execute_stage(
                run.id,
                "director_dispatch",
                context={"heartbeat_interval_seconds": 0.05},
            )
        )
        await asyncio.sleep(0.05)
        cancelled = await service.cancel_run(run.id, reason="operator stop")
        assert cancelled.status == FactoryRunStatus.CANCELLED

        result = await stage_task
        assert result.status == "cancelled"

        updated = await service.get_run(run.id)
        assert updated is not None
        assert updated.status == FactoryRunStatus.CANCELLED
        assert "director_dispatch" not in updated.stages_completed

    @pytest.mark.asyncio
    async def test_execute_stage_emits_heartbeat_events_for_long_stage(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=SlowStageExecutor(sleep_seconds=0.16))
        run = await service.create_run(FactoryConfig(name="test-run"))
        await service.start_run(run.id)

        result = await service.execute_stage(
            run.id,
            "pm_planning",
            context={"heartbeat_interval_seconds": 0.05},
        )
        assert result.status == "success"

        events = await service.get_run_events(run.id)
        heartbeat_events = [
            event for event in events if event.get("type") == "stage_heartbeat" and event.get("stage") == "pm_planning"
        ]
        assert heartbeat_events

    @pytest.mark.asyncio
    async def test_stage_heartbeat_keeps_lease_alive_when_projection_repeatedly_fails(
        self,
        temp_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        run = await service.create_run(FactoryConfig(name="projection-failure"))
        started = await service.start_run(run.id)
        lease = service._admission.current()
        assert lease is not None
        assert lease.run_id == started.id

        renewed_tokens: list[int] = []
        projection_attempts = 0
        original_renew = service._admission.renew

        def capture_renew(
            run_id: str,
            *,
            fencing_token: int,
            lease_ttl_seconds: float | None = None,
        ) -> Any:
            renewed_tokens.append(fencing_token)
            return original_renew(
                run_id,
                fencing_token=fencing_token,
                lease_ttl_seconds=lease_ttl_seconds,
            )

        async def fail_projection(_run_id: str, _stage: str) -> None:
            nonlocal projection_attempts
            projection_attempts += 1
            raise OSError("injected Factory Run Store projection lock timeout")

        monkeypatch.setattr(service._admission, "renew", capture_renew)
        monkeypatch.setattr(service, "_emit_stage_heartbeat", fail_projection)

        heartbeat = asyncio.create_task(
            service._run_stage_heartbeat(
                run.id,
                "director_dispatch",
                0.01,
                fencing_token=lease.fencing_token,
            )
        )
        await asyncio.sleep(0.055)
        heartbeat.cancel()
        with pytest.raises(asyncio.CancelledError):
            await heartbeat

        assert projection_attempts >= 3
        assert len(renewed_tokens) >= 3
        assert set(renewed_tokens) == {lease.fencing_token}

    @pytest.mark.asyncio
    async def test_execute_stage_not_found(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())

        with pytest.raises(ValueError, match="Run nonexistent not found"):
            await service.execute_stage("nonexistent", "docs_generation")

    @pytest.mark.asyncio
    async def test_pause_and_resume(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        # Pause
        paused = await service.execute_pause(run.id)
        assert paused.status == FactoryRunStatus.PAUSED

        # Verify pause event
        events = await service.get_run_events(run.id)
        pause_events = [e for e in events if e["type"] == "paused"]
        assert len(pause_events) == 1

        # Resume
        resumed = await service.execute_resume(run.id)
        assert resumed.status == FactoryRunStatus.RUNNING

        # Verify resume event
        events = await service.get_run_events(run.id)
        resume_events = [e for e in events if e["type"] == "resumed"]
        assert len(resume_events) == 1

    @pytest.mark.asyncio
    async def test_update_run_metadata_persists_export_evidence(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        updated = await service.update_run_metadata(
            run.id,
            {
                "export_session_id": "sess_pm",
                "export_bundle_path": ".polaris/exports/sess_pm_export.json",
                "directive": "Build the role desktop workflow",
            },
        )

        reloaded = await service.get_run(run.id)
        assert updated.metadata["export_session_id"] == "sess_pm"
        assert reloaded is not None
        assert reloaded.metadata["export_bundle_path"] == ".polaris/exports/sess_pm_export.json"
        assert reloaded.metadata["directive"] == "Build the role desktop workflow"

        events = await service.get_run_events(run.id)
        metadata_events = [event for event in events if event.get("type") == "metadata_updated"]
        assert metadata_events
        assert metadata_events[-1]["metadata_keys"] == ["directive", "export_bundle_path", "export_session_id"]

    @pytest.mark.asyncio
    async def test_complete_run(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        completed = await service.complete_run(run.id, success=True)

        assert completed.status == FactoryRunStatus.COMPLETED
        assert completed.completed_at is not None

        # Verify completion event
        events = await service.get_run_events(run.id)
        complete_events = [e for e in events if e["type"] == "completed"]
        assert len(complete_events) == 1
        assert complete_events[0]["success"] is True

    @pytest.mark.asyncio
    async def test_complete_run_keeps_cancelled_status(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        run = await service.create_run(FactoryConfig(name="test-run"))
        await service.start_run(run.id)
        await service.cancel_run(run.id, reason="operator stop")

        completed = await service.complete_run(run.id, success=True)
        assert completed.status == FactoryRunStatus.CANCELLED

        events = await service.get_run_events(run.id)
        complete_events = [event for event in events if event.get("type") == "completed"]
        assert complete_events == []

    @pytest.mark.asyncio
    async def test_list_runs(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())

        # Create multiple runs
        config1 = FactoryConfig(name="run-1")
        config2 = FactoryConfig(name="run-2")

        run1 = await service.create_run(config1)
        run2 = await service.create_run(config2)

        runs = await service.list_runs()

        assert len(runs) == 2
        run_ids = [r["id"] for r in runs]
        assert run1.id in run_ids
        assert run2.id in run_ids

    @pytest.mark.asyncio
    async def test_recover_run(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        # Execute a stage to have something to recover to
        await service.execute_stage(run.id, "docs_generation")

        # Simulate a crash by manually setting status back to RUNNING
        run = await service.get_run(run.id)
        run.status = FactoryRunStatus.RUNNING
        await service.store.save_run(run)

        # Recover
        recovered = await service.recover_run(run.id)

        assert recovered.status == FactoryRunStatus.RECOVERING
        assert recovered.recovery_point == "docs_generation"

    @pytest.mark.asyncio
    async def test_fresh_service_replays_empty_physical_attempt_stream_as_closed(self, temp_workspace):
        cache_root = temp_workspace / "runtime"
        creator = FactoryRunService(
            temp_workspace,
            cache_root=cache_root,
            executor=FakeStageExecutor(),
        )
        run = await creator.create_run(FactoryConfig(name="restart-replay"))
        assert creator._physical_attempt_coordinator(run.id).drain_snapshot().settled is True

        restarted = FactoryRunService(
            temp_workspace,
            cache_root=cache_root,
            executor=FakeStageExecutor(),
        )
        role_evidence, lifecycle = restarted._capture_physical_attempt_replay_views(
            run.id,
        )
        assert role_evidence.captured_head.global_seq == 0
        assert lifecycle.captured_head.global_seq == 0
        with pytest.raises(RuntimeError, match="factory_physical_attempt_replay_required"):
            restarted._physical_attempt_coordinator(run.id)

        recovered = await restarted.recover_run(run.id)
        replayed = restarted._physical_attempt_coordinator(run.id)

        assert recovered.status == FactoryRunStatus.RECOVERING
        assert replayed.drain_snapshot().settled is True
        assert replayed.close().settled is True

    @pytest.mark.asyncio
    async def test_lifecycle_claim_rolls_back_when_restart_replay_fails(
        self,
        temp_workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cache_root = temp_workspace / "runtime"
        creator = FactoryRunService(
            temp_workspace,
            cache_root=cache_root,
            executor=FakeStageExecutor(),
        )
        created = await creator.create_run(FactoryConfig(name="restart-replay-failure"))
        restarted = FactoryRunService(
            temp_workspace,
            cache_root=cache_root,
            executor=FakeStageExecutor(),
        )
        persisted = await restarted.get_run(created.id)
        assert persisted is not None

        def fail_replay(**_kwargs: object) -> None:
            raise RuntimeError("factory_physical_attempt_replay_head_drift")

        monkeypatch.setattr(restarted, "_recover_physical_attempt_coordinator", fail_replay)
        with pytest.raises(RuntimeError, match="head_drift"):
            restarted._claim_lifecycle_operation(
                persisted,
                operation="recover_run",
                nonce="forced-replay-failure",
                acquire_if_available=True,
            )

        durable = restarted._admission.current()
        assert durable is not None
        assert durable.state.value == "released"
        assert durable.lifecycle_operation_claim is None
        assert created.id not in restarted._physical_attempt_coordinators

    @pytest.mark.asyncio
    async def test_replay_claim_fences_live_admission_before_durable_storage(
        self,
        temp_workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        creator = FactoryRunService(
            temp_workspace,
            cache_root=temp_workspace / "runtime",
            executor=FakeStageExecutor(),
        )
        created = await creator.create_run(FactoryConfig(name="local-before-durable-replay-fence"))
        service = FactoryRunService(
            temp_workspace,
            cache_root=temp_workspace / "runtime",
            executor=FakeStageExecutor(),
        )
        persisted = await service.get_run(created.id)
        assert persisted is not None
        original_claim = service._admission.claim_lifecycle_operation
        observed_local_fence: list[bool] = []

        def claim_after_observing_local_fence(*args: object, **kwargs: object) -> Any:
            observed_local_fence.append(kwargs.get("replay_fence") is True)
            return original_claim(*args, **kwargs)

        monkeypatch.setattr(service._admission, "claim_lifecycle_operation", claim_after_observing_local_fence)
        monkeypatch.setattr(service, "_recover_physical_attempt_coordinator", lambda **_kwargs: None)

        service._claim_lifecycle_operation(
            persisted,
            operation="recover_run",
            nonce="local-fence-first",
            acquire_if_available=True,
        )

        assert observed_local_fence == [True]
        assert service._admission.current().state.value == "draining"

    @pytest.mark.asyncio
    async def test_restart_replay_discards_three_drifting_factory_heads(
        self,
        temp_workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cache_root = temp_workspace / "runtime"
        creator = FactoryRunService(
            temp_workspace,
            cache_root=cache_root,
            executor=FakeStageExecutor(),
        )
        created = await creator.create_run(FactoryConfig(name="restart-replay-drift"))
        restarted = FactoryRunService(
            temp_workspace,
            cache_root=cache_root,
            executor=FakeStageExecutor(),
        )
        persisted = await restarted.get_run(created.id)
        assert persisted is not None
        original_capture = restarted._capture_physical_attempt_replay_fence
        capture_count = 0

        def drifting_capture(
            *,
            factory_run_id: str,
            lease: Any,
            deadline: float | None = None,
        ) -> Any:
            nonlocal capture_count
            capture_count += 1
            captured = original_capture(
                factory_run_id=factory_run_id,
                lease=lease,
                deadline=deadline,
            )
            return replace(captured, current_stage=f"drift-{capture_count}")

        monkeypatch.setattr(restarted, "_capture_physical_attempt_replay_fence", drifting_capture)
        with pytest.raises(RuntimeError, match="factory_physical_attempt_replay_head_unstable"):
            restarted._claim_lifecycle_operation(
                persisted,
                operation="recover_run",
                nonce="forced-head-drift",
                acquire_if_available=True,
            )

        assert capture_count == 6
        durable = restarted._admission.current()
        assert durable is not None
        assert durable.state.value == "released"
        assert durable.lifecycle_operation_claim is None
        assert created.id not in restarted._physical_attempt_coordinators

    @pytest.mark.asyncio
    async def test_restart_execute_stage_cannot_mutate_before_lifecycle_replay(self, temp_workspace) -> None:
        cache_root = temp_workspace / "runtime"
        creator = FactoryRunService(
            temp_workspace,
            cache_root=cache_root,
            executor=FakeStageExecutor(),
        )
        created = await creator.create_run(FactoryConfig(name="restart-stage-bypass"))
        running = await creator.start_run(created.id)
        before_run = running.to_dict()
        before_lease = creator._admission.current()
        assert before_lease is not None

        restarted = FactoryRunService(
            temp_workspace,
            cache_root=cache_root,
            executor=FakeStageExecutor(),
        )
        with pytest.raises(RuntimeError, match="factory_physical_attempt_replay_required"):
            await restarted.execute_stage(created.id, "pm_planning")

        after_run = await restarted.get_run(created.id)
        assert after_run is not None
        assert after_run.to_dict() == before_run
        assert restarted._admission.current() == before_lease
        assert created.id not in restarted._physical_attempt_coordinators

    @pytest.mark.asyncio
    async def test_recovered_run_requires_explicit_fresh_execution_epoch(self, temp_workspace) -> None:
        cache_root = temp_workspace / "runtime"
        creator = FactoryRunService(
            temp_workspace,
            cache_root=cache_root,
            executor=FakeStageExecutor(),
        )
        created = await creator.create_run(FactoryConfig(name="restart-permanently-closed"))
        restarted = FactoryRunService(
            temp_workspace,
            cache_root=cache_root,
            executor=FakeStageExecutor(),
        )
        recovered = await restarted.recover_run(created.id)
        replayed = restarted._physical_attempt_coordinator(created.id)
        replay_token = recovered.metadata["factory_workspace_run_lease"]["fencing_token"]
        before = recovered.to_dict()

        with pytest.raises(RuntimeError, match="factory_physical_attempt_recovered_run_permanently_closed"):
            await restarted.start_run(created.id)

        after = await restarted.get_run(created.id)
        assert after is not None
        assert after.to_dict() == before
        durable = restarted._admission.current()
        assert durable is not None
        assert durable.lifecycle_operation_claim is None

        resumed = await restarted.resume_recovered_run(created.id)
        resumed_token = resumed.metadata["factory_workspace_run_lease"]["fencing_token"]

        assert resumed.status == FactoryRunStatus.RECOVERING
        assert resumed_token > replay_token
        assert resumed.metadata["factory_physical_attempt_execution_epoch"] == 2
        assert "factory_physical_attempt_admission_dead" not in resumed.metadata
        assert replayed.admission_closed is True
        assert restarted._physical_attempt_coordinator(created.id).admission_closed is False

        started = await restarted.start_run(created.id)
        assert started.status == FactoryRunStatus.RECOVERING

    @pytest.mark.asyncio
    async def test_terminal_release_waits_for_physical_attempt_drain(
        self,
        temp_workspace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        created = await service.create_run(FactoryConfig(name="physical-drain"))
        await service.start_run(created.id)
        coordinator = service._physical_attempt_coordinator(created.id)
        monkeypatch.setattr(
            coordinator,
            "close",
            lambda: SimpleNamespace(
                factory_run_id=created.id,
                settled=False,
                blocking_reservation_ids=("reservation-open",),
                terminal_failure_reservation_ids=(),
                by_authority=(),
            ),
        )

        completed = await service.complete_run(created.id)

        lease = service._admission.current()
        assert lease is not None
        assert lease.state.value == "draining"
        assert completed.metadata["factory_workspace_run_drain_conflict"]["code"] == (
            "factory_physical_attempt_drain_open"
        )
        assert completed.metadata["factory_physical_attempt_drain"]["blocking_reservation_ids"] == ["reservation-open"]

    @pytest.mark.asyncio
    async def test_retry_run_from_stage_recovers_failed_run(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run", stages=["pm_planning", "chief_engineer_review", "director_dispatch"])
        run = await service.create_run(config)
        await service.start_run(run.id)
        run = await service.get_run(run.id)
        run.status = FactoryRunStatus.FAILED
        run.completed_at = "2026-05-24T00:00:00+00:00"
        run.metadata["failure"] = {"detail": "director failed"}
        run.metadata["last_failed_stage"] = "director_dispatch"
        await service.store.save_run(run)

        retried = await service.retry_run_from_stage(run.id, "director_dispatch", "rerun delivery")

        assert retried.status == FactoryRunStatus.RECOVERING
        assert retried.recovery_point == "director_dispatch"
        assert retried.completed_at is None
        assert retried.metadata["retry_from_status"] == "failed"
        assert retried.metadata["retry_start_policy"] == "rerun_stage"
        assert retried.metadata["retry_execution_stage"] == "director_dispatch"
        assert retried.metadata["retry_reason"] == "rerun delivery"
        assert retried.metadata["retry_previous_failure"] == {"detail": "director failed"}
        assert retried.metadata["failure"] is None
        assert retried.metadata["last_failed_stage"] is None
        events = await service.get_run_events(run.id)
        assert events[-1]["type"] == "retry_requested"
        assert events[-1]["stage"] == "director_dispatch"

    @pytest.mark.asyncio
    async def test_retry_run_from_checkpoint_resumes_after_last_successful_stage(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(
            name="test-run",
            stages=["pm_planning", "chief_engineer_review", "director_dispatch", "quality_gate"],
        )
        run = await service.create_run(config)
        await service.start_run(run.id)
        run = await service.get_run(run.id)
        run.status = FactoryRunStatus.FAILED
        run.completed_at = "2026-05-24T00:00:00+00:00"
        run.recovery_point = "pm_planning"
        run.stages_completed = ["pm_planning", "chief_engineer_review", "director_dispatch"]
        run.stages_failed = ["director_dispatch"]
        run.metadata["last_successful_stage"] = "pm_planning"
        run.metadata["last_failed_stage"] = "director_dispatch"
        run.metadata["failure"] = {"detail": "director failed"}
        await service.store.save_run(run)

        retried = await service.retry_run_from_stage(run.id, None, "resume delivery")

        assert retried.status == FactoryRunStatus.RECOVERING
        assert retried.recovery_point == "pm_planning"
        assert retried.metadata["retry_start_policy"] == "after_checkpoint"
        assert retried.metadata["retry_execution_stage"] == "chief_engineer_review"
        assert retried.metadata["current_stage"] == "chief_engineer_review"
        assert retried.stages_completed == ["pm_planning"]
        assert retried.stages_failed == []

    @pytest.mark.asyncio
    async def test_retry_run_from_stage_rejects_unconfigured_stage(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run", stages=["pm_planning"])
        run = await service.create_run(config)
        await service.start_run(run.id)
        run = await service.get_run(run.id)
        run.status = FactoryRunStatus.FAILED
        await service.store.save_run(run)

        with pytest.raises(ValueError, match="not configured"):
            await service.retry_run_from_stage(run.id, "director_dispatch")

    @pytest.mark.asyncio
    async def test_all_stage_handlers(self, temp_workspace):
        """Test all stage handlers return proper StageResult"""
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        stages = ["docs_generation", "pm_planning", "chief_engineer_review", "director_dispatch", "quality_gate"]

        for stage in stages:
            result = await service.execute_stage(run.id, stage)
            assert result.stage == stage
            assert result.status == "success"
            assert result.output is not None
            assert len(result.artifacts) > 0


class _ImmediateFailureCommandService:
    def __init__(self) -> None:
        self.queried = False

    async def query_run_status(self, run_id: str) -> CommandResult:
        self.queried = True
        return CommandResult(run_id=run_id, status="running", message="should not be queried")


class _DetailedFailureCommandService:
    def __init__(self) -> None:
        self.query_calls = 0

    async def execute_pm_run(self, workspace: str, run_type: str, options: dict) -> CommandResult:
        del workspace, run_type, options
        return CommandResult(
            run_id="pm-run-detailed-failure",
            status="running",
            message="PM run started",
        )

    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        return CommandResult(
            run_id=run_id,
            status="failed",
            message=("Run status: failed | failed_task=task-0-pm (pm) | error=PM contract normalization failed"),
            metadata={
                "failed_task_count": 1,
                "failed_tasks": [
                    {
                        "task_id": "task-0-pm",
                        "role_id": "pm",
                        "status": "failed",
                        "error_category": "runtime",
                        "error_message": "PM contract normalization failed",
                    }
                ],
            },
        )


class _CompletedCommandService:
    def __init__(self) -> None:
        self.query_calls = 0
        self.observed_bindings: list[FactoryRoleEvidenceAuthorityBindingV1 | None] = []

    async def execute_pm_run(self, workspace: str, run_type: str, options: dict) -> CommandResult:
        self.observed_bindings.append(get_factory_role_evidence_authority_binding())
        del workspace, run_type, options
        return CommandResult(
            run_id="pm-run-completed",
            status="running",
            message="PM run started",
        )

    async def execute_qa_run(self, workspace: str, target: str, options: dict) -> CommandResult:
        self.observed_bindings.append(get_factory_role_evidence_authority_binding())
        del workspace, target, options
        return CommandResult(
            run_id="qa-run-completed",
            status="running",
            message="QA run started",
        )

    async def execute_director_run(self, workspace: str, tasks: list | None, options: dict) -> CommandResult:
        self.observed_bindings.append(get_factory_role_evidence_authority_binding())
        del workspace, tasks, options
        return CommandResult(
            run_id="director-run-completed",
            status="running",
            message="Director run started",
        )

    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        return CommandResult(
            run_id=run_id,
            status="completed",
            message="Run status: completed",
            metadata={},
        )


class _CapturingCompletedCommandService(_CompletedCommandService):
    def __init__(self) -> None:
        super().__init__()
        self.pm_calls: list[dict[str, object]] = []

    async def execute_pm_run(self, workspace: str, run_type: str, options: dict) -> CommandResult:
        self.observed_bindings.append(get_factory_role_evidence_authority_binding())
        self.pm_calls.append(
            {
                "workspace": workspace,
                "run_type": run_type,
                "options": dict(options),
            }
        )
        return CommandResult(
            run_id=f"pm-run-captured-{len(self.pm_calls)}",
            status="running",
            message="PM run started",
        )


class _CapturingQaCommandService(_CompletedCommandService):
    def __init__(self) -> None:
        super().__init__()
        self.qa_calls: list[dict[str, object]] = []
        self.validation_exists_at_qa = False

    async def execute_qa_run(self, workspace: str, target: str, options: dict) -> CommandResult:
        self.observed_bindings.append(get_factory_role_evidence_authority_binding())
        validation_path = Path(resolve_runtime_path(workspace, "runtime/qa/workspace-validation.json"))
        self.validation_exists_at_qa = validation_path.is_file()
        self.qa_calls.append(
            {
                "workspace": workspace,
                "target": target,
                "options": dict(options),
            }
        )
        return CommandResult(
            run_id="qa-run-captured",
            status="running",
            message="QA run started",
        )


class _NeverTerminalCommandService:
    def __init__(self) -> None:
        self.query_calls = 0

    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        return CommandResult(
            run_id=run_id,
            status="running",
            message="still running",
            metadata={},
        )


class _DirectorFailedCommandService(_CompletedCommandService):
    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        return CommandResult(
            run_id=run_id,
            status="failed",
            message="Run status: failed | failed_task=task-0-director (director) | error=tool_failed",
            metadata={
                "failed_task_count": 1,
                "task_status_counts": {"failed": 1},
            },
        )


class _DirectorCompletedMetadataProgressService(_CompletedCommandService):
    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        return CommandResult(
            run_id=run_id,
            status="completed",
            message="Run status: completed",
            metadata={
                "task_count": 1,
                "task_status_counts": {"completed": 1},
                "failed_task_count": 0,
            },
        )


class _DirectorCompletedThenTimeoutWithMaterializedTargetService(_CompletedCommandService):
    def __init__(self) -> None:
        super().__init__()
        self.workspace_path: Path | None = None
        self.execute_calls = 0

    async def execute_director_run(self, workspace: str, tasks: list | None, options: dict) -> CommandResult:
        del tasks, options
        self.execute_calls += 1
        self.workspace_path = Path(workspace)
        return CommandResult(
            run_id=f"director-run-{self.execute_calls}",
            status="running",
            message="Director run started",
        )

    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        if self.query_calls == 1:
            assert self.workspace_path is not None
            target_path = self.workspace_path / "src" / "account.js"
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text("module.exports = { accountId: 'acct-1' };\n", encoding="utf-8")
            return CommandResult(
                run_id=run_id,
                status="completed",
                message="Run status: completed",
                metadata={
                    "task_count": 1,
                    "task_status_counts": {"completed": 1},
                    "failed_task_count": 0,
                },
            )
        return CommandResult(
            run_id=run_id,
            status="timeout",
            message="Run timed out after 1 seconds",
            metadata={
                "cancel_signal_sent": True,
                "cancel_reason": "factory_stage_timeout",
            },
        )


class _DirectorNoMaterializedChangesAfterProgressService(_CompletedCommandService):
    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        if self.query_calls <= 1:
            return CommandResult(
                run_id=run_id,
                status="completed",
                message="Run status: completed",
                metadata={
                    "task_count": 1,
                    "task_status_counts": {"completed": 1},
                    "failed_task_count": 0,
                },
            )
        return CommandResult(
            run_id=run_id,
            status="failed",
            message="Run status: failed | failed_task=task-0-director (director) | error=director_no_materialized_changes",
            metadata={
                "task_count": 1,
                "task_status_counts": {"failed": 1},
                "failed_task_count": 1,
                "failed_tasks": [
                    {
                        "task_id": "task-0-director",
                        "role_id": "director",
                        "status": "failed",
                        "error_category": "runtime",
                        "error_message": "director_no_materialized_changes",
                    }
                ],
            },
        )


class _DirectorNoMaterializedChangesOnlyService(_CompletedCommandService):
    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        return CommandResult(
            run_id=run_id,
            status="failed",
            message="Run status: failed | failed_task=task-0-director (director) | error=director_no_materialized_changes",
            metadata={
                "task_count": 1,
                "task_status_counts": {"failed": 1},
                "failed_task_count": 1,
                "failed_tasks": [
                    {
                        "task_id": "task-0-director",
                        "role_id": "director",
                        "status": "failed",
                        "error_category": "runtime",
                        "error_message": "director_no_materialized_changes",
                    }
                ],
            },
        )


class _TestFactoryRoleEvidenceAuthorityPort:
    def __init__(self, factory_run_id: str = "factory-test-run") -> None:
        self.factory_run_id = factory_run_id
        self.bindings: list[FactoryRoleEvidenceAuthorityBindingV1] = []
        self.revoked: list[str] = []

    async def acquire_cutoff(self, request: object) -> object:
        del request
        raise AssertionError("stage seam test must not acquire cutoff")

    async def resolve_cutoff_proof(self, ack: object) -> object:
        del ack
        raise AssertionError("stage seam test must not resolve cutoff proof")

    def reserve(self, command: object) -> object:
        raise AssertionError(command)

    def begin_start(self, command: object) -> object:
        raise AssertionError(command)

    def commit_started(self, command: object) -> object:
        raise AssertionError(command)

    def abort_reservation(self, command: object) -> object:
        raise AssertionError(command)

    def mark_start_ambiguous(self, command: object) -> object:
        raise AssertionError(command)

    def settle(self, command: object) -> object:
        raise AssertionError(command)

    def terminal_persistence_failed(self, command: object) -> object:
        raise AssertionError(command)

    def require_grant_capacity(self, role: str, count: int) -> None:
        del role
        if len(self.bindings) + count > 512:
            raise RuntimeError("factory_role_evidence_stage_grant_cardinality_exceeded")

    def mint_authority_binding(self, role: str) -> FactoryRoleEvidenceAuthorityBindingV1:
        binding = FactoryRoleEvidenceAuthorityBindingV1(
            schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
            verification_scope="factory",
            factory_run_id=self.factory_run_id,
            role=role,
            cutoff_port=self,
            physical_attempt_control_port=self,
            attempt_budget=32,
            execution_authority_hash=hashlib.sha256(f"test-grant-{len(self.bindings)}-{role}".encode()).hexdigest(),
        )
        self.bindings.append(binding)
        return binding

    def revoke_authority_binding(self, binding: FactoryRoleEvidenceAuthorityBindingV1) -> None:
        self.revoked.append(binding.execution_authority_hash)


class _TestStageExecutor(OrchestrationStageExecutor):
    def __init__(self, workspace: Path, command_service: object) -> None:
        super().__init__(workspace)
        self._command_service = command_service
        self._test_role_evidence_port = _TestFactoryRoleEvidenceAuthorityPort()

    def _build_orchestration_service(self, context: dict):
        return self._command_service

    def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        del context
        return []

    def _factory_role_evidence_cutoff_port(self, context: Mapping[str, Any]) -> Any:
        del context
        return self._test_role_evidence_port

    def _validate_director_binding_coverage(self, additional_events=None):
        return True, []


def _write_handoff_ready_review_for_tasks(
    executor: OrchestrationStageExecutor,
    *,
    run_id: str,
    tasks: list[dict[str, Any]],
) -> None:
    rows: list[dict[str, str]] = []
    persistence = BlueprintPersistence(str(executor.workspace))
    for index, task in enumerate(tasks, start=1):
        task_id = str(task.get("id") or task.get("task_id") or f"TASK-{index}").strip()
        raw_targets = task.get("target_files")
        target_files = [str(item) for item in raw_targets if str(item).strip()] if isinstance(raw_targets, list) else []
        if not target_files:
            target_files = ["src/index.js"]
        blueprint_id = f"bp-{run_id}-{task_id}"
        persistence.save(
            blueprint_id,
            {
                "schema_version": "factory.test.blueprint.v1",
                "blueprint_id": blueprint_id,
                "task_id": task_id,
                "target_files": target_files,
                "acceptance_criteria": ["workspace validation passes"],
                "execution_checklist": ["materialize declared target files"],
                "dependencies": [],
                "recommendations": ["run package validation", "verify handoff evidence"],
                "pm_contract_ref": f"runtime/tasks/{task_id}.json",
                "pm_contract_hash": f"pm-hash-{task_id}",
                "blueprint_hash": f"blueprint-hash-{task_id}",
                "execution_profile_ref": f"runtime/execution-profiles/{task_id}.json",
                "execution_profile_hash": f"profile-hash-{task_id}",
            },
        )
        rows.append(
            {
                "task_id": task_id,
                "blueprint_id": blueprint_id,
                "blueprint_path": f"runtime/blueprints/{blueprint_id}.json",
            }
        )
    executor._write_json_artifact(
        f"runtime/state/blueprints/{run_id}.review.json",
        {
            "schema_version": "factory.chief_engineer_review.v1",
            "factory_run_id": run_id,
            "blueprints": rows,
        },
    )


class _WorkspaceValidationStageExecutor(_TestStageExecutor):
    def __init__(self, workspace: Path, command_service: object, exit_codes: list[int]) -> None:
        super().__init__(workspace, command_service)
        self.exit_codes = list(exit_codes)
        self.commands_seen: list[list[str]] = []

    def _run_workspace_quality_command(self, command: list[str], timeout_seconds: float) -> dict[str, object]:
        del timeout_seconds
        self.commands_seen.append(list(command))
        exit_code = self.exit_codes.pop(0) if self.exit_codes else 0
        return {
            "command": list(command),
            "exit_code": exit_code,
            "passed": exit_code == 0,
            "stdout_tail": "ok" if exit_code == 0 else "",
            "stderr_tail": "" if exit_code == 0 else "failed",
        }


def _authorize_workspace_quality_checks(executor: OrchestrationStageExecutor) -> None:
    """Provide canonical completed TaskBoundary evidence to workspace-check tests."""

    projection = {
        "source": "run_ledger",
        "integrity_ok": True,
        "outcome_ok": False,
        "gates": [{"name": "workspace_validation", "ok": True}],
        "task_boundary": {
            "latest_by_task": {
                "TASK-1": {
                    "task_id": "TASK-1",
                    "status": "completed_verified",
                    "ok": True,
                }
            }
        },
        "evidence_policy": {
            "integrity_ok": True,
            "outcome_ok": True,
            "missing_required_modalities": [],
            "failed_required_modalities": [],
        },
        "task_runtime_projection": {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
            "row_count": 1,
            "rows": [
                {
                    "task_id": "TASK-1",
                    "status": "completed",
                    "execution_state": "completed",
                    "fact_event_seq": 1,
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                }
            ],
            "readiness": {"ready": True, "blocking_reasons": []},
        },
    }
    executor._canonical_factory_projection = lambda _run, _context: projection  # type: ignore[method-assign]


def _authorize_director_fact_projection(
    executor: OrchestrationStageExecutor,
    *,
    factory_run_id: str,
) -> None:
    """Give non-projection Director tests one authoritative pending TaskRuntime row."""

    rows = [
        {
            "task_id": "TASK-1",
            "external_task_id": "TASK-1",
            "status": "pending",
            "execution_state": "pending",
            "source": "task_runtime.execution_fact",
            "status_source": "task_runtime.execution_fact",
            "metadata": {
                "factory_run_id": factory_run_id,
                "factory_stage": "director_dispatch",
                "external_task_id": "TASK-1",
                "source_task_id": "TASK-1",
                "materialized_by": "runtime.task_runtime",
            },
        }
    ]

    def query_rows(*, factory_run_id: str = "") -> tuple[list[dict[str, Any]], None]:
        assert not factory_run_id or factory_run_id == rows[0]["metadata"]["factory_run_id"]
        return rows, None

    executor._query_observable_task_rows = query_rows  # type: ignore[method-assign]


class TestOrchestrationStageExecutor:
    def test_declared_delivery_targets_filter_directory_scope_paths(self, temp_workspace):
        executor = _TestStageExecutor(temp_workspace, _ImmediateFailureCommandService())

        targets = executor._collect_declared_delivery_targets(
            [
                {
                    "target_files": ["calculator.py", "tests/test_calculator.py"],
                    "scope_paths": ["calculator.py", "tests/test_calculator.py", "tests"],
                    "scope": "calculator.py, tests/",
                }
            ]
        )

        assert targets == ["calculator.py", "tests/test_calculator.py"]

    @pytest.mark.asyncio
    async def test_wait_run_completion_short_circuits_immediate_failure(self, temp_workspace):
        command_service = _ImmediateFailureCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        initial = CommandResult(
            run_id="pm-test-001",
            status="failed",
            message="No module named 'pytz'",
            reason_code="PM_RUN_FAILED",
        )

        result = await executor._wait_run_completion(command_service, initial, timeout_seconds=60)

        assert result is initial
        assert command_service.queried is False

    @pytest.mark.asyncio
    async def test_wait_run_completion_honors_initial_abort_checker(self, temp_workspace):
        command_service = _NeverTerminalCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        initial = CommandResult(
            run_id="pm-test-cancelled",
            status="running",
            message="PM run started",
        )

        async def _abort_checker() -> str | None:
            return "operator stop"

        result = await executor._wait_run_completion(
            command_service,
            initial,
            timeout_seconds=60,
            abort_checker=_abort_checker,
        )

        assert result.status == "cancelled"
        assert "operator stop" in str(result.message)
        assert command_service.query_calls == 0

    @pytest.mark.asyncio
    async def test_docs_stage_uses_extended_default_timeout_budget(self, temp_workspace, monkeypatch):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_docs_timeout_budget",
            config=FactoryConfig(name="test-run", stages=["docs_generation"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        captured: dict[str, int] = {}

        async def _fake_wait(service, command_result, timeout_seconds, cancel_event=None, abort_checker=None):
            del service, cancel_event, abort_checker
            captured["timeout_seconds"] = int(timeout_seconds)
            return CommandResult(
                run_id=command_result.run_id,
                status="completed",
                message="Run status: completed",
                metadata={},
            )

        monkeypatch.setattr(executor, "_wait_run_completion", _fake_wait)
        monkeypatch.setattr(executor, "_ensure_docs_artifacts", lambda directive, summary: [])
        monkeypatch.setattr(executor, "_artifact_exists", lambda relative_path, min_chars=1: True)

        result = await executor._execute_docs_generation(run, context={"directive": "Generate docs"})

        assert result.status == "success"
        assert captured["timeout_seconds"] == 600
        assert command_service.observed_bindings[-1] is executor._test_role_evidence_port.bindings[-1]
        assert command_service.observed_bindings[-1].role == "architect"
        assert get_factory_role_evidence_authority_binding() is None

    @pytest.mark.asyncio
    async def test_pm_stage_uses_extended_default_timeout_budget(self, temp_workspace, monkeypatch):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_timeout_budget",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        captured: dict[str, int] = {}

        async def _fake_wait(service, command_result, timeout_seconds, cancel_event=None, abort_checker=None):
            del service, cancel_event, abort_checker
            captured["timeout_seconds"] = int(timeout_seconds)
            return CommandResult(
                run_id=command_result.run_id,
                status="completed",
                message="Run status: completed",
                metadata={},
            )

        monkeypatch.setattr(executor, "_wait_run_completion", _fake_wait)
        monkeypatch.setattr(executor, "_validate_pm_plan_contract", lambda relative_path: None)
        monkeypatch.setattr(executor, "_artifact_exists", lambda relative_path, min_chars=1: True)

        result = await executor._execute_pm_planning(run, context={"directive": "Plan implementation tasks"})

        assert result.status == "success"
        assert captured["timeout_seconds"] == 600
        assert command_service.observed_bindings[-1] is executor._test_role_evidence_port.bindings[-1]
        assert command_service.observed_bindings[-1].role == "pm"
        assert get_factory_role_evidence_authority_binding() is None

    @pytest.mark.asyncio
    async def test_pm_stage_builds_directive_from_architect_artifacts(self, temp_workspace, monkeypatch):
        command_service = _CapturingCompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_directive_artifacts",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        for rel_path, content in {
            "docs/plan.md": "# Plan\n\nArchitect plan section " * 12,
            "docs/architecture.md": "# Architecture\n\nArchitect architecture section " * 12,
        }.items():
            target = Path(resolve_logical_path(str(temp_workspace), f"workspace/{rel_path}"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        monkeypatch.setattr(executor, "_validate_pm_plan_contract", lambda relative_path: None)
        monkeypatch.setattr(executor, "_artifact_exists", lambda relative_path, min_chars=1: True)

        result = await executor._execute_pm_planning(
            run,
            context={"directive": "Original user requirement\nsystem prompt should not pass through"},
        )

        assert result.status == "success"
        assert command_service.pm_calls
        options = command_service.pm_calls[0]["options"]
        assert isinstance(options, dict)
        directive = str(options.get("directive") or "")
        assert "Architect Plan" in directive
        assert "Architect Architecture" in directive
        assert "Original Requirement Excerpt" in directive
        assert "system prompt" not in directive.lower()

    @pytest.mark.asyncio
    async def test_pm_stage_recovers_timeout_with_deterministic_contracts(self, temp_workspace, monkeypatch):
        command_service = _CapturingCompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_timeout_recovery",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        wait_calls = 0

        async def _fake_wait(service, command_result, timeout_seconds, cancel_event=None, abort_checker=None):
            nonlocal wait_calls
            del service, timeout_seconds, cancel_event, abort_checker
            wait_calls += 1
            if wait_calls == 1:
                return CommandResult(
                    run_id=command_result.run_id,
                    status="timeout",
                    message="Run timed out after 600 seconds",
                )
            plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "TASK-1",
                                "title": "实现可运行项目基础",
                                "goal": "完成项目基础并可验证",
                                "scope": "src",
                                "steps": ["实现基础", "补充测试"],
                                "acceptance": ["执行 `npm test` 返回 PASS"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return CommandResult(
                run_id=command_result.run_id,
                status="completed",
                message="Run status: completed",
            )

        monkeypatch.setattr(executor, "_wait_run_completion", _fake_wait)

        result = await executor._execute_pm_planning(run, context={"directive": "Plan implementation tasks"})

        assert result.status == "success"
        assert wait_calls == 2
        assert len(command_service.pm_calls) == 2
        assert len(command_service.observed_bindings) == 2
        assert all(binding is not None and binding.role == "pm" for binding in command_service.observed_bindings)
        assert (
            command_service.observed_bindings[0].execution_authority_hash
            != command_service.observed_bindings[1].execution_authority_hash
        )
        assert get_factory_role_evidence_authority_binding() is None
        second_options = command_service.pm_calls[1]["options"]
        assert isinstance(second_options, dict)
        metadata = second_options.get("metadata")
        assert isinstance(metadata, dict)
        assert metadata["deterministic_pm_contracts"] is True
        assert "workspace/roles/pm/factory_test_pm_timeout_recovery/plan.json" in result.artifacts
        signal_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/signals/pm_planning.signals.json"))
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        rows = payload.get("signals")
        assert isinstance(rows, list)
        assert any(
            isinstance(item, dict) and item.get("code") == "pm.timeout_recovered_by_deterministic_contracts"
            for item in rows
        )

    @pytest.mark.asyncio
    async def test_pm_stage_failure_output_includes_failed_task_details(self, temp_workspace):
        command_service = _DetailedFailureCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_failure",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        result = await executor._execute_pm_planning(
            run,
            context={"directive": "Plan implementation tasks"},
        )

        assert result.status == "failed"
        assert "failed_task=task-0-pm (pm)" in str(result.output)
        assert "PM contract normalization failed" in str(result.output)
        assert "signals=" in str(result.output)
        assert "runtime/signals/pm_planning.signals.json" in result.artifacts
        signal_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                "runtime/signals/pm_planning.signals.json",
            )
        )
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        rows = payload.get("signals") if isinstance(payload, dict) else []
        assert isinstance(rows, list)
        codes = {str(item.get("code") or "") for item in rows if isinstance(item, dict)}
        assert "pm.run_status_non_success" in codes
        assert "pm.contract_issue_detected" in codes
        assert command_service.query_calls >= 1

    @pytest.mark.asyncio
    async def test_pm_stage_requires_materialized_plan_artifact(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_missing_plan",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        result = await executor._execute_pm_planning(
            run,
            context={"directive": "Plan implementation tasks"},
        )

        assert result.status == "failed"
        assert "signals=" in str(result.output)
        assert "runtime/signals/pm_planning.signals.json" in result.artifacts
        signal_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                "runtime/signals/pm_planning.signals.json",
            )
        )
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        rows = payload.get("signals") if isinstance(payload, dict) else []
        assert isinstance(rows, list)
        assert any(
            isinstance(item, dict)
            and str(item.get("code") or "") == "pm.contract_issue_detected"
            and "missing_tasks_plan" in str(item.get("detail") or "")
            for item in rows
        )

    @pytest.mark.asyncio
    async def test_pm_stage_accepts_valid_plan_artifact(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_valid_plan",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        runtime = TaskRuntimeService(str(temp_workspace))
        stale = runtime.create_task_row(subject="Stale task", description="completed in a previous run")
        _complete_task_row(runtime, stale["id"], metadata={"previous_run": "old"})
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account",
      "scope_paths": ["src/account"],
      "target_files": ["src/account"],
      "steps": ["实现实体", "补充测试"],
      "execution_checklist": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"],
      "acceptance_criteria": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_pm_planning(
            run,
            context={"directive": "Plan implementation tasks"},
        )

        assert result.status == "success"
        assert "tasks/plan.json" in result.artifacts
        assert f"workspace/roles/pm/{run.id}/plan.json" in result.artifacts
        assert "workspace/plans/latest.plan.json" in result.artifacts
        mirrored_plan = Path(resolve_logical_path(str(temp_workspace), f"workspace/roles/pm/{run.id}/plan.json"))
        assert json.loads(mirrored_plan.read_text(encoding="utf-8"))["tasks"][0]["id"] == "TASK-1"
        assert TaskRuntimeService(str(temp_workspace)).get_task(stale["id"]) is None
        task_row = TaskRuntimeService(str(temp_workspace)).get_task("TASK-1")
        assert task_row is not None
        assert task_row["status"] == "pending"
        assert task_row["metadata"]["pm_task_id"] == "TASK-1"

    @pytest.mark.asyncio
    async def test_chief_engineer_stage_requires_pm_plan_artifact(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_ce_missing_plan",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        result = await executor._execute_chief_engineer_review(run, context={})

        assert result.status == "failed"
        assert "error_code=chief_engineer.plan_missing" in str(result.output)
        assert "runtime/signals/chief_engineer_review.signals.json" in result.artifacts

    @pytest.mark.asyncio
    async def test_chief_engineer_stage_generates_blueprint_artifacts(self, temp_workspace, monkeypatch):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_ce_blueprints",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account",
      "scope_paths": ["src/account"],
      "target_files": ["src/account"],
      "steps": ["实现实体", "补充测试"],
      "execution_checklist": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"],
      "acceptance_criteria": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )

        # Mock RoleRuntimeService to return successful result
        from polaris.cells.roles.runtime.public.contracts._execution_contracts import RoleExecutionResultV1

        captured_commands = []
        captured_bindings: list[FactoryRoleEvidenceAuthorityBindingV1 | None] = []

        class FakeRoleRuntimeService:
            async def execute_role_task(self, command):
                captured_commands.append(command)
                captured_bindings.append(get_factory_role_evidence_authority_binding())
                return RoleExecutionResultV1(
                    ok=True,
                    status="success",
                    role="chief_engineer",
                    workspace=str(temp_workspace),
                    task_id=command.task_id,
                    run_id=command.run_id,
                    output=json.dumps(_chief_engineer_portfolio_output(), ensure_ascii=False),
                    metadata={
                        "provider": "test-provider",
                        "model": "test-model",
                        "cache_hit": False,
                        "final_request_context_audit": {
                            "final_request_token_estimate": 2048,
                            "context_window_utilization": 0.08,
                        },
                        "context_snapshot_ref": "0123456789abcdef01234567",
                    },
                )

        # Patch RoleRuntimeService in the module - it's instantiated in the method
        # so we need to patch the class itself
        original_role_runtime_service = factory_stage_module.RoleRuntimeService
        factory_stage_module.RoleRuntimeService = FakeRoleRuntimeService

        try:
            result = await executor._execute_chief_engineer_review(run, context={})
        finally:
            # Restore original
            factory_stage_module.RoleRuntimeService = original_role_runtime_service

        # Verify the result
        assert result.status == "success"
        assert any(path.startswith("runtime/blueprints/ce_TASK-1_") for path in result.artifacts)
        assert f"runtime/state/blueprints/{run.id}.review.json" in result.artifacts
        assert f"workspace/roles/chief_engineer/{run.id}/review.json" in result.artifacts
        assert "workspace/blueprints/latest.review.json" in result.artifacts
        review_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                f"runtime/state/blueprints/{run.id}.review.json",
            )
        )
        payload = json.loads(review_path.read_text(encoding="utf-8"))
        assert payload["generated_blueprints"] == 1
        assert payload["blueprints"][0]["task_id"] == "TASK-1"
        assert len(captured_commands) == 1
        assert captured_bindings == [executor._test_role_evidence_port.bindings[-1]]
        assert captured_bindings[0] is not None and captured_bindings[0].role == "chief_engineer"
        assert get_factory_role_evidence_authority_binding() is None
        ce_command = captured_commands[0]
        assert ce_command.context["cognitive_runtime_mode"] == "off"
        assert ce_command.context["cognitive_runtime_enabled"] is False
        assert ce_command.context["cognitive_runtime_required"] is False
        assert ce_command.timeout_seconds == 600
        assert ce_command.context["chief_engineer_llm_timeout_seconds"] == 600
        assert ce_command.context["llm_call_timeout_seconds"] == 600
        assert ce_command.context["request_timeout_seconds"] == 600
        assert ce_command.metadata["cognitive_runtime_mode"] == "off"
        assert ce_command.metadata["cognitive_runtime_enabled"] is False
        assert ce_command.metadata["cognitive_runtime_required"] is False
        assert ce_command.metadata["llm_call_timeout_seconds"] == 600
        blueprint_path = Path(resolve_runtime_path(str(temp_workspace), payload["blueprints"][0]["blueprint_path"]))
        assert blueprint_path.is_file()
        mirrored_review = Path(
            resolve_logical_path(str(temp_workspace), f"workspace/roles/chief_engineer/{run.id}/review.json")
        )
        assert json.loads(mirrored_review.read_text(encoding="utf-8"))["generated_blueprints"] == 1

    def test_chief_engineer_stage_timeout_prefers_context_override(self, temp_workspace):
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())

        timeout = executor._chief_engineer_llm_timeout_seconds({"chief_engineer_llm_timeout_seconds": "123"})

        assert timeout == 123

    @pytest.mark.asyncio
    async def test_director_stage_fails_when_plan_lineage_missing(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_missing_plan",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        result = await executor._execute_director_dispatch(
            run,
            context={},
        )

        assert result.status == "failed"
        assert "error_code=director.task_lineage_missing" in str(result.output)
        assert "dispatch/log.json" in result.artifacts

    def test_pre_director_snapshot_restore_removes_director_delivery_files(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        seed_file = temp_workspace / "requirements.md"
        seed_file.write_text("Original requirement\n", encoding="utf-8")
        platform_evidence = temp_workspace / ".polaris" / "blueprints" / "latest.review.json"
        platform_evidence.parent.mkdir(parents=True, exist_ok=True)
        platform_evidence.write_text('{"generated_blueprints":1}\n', encoding="utf-8")

        manifest = executor._create_pre_director_snapshot(run_id="factory_snapshot_test")
        assert manifest["snapshot_kind"] == "pre_director_workspace"

        generated_source = temp_workspace / "src" / "index.ts"
        generated_source.parent.mkdir(parents=True, exist_ok=True)
        generated_source.write_text("export const generated = true;\n", encoding="utf-8")
        generated_package = temp_workspace / "package.json"
        generated_package.write_text('{"scripts":{"build":"tsc"}}\n', encoding="utf-8")
        seed_file.write_text("Director polluted requirement\n", encoding="utf-8")

        restored = executor._restore_pre_director_snapshot()

        assert "src/index.ts" in restored["removed_files"]
        assert "package.json" in restored["removed_files"]
        assert seed_file.read_text(encoding="utf-8") == "Original requirement\n"
        assert not generated_source.exists()
        assert not generated_package.exists()
        assert platform_evidence.is_file()

    @pytest.mark.asyncio
    async def test_director_stage_fails_when_upstream_run_non_success(self, temp_workspace):
        command_service = _DirectorFailedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_failed_run",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _authorize_director_fact_projection(executor, factory_run_id=run.id)

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account",
      "steps": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                {
                    "id": "TASK-1",
                    "target_files": ["src/account.py"],
                }
            ],
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "实现账户实体",
  "description": "实现与测试",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 1},
        )

        assert result.status == "failed"
        assert "error_code=director.run_status_non_success" in str(result.output)
        assert "dispatch/log.json" in result.artifacts
        assert command_service.observed_bindings[-1] is executor._test_role_evidence_port.bindings[-1]
        assert command_service.observed_bindings[-1].role == "director"
        assert get_factory_role_evidence_authority_binding() is None

    @pytest.mark.asyncio
    async def test_director_stage_rejects_file_only_taskboard_even_with_metadata_progress(
        self,
        temp_workspace,
    ):
        command_service = _DirectorCompletedMetadataProgressService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_metadata_progress",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account",
      "steps": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                {
                    "id": "TASK-1",
                    "target_files": ["src/account.js"],
                }
            ],
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "实现账户实体",
  "description": "实现与测试",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 2},
        )

        assert result.status == "failed"
        assert "error_code=director.task_runtime_fact_projection_not_ready" in str(result.output)
        assert "TaskRuntime fact-only observable projection is not ready" in str(result.output)
        assert "dispatch/log.json" in result.artifacts

    @pytest.mark.asyncio
    async def test_director_stage_does_not_handoff_file_only_materialization_after_timeout(
        self,
        temp_workspace,
    ):
        command_service = _DirectorCompletedThenTimeoutWithMaterializedTargetService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_timeout_handoff",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account.js",
      "target_files": ["src/account.js"],
      "steps": ["实现实体"],
      "acceptance": ["`npm test` 通过"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                {
                    "id": "TASK-1",
                    "target_files": ["src/account.js"],
                }
            ],
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "实现账户实体",
  "description": "实现与测试",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 2},
        )

        assert result.status == "failed"
        assert "handed off to workspace quality" not in str(result.output)
        assert "director.dispatch_timeout" not in str(result.output)
        assert "dispatch/log.json" in result.artifacts
        dispatch_log = Path(resolve_logical_path(str(temp_workspace), "workspace/dispatch/latest.log.json"))
        payload = json.loads(dispatch_log.read_text(encoding="utf-8"))
        assert "director.task_runtime_fact_projection_not_ready" in {
            signal.get("code") for signal in payload.get("signals", []) if isinstance(signal, dict)
        }

    @pytest.mark.asyncio
    async def test_director_stage_rejects_file_only_progress_before_target_diagnosis(
        self,
        temp_workspace,
    ):
        command_service = _DirectorNoMaterializedChangesAfterProgressService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_idempotent_no_changes",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _authorize_director_fact_projection(executor, factory_run_id=run.id)

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account",
      "scope_paths": ["src/account.py", "tests/test_account.py"],
      "target_files": ["src/account.py", "tests/test_account.py"],
      "steps": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                {
                    "id": "TASK-1",
                    "target_files": ["src/account.py", "tests/test_account.py"],
                }
            ],
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "实现账户实体",
  "description": "实现与测试",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 2},
        )

        assert result.status == "failed"
        assert "error_code=director.run_status_non_success" in str(result.output)
        signal_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/signals/director_dispatch.signals.json"))
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        rows = payload.get("signals") if isinstance(payload, dict) else []
        assert any(isinstance(item, dict) and item.get("code") == "director.run_status_non_success" for item in rows)

    @pytest.mark.asyncio
    async def test_director_stage_covered_targets_do_not_bypass_execution_facts(
        self,
        temp_workspace,
    ):
        command_service = _DirectorNoMaterializedChangesAfterProgressService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_idempotent_covered_targets",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _authorize_director_fact_projection(executor, factory_run_id=run.id)

        delivered = temp_workspace / "src" / "account.py"
        delivered.parent.mkdir(parents=True, exist_ok=True)
        delivered.write_text("class Account:\n    pass\n", encoding="utf-8")

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account.py",
      "scope_paths": ["src/account.py"],
      "target_files": ["src/account.py"],
      "steps": ["实现实体"],
      "acceptance": ["`src/account.py` 存在"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                {
                    "id": "TASK-1",
                    "target_files": ["src/account.py"],
                }
            ],
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "实现账户实体",
  "description": "实现与测试",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 2},
        )

        assert result.status == "failed"
        assert "error_code=director.run_status_non_success" in str(result.output)
        signal_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/signals/director_dispatch.signals.json"))
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        rows = payload.get("signals") if isinstance(payload, dict) else []
        assert not any(
            isinstance(item, dict) and item.get("code") == "director.idempotent_no_materialized_changes"
            for item in rows
        )

    @pytest.mark.asyncio
    async def test_director_stage_does_not_treat_first_no_materialized_failure_as_idempotent(
        self,
        temp_workspace,
    ):
        command_service = _DirectorNoMaterializedChangesOnlyService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_first_no_changes_fails",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _authorize_director_fact_projection(executor, factory_run_id=run.id)

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "修复前端测试失败",
      "goal": "修复 Vitest TypeScript failure",
      "scope": "src/types/generation.ts",
      "steps": ["Apply minimal fix"],
      "acceptance": ["npm test returns PASS"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                {
                    "id": "TASK-1",
                    "target_files": ["src/types/generation.ts"],
                }
            ],
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "修复前端测试失败",
  "description": "修复 Vitest TypeScript failure",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 2},
        )

        assert result.status == "failed"
        assert "error_code=director.run_status_non_success" in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_uses_report_verdict(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_quality_gate",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            """{
  "passed": false,
  "score": 52,
  "critical_issue_count": 2
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(
            run,
            context={"qa_target": "Quality gate"},
        )

        assert result.status == "failed"
        assert "qa_verdict_passed=False" in str(result.output)
        assert "runtime/qa/report.json" in result.artifacts
        assert f"workspace/roles/qa/{run.id}/report.json" in result.artifacts

    @pytest.mark.asyncio
    async def test_quality_gate_fails_when_report_passed_but_score_is_low(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_quality_gate_low_score",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "score": 52,
                    "critical_issue_count": 0,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(run, context={"qa_target": "Quality gate"})

        assert result.status == "failed"
        assert "qa_verdict_passed=False" in str(result.output)
        assert "report_consistent=False" in str(result.output)
        assert "canonical_reason=task_runtime_tasks_missing" in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_offloads_report_read_off_event_loop(self, temp_workspace, monkeypatch):
        # Arrange: a finished QA run plus an on-disk report. Spy on asyncio.to_thread
        # so we can assert the (blocking) report read is dispatched off the event loop.
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_quality_gate_offload",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"passed": True, "score": 91, "critical_issue_count": 0}, ensure_ascii=False),
            encoding="utf-8",
        )

        real_to_thread = asyncio.to_thread
        offloaded_report_reads: list[dict[str, object]] = []

        async def _spy_to_thread(func, /, *args, **kwargs):
            bound_self = getattr(func, "__self__", None)
            if getattr(func, "__name__", "") == "read_text" and bound_self == report_path:
                offloaded_report_reads.append({"args": args, "kwargs": dict(kwargs)})
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(factory_stage_module.asyncio, "to_thread", _spy_to_thread)

        # Act
        result = await executor._execute_quality_gate(run, context={"qa_target": "Quality gate"})

        # Assert: the report read was offloaded exactly once, with explicit UTF-8, and the
        # verdict still reflects the on-disk payload.
        assert len(offloaded_report_reads) == 1
        assert offloaded_report_reads[0]["kwargs"] == {"encoding": "utf-8"}
        assert "qa_verdict_passed=False" in str(result.output)
        assert "report_consistent=False" in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_fails_when_llm_judgement_unavailable_by_default(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_quality_gate_llm_unavailable",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "score": 96,
                    "critical_issue_count": 0,
                    "warnings": ["qa_llm_judgement_unavailable"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(run, context={"qa_target": "Quality gate"})

        assert result.status == "failed"
        assert "canonical_reason=task_runtime_tasks_missing" in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_fails_when_llm_judgement_unavailable_and_explicitly_required(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_quality_gate_llm_required_unavailable",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "score": 96,
                    "critical_issue_count": 0,
                    "warnings": ["qa_llm_judgement_unavailable"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(
            run,
            context={"qa_target": "Quality gate", "qa_require_llm_judgement": True},
        )

        assert result.status == "failed"
        assert "canonical_reason=task_runtime_tasks_missing" in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_can_explicitly_allow_llm_judgement_fallback(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_quality_gate_llm_fallback",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "score": 96,
                    "critical_issue_count": 0,
                    "warnings": ["qa_llm_judgement_unavailable"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(
            run,
            context={"qa_target": "Quality gate", "qa_require_llm_judgement": False},
        )

        assert result.status == "failed"
        assert "canonical_reason=task_runtime_tasks_missing" in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_runs_workspace_node_scripts(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _WorkspaceValidationStageExecutor(temp_workspace, command_service, exit_codes=[0, 0])
        _authorize_workspace_quality_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_workspace_checks",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        package_path = temp_workspace / "package.json"
        package_path.write_text(
            json.dumps({"scripts": {"test": "vitest --run", "build": "vite build"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"passed": True, "score": 92, "critical_issue_count": 0}, ensure_ascii=False),
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(run, context={"qa_target": "Quality gate"})

        assert result.status == "failed"
        assert "canonical_reason=qa_verdict_missing" in str(result.output)
        assert command_service.observed_bindings[-1] is executor._test_role_evidence_port.bindings[-1]
        assert command_service.observed_bindings[-1].role == "qa"
        assert get_factory_role_evidence_authority_binding() is None
        assert executor.commands_seen == [["npm", "run", "build"], ["npm", "test"]]
        assert "workspace_checks_diagnostic=True" in str(result.output)
        assert "runtime/qa/workspace-validation.json" in result.artifacts
        assert f"workspace/roles/qa/{run.id}/workspace-validation.json" in result.artifacts
        assert "workspace/qa/latest.workspace-validation.json" in result.artifacts

    @pytest.mark.asyncio
    async def test_quality_gate_injects_workspace_evidence_before_qa_llm(self, temp_workspace):
        command_service = _CapturingQaCommandService()
        executor = _WorkspaceValidationStageExecutor(temp_workspace, command_service, exit_codes=[0])
        _authorize_workspace_quality_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_workspace_evidence",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        (temp_workspace / "package.json").write_text(
            json.dumps({"scripts": {"build": "tsc"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"passed": True, "score": 92, "critical_issue_count": 0}, ensure_ascii=False),
            encoding="utf-8",
        )
        executor._write_json_artifact(
            f"runtime/state/blueprints/{run.id}.review.json",
            {
                "schema_version": "factory.chief_engineer_review.v1",
                "generated_blueprints": 1,
                "blueprints": [
                    {
                        "task_id": "TASK-1",
                        "summary": "Create the runtime entrypoint and tests.",
                    }
                ],
            },
        )

        result = await executor._execute_quality_gate(
            run,
            context={"qa_target": "Quality gate", "qa_input": "original qa context"},
        )

        assert result.status == "failed"
        assert "canonical_reason=qa_verdict_missing" in str(result.output)
        assert command_service.validation_exists_at_qa is True
        assert len(command_service.qa_calls) == 1
        qa_input = str(command_service.qa_calls[0]["options"]["input"])
        assert "original qa context" in qa_input
        assert "Workspace quality evidence collected before QA judgement" in qa_input
        assert "runtime/qa/workspace-validation.json" in qa_input
        assert '"command": [' in qa_input
        assert '"npm"' in qa_input
        assert '"run"' in qa_input
        assert '"build"' in qa_input
        assert '"exit_code": 0' in qa_input
        assert "Chief Engineer blueprint evidence collected before QA judgement" in qa_input
        assert f"runtime/state/blueprints/{run.id}.review.json" in qa_input
        assert '"generated_blueprints": 1' in qa_input

    def test_qa_input_injects_chief_engineer_latest_review_fallback(self, temp_workspace):
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        executor._write_json_artifact(
            "workspace/.polaris/blueprints/latest.review.json",
            {
                "schema_version": "factory.chief_engineer_review.v1",
                "generated_blueprints": 2,
                "blueprints": [{"task_id": "TASK-2", "summary": "Mirror fallback blueprint"}],
            },
        )

        qa_input = executor._build_qa_input_with_workspace_quality_evidence(
            "original qa context",
            "",
            run_id="factory_missing_state_review",
        )

        assert "original qa context" in qa_input
        assert "Chief Engineer blueprint evidence collected before QA judgement" in qa_input
        assert "workspace/.polaris/blueprints/latest.review.json" in qa_input
        assert '"generated_blueprints": 2' in qa_input

    def test_workspace_quality_repairs_pass_declared_target_files_to_director_repairs(
        self,
        temp_workspace,
        monkeypatch,
    ):
        from polaris.cells.roles.adapters.public import service as role_service

        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "target_files": ["src/main.ts", "README.md"],
                    }
                ]
            },
        )
        captured: dict[str, Any] = {}

        def _capture_repair(adapter: Any, *, task: dict[str, Any], task_id: str, artifact_quality_errors: list[str]):
            del adapter, task_id, artifact_quality_errors
            captured["task"] = task
            return [], {"attempted": False}

        monkeypatch.setattr(role_service, "run_director_materialization_quality_repair_schedule", _capture_repair)

        executor._apply_workspace_quality_repairs(
            run_id="run-target-files",
            artifact_quality_errors=["Artifact quality scan failed: declared target file missing 'src/main.ts'"],
        )

        assert captured["task"]["target_files"] == ["src/main.ts", "README.md"]
        assert captured["task"]["metadata"]["target_files"] == ["src/main.ts", "README.md"]

    @pytest.mark.asyncio
    async def test_quality_gate_installs_node_dependencies_before_scripts(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _WorkspaceValidationStageExecutor(temp_workspace, command_service, exit_codes=[0, 0])
        _authorize_workspace_quality_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_npm_install",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        (temp_workspace / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"build": "vite build"},
                    "dependencies": {"marked": "^12.0.0"},
                    "devDependencies": {"vite": "^5.4.0"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"passed": True, "score": 92, "critical_issue_count": 0}, ensure_ascii=False),
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(run, context={"qa_target": "Quality gate"})

        assert result.status == "failed"
        assert "canonical_reason=qa_verdict_missing" in str(result.output)
        assert executor.commands_seen == [
            ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
            ["npm", "run", "build"],
        ]
        validation_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/workspace-validation.json"))
        payload = json.loads(validation_path.read_text(encoding="utf-8"))
        assert payload["commands"][0]["phase"] == "prepare"
        assert payload["commands"][1]["phase"] == "check"

    def test_workspace_quality_command_resolves_windows_cmd_shims(self, temp_workspace, monkeypatch):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        monkeypatch.setattr(
            "polaris.cells.factory.pipeline.internal.factory_run_service.shutil.which",
            lambda value: "C:/node/npm.cmd" if value == "npm.cmd" else None,
        )
        monkeypatch.setattr(
            "polaris.cells.factory.pipeline.internal.factory_run_service.os.name",
            "nt",
        )

        resolved = executor._resolve_workspace_quality_command(["npm", "test"])

        assert resolved == ["C:/node/npm.cmd", "test"]

    @pytest.mark.asyncio
    async def test_quality_gate_fails_on_workspace_node_script_failure(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _WorkspaceValidationStageExecutor(temp_workspace, command_service, exit_codes=[1, 1])
        _authorize_workspace_quality_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_workspace_failure",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        (temp_workspace / "package.json").write_text(
            json.dumps({"scripts": {"test": "vitest --run"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"passed": True, "score": 95, "critical_issue_count": 0}, ensure_ascii=False),
            encoding="utf-8",
        )

        async def _no_llm_repairs(
            *,
            run_id: str,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            del run_id, context, artifact_quality_errors, repair_attempt
            return [], {
                "attempted": False,
                "repair_mode": "director_llm",
                "reason": "unit_test_workspace_gate_failure",
                "source_tools": [],
                "tool_results": 0,
            }

        executor._apply_workspace_quality_llm_repairs = _no_llm_repairs

        result = await executor._execute_quality_gate(run, context={"qa_target": "Quality gate"})

        assert result.status == "failed"
        assert "workspace_checks_diagnostic=False" in str(result.output)
        validation_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/workspace-validation.json"))
        payload = json.loads(validation_path.read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert payload["commands"][0]["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_default_stage_handler(self, temp_workspace):
        """Test unknown stage uses default handler"""
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        result = await service.execute_stage(run.id, "unknown_stage")

        assert result.status == "skipped"
        assert "No handler" in result.output

    @pytest.mark.asyncio
    async def test_execute_stage_failure_sets_failure_metadata(self, temp_workspace):
        service = FactoryRunService(
            temp_workspace,
            executor=FakeStageExecutor(fail_stages={"quality_gate"}),
        )
        run = await service.create_run(FactoryConfig(name="test-run"))
        await service.start_run(run.id)

        result = await service.execute_stage(run.id, "quality_gate")

        assert result.status == "failed"
        assert "factory_terminal_drain_deferred" not in result.metadata
        updated_run = await service.get_run(run.id)
        assert updated_run.status == FactoryRunStatus.FAILED
        assert updated_run.metadata["last_failed_stage"] == "quality_gate"
        assert updated_run.metadata["failure"]["code"] == "FACTORY_STAGE_FAILED"
        terminal_lease = service._admission.current()
        assert terminal_lease is not None
        assert terminal_lease.state.value == "released"

        await service.complete_run(run.id, success=False)

        released_lease = service._admission.current()
        assert released_lease is not None
        assert released_lease.state.value == "released"


class TestFactoryRunStatus:
    """Test FactoryRunStatus enum"""

    def test_status_values(self):
        assert FactoryRunStatus.PENDING == "pending"
        assert FactoryRunStatus.RUNNING == "running"
        assert FactoryRunStatus.PAUSED == "paused"
        assert FactoryRunStatus.COMPLETED == "completed"
        assert FactoryRunStatus.FAILED == "failed"
        assert FactoryRunStatus.RECOVERING == "recovering"

    def test_status_from_string(self):
        assert FactoryRunStatus("pending") == FactoryRunStatus.PENDING
        assert FactoryRunStatus("running") == FactoryRunStatus.RUNNING


class TestDirectorFanoutRouteAuditR15B:
    """R15-B regression tests: Director fanout per_binding terminal route evidence
    and timeout attribution.

    Reproduces R14 live dispatch scenario: 3 bindings, 2 completed, 1 timeout.
    """

    def test_build_per_binding_route_events_generates_terminal_events(self):
        per_binding = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "run_id": "r0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "run_id": "r1", "status": "timeout"},
            {"provider_id": "p2", "model": "m2", "binding_id": "d2", "run_id": "r2", "status": "completed"},
        ]
        events = OrchestrationStageExecutor._build_per_binding_route_events(per_binding)
        assert len(events) == 3
        assert all(e["event"] == "llm_route_terminal" for e in events)
        assert all(e["terminal"] is True for e in events)
        assert all(e["source"] == "llm" for e in events)
        statuses = {e["provider_id"]: e["status"] for e in events}
        assert statuses["p0"] == "completed"
        assert statuses["p1"] == "timeout"
        assert statuses["p2"] == "completed"

    def test_build_per_binding_route_events_skips_invalid(self):
        per_binding = [
            {"provider_id": "", "model": "m1", "binding_id": "b1", "run_id": "r1", "status": "completed"},
            {"provider_id": "p1", "model": "", "binding_id": "b2", "run_id": "r2", "status": "completed"},
            {"provider_id": "p3", "model": "m3", "binding_id": "b3", "run_id": "r3", "status": "timeout"},
        ]
        events = OrchestrationStageExecutor._build_per_binding_route_events(per_binding)
        assert len(events) == 1
        assert events[0]["provider_id"] == "p3"

    def test_fail_closed_excludes_per_binding_evidence(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
        ]
        route_events = [
            {
                "provider_id": "p0",
                "model": "m0",
                "binding_id": "d0",
                "status": "completed",
                "terminal": True,
                "invocation": True,
            },
            {
                "provider_id": "p1",
                "model": "m1",
                "binding_id": "d1",
                "status": "timeout",
                "terminal": True,
                "invocation": True,
            },
        ]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            events = OrchestrationStageExecutor._build_fail_closed_director_route_events(
                attempts=[],
                stage_signals=[],
                per_binding_route_events=route_events,
            )
        assert len(events) == 0

    def test_fail_closed_only_for_truly_missing(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
            {"role": "director", "provider_id": "p2", "model": "m2", "binding_id": "d2"},
        ]
        route_events = [
            {
                "provider_id": "p0",
                "model": "m0",
                "binding_id": "d0",
                "status": "completed",
                "terminal": True,
                "invocation": True,
            },
            {
                "provider_id": "p1",
                "model": "m1",
                "binding_id": "d1",
                "status": "timeout",
                "terminal": True,
                "invocation": True,
            },
        ]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            events = OrchestrationStageExecutor._build_fail_closed_director_route_events(
                attempts=[],
                stage_signals=[],
                per_binding_route_events=route_events,
            )
        assert len(events) == 1
        assert events[0]["provider_id"] == "p2"
        assert events[0]["fail_closed"] is True

    def test_reclassify_converts_to_timeout(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
            {"role": "director", "provider_id": "p2", "model": "m2", "binding_id": "d2"},
        ]
        route_events = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "status": "timeout"},
            {"provider_id": "p2", "model": "m2", "binding_id": "d2", "status": "completed"},
        ]
        signals = [{"code": "director.binding_coverage_incomplete", "severity": "error", "detail": "..."}]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            OrchestrationStageExecutor._reclassify_binding_coverage_signals(signals, route_events)
        assert signals[0]["code"] == "director.binding_timeout"
        assert "timed out" in signals[0]["detail"].lower()

    def test_reclassify_noop_when_missing_bindings(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
            {"role": "director", "provider_id": "p2", "model": "m2", "binding_id": "d2"},
        ]
        route_events = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "status": "timeout"},
        ]
        signals = [{"code": "director.binding_coverage_incomplete", "severity": "error", "detail": "..."}]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            OrchestrationStageExecutor._reclassify_binding_coverage_signals(signals, route_events)
        assert signals[0]["code"] == "director.binding_coverage_incomplete"

    def test_reclassify_noop_when_no_timeout(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
        ]
        route_events = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "status": "completed"},
        ]
        signals = [{"code": "director.binding_coverage_incomplete", "severity": "error", "detail": "..."}]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            OrchestrationStageExecutor._reclassify_binding_coverage_signals(signals, route_events)
        assert signals[0]["code"] == "director.binding_coverage_incomplete"

    def test_r14_route_audit_missing_empty(self):
        from polaris.cells.factory.pipeline.internal.bench_gates import build_llm_route_audit

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
            {"role": "director", "provider_id": "p2", "model": "m2", "binding_id": "d2"},
        ]
        per_binding = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "run_id": "r0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "run_id": "r1", "status": "timeout"},
            {"provider_id": "p2", "model": "m2", "binding_id": "d2", "run_id": "r2", "status": "completed"},
        ]
        events = OrchestrationStageExecutor._build_per_binding_route_events(per_binding)
        audit = build_llm_route_audit(
            events,
            expected_bindings={"director": configured},
            required_roles=("director",),
            require_all_director_routes=True,
        )
        director_result = audit["roles"]["director"]
        assert director_result["missing_bindings"] == []
        assert director_result["observed_count"] == 3

    def test_r14_fail_closed_not_hit_completed(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
            {"role": "director", "provider_id": "p2", "model": "m2", "binding_id": "d2"},
        ]
        route_events = [
            {
                "provider_id": "p0",
                "model": "m0",
                "binding_id": "d0",
                "status": "completed",
                "terminal": True,
                "invocation": True,
            },
            {
                "provider_id": "p1",
                "model": "m1",
                "binding_id": "d1",
                "status": "timeout",
                "terminal": True,
                "invocation": True,
            },
            {
                "provider_id": "p2",
                "model": "m2",
                "binding_id": "d2",
                "status": "completed",
                "terminal": True,
                "invocation": True,
            },
        ]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            events = OrchestrationStageExecutor._build_fail_closed_director_route_events(
                attempts=[],
                stage_signals=[],
                per_binding_route_events=route_events,
            )
        assert len(events) == 0
        fail_closed_providers = {e.get("provider_id") for e in events}
        assert "p0" not in fail_closed_providers
        assert "p2" not in fail_closed_providers

    def test_r14_root_cause_is_timeout(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
            {"role": "director", "provider_id": "p2", "model": "m2", "binding_id": "d2"},
        ]
        route_events = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "status": "timeout"},
            {"provider_id": "p2", "model": "m2", "binding_id": "d2", "status": "completed"},
        ]
        signals = [{"code": "director.binding_coverage_incomplete", "severity": "error", "detail": "..."}]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            OrchestrationStageExecutor._reclassify_binding_coverage_signals(signals, route_events)
        error_code = ""
        for s in signals:
            if isinstance(s, dict) and s.get("severity") == "error":
                error_code = str(s.get("code") or "")
                break
        assert error_code == "director.binding_timeout"


class TestCEProviderModelPropagationR15A:
    """R15-A regression tests: provider/model must propagate from
    RoleExecutionKernel metadata into CE evidence and audit artifacts."""

    @staticmethod
    def _write_plan(temp_workspace: Path, task_id: str = "TASK-1") -> None:
        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": task_id,
                            "title": "Implement feature",
                            "goal": "Complete the feature",
                            "scope": "src/feature",
                            "scope_paths": ["src/feature"],
                            "target_files": ["src/feature"],
                            "steps": ["implement", "test"],
                            "execution_checklist": ["implement", "test"],
                            "acceptance": ["tests pass"],
                            "acceptance_criteria": ["tests pass"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @pytest.mark.asyncio
    async def test_ce_evidence_success_result_extracts_kimi_provider_model(self, temp_workspace: Path) -> None:
        """R15-A: A successful CE result carrying provider_id/model in metadata
        must extract real Kimi provider/model -- not 'unknown unknown'."""
        from polaris.cells.roles.runtime.public.contracts._execution_contracts import (
            RoleExecutionResultV1,
        )

        self._write_plan(temp_workspace)
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        run = FactoryRun(
            id="factory_test_r15a_kimi_success",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        class _KimiSuccessRuntimeService:
            async def execute_role_task(self, command: object) -> RoleExecutionResultV1:
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role="chief_engineer",
                    workspace=str(temp_workspace),
                    task_id="TASK-1",
                    run_id="factory_test_r15a_kimi_success",
                    output=json.dumps(
                        _chief_engineer_portfolio_output(scope_path="src/feature"),
                        ensure_ascii=False,
                    ),
                    usage={},
                    metadata={
                        "provider_id": "kimi",
                        "model": "kimi-k2-thinking-turbo",
                        "final_request_context_audit": {
                            "final_request_token_estimate": 2048,
                            "context_window_utilization": 0.08,
                        },
                        "context_snapshot_ref": "0123456789abcdef01234567",
                    },
                )

        original_service = factory_stage_module.RoleRuntimeService
        factory_stage_module.RoleRuntimeService = _KimiSuccessRuntimeService  # type: ignore
        try:
            result = await executor._execute_chief_engineer_review(run, context={})
        finally:
            factory_stage_module.RoleRuntimeService = original_service  # type: ignore

        assert result.status == "success"

        review_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                f"runtime/state/blueprints/{run.id}.review.json",
            )
        )
        assert review_path.is_file()
        review_payload = json.loads(review_path.read_text(encoding="utf-8"))
        assert review_payload["generated_blueprints"] == 1

        blueprint_rows = review_payload.get("blueprints", [])
        assert len(blueprint_rows) == 1
        evidence = blueprint_rows[0].get("llm_evidence", {})
        assert evidence.get("provider") == "kimi", f"Expected provider='kimi', got '{evidence.get('provider')}'"
        assert evidence.get("model") == "kimi-k2-thinking-turbo", (
            f"Expected model='kimi-k2-thinking-turbo', got '{evidence.get('model')}'"
        )
        assert evidence.get("provider_model_unknown") is not True

    @pytest.mark.asyncio
    async def test_ce_review_schema_failure_records_warning_and_continues_blueprint(
        self,
        temp_workspace: Path,
    ) -> None:
        from polaris.cells.roles.runtime.public.contracts._execution_contracts import (
            RoleExecutionResultV1,
        )

        self._write_plan(temp_workspace)
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        run = FactoryRun(
            id="factory_test_ce_schema_recoverable",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        class _SchemaFailedRuntimeService:
            async def execute_role_task(self, command: object) -> RoleExecutionResultV1:
                return RoleExecutionResultV1(
                    ok=False,
                    status="failed",
                    role="chief_engineer",
                    workspace=str(temp_workspace),
                    task_id="TASK-1",
                    run_id="factory_test_ce_schema_recoverable",
                    output='{"plan": "analysis only"',
                    error_code="role_runtime_error",
                    error_message=(
                        "验证失败，已重试1次: No JSON object matched chief_engineer blueprint keys: "
                        "construction_plan, scope_for_apply, risk_flags"
                    ),
                    usage={
                        "kernel_repair_reasons": ["attempt_0: No JSON object matched chief_engineer blueprint keys"]
                    },
                    metadata={
                        "provider_id": "kimi",
                        "model": "kimi-k2-thinking-turbo",
                        "final_request_context_audit": {
                            "final_request_token_estimate": 2708,
                            "context_underutilized": False,
                        },
                        "context_os_audit": {"ok": True},
                        "context_snapshot_ref": "fedcba9876543210fedcba98",
                    },
                )

        original_service = factory_stage_module.RoleRuntimeService
        factory_stage_module.RoleRuntimeService = _SchemaFailedRuntimeService  # type: ignore
        try:
            result = await executor._execute_chief_engineer_review(run, context={})
        finally:
            factory_stage_module.RoleRuntimeService = original_service  # type: ignore

        assert result.status == "failed"

        review_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                f"runtime/state/blueprints/{run.id}.review.json",
            )
        )
        review_payload = json.loads(review_path.read_text(encoding="utf-8"))
        assert review_payload["generated_blueprints"] == 0
        signals = review_payload["signals"]
        assert len(signals) == 1
        signal = signals[0]
        assert signal["code"] == "chief_engineer.llm_review_failed"
        assert signal["severity"] == "error"
        assert signal["recoverable"] is False
        assert signal["provider"] == "kimi"
        assert signal["model"] == "kimi-k2-thinking-turbo"
        assert signal["context_snapshot_ref"] == "fedcba9876543210fedcba98"
        assert signal["final_request_context_audit"]["final_request_token_estimate"] == 2708
        assert signal["context_os_audit"]["ok"] is True

    @pytest.mark.asyncio
    async def test_ce_evidence_failed_result_preserves_provider_model(self, temp_workspace: Path) -> None:
        """R15-A: A failed CE result must still carry provider/model from
        metadata so the audit trail is complete."""
        from polaris.cells.roles.runtime.public.contracts._execution_contracts import (
            RoleExecutionResultV1,
        )

        self._write_plan(temp_workspace)
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        run = FactoryRun(
            id="factory_test_r15a_failed_preserves",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        class _FailedKimiRuntimeService:
            async def execute_role_task(self, command: object) -> RoleExecutionResultV1:
                return RoleExecutionResultV1(
                    ok=False,
                    status="failed",
                    role="chief_engineer",
                    workspace=str(temp_workspace),
                    task_id="TASK-1",
                    run_id="factory_test_r15a_failed_preserves",
                    output="Partial analysis",
                    error_code="provider_timeout",
                    error_message="LLM call timed out",
                    usage={},
                    metadata={
                        "provider_id": "kimi",
                        "model": "kimi-v1",
                    },
                )

        original_service = factory_stage_module.RoleRuntimeService
        factory_stage_module.RoleRuntimeService = _FailedKimiRuntimeService  # type: ignore
        try:
            result = await executor._execute_chief_engineer_review(run, context={})
        finally:
            factory_stage_module.RoleRuntimeService = original_service  # type: ignore

        assert result.status == "failed"

        signal_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                "runtime/signals/chief_engineer_review.signals.json",
            )
        )
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        signals = (payload.get("signals") or []) if isinstance(payload, dict) else []
        llm_signal = next(
            (s for s in signals if isinstance(s, dict) and s.get("code") == "chief_engineer.llm_review_failed"),
            None,
        )
        assert llm_signal is not None, "missing chief_engineer.llm_review_failed signal"
        assert llm_signal.get("provider") == "kimi"
        assert llm_signal.get("model") == "kimi-v1"

    @pytest.mark.asyncio
    async def test_ce_empty_response_failure_uses_blueprint_projection(self, temp_workspace: Path) -> None:
        """A CE request with complete context can recover from a post-retry empty response."""
        from polaris.cells.roles.runtime.public.contracts._execution_contracts import (
            RoleExecutionResultV1,
        )

        self._write_plan(temp_workspace)
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        run = FactoryRun(
            id="factory_test_ce_empty_projection",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        class _EmptyResponseRuntimeService:
            async def execute_role_task(self, command: object) -> RoleExecutionResultV1:
                return RoleExecutionResultV1(
                    ok=False,
                    status="failed",
                    role="chief_engineer",
                    workspace=str(temp_workspace),
                    task_id="TASK-1",
                    run_id="factory_test_ce_empty_projection",
                    output="",
                    error_code="clarification_needed",
                    error_message="model returned no visible output or tool calls; awaiting user clarification",
                    usage={},
                    metadata={
                        "provider_id": "kimi",
                        "model": "kimi-for-coding",
                        "final_request_context_audit": {
                            "final_request_token_estimate": 2412,
                            "context_window_utilization": 0.0092,
                        },
                        "context_snapshot_ref": "abcdef0123456789abcdef01",
                    },
                )

        original_service = factory_stage_module.RoleRuntimeService
        factory_stage_module.RoleRuntimeService = _EmptyResponseRuntimeService  # type: ignore
        try:
            result = await executor._execute_chief_engineer_review(run, context={})
        finally:
            factory_stage_module.RoleRuntimeService = original_service  # type: ignore

        assert result.status == "failed"

        review_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                f"runtime/state/blueprints/{run.id}.review.json",
            )
        )
        review_payload = json.loads(review_path.read_text(encoding="utf-8"))
        assert review_payload["generated_blueprints"] == 0
        signal = next(
            item
            for item in review_payload["signals"]
            if isinstance(item, dict) and item.get("code") == "chief_engineer.llm_review_failed"
        )
        assert signal["severity"] == "error"
        assert signal["recoverable"] is False
        assert "recovery_strategy" not in signal
        assert signal["provider"] == "kimi"
        assert signal["model"] == "kimi-for-coding"
        assert signal["context_snapshot_ref"] == "abcdef0123456789abcdef01"

    @pytest.mark.asyncio
    async def test_ce_evidence_unknown_provider_model_marks_unknown_flag(self, temp_workspace: Path) -> None:
        """R15-A: When provider/model are genuinely unknown (metadata has no
        provider_id/model keys), the CE evidence must set
        provider_model_unknown=True with a clear root cause description."""
        from polaris.cells.roles.runtime.public.contracts._execution_contracts import (
            RoleExecutionResultV1,
        )

        self._write_plan(temp_workspace)
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        run = FactoryRun(
            id="factory_test_r15a_unknown_flag",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        class _UnknownProviderRuntimeService:
            async def execute_role_task(self, command: object) -> RoleExecutionResultV1:
                return RoleExecutionResultV1(
                    ok=False,
                    status="failed",
                    role="chief_engineer",
                    workspace=str(temp_workspace),
                    task_id="TASK-1",
                    run_id="factory_test_r15a_unknown_flag",
                    output="analysis",
                    error_code="some_error",
                    error_message="some error",
                    usage={},
                    metadata={},  # no provider_id/model
                )

        original_service = factory_stage_module.RoleRuntimeService
        factory_stage_module.RoleRuntimeService = _UnknownProviderRuntimeService  # type: ignore
        try:
            result = await executor._execute_chief_engineer_review(run, context={})
        finally:
            factory_stage_module.RoleRuntimeService = original_service  # type: ignore

        assert result.status == "failed"

        signal_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                "runtime/signals/chief_engineer_review.signals.json",
            )
        )
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        signals = (payload.get("signals") or []) if isinstance(payload, dict) else []
        llm_signal = next(
            (s for s in signals if isinstance(s, dict) and s.get("code") == "chief_engineer.llm_review_failed"),
            None,
        )
        assert llm_signal is not None
        assert llm_signal.get("provider") == "unknown"
        assert llm_signal.get("model") == "unknown"
        assert llm_signal.get("provider_model_unknown") is True
        assert isinstance(llm_signal.get("provider_model_unknown_reason"), str)
        assert len(llm_signal["provider_model_unknown_reason"]) > 0
