"""Focused tests for pure ProjectOutcomeV1 reducer in runtime.projection."""

from __future__ import annotations

import pytest
from polaris.cells.runtime.projection.internal.project_outcome import (
    reduce_project_outcome,
)
from polaris.cells.runtime.projection.public.contracts import (
    ChainAxisV1,
    DeliveryAxisV1,
    ProjectOutcomeEvidenceRefsV1,
    ProjectOutcomeQueryV1,
    ProjectOutcomeV1,
    ProjectOutcomeValidationV1Error,
    QaAxisV1,
    RecommendedDispositionV1,
    RunLedgerAxisV1,
    TaskBoundaryAxisV1,
    TaskRuntimeAxisV1,
)
from polaris.cells.runtime.projection.public.service import query_project_outcome


def _full_evidence_refs(**overrides: object) -> ProjectOutcomeEvidenceRefsV1:
    payload: dict[str, object] = {
        "delivery": ("delivery:artifact-hash-1",),
        "chain": ("chain:stage-complete-1",),
        "qa": ("qa:verdict-1",),
        "task_boundary": ("boundary:task-1",),
        "task_runtime": ("task_runtime:converged-1",),
        "run_ledger": ("ledger:receipt-1",),
    }
    payload.update(overrides)
    return ProjectOutcomeEvidenceRefsV1(**payload)  # type: ignore[arg-type]


def _fully_verified_query(**overrides: object) -> ProjectOutcomeQueryV1:
    payload: dict[str, object] = {
        "run_id": "run-verified-001",
        "delivery": DeliveryAxisV1.VERIFIED,
        "chain": ChainAxisV1.COMPLETED,
        "qa": QaAxisV1.PASSED,
        "task_boundary": TaskBoundaryAxisV1.PASSED,
        "task_runtime": TaskRuntimeAxisV1.CONVERGED,
        "run_ledger": RunLedgerAxisV1.CLOSED,
        "missing_required_modalities": (),
        "failed_required_modalities": (),
        "evidence_refs": _full_evidence_refs(),
        "reasons": ("all_axes_pass",),
        "task_count": 3,
        "completed_task_count": 3,
    }
    payload.update(overrides)
    return ProjectOutcomeQueryV1(**payload)  # type: ignore[arg-type]


def test_fully_green_claim_is_unbound_completion_candidate() -> None:
    result = query_project_outcome(_fully_verified_query())

    assert isinstance(result, ProjectOutcomeV1)
    assert result.completion_candidate is True
    assert result.authority_bound is False
    assert result.completed_verified is False
    assert result.delivery is DeliveryAxisV1.VERIFIED
    assert result.chain is ChainAxisV1.COMPLETED
    assert result.qa is QaAxisV1.PASSED
    assert result.task_boundary is TaskBoundaryAxisV1.PASSED
    assert result.task_runtime is TaskRuntimeAxisV1.CONVERGED
    assert result.run_ledger is RunLedgerAxisV1.CLOSED
    assert result.missing_required_modalities == ()
    assert result.failed_required_modalities == ()
    assert result.recommended_disposition is RecommendedDispositionV1.AWAIT_AUTHORITY_BINDING
    assert result.blocking_axes == ()
    assert result.task_count == 3
    assert result.completed_task_count == 3
    assert result.evidence_refs.delivery == ("delivery:artifact-hash-1",)
    assert result.evidence_refs.run_ledger == ("ledger:receipt-1",)


def test_delivery_verified_chain_incomplete_preserves_delivery() -> None:
    result = query_project_outcome(
        _fully_verified_query(
            chain=ChainAxisV1.INCOMPLETE,
            reasons=("chain_still_open",),
        )
    )

    assert result.completed_verified is False
    assert result.delivery is DeliveryAxisV1.VERIFIED
    assert result.chain is ChainAxisV1.INCOMPLETE
    assert "chain" in result.blocking_axes
    assert result.recommended_disposition is not RecommendedDispositionV1.COMPLETE


def test_missing_required_modalities_distinct_from_failed() -> None:
    missing = query_project_outcome(
        _fully_verified_query(
            missing_required_modalities=("command", "browser"),
            failed_required_modalities=(),
        )
    )
    failed = query_project_outcome(
        _fully_verified_query(
            missing_required_modalities=(),
            failed_required_modalities=("command",),
        )
    )

    assert missing.completed_verified is False
    assert failed.completed_verified is False
    assert missing.missing_required_modalities == ("browser", "command")
    assert missing.failed_required_modalities == ()
    assert failed.missing_required_modalities == ()
    assert failed.failed_required_modalities == ("command",)
    assert missing.recommended_disposition is RecommendedDispositionV1.REVALIDATE
    assert failed.recommended_disposition is RecommendedDispositionV1.REPAIR
    assert "missing_required_modalities" in missing.blocking_axes
    assert "failed_required_modalities" in failed.blocking_axes


@pytest.mark.parametrize(
    "qa",
    [QaAxisV1.NOT_RUN, QaAxisV1.PENDING, QaAxisV1.FAILED],
)
def test_qa_not_passed_cannot_complete(qa: QaAxisV1) -> None:
    result = query_project_outcome(_fully_verified_query(qa=qa))

    assert result.completed_verified is False
    assert result.qa is qa
    assert "qa" in result.blocking_axes


@pytest.mark.parametrize(
    "boundary",
    [TaskBoundaryAxisV1.UNKNOWN, TaskBoundaryAxisV1.FAILED],
)
def test_task_boundary_unknown_or_failed_cannot_complete(
    boundary: TaskBoundaryAxisV1,
) -> None:
    result = query_project_outcome(_fully_verified_query(task_boundary=boundary))

    assert result.completed_verified is False
    assert result.task_boundary is boundary
    assert "task_boundary" in result.blocking_axes


def test_task_runtime_not_converged_cannot_complete() -> None:
    result = query_project_outcome(_fully_verified_query(task_runtime=TaskRuntimeAxisV1.NOT_CONVERGED))

    assert result.completed_verified is False
    assert result.task_runtime is TaskRuntimeAxisV1.NOT_CONVERGED
    assert "task_runtime" in result.blocking_axes


def test_run_ledger_not_closed_cannot_complete() -> None:
    result = query_project_outcome(_fully_verified_query(run_ledger=RunLedgerAxisV1.NOT_CLOSED))

    assert result.completed_verified is False
    assert result.run_ledger is RunLedgerAxisV1.NOT_CLOSED
    assert "run_ledger" in result.blocking_axes


def test_control_plane_failure_never_erases_verified_delivery() -> None:
    result = query_project_outcome(
        _fully_verified_query(
            chain=ChainAxisV1.CONTROL_PLANE_FAILED,
            reasons=("control_plane_fault",),
        )
    )

    assert result.completed_verified is False
    assert result.delivery is DeliveryAxisV1.VERIFIED
    assert result.chain is ChainAxisV1.CONTROL_PLANE_FAILED
    assert "chain" in result.blocking_axes
    assert result.recommended_disposition is RecommendedDispositionV1.ESCALATE_CONTROL_PLANE


def test_result_deterministic_and_input_order_normalized() -> None:
    left = query_project_outcome(
        _fully_verified_query(
            missing_required_modalities=("command", "browser", "tool_receipt"),
            failed_required_modalities=("domain", "api_contract"),
            reasons=("second", "first"),
            evidence_refs=_full_evidence_refs(
                delivery=("delivery:z", "delivery:a", "delivery:z"),
                chain=("chain:b", "chain:a"),
            ),
        )
    )
    right = reduce_project_outcome(
        ProjectOutcomeQueryV1(
            run_id="run-verified-001",
            delivery=DeliveryAxisV1.VERIFIED,
            chain=ChainAxisV1.COMPLETED,
            qa=QaAxisV1.PASSED,
            task_boundary=TaskBoundaryAxisV1.PASSED,
            task_runtime=TaskRuntimeAxisV1.CONVERGED,
            run_ledger=RunLedgerAxisV1.CLOSED,
            missing_required_modalities=("tool_receipt", "browser", "command"),
            failed_required_modalities=("api_contract", "domain"),
            evidence_refs=ProjectOutcomeEvidenceRefsV1(
                delivery=("delivery:a", "delivery:z"),
                chain=("chain:a", "chain:b"),
                qa=("qa:verdict-1",),
                task_boundary=("boundary:task-1",),
                task_runtime=("task_runtime:converged-1",),
                run_ledger=("ledger:receipt-1",),
            ),
            reasons=("first", "second"),
            task_count=3,
            completed_task_count=3,
        )
    )

    assert left == right
    assert left.missing_required_modalities == ("browser", "command", "tool_receipt")
    assert left.failed_required_modalities == ("api_contract", "domain")
    assert left.evidence_refs.delivery == ("delivery:a", "delivery:z")
    assert left.evidence_refs.chain == ("chain:a", "chain:b")
    assert left.reasons == ("first", "second")
    assert left.completed_verified is False


def test_present_unverified_delivery_cannot_complete() -> None:
    result = query_project_outcome(_fully_verified_query(delivery=DeliveryAxisV1.PRESENT_UNVERIFIED))

    assert result.completed_verified is False
    assert result.delivery is DeliveryAxisV1.PRESENT_UNVERIFIED
    assert "delivery" in result.blocking_axes
    assert result.recommended_disposition is RecommendedDispositionV1.REVALIDATE


@pytest.mark.parametrize(
    "axis_name",
    [
        "delivery",
        "chain",
        "qa",
        "task_boundary",
        "task_runtime",
        "run_ledger",
    ],
)
def test_missing_per_axis_evidence_ref_blocks_completion(axis_name: str) -> None:
    evidence = _full_evidence_refs(**{axis_name: ()})
    result = query_project_outcome(_fully_verified_query(evidence_refs=evidence))

    assert result.completed_verified is False
    assert f"evidence_refs.{axis_name}" in result.blocking_axes


def test_zero_task_count_blocks_completion() -> None:
    result = query_project_outcome(_fully_verified_query(task_count=0, completed_task_count=0))

    assert result.completed_verified is False
    assert "task_count" in result.blocking_axes
    assert result.task_count == 0
    assert result.completed_task_count == 0


def test_incomplete_task_count_blocks_completion() -> None:
    result = query_project_outcome(_fully_verified_query(task_count=4, completed_task_count=2))

    assert result.completed_verified is False
    assert "completed_task_count" in result.blocking_axes
    assert result.task_count == 4
    assert result.completed_task_count == 2


def test_completed_exceeds_total_typed_failure() -> None:
    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        _fully_verified_query(task_count=2, completed_task_count=3)

    assert exc_info.value.error_code == "completed_task_count_exceeds_task_count"


@pytest.mark.parametrize(
    ("field_name", "bad_value", "error_code"),
    [
        ("task_count", True, "invalid_task_count_type"),
        ("task_count", "3", "invalid_task_count_type"),
        ("task_count", 3.0, "invalid_task_count_type"),
        ("completed_task_count", False, "invalid_completed_task_count_type"),
        ("completed_task_count", "2", "invalid_completed_task_count_type"),
        ("completed_task_count", 2.5, "invalid_completed_task_count_type"),
    ],
)
def test_non_int_and_bool_counts_typed_failure(
    field_name: str,
    bad_value: object,
    error_code: str,
) -> None:
    kwargs = {field_name: bad_value}
    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        _fully_verified_query(**kwargs)

    assert exc_info.value.error_code == error_code


def test_modality_overlap_typed_failure() -> None:
    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        _fully_verified_query(
            missing_required_modalities=("command", "browser"),
            failed_required_modalities=("browser", "domain"),
        )

    assert exc_info.value.error_code == "overlapping_required_modalities"


def test_token_dedup_normalization() -> None:
    result = query_project_outcome(
        _fully_verified_query(
            missing_required_modalities=("command", "command", "browser", "browser"),
            reasons=("dup", "dup", "only"),
            evidence_refs=_full_evidence_refs(
                qa=("qa:1", "qa:1", "qa:0"),
            ),
        )
    )

    assert result.missing_required_modalities == ("browser", "command")
    assert result.reasons == ("dup", "only")
    assert result.evidence_refs.qa == ("qa:0", "qa:1")
    assert result.completed_verified is False


def test_raw_string_enum_rejection() -> None:
    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        ProjectOutcomeQueryV1(
            run_id="run-1",
            delivery="verified",  # type: ignore[arg-type]
            chain=ChainAxisV1.COMPLETED,
            qa=QaAxisV1.PASSED,
            task_boundary=TaskBoundaryAxisV1.PASSED,
            task_runtime=TaskRuntimeAxisV1.CONVERGED,
            run_ledger=RunLedgerAxisV1.CLOSED,
            evidence_refs=_full_evidence_refs(),
            task_count=1,
            completed_task_count=1,
        )

    assert exc_info.value.error_code == "invalid_delivery"


@pytest.mark.parametrize(
    ("field_name", "bad_value", "error_code"),
    [
        (
            "missing_required_modalities",
            None,
            "invalid_missing_required_modalities_type",
        ),
        (
            "failed_required_modalities",
            ("command", 7),
            "invalid_failed_required_modalities_item_type",
        ),
        ("reasons", "not-a-sequence-of-tokens", "invalid_reasons_type"),
    ],
)
def test_untyped_token_collections_fail_closed(
    field_name: str,
    bad_value: object,
    error_code: str,
) -> None:
    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        _fully_verified_query(**{field_name: bad_value})

    assert exc_info.value.error_code == error_code


def test_untyped_evidence_ref_item_fails_closed() -> None:
    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        ProjectOutcomeEvidenceRefsV1(delivery=("delivery:ok", 9))  # type: ignore[arg-type]

    assert exc_info.value.error_code == "invalid_evidence_refs_delivery_item_type"


def test_canonical_public_package_import() -> None:
    import polaris.cells.runtime.projection.public as projection_public

    assert projection_public.ProjectOutcomeQueryV1 is ProjectOutcomeQueryV1
    assert projection_public.ProjectOutcomeV1 is ProjectOutcomeV1
    assert projection_public.ProjectOutcomeEvidenceRefsV1 is ProjectOutcomeEvidenceRefsV1
    assert projection_public.ProjectOutcomeValidationV1Error is ProjectOutcomeValidationV1Error
    assert projection_public.DeliveryAxisV1 is DeliveryAxisV1
    assert projection_public.ChainAxisV1 is ChainAxisV1
    assert projection_public.QaAxisV1 is QaAxisV1
    assert projection_public.TaskBoundaryAxisV1 is TaskBoundaryAxisV1
    assert projection_public.TaskRuntimeAxisV1 is TaskRuntimeAxisV1
    assert projection_public.RunLedgerAxisV1 is RunLedgerAxisV1
    assert projection_public.RecommendedDispositionV1 is RecommendedDispositionV1
    assert projection_public.query_project_outcome is query_project_outcome

    result = projection_public.query_project_outcome(_fully_verified_query())
    assert result.completion_candidate is True
    assert result.authority_bound is False
    assert result.completed_verified is False


def test_public_service_rejects_untyped_query() -> None:
    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        query_project_outcome(object())  # type: ignore[arg-type]

    assert exc_info.value.error_code == "invalid_project_outcome_query_type"


def test_non_string_run_id_fails_closed() -> None:
    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        _fully_verified_query(run_id=123)

    assert exc_info.value.error_code == "invalid_run_id_type"


def test_direct_result_cannot_claim_unbound_completed_verified() -> None:
    query = _fully_verified_query()
    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        ProjectOutcomeV1(
            run_id=query.run_id,
            delivery=query.delivery,
            chain=query.chain,
            qa=query.qa,
            task_boundary=query.task_boundary,
            task_runtime=query.task_runtime,
            run_ledger=query.run_ledger,
            missing_required_modalities=query.missing_required_modalities,
            failed_required_modalities=query.failed_required_modalities,
            completion_candidate=True,
            authority_bound=False,
            completed_verified=True,
            recommended_disposition=RecommendedDispositionV1.AWAIT_AUTHORITY_BINDING,
            evidence_refs=query.evidence_refs,
            reasons=query.reasons,
            blocking_axes=(),
            task_count=query.task_count,
            completed_task_count=query.completed_task_count,
        )

    assert exc_info.value.error_code == "unbound_completed_verified"


def test_direct_result_cannot_claim_authority_binding_in_gr0() -> None:
    query = _fully_verified_query()
    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        ProjectOutcomeV1(
            run_id=query.run_id,
            delivery=query.delivery,
            chain=query.chain,
            qa=query.qa,
            task_boundary=query.task_boundary,
            task_runtime=query.task_runtime,
            run_ledger=query.run_ledger,
            missing_required_modalities=query.missing_required_modalities,
            failed_required_modalities=query.failed_required_modalities,
            completion_candidate=True,
            authority_bound=True,
            completed_verified=False,
            recommended_disposition=RecommendedDispositionV1.AWAIT_AUTHORITY_BINDING,
            evidence_refs=query.evidence_refs,
            reasons=query.reasons,
            blocking_axes=(),
            task_count=query.task_count,
            completed_task_count=query.completed_task_count,
        )

    assert exc_info.value.error_code == "unsupported_authority_binding_v1"


def test_direct_result_cannot_hide_reducer_blockers() -> None:
    query = _fully_verified_query(delivery=DeliveryAxisV1.MISSING)
    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        ProjectOutcomeV1(
            run_id=query.run_id,
            delivery=query.delivery,
            chain=query.chain,
            qa=query.qa,
            task_boundary=query.task_boundary,
            task_runtime=query.task_runtime,
            run_ledger=query.run_ledger,
            missing_required_modalities=query.missing_required_modalities,
            failed_required_modalities=query.failed_required_modalities,
            completion_candidate=True,
            authority_bound=False,
            completed_verified=False,
            recommended_disposition=RecommendedDispositionV1.REVALIDATE,
            evidence_refs=query.evidence_refs,
            reasons=query.reasons,
            blocking_axes=(),
            task_count=query.task_count,
            completed_task_count=query.completed_task_count,
        )

    assert exc_info.value.error_code == "inconsistent_blocking_axes"


@pytest.mark.parametrize(
    ("kwargs", "error_code"),
    [
        ({"run_id": ""}, "empty_run_id"),
        ({"run_id": "   "}, "empty_run_id"),
        ({"task_count": -1}, "negative_task_count"),
        ({"completed_task_count": -2, "task_count": 0}, "negative_completed_task_count"),
    ],
)
def test_invalid_query_values_fail_closed_with_typed_errors(
    kwargs: dict[str, object],
    error_code: str,
) -> None:
    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        query_project_outcome(_fully_verified_query(**kwargs))

    assert exc_info.value.error_code == error_code
