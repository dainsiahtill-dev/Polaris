from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal.factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptControlError,
)
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus,
    StageResult,
    _FactoryStageCommitArbitration,
)
from polaris.cells.factory.pipeline.internal.factory_stage_artifact_bindings import (
    FactoryStageArtifactBindingError,
    FactoryStageArtifactBindingsV1,
    PMContractArtifactBindingV1,
)
from polaris.cells.factory.pipeline.internal.factory_stage_persistence import (
    FactoryStagePersistenceError,
    FactoryStagePersistenceIntentV1,
    build_stage_persistence_intent,
)


class _SuccessExecutor:
    async def execute(self, stage: str, run: FactoryRun, context: dict[str, Any]) -> StageResult:
        return StageResult(stage=stage, status="success", output="done", artifacts=[])


async def _running_service(tmp_path: Path) -> tuple[FactoryRunService, FactoryRun]:
    service = FactoryRunService(tmp_path, executor=_SuccessExecutor())
    run = await service.create_run(FactoryConfig(name="transaction", stages=["docs_generation"]))
    await service.start_run(run.id)
    return service, run


def _claim_is_preserved(service: FactoryRunService, run_id: str) -> bool:
    lease = service._admission.current()
    return lease is not None and lease.run_id == run_id and lease.stage_execution_claim is not None


@pytest.mark.asyncio
async def test_success_orders_stage_event_before_commit_marker_and_publishes_after_ack(tmp_path: Path) -> None:
    service, run = await _running_service(tmp_path)
    published: list[str] = []

    async def capture_publish(run_id: str, event: dict[str, Any]) -> None:
        assert run_id == run.id
        published.append(str(event.get("type")))

    service._publish_factory_event = capture_publish  # type: ignore[method-assign]
    result = await service.execute_stage(run.id, "docs_generation")

    events = await service.store.get_authoritative_events(run.id)
    assert [event["type"] for event in events[-2:]] == [
        "stage_completed",
        "factory_stage_persistence_committed",
    ]
    assert published == ["stage_started", "stage_completed"]
    assert result.status == "success"
    current = await service.store.read_strict_run_snapshot(run.id)
    assert current["metadata"]["last_factory_stage_commit"]["stage_completed_event_id"] == events[-2]["event_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_step", ["save_run", "checkpoint"])
async def test_post_event_storage_failure_quarantines_without_publish_and_preserves_claim(
    tmp_path: Path,
    failed_step: str,
) -> None:
    service, run = await _running_service(tmp_path)
    published: list[str] = []

    async def capture_publish(_run_id: str, event: dict[str, Any]) -> None:
        published.append(str(event.get("type")))

    service._publish_factory_event = capture_publish  # type: ignore[method-assign]
    if failed_step == "save_run":
        original_save = service.store.save_run

        async def fail_save(candidate: FactoryRun) -> str:
            if "last_factory_stage_commit" in candidate.metadata:
                raise OSError("save failed")
            return await original_save(candidate)

        service.store.save_run = fail_save  # type: ignore[method-assign]
    else:

        async def fail_checkpoint(_candidate: FactoryRun) -> str:
            raise OSError("checkpoint failed")

        service.store.checkpoint = fail_checkpoint  # type: ignore[method-assign]

    with pytest.raises(OSError, match="failed"):
        await service.execute_stage(run.id, "docs_generation")

    events = await service.store.get_authoritative_events(run.id)
    assert events[-2]["type"] == "stage_completed"
    assert events[-1]["type"] == "factory_run_quarantined"
    assert events[-1]["failed_step"] == failed_step
    assert "factory_stage_persistence_committed" not in {event["type"] for event in events}
    assert "stage_completed" not in published
    assert _claim_is_preserved(service, run.id)


@pytest.mark.asyncio
async def test_detached_stage_append_failure_has_no_success_publish_and_preserves_claim(tmp_path: Path) -> None:
    service, run = await _running_service(tmp_path)
    original_append = service._append_event
    published: list[str] = []

    async def fail_stage_append(
        run_id: str,
        event: dict[str, Any],
        *,
        publish: bool = True,
        commit_permit: Callable[[], object] | None = None,
    ) -> dict[str, Any]:
        if event.get("type") == "stage_completed":
            raise OSError("detached append failed")
        return await original_append(
            run_id,
            event,
            publish=publish,
            commit_permit=commit_permit,  # type: ignore[arg-type]
        )

    async def capture_publish(_run_id: str, event: dict[str, Any]) -> None:
        published.append(str(event.get("type")))

    service._append_event = fail_stage_append  # type: ignore[method-assign]
    service._publish_factory_event = capture_publish  # type: ignore[method-assign]

    with pytest.raises(OSError, match="detached append failed"):
        await service.execute_stage(run.id, "docs_generation")

    events = await service.store.get_authoritative_events(run.id)
    assert "stage_completed" not in {event["type"] for event in events}
    assert "stage_completed" not in published
    assert _claim_is_preserved(service, run.id)


@pytest.mark.asyncio
async def test_commit_marker_failure_appends_explicit_quarantine(tmp_path: Path) -> None:
    service, run = await _running_service(tmp_path)
    original_append = service._append_event

    async def fail_marker(
        run_id: str,
        event: dict[str, Any],
        *,
        publish: bool = True,
        commit_permit: Callable[[], object] | None = None,
    ) -> dict[str, Any]:
        if event.get("type") == "factory_stage_persistence_committed":
            raise OSError("marker failed")
        return await original_append(
            run_id,
            event,
            publish=publish,
            commit_permit=commit_permit,  # type: ignore[arg-type]
        )

    service._append_event = fail_marker  # type: ignore[method-assign]
    with pytest.raises(OSError, match="marker failed"):
        await service.execute_stage(run.id, "docs_generation")

    events = await service.store.get_authoritative_events(run.id)
    assert events[-1]["type"] == "factory_run_quarantined"
    assert events[-1]["failed_step"] == "commit_marker"
    assert _claim_is_preserved(service, run.id)


@pytest.mark.asyncio
async def test_publish_failure_is_non_authoritative_after_commit_ack(tmp_path: Path) -> None:
    service, run = await _running_service(tmp_path)

    original_publish = service._publish_factory_event

    async def fail_publish(run_id: str, event: dict[str, Any]) -> None:
        if event.get("type") == "stage_completed":
            raise RuntimeError("fanout down")
        await original_publish(run_id, event)

    service._publish_factory_event = fail_publish  # type: ignore[method-assign]
    result = await service.execute_stage(run.id, "docs_generation")

    events = await service.store.get_authoritative_events(run.id)
    assert result.status == "success"
    assert events[-1]["type"] == "factory_stage_persistence_committed"
    assert not _claim_is_preserved(service, run.id)


@pytest.mark.asyncio
async def test_cancellation_cut_before_marker_prevents_marker_and_publish_and_quarantines(tmp_path: Path) -> None:
    service, run = await _running_service(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()
    original_save = service.store.save_run
    published: list[str] = []

    async def blocked_save(candidate: FactoryRun) -> str:
        if "last_factory_stage_commit" in candidate.metadata:
            entered.set()
            await release.wait()
        return await original_save(candidate)

    service.store.save_run = blocked_save  # type: ignore[method-assign]

    async def capture_publish(_run_id: str, event: dict[str, Any]) -> None:
        published.append(str(event.get("type")))

    service._publish_factory_event = capture_publish  # type: ignore[method-assign]
    task = asyncio.create_task(service.execute_stage(run.id, "docs_generation"))
    await asyncio.wait_for(entered.wait(), timeout=3)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    events = await service.store.get_authoritative_events(run.id)
    assert events[-1]["type"] == "factory_run_quarantined"
    assert events[-1]["failed_step"] == "cancelled_before_commit_ack"
    assert "factory_stage_persistence_committed" not in {event["type"] for event in events}
    assert "stage_completed" not in published
    assert _claim_is_preserved(service, run.id)


@pytest.mark.asyncio
async def test_cancellation_after_marker_wrapper_entry_but_before_real_append_wins_cut(tmp_path: Path) -> None:
    service, run = await _running_service(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()
    original_append = service._append_event
    published: list[str] = []

    async def block_before_real_marker_append(
        run_id: str,
        event: dict[str, Any],
        *,
        publish: bool = True,
        commit_permit: Callable[[], object] | None = None,
    ) -> dict[str, Any]:
        if event.get("type") == "factory_stage_persistence_committed":
            entered.set()
            await release.wait()
        if commit_permit is None:
            return await original_append(run_id, event, publish=publish)
        return await original_append(
            run_id,
            event,
            publish=publish,
            commit_permit=commit_permit,  # type: ignore[arg-type]
        )

    async def capture_publish(_run_id: str, event: dict[str, Any]) -> None:
        published.append(str(event.get("type")))

    service._append_event = block_before_real_marker_append  # type: ignore[method-assign]
    service._publish_factory_event = capture_publish  # type: ignore[method-assign]
    task = asyncio.create_task(service.execute_stage(run.id, "docs_generation"))
    await asyncio.wait_for(entered.wait(), timeout=3)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    events = await service.store.get_authoritative_events(run.id)
    assert events[-1]["type"] == "factory_run_quarantined"
    assert events[-1]["failed_step"] == "cancelled_before_commit_ack"
    assert "factory_stage_persistence_committed" not in {event["type"] for event in events}
    assert "stage_completed" not in published
    assert _claim_is_preserved(service, run.id)


@pytest.mark.asyncio
async def test_cancellation_racing_marker_append_uses_final_authoritative_ack(tmp_path: Path) -> None:
    service, run = await _running_service(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()
    original_append = service._append_event
    published: list[str] = []

    async def append_then_block_marker_ack(
        run_id: str,
        event: dict[str, Any],
        *,
        publish: bool = True,
        commit_permit: Callable[[], object] | None = None,
    ) -> dict[str, Any]:
        appended = await original_append(
            run_id,
            event,
            publish=publish,
            commit_permit=commit_permit,  # type: ignore[arg-type]
        )
        if event.get("type") == "factory_stage_persistence_committed":
            entered.set()
            await release.wait()
        return appended

    async def capture_publish(_run_id: str, event: dict[str, Any]) -> None:
        published.append(str(event.get("type")))

    service._append_event = append_then_block_marker_ack  # type: ignore[method-assign]
    service._publish_factory_event = capture_publish  # type: ignore[method-assign]
    task = asyncio.create_task(service.execute_stage(run.id, "docs_generation"))
    await asyncio.wait_for(entered.wait(), timeout=3)
    task.cancel()
    release.set()
    result = await task

    events = await service.store.get_authoritative_events(run.id)
    assert result.status == "success"
    assert events[-1]["type"] == "factory_stage_persistence_committed"
    assert "factory_run_quarantined" not in {event["type"] for event in events}
    assert published.count("stage_completed") == 1
    assert not _claim_is_preserved(service, run.id)


@pytest.mark.asyncio
async def test_cancellation_after_marker_ack_cannot_revoke_commit(tmp_path: Path) -> None:
    service, run = await _running_service(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_publish(_run_id: str, event: dict[str, Any]) -> None:
        if event.get("type") == "stage_completed":
            entered.set()
            await release.wait()

    service._publish_factory_event = blocked_publish  # type: ignore[method-assign]
    task = asyncio.create_task(service.execute_stage(run.id, "docs_generation"))
    await asyncio.wait_for(entered.wait(), timeout=3)
    task.cancel()
    release.set()
    result = await task

    events = await service.store.get_authoritative_events(run.id)
    assert result.status == "success"
    assert events[-1]["type"] == "factory_stage_persistence_committed"
    assert "factory_run_quarantined" not in {event["type"] for event in events}
    assert not _claim_is_preserved(service, run.id)


@pytest.mark.asyncio
async def test_all_mutating_entrypoints_reject_unmatched_stage_event_before_early_return(tmp_path: Path) -> None:
    service, run = await _running_service(tmp_path)
    result = StageResult(
        stage="docs_generation",
        status="success",
        output="pending",
        completed_at="2026-07-19T01:02:03Z",
    )
    current = await service.get_run(run.id)
    assert current is not None
    current.updated_at = result.completed_at
    checkpoint_ref = service.store.checkpoint_ref(current)
    intent = build_stage_persistence_intent(
        factory_run_id=run.id,
        stage=result.stage,
        stage_result=result.to_dict(),
        checkpoint_ref=checkpoint_ref,
    )
    await service._append_event(
        run.id,
        {
            "type": "stage_completed",
            "stage": result.stage,
            "result": result.to_dict(),
            "persistence_intent": intent.to_record(),
        },
        publish=False,
    )

    operations: list[Callable[[], Awaitable[object]]] = [
        lambda: service.execute_stage(run.id, "docs_generation"),
        lambda: service.start_run(run.id),
        lambda: service.recover_run(run.id),
        lambda: service.retry_run_from_stage(run.id),
        lambda: service.execute_pause(run.id),
        lambda: service.execute_resume(run.id),
        lambda: service.update_run_metadata(run.id, {"x": 1}),
        lambda: service.cancel_run(run.id),
        lambda: service.complete_run(run.id),
        lambda: service.settle_terminal_run(run.id),
        lambda: service.recover_stale_workspace_owner(
            run.id,
            expected_fencing_token=1,
            reason="test",
        ),
    ]
    for operation in operations:
        with pytest.raises(FactoryStagePersistenceError) as captured:
            await operation()
        assert captured.value.code == "factory_stage_persistence_quarantined"


@pytest.mark.asyncio
async def test_current_pointer_tamper_blocks_next_mutation(tmp_path: Path) -> None:
    service, run = await _running_service(tmp_path)
    await service.execute_stage(run.id, "docs_generation")
    current = await service.get_run(run.id)
    assert current is not None
    current.metadata["last_factory_stage_commit"]["checkpoint_ref"] = (
        f"runtime/{run.id}/checkpoints/running_2000-01-01T00_00_00Z.json"
    )
    await service.store.save_run(current)

    with pytest.raises(FactoryStagePersistenceError) as captured:
        await service.start_run(run.id)
    assert captured.value.code == "current_pointer_mismatch"


@pytest.mark.asyncio
async def test_find_last_successful_stage_revalidates_immutable_checkpoint_hash(tmp_path: Path) -> None:
    service, run = await _running_service(tmp_path)
    await service.execute_stage(run.id, "docs_generation")
    events = await service.store.get_authoritative_events(run.id)
    marker = events[-1]
    checkpoint_path = service.store.base_dir / str(marker["checkpoint_ref"]).removeprefix("runtime/")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["metadata"]["tampered"] = True
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    with pytest.raises(FactoryStagePersistenceError) as captured:
        await service._find_last_successful_stage(run.id)
    assert captured.value.code == "factory_stage_checkpoint_hash_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("consumer", "hash_field", "expected_code"),
    [
        ("guard", "run_snapshot_canonical_sha256", "factory_stage_run_snapshot_hash_mismatch"),
        ("history", "run_snapshot_canonical_sha256", "factory_stage_run_snapshot_hash_mismatch"),
        ("guard", "checkpoint_canonical_sha256", "factory_stage_checkpoint_hash_mismatch"),
        ("history", "checkpoint_canonical_sha256", "factory_stage_checkpoint_hash_mismatch"),
    ],
)
async def test_guard_and_history_recompute_both_marker_hash_domains(
    tmp_path: Path,
    consumer: str,
    hash_field: str,
    expected_code: str,
) -> None:
    service, run = await _running_service(tmp_path)
    await service.execute_stage(run.id, "docs_generation")
    events = await service.store.get_authoritative_events(run.id)
    forged_events = copy.deepcopy(events)
    marker = next(event for event in forged_events if event.get("type") == "factory_stage_persistence_committed")
    marker[hash_field] = "f" * 64

    async def forged_authoritative_events(_run_id: str) -> list[dict[str, Any]]:
        return forged_events

    service.store.get_authoritative_events = forged_authoritative_events  # type: ignore[method-assign]
    with pytest.raises(FactoryStagePersistenceError) as captured:
        if consumer == "guard":
            await service.assert_mutation_allowed(run.id)
        else:
            await service._find_last_successful_stage(run.id)
    assert captured.value.code == expected_code


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["guard", "history"])
async def test_guard_and_history_reject_valid_shaped_but_noncanonical_checkpoint_ref(
    tmp_path: Path,
    consumer: str,
) -> None:
    service, run = await _running_service(tmp_path)
    await service.execute_stage(run.id, "docs_generation")
    persisted_snapshot = await service.store.read_strict_run_snapshot(run.id)
    events = await service.store.get_authoritative_events(run.id)
    forged_events = copy.deepcopy(events)
    stage_event = next(event for event in forged_events if event.get("type") == "stage_completed")
    marker = next(event for event in forged_events if event.get("type") == "factory_stage_persistence_committed")
    original_ref = str(marker["checkpoint_ref"])
    checkpoint = await service.store.read_strict_checkpoint_snapshot(run.id, original_ref)
    forged_ref = f"runtime/{run.id}/checkpoints/running_2000-01-01T00_00_00Z.json"
    old_intent = FactoryStagePersistenceIntentV1.from_record(stage_event["persistence_intent"])
    forged_intent = FactoryStagePersistenceIntentV1.create(
        factory_run_id=run.id,
        stage=str(stage_event["stage"]),
        stage_result_canonical_sha256=old_intent.stage_result_canonical_sha256,
        checkpoint_ref=forged_ref,
    )
    stage_event["persistence_intent"] = forged_intent.to_record()
    marker["checkpoint_ref"] = forged_ref
    marker["persistence_intent_sha256"] = forged_intent.persistence_intent_sha256
    forged_snapshot = copy.deepcopy(persisted_snapshot)
    forged_snapshot["metadata"]["last_factory_stage_commit"]["checkpoint_ref"] = forged_ref
    forged_snapshot["metadata"]["last_factory_stage_commit"]["persistence_intent_sha256"] = (
        forged_intent.persistence_intent_sha256
    )

    async def forged_authoritative_events(_run_id: str) -> list[dict[str, Any]]:
        return forged_events

    async def forged_run_snapshot(_run_id: str) -> dict[str, Any]:
        return forged_snapshot

    async def original_checkpoint(_run_id: str, _ref: str) -> dict[str, Any]:
        return checkpoint

    service.store.get_authoritative_events = forged_authoritative_events  # type: ignore[method-assign]
    service.store.read_strict_run_snapshot = forged_run_snapshot  # type: ignore[method-assign]
    service.store.read_strict_checkpoint_snapshot = original_checkpoint  # type: ignore[method-assign]
    with pytest.raises(FactoryStagePersistenceError) as captured:
        if consumer == "guard":
            await service.assert_mutation_allowed(run.id)
        else:
            await service._find_last_successful_stage(run.id)
    assert captured.value.code == "factory_stage_checkpoint_ref_mismatch"


@pytest.mark.asyncio
async def test_bound_artifact_snapshot_is_reread_immediately_before_stage_append(tmp_path: Path) -> None:
    service: FactoryRunService

    def binding_with_post_bind_tamper(run_id: str, _result: StageResult) -> FactoryStageArtifactBindingsV1:
        raw = b'{"tasks":[{"id":"TASK-1"}]}'
        raw_hash = hashlib.sha256(raw).hexdigest()
        snapshot = service.store.persist_stage_artifact_snapshot(run_id, raw_hash, raw)
        item = PMContractArtifactBindingV1(
            kind="pm_contract",
            logical_source_path="tasks/plan.json",
            immutable_snapshot_ref=snapshot.logical_ref,
            document_schema_version="pm.plan_artifact.v1",
            utf8_byte_count=len(raw),
            task_count=1,
            raw_sha256=raw_hash,
            canonical_json_sha256="a" * 64,
            task_id_vector_sha256="b" * 64,
            target_files_projection_sha256="c" * 64,
        )
        binding = FactoryStageArtifactBindingsV1.create(
            factory_run_id=run_id,
            stage="pm_planning",
            items=(item,),
        )

        def fail_strict_reread(
            _run_id: str,
            _logical_ref: str,
            _raw_sha256: str,
            _byte_count: int,
        ) -> None:
            raise RuntimeError("immutable snapshot drifted after binding")

        service.store.read_stage_artifact_snapshot = fail_strict_reread  # type: ignore[method-assign]
        return binding

    service = FactoryRunService(
        tmp_path,
        executor=_SuccessExecutor(),
        stage_artifact_binding_builder=binding_with_post_bind_tamper,
    )
    run = await service.create_run(FactoryConfig(name="binding-reread", stages=["pm_planning"]))
    await service.start_run(run.id)

    with pytest.raises(FactoryStagePersistenceError) as captured:
        await service.execute_stage(run.id, "pm_planning")
    assert captured.value.code == "factory_stage_artifact_snapshot_reread_failed"
    events = await service.store.get_authoritative_events(run.id)
    assert "stage_completed" not in {event["type"] for event in events}
    assert "factory_stage_persistence_committed" not in {event["type"] for event in events}


@pytest.mark.asyncio
async def test_automatic_router_mutation_matrix_executes_real_service_owned_writes(tmp_path: Path) -> None:
    expected = {
        "summary_projection": ("store.save_run",),
        "quality_rework": (
            "store.save_run",
            "_append_event",
            "reconcile_stage_execution_for_reentry",
        ),
        "quality_rework_reentry": ("reconcile_stage_execution_for_reentry",),
        "stage_sequence": ("execute_stage",),
        "run_configuration": ("store.save_run",),
        "delivery_loop_projection": ("store.save_run", "_append_event"),
        "success_terminalization": ("_persist_run_summary", "complete_run"),
        "failure_terminalization": (
            "reconcile_stage_execution_for_reentry",
            "store.save_run",
            "_persist_run_summary",
            "_append_event",
            "complete_run",
        ),
        "factory_failure_terminalization": ("reconcile_stage_execution_for_reentry",),
    }
    service, run = await _running_service(tmp_path)
    assert expected == service.automatic_router_mutation_guard_matrix()
    direct_operations = {
        "summary_projection": None,
        "quality_rework": "quality_rework_requested",
        "run_configuration": None,
        "delivery_loop_projection": "delivery_loop_cycle",
        "failure_terminalization": "error",
    }
    for index, (operation, event_type) in enumerate(direct_operations.items(), start=1):
        metadata_key = f"router_atomic_{index}"
        event = {"type": event_type, "operation": operation} if event_type is not None else None
        updated = await service.apply_automatic_router_mutation(
            run.id,
            operation=operation,
            mutation=lambda current, key=metadata_key: current.metadata.__setitem__(key, "persisted"),
            event=event,
        )
        persisted = await service.get_run(run.id)
        assert updated.metadata[metadata_key] == "persisted"
        assert persisted is not None and persisted.metadata[metadata_key] == "persisted"
    events = await service.store.get_authoritative_events(run.id)
    assert {"quality_rework_requested", "delivery_loop_cycle", "error"}.issubset(
        {str(event.get("type")) for event in events}
    )

    for operation in set(expected).difference(direct_operations):
        with pytest.raises(RuntimeError, match="not a direct persistence family"):
            await service.apply_automatic_router_mutation(
                run.id,
                operation=operation,
                mutation=lambda _current: None,
            )
    with pytest.raises(RuntimeError, match="Unknown automatic Factory router mutation group"):
        await service.apply_automatic_router_mutation(
            run.id,
            operation="unreviewed_mutation",
            mutation=lambda _current: None,
        )


@pytest.mark.asyncio
async def test_restarted_router_projection_cannot_bypass_physical_replay(tmp_path: Path) -> None:
    owner, run = await _running_service(tmp_path)
    before = await owner.get_run(run.id)
    before_events = await owner.store.get_authoritative_events(run.id)
    assert before is not None

    restarted = FactoryRunService(tmp_path, executor=_SuccessExecutor())
    with pytest.raises(
        FactoryPhysicalAttemptControlError,
        match="factory_physical_attempt_replay_required",
    ):
        await restarted.apply_automatic_router_mutation(
            run.id,
            operation="summary_projection",
            mutation=lambda current: current.metadata.__setitem__("bypassed", True),
        )

    after = await restarted.get_run(run.id)
    after_events = await restarted.store.get_authoritative_events(run.id)
    assert after is not None
    assert after.to_dict() == before.to_dict()
    assert after_events == before_events


@pytest.mark.asyncio
async def test_atomic_router_mutation_serializes_cancel_and_preserves_commit_pointer(tmp_path: Path) -> None:
    service, run = await _running_service(tmp_path)
    await service.execute_stage(run.id, "docs_generation")
    committed = await service.get_run(run.id)
    assert committed is not None
    pointer = copy.deepcopy(committed.metadata["last_factory_stage_commit"])
    entered = asyncio.Event()
    release = asyncio.Event()
    original_save = service.store.save_run

    async def blocked_atomic_save(candidate: FactoryRun) -> str:
        if candidate.metadata.get("router_atomic_race") == "kept" and candidate.status != FactoryRunStatus.CANCELLED:
            entered.set()
            await release.wait()
        return await original_save(candidate)

    service.store.save_run = blocked_atomic_save  # type: ignore[method-assign]
    mutation_task = asyncio.create_task(
        service.apply_automatic_router_mutation(
            run.id,
            operation="run_configuration",
            mutation=lambda current: current.metadata.__setitem__("router_atomic_race", "kept"),
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=3)
    cancel_task = asyncio.create_task(service.cancel_run(run.id, reason="race"))
    await asyncio.sleep(0)
    assert not cancel_task.done()
    release.set()
    await mutation_task
    await cancel_task

    final = await service.get_run(run.id)
    assert final is not None
    assert final.status == FactoryRunStatus.CANCELLED
    assert final.metadata["router_atomic_race"] == "kept"
    assert final.metadata["last_factory_stage_commit"] == pointer


def test_marker_append_holds_arbiter_permit_across_pre_durable_seam() -> None:
    arbitration = _FactoryStageCommitArbitration()
    permit_entered = threading.Event()
    release_append = threading.Event()
    cancellation_linearized = threading.Event()
    order: list[str] = []

    def append_under_permit() -> None:
        with arbitration.commit_permit():
            permit_entered.set()
            assert release_append.wait(timeout=3)
            order.append("durable")

    def cancel() -> None:
        arbitration.mark_cancelled()
        order.append("cancelled")
        cancellation_linearized.set()

    append_thread = threading.Thread(target=append_under_permit)
    append_thread.start()
    assert permit_entered.wait(timeout=3)
    cancel_thread = threading.Thread(target=cancel)
    cancel_thread.start()
    assert not cancellation_linearized.wait(timeout=0.05)
    release_append.set()
    append_thread.join(timeout=3)
    cancel_thread.join(timeout=3)
    assert order == ["durable", "cancelled"]


@pytest.mark.asyncio
async def test_role_artifact_binding_failure_persists_only_explicit_failed_result(tmp_path: Path) -> None:
    def fail_binding(_run_id: str, _result: StageResult) -> None:
        raise FactoryStageArtifactBindingError("binding_invalid", "source artifact drifted")

    service = FactoryRunService(
        tmp_path,
        executor=_SuccessExecutor(),
        stage_artifact_binding_builder=fail_binding,
    )
    run = await service.create_run(FactoryConfig(name="binding-failure", stages=["pm_planning"]))
    await service.start_run(run.id)

    result = await service.execute_stage(run.id, "pm_planning")

    events = await service.store.get_authoritative_events(run.id)
    stage_event = events[-2]
    assert result.status == "failed"
    assert result.metadata["error_code"] == "factory_stage_artifact_binding_failed"
    assert stage_event["type"] == "stage_completed"
    assert stage_event["result"]["status"] == "failed"
    assert "stage_artifact_bindings" not in stage_event
    assert events[-1]["type"] == "factory_stage_persistence_committed"
    assert _claim_is_preserved(service, run.id)
