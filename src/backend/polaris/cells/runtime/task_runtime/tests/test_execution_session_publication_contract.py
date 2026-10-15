"""Public task-session publication must survive compact heartbeat facts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
    query_fact_events,
)
from polaris.cells.factory.pipeline.internal.factory_stage_executor._mixin_00 import _Mixin00
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    TaskRuntimeService,
    bind_runtime_task_to_factory_run,
)


def _active_then_heartbeat(root: Path, extra_metadata=None):
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(root), streams=fact_stream_bootstrap_streams(), maintenance_reason="M07-public-producer"
        )
    )
    service = TaskRuntimeService(str(root))
    task = service.create_task_row(
        subject="M07 ordinary active task",
        metadata={"m07_static_marker": {"origin": "committed-create-fact"}, **(extra_metadata or {})},
    )
    bound = bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(str(root), str(task["id"]), "factory-M07")
    )
    assert bound.ok
    claim = service.claim_execution(
        task["id"],
        worker_id="director",
        role_id="director",
        run_id="director-M07",
        external_task_id="TASK-M07",
        selection_source="private-module-experiment",
    )
    assert claim["success"]
    before = TaskRuntimeService(str(root)).query_observable_task_rows_projection()
    assert before.authoritative and before.readiness["ready"] is True
    assert before.rows[0]["subject"] == "M07 ordinary active task"
    assert before.rows[0]["metadata"]["m07_static_marker"] == {"origin": "committed-create-fact"}
    preserved_before = json.dumps(list(before.rows), sort_keys=True, ensure_ascii=False)
    heartbeat = service.heartbeat_execution(
        task["id"], session_id=claim["session"]["session_id"], lease_ttl_seconds=120
    )
    assert heartbeat["success"]
    assert json.dumps(list(before.rows), sort_keys=True, ensure_ascii=False) == preserved_before
    facts = query_fact_events(
        QueryFactEventsV1(workspace=str(root), stream="task_runtime.execution", strict_integrity=True, limit=100)
    )
    event = facts.events[-1]
    assert event["event_type"] == "heartbeat_renewed"
    after = TaskRuntimeService(str(root)).query_observable_task_rows_projection()
    assert json.dumps(list(before.rows), sort_keys=True, ensure_ascii=False) == preserved_before
    observation = {
        "claim": claim,
        "heartbeat": heartbeat,
        "last_fact": event,
        "before": {"source": before.source, "authoritative": before.authoritative},
        "after": {
            "source": after.source,
            "authoritative": after.authoritative,
            "readiness": dict(after.readiness),
            "rows": list(after.rows),
        },
    }
    (root / "observations.json").write_text(
        json.dumps(observation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return claim, heartbeat, event, after


def test_active_public_heartbeat_keeps_current_owner_in_durable_projection(tmp_path: Path):
    _, heartbeat, event, projection = _active_then_heartbeat(tmp_path)
    binding = event["payload"]["task_row_snapshot"]["metadata"].get("runtime_execution")
    assert isinstance(binding, dict), "Producer heartbeat snapshot lost the actual persisted session owner"
    for key in (
        "session_id",
        "task_id",
        "attempt",
        "run_id",
        "worker_id",
        "role_id",
        "last_heartbeat_at",
        "lease_expires_at",
    ):
        assert binding[key] == heartbeat["session"][key]
    assert projection.authoritative and projection.source == "task_runtime.execution_fact", projection.readiness


def test_factory_strict_consumer_accepts_real_active_publication(tmp_path: Path):
    _active_then_heartbeat(tmp_path)
    rows, error = _Mixin00._query_observable_task_rows(
        SimpleNamespace(workspace=tmp_path), factory_run_id="factory-M07"
    )
    assert error is None, error
    assert len(rows) == 1
    assert rows[0]["metadata"]["runtime_execution"]["session_id"]


def test_heartbeat_keeps_previous_committed_static_task_state(tmp_path: Path):
    _, _, event, projection = _active_then_heartbeat(tmp_path)
    assert "m07_static_marker" not in event["payload"]["task_row_snapshot"]["metadata"]
    row = projection.rows[0]
    assert row.get("subject") == "M07 ordinary active task", "A compact heartbeat must not erase committed task state"
    assert row["metadata"].get("m07_static_marker") == {"origin": "committed-create-fact"}


def _events(root: Path):
    return query_fact_events(
        QueryFactEventsV1(workspace=str(root), stream="task_runtime.execution", strict_integrity=True, limit=100)
    ).events


def _append_simulated_heartbeat(root: Path, event, *, missing_runtime=False, foreign=None):
    """Defensive fixture only: append a declared malformed/late owner fact."""
    from polaris.cells.events.fact_stream.public import AppendFactEventCommandV1, append_fact_event

    payload = deepcopy(event["payload"])
    key = "m07-simulated-" + str(missing_runtime) + str(foreign)
    payload["idempotency_key"] = key
    binding = payload["task_row_snapshot"]["metadata"].get("runtime_execution")
    if missing_runtime:
        payload["task_row_snapshot"]["metadata"].pop("runtime_execution", None)
    if foreign:
        binding[foreign] = binding[foreign] + 1 if foreign == "attempt" else "foreign-" + str(binding[foreign])
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(root),
            stream="task_runtime.execution",
            event_type="heartbeat_renewed",
            payload=payload,
            source="runtime.task_runtime",
            run_id=payload["run_id"],
            task_id=payload["task_id"],
            idempotency_key=key,
            strict_integrity=True,
        )
    )


def _settle(root: Path, claim, outcome):
    from polaris.cells.runtime.task_runtime.public import (
        SettleTaskRuntimeExecutionAttemptCommandV1,
        TaskRuntimeExecutionAttemptIdentityV1,
        settle_task_runtime_execution_attempt_typed,
    )

    verdict = settle_task_runtime_execution_attempt_typed(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(root),
            identity=TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"]),
            outcome=outcome,
            summary="M07 real public settlement",
        )
    )
    assert verdict.success, verdict


def test_cold_process_and_small_pages_rebuild_the_same_current_owner(tmp_path: Path):
    _, heartbeat, event, _projection = _active_then_heartbeat(tmp_path)
    before_bytes = next(tmp_path.rglob("task_runtime.execution.jsonl")).read_bytes()
    code = """import json,sys
from polaris.cells.runtime.task_runtime.public import TaskRuntimeService
s=TaskRuntimeService(sys.argv[1])
rows=s.list_task_rows_from_execution_facts(limit=2)
p=s.query_observable_task_rows_projection()
print(json.dumps({"rows":rows,"source":p.source,"ready":p.authoritative},ensure_ascii=False))
"""
    child = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=dict(os.environ),
        timeout=60,
        check=True,
    )
    result = json.loads(child.stdout)
    assert result["ready"] is True and result["source"] == "task_runtime.execution_fact"
    row = result["rows"][0]
    assert row["subject"] == "M07 ordinary active task"
    assert row["metadata"]["m07_static_marker"] == {"origin": "committed-create-fact"}
    for key in (
        "session_id",
        "task_id",
        "attempt",
        "run_id",
        "worker_id",
        "role_id",
        "last_heartbeat_at",
        "lease_expires_at",
    ):
        assert row["metadata"]["runtime_execution"][key] == heartbeat["session"][key]
    assert row["fact_event_seq"] == event["seq"]
    assert next(tmp_path.rglob("task_runtime.execution.jsonl")).read_bytes() == before_bytes


def test_compact_heartbeat_preserves_static_body_without_repeating_it(tmp_path: Path):
    _, _, event, projection = _active_then_heartbeat(tmp_path, {"m07_large_unrelated_audit": "X" * 150000})
    raw = event["payload"]["task_row_snapshot"]
    assert "m07_large_unrelated_audit" not in raw["metadata"]
    assert len(json.dumps(event["payload"], ensure_ascii=False).encode("utf-8")) < 8192
    assert projection.rows[0]["metadata"]["m07_large_unrelated_audit"] == "X" * 150000
    assert projection.rows[0]["metadata"]["task_runtime_execution_fact"]["task_row_snapshot"] == raw
    actual = _events(tmp_path)[-1]
    assert actual["integrity_digest"] == event["integrity_digest"] and actual["seq"] == event["seq"]
    assert actual["payload"] == event["payload"]


def test_legacy_missing_runtime_heartbeat_does_not_inherit_owner_authority(tmp_path: Path):
    _, _, event, _ = _active_then_heartbeat(tmp_path)
    _append_simulated_heartbeat(tmp_path, event, missing_runtime=True)
    projection = TaskRuntimeService(str(tmp_path)).query_observable_task_rows_projection()
    assert projection.authoritative is False
    assert projection.rows[0]["metadata"].get("runtime_execution") is None
    assert projection.rows[0]["subject"] == "M07 ordinary active task"


@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_public_terminal_owner_cannot_be_revived_by_a_late_heartbeat(tmp_path: Path, outcome):
    claim, _, event, _ = _active_then_heartbeat(tmp_path)
    _settle(tmp_path, claim, outcome)
    service = TaskRuntimeService(str(tmp_path))
    before = len(_events(tmp_path))
    denied = service.heartbeat_execution(claim["session"]["task_id"], session_id=claim["session"]["session_id"])
    assert denied["success"] is False and len(_events(tmp_path)) == before
    terminal = service.query_observable_task_rows_projection()
    assert terminal.authoritative and terminal.rows[0]["status"] == outcome
    _append_simulated_heartbeat(tmp_path, event)
    after = TaskRuntimeService(str(tmp_path)).query_observable_task_rows_projection()
    assert after.authoritative is False
    assert not any(row.get("running") and row.get("metadata", {}).get("runtime_execution") for row in after.rows)
    denied_claim = service.claim_execution(
        claim["session"]["task_id"], worker_id="director", role_id="director", run_id="new-unadmitted-run"
    )
    assert denied_claim["success"] is False and denied_claim["reason"] == "task_terminal"


@pytest.mark.parametrize("late_old", [False, True])
def test_sanctioned_new_owner_works_but_late_old_owner_never_mixes_into_it(tmp_path: Path, late_old):
    first, _, old_event, _ = _active_then_heartbeat(tmp_path)
    _settle(tmp_path, first, "completed")
    service = TaskRuntimeService(str(tmp_path))
    reopened = service.reopen_task_row(first["session"]["task_id"], reason="M07 sanctioned rework")
    assert reopened is not None
    second = service.claim_execution(
        first["session"]["task_id"],
        worker_id="director",
        role_id="director",
        run_id="director-M07-B",
        external_task_id="TASK-M07",
    )
    assert second["success"] and second["session"]["session_id"] != first["session"]["session_id"]
    assert second["session"]["attempt"] > first["session"]["attempt"]
    heartbeat = service.heartbeat_execution(second["session"]["task_id"], session_id=second["session"]["session_id"])
    assert heartbeat["success"]
    current = TaskRuntimeService(str(tmp_path)).query_observable_task_rows_projection()
    assert current.authoritative
    assert current.rows[0]["metadata"]["runtime_execution"]["session_id"] == second["session"]["session_id"]
    if late_old:
        _append_simulated_heartbeat(tmp_path, old_event)
        after = TaskRuntimeService(str(tmp_path)).query_observable_task_rows_projection()
        assert after.authoritative is False
        assert not any(
            row.get("metadata", {}).get("runtime_execution", {}).get("session_id") == first["session"]["session_id"]
            for row in after.rows
        )


@pytest.mark.parametrize("foreign", ["session_id", "run_id", "attempt"])
def test_contradictory_current_heartbeat_binding_is_not_accepted(tmp_path: Path, foreign):
    _, _, event, _ = _active_then_heartbeat(tmp_path)
    _append_simulated_heartbeat(tmp_path, event, foreign=foreign)
    after = TaskRuntimeService(str(tmp_path)).query_observable_task_rows_projection()
    assert after.authoritative is False
