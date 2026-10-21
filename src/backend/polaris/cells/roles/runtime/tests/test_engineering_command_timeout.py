"""Real sandbox timeouts with synthetic native HTTP model responses.

Only the model port is replaced. The bounded verifier probe shortens the
physical worker's timeout, without replacing execution, drainage, or SQLite.
These fixtures do not establish remote model or Bench delivery capability.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _response, _scope, _service


async def test_drained_command_timeout_reaches_next_native_turn_and_current_qa(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command deadline must not terminate the project or replay partial effects."""
    preparation = (
        'python3 -c "from pathlib import Path; import time; '
        "p=Path('partial.txt'); p.write_text(p.read_text(encoding='utf-8')+'1' if p.exists() else '1', "
        "encoding='utf-8'); print('PARTIAL_WRITTEN', flush=True); "
        "time.sleep(10); Path('late.txt').write_text('leaked',encoding='utf-8')\""
    )
    check = "python3 -c \"from pathlib import Path; assert Path('partial.txt').read_text(encoding='utf-8')=='1'\""
    for response in (
        _response(("prepare-once", "execute_command", {"command": preparation, "timeout": 1})),
        _response(("inspect", "read_file", {"file": "partial.txt"})),
        _response(("verify", "verify_project", {"commands": [check]})),
        _response(text="Verified the surviving partial state; independently review it."),
        _response(("qa-read", "read_file", {"file": "partial.txt"})),
        _response(("qa-review", "report_review", {"approved": True, "feedback": "Current value is exactly one."})),
    ):
        scripted_provider.enqueue_json(response)
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    command = RoleRunCommand("drained-timeout", "director", "Deliver exactly one write.", isolated_workspace)
    await service.start(
        command, scope=_scope(isolated_workspace), limits=RunLimits(time.time() + 30, 1_000_000, 64_000)
    )
    await service.wait(command.session_id, workspace=isolated_workspace)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["project_outcome"] == "accepted", row
    assert row["verification"] == "passed" and row["qa_verdict"] == "approved"
    assert (isolated_workspace / "partial.txt").read_text(encoding="utf-8") == "1"
    assert not (isolated_workspace / "late.txt").exists()
    events = service.store.load_events(command.session_id)
    observed = [e["payload"] for e in events if e["kind"] == "tool_observed"]
    timeout = next(e for e in observed if e["call_id"] == "prepare-once")
    assert timeout["ok"] is False and timeout["exit_code"] is None
    assert timeout["timed_out"] is True and timeout["interrupted"] is False
    assert "PARTIAL_WRITTEN" in timeout["content"]
    next_messages = scripted_provider.requests[1]["body"]["messages"]
    feedback = next(m for m in next_messages if m.get("tool_call_id") == "prepare-once")
    assert json.loads(feedback["content"])["timed_out"] is True
    assert len([e for e in observed if e["call_id"] == "prepare-once"]) == 1
    assert row["workspace_revision"] == 1, "A partially mutating timeout must invalidate previous checks."
    assert not service.store.pending_tool_calls(command.session_id)
    cold = _service(isolated_workspace, scripted_provider, monkeypatch).snapshot(
        command.session_id, workspace=isolated_workspace
    )
    assert cold["project_outcome"] == "accepted" and cold["workspace_revision"] == 1


async def test_drained_verifier_timeout_is_failed_evidence_and_can_be_replaced(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An actually timed-out check is failed evidence; a fresh chosen check can follow."""
    from polaris.cells.roles.runtime.internal import engineering_tools

    actual_worker = engineering_tools.run_process_tree_safe

    def bounded_worker(*args: Any, **kwargs: Any) -> Any:
        kwargs["timeout"] = min(kwargs["timeout"], 1)
        return actual_worker(*args, **kwargs)

    monkeypatch.setattr(engineering_tools, "run_process_tree_safe", bounded_worker)
    (isolated_workspace / "artifact.txt").write_text("2", encoding="utf-8")
    slow = "python3 -c \"import time; print('CHECK_STARTED',flush=True); time.sleep(10)\""
    fast = "python3 -c \"from pathlib import Path; assert Path('artifact.txt').read_text(encoding='utf-8')=='2'\""
    for response in (
        _response(("slow-verify", "verify_project", {"commands": [slow]})),
        _response(("fast-verify", "verify_project", {"commands": [fast]})),
        _response(text="Current check passed; request independent review."),
        _response(("qa-read", "read_file", {"file": "artifact.txt"})),
        _response(("qa-review", "report_review", {"approved": True, "feedback": "Current artifact is two."})),
    ):
        scripted_provider.enqueue_json(response)
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    command = RoleRunCommand("verifier-timeout", "director", "Deliver artifact2.", isolated_workspace)
    await service.start(
        command, scope=_scope(isolated_workspace), limits=RunLimits(time.time() + 30, 1_000_000, 64_000)
    )
    await service.wait(command.session_id, workspace=isolated_workspace)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["project_outcome"] == "accepted", row
    attempts = [
        e["payload"] for e in service.store.load_events(command.session_id) if e["kind"] == "verification_completed"
    ]
    assert [e["verification"] for e in attempts] == ["failed", "passed"]
    failed = attempts[0]["checks"][0]
    assert failed["timed_out"] is True and failed["ok"] is False and failed["exit_code"] is None
    assert "CHECK_STARTED" in failed["content"]
    assert service.store.verification_completion_is_observed(command.session_id, attempts[0])
    feedback = next(
        m for m in scripted_provider.requests[1]["body"]["messages"] if m.get("tool_call_id") == "slow-verify"
    )
    assert json.loads(json.loads(feedback["content"])["content"])["verification"] == "failed"
    assert [r["tool_call_id"] for r in scripted_provider.requests[1]["body"]["messages"] if r["role"] == "tool"] == [
        "slow-verify"
    ]
    cold = _service(isolated_workspace, scripted_provider, monkeypatch).snapshot(
        command.session_id, workspace=isolated_workspace
    )
    assert cold["verification"] == "passed" and cold["project_outcome"] == "accepted"
