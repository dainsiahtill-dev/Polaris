from __future__ import annotations

import pytest
from polaris.cells.factory.cognitive_runtime.public.contracts import (
    ExportHandoffPackCommandV1,
    LeaseEditScopeCommandV1,
    MapDiffToCellsCommandV1,
    PromoteOrRejectCommandV1,
    RecordRollbackLedgerCommandV1,
    RehydrateHandoffPackCommandV1,
    RequestProjectionCompileCommandV1,
    ResolveContextCommandV1,
    ValidateChangeSetCommandV1,
)


def test_resolve_context_contract_validates_required_fields() -> None:
    with pytest.raises(ValueError):
        ResolveContextCommandV1(
            workspace="",
            role="director",
            query="summarize",
            step=1,
            run_id="run-1",
            mode="chat",
        )


def test_lease_edit_scope_requires_non_empty_scope() -> None:
    with pytest.raises(ValueError):
        LeaseEditScopeCommandV1(
            workspace="C:/workspace",
            requested_by="director",
            scope_paths=(),
        )


def test_validate_change_set_requires_allowed_scope() -> None:
    with pytest.raises(ValueError):
        ValidateChangeSetCommandV1(
            workspace="C:/workspace",
            changed_files=("a.py",),
            allowed_scope_paths=(),
        )


def test_export_handoff_requires_positive_limit() -> None:
    with pytest.raises(ValueError):
        ExportHandoffPackCommandV1(
            workspace="C:/workspace",
            session_id="session-1",
            receipt_limit=0,
        )


def test_rehydrate_handoff_requires_target_role() -> None:
    with pytest.raises(ValueError):
        RehydrateHandoffPackCommandV1(
            workspace="C:/workspace",
            handoff_id="handoff-1",
            target_role="",
        )


def test_map_diff_to_cells_requires_changed_files() -> None:
    with pytest.raises(ValueError):
        MapDiffToCellsCommandV1(
            workspace="C:/workspace",
            changed_files=(),
        )


def test_projection_compile_requires_requested_by() -> None:
    with pytest.raises(ValueError):
        RequestProjectionCompileCommandV1(
            workspace="C:/workspace",
            requested_by="",
            subject_ref="task-1",
            changed_files=("polaris/a.py",),
        )


def test_promote_or_reject_requires_projection_status() -> None:
    with pytest.raises(ValueError):
        PromoteOrRejectCommandV1(
            workspace="C:/workspace",
            subject_ref="task-1",
            changed_files=("polaris/a.py",),
            mapped_cells=("roles.runtime",),
            write_gate_allowed=True,
            projection_status="",
        )


def test_record_rollback_requires_reason() -> None:
    with pytest.raises(ValueError):
        RecordRollbackLedgerCommandV1(
            workspace="C:/workspace",
            subject_ref="task-1",
            reason="",
        )
