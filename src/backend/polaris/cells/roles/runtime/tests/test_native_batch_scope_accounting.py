"""Every native member reaches the real policy owner and a terminal outcome.

Only the remote model is replaced by the existing loopback HTTP fixture.
Canonical preparation, TaskRuntime, DEO, gateway, filesystem and receipts remain
real. Harness source anchors and negative input injections are checked below.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from polaris.cells.roles.kernel.tests import test_director_persistent_execution_http as harness
from polaris.cells.roles.runtime.public.contracts import RoleRuntimeError


def _replace_once(source, old, new):
    assert source.count(old) == 1, (old, source.count(old))
    return source.replace(old, new, 1)


async def _exercise(tmp_path, monkeypatch, mode):
    source = inspect.getsource(harness.test_canonical_director_session_reads_writes_repairs_and_verifies_over_http)
    marker = (
        "async def "
        + harness.test_canonical_director_session_reads_writes_repairs_and_verifies_over_http.__name__
        + "("
    )
    assert source.count(marker) == 1
    source = source[source.index(marker) :]
    source = _replace_once(
        source,
        harness.test_canonical_director_session_reads_writes_repairs_and_verifies_over_http.__name__,
        "_run_accounting",
    )
    assert source.count("    finally:\n") == 1
    finally_index = source.index("    finally:\n")
    tail = "    if forged_wire:\n"
    assert source[finally_index:].count(tail) == 1
    source = source[: source.index(tail, finally_index)]
    assert source.count("    steps: list[") == source.count("    read_only =") == 1
    first = source.index("    steps: list[")
    end = source.index("    read_only =", first)
    source = (
        source[:first]
        + """    steps = [("write_file", {"file": "answer.py", "content": "VALUE = 42\\n"}), None]
"""
        + source[end:]
    )
    # Narrow the actual token to two outputs. Required targets do not mint rights.
    token_start = source.index("    token: dict[")
    context_start = source.index("    context = {")
    assert source.count("    token: dict[") == source.count("    context = {") == 1
    token = source[token_start:context_start]
    token = _replace_once(
        token,
        '"target_files": [] if read_only else ["answer.py", "optional.md"],',
        '"target_files": [] if read_only else ["answer.py", ".gitignore"],',
    )
    token = _replace_once(
        token,
        '"allowed_write_paths": [] if read_only else ["answer.py", "optional.md"],',
        '"allowed_write_paths": [] if read_only else ["answer.py", ".gitignore"],',
    )
    token = _replace_once(
        token,
        '"allowed_read_paths": ["answer.py", "verify.py"],',
        '"allowed_read_paths": ["answer.py", ".gitignore", "verify.py"],',
    )
    token = _replace_once(
        token, '["answer.py", "verify.py", "optional.md"]', '["answer.py", "verify.py", ".gitignore"]'
    )
    source = source[:token_start] + token + source[context_start:]
    source = _replace_once(
        source,
        '"target_files": [] if read_only else ["answer.py", "optional.md"],',
        '"target_files": [] if read_only else ["answer.py", ".gitignore"],',
    )
    source = _replace_once(
        source,
        '"required_write_targets": [] if read_only else ["answer.py"],',
        '"required_write_targets": [] if read_only else ["answer.py", ".gitignore"],',
    )
    source = _replace_once(
        source,
        '        "context_files": ["answer.py", "verify.py"] if read_only else [],',
        '        "continuous_execution": {"enabled": True, "max_auto_turns": 2},\n        "context_files": ["answer.py", "verify.py"] if read_only else [],',
    )
    source = _replace_once(
        source,
        'else "Read answer.py, implement VALUE=42, run the CE command, repair failures and finish."',
        'else "Create answer.py and .gitignore only, then verify current files."',
    )
    source = _replace_once(
        source,
        "            payload = json.dumps(",
        """            if index == 0:
                content = [
                    {"type":"tool_use","id":"hidden-owned","name":"write_file","input":{"file":".gitignore","content":"node_modules/\\ndist/\\n"}},
                    {"type":"tool_use","id":"normal-owned","name":"write_file","input":{"file":"answer.py","content":"VALUE = 42\\n"}},
                    {"type":"tool_use","id":"foreign-denied","name":"write_file","input":{"file":"seed/private.txt","content":"outside scope\\n"}},
                ]
            payload = json.dumps(""",
    )
    injection = ""
    if mode == "wide":
        injection = "    context['allowed_write_paths'] = ['answer.py', '.gitignore', 'seed/private.txt']\n    context['allowed_paths'] = ['answer.py', '.gitignore', 'seed/private.txt', 'verify.py']\n"
    elif mode == "narrow":
        injection = "    context['allowed_write_paths'] = ['answer.py']\n"
    elif mode == "readonly":
        injection = "    token['allowed_write_paths'] = []\n"
    elif mode == "foreign_owner":
        source = _replace_once(source, "session_id=attempt.session_id,", 'session_id="foreign-session",')
    elif mode == "stale_owner":
        injection = "    verdict = authority.settle(outcome='failed', summary='Private owner closed before dispatch')\n    assert verdict.success, verdict\n"
    elif mode == "missing_owner":
        injection = "    context.pop('task_runtime_execution_attempt_authority')\n"
    if mode != "ordinary":
        injection += "    _injected['fired'] = True\n"
        source = _replace_once(
            source,
            "    try:\n        result = await service.execute_role_session(",
            injection + "    try:\n        result = await service.execute_role_session(",
        )
    source += "\n    return result, bodies, token\n"
    witness = {"fired": False}
    ns = {**harness.__dict__, "_injected": witness}
    exec(compile(source, __file__, "exec"), ns)
    try:
        result, bodies, token = await ns["_run_accounting"](tmp_path, monkeypatch, "repair")
    except RoleRuntimeError as exc:
        assert witness["fired"]
        (tmp_path / "accounting-observation.json").write_text(
            json.dumps(
                {"mode": mode, "injected": True, "typed_error": type(exc).__name__, "error": str(exc), "code": exc.code}
            ),
            encoding="utf-8",
        )
        raise
    if mode != "ordinary":
        assert witness["fired"]
    rows = result.metadata.get("batch_receipt", {}).get("results", [])
    observation = {
        "mode": mode,
        "injected": witness["fired"],
        "error": result.error_message,
        "http_calls": len(bodies),
        "status_by_id": {r["call_id"]: r["status"] for r in rows},
        "token_writes": token["allowed_write_paths"],
    }
    (tmp_path / "accounting-observation.json").write_text(
        json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result, bodies, rows


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ordinary", "wide", "narrow", "readonly"])
async def test_all_native_members_have_real_owned_or_denied_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
):
    _result, bodies, rows = await _exercise(tmp_path, monkeypatch, mode)
    by_id = {r["call_id"]: r for r in rows}
    assert len(rows) == len(by_id) == 3
    assert set(by_id) == {"hidden-owned", "normal-owned", "foreign-denied"}
    assert by_id["foreign-denied"]["status"] == "error"
    assert not (tmp_path / "seed/private.txt").exists()
    owned = {"normal-owned"} if mode == "narrow" else set() if mode == "readonly" else {"hidden-owned", "normal-owned"}
    for call_id in {"normal-owned", "hidden-owned"}:
        assert by_id[call_id]["status"] == ("success" if call_id in owned else "error")
        if call_id in owned:
            assert by_id[call_id]["effect_receipt_commit"]["code"] == "receipt_committed"
        else:
            assert not by_id[call_id].get("effect_receipt")
    assert len({by_id[call_id]["effect_receipt_commit"]["event_id"] for call_id in owned}) == len(owned)
    assert (tmp_path / ".gitignore").exists() == ("hidden-owned" in owned)
    assert (tmp_path / "answer.py").read_text(encoding="utf-8") == (
        "VALUE = 42\n" if "normal-owned" in owned else "VALUE = 0\n"
    )
    if mode == "readonly":
        # Physical contract-correction calls are not outer session turns.
        assert not _result.ok
        assert _result.metadata["continuous_execution"]["turns_executed"] <= 2
    else:
        assert len(bodies) <= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["foreign_owner", "stale_owner", "missing_owner"])
async def test_inactive_or_foreign_owner_never_dispatches_model_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
):
    with pytest.raises(RoleRuntimeError, match="lacks a valid TaskRuntime execution attempt") as raised:
        await _exercise(tmp_path, monkeypatch, mode)
    expected_code = (
        "deo_execution_attempt_mismatch"
        if mode == "foreign_owner"
        else "deo_execution_attempt_invalid"
        if mode == "stale_owner"
        else "deo_execution_attempt_missing"
    )
    assert raised.value.code == expected_code
    assert json.loads((tmp_path / "http-requests.json").read_text(encoding="utf-8")) == []
    assert (tmp_path / "answer.py").read_text(encoding="utf-8") == "VALUE = 0\n"
    assert not (tmp_path / ".gitignore").exists() and not (tmp_path / "seed/private.txt").exists()
