"""Mandatory QA handoff through real verification, browser and physical HTTP.

Only model replies are scripted; these fixtures do not establish model quality.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_roles import role_instruction
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _response
from polaris.cells.roles.runtime.tests.test_engineering_verification_visual_ordering import _pixels, _scope
from polaris.kernelone.fs import get_default_adapter

_ROOT = Path(__file__).resolve().parents[7]
_ARTIFACT = _ROOT / ".polaris/audits/director-qa-handoff-20261023/implementation"
_HTML = '<!doctype html><meta charset="utf-8"><h1>Independent QA fixture</h1>'
_CHECK = "python3 -B -c \"from pathlib import Path; assert 'Independent QA fixture' in Path('index.html').read_text(encoding='utf-8'); print('REAL_HANDOFF_CHECK')\""


def _save(name: str, value: Any) -> None:
    get_default_adapter().write_text(
        str(_ARTIFACT / f"{name}.json"), json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@pytest.mark.parametrize("explicit,capture_director", [(False, False), (True, False), (True, True)])
async def test_required_qa_handoff_keeps_current_independent_pixels_and_cold_acceptance(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    explicit: bool,
    capture_director: bool,
) -> None:
    """A repeated Director finalization fails before any QA acceptance assertion."""
    fs = get_default_adapter()
    fs.write_text(str(isolated_workspace / "index.html"), _HTML, encoding="utf-8")
    scope = _scope(isolated_workspace)
    capture = {"entry": "index.html", "viewport": {"width": 640, "height": 360}, "frame_times_ms": [0]}
    if capture_director:
        scripted_provider.enqueue_json(_response(("optional-director-capture", "browser_observe", capture)))
    if explicit:
        scripted_provider.enqueue_json(_response(("verify", "verify_project", {"commands": [_CHECK]})))
    scripted_provider.enqueue_json(_response(text="Current checks are ready; hand off to independent QA."))
    director_requests = 1 + int(explicit) + int(capture_director)
    scripted_provider.enqueue_json(_response(("qa-read", "read_file", {"file": "index.html"})))
    scripted_provider.enqueue_json(
        _response(
            ("qa-capture", "browser_observe", capture),
            ("early-approval", "report_review", {"approved": True, "feedback": "Premature same-turn approval"}),
        )
    )
    scripted_provider.enqueue_json(
        _response(
            (
                "qa-approval",
                "report_review",
                {"approved": True, "feedback": "Current source and own actual images inspected."},
            ),
        )
    )
    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    service = EngineeringRunService(isolated_workspace, require_qa=True, permission_resolver=lambda _row: scope)
    command = RoleRunCommand(
        f"qa-handoff-{explicit}-{capture_director}",
        "director",
        "Deliver the current disposable HTML page.",
        isolated_workspace,
        required_checks=() if explicit else (_CHECK,),
    )
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 45, 2_000_000, 64_000))
    await service.wait(command.session_id, workspace=isolated_workspace)
    events = service.store.load_events(command.session_id)
    prepared = [event for event in events if event["kind"] == "request_prepared"]
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    label = f"handoff-{explicit}-{capture_director}"
    _save(label, {"events": events, "requests": scripted_provider.requests, "snapshot": row})
    # Causal RED: current checks must hand off, without another Director turn.
    assert prepared[director_requests]["role"] == "qa", [event["role"] for event in prepared]
    assert row["verification"] == "passed" and row["qa_verdict"] == "approved", row
    assert row["project_outcome"] == "accepted", row
    completed = [event for event in events if event["kind"] == "verification_completed"]
    assert len(completed) == 1 and completed[0]["payload"]["verification"] == "passed"
    assert all(check["exit_code"] == 0 for check in completed[0]["payload"]["checks"])
    observations = [event for event in events if event["kind"] == "tool_observed"]
    early = next(event for event in observations if event["payload"].get("call_id") == "early-approval")
    assert early["role"] == "qa" and early["payload"]["ok"] is False
    assert "visual" in early["payload"]["content"].lower()
    qa_capture = next(event for event in observations if event["payload"].get("call_id") == "qa-capture")
    assert qa_capture["payload"]["ok"] and completed[0]["seq"] < qa_capture["seq"]
    raw = [base64.b64decode(image["data_base64"], validate=True) for image in qa_capture["payload"]["images"]]
    assert raw and all(
        hashlib.sha256(data).hexdigest() == image["sha256"]
        for data, image in zip(raw, qa_capture["payload"]["images"], strict=True)
    )
    approval_frame = prepared[director_requests + 2]
    body = scripted_provider.requests[director_requests + 2]["body"]
    assert all(data in _pixels(body) for data in raw)
    assert any(
        event["kind"] == "llm_request_observed"
        and event["role"] == "qa"
        and event["payload"].get("turn_seq") == approval_frame["payload"]["turn_seq"]
        for event in events
    )
    director_body = scripted_provider.requests[0]["body"]
    system = director_body["messages"][0]["content"]
    assert "browser_observe" in system and "independent QA" in system
    assert "before completion" not in system
    assert any(tool["function"]["name"] == "browser_observe" for tool in director_body["tools"])
    assert not any(event["role"] in {"pm", "chief_engineer"} for event in events)
    assert not service.store.pending_tool_calls(command.session_id)
    with service.store.acquire_workspace(isolated_workspace):
        pass
    # Saved run policy wins over a fresh service's constructor default.
    cold = EngineeringRunService(isolated_workspace, require_qa=False, permission_resolver=lambda _row: scope)
    for observed in (
        service.observe(command.session_id, workspace=isolated_workspace),
        cold.snapshot(command.session_id, workspace=isolated_workspace),
        cold.observe(command.session_id, workspace=isolated_workspace),
    ):
        assert observed["require_qa"] is True
        assert observed["verification"] == "passed" and observed["qa_verdict"] == "approved"
        assert observed["project_outcome"] == "accepted"
    saved = service.store.load_run(command.session_id, workspace=isolated_workspace)
    closed = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: replace(scope, browser=None))
    assert closed.snapshot(command.session_id, workspace=isolated_workspace)["project_outcome"] == "unknown"
    assert service.store.load_run(command.session_id, workspace=isolated_workspace) == saved
    fs.write_text(str(isolated_workspace / "index.html"), _HTML + "<p>outside change</p>", encoding="utf-8")
    drifted = cold.snapshot(command.session_id, workspace=isolated_workspace)
    assert drifted["verification"] == "unknown" and drifted["qa_verdict"] == "unknown"
    assert drifted["project_outcome"] == "unknown"
    assert service.store.load_run(command.session_id, workspace=isolated_workspace) == saved


@pytest.mark.parametrize("checks", [(), ("python3 -B -c 'raise SystemExit(7)'",)])
async def test_required_qa_cannot_follow_missing_or_failed_current_checks(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    checks: tuple[str, ...],
) -> None:
    get_default_adapter().write_text(str(isolated_workspace / "index.html"), _HTML, encoding="utf-8")
    scope = _scope(isolated_workspace)
    if not checks:
        scope = replace(scope, enabled_tools=scope.enabled_tools - {"execute_command", "verify_project"})
    scripted_provider.enqueue_json(_response(text="Text does not certify checks."))
    if checks:
        scripted_provider.enqueue_json({"error": "Stop after actual failed-check feedback"}, status=503)
    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    service = EngineeringRunService(isolated_workspace, require_qa=True, permission_resolver=lambda _row: scope)
    command = RoleRunCommand(
        "qa-invalid-checks", "director", "Inspect the fixture.", isolated_workspace, required_checks=checks
    )
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 30, 1_000_000, 64_000))
    await service.wait(command.session_id, workspace=isolated_workspace)
    events = service.store.load_events(command.session_id)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["verification"] == ("failed" if checks else "not_run")
    assert row["project_outcome"] != "accepted" and row["qa_verdict"] == "not_run"
    assert not any(event["kind"] == "role_started" and event["role"] == "qa" for event in events)
    assert not service.store.pending_tool_calls(command.session_id)
    if checks:
        completed = next(event for event in events if event["kind"] == "verification_completed")
        assert completed["payload"]["checks"][0]["exit_code"] == 7


def test_visual_guidance_delegates_only_when_independent_qa_is_required() -> None:
    default = role_instruction("director", visual=True, require_visual_review=True)
    assert "before completion" in default
    delegated = role_instruction("director", visual=True, require_visual_review=True, require_qa=True)
    assert "before completion" not in delegated
    assert "browser_observe" in delegated and "independent QA" in delegated
    qa = role_instruction("qa", visual=True, require_visual_review=True, require_qa=True)
    assert "QA must capture independently" in qa
