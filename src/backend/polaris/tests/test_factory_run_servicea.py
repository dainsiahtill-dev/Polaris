"""Tests for FactoryRunService and FactoryStore."""

import asyncio
import hashlib
import json
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    ArtifactObligationV1,
    BlueprintPersistence,
    EntrypointObligationV1,
    ProjectCompletionObligationsV1,
    VerificationCommandAuthorityV1,
    VerificationObligationV1,
    build_project_completion_contract,
    derive_project_kind_authority_from_catalog_snapshot,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)
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

    build_authority = VerificationCommandAuthorityV1(
        task_id=task_id,
        modality="build",
        argv=("python", "-m", "compileall", "."),
    )
    test_authority = VerificationCommandAuthorityV1(
        task_id=task_id,
        modality="test",
        argv=("pytest", "-q"),
    )
    test_path = f"tests/test_{Path(scope_path).stem}.py"

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
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "artifact-task-1",
                        "path": scope_path,
                        "semantic_role": "source",
                        "applicability": "required",
                        "owner_task_id": task_id,
                    },
                    {
                        "obligation_id": "artifact-task-1-test",
                        "path": test_path,
                        "semantic_role": "test",
                        "applicability": "required",
                        "owner_task_id": task_id,
                    },
                ],
                "entrypoints": [
                    {
                        "obligation_id": "entrypoint-library-na",
                        "kind": "library",
                        "applicability": "not_applicable",
                        "owner_task_id": None,
                        "source_path": None,
                        "runtime_path": None,
                        "command": None,
                    }
                ],
                "verification": [
                    {
                        "obligation_id": "verify-build",
                        "modality": "build",
                        "command_authority_hash": build_authority.authority_hash,
                        "applicability": "required",
                        "covers_obligation_ids": ["artifact-task-1"],
                        "owner_task_id": task_id,
                    },
                    {
                        "obligation_id": "verify-test",
                        "modality": "test",
                        "command_authority_hash": test_authority.authority_hash,
                        "applicability": "required",
                        "covers_obligation_ids": ["artifact-task-1-test"],
                        "owner_task_id": task_id,
                    },
                    {
                        "obligation_id": "verify-environment-na",
                        "modality": "environment_prep",
                        "command_authority_hash": None,
                        "applicability": "not_applicable",
                        "covers_obligation_ids": [],
                        "owner_task_id": None,
                    },
                ],
            }
        },
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


