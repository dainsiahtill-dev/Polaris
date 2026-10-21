"""Native check choices remain model-owned; fixed user checks remain mandatory.

Only model responses are synthetic. Real public-service execution and a build
output counter expose any hidden finalizer dispatch. No Bench delivery claim.
"""

from __future__ import annotations

import json
import shlex
import time
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _response, _scope


async def test_source_change_requires_new_native_verification_instead_of_implicit_replay(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale selected recipe must not gain a second effect from text completion."""
    (isolated_workspace / "dist").mkdir()
    (isolated_workspace / "source.txt").write_text("v1", encoding="utf-8")
    required = (
        "python3 -c \"from pathlib import Path; assert Path('source.txt').read_text(encoding='utf-8') in ('v1','v2')\""
    )
    code = "from pathlib import Path; p=Path('dist/check-count'); n=int(p.read_text(encoding='utf-8'))+1 if p.exists() else 1; p.write_text(str(n),encoding='utf-8')"
    selected = "python3 -c " + shlex.quote(code)
    for response in (
        _response(("native-v1", "verify_project", {"commands": [selected]})),
        _response(("change-source", "write_file", {"file": "source.txt", "content": "v2"})),
        _response(text="Changed the candidate after checking it."),
        _response(("native-v2", "verify_project", {"commands": [selected]})),
        _response(text="Current candidate explicitly checked."),
        _response(("qa-source", "read_file", {"file": "source.txt"})),
        _response(
            (
                "qa-review",
                "report_review",
                {"approved": True, "feedback": "Read current v2 source and actual current checks."},
            )
        ),
    ):
        scripted_provider.enqueue_json(response)
    client = scripted_provider.client("openai", isolated_workspace, monkeypatch)
    scope = _scope(isolated_workspace)
    service = EngineeringRunService(
        isolated_workspace,
        require_qa=True,
        client=client,
        permission_resolver=lambda _row: scope,
        writable_output_roots=(isolated_workspace / "dist",),
    )
    command = RoleRunCommand(
        "selection-owner",
        "director",
        "Change source v1 to v2 and explicitly check the new candidate.",
        isolated_workspace,
        required_checks=(required,),
    )
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 30, 1_000_000, 64_000))
    await service.wait(command.session_id, workspace=isolated_workspace)
    events = service.store.load_events(command.session_id)
    starts = [event for event in events if event["kind"] == "verification_started"]
    assert all(event["payload"]["input_binding"].get("requested_by") is not None for event in starts), starts
    assert len(starts) == 2 and len(scripted_provider.requests) == 7
    assert (isolated_workspace / "dist/check-count").read_text(encoding="utf-8") == "2"
    assert all(event["payload"]["input_binding"]["commands"] == [required, selected] for event in starts)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["status"] == "completed" and row["verification"] == "passed"
    assert row["qa_verdict"] == "approved" and row["project_outcome"] == "accepted"
    assert row["required_checks"] == [required] and not service.store.pending_tool_calls(command.session_id)
    assert [event["role"] for event in events if event["kind"] == "role_started"] == ["director", "director", "qa"]


async def test_failed_selected_checks_keep_their_origin_in_same_director_feedback(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed optional choice cannot be relabeled an immutable host requirement."""
    required = "python3 -c \"print('HOST_REQUIRED')\""
    bad = 'python3 -c "raise SystemExit(9)"'
    good = "python3 -c \"print('CURRENT_CHECK')\""
    for response in (
        _response(("bad-choice", "verify_project", {"commands": [bad]})),
        _response(text="Inspect the failed selected check."),
        _response(("corrected-choice", "verify_project", {"commands": [good]})),
        _response(),
    ):
        scripted_provider.enqueue_json(response)
    client = scripted_provider.client("openai", isolated_workspace, monkeypatch)
    scope = _scope(isolated_workspace)
    service = EngineeringRunService(isolated_workspace, client=client, permission_resolver=lambda _row: scope)
    command = RoleRunCommand(
        "choice-feedback",
        "director",
        "Deliver with actual checks; preserve the host-required command.",
        isolated_workspace,
        required_checks=(required,),
    )
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 30, 1_000_000, 64_000))
    await service.wait(command.session_id, workspace=isolated_workspace)
    feedback = next(
        message["content"]
        for message in reversed(scripted_provider.requests[2]["body"]["messages"])
        if message["role"] == "user"
    )
    facts = json.loads(feedback.split("\n", 1)[1])
    assert facts["required_checks"] == [required]
    assert facts["verification_commands"] == [required, bad]
    assert facts["checks"][1]["result"]["exit_code"] == 9
    # The actual provider request receives the same origin distinction.
    body = json.dumps(scripted_provider.requests[2]["body"], ensure_ascii=False)
    assert json.dumps(facts, ensure_ascii=False) in body or json.dumps(facts, ensure_ascii=False) in "\n".join(
        message.get("content", "")
        for message in scripted_provider.requests[2]["body"]["messages"]
        if isinstance(message.get("content"), str)
    )
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["verification"] == "passed" and row["required_checks"] == [required]
    assert row["verification_commands"] == [required, good]
