"""Host-pinned routing uses independent observations and a strict binding subset."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.internal import engineering_qualified_routing as routing
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)


def _trial(index: int, cost: float, provider: str):
    run = f"{provider}-{index}"
    common = {"run_id": run, "project_id": str(index), "plan_sha256": "b" * 64}
    return {
        "project": str(index),
        "run_id": run,
        "project_outcome": "accepted",
        "budget_seconds": 30,
        "elapsed_seconds": 12,
        "artifact_sha256": "c" * 64,
        "review": {
            **common,
            "kind": "external_full_contract_review",
            "conditions_sha256": "a" * 64,
            "verdict": "PASS",
            "artifact_sha256": "c" * 64,
            "protocol_sha256": "d" * 64,
            "reviewer": "independent-audit",
        },
        "bill": {
            **common,
            "kind": "settled_billing",
            "currency": "USD",
            "all_attempts": True,
            "amount": cost,
            "provider_id": provider,
            "model": "version",
        },
    }


def _evidence():
    return {
        "task_class": "cli-maintenance",
        "plan_sha256": "b" * 64,
        "protocol_sha256": "d" * 64,
        "reviewer": "independent-audit",
        "goals": [hashlib.sha256(b"goal").hexdigest()],
        "conditions_sha256": "a" * 64,
        "minimum_remaining_seconds": 12,
        "maximum_remaining_seconds": 60,
        "minimum_success_rate": 0.95,
        "fixed_sample_size": 80,
        "projects": [str(i) for i in range(80)],
        "bindings": [
            {"provider_id": name, "model": "version", "trials": [_trial(i, cost, name) for i in range(80)]}
            for name, cost in [("primary", 3), ("cheap", 1), ("foreign", 0)]
        ],
    }


def test_only_qualified_allowed_cheaper_binding_selected() -> None:
    record = _evidence()
    scope = (("primary", "version"), ("cheap", "version"))
    result = routing.choose_qualified_bindings(record, scope, goal="goal", conditions_sha256="a" * 64, seconds_left=25)
    assert result == (scope[1], scope[0])
    for changed in ["different goal", " "]:
        assert not routing.choose_qualified_bindings(
            record, scope, goal=changed, conditions_sha256="a" * 64, seconds_left=25
        )
    assert not routing.choose_qualified_bindings(
        record, scope, goal="goal", conditions_sha256="b" * 64, seconds_left=25
    )
    assert not routing.choose_qualified_bindings(record, scope, goal="goal", conditions_sha256="a" * 64, seconds_left=1)


@pytest.mark.parametrize(
    "corruption",
    [
        "unknown",
        "failed",
        "duplicate",
        "few",
        "bad_cost",
        "mismatched_cohort",
        "stale_artifact",
        "wrong_bill",
        "stale_conditions",
        "overtime",
        "not_released",
        "partial_bill",
    ],
)
def test_incomplete_or_unsupported_evidence_cannot_promote(corruption: str) -> None:
    record = _evidence()
    trials = record["bindings"][1]["trials"]
    if corruption == "unknown":
        trials[0]["review"]["verdict"] = None
    if corruption == "failed":
        trials[0]["review"]["verdict"] = "FAIL"
    if corruption == "duplicate":
        trials[0]["project"] = trials[-1]["project"]
    if corruption == "few":
        trials.pop()
    if corruption == "bad_cost":
        trials[0]["bill"]["amount"] = -1
    if corruption == "mismatched_cohort":
        trials[0]["project"] = "unpaired"
    if corruption == "stale_artifact":
        trials[0]["review"]["artifact_sha256"] = "0" * 64
    if corruption == "wrong_bill":
        trials[0]["bill"]["run_id"] = "other"
    if corruption == "stale_conditions":
        trials[0]["review"]["conditions_sha256"] = "0" * 64
    if corruption == "overtime":
        trials[0]["elapsed_seconds"] = 35
    if corruption == "not_released":
        trials[0]["project_outcome"] = "unknown"
    if corruption == "partial_bill":
        trials[0]["bill"]["all_attempts"] = False
    scope = (("primary", "version"), ("cheap", "version"))
    result = routing.choose_qualified_bindings(record, scope, goal="goal", conditions_sha256="a" * 64, seconds_left=25)
    assert result == (scope[0],)


def test_pinned_routing_record_detects_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "host-record.json"
    raw = json.dumps(_evidence()).encode("utf-8")
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    assert routing.load_routing_record(str(path), digest)[1] == _evidence()
    path.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="digest"):
        routing.load_routing_record(str(path), digest)


async def test_normal_service_dispatches_qualified_model_on_wire(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import time

    from polaris.cells.roles.runtime.public import engineering_service
    from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
    from polaris.cells.roles.runtime.tests.test_engineering_loop import _native
    from polaris.cells.roles.runtime.tests.test_engineering_provider_failover import _slots
    from polaris.kernelone.llm import config_store, runtime_config

    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    monkeypatch.setattr(
        engineering_service,
        "get_role_binding_candidates",
        lambda role: _slots(("synthetic", "synthetic-model"), ("cheap", "qualified-version")),
    )
    monkeypatch.setattr(runtime_config, "get_role_model", lambda role: ("synthetic", "synthetic-model"))
    monkeypatch.setattr(
        config_store,
        "load_llm_config",
        lambda *args, **kwargs: {"providers": {key: {"max_context_tokens": 32768} for key in ("synthetic", "cheap")}},
    )
    path = isolated_workspace / "host.json"
    scope = PermissionScope((isolated_workspace,), (), frozenset(), False)

    async def invoke(session: str, record: dict):
        data = json.dumps(record).encode("utf-8")
        path.write_bytes(data)
        service = engineering_service.EngineeringRunService(
            isolated_workspace,
            permission_resolver=lambda _run: scope,
            routing_record_file=str(path),
            routing_record_sha256=hashlib.sha256(data).hexdigest(),
        )
        scripted_provider.enqueue_json(_native(text="Done."))
        result = await service.run_role(
            RoleRunCommand(session, "director", "goal", isolated_workspace),
            scope=scope,
            limits=RunLimits(time.time() + 40, 100000, 20000),
            cancel=asyncio.Event(),
        )
        assert result.status == "completed", result.text
        prepared = service.store.load_events(session, kinds=("request_prepared",))
        calls_before = len(scripted_provider.requests)
        assert await service.resume(session, workspace=isolated_workspace) == session
        assert len(scripted_provider.requests) == calls_before
        assert len(service.store.load_events(session, kinds=("request_prepared",))) == len(prepared)
        return prepared[-1]["payload"]["binding_selection"]

    audit = await invoke("baseline", {})
    assert not audit["activated"]
    record = _evidence()
    record["conditions_sha256"] = audit["conditions_sha256"]
    record["bindings"][1]["model"] = "qualified-version"
    for trial in record["bindings"][1]["trials"]:
        trial["bill"]["model"] = "qualified-version"
        trial["review"]["conditions_sha256"] = audit["conditions_sha256"]
    audit = await invoke("routed", record)
    assert audit["activated"]
    assert scripted_provider.requests[-1]["body"]["model"] == "qualified-version"


def test_frozen_startup_loads_host_policy_into_owner(isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.bootstrap import engineering_runtime
    from polaris.bootstrap.config import Settings
    from polaris.delivery.cli.engineering_acceptance import _startup_environment

    record = isolated_workspace / "record.json"
    record.write_bytes(b"{}")
    digest = hashlib.sha256(record.read_bytes()).hexdigest()
    host = isolated_workspace / "host.json"
    host.write_text(
        json.dumps(
            {
                "routing_record_file": str(record),
                "routing_record_sha256": digest,
                "counterexample_refs": [{"session_id": "source", "event_seq": 18}],
            }
        ),
        encoding="utf-8",
    )
    llm = isolated_workspace / "llm.json"
    llm.write_bytes(b"{}")
    monkeypatch.setenv("KERNELONE_ENGINEERING_COUNTEREXAMPLE_REFS", "untrusted")
    monkeypatch.setattr(engineering_runtime, "_prepare_model_runtime", lambda *a: None)
    with _startup_environment(
        {"frozen_inputs": {"host_config": {"path": str(host)}, "llm_config": {"path": str(llm)}}, "registry_home": None}
    ):
        settings = Settings.from_env().model_copy(update={"workspace": str(isolated_workspace)})
        service = engineering_runtime.create_engineering_service(settings)
    assert service._routing_record == (digest, {})
    assert service._counterexample_refs == (("source", 18),)
