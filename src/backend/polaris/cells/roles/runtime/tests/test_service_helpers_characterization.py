"""Characterization tests for `roles.runtime.public.service` module-level helpers.

These tests pin the *current* behavior of the pure helper functions before the
lossless decomposition that moves their bodies into sibling modules. Every
assertion is derived from the real implementation, and every name is referenced
through the ``service`` module namespace so the suite doubles as a
re-export-preservation guard for the public surface.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.cells.roles.runtime.public import service as runtime_service
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1


def _make_role_execution_result(**overrides: Any) -> RoleExecutionResultV1:
    base: dict[str, Any] = {
        "ok": True,
        "status": "ok",
        "role": "pm",
        "workspace": ".",
        "task_id": "t1",
        "session_id": "s1",
        "run_id": "r1",
        "output": "out",
        "thinking": None,
        "tool_calls": (),
        "artifacts": (),
        "usage": {},
        "metadata": {},
        "error_code": None,
        "error_message": None,
    }
    base.update(overrides)
    return RoleExecutionResultV1(**base)


# ── result_mapping cluster ───────────────────────────────────────────────────


def test_extract_tool_calls_reads_name_then_tool_keys() -> None:
    # The 4th item is intentionally not a dict to characterize the guard that
    # skips non-mapping entries.
    tool_calls: list[Any] = [{"name": "write_file"}, {"tool": "read"}, {"other": "y"}, "not-a-dict"]
    result = RoleTurnResult(content="x", tool_calls=tool_calls)
    assert runtime_service._extract_tool_calls(result) == ("write_file", "read")


def test_extract_artifacts_filters_blank_and_non_list() -> None:
    with_list = RoleTurnResult(content="x", structured_output={"artifacts": ["a.py", " ", "b.py"]})
    assert runtime_service._extract_artifacts(with_list) == ("a.py", "b.py")
    without_list = RoleTurnResult(content="x", structured_output={"artifacts": "nope"})
    assert runtime_service._extract_artifacts(without_list) == ()
    no_structured = RoleTurnResult(content="x")
    assert runtime_service._extract_artifacts(no_structured) == ()


def test_copy_result_metadata_returns_new_dict() -> None:
    src = {"a": 1}
    out = runtime_service._copy_result_metadata(src)
    assert out == {"a": 1}
    assert out is not src
    assert runtime_service._copy_result_metadata(None) == {}


def test_copy_tool_result_metadata_keeps_mappings_only() -> None:
    items = [{"k": 1}, "skip", {"j": 2}]
    assert runtime_service._copy_tool_result_metadata(items) == [{"k": 1}, {"j": 2}]
    assert runtime_service._copy_tool_result_metadata("not-a-list") == []


def test_copy_batch_receipt_metadata_handles_mapping_and_model_dump() -> None:
    assert runtime_service._copy_batch_receipt_metadata({"r": 1}) == {"r": 1}

    class _Dumpable:
        def model_dump(self) -> dict[str, Any]:
            return {"d": 2}

    assert runtime_service._copy_batch_receipt_metadata(_Dumpable()) == {"d": 2}
    assert runtime_service._copy_batch_receipt_metadata(object()) is None


def test_contract_result_metadata_folds_tool_results_and_receipt() -> None:
    result = RoleTurnResult(
        content="x",
        metadata={"a": 1},
        structured_output={"construction_plan": {"implementation": ["build"]}},
        tool_results=[{"name": "t"}],
        batch_receipt={"rid": "abc"},
    )
    meta = runtime_service._contract_result_metadata(result)
    assert meta["a"] == 1
    assert meta["structured_output"] == {"construction_plan": {"implementation": ["build"]}}
    assert meta["tool_results"] == [{"name": "t"}]
    assert meta["batch_receipt"] == {"rid": "abc"}


def test_with_result_metadata_patch_merges_and_preserves_fields() -> None:
    result = _make_role_execution_result(metadata={"a": 1}, output="hi")
    patched = runtime_service._with_result_metadata_patch(result, {"b": 2})
    assert patched.metadata == {"a": 1, "b": 2}
    assert patched.output == "hi"
    assert patched.role == "pm"
    # Original is untouched.
    assert result.metadata == {"a": 1}


def test_extract_turn_envelope_metadata_prefers_existing_envelope() -> None:
    with_env = _make_role_execution_result(metadata={"turn_envelope": {"turn_id": "T"}})
    assert runtime_service._extract_turn_envelope_metadata(with_env) == {"turn_id": "T"}

    with_turn_id = _make_role_execution_result(
        metadata={"turn_id": "T2"}, session_id="S", run_id="R", role="pm", task_id="TK"
    )
    env = runtime_service._extract_turn_envelope_metadata(with_turn_id)
    assert env == {"turn_id": "T2", "session_id": "S", "run_id": "R", "role": "pm", "task_id": "TK"}

    empty = _make_role_execution_result(metadata={})
    assert runtime_service._extract_turn_envelope_metadata(empty) == {}


def test_to_contract_result_ok_failed_and_in_progress() -> None:
    ok = runtime_service._to_contract_result(
        role="pm",
        workspace=".",
        task_id="t",
        session_id="se",
        run_id="ru",
        result=RoleTurnResult(
            content="out",
            tool_calls=[{"name": "write_file"}],
            tool_results=[{"tool_name": "write_file", "status": "success"}],
            structured_output={"artifacts": ["f.py"]},
            execution_stats={"k": 1},
        ),
    )
    assert ok.ok is True
    assert ok.status == "ok"
    assert ok.tool_calls == ("write_file",)
    assert ok.artifacts == ("f.py",)
    assert ok.usage == {"k": 1}
    assert ok.metadata["structured_output"] == {"artifacts": ["f.py"]}
    assert ok.error_code is None

    dropped = runtime_service._to_contract_result(
        role="director",
        workspace=".",
        task_id="t",
        session_id="se",
        run_id="ru",
        result=RoleTurnResult(
            content="I will call write_file.",
            tool_calls=[{"name": "write_file"}],
        ),
    )
    assert dropped.ok is False
    assert dropped.status == "failed"
    assert dropped.error_code == "tool_dispatch_dropped"
    assert dropped.error_message == (
        "tool_dispatch_dropped: native tool calls observed but no tool dispatch/effect receipt was committed"
    )
    assert dropped.metadata["tool_call_lifecycle"]["dispatch_status"] == "dropped"
    assert dropped.metadata["tool_call_lifecycle"]["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "reason": "tool_dispatch_dropped",
        }
    ]

    dropped_from_metadata = runtime_service._to_contract_result(
        role="director",
        workspace=".",
        task_id="t",
        session_id="se",
        run_id="ru",
        result=RoleTurnResult(
            content="I will call write_file.",
            metadata={
                "tool_call_lifecycle": {
                    "schema_version": "tool_call_lifecycle_receipt.v1",
                    "dispatch_status": "dropped",
                    "failure_class": "tool_dispatch_dropped",
                    "native_tool_calls_count": 1,
                    "decoded_tool_calls_count": 0,
                    "dispatched_tool_calls_count": 0,
                    "dropped_tool_calls": ["write_file"],
                }
            },
        ),
    )
    assert dropped_from_metadata.ok is False
    assert dropped_from_metadata.status == "failed"
    assert dropped_from_metadata.error_code == "tool_dispatch_dropped"
    assert dropped_from_metadata.error_message == (
        "tool_dispatch_dropped: required or native tool calls had no dispatch/effect receipt"
    )
    assert dropped_from_metadata.metadata["tool_call_lifecycle"]["native_tool_calls_count"] == 1

    failed = runtime_service._to_contract_result(
        role="pm",
        workspace=".",
        task_id=None,
        session_id=None,
        run_id=None,
        result=RoleTurnResult(content="", error="boom"),
    )
    assert failed.ok is False
    assert failed.status == "failed"
    assert failed.error_code == "role_runtime_error"
    assert failed.error_message == "boom"

    failed_with_event_evidence = runtime_service._to_contract_result(
        role="chief_engineer",
        workspace=".",
        task_id="TASK-1",
        session_id=None,
        run_id="run-1",
        result=RoleTurnResult(
            content="",
            error="Request timeout (57.0s)",
            turn_events_metadata=[
                {
                    "event": "llm_error",
                    "metadata": {
                        "context_snapshot_ref": "ctx-timeout",
                        "final_request_context_audit": {
                            "final_request_token_estimate": 2307,
                            "context_window_utilization": 0.0088,
                        },
                        "context_os_audit": {"ok": True},
                    },
                }
            ],
        ),
    )
    assert failed_with_event_evidence.ok is False
    assert failed_with_event_evidence.metadata["context_snapshot_ref"] == "ctx-timeout"
    assert failed_with_event_evidence.metadata["final_request_context_audit"]["final_request_token_estimate"] == 2307
    assert failed_with_event_evidence.metadata["context_os_audit"] == {"ok": True}

    in_progress = runtime_service._to_contract_result(
        role="pm",
        workspace=".",
        task_id=None,
        session_id=None,
        run_id=None,
        result=RoleTurnResult(content="x", is_complete=False),
    )
    assert in_progress.status == "in_progress"


# ── cognitive_strategy cluster ───────────────────────────────────────────────


def test_metadata_flag_enabled_truth_table() -> None:
    assert runtime_service._metadata_flag_enabled({}, key="x") is False
    assert runtime_service._metadata_flag_enabled({"x": True}, key="x") is True
    assert runtime_service._metadata_flag_enabled({"x": "required"}, key="x") is True
    assert runtime_service._metadata_flag_enabled({"x": "off"}, key="x") is False
    # First payload that contains the key wins.
    assert runtime_service._metadata_flag_enabled({"x": "on"}, {"x": "off"}, key="x") is True


def test_safe_float_falls_back_on_bad_input() -> None:
    assert runtime_service._safe_float(None) == 0.0
    assert runtime_service._safe_float("3.5") == 3.5
    assert runtime_service._safe_float("zz", 9.0) == 9.0


def test_copy_string_tuple_dedupes_strips_and_limits() -> None:
    assert runtime_service._copy_string_tuple([" a ", "a", "", "b", "c"], limit=2) == ("a", "b")
    assert runtime_service._copy_string_tuple("abc", limit=5) == ()


def test_deep_merge_strategy_overrides_recurses_and_tuples_sequences() -> None:
    out = runtime_service._deep_merge_strategy_overrides({"a": {"x": 1}}, {"a": {"y": 2}, "b": [1, 2]})
    assert out == {"a": {"x": 1, "y": 2}, "b": (1, 2)}
    assert runtime_service._deep_merge_strategy_overrides({"a": 1}, None) == {"a": 1}


def test_copy_strategy_override_handles_non_mapping() -> None:
    assert runtime_service._copy_strategy_override({"a": {"b": 1}}) == {"a": {"b": 1}}
    assert runtime_service._copy_strategy_override("x") == {}


def test_copy_cognitive_guidance_shape() -> None:
    guidance = runtime_service._copy_cognitive_guidance(
        {
            "intent_type": "code_generation",
            "confidence": "0.9",
            "uncertainty_score": 0.3,
            "execution_path": "full",
            "blocked_tools": ["a", "a", "b"],
            "cognitive_analysis": {
                "clarity_level": "high",
                "verification_needed": True,
                "actions_taken": ["x", "y"],
            },
        }
    )
    assert guidance["intent_type"] == "code_generation"
    assert guidance["confidence"] == 0.9
    assert guidance["blocked_tools"] == ("a", "b")
    assert guidance["actions_taken"] == ("x", "y")
    assert guidance["clarity_level"] == "high"
    assert guidance["verification_needed"] is True


def test_has_forced_transaction_tool_choice_variants() -> None:
    assert runtime_service._has_forced_transaction_tool_choice({"_transaction_kernel_forced_tool_choice": {"x": 1}})
    assert not runtime_service._has_forced_transaction_tool_choice({"_transaction_kernel_forced_tool_choice": "auto"})
    assert runtime_service._has_forced_transaction_tool_choice({"_transaction_kernel_forced_tool_choice": "required"})
    assert runtime_service._has_forced_transaction_tool_choice({"_transaction_kernel_forced_tool_definitions": [1]})
    assert not runtime_service._has_forced_transaction_tool_choice({})


def test_apply_forced_transaction_tool_guidance_rewrites_intent() -> None:
    guidance: dict[str, Any] = {"intent_type": "qa", "execution_path": "read", "verification_needed": False}
    applied = runtime_service._apply_forced_transaction_tool_guidance(
        guidance, {"_transaction_kernel_forced_tool_choice": {"a": 1}}
    )
    assert applied is True
    assert guidance["intent_type"] == "code_generation"
    assert guidance["execution_path"] == "forced_transaction_tool_write"
    assert guidance["verification_needed"] is True
    assert guidance["original_intent_type"] == "qa"

    no_force: dict[str, Any] = {"intent_type": "qa"}
    assert runtime_service._apply_forced_transaction_tool_guidance(no_force, {}) is False


def test_resolve_cognitive_runtime_blocker_approval() -> None:
    approved = runtime_service._resolve_cognitive_runtime_blocker_approval(
        context={},
        metadata={
            "cognitive_runtime_approval": {
                "mode": "auto_accept",
                "scope": "x",
                "source": "cli",
                "approved_by": "me",
            }
        },
    )
    assert approved == {"mode": "auto_accept", "source": "cli", "scope": "x", "approved_by": "me"}
    none_approved = runtime_service._resolve_cognitive_runtime_blocker_approval(
        context={},
        metadata={"cognitive_runtime_approval": {"mode": "manual", "scope": "x"}},
    )
    assert none_approved is None


def test_build_cognitive_strategy_override_gating() -> None:
    assert (
        runtime_service._build_cognitive_strategy_override(
            {"execution_path": "read", "intent_type": "qa", "uncertainty_score": 0.1, "verification_needed": False}
        )
        == {}
    )
    override = runtime_service._build_cognitive_strategy_override(
        {
            "execution_path": "full_write",
            "intent_type": "code_generation",
            "uncertainty_score": 0.7,
            "verification_needed": True,
        }
    )
    assert override["exploration"]["max_expansion_depth"] == 5
    assert override["read_escalation"]["full_read_threshold_kb"] == 500
    assert override["cognitive_runtime"]["applied"] is True
    assert override["cognitive_runtime"]["uncertainty_score"] == 0.7


def test_copy_llm_provider_policy_into_context_copies_known_keys() -> None:
    out = runtime_service._copy_llm_provider_policy_into_context(
        context_override={"existing": 1},
        metadata={
            "allowed_provider_types": ["a"],
            "llm_provider_policy": {"deny": ["x"]},
            "unrelated": "ignored",
        },
    )
    assert out["existing"] == 1
    assert out["allowed_provider_types"] == ["a"]
    assert out["llm_provider_policy"] == {"deny": ["x"]}
    assert "unrelated" not in out


def test_cognitive_runtime_result_patch_shape() -> None:
    patch = runtime_service._cognitive_runtime_result_patch(
        evidence={"available": True},
        request_metadata={"cognitive_runtime_preflight": {"mode": "off"}, "context_os_preflight": {"enabled": True}},
    )
    assert patch["cognitive_runtime_evidence"] == {"available": True}
    assert patch["cognitive_runtime_preflight"] == {"mode": "off"}
    assert patch["context_os_preflight"] == {"enabled": True}
    invalid = runtime_service._cognitive_runtime_result_patch(evidence=None, request_metadata={})
    assert invalid["cognitive_runtime_evidence"] == {
        "available": False,
        "error_code": "invalid_cognitive_runtime_evidence",
    }


# ── context_gateway_wiring cluster ───────────────────────────────────────────


def test_build_context_gateway_config_uses_module_namespace_providers(monkeypatch: Any) -> None:
    """The mounted providers must resolve the (patchable) reader functions from
    the service module namespace at call time."""
    blueprint_sentinel = object()
    verdict_sentinel = object()

    monkeypatch.setattr(
        runtime_service,
        "_read_blueprint_status_for_context",
        lambda task_id, workspace: blueprint_sentinel,
    )
    monkeypatch.setattr(
        runtime_service,
        "_read_qa_verdict_for_context",
        lambda task_id, workspace: verdict_sentinel,
    )

    from unittest.mock import MagicMock

    request = MagicMock()
    resident_capability_surface = {
        "schema_version": "resident.agi_capability_surface.v1",
        "items": [],
    }
    resident_decision_trace = [{"actor": "resident", "stage": "goal_staging"}]
    request.context_override = {
        "resident_agi_capability_surface": resident_capability_surface,
        "resident_agi_decision_trace": resident_decision_trace,
    }
    config = runtime_service._build_context_gateway_config_for_role("pm", MagicMock(), request)
    assert config.blueprint_overview_provider is not None
    assert config.verdict_history_provider is not None
    assert config.resident_agi_capability_provider is not None
    assert config.resident_agi_decision_trace_provider is not None
    assert config.blueprint_overview_provider("t1", ".") is blueprint_sentinel
    assert config.verdict_history_provider("t2", ".") is verdict_sentinel
    assert config.resident_agi_capability_provider(".") is resident_capability_surface
    assert config.resident_agi_decision_trace_provider(".") is resident_decision_trace


def test_read_blueprint_and_qa_return_none_for_blank_task() -> None:
    assert runtime_service._read_blueprint_status_for_context("", ".") is None
    assert runtime_service._read_qa_verdict_for_context("   ", ".") is None
