from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING, cast

from polaris.application.cognitive_runtime import CognitiveRuntimeService
from polaris.cells.factory.cognitive_runtime.public.contracts import (
    ExportHandoffPackCommandV1,
    GetHandoffPackQueryV1,
    GetRuntimeReceiptQueryV1,
    LeaseEditScopeCommandV1,
    MapDiffToCellsCommandV1,
    PromoteOrRejectCommandV1,
    RecordRollbackLedgerCommandV1,
    RecordRuntimeReceiptCommandV1,
    RehydrateHandoffPackCommandV1,
    RequestProjectionCompileCommandV1,
    ResolveContextCommandV1,
    ValidateChangeSetCommandV1,
)
from polaris.cells.factory.cognitive_runtime.public.service import CognitiveRuntimePublicService
from polaris.infrastructure.cognitive_runtime import CognitiveRuntimeSqliteStore

if TYPE_CHECKING:
    from polaris.cells.roles.session.internal.context_memory_service import (
        RoleSessionContextMemoryService,
    )
    from polaris.cells.roles.session.internal.role_session_service import RoleSessionService
    from polaris.cells.roles.session.public.contracts import (
        IRoleSessionContextMemoryService,
        IRoleSessionService,
    )


class _FakeRoleSessionService:
    def get_context_config_dict(self, session_id: str) -> dict[str, object]:
        return {
            "session_continuity": {
                "summary": "current continuity",
                "source_message_count": 4,
            },
            "state_first_context_os": {
                "run_card": {
                    "current_goal": "stabilize context runtime",
                    "hard_constraints": ["do not duplicate truth", "use sqlite"],
                    "open_loops": ["finish cognitive runtime"],
                }
            },
        }

    def close(self) -> None:
        return None


class _FakeContextMemoryService:
    def get_state_for_session(self, session_id: str, path: str):
        if path == "run_card":
            return {
                "current_goal": "stabilize context runtime",
                "hard_constraints": ["do not duplicate truth", "use sqlite"],
                "open_loops": ["finish cognitive runtime"],
                "recent_decisions": ["prefer state-first continuity"],
            }
        if path == "context_slice_plan":
            return {"included": [{"ref": "run_card", "reason": "active root"}]}
        if path == "decision_log":
            return [
                {
                    "decision_id": "decision-1",
                    "summary": "prefer state-first continuity",
                    "source_turns": ["t41"],
                    "updated_at": "2026-03-27T00:00:00+00:00",
                    "kind": "architecture",
                    "basis_refs": ["receipt-1"],
                }
            ]
        return None

    def search_memory_for_session(self, session_id: str, query: str, **kwargs):
        return [
            {"artifact_id": "art-1", "episode_id": "ep-1"},
            {"artifact_id": "art-2", "episode_id": "ep-2"},
        ]

    def read_episode_for_session(self, session_id: str, episode_id: str):
        return {
            "episode_id": episode_id,
            "source_spans": [f"{episode_id}:t41:t44"],
        }

    def close(self) -> None:
        return None


def _build_store(workspace: str) -> CognitiveRuntimeSqliteStore:
    fd, db_path = tempfile.mkstemp(prefix="cognitive-runtime-", suffix=".sqlite")
    os.close(fd)
    if os.path.exists(db_path):
        os.unlink(db_path)
    return CognitiveRuntimeSqliteStore(workspace, db_path=db_path)


def test_public_service_resolves_context_scope_and_validation(monkeypatch) -> None:
    workspace = tempfile.mkdtemp(prefix="cognitive-runtime-")

    def _fake_get_context(*args, **kwargs):
        class _Pack:
            total_tokens = 321
            items = [type("Item", (), {"id": "ctx-1"})()]

        return {
            "anthropomorphic_context": "resolved prompt",
            "context_pack": _Pack(),
            "context_os_summary": {
                "current_goal": "stabilize context runtime",
                "pressure_level": "soft",
            },
        }

    monkeypatch.setattr(
        "polaris.application.cognitive_runtime.service.get_anthropomorphic_context_v2",
        _fake_get_context,
    )

    runtime = CognitiveRuntimeService(
        session_service=cast("IRoleSessionService | None", _FakeRoleSessionService()),
        context_memory_service=cast("IRoleSessionContextMemoryService | None", _FakeContextMemoryService()),
        store=_build_store(workspace),
    )
    service = CognitiveRuntimePublicService(runtime=runtime)

    context_result = service.resolve_context(
        ResolveContextCommandV1(
            workspace=workspace,
            role="director",
            query="summarize current code",
            step=2,
            run_id="run-1",
            mode="chat",
            session_id="session-1",
        )
    )
    assert context_result.ok is True
    assert context_result.snapshot is not None
    assert context_result.snapshot.rendered_prompt == "resolved prompt"

    lease_result = service.lease_edit_scope(
        LeaseEditScopeCommandV1(
            workspace=workspace,
            requested_by="director",
            scope_paths=("polaris/kernelone/context",),
            session_id="session-1",
            reason="stabilize context runtime",
        )
    )
    assert lease_result.ok is True
    assert lease_result.lease is not None
    assert lease_result.lease.current_goal == "stabilize context runtime"

    validation_result = service.validate_change_set(
        ValidateChangeSetCommandV1(
            workspace=workspace,
            changed_files=("polaris/kernelone/context/runtime.py",),
            allowed_scope_paths=("polaris/kernelone/context",),
            evidence_refs=("receipt-1",),
        )
    )
    assert validation_result.validation is not None
    assert validation_result.ok is True
    assert str(validation_result.validation.validation_id or "").strip()

    invalid_result = service.validate_change_set(
        ValidateChangeSetCommandV1(
            workspace=workspace,
            changed_files=("polaris/cells/roles/kernel/turn_engine.py",),
            allowed_scope_paths=("polaris/kernelone/context",),
        )
    )
    assert invalid_result.validation is not None
    assert invalid_result.ok is False

    service.close()


def test_public_service_round_trips_receipts_and_handoffs() -> None:
    workspace = tempfile.mkdtemp(prefix="cognitive-runtime-")
    runtime = CognitiveRuntimeService(
        session_service=cast("IRoleSessionService | None", _FakeRoleSessionService()),
        context_memory_service=cast("IRoleSessionContextMemoryService | None", _FakeContextMemoryService()),
        store=_build_store(workspace),
    )
    service = CognitiveRuntimePublicService(runtime=runtime)

    receipt_result = service.record_runtime_receipt(
        RecordRuntimeReceiptCommandV1(
            workspace=workspace,
            receipt_type="scope_lease",
            session_id="session-1",
            run_id="run-1",
            payload={"scope_paths": ["polaris/kernelone/context"]},
            trace_refs=("trace-1",),
            turn_envelope={
                "turn_id": "turn-1",
                "projection_version": "state_first_context_os.v1",
                "lease_id": "lease-1",
                "role": "director",
                "task_id": "task-1",
            },
        )
    )
    assert receipt_result.ok is True
    assert receipt_result.receipt is not None
    assert receipt_result.receipt.turn_envelope is not None
    assert receipt_result.receipt.turn_envelope.turn_id == "turn-1"
    assert receipt_result.receipt.turn_envelope.receipt_ids == (receipt_result.receipt.receipt_id,)

    fetched_receipt = service.get_runtime_receipt(
        GetRuntimeReceiptQueryV1(
            workspace=workspace,
            receipt_id=receipt_result.receipt.receipt_id,
        )
    )
    assert fetched_receipt.ok is True
    assert fetched_receipt.receipt is not None
    assert fetched_receipt.receipt.payload["scope_paths"] == ["polaris/kernelone/context"]
    assert fetched_receipt.receipt.turn_envelope is not None
    assert fetched_receipt.receipt.turn_envelope.lease_id == "lease-1"

    handoff_result = service.export_handoff_pack(
        ExportHandoffPackCommandV1(
            workspace=workspace,
            session_id="session-1",
            run_id="run-1",
            reason="sleep handoff",
        )
    )
    assert handoff_result.ok is True
    assert handoff_result.handoff is not None
    assert handoff_result.handoff.current_goal == "stabilize context runtime"
    assert handoff_result.handoff.run_card["current_goal"] == "stabilize context runtime"
    assert handoff_result.handoff.decision_log[0]["decision_id"] == "decision-1"
    assert "ep-1:t41:t44" in handoff_result.handoff.source_spans
    assert receipt_result.receipt.receipt_id in handoff_result.handoff.receipt_refs
    assert handoff_result.handoff.turn_envelope is not None
    assert handoff_result.handoff.turn_envelope.turn_id == "turn-1"
    assert receipt_result.receipt.receipt_id in handoff_result.handoff.turn_envelope.receipt_ids

    fetched_handoff = service.get_handoff_pack(
        GetHandoffPackQueryV1(
            workspace=workspace,
            handoff_id=handoff_result.handoff.handoff_id,
        )
    )
    assert fetched_handoff.ok is True
    assert fetched_handoff.handoff is not None
    assert fetched_handoff.handoff.session_id == "session-1"
    assert fetched_handoff.handoff.turn_envelope is not None
    assert fetched_handoff.handoff.turn_envelope.turn_id == "turn-1"

    service.close()


def test_public_service_rehydrates_handoff_into_state_first_context() -> None:
    workspace = tempfile.mkdtemp(prefix="cognitive-runtime-rehydrate-")
    runtime = CognitiveRuntimeService(
        session_service=cast("IRoleSessionService | None", _FakeRoleSessionService()),
        context_memory_service=cast("IRoleSessionContextMemoryService | None", _FakeContextMemoryService()),
        store=_build_store(workspace),
    )
    service = CognitiveRuntimePublicService(runtime=runtime)

    handoff_result = service.export_handoff_pack(
        ExportHandoffPackCommandV1(
            workspace=workspace,
            session_id="session-1",
            run_id="run-1",
            reason="handoff to writer",
        )
    )
    assert handoff_result.ok is True
    assert handoff_result.handoff is not None

    rehydration_result = service.rehydrate_handoff_pack(
        RehydrateHandoffPackCommandV1(
            workspace=workspace,
            handoff_id=handoff_result.handoff.handoff_id,
            target_role="writer",
            target_session_id="session-writer-1",
        )
    )
    assert rehydration_result.ok is True
    assert rehydration_result.rehydration is not None
    assert rehydration_result.rehydration.target_role == "writer"
    assert rehydration_result.rehydration.run_card["current_goal"] == "stabilize context runtime"
    assert rehydration_result.rehydration.decision_log[0]["decision_id"] == "decision-1"
    assert "ep-1:t41:t44" in rehydration_result.rehydration.source_spans
    state_first = rehydration_result.rehydration.context_override["state_first_context_os"]
    assert state_first["mode"] == "state_first_context_os.handoff_rehydrate"
    assert state_first["run_card"]["current_goal"] == "stabilize context runtime"
    assert state_first["decision_log"][0]["summary"] == "prefer state-first continuity"
    assert rehydration_result.rehydration.metadata_patch["handoff_rehydrated"] is True

    service.close()


def test_public_service_isolates_sqlite_state_per_workspace(monkeypatch) -> None:
    workspace_a = tempfile.mkdtemp(prefix="cognitive-runtime-a-")
    workspace_b = tempfile.mkdtemp(prefix="cognitive-runtime-b-")
    store_cache: dict[str, CognitiveRuntimeSqliteStore] = {}

    def _store_factory(workspace: str) -> CognitiveRuntimeSqliteStore:
        key = os.path.abspath(workspace)
        store = store_cache.get(key)
        if store is None:
            store = _build_store(workspace)
            store_cache[key] = store
        return store

    monkeypatch.setattr(
        "polaris.application.cognitive_runtime.service.CognitiveRuntimeSqliteStore",
        _store_factory,
    )
    runtime = CognitiveRuntimeService(
        session_service=cast("RoleSessionService | None", _FakeRoleSessionService()),
        context_memory_service=cast("RoleSessionContextMemoryService | None", _FakeContextMemoryService()),
    )
    service = CognitiveRuntimePublicService(runtime=runtime)

    receipt_a = service.record_runtime_receipt(
        RecordRuntimeReceiptCommandV1(
            workspace=workspace_a,
            receipt_type="scope_lease",
            session_id="session-a",
            payload={"workspace": "a"},
        )
    )
    receipt_b = service.record_runtime_receipt(
        RecordRuntimeReceiptCommandV1(
            workspace=workspace_b,
            receipt_type="scope_lease",
            session_id="session-b",
            payload={"workspace": "b"},
        )
    )

    assert receipt_a.ok is True
    assert receipt_b.ok is True
    assert receipt_a.receipt is not None
    assert receipt_b.receipt is not None

    fetched_a = service.get_runtime_receipt(
        GetRuntimeReceiptQueryV1(
            workspace=workspace_a,
            receipt_id=receipt_a.receipt.receipt_id,
        )
    )
    fetched_b = service.get_runtime_receipt(
        GetRuntimeReceiptQueryV1(
            workspace=workspace_b,
            receipt_id=receipt_b.receipt.receipt_id,
        )
    )
    missing_cross_fetch = service.get_runtime_receipt(
        GetRuntimeReceiptQueryV1(
            workspace=workspace_b,
            receipt_id=receipt_a.receipt.receipt_id,
        )
    )

    assert fetched_a.ok is True
    assert fetched_a.receipt is not None
    assert fetched_a.receipt.payload["workspace"] == "a"
    assert fetched_b.ok is True
    assert fetched_b.receipt is not None
    assert fetched_b.receipt.payload["workspace"] == "b"
    assert missing_cross_fetch.ok is False
    assert missing_cross_fetch.error_code == "runtime_receipt_not_found"

    service.close()


def test_public_service_phase2_flow() -> None:
    workspace = tempfile.mkdtemp(prefix="cognitive-runtime-phase2-")
    os.makedirs(os.path.join(workspace, "docs", "graph", "catalog"), exist_ok=True)
    graph_catalog = os.path.join(workspace, "docs", "graph", "catalog", "cells.yaml")
    with open(graph_catalog, "w", encoding="utf-8") as fh:
        fh.write(
            "version: 1\ncells:\n  - id: roles.runtime\n    owned_paths:\n      - polaris/cells/roles/runtime/**\n"
        )

    runtime = CognitiveRuntimeService(
        session_service=cast("IRoleSessionService | None", _FakeRoleSessionService()),
        context_memory_service=cast("IRoleSessionContextMemoryService | None", _FakeContextMemoryService()),
        store=_build_store(workspace),
    )
    service = CognitiveRuntimePublicService(runtime=runtime)

    mapping = service.map_diff_to_cells(
        MapDiffToCellsCommandV1(
            workspace=workspace,
            changed_files=("polaris/cells/roles/runtime/public/service.py",),
        )
    )
    assert mapping.ok is True
    assert mapping.mapping is not None
    assert "roles.runtime" in mapping.mapping.matched_cells

    compile_result = service.request_projection_compile(
        RequestProjectionCompileCommandV1(
            workspace=workspace,
            requested_by="director",
            subject_ref="task-1",
            changed_files=("polaris/cells/roles/runtime/public/service.py",),
            mapped_cells=("roles.runtime",),
            session_id="session-1",
            run_id="run-1",
        )
    )
    assert compile_result.ok is True
    assert compile_result.request is not None
    assert compile_result.request.status == "queued"

    decision_result = service.promote_or_reject(
        PromoteOrRejectCommandV1(
            workspace=workspace,
            subject_ref="task-1",
            changed_files=("polaris/cells/roles/runtime/public/service.py",),
            mapped_cells=("roles.runtime",),
            write_gate_allowed=True,
            projection_status="queued",
            projection_request_id=compile_result.request.request_id,
            reasons=("quality_gate_passed",),
        )
    )
    assert decision_result.ok is True
    assert decision_result.decision is not None
    assert decision_result.decision.decision == "promote"

    rollback_result = service.record_rollback_ledger(
        RecordRollbackLedgerCommandV1(
            workspace=workspace,
            subject_ref="task-1",
            reason="manual_rollback_requested",
            decision_id=decision_result.decision.decision_id,
            changed_files=("polaris/cells/roles/runtime/public/service.py",),
        )
    )
    assert rollback_result.ok is True
    assert rollback_result.entry is not None
    assert rollback_result.entry.reason == "manual_rollback_requested"

    service.close()


def test_public_service_rejects_graph_catalog_path_outside_workspace() -> None:
    workspace = tempfile.mkdtemp(prefix="cognitive-runtime-path-")
    runtime = CognitiveRuntimeService(
        session_service=cast("IRoleSessionService | None", _FakeRoleSessionService()),
        context_memory_service=cast("IRoleSessionContextMemoryService | None", _FakeContextMemoryService()),
        store=_build_store(workspace),
    )
    service = CognitiveRuntimePublicService(runtime=runtime)
    result = service.map_diff_to_cells(
        MapDiffToCellsCommandV1(
            workspace=workspace,
            changed_files=("polaris/cells/roles/runtime/public/service.py",),
            graph_catalog_path="../outside.yaml",
        )
    )
    assert result.ok is False
    assert result.error_code == "map_diff_to_cells_failed"
    assert "must stay within workspace" in str(result.error_message or "")
    service.close()


def test_public_service_respects_cognitive_runtime_mode_off(monkeypatch) -> None:
    workspace = tempfile.mkdtemp(prefix="cognitive-runtime-off-")
    monkeypatch.setenv("KERNELONE_COGNITIVE_RUNTIME_MODE", "off")
    runtime = CognitiveRuntimeService(
        session_service=cast("IRoleSessionService | None", _FakeRoleSessionService()),
        context_memory_service=cast("IRoleSessionContextMemoryService | None", _FakeContextMemoryService()),
        store=_build_store(workspace),
    )
    service = CognitiveRuntimePublicService(runtime=runtime)
    result = service.resolve_context(
        ResolveContextCommandV1(
            workspace=workspace,
            role="director",
            query="summarize",
            step=1,
            run_id="run-off-1",
            mode="chat",
            session_id="session-1",
        )
    )
    assert result.ok is False
    assert result.error_code == "cognitive_runtime_disabled"
    receipt = runtime.record_runtime_receipt(
        workspace=workspace,
        receipt_type="scope_lease",
        payload={"source": "seed"},
        session_id="session-1",
    )
    get_result = service.get_runtime_receipt(
        GetRuntimeReceiptQueryV1(
            workspace=workspace,
            receipt_id=receipt.receipt_id,
        )
    )
    assert get_result.ok is True
    assert get_result.receipt is not None
    assert get_result.receipt.payload["source"] == "seed"
    service.close()
