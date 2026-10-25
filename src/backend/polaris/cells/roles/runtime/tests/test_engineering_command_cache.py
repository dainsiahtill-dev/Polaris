"""Actual cache inputs join existing verification, without granting check writes.

The tiny executable is a synthetic fixture, not a generated Bench delivery.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import time
from pathlib import Path
from typing import Any

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
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


def _prepare(workspace: Path) -> tuple[Path, KernelFileSystem]:
    filesystem = KernelFileSystem(
        str(workspace), get_default_adapter(), read_only_paths=True, runtime_owner_workspace=str(workspace)
    )
    cache = workspace / ".polaris/runtime/command-cache/data"
    filesystem.workspace_mkdir(str(cache / "bin"), parents=True, exist_ok=True)
    filesystem.workspace_mkdir(str(workspace / "dist"), parents=True, exist_ok=True)
    filesystem.workspace_write_text_atomic(str(cache / "dep.txt"), "cache-payload\n", encoding="utf-8")
    filesystem.workspace_write_text_atomic(
        str(cache / "bin/probe"), '#!/bin/sh\ncat "$(dirname "$0")/../dep.txt"\n', encoding="utf-8"
    )
    os.chmod(cache / "bin/probe", 0o755)
    # Link metadata is part of the declared fixture, not a substitute tool call.
    (cache / "alias").symlink_to("dep.txt")
    return cache, filesystem


def _verifier(
    workspace: Path, command: str, *, store: EngineeringSessionStore | None = None
) -> tuple[EngineeringVerification, EngineeringSessionStore]:
    scope = PermissionScope(
        (workspace,), (workspace,), frozenset({"execute_command"}), False, command_cache="read_write"
    )
    limits = RunLimits(None, 100000, 20000)
    if store is None:
        store = EngineeringSessionStore(workspace)
        store.create_run(
            RoleRunCommand("cache-verify", "director", "verify synthetic cache", workspace, required_checks=(command,)),
            scope=scope,
            limits=limits,
            engine_version="engineering_v2",
            request_key=None,
        )
    return (
        EngineeringVerification(
            workspace,
            store=store,
            scope=scope,
            limits=limits,
            cancel=asyncio.Event(),
            writable_output_roots=(workspace / "dist",),
        ),
        store,
    )


async def test_verifier_cache_is_physically_readonly_with_writable_outputs(isolated_workspace: Path) -> None:
    cache, filesystem = _prepare(isolated_workspace)
    source = (
        "import os,subprocess; from pathlib import Path; "
        "c=Path(os.environ['POLARIS_COMMAND_CACHE']); "
        "assert subprocess.check_output([str(c/'bin/probe')]).decode()=='cache-payload\\n'; "
        "Path('dist/result.txt').write_text('actual output',encoding='utf-8'); "
        "\ntry: (c/'dep.txt').write_text('illegal',encoding='utf-8')"
        "\nexcept OSError: print('CACHE_READONLY_PROVED')"
        "\nelse: raise AssertionError('verification mutated cache')"
    )
    command = "python3 -c " + shlex.quote(source)
    verifier, store = _verifier(isolated_workspace, command)
    with store.acquire_workspace(isolated_workspace):
        result = await verifier.run_verification((command,), session_id="cache-verify", workspace_revision=0)
    assert len(result) == 1 and result[0].ok and result[0].exit_code == 0, result
    assert "CACHE_READONLY_PROVED" in result[0].content
    assert filesystem.workspace_read_text(str(cache / "dep.txt"), encoding="utf-8") == "cache-payload\n"
    assert (
        filesystem.workspace_read_text(str(isolated_workspace / "dist/result.txt"), encoding="utf-8") == "actual output"
    )
    assert verifier.current_verification("cache-verify") == "passed"
    binding = verifier.review_binding("cache-verify")
    entries = binding["inputs"]
    assert isinstance(entries, dict)
    assert entries[str(cache / "dep.txt")]["sha256"] == hashlib.sha256(b"cache-payload\n").hexdigest()
    assert entries[str(cache / "alias")]["symlink"] == "dep.txt"
    assert not any("/runtime/engineering/" in name for name in entries)
    assert verifier.tools.scope.command_cache == "read_only"


@pytest.mark.parametrize("mutation", ["executable", "dependency", "mode", "link"])
async def test_cache_mutation_invalidates_existing_cold_verification_and_review_binding(
    isolated_workspace: Path, mutation: str
) -> None:
    cache, filesystem = _prepare(isolated_workspace)
    command = '"$POLARIS_COMMAND_CACHE/bin/probe"'
    verifier, store = _verifier(isolated_workspace, command)
    result = await verifier.run_verification((command,), session_id="cache-verify", workspace_revision=0)
    assert len(result) == 1 and result[0].ok, result
    assert verifier.current_verification("cache-verify") == "passed"
    original_review = verifier.review_binding("cache-verify")
    original_events = store.load_events("cache-verify", kinds=("verification_completed",))
    if mutation == "executable":
        filesystem.workspace_write_text_atomic(str(cache / "bin/probe"), "#!/bin/sh\nprintf other\n", encoding="utf-8")
    elif mutation == "dependency":
        filesystem.workspace_write_text_atomic(str(cache / "dep.txt"), "other\n", encoding="utf-8")
    elif mutation == "mode":
        os.chmod(cache / "bin/probe", 0o700)
    else:
        (cache / "alias").unlink()
        (cache / "alias").symlink_to("bin/probe")
    cold_store = EngineeringSessionStore(isolated_workspace)
    cold, _ = _verifier(isolated_workspace, command, store=cold_store)
    assert cold.current_completed_result("cache-verify") is None
    assert cold.current_verification("cache-verify") == "unknown"
    assert cold.review_binding("cache-verify") != original_review
    assert cold_store.load_events("cache-verify", kinds=("verification_completed",)) == original_events
    assert cold_store.load_run("cache-verify", workspace=isolated_workspace)["verification"] == "passed"


def test_disabled_cache_does_not_join_inputs_or_allocate(isolated_workspace: Path) -> None:
    scope = PermissionScope((isolated_workspace,), (), frozenset(), False)
    store = EngineeringSessionStore(isolated_workspace)
    check = EngineeringVerification(
        isolated_workspace, store=store, scope=scope, limits=RunLimits(None, 100000, 20000), cancel=asyncio.Event()
    )
    assert not (isolated_workspace / ".polaris/runtime/command-cache").exists()
    assert not any("/command-cache/" in path for path in check._manifest(include_outputs=True))
    assert not (isolated_workspace / ".polaris/runtime/command-cache").exists()


def test_missing_enabled_cache_observation_does_not_create_it(isolated_workspace: Path) -> None:
    (isolated_workspace / "dist").mkdir()
    check, _ = _verifier(isolated_workspace, "true")
    with pytest.raises((ValueError, OSError), match="cache"):
        check._manifest(include_outputs=True)
    assert not (isolated_workspace / ".polaris/runtime/command-cache").exists()


def _packet(call: tuple[str, str, dict[str, Any]] | None = None) -> dict[str, Any]:
    calls = (
        [{"id": call[0], "type": "function", "function": {"name": call[1], "arguments": json.dumps(call[2])}}]
        if call is not None
        else []
    )
    return {
        "choices": [
            {
                "message": {"content": "Synthetic role handoff.", "tool_calls": calls},
                "finish_reason": "tool_calls" if calls else "stop",
            }
        ]
    }


async def test_actual_collaborative_qa_and_saved_artifact_stale_after_cache_change(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the provider is scripted; execution, SQLite, QA and cold reads are real."""
    workspace = isolated_workspace
    cache, filesystem = _prepare(workspace)
    filesystem.workspace_write_text_atomic(str(workspace / "app.txt"), "synthetic candidate", encoding="utf-8")
    scope = PermissionScope(
        (workspace,),
        (workspace,),
        frozenset({"read_file", "execute_command", "report_review"}),
        False,
        command_cache="read_write",
    )
    qa_source = (
        "import os,subprocess; from pathlib import Path; "
        "c=Path(os.environ['POLARIS_COMMAND_CACHE']); "
        "assert subprocess.check_output([str(c/'bin/probe')])==b'cache-payload\\n'; "
        "\ntry: (c/'dep.txt').write_text('illegal',encoding='utf-8')"
        "\nexcept OSError: print('QA_CACHE_READONLY')"
        "\nelse: raise AssertionError('QA mutated cache')"
    )
    artifact_source = (
        "import os,subprocess; from pathlib import Path; "
        "v=subprocess.check_output([os.environ['POLARIS_COMMAND_CACHE']+'/bin/probe']).decode(); "
        "p=Path(os.environ['POLARIS_SANDBOX_OUTPUT'])/'report.svg'; "
        "p.write_text('<svg xmlns=\"http://www.w3.org/2000/svg\"><text>'+v+'</text></svg>',encoding='utf-8')"
    )
    for packet in [
        _packet(),  # PM
        _packet(),  # CE
        _packet(
            (
                "capture",
                "execute_command",
                {
                    "command": "python3 -c " + shlex.quote(artifact_source),
                    "artifacts": ["report.svg"],
                },
            )
        ),
        _packet(),  # Director hands off to actual required verification.
        _packet(("qa-source", "read_file", {"file": "app.txt"})),
        _packet(("qa-command", "execute_command", {"command": "python3 -c " + shlex.quote(qa_source)})),
        _packet(("qa-review", "report_review", {"approved": True, "feedback": "Synthetic cache check passed."})),
    ]:
        scripted_provider.enqueue_json(packet)
    service = EngineeringRunService(
        workspace,
        client=scripted_provider.client("openai", workspace, monkeypatch),
        permission_resolver=lambda _row: scope,
        require_qa=True,
    )
    command = RoleRunCommand(
        "cache-qa",
        "director",
        "Review the synthetic candidate using the cache probe.",
        workspace,
        workflow="collaborative",
        required_checks=('"$POLARIS_COMMAND_CACHE/bin/probe"',),
    )
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 60, 1_000_000, 64000))
    await service.wait(command.session_id, workspace=workspace)
    before = service.snapshot(command.session_id, workspace=workspace)
    assert before["project_outcome"] == "accepted", before
    events = service.store.load_events(command.session_id)
    qa = next(e["payload"] for e in events if e["kind"] == "tool_observed" and e["payload"]["call_id"] == "qa-command")
    assert qa["ok"] and "QA_CACHE_READONLY" in qa["content"]
    captures = [e for e in events if e["kind"] == "tool_output_capture_completed" and e["payload"].get("artifact_refs")]
    if not captures:
        print(json.dumps([e for e in events if e["kind"] in {"tool_observed", "tool_output_capture_completed"}]))
    assert captures, [e for e in events if e["kind"] in {"tool_observed", "tool_output_capture_completed"}]
    capture = captures[0]
    reference = capture["payload"]["artifact_refs"][0]
    verifier = service._verification(scope, RunLimits(None, 100000, 20000), asyncio.Event())
    assert service._saved_artifact(command.session_id, reference, verifier.review_binding(command.session_id))
    filesystem.workspace_write_text_atomic(str(cache / "dep.txt"), "changed dependency\n", encoding="utf-8")
    cold = EngineeringRunService(workspace, permission_resolver=lambda _row: scope, require_qa=True)
    current = cold.snapshot(command.session_id, workspace=workspace)
    assert current["verification"] == current["qa_verdict"] == current["project_outcome"] == "unknown", current
    assert cold.store.load_run(command.session_id, workspace=workspace)["qa_verdict"] == "approved"
    with pytest.raises(ValueError, match="stale"):
        cold._saved_artifact(command.session_id, reference, verifier.review_binding(command.session_id))
    assert cold.store.load_events(command.session_id) == events
    assert len(scripted_provider.requests) == 7
