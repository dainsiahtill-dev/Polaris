"""Repairable task-boundary feedback across real kernel/session ownership.

The remote model is replaced by the existing private loopback HTTP fixture.
Request preparation, public session service, TaskRuntime, policy-gated DEO writes,
TaskBoundary facts and verification commands run unchanged. Fault controls below
explicitly simulate a lost event carrier or changed next-turn owner input.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from polaris.cells.roles.kernel.tests import test_director_persistent_execution_http as harness
from polaris.cells.roles.kernel.tests.runtime_boundary_scenarios import install_final_answer_projection_fault


def _replace_once(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1, f"Expected one harness anchor, found {source.count(old)}: {old!r}"
    return source.replace(old, new, 1)


async def _run_two_file_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str = "repair"):
    observations: dict[str, object] = {"mode": mode}
    if mode in {"missing_commit", "nonrepairable"}:
        install_final_answer_projection_fault(monkeypatch, observations, mode)
    if mode == "foreign_owner":
        from dataclasses import replace

        from polaris.cells.roles.runtime.internal.kernel_session_turn import KernelSessionTurnBridge

        actual_command = KernelSessionTurnBridge._turn_command
        calls = 0

        def foreign_next_command(self, *args, **kwargs):
            nonlocal calls
            command = actual_command(self, *args, **kwargs)
            calls += 1
            if calls == 3:
                observations[mode] = int(observations.get(mode, 0)) + 1
                return replace(command, session_id="foreign-session")
            return command

        monkeypatch.setattr(KernelSessionTurnBridge, "_turn_command", foreign_next_command)
    source = inspect.getsource(harness.test_canonical_director_session_reads_writes_repairs_and_verifies_over_http)
    function_name = harness.test_canonical_director_session_reads_writes_repairs_and_verifies_over_http.__name__
    function_marker = f"async def {function_name}("
    assert source.count(function_marker) == 1
    source = source[source.index(function_marker) :]
    source = _replace_once(source, function_name, "_run_partial_final")
    finally_marker = "    finally:\n"
    assert source.count(finally_marker) == 1
    finally_offset = source.index(finally_marker)
    tail_marker = "    if forged_wire:\n"
    assert source[finally_offset:].count(tail_marker) == 1
    source = source[: source.index(tail_marker, finally_offset)]
    steps_marker = "    steps: list["
    next_section = "    read_only ="
    assert source.count(steps_marker) == source.count(next_section) == 1
    start = source.index(steps_marker)
    end = source.index(next_section, start)
    source = (
        source[:start]
        + """    steps = [
        ("write_file", {"file": "answer.py", "content": "VALUE = 42\\n"}),
        None,
        ("write_file", {"file": "optional.md", "content": "# Required independent output\\n"}),
        ("execute_command", {"command": command}),
        None,
    ]
"""
        + source[end:]
    )
    source = _replace_once(
        source,
        '"required_write_targets": [] if read_only else ["answer.py"],',
        '"required_write_targets": [] if read_only else ["answer.py", "optional.md"],',
    )
    if mode == "repeat_empty":
        source = _replace_once(
            source, '("write_file", {"file": "optional.md", "content": "# Required independent output\\n"}),', "None,"
        )
        # The session policy belongs to context, never the capability token.
        needle = '        "context_files": ["answer.py", "verify.py"] if read_only else [],'
        source = _replace_once(
            source,
            needle,
            '        "continuous_execution": {"enabled": True, "max_auto_turns": 3},\n' + needle,
        )
        observations[mode] = 1
    if mode == "stale_owner":
        insertion = """    actual_persist = service._persist_session_turn_state
    persisted_turns = 0
    async def persist_then_close(*args, **kwargs):
        nonlocal persisted_turns
        await actual_persist(*args, **kwargs)
        persisted_turns += 1
        if persisted_turns == 2:
            verdict = authority.settle(outcome="failed", summary="Private current owner closed before followup")
            assert verdict.success, verdict
            _fault_observations["stale_owner"] = int(_fault_observations.get("stale_owner", 0)) + 1
    monkeypatch.setattr(service, "_persist_session_turn_state", persist_then_close)
"""
        source = _replace_once(
            source,
            "    try:\n        result = await service.execute_role_session(",
            insertion + "    try:\n        result = await service.execute_role_session(",
        )
    source += "\n    return result, bodies, token\n"
    namespace = {**harness.__dict__, "_fault_observations": observations}
    exec(compile(source, __file__, "exec"), namespace)
    result = await namespace["_run_partial_final"](tmp_path, monkeypatch, "repair")
    if mode != "repair":
        assert observations.get(mode) == 1, observations
    observations["error_message"] = result[0].error_message
    observations["continuous_execution"] = result[0].metadata.get("continuous_execution")
    (tmp_path / "injection-observations.json").write_text(
        json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


@pytest.mark.asyncio
async def test_partial_committed_write_then_empty_final_continues_to_missing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    result, bodies, token = await _run_two_file_contract(tmp_path, monkeypatch)
    rows = result.metadata["batch_receipt"]["results"]
    writes = [row for row in rows if row["tool_name"] == "write_file" and row["status"] == "success"]
    assert result.ok, (result.error_message, result.metadata.get("continuous_execution"))
    assert (tmp_path / "answer.py").read_text(encoding="utf-8") == "VALUE = 42\n"
    assert (tmp_path / "optional.md").read_text(encoding="utf-8").startswith("# Required independent output")
    assert len(bodies) >= 4
    assert "incomplete_materialization" in json.dumps(bodies[2], ensure_ascii=False)
    assert "optional.md" in json.dumps(bodies[2], ensure_ascii=False)
    assert token["target_files"] == ["answer.py", "optional.md"]
    assert [row["result"]["file"] for row in writes] == ["answer.py", "optional.md"]
    assert len({row["effect_receipt_commit"]["event_id"] for row in writes}) == 2
    assert all(row["effect_receipt_commit"]["code"] == "receipt_committed" for row in writes)
    assert any(row["tool_name"] == "execute_command" and row["result"].get("exit_code") == 0 for row in rows)
    assert result.metadata["task_boundary_verdict"]["status"] == "completed_verified"
    assert result.metadata["task_boundary_verdict"]["missing_required_verifiers"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing_commit", "nonrepairable", "foreign_owner", "stale_owner"])
async def test_empty_final_followup_cannot_bypass_commit_or_owner_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
):
    result, bodies, _token = await _run_two_file_contract(tmp_path, monkeypatch, mode)
    assert not result.ok
    assert len(bodies) == 2, (mode, len(bodies), result.error_message)
    assert (tmp_path / "answer.py").read_text(encoding="utf-8") == "VALUE = 42\n"
    assert not (tmp_path / "optional.md").exists()
    writes = [
        r
        for r in result.metadata["batch_receipt"]["results"]
        if r["tool_name"] == "write_file" and r["status"] == "success"
    ]
    assert len(writes) == 1 and writes[0]["effect_receipt_commit"]["code"] == "receipt_committed"


@pytest.mark.asyncio
async def test_repeated_empty_final_remains_failed_with_existing_explicit_turn_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    result, bodies, _token = await _run_two_file_contract(tmp_path, monkeypatch, "repeat_empty")
    assert not result.ok
    assert len(bodies) == 3, (len(bodies), result.error_message)
    assert not (tmp_path / "optional.md").exists()
    assert result.metadata["continuous_execution"]["turns_executed"] == 3
    assert result.metadata["continuous_execution"]["stop_reason"] == "max_turns_exceeded"
