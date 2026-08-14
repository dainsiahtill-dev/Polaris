from __future__ import annotations

from polaris.cells.control_plane.run_ledger.public import (
    ReadRunLedgerProjectionQueryV1,
    native_tool_call_count_from_metadata,
    native_tool_call_envelope_refs_from_metadata,
    read_run_ledger_projection,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    batch_write_failure_error_types,
    batch_write_failures_require_llm_replan,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    _append_tool_batch_receipts_to_run_ledger,
    _batch_has_authoritative_success,
    _deo_prepare_upstream_code,
    _effect_receipts_from_batch_receipts,
    _is_deo_abort_error,
    _is_transient_deo_prepare_lock_failure,
    _resolve_tool_batch_execution_identity,
    _seal_deo_abort_tool_lifecycle,
)
from polaris.cells.roles.kernel.internal.transaction.tool_call_audit_refs import tool_invocation_audit_ref
from polaris.cells.roles.kernel.public.turn_contracts import ToolExecutionMode


def _bootstrap_test_fact_stream(tmp_path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            maintenance_reason="tool_batch_executor_metadata_test",
            streams=fact_stream_bootstrap_streams(),
        )
    )


def test_effect_receipts_from_batch_receipts_accepts_top_level_effect_receipts() -> None:
    original = {"operation": "write", "file": "src/generated.py"}

    receipts = _effect_receipts_from_batch_receipts(
        [
            {
                "batch_id": "batch-1",
                "turn_id": "turn-1",
                "effect_receipts": [
                    original,
                    "invalid",
                    None,
                    ["invalid"],
                ],
            }
        ]
    )

    assert receipts == [{"operation": "write", "file": "src/generated.py"}]
    assert receipts[0] is not original


def test_effect_receipts_from_batch_receipts_keeps_direct_and_nested_receipts() -> None:
    result_direct = {"source": "results-direct"}
    result_nested = {"source": "results-nested"}
    raw_direct = {"source": "raw-results-direct"}
    raw_nested = {"source": "raw-results-nested"}

    receipts = _effect_receipts_from_batch_receipts(
        [
            {
                "batch_id": "batch-1",
                "turn_id": "turn-1",
                "results": [
                    {"effect_receipt": result_direct},
                    {"result": {"effect_receipt": result_nested}},
                ],
                "raw_results": [
                    {"effect_receipt": raw_direct},
                    {"result": {"effect_receipt": raw_nested}},
                ],
            }
        ]
    )

    assert receipts == [
        {"source": "results-direct"},
        {"source": "results-nested"},
        {"source": "raw-results-direct"},
        {"source": "raw-results-nested"},
    ]
    assert receipts[0] is not result_direct
    assert receipts[1] is not result_nested
    assert receipts[2] is not raw_direct
    assert receipts[3] is not raw_nested


def test_effect_receipts_from_batch_receipts_filters_invalid_shapes() -> None:
    receipts = _effect_receipts_from_batch_receipts(
        [
            None,
            "invalid",
            {
                "batch_id": "batch-1",
                "turn_id": "turn-1",
                "effect_receipts": {"invalid": "shape"},
                "results": {"invalid": "shape"},
                "raw_results": [
                    None,
                    "invalid",
                    {"effect_receipt": "invalid"},
                    {"result": "invalid"},
                    {"result": {"effect_receipt": ["invalid"]}},
                ],
            },
        ]
    )

    assert receipts == []


def test_effect_receipts_from_batch_receipts_copies_reused_dict_objects() -> None:
    shared_receipt = {"operation": "write", "file": "src/shared.py"}

    receipts = _effect_receipts_from_batch_receipts(
        [
            {
                "batch_id": "batch-1",
                "turn_id": "turn-1",
                "effect_receipts": [shared_receipt],
                "results": [{"effect_receipt": shared_receipt}],
                "raw_results": [{"result": {"effect_receipt": shared_receipt}}],
            }
        ]
    )

    assert receipts == [shared_receipt, shared_receipt, shared_receipt]
    assert all(receipt is not shared_receipt for receipt in receipts)
    assert len({id(receipt) for receipt in receipts}) == len(receipts)


def test_batch_authoritative_success_requires_success_pending_or_effect_receipt() -> None:
    all_failed_receipts = [
        {
            "results": [
                {
                    "tool_name": "write_file",
                    "status": "error",
                    "error": "director_tool_execution_cancelled: session_not_active",
                }
            ],
            "raw_results": [
                {
                    "tool_name": "write_file",
                    "status": "error",
                    "error": "director_tool_execution_cancelled: session_not_active",
                }
            ],
            "effect_receipts": [],
            "pending_async_count": 0,
            "has_pending_async": False,
        }
    ]

    assert _batch_has_authoritative_success(all_failed_receipts) is False
    assert _batch_has_authoritative_success([{"results": [{"status": "success"}]}]) is True
    assert _batch_has_authoritative_success([{"effect_receipts": [{"file": "src/app.ts"}]}]) is True
    assert _batch_has_authoritative_success([{"pending_async_count": 1}]) is True


def test_no_effect_write_receipt_is_not_authoritative_success_and_requires_replan() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "edit_file",
                "status": "success",
                "result": {
                    "ok": True,
                    "no_op": True,
                    "reason": "edit_file_empty_search",
                },
                "effect_receipt": {
                    "authoritative": True,
                    "receipt_outcome": "succeeded",
                },
            }
        ],
        "effect_receipts": [
            {
                "authoritative": True,
                "receipt_outcome": "succeeded",
            }
        ],
        "success_count": 1,
        "failure_count": 0,
    }

    assert _batch_has_authoritative_success([receipt]) is False
    assert batch_write_failure_error_types(receipt) == ("director_write_no_effect",)
    assert batch_write_failures_require_llm_replan(receipt) is True


def test_scope_denied_write_requires_new_llm_invocation_without_widening_scope() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "write_file",
                "status": "error",
                "result": {
                    "ok": False,
                    "error_type": "director_write_policy_denied",
                    "retryable": False,
                    "director_policy": {
                        "allowed": False,
                        "allowed_scope": ["src/models/firefly.ts"],
                    },
                },
            }
        ]
    }

    assert batch_write_failure_error_types(receipt) == ("director_write_policy_denied",)
    assert batch_write_failures_require_llm_replan(receipt) is True
    assert receipt["results"][0]["result"]["director_policy"]["allowed_scope"] == ["src/models/firefly.ts"]


def test_inactive_session_write_failure_remains_fail_closed() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "write_file",
                "status": "error",
                "result": {
                    "ok": False,
                    "error_type": "session_not_active",
                    "retryable": False,
                },
            }
        ]
    }

    assert batch_write_failure_error_types(receipt) == ("session_not_active",)
    assert batch_write_failures_require_llm_replan(receipt) is False


def test_unknown_write_failure_remains_fail_closed() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "write_file",
                "status": "error",
                "result": {"ok": False, "error_type": "unclassified_runtime_failure"},
            }
        ]
    }

    assert batch_write_failures_require_llm_replan(receipt) is False


def test_directed_effect_no_replacement_failure_requires_fresh_edit_replan() -> None:
    """A physical no-match is a stale edit, not an unknown hard failure."""

    receipt = {
        "results": [
            {
                "tool_name": "edit_file",
                "status": "error",
                "error": "deo_physical_execution_failed:No replacements made",
                "result": {
                    "schema_version": "roles.adapters.directed_effect_physical_failure.v1",
                    "error_code": "deo_physical_execution_failed",
                    "failure_kind": "physical_result_failed",
                    "physical_error": "No replacements made",
                    "physical_error_type": "",
                },
            }
        ]
    }

    assert batch_write_failure_error_types(receipt) == ("stale_edit",)
    assert batch_write_failures_require_llm_replan(receipt) is True


def test_other_directed_effect_physical_failure_remains_fail_closed() -> None:
    """Do not make arbitrary DEO failures retriable merely because one no-match is."""

    receipt = {
        "results": [
            {
                "tool_name": "edit_file",
                "status": "error",
                "result": {
                    "error_code": "deo_physical_execution_failed",
                    "physical_error": "permission denied",
                },
            }
        ]
    }

    assert batch_write_failure_error_types(receipt) == ("deo_physical_execution_failed",)
    assert batch_write_failures_require_llm_replan(receipt) is False


def test_tool_batch_execution_identity_falls_back_to_transaction_authority() -> None:
    config = TransactionConfig(
        role_id="director",
        run_id="director-run-1",
        task_id="TASK-2",
        workspace="/workspace/project",
    )

    assert _resolve_tool_batch_execution_identity({}, config) == (
        "/workspace/project",
        "director-run-1",
        "TASK-2",
    )


def test_is_deo_abort_error_recognizes_policy_codes() -> None:
    assert _is_deo_abort_error("deo_director_policy_denied") is True
    assert _is_deo_abort_error("directed_effect_policy_guard_failed") is True
    assert _is_deo_abort_error("deo_authorization_hash_drift") is True
    assert _is_deo_abort_error("tool_dispatch_dropped: provider emitted 1 tool call(s)") is False
    assert _is_deo_abort_error("") is False


def test_seal_deo_abort_tool_lifecycle_writes_blocked_receipt(tmp_path) -> None:
    """R135: DEO abort must seal tool_call_lifecycle so ledger is not TOOL_LIFECYCLE_MISSING."""
    _bootstrap_test_fact_stream(tmp_path)
    ledger = TurnLedger(turn_id="turn-deo-abort")
    lifecycle = _seal_deo_abort_tool_lifecycle(
        workspace=str(tmp_path),
        run_id="director-run-deo",
        task_id="TASK-1",
        turn_id="turn-deo-abort",
        role_id="director",
        invocations=[
            {
                "tool_name": "write_file",
                "call_id": "call_write_1",
                "arguments": {"file": "src/main.ts", "content": "export {}\n"},
            }
        ],
        metadata={"provider_response_hash": "abc123", "run_id": "director-run-deo", "task_id": "TASK-1"},
        ledger=ledger,
        error_code="deo_director_policy_denied",
    )

    assert lifecycle["ok"] is False
    assert lifecycle["dispatch_status"] == "blocked"
    assert lifecycle["failure_class"] == "TOOL_RESULT_FAILED"
    assert lifecycle["reason"] == "deo_director_policy_denied"
    assert lifecycle["deo_abort"] is True
    assert any(flag.get("type") == "DEO_ABORT" for flag in ledger.anomaly_flags)

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id="director-run-deo",
        )
    ).projection

    assert projection["tool_lifecycle"]["event_count"] >= 1
    assert projection["tool_lifecycle"]["ok"] is False
    # Must not classify as bare missing once sealed.
    events = projection["tool_lifecycle"].get("events") or []
    assert events
    latest = events[-1]
    assert latest.get("ok") is False or latest.get("failed") is True
    status = str(latest.get("status") or latest.get("dispatch_status") or "")
    assert status in {"blocked", "dropped", "failed"} or latest.get("failed") is True


def test_failed_tool_batch_lifecycle_is_durable_without_effect_receipt(tmp_path) -> None:
    _bootstrap_test_fact_stream(tmp_path)
    _append_tool_batch_receipts_to_run_ledger(
        workspace=str(tmp_path),
        run_id="director-run-1",
        role_id="director",
        task_id="TASK-2",
        turn_id="turn-1",
        invocations=[{"tool_name": "write_file", "call_id": "call-1"}],
        receipts=[
            {
                "batch_id": "batch-1",
                "turn_id": "turn-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "write_file",
                        "status": "error",
                        "result": {"error_type": "director_write_policy_denied"},
                    }
                ],
                "raw_results": [],
                "success_count": 0,
                "failure_count": 1,
                "pending_async_count": 0,
                "has_pending_async": False,
            }
        ],
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id="director-run-1",
        )
    ).projection

    assert projection["tool_lifecycle"]["event_count"] == 1
    assert projection["tool_lifecycle"]["failed_count"] == 1
    failure = projection["tool_lifecycle"]["failure_evidence"][0]
    assert failure["failure_class"] == "TOOL_RESULT_FAILED"


def test_metadata_native_tool_call_count_accepts_lifecycle_envelope_refs() -> None:
    metadata = {
        "native_tool_call_envelope_refs": (
            {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
            {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
        ),
        "native_tool_calls_count": 1,
    }

    assert native_tool_call_count_from_metadata(metadata, fallback=0) == 2
    assert [item["tool_name"] for item in native_tool_call_envelope_refs_from_metadata(metadata)] == [
        "write_file",
        "execute_command",
    ]


def test_metadata_native_tool_call_count_accepts_lifecycle_receipt_envelope_refs() -> None:
    metadata = {
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_call_envelope_refs": (
                {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
                {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
            ),
        },
        "native_tool_calls_count": 1,
    }

    assert native_tool_call_count_from_metadata(metadata, fallback=0) == 2
    assert [item["tool_name"] for item in native_tool_call_envelope_refs_from_metadata(metadata)] == [
        "write_file",
        "execute_command",
    ]


def test_metadata_native_tool_call_count_keeps_numeric_fallback_without_envelopes() -> None:
    assert native_tool_call_count_from_metadata({"native_tool_calls_count": 3}, fallback=1) == 3
    assert native_tool_call_count_from_metadata({}, fallback=2) == 2


def test_metadata_native_tool_call_envelopes_deduplicates_aliases() -> None:
    metadata = {
        "native_tool_call_envelopes": ["bad legacy projection"],
        "native_tool_call_envelope_refs": [
            {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
            {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
            {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
        ],
    }

    assert [item["tool_name"] for item in native_tool_call_envelope_refs_from_metadata(metadata)] == [
        "write_file",
        "execute_command",
    ]


def test_tool_invocation_audit_ref_preserves_decoded_invocation_evidence() -> None:
    invocation = {
        "call_id": "call-1",
        "tool_name": "write_file",
        "execution_mode": ToolExecutionMode.WRITE_SERIAL,
        "arguments": {"file": "src/main.py"},
    }

    assert tool_invocation_audit_ref(
        invocation,
        reason="decoded_tool_batch_without_authoritative_receipt",
    ) == {
        "reason": "decoded_tool_batch_without_authoritative_receipt",
        "tool_name": "write_file",
        "call_id": "call-1",
        "execution_mode": "write_serial",
        "target_file": "src/main.py",
    }


def test_tool_invocation_audit_ref_accepts_provider_native_call_shape() -> None:
    invocation = {
        "id": "call-native",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": '{"path": "src/generated.py", "content": "print(1)"}',
        },
    }

    assert tool_invocation_audit_ref(
        invocation,
        reason="finalization_tool_calls_blocked",
    ) == {
        "reason": "finalization_tool_calls_blocked",
        "tool_name": "write_file",
        "call_id": "call-native",
        "target_file": "src/generated.py",
    }


class _FakePrepareDenial:
    def __init__(self, *, status: str, error_code: str, upstream: str) -> None:
        self.status = status
        self.prepared_batch = None if status != "ready" else object()
        self.error_code = error_code
        self.upstream_evidence = (
            ("stage", "member_admission"),
            ("upstream_code", upstream),
        )


def test_r149_transient_deo_prepare_lock_failure_classifies_lock_codes() -> None:
    """R149: flock contention upstream codes must be retryable for prepare_batch."""

    assert _is_transient_deo_prepare_lock_failure(
        _FakePrepareDenial(
            status="denied",
            error_code="deo_member_admission_failed",
            upstream="fact_stream_unknown_failure",
        )
    )
    assert _is_transient_deo_prepare_lock_failure(
        _FakePrepareDenial(
            status="denied",
            error_code="deo_member_admission_failed",
            upstream="stream_lock_timeout",
        )
    )
    assert _is_transient_deo_prepare_lock_failure(
        _FakePrepareDenial(
            status="denied",
            error_code="deo_member_admission_failed",
            upstream="lock_acquisition_timeout",
        )
    )
    assert not _is_transient_deo_prepare_lock_failure(
        _FakePrepareDenial(
            status="denied",
            error_code="deo_member_admission_failed",
            upstream="inventory_member_not_found",
        )
    )
    ready = _FakePrepareDenial(status="ready", error_code="", upstream="")
    ready.prepared_batch = object()
    assert not _is_transient_deo_prepare_lock_failure(ready)
    assert (
        _deo_prepare_upstream_code(
            _FakePrepareDenial(
                status="denied",
                error_code="deo_member_admission_failed",
                upstream="stream_lock_timeout",
            )
        )
        == "stream_lock_timeout"
    )
