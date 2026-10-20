"""Real final commands, output containment and current-input verification evidence."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import shlex
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.kernelone.exceptions import PathSecurityError

pytest_plugins = ["polaris.cells.roles.runtime.tests.engineering_support"]


def verifier(
    workspace: Path,
    commands: tuple[str, ...],
    *,
    scope: PermissionScope | None = None,
    output_roots: tuple[Path, ...] = (),
) -> tuple[Any, EngineeringSessionStore]:
    name = "polaris.cells.roles.runtime.internal.engineering_verification"
    assert importlib.util.find_spec(name) is not None, "bound engineering verification is missing"
    module = importlib.import_module(name)
    scope = scope or PermissionScope((workspace,), (workspace,), frozenset({"execute_command"}), False)
    limits = RunLimits(None, 100000, 20000)
    store = EngineeringSessionStore(workspace)
    store.create_run(
        RoleRunCommand("verify", "director", "实现并验证", workspace, required_checks=commands),
        scope=scope,
        limits=limits,
        engine_version="engineering_v2",
        request_key=None,
    )
    return (
        module.EngineeringVerification(
            workspace,
            store=store,
            scope=scope,
            limits=limits,
            cancel=asyncio.Event(),
            writable_output_roots=output_roots,
        ),
        store,
    )


def python(source: str) -> str:
    return "python3 -c " + shlex.quote(source)


@pytest.mark.parametrize(
    ("command", "exit_code", "evidence"),
    [
        ("printf actual-exit; exit 7", 7, "actual-exit"),
        (python("assert 2 + 2 == 5, 'real failure'"), 1, "real failure"),
    ],
)
async def test_actual_nonzero_command_is_failed_evidence(
    isolated_workspace: Path, command: str, exit_code: int, evidence: str
) -> None:
    check, store = verifier(isolated_workspace, (command,))
    with store.acquire_workspace(isolated_workspace):
        observations = await check.run_verification((command,), session_id="verify", workspace_revision=0)
    assert len(observations) == 1
    assert observations[0].exit_code == exit_code
    assert not observations[0].ok and evidence in observations[0].content
    assert store.load_run("verify", workspace=isolated_workspace)["verification"] == "failed"
    events = store.load_events("verify")
    assert [event["kind"] for event in events].count("tool_started") == 1
    assert [event["kind"] for event in events].count("tool_observed") == 1
    assert store.pending_tool_calls("verify") == []


async def test_readonly_python_check_uses_private_cache_and_persists_real_binding(isolated_workspace: Path) -> None:
    (isolated_workspace / "calc.py").write_text("VALUE = '中文'\n", encoding="utf-8")
    command = python("import py_compile,calc; py_compile.compile('calc.py',doraise=True); assert calc.VALUE == '中文'")
    check, store = verifier(isolated_workspace, (command,))
    observations = await check.run_verification((command,), session_id="verify", workspace_revision=0)
    assert len(observations) == 1 and observations[0].ok and observations[0].exit_code == 0
    assert not (isolated_workspace / "__pycache__").exists()
    assert check.current_verification("verify") == "passed"
    completed = [event for event in store.load_events("verify") if event["kind"] == "verification_completed"][-1]
    assert completed["payload"]["input_binding"]["commands"] == [command]
    assert completed["payload"]["workspace_revision"] == 0
    assert store.load_run("verify", workspace=isolated_workspace)["checks"][0]["exit_code"] == 0


async def test_empty_checks_and_plaintext_success_never_create_pass(isolated_workspace: Path) -> None:
    check, store = verifier(isolated_workspace, ())
    store.update_run("verify", text="All checks passed. python3 -m unittest completed.")
    assert await check.run_verification((), session_id="verify", workspace_revision=0) == ()
    assert check.current_verification("verify") == "not_run"
    assert not [event for event in store.load_events("verify") if event["kind"] == "tool_started"]


async def test_explicit_empty_execute_permission_has_nonexecuted_unknown_evidence(isolated_workspace: Path) -> None:
    command = "printf changed > source.txt"
    scope = PermissionScope((isolated_workspace,), (isolated_workspace,), frozenset(), False)
    check, store = verifier(isolated_workspace, (command,), scope=scope)
    result = await check.run_verification((command,), session_id="verify", workspace_revision=0)
    assert len(result) == 1 and not result[0].ok and result[0].exit_code is None
    assert "disabled" in result[0].content
    assert not (isolated_workspace / "source.txt").exists()
    assert store.load_run("verify", workspace=isolated_workspace)["verification"] == "unknown"


@pytest.mark.parametrize("target_kind", ["source", "control", "external"])
async def test_final_command_cannot_write_business_source_control_or_external(
    isolated_workspace: Path, target_kind: str
) -> None:
    source = isolated_workspace / "source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    external = isolated_workspace.parent / (isolated_workspace.name + "-sentinel")
    external.write_text("external-original", encoding="utf-8")
    try:
        target = {
            "source": source,
            "control": isolated_workspace / ".polaris/runtime/engineering/workspace.lock",
            "external": external,
        }[target_kind]
        command = python(f"from pathlib import Path; Path({str(target)!r}).write_text('attack',encoding='utf-8')")
        check, store = verifier(isolated_workspace, (command,))
        result = await check.run_verification((command,), session_id="verify", workspace_revision=0)
        assert not result[0].ok and result[0].exit_code == 1
        assert store.load_run("verify", workspace=isolated_workspace)["verification"] == "failed"
        assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
        assert store.lock_path.read_text(encoding="utf-8") == ""
        assert external.read_text(encoding="utf-8") == "external-original"
    finally:
        external.unlink()


@pytest.mark.parametrize("kind", ["workspace", "control", "missing", "external", "symlink", "outside-grant"])
def test_host_output_roots_require_preexisting_narrow_original_authority(isolated_workspace: Path, kind: str) -> None:
    output = isolated_workspace / "dist"
    output.mkdir()
    store = EngineeringSessionStore(isolated_workspace)
    link = isolated_workspace / "alias"
    link.symlink_to(isolated_workspace.parent, target_is_directory=True)
    candidate = {
        "workspace": isolated_workspace,
        "control": store.control_root,
        "missing": isolated_workspace / "absent",
        "external": isolated_workspace.parent,
        "symlink": link,
        "outside-grant": output,
    }[kind]
    grants = () if kind == "outside-grant" else (isolated_workspace,)
    scope = PermissionScope((isolated_workspace,), grants, frozenset({"execute_command"}), False)
    with pytest.raises(ValueError, match="output"):
        verifier(isolated_workspace, (), scope=scope, output_roots=(candidate,))


def test_mutable_original_write_alias_cannot_expand_output_authority(isolated_workspace: Path) -> None:
    source = isolated_workspace / "source"
    output = source / "dist"
    output.mkdir(parents=True)
    grant = isolated_workspace / "allowed"
    grant.symlink_to(source, target_is_directory=True)
    scope = PermissionScope((isolated_workspace,), (grant,), frozenset({"execute_command"}), False)
    with pytest.raises(ValueError, match="output"):
        verifier(isolated_workspace, (), scope=scope, output_roots=(output,))


async def test_host_dist_only_output_succeeds_then_output_change_invalidates_reuse(isolated_workspace: Path) -> None:
    source = isolated_workspace / "source.txt"
    source.write_text("input", encoding="utf-8")
    output = isolated_workspace / "dist"
    output.mkdir()
    command = python(
        "from pathlib import Path; Path('dist/result.txt').write_text(Path('source.txt').read_text(encoding='utf-8'),encoding='utf-8')"
    )
    check, store = verifier(isolated_workspace, (command,), output_roots=(output,))
    result = await check.run_verification((command,), session_id="verify", workspace_revision=0)
    assert result[0].ok and check.current_verification("verify") == "passed"
    assert source.read_text(encoding="utf-8") == "input"
    (output / "result.txt").write_text("changed after verification", encoding="utf-8")
    assert check.current_verification("verify") == "unknown"
    historical = [event for event in store.load_events("verify") if event["kind"] == "verification_completed"][-1]
    assert historical["payload"]["verification"] == "passed"


@pytest.mark.parametrize("change", ["revision", "outside-edit", "outside-aba", "new-file"])
async def test_old_pass_cannot_be_reused_after_actual_input_change(isolated_workspace: Path, change: str) -> None:
    source = isolated_workspace / "source.txt"
    source.write_text("original", encoding="utf-8")
    command = python("from pathlib import Path; assert Path('source.txt').read_text(encoding='utf-8') == 'original'")
    check, store = verifier(isolated_workspace, (command,))
    assert (await check.run_verification((command,), session_id="verify", workspace_revision=0))[0].ok
    assert check.current_verification("verify") == "passed"
    if change == "revision":
        store.update_run("verify", workspace_revision=1)
    elif change == "new-file":
        (isolated_workspace / "added.py").write_text("new = 1\n", encoding="utf-8")
    else:
        before = source.stat().st_ctime_ns
        source.write_text("outside change", encoding="utf-8")
        if change == "outside-aba":
            source.write_text("original", encoding="utf-8")
            assert source.stat().st_ctime_ns != before
    assert check.current_verification("verify") == "unknown"


async def test_stale_requested_revision_does_not_execute_check(isolated_workspace: Path) -> None:
    command = "printf must-not-run"
    check, store = verifier(isolated_workspace, (command,))
    store.update_run("verify", workspace_revision=1)
    assert await check.run_verification((command,), session_id="verify", workspace_revision=0) == ()
    assert store.load_run("verify", workspace=isolated_workspace)["verification"] == "unknown"
    assert not [event for event in store.load_events("verify") if event["kind"] == "tool_started"]


async def test_outside_edit_and_restore_during_actual_check_is_unknown(isolated_workspace: Path) -> None:
    source = isolated_workspace / "source.txt"
    source.write_text("original", encoding="utf-8")
    output = isolated_workspace / "dist"
    output.mkdir()
    command = python(
        "from pathlib import Path; import time; print(Path('source.txt').read_text(encoding='utf-8')); Path('dist/started').write_text('yes',encoding='utf-8'); time.sleep(0.4); print(Path('source.txt').read_text(encoding='utf-8'))"
    )
    check, store = verifier(isolated_workspace, (command,), output_roots=(output,))
    task = asyncio.create_task(check.run_verification((command,), session_id="verify", workspace_revision=0))
    for _ in range(200):
        if (output / "started").exists():
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("real verification process did not start")
    source.write_text("outside-change", encoding="utf-8")
    await asyncio.sleep(0.03)
    source.write_text("original", encoding="utf-8")
    observations = await task
    assert observations[0].ok and observations[0].exit_code == 0
    assert source.read_text(encoding="utf-8") == "original"
    assert store.load_run("verify", workspace=isolated_workspace)["verification"] == "unknown"


async def test_mutable_external_declared_dependency_invalidates_current_pass(isolated_workspace: Path) -> None:
    dependency = isolated_workspace.parent / (isolated_workspace.name + "-dependency")
    dependency.mkdir()
    value = dependency / "value.txt"
    value.write_text("dependency", encoding="utf-8")
    try:
        scope = PermissionScope((isolated_workspace, dependency), (), frozenset({"execute_command"}), False)
        command = python(
            f"from pathlib import Path; assert Path({str(value)!r}).read_text(encoding='utf-8') == 'dependency'"
        )
        check, _store = verifier(isolated_workspace, (command,), scope=scope)
        assert (await check.run_verification((command,), session_id="verify", workspace_revision=0))[0].ok
        assert check.current_verification("verify") == "passed"
        value.write_text("changed", encoding="utf-8")
        assert check.current_verification("verify") == "unknown"
    finally:
        value.unlink()
        dependency.rmdir()


@pytest.mark.parametrize(
    "error", [PermissionError("actual input unavailable"), PathSecurityError("input path changed")]
)
async def test_unreadable_input_cannot_claim_current_pass(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    source = isolated_workspace / "input.bin"
    source.write_bytes(b"\xff\x00binary")
    check, _store = verifier(isolated_workspace, ("true",))
    assert (await check.run_verification(("true",), session_id="verify", workspace_revision=0))[0].ok
    assert check.current_verification("verify") == "passed"

    def unavailable(_self: object, _path: str) -> bytes:
        raise error

    from polaris.kernelone.fs.runtime import KernelFileSystem

    monkeypatch.setattr(KernelFileSystem, "workspace_read_bytes", unavailable)
    assert check.current_verification("verify") == "unknown"


async def test_source_change_between_freeze_and_result_capture_cannot_pass(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = isolated_workspace / "source.txt"
    source.write_text("original", encoding="utf-8")
    check, store = verifier(isolated_workspace, ("true",))
    observe = check._manifest

    def edit_before_result(*, include_outputs: bool) -> dict[str, object]:
        if include_outputs:
            source.write_text("external change at result capture", encoding="utf-8")
        return observe(include_outputs=include_outputs)

    monkeypatch.setattr(check, "_manifest", edit_before_result)
    result = await check.run_verification(("true",), session_id="verify", workspace_revision=0)
    assert result[0].ok
    assert store.load_run("verify", workspace=isolated_workspace)["verification"] == "unknown"


async def test_unproved_drain_escapes_even_when_verification_journal_fails(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
    from polaris.kernelone.process.process_tree import ProcessTreeDrainError

    check, store = verifier(isolated_workspace, ("true",))
    error = ProcessTreeDrainError("physical drain unproved")

    async def unproved(*_args: object, **_kwargs: object) -> object:
        raise error

    def journal_failure(*_args: object, **_kwargs: object) -> bool:
        raise OSError("journal unavailable")

    monkeypatch.setattr(EngineeringTools, "_process", unproved)
    monkeypatch.setattr(store, "record_verification", journal_failure)
    with pytest.raises(ProcessTreeDrainError) as caught:
        await check.run_verification(("true",), session_id="verify", workspace_revision=0)
    assert caught.value is error
    assert len(store.pending_tool_calls("verify")) == 1
