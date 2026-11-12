"""Committed-value joins and private persistent-session component fixtures."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.tests.runtime_boundary_scenarios import committed_result, project_denied_batch_fixture
from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.cells.roles.runtime.internal.kernel_session_turn import (
    _committed_transcript_messages,
    _committed_transcript_sources,
)
from polaris.kernelone.audit.context_os_prompt import audit_context_os_prompt_messages
from polaris.kernelone.context.prompt_safety import validate_tool_observation_sources


def _project_denied_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RoleTurnResult:
    return project_denied_batch_fixture(tmp_path, monkeypatch)


def test_cold_inventory_regeneration_keeps_native_and_normalized_namespaces_separate() -> None:
    result = committed_result("write_file")
    facts = json.dumps(
        {"metadata": result.metadata, "batch_receipt": result.batch_receipt, "tool_calls": result.tool_calls}
    )
    cold = RoleTurnResult(**json.loads(facts))
    sources = _committed_transcript_sources([cold], role="director")
    messages = _committed_transcript_messages([cold])
    assert validate_tool_observation_sources(messages, sources, role="director") == ({1}, [])
    assert sources[0].metadata["native_arguments_hash"] != sources[0].metadata["arguments_hash"]
    assert json.loads(sources[0].metadata["native_arguments_json"]) == {
        "path": "example.ts",
        "text": "export const timeout_seconds = 60;\n",
    }
    assert json.loads(sources[0].metadata["arguments_json"]) == {
        "file": "example.ts",
        "content": "export const timeout_seconds = 60;\n",
    }
    assert (
        json.dumps(
            {"metadata": result.metadata, "batch_receipt": result.batch_receipt, "tool_calls": result.tool_calls}
        )
        == facts
    )


@pytest.mark.parametrize("change", ["receipt", "normalized", "native", "snapshot"])
def test_changed_committed_owner_inputs_cannot_issue_data_binding(change) -> None:
    result = committed_result()
    invocation = result.metadata["turn_outcome"]["execution"]["invocations"][0]
    if change == "receipt":
        assert result.batch_receipt is not None
        result.batch_receipt["results"][0]["result"]["stdout"] = "changed"
    elif change == "normalized":
        result.tool_calls[0]["args"]["command"] = "changed"
    elif change == "native":
        invocation["native_arguments"]["command"] = "changed"
    else:
        invocation["classification"]["snapshot"]["snapshot_hash"] = "f" * 64
    with pytest.raises(RuntimeError, match="committed_tool_observation_"):
        _committed_transcript_sources([result], role="director")


def test_reused_native_call_id_binds_each_committed_occurrence() -> None:
    results = [committed_result(turn="first"), committed_result(turn="second")]
    sources = _committed_transcript_sources(results, role="director")
    messages = _committed_transcript_messages(results)
    assert validate_tool_observation_sources(messages, sources, role="director") == ({1, 3}, [])
    assert validate_tool_observation_sources(messages[:2], sources, role="director")[1]


def test_legacy_missing_inventory_gets_no_data_binding() -> None:
    result = committed_result()
    result.metadata.pop("turn_outcome")
    assert _committed_transcript_sources([result], role="director") == ()


def _projected_audit(result: RoleTurnResult):
    messages = _committed_transcript_messages([result])
    sources = _committed_transcript_sources([result], role="director")
    audit = audit_context_os_prompt_messages(
        messages=[
            {"role": "system", "content": "You are Director."},
            *messages,
            {"role": "user", "content": "Continue verification."},
        ],
        metadata={"prebuilt_projection_messages": True},
        current_user_instruction="Continue verification.",
        expected=True,
        tool_observation_sources=sources,
        tool_observation_role="director",
    )
    return messages, audit


def _replace_fixture_payload(result: RoleTurnResult, payload: dict[str, Any]) -> None:
    # Consistent synthetic owner values, before real projection; not a native fact.
    assert result.batch_receipt is not None
    result.batch_receipt["results"][0]["result"] = deepcopy(payload)
    result.metadata["turn_outcome"]["execution"]["receipt"]["results"][0]["result"] = deepcopy(payload)


@pytest.mark.parametrize("key", ["control_plane_job_token", "controlPlaneJobToken"])
def test_real_producer_projects_existing_control_plane_token_aliases_out_of_bound_data(key) -> None:
    result = committed_result()
    assert result.batch_receipt is not None
    payload = deepcopy(result.batch_receipt["results"][0]["result"])
    payload["nested"] = {key: {"token_id": "synthetic-token", "stage": "director"}, "ordinary": "retained"}
    _replace_fixture_payload(result, payload)
    messages, audit = _projected_audit(result)
    assert "synthetic-token" not in messages[1]["content"]
    assert "retained" in messages[1]["content"]
    assert audit["ok"] is True


def test_real_bound_success_stdout_may_contain_source_literal_receipt_key() -> None:
    result = committed_result()
    assert result.batch_receipt is not None
    payload = deepcopy(result.batch_receipt["results"][0]["result"])
    payload["stdout"] = "const director_policy = {};"
    _replace_fixture_payload(result, payload)
    messages, audit = _projected_audit(result)
    assert "const director_policy = {};" in messages[1]["content"]
    assert audit["ok"] is True
    unbound = audit_context_os_prompt_messages(
        messages=[*messages, {"role": "user", "content": "Continue verification."}],
        metadata={"prebuilt_projection_messages": True},
        current_user_instruction="Continue verification.",
        expected=True,
    )
    assert unbound["ok"] is False


def test_success_label_cannot_bind_a_structurally_failed_physical_result() -> None:
    result = committed_result()
    assert result.batch_receipt is not None
    payload = deepcopy(result.batch_receipt["results"][0]["result"])
    payload["ok"] = False
    _replace_fixture_payload(result, payload)
    with pytest.raises(ValueError, match="tool_observation_failed_result"):
        _committed_transcript_sources([result], role="director")


@pytest.mark.parametrize("cold", [False, True])
def test_owner_defaults_in_denied_receipt_preserve_native_followup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cold: bool
) -> None:
    result = _project_denied_batch(tmp_path, monkeypatch)
    if cold:
        result = RoleTurnResult(
            **json.loads(
                json.dumps(
                    {
                        "metadata": result.metadata,
                        "batch_receipt": result.batch_receipt,
                        "tool_calls": result.tool_calls,
                        "is_complete": result.is_complete,
                    },
                    ensure_ascii=False,
                )
            )
        )
    assert result.metadata["needs_followup_workflow"] is True
    assert result.is_complete is False
    assert _committed_transcript_sources([result], role="director") == ()
    messages = _committed_transcript_messages([result])
    assert [m.get("tool_call_id") for m in messages if m["role"] == "tool"] == ["tree-call", "denied-call"]
    assert "deo_batch_mutation_admission_rejected" in messages[1]["content"]
    assert "deo_command_scope_denied" in messages[2]["content"]


@pytest.mark.parametrize("cold", [False, True])
def test_successful_read_replays_captured_decoder_pass_through_without_invented_defaults(cold: bool) -> None:
    result = committed_result("read_file")
    if cold:
        result = RoleTurnResult(
            **json.loads(
                json.dumps(
                    {
                        "metadata": result.metadata,
                        "batch_receipt": result.batch_receipt,
                        "tool_calls": result.tool_calls,
                    }
                )
            )
        )
    invocation = result.metadata["turn_outcome"]["execution"]["invocations"][0]
    assert invocation["classification"]["normalization_required"] is False
    assert invocation["native_arguments"] == invocation["arguments"] == {"file": "example.ts"}
    sources = _committed_transcript_sources([result], role="director")
    assert validate_tool_observation_sources(_committed_transcript_messages([result]), sources, role="director") == (
        {1},
        [],
    )


def test_observation_normalization_is_rederived_from_snapshot_not_caller_flag() -> None:
    result = committed_result("write_file")
    invocation = result.metadata["turn_outcome"]["execution"]["invocations"][0]
    invocation["classification"]["normalization_required"] = False
    sources = _committed_transcript_sources([result], role="director")
    assert len(sources) == 1
    invocation["native_arguments"]["path"] = "foreign.ts"
    with pytest.raises(RuntimeError, match="committed_tool_observation_native_arguments_mismatch"):
        _committed_transcript_sources([result], role="director")


@pytest.mark.parametrize(
    "change",
    ["result", "status", "call_id", "batch_id", "duration", "duration_type", "effect_commit", "unknown"],
)
def test_owner_default_normalization_keeps_real_receipt_drift_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    result = _project_denied_batch(tmp_path, monkeypatch)
    assert result.batch_receipt is not None
    row = result.batch_receipt["results"][0]
    if change == "result":
        row["result"]["error"] = "changed physical observation"
    elif change == "status":
        row["status"] = "timeout"
    elif change == "call_id":
        row["call_id"] = "foreign-call"
    elif change == "batch_id":
        result.batch_receipt["batch_id"] = "foreign-batch"
    elif change == "duration":
        row["execution_time_ms"] = 7
    elif change == "duration_type":
        row["execution_time_ms"] = False
    elif change == "effect_commit":
        row["effect_receipt_commit"] = {"code": "receipt_committed", "event_id": "foreign-effect"}
    else:
        row["unknown_authority"] = True
        # An equal but undeclared field must not disappear during normalization.
        result.metadata["turn_outcome"]["execution"]["receipt"]["results"][0]["unknown_authority"] = True
    with pytest.raises(RuntimeError, match="committed_tool_observation_"):
        _committed_transcript_sources([result], role="director")


@pytest.mark.asyncio
async def test_retired_denied_mixed_batch_cannot_reach_a_legal_owner_turn(
    tmp_path: Path,
) -> None:
    """The legacy mixed-batch subject is retired; the entry must stay closed."""
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
        legacy_retirement_reason,
    )

    assert legacy_retirement_reason()

    await assert_legacy_session_entry_is_closed(
        session_id="retired-denied-mixed-batch",
        workspace=str(tmp_path),
    )

    assert not list(tmp_path.iterdir())
