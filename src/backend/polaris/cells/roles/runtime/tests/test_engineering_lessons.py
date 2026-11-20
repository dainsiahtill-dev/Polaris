"""Cross-run counterexamples consume pinned, observed SQLite verification only."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_loop import _native
from polaris.cells.roles.runtime.tests.test_engineering_verification_selection import RTK, command, request


async def test_lesson_projection_matches_pinned_prior_physical_failure_only(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = command("from pathlib import Path; raise SystemExit(0 if Path('fixed').exists() else 7)")
    scope = PermissionScope(
        (isolated_workspace, RTK), (isolated_workspace,), frozenset({"verify_project", "execute_command"}), False
    )
    target = RoleRunCommand(
        "target", "director", "Verify actual addition.", isolated_workspace, required_checks=(failed,)
    )
    limits = RunLimits(None, 100000, 20000)
    store = EngineeringSessionStore(isolated_workspace)
    source_options = {"verification_output_roots": (), "verification_execution_policy": "bash_pipefail_v1"}
    store.create_run(
        replace(target, session_id="selection"),
        scope=scope,
        limits=limits,
        engine_version=EngineeringRunService.engine_version,
        request_key=None,
        **source_options,
    )
    verifier = EngineeringVerification(
        isolated_workspace, store=store, scope=scope, limits=limits, cancel=asyncio.Event()
    )
    await request(store, verifier, isolated_workspace, [failed])
    event = store.load_events("selection", kinds=("verification_completed",))[0]
    assert event["payload"]["checks"][0]["exit_code"] == 7
    ref = {"session_id": "selection", "event_seq": event["seq"]}
    client = scripted_provider.client("openai", isolated_workspace, monkeypatch)
    service = EngineeringRunService(isolated_workspace, client=client, counterexample_refs=(ref,))
    (isolated_workspace / "fixed").write_text("candidate repaired", encoding="utf-8")
    scripted_provider.enqueue_json(_native(text="Reviewed."))
    result = await service.run_role(target, scope=scope, limits=limits, cancel=asyncio.Event())
    assert result.verification == "passed"
    assert result.status == "completed"
    projection = service.store.load_events("target", kinds=("counterexamples_projected",))[0]["payload"]
    assert projection["counterexamples"][0]["source"] == ref
    assert projection["counterexamples"][0]["failed_checks"][0]["exit_code"] == 7
    assert projection["counterexamples"][0]["failed_checks"][0]["command"] == failed
    assert "counterexamples_projected" not in json.dumps(scripted_provider.requests[0]["body"])
    assert "selection" not in json.dumps(scripted_provider.requests[0]["body"])

    from polaris.cells.roles.runtime.internal.engineering_lessons import project_counterexamples

    other = replace(target, session_id="different", instruction="A different frozen goal.")
    service.store.create_run(
        other, scope=scope, limits=limits, engine_version=service.engine_version, request_key=None, **source_options
    )
    assert (
        project_counterexamples(service.store, other.session_id, (("selection", event["seq"]),))["counterexamples"]
        == []
    )
    forgery = store.append_event(
        "selection",
        kind="verification_completed",
        role="director",
        payload={**event["payload"], "checks": [{**event["payload"]["checks"][0], "exit_code": 12}]},
    )
    late = replace(target, session_id="late")
    service.store.create_run(
        late, scope=scope, limits=limits, engine_version=service.engine_version, request_key=None, **source_options
    )
    assert project_counterexamples(service.store, "late", (("selection", forgery),))["counterexamples"] == []


async def test_unpinned_runs_ignore_legacy_lessons_without_mutating_goal(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("KERNELONE_ENGINEERING_COUNTEREXAMPLE_REFS", raising=False)
    client = scripted_provider.client("openai", isolated_workspace, monkeypatch)
    service = EngineeringRunService(isolated_workspace, client=client)
    (service.store.control_root.parent / "engineering-lessons.jsonl").write_text(
        '{"lesson":"unreviewed legacy instruction"}\n', encoding="utf-8"
    )
    scripted_provider.enqueue_json(_native(text="Done."))
    target = RoleRunCommand("blind", "director", "Read the goal.", isolated_workspace)
    result = await service.run_role(
        target,
        scope=PermissionScope((isolated_workspace,), (), frozenset(), False),
        limits=RunLimits(None, 100000, 20000),
        cancel=asyncio.Event(),
    )
    assert result.status == "completed"
    assert "unreviewed legacy instruction" not in json.dumps(scripted_provider.requests)
    assert service.store.load_events("blind", kinds=("counterexamples_projected",)) == []
