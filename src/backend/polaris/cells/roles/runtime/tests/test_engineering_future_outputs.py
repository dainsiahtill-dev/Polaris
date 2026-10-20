"""Future host output grants: no host mkdir, no source-write expansion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.bootstrap.config import ServerConfig, Settings
from polaris.bootstrap.engineering_runtime import create_engineering_service, start_engineering_run
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)


def _settings(workspace: Path) -> Settings:
    return Settings(workspace=workspace, server=ServerConfig(engineering_verification_output_roots=("dist",)))


def test_declared_future_output_admits_without_creating_target_directory(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KERNELONE_LLM_CONFIG", str(isolated_workspace / ".polaris" / "llm.json"))
    service = create_engineering_service(_settings(isolated_workspace))
    assert service.workspace == isolated_workspace
    assert not (isolated_workspace / "dist").exists()


@pytest.mark.parametrize("declared", [".", ".polaris/outputs", "../outside", "linked/future"])
def test_future_output_does_not_expand_scope_or_follow_symlinks(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, declared: str
) -> None:
    monkeypatch.setenv("KERNELONE_LLM_CONFIG", str(isolated_workspace / ".polaris" / "llm.json"))
    outside = isolated_workspace / "elsewhere"
    outside.mkdir()
    (isolated_workspace / "linked").symlink_to(outside, target_is_directory=True)
    settings = _settings(isolated_workspace)
    settings.server.engineering_verification_output_roots = (declared,)
    with pytest.raises((OSError, ValueError)):
        create_engineering_service(settings)


async def test_director_creates_declared_output_then_final_check_writes_only_it(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_LLM_CONFIG", str(isolated_workspace / ".polaris" / "llm.json"))
    (isolated_workspace / "source.txt").write_text("original", encoding="utf-8")
    settings = _settings(isolated_workspace)
    service = create_engineering_service(settings)
    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    scripted_provider.enqueue_json(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "mkdir",
                                "type": "function",
                                "function": {
                                    "name": "execute_command",
                                    "arguments": json.dumps({"command": "mkdir -p dist"}),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    scripted_provider.enqueue_json(
        {
            "choices": [
                {"message": {"role": "assistant", "content": "Ready for actual checks."}, "finish_reason": "stop"}
            ]
        }
    )
    command = "python3 -c \"from pathlib import Path; Path('dist/result.txt').write_text('built',encoding='utf-8'); assert Path('source.txt').read_text(encoding='utf-8')=='original'\""
    row = await start_engineering_run(
        service,
        settings,
        instruction="Prepare output directory then verify.",
        workspace=isolated_workspace,
        required_checks=(command,),
    )
    result = await service.wait(row["session_id"], workspace=isolated_workspace)
    assert result.verification == "passed"
    assert (isolated_workspace / "dist/result.txt").read_text(encoding="utf-8") == "built"
    assert (isolated_workspace / "source.txt").read_text(encoding="utf-8") == "original"
    await service.shutdown()


async def test_cold_pending_run_cannot_expand_original_final_output_grants(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shlex
    import time

    from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    workspace = isolated_workspace
    (workspace / "source.txt").write_text("source", encoding="utf-8")
    (workspace / "cache").mkdir()
    dist = workspace / "dist"
    scope = PermissionScope((workspace,), (workspace,), frozenset({"execute_command"}), False)
    original = EngineeringRunService(workspace, writable_output_roots=(dist,))
    probe = "from pathlib import Path\ntry: Path('cache/forbidden').write_text('bad',encoding='utf-8')\nexcept OSError as error: assert error.errno in (1,13,30)\nelse: raise AssertionError('expanded output grant')\nPath('dist/final').write_text('ok',encoding='utf-8')"
    command = RoleRunCommand(
        "cold-outputs",
        "director",
        "Create declared output directory.",
        workspace,
        required_checks=("python3 -c " + shlex.quote(probe),),
    )
    original.store.create_run(
        command,
        scope=scope,
        limits=RunLimits(time.time() + 30, 100000, 10000),
        engine_version="engineering_v2",
        request_key="original",
        verification_output_roots=(dist,),
    )
    scripted_provider.client("openai", workspace, monkeypatch)
    from polaris.cells.roles.runtime.tests.test_engineering_qa import _calls, _text

    scripted_provider.enqueue_json(_calls(("mkdir", "execute_command", {"command": "mkdir -p dist"})))
    scripted_provider.enqueue_json(_text("Ready."))
    cold = EngineeringRunService(
        workspace, writable_output_roots=(dist, workspace / "cache"), permission_resolver=lambda _: scope
    )
    await cold.resume(command.session_id, workspace=workspace)
    await cold.wait(command.session_id, workspace=workspace)
    row = cold.observe(command.session_id, workspace=workspace)
    assert row["verification"] == "passed", row["checks"]
    assert row["verification_output_roots"] == [str(dist)]
    assert not (workspace / "cache/forbidden").exists() and (dist / "final").read_text(encoding="utf-8") == "ok"


def test_future_output_replaced_by_symlink_is_not_rebound(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    workspace = isolated_workspace
    outputs = workspace / "dist"
    create_engineering_service(_settings(workspace))
    outside = workspace / "different"
    outside.mkdir()
    outputs.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        EngineeringRunService(workspace, writable_output_roots=(outputs,))
    assert not (outside / "result").exists()
