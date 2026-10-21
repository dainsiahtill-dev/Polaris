"""Verification feedback reflects its actual scope without changing authority.

Only the remote model is replaced with finite native HTTP responses. The
readonly denial, ordinary preparation, checks and current QA use real tools.
This fixture cannot prove that a remote model understands the guidance.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.internal.engineering_verification import verification_observation
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _response, _scope, _service


async def test_failed_verification_reports_actual_scope_then_ordinary_preparation_can_progress(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting the bound execution scope must fail even if later work succeeds."""
    (isolated_workspace / "artifact.txt").write_text("2", encoding="utf-8")
    (isolated_workspace / "dependency.txt").write_text("old", encoding="utf-8")
    required = "python3 -c \"from pathlib import Path; assert Path('artifact.txt').read_text(encoding='utf-8')=='2'\""
    prepare = "python3 -c \"from pathlib import Path; Path('dependency.txt').write_text('prepared',encoding='utf-8')\""
    check = (
        "python3 -c \"from pathlib import Path; assert Path('dependency.txt').read_text(encoding='utf-8')=='prepared'\""
    )
    for response in (
        _response(("bad-recipe", "verify_project", {"commands": [prepare]})),
        _response(("prepare", "execute_command", {"command": prepare})),
        _response(("current-check", "verify_project", {"commands": [check]})),
        _response(),
        _response(
            ("qa-artifact", "read_file", {"file": "artifact.txt"}),
            ("qa-dependency", "read_file", {"file": "dependency.txt"}),
        ),
        _response(
            (
                "qa-review",
                "report_review",
                {"approved": True, "feedback": "Read artifact2 and prepared dependency, with actual current checks."},
            )
        ),
    ):
        scripted_provider.enqueue_json(response)
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    command = RoleRunCommand(
        "prepare-then-verify",
        "director",
        "Prepare dependencies and deliver artifact2.",
        isolated_workspace,
        required_checks=(required,),
    )
    await service.start(
        command, scope=_scope(isolated_workspace), limits=RunLimits(time.time() + 45, 1_000_000, 64_000)
    )
    await service.wait(command.session_id, workspace=isolated_workspace)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["project_outcome"] == "accepted" and row["qa_verdict"] == "approved", row
    assert row["required_checks"] == [required] and row["verification_commands"] == [required, check]
    events = service.store.load_events(command.session_id)
    completed = [event["payload"] for event in events if event["kind"] == "verification_completed"]
    assert [event["verification"] for event in completed] == ["failed", "passed"]
    assert completed[0]["checks"][0]["ok"] is True and completed[0]["checks"][1]["ok"] is False
    failed = next(
        event["payload"]
        for event in events
        if event["kind"] == "tool_observed" and event["payload"]["call_id"] == "bad-recipe"
    )
    summary = json.loads(failed["content"])
    expected_scope = {
        "read_roots": [str(isolated_workspace)],
        "output_roots": [],
        "execute_command": True,
        "network_allowed": False,
    }
    assert summary["execution_scope"] == expected_scope
    # Inspect the real serialized request after the failed command, rather than
    # a semantic/prompt projection or a synthetic success response.
    message = next(
        message
        for message in scripted_provider.requests[1]["body"]["messages"]
        if message.get("tool_call_id") == "bad-recipe"
    )
    observed_feedback = json.loads(json.loads(message["content"])["content"])
    assert observed_feedback["execution_scope"] == expected_scope
    assert (isolated_workspace / "dependency.txt").read_text(encoding="utf-8") == "prepared"
    ordinary = next(
        event["payload"]
        for event in events
        if event["kind"] == "tool_observed" and event["payload"]["call_id"] == "prepare"
    )
    assert ordinary["ok"] is True and ordinary["exit_code"] == 0


def test_absent_historical_scope_stays_unknown_without_reclassifying_actual_failure() -> None:
    payload = {
        "verification": "failed",
        "input_binding": {"commands": ["failed-check"]},
        "checks": [{"ok": False, "exit_code": 1}],
        "detail": "Real check failed.",
    }
    observation = verification_observation("old-observation", payload, 64_000)
    summary = json.loads(observation.content)
    assert summary["execution_scope"] is None
    assert summary["verification"] == "failed" and observation.ok is False and observation.interrupted is False
