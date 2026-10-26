"""Actual producer/merge/commit with private synthetic tool I/O only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public.committed_turn_receipts import query_committed_turn_receipts
from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchReceipt,
    ToolCallId,
    ToolInvocation,
)
from polaris.cells.roles.kernel.tests.run_ledger_owner_scenarios import (
    produce_readonly_completion_order_case,
    produce_readonly_owner_case,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("count", "reordered", "mixed"), [(9, False, False), (3, True, True)])
async def test_actual_parent_child_and_parallel_collection_layout_is_observed(
    tmp_path: Path,
    count: int,
    reordered: bool,
    mixed: bool,
) -> None:
    data = await produce_readonly_owner_case(tmp_path, count=count, reordered=reordered, mixed_serializers=mixed)
    observation = query_committed_turn_receipts(tmp_path, lifecycle_receipts=[data["lifecycle"]])[0]
    assert observation.status == "observed", observation.reason
    assert observation.batch_receipt == data["outcome"].execution.receipt.model_dump(mode="json")
    assert observation.batch_receipt_refs == tuple((item["batch_id"], stable_hash(item)) for item in data["components"])


@pytest.mark.asyncio
async def test_runtime_completion_order_is_distinct_from_its_returned_declaration_order(tmp_path: Path) -> None:
    physical, returned = await produce_readonly_completion_order_case(tmp_path)
    assert physical == [1, 2, 0]
    assert returned == ["opaque-0", "opaque-1", "opaque-2"]


def _cold_owner_query(root: Path, lifecycle: dict[str, Any]) -> dict[str, Any]:
    query_input = root / "private-cold-query.json"
    query_input.write_text(json.dumps(lifecycle, ensure_ascii=False), encoding="utf-8")
    script = """from dataclasses import asdict
import json
from pathlib import Path
import sys
from polaris.cells.control_plane.run_ledger.public.committed_turn_receipts import query_committed_turn_receipts
lifecycle=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
print(json.dumps(asdict(query_committed_turn_receipts(sys.argv[1],lifecycle_receipts=[lifecycle])[0])))
"""
    result = subprocess.run(
        ["rtk", "proxy", sys.executable, "-c", script, str(root), str(query_input)],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize(("count", "reordered", "mixed"), [(9, False, False), (3, True, True)])
async def test_actual_layout_cold_query_uses_unchanged_durable_owner(
    tmp_path: Path,
    count: int,
    reordered: bool,
    mixed: bool,
) -> None:
    data = await produce_readonly_owner_case(tmp_path, count=count, reordered=reordered, mixed_serializers=mixed)
    runtime = tmp_path / ".polaris" / "runtime"
    before = {str(p.relative_to(runtime)): p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    observed = _cold_owner_query(tmp_path, data["lifecycle"])
    assert before == {str(p.relative_to(runtime)): p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    assert len(data["calls"]) == count
    assert observed["status"] == "observed", observed["reason"]
    assert observed["outcome_hash"] == stable_hash(data["outcome"].model_dump(mode="json", exclude={"commit_ref"}))


@pytest.mark.asyncio
@pytest.mark.parametrize("schedule", ["serial", "mixed"])
async def test_actual_serial_or_mixed_schedule_cannot_reorder(tmp_path: Path, schedule: str) -> None:
    data = await produce_readonly_owner_case(tmp_path, reordered=True, schedule=schedule)
    assert data["outcome"].decision.tool_batch.readonly_serial
    observed = query_committed_turn_receipts(tmp_path, lifecycle_receipts=[data["lifecycle"]])[0]
    assert observed.status == "unknown" and observed.reason == "owner_invocation_order_not_parallel"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "foreign_id",
        "hash",
        "missing",
        "duplicate",
        "stale_retry",
        "ref_order",
        "list_hash",
        "provider_hash",
        "foreign_run",
        "foreign_task",
        "foreign_turn",
    ],
)
async def test_actual_component_ref_or_lifecycle_damage_remains_unknown(tmp_path: Path, defect: str) -> None:
    from copy import deepcopy

    data = await produce_readonly_owner_case(tmp_path, reordered=True, mixed_serializers=True)
    good = query_committed_turn_receipts(tmp_path, lifecycle_receipts=[data["lifecycle"]])[0]
    assert good.status == "observed", good.reason
    lifecycle = deepcopy(data["lifecycle"])
    refs = lifecycle["batch_receipt_refs"]
    if defect == "foreign_id":
        refs[0]["batch_id"] = "opaque-foreign-component"
    elif defect == "hash":
        refs[0]["receipt_hash"] = "0" * 64
    elif defect == "missing":
        refs.pop()
    elif defect == "duplicate":
        refs[-1] = deepcopy(refs[0])
    elif defect == "stale_retry":
        previous = await produce_readonly_owner_case(
            tmp_path / "other-retry", reordered=True, read_prefix="stale prior result "
        )
        old_ref = previous["lifecycle"]["batch_receipt_refs"][0]
        assert old_ref["batch_id"] == refs[0]["batch_id"] and old_ref["receipt_hash"] != refs[0]["receipt_hash"]
        refs[0] = deepcopy(old_ref)
    elif defect == "ref_order":
        refs.reverse()
    elif defect == "list_hash":
        lifecycle["batch_receipt_hash"] = "0" * 64
    elif defect == "provider_hash":
        lifecycle["provider_response_hash"] = "c" * 64
    else:
        lifecycle[defect.removeprefix("foreign_") + "_id"] = "foreign-identity"
    observed = query_committed_turn_receipts(tmp_path, lifecycle_receipts=[lifecycle])[0]
    assert observed.status == "unknown", (defect, observed)


def _protocol_owner_with_snapshot(root: Path):
    """Counterfactual protocol owner for rejection tests, never native evidence."""
    from copy import deepcopy

    from polaris.cells.control_plane.run_ledger.tests.test_committed_turn_receipts import _fixture

    payload, lifecycle = _fixture(root)
    owner = payload["outcome"]
    typed = [
        ToolInvocation(
            call_id=ToolCallId(item["call_id"]),
            tool_name=item["tool_name"],
            raw_tool_name=item["raw_tool_name"],
            arguments=item["arguments"],
            native_arguments=item["native_arguments"],
        ).model_dump(mode="json")
        for item in owner["decision"]["tool_batch"]["invocations"]
    ]
    owner["decision"]["tool_batch"]["invocations"] = deepcopy(typed)
    owner["execution"]["invocations"] = deepcopy(typed)
    owner["execution"]["invocation_batches"][0]["invocations"] = deepcopy(typed)
    return payload, lifecycle


@pytest.mark.parametrize(
    "defect",
    [
        "snapshot_hash",
        "missing_provided_hash",
        "unregistered_snapshot",
        "write_snapshot",
        "classification_raw_name",
        "normalization_zero",
        "native_index_bool",
        "input_bool_vs_int",
        "result_bool_vs_int",
        "duplicate_call",
        "inventory_input",
        "explicit_null_snapshot",
        "legacy_raw_name_missing",
    ],
)
def test_enclosing_input_types_and_owner_snapshot_cannot_be_forged(tmp_path: Path, defect: str) -> None:
    from copy import deepcopy
    from dataclasses import asdict, replace

    from polaris.cells.control_plane.run_ledger.tests.test_committed_turn_receipts import _append
    from polaris.kernelone.tool_execution.captured_tool_semantics import restore_captured_tool_spec_snapshot
    from polaris.kernelone.tool_execution.contracts import FrozenMapEntryV1, FrozenMapV1, FrozenScalarV1

    payload, lifecycle = _protocol_owner_with_snapshot(tmp_path)
    owner = payload["outcome"]
    declaration = owner["decision"]["tool_batch"]["invocations"]
    execution = owner["execution"]["invocations"]
    inventory = owner["execution"]["invocation_batches"][0]["invocations"]
    if defect in {
        "snapshot_hash",
        "missing_provided_hash",
        "unregistered_snapshot",
        "write_snapshot",
        "classification_raw_name",
        "normalization_zero",
        "explicit_null_snapshot",
        "legacy_raw_name_missing",
    }:
        for invocation in (declaration[0], execution[0], inventory[0]):
            classification = invocation["classification"]
            if defect == "snapshot_hash":
                classification["snapshot"]["snapshot_hash"] = "0" * 64
            elif defect == "missing_provided_hash":
                classification["snapshot"].pop("alias_binding_hash")
            elif defect in {"unregistered_snapshot", "write_snapshot"}:
                snapshot = restore_captured_tool_spec_snapshot(classification["snapshot"])
                assert snapshot is not None
                if defect == "unregistered_snapshot":
                    snapshot = replace(snapshot, registered=False)
                else:
                    snapshot = replace(
                        snapshot,
                        canonical_effective_spec=FrozenMapV1(
                            (FrozenMapEntryV1("category", FrozenScalarV1("string", "write")),)
                        ),
                    )
                classification["snapshot"] = json.loads(json.dumps(asdict(snapshot)))
            elif defect == "classification_raw_name":
                classification["raw_tool_name"] = "foreign-raw-tool"
            elif defect == "normalization_zero":
                classification["normalization_required"] = 0
            elif defect == "explicit_null_snapshot":
                classification["snapshot"] = None
            else:
                classification.pop("snapshot")
                classification["raw_tool_name"] = invocation["raw_tool_name"] = None
        if defect == "legacy_raw_name_missing":
            owner["decision"]["metadata"]["native_tool_call_envelopes"][0]["tool_name"] = None
    elif defect == "native_index_bool":
        owner["decision"]["metadata"]["native_tool_call_envelopes"][0]["index"] = False
    elif defect == "input_bool_vs_int":
        execution[0]["arguments"]["start_line"] = True
        execution[0]["native_arguments"]["start_line"] = True
        owner["decision"]["metadata"]["native_tool_call_envelopes"][0]["arguments_hash"] = stable_hash(
            execution[0]["native_arguments"]
        )
    elif defect == "inventory_input":
        inventory[0]["arguments"]["start_line"] = 7
    elif defect == "duplicate_call":
        execution[1] = deepcopy(execution[0])
    else:
        receipt = owner["execution"]["receipt"]
        receipt["results"][0]["result"] = {"size": 1}
        receipt["raw_results"][0]["result"] = {"size": True}
        lifecycle["batch_receipt_refs"] = [{"batch_id": receipt["batch_id"], "receipt_hash": stable_hash(receipt)}]
        lifecycle["batch_receipt_hash"] = stable_hash([receipt])
    payload["outcome_hash"] = stable_hash(owner)
    _append(tmp_path, payload)
    observed = query_committed_turn_receipts(tmp_path, lifecycle_receipts=[lifecycle])[0]
    assert observed.status == "unknown", (defect, observed)


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["v2", "v3"])
async def test_actual_v2_v3_recommit_keeps_outcome_bytes_and_does_not_rerun_io(tmp_path: Path, protocol: str) -> None:
    from polaris.cells.events.fact_stream.public import FactStreamProvenanceV1
    from polaris.cells.roles.kernel.public.outcome_recovery import (
        QueryPreparedTurnOutcomeV1,
        ReplayPreparedTurnOutcomeCommandV1,
        query_prepared_turn_outcome,
        replay_prepared_turn_outcome,
    )

    data = await produce_readonly_owner_case(
        tmp_path, reordered=True, mixed_serializers=True, protocol=protocol, recommit=True
    )
    durable_receipt = data["commit_receipt"]
    assert durable_receipt.fact_stream == (
        "roles.kernel.turn_outcomes" if protocol == "v2" else "roles.kernel.turn_outcomes.v3"
    )
    again = data["commit_receipts"][1]
    provenance = FactStreamProvenanceV1(
        workspace=str(tmp_path),
        run_id=data["attempt"].run_id,
        task_id=data["attempt"].external_task_id,
        turn_id="opaque-layout-turn",
        transition_id="opaque-layout-transition",
    )
    prepared = query_prepared_turn_outcome(QueryPreparedTurnOutcomeV1(provenance))
    assert prepared is not None
    recovered = replay_prepared_turn_outcome(
        ReplayPreparedTurnOutcomeCommandV1(
            prepared=prepared,
            expected_head_seq=durable_receipt.fact_event_seq,
            expected_session_id=data["attempt"].session_id,
        )
    )
    assert len(data["calls"]) == 3
    for receipt in (again, recovered.receipt):
        assert (receipt.fact_event_id, receipt.fact_event_seq, receipt.outcome_hash) == (
            durable_receipt.fact_event_id,
            durable_receipt.fact_event_seq,
            durable_receipt.outcome_hash,
        )
    observed = _cold_owner_query(tmp_path, data["lifecycle"])
    assert observed["status"] == "observed", observed["reason"]
    assert observed["outcome_hash"] == durable_receipt.outcome_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [3, 9])
async def test_complete_owner_snapshot_never_recaptures_current_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    data = await produce_readonly_owner_case(tmp_path, count=count, reordered=count == 3, mixed_serializers=True)
    calls = []

    def deny(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("current_registry_must_not_classify_owner_history")

    monkeypatch.setattr(ToolSpecRegistry, "get", deny)
    monkeypatch.setattr(ToolSpecRegistry, "get_canonical", deny)
    observed = query_committed_turn_receipts(tmp_path, lifecycle_receipts=[data["lifecycle"]])[0]
    assert observed.status == "observed", observed.reason
    assert calls == []


def test_legacy_without_snapshot_keeps_restrictive_registry_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.control_plane.run_ledger.tests.test_committed_turn_receipts import _append, _fixture
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    payload, lifecycle = _fixture(tmp_path)
    _append(tmp_path, payload)
    assert query_committed_turn_receipts(tmp_path, lifecycle_receipts=[lifecycle])[0].status == "observed"
    calls = []

    def deny(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("legacy_registry_proof_unavailable")

    monkeypatch.setattr(ToolSpecRegistry, "get_canonical", deny)
    observed = query_committed_turn_receipts(tmp_path, lifecycle_receipts=[lifecycle])[0]
    assert observed.status == "unknown" and calls


@pytest.mark.parametrize(
    ("case", "value", "valid"),
    [
        ("absent", None, True),
        ("null", None, True),
        ("false", False, False),
        ("zero", 0, False),
        ("empty", "", True),
        ("list", [], False),
        ("mapping", {}, False),
        ("wrong", "0" * 64, False),
    ],
)
def test_provided_whole_list_hash_is_never_a_falsey_escape(
    tmp_path: Path,
    case: str,
    value: Any,
    valid: bool,
) -> None:
    from polaris.cells.control_plane.run_ledger.tests.test_committed_turn_receipts import _append, _fixture

    payload, lifecycle = _fixture(tmp_path)
    if case != "absent":
        lifecycle["batch_receipt_hash"] = value
    _append(tmp_path, payload)
    observed = query_committed_turn_receipts(tmp_path, lifecycle_receipts=[lifecycle])[0]
    assert (observed.status == "observed") is valid, (case, observed)


def test_same_batch_id_distinct_hashes_support_independent_known_serializers(tmp_path: Path) -> None:
    from copy import deepcopy

    from polaris.cells.control_plane.run_ledger.tests.test_committed_turn_receipts import _append

    payload, lifecycle = _protocol_owner_with_snapshot(tmp_path)
    receipt = payload["outcome"]["execution"]["receipt"]
    components = []
    for index in range(2):
        one = BatchReceipt.model_validate(
            {
                **receipt,
                "results": [receipt["results"][index]],
                "raw_results": [receipt["raw_results"][index]],
                "success_count": 1,
                "failure_count": 0,
            }
        ).model_dump(mode="json")
        if index:
            # The exact complete legacy runtime wire, not arbitrary null subsets.
            one = deepcopy(one)
            one.pop("effect_receipts")
            for field in ("effect_receipt_commit", "directed_effect_mutation_status", "directed_effect_claim_status"):
                one["results"][0].pop(field)
        components.append(one)
    lifecycle["batch_receipt_refs"] = [
        {"batch_id": item["batch_id"], "receipt_hash": stable_hash(item)} for item in components
    ]
    lifecycle["batch_receipt_hash"] = stable_hash(components)
    assert components[0]["batch_id"] == components[1]["batch_id"]
    assert lifecycle["batch_receipt_refs"][0]["receipt_hash"] != lifecycle["batch_receipt_refs"][1]["receipt_hash"]
    payload["outcome_hash"] = stable_hash(payload["outcome"])
    _append(tmp_path, payload)
    observed = query_committed_turn_receipts(tmp_path, lifecycle_receipts=[lifecycle])[0]
    assert observed.status == "observed", observed.reason
    assert observed.batch_receipt_refs == tuple((item["batch_id"], stable_hash(item)) for item in components)


@pytest.mark.parametrize(
    "layout",
    [
        "matching",
        "reversed_groups",
        "inventory_only",
        "argument_mismatch",
        "native_argument_mismatch",
        "classification_mismatch",
        "typed_argument_mismatch",
        "missing_member",
        "extra_member",
        "duplicate_member",
    ],
)
def test_historical_owner_original_decision_inventory_must_match_execution_groups(tmp_path: Path, layout: str) -> None:
    """Read counterfactual historical bytes; never dispatch an invalid tool batch."""
    from copy import deepcopy

    from polaris.cells.control_plane.run_ledger.tests.test_committed_turn_receipts import _append

    payload, lifecycle = _protocol_owner_with_snapshot(tmp_path)
    owner = payload["outcome"]
    decision_batch = owner["decision"]["tool_batch"]
    declared = decision_batch["invocations"]
    grouped = deepcopy(declared)
    if layout == "reversed_groups":
        grouped.reverse()
    elif layout == "argument_mismatch":
        grouped[0]["arguments"]["file"] = "different-historical-input.py"
    elif layout == "native_argument_mismatch":
        grouped[0]["native_arguments"]["file"] = "different-historical-native.py"
    elif layout == "classification_mismatch":
        grouped[0]["classification"]["normalization_required"] = True
    elif layout == "typed_argument_mismatch":
        assert declared[0]["arguments"]["start_line"] == 1
        grouped[0]["arguments"]["start_line"] = True
    elif layout == "missing_member":
        grouped.pop()
    elif layout == "extra_member":
        extra = deepcopy(grouped[0])
        extra["call_id"] = "opaque-extra-historical-call"
        grouped.append(extra)
    elif layout == "duplicate_member":
        grouped.append(deepcopy(grouped[0]))
    if layout != "inventory_only":
        decision_batch["parallel_readonly"] = grouped
        decision_batch["readonly_serial"] = []
        decision_batch["serial_writes"] = []
        decision_batch["async_receipts"] = []
    payload["outcome_hash"] = stable_hash(owner)
    _append(tmp_path, payload)
    before = {str(path): path.read_bytes() for path in (tmp_path / ".polaris" / "runtime").rglob("*") if path.is_file()}
    observed = query_committed_turn_receipts(tmp_path, lifecycle_receipts=[lifecycle])[0]
    valid = layout in {"matching", "reversed_groups", "inventory_only"}
    assert observed.status == ("observed" if valid else "unknown"), (layout, observed)
    if not valid:
        assert observed.reason.startswith("tool_batch_inventory_"), observed.reason
    after = {str(path): path.read_bytes() for path in (tmp_path / ".polaris" / "runtime").rglob("*") if path.is_file()}
    assert after == before
