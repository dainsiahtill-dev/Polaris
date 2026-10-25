"""Actual contained cache effects; synthetic fixtures, no delivery verdict."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.process import resolve_command_cache
from polaris.kernelone.process.command_cache import CommandCacheUnavailableError
from polaris.kernelone.process.sandbox import build_workspace_sandbox_argv


@pytest.fixture
def cache_workspace() -> Iterator[Path]:
    owner = Path(__file__).resolve().parents[6]
    fs = KernelFileSystem(str(owner), get_default_adapter(), read_only_paths=True)
    namespace = ".polaris/audits/persistent-command-cache-boundary-20261025/storage/test-workspaces"
    fs.workspace_mkdir(namespace, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cache-", dir=owner / namespace) as temporary:
        workspace = Path(temporary) / "project"
        workspace.mkdir()
        (workspace / ".polaris/runtime/engineering").mkdir(parents=True)
        (workspace / ".polaris/runtime/engineering/sessions.sqlite3").write_text("protected", encoding="utf-8")
        yield workspace


def _tools(
    workspace: Path, mode: Literal["disabled", "read_only", "read_write"], *, writes: bool = True
) -> EngineeringTools:
    return EngineeringTools(
        workspace,
        PermissionScope(
            (workspace,),
            (workspace,) if writes else (),
            frozenset({"execute_command", "repo_rg"}),
            False,
            command_cache=mode,
        ),
        RunLimits(None, 100_000, 16_000),
        asyncio.Event(),
    )


async def _execute(tools: EngineeringTools, command: str, *, timeout: int = 10):
    return await tools.execute("cache-call", "execute_command", {"command": command, "timeout": timeout})


async def test_actual_engineering_calls_persist_executable_and_dependency(cache_workspace: Path) -> None:
    tools = _tools(cache_workspace, "read_write")
    first = await _execute(
        tools,
        'test -n "${POLARIS_COMMAND_CACHE-}" && '
        'mkdir -p "$POLARIS_COMMAND_CACHE/sdk/bin" "$POLARIS_COMMAND_CACHE/sdk/lib" && '
        'printf \'#!/bin/sh\nprintf "value="; cat "$(dirname "$0")/../lib/value"\' '
        '> "$POLARIS_COMMAND_CACHE/sdk/bin/check" && '
        'chmod 755 "$POLARIS_COMMAND_CACHE/sdk/bin/check" && '
        'printf persisted > "$POLARIS_COMMAND_CACHE/sdk/lib/value"',
    )
    assert first.ok, first.content
    second = await _execute(tools, '"$POLARIS_COMMAND_CACHE/sdk/bin/check"')
    assert second.ok, second.content
    assert "value=persisted" in second.content
    root = cache_workspace / ".polaris/runtime/command-cache/data"
    assert (root / "sdk/lib/value").read_text(encoding="utf-8") == "persisted"


async def test_disabled_has_no_environment_mount_or_allocation(cache_workspace: Path) -> None:
    tools = _tools(cache_workspace, "disabled")
    result = await _execute(tools, 'test -z "${POLARIS_COMMAND_CACHE-}" && test ! -e /polaris-command-cache')
    assert result.ok, result.content
    assert not (cache_workspace / ".polaris/runtime/command-cache").exists()
    assert "POLARIS_COMMAND_CACHE" not in tools.schemas()[0]["function"]["description"]


@pytest.mark.parametrize("mode", ["read_only", "read_write"])
def test_enabled_schema_distinguishes_workspace_and_persistent_cache(cache_workspace: Path, mode) -> None:
    description = _tools(cache_workspace, mode).schemas()[0]["function"]["description"]
    assert "For persistence, save files only within authorized workspace write roots" not in description
    assert "Workspace files may persist only within authorized workspace write roots" in description
    assert "POLARIS_COMMAND_CACHE" in description and "/polaris-command-cache" in description
    assert ("Cache writes are authorized" if mode == "read_write" else "Cache is read-only") in description
    assert str(cache_workspace / ".polaris/runtime/command-cache") not in description


async def test_readonly_missing_cache_is_unavailable_without_creation(cache_workspace: Path) -> None:
    result = await _execute(_tools(cache_workspace, "read_only"), "printf should-not-run")
    assert not result.ok
    assert "command cache unavailable" in result.content
    assert "should-not-run" not in result.content
    assert not (cache_workspace / ".polaris/runtime/command-cache").exists()


async def test_actual_readonly_cache_reads_but_cannot_write(cache_workspace: Path) -> None:
    writer = _tools(cache_workspace, "read_write")
    assert (await _execute(writer, 'printf before > "$POLARIS_COMMAND_CACHE/value"')).ok
    reader = _tools(cache_workspace, "read_only")
    result = await _execute(reader, 'cat "$POLARIS_COMMAND_CACHE/value"')
    assert result.ok and "before" in result.content
    failed = await _execute(reader, 'printf changed > "$POLARIS_COMMAND_CACHE/value"')
    assert not failed.ok and failed.exit_code != 0
    root = cache_workspace / ".polaris/runtime/command-cache/data"
    assert (root / "value").read_text(encoding="utf-8") == "before"


async def test_readonly_helper_never_inherits_cache_write_authority(cache_workspace: Path) -> None:
    tools = _tools(cache_workspace, "read_write")
    assert (await _execute(tools, 'printf before > "$POLARIS_COMMAND_CACHE/value"')).ok
    result = await tools._process(
        ["/bin/sh", "-c", 'printf changed > "$POLARIS_COMMAND_CACHE/value"'], timeout=10, writes=()
    )
    assert result.returncode != 0
    assert (cache_workspace / ".polaris/runtime/command-cache/data/value").read_text(encoding="utf-8") == "before"


async def test_helper_does_not_allocate_missing_rw_cache(cache_workspace: Path) -> None:
    with pytest.raises(CommandCacheUnavailableError):
        await _tools(cache_workspace, "read_write")._process(["/bin/true"], timeout=10, writes=())
    assert not (cache_workspace / ".polaris/runtime/command-cache").exists()


async def test_helper_expired_deadline_does_not_allocate_cache(cache_workspace: Path) -> None:
    tools = _tools(cache_workspace, "read_write")
    tools.limits = RunLimits(0, 100_000, 16_000)
    with pytest.raises(ValueError, match="deadline exceeded"):
        await tools._process(["/bin/true"], timeout=10, writes=(), command_cache_write=True)
    assert not (cache_workspace / ".polaris/runtime/command-cache").exists()


async def test_unavailable_containment_does_not_allocate_cache(
    cache_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import polaris.kernelone.process.sandbox as sandbox

    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    result = await _execute(_tools(cache_workspace, "read_write"), "printf forbidden")
    assert not result.ok and "requires bubblewrap" in result.content
    assert not (cache_workspace / ".polaris/runtime/command-cache").exists()


async def test_failed_source_containment_admission_does_not_allocate_cache(cache_workspace: Path) -> None:
    outside = cache_workspace.parent / "outside"
    outside.write_text("protected", encoding="utf-8")
    (cache_workspace / "source-link").hardlink_to(outside)
    result = await _execute(_tools(cache_workspace, "read_write"), "printf forbidden")
    assert not result.ok and "hardlink" in result.content
    assert not (cache_workspace / ".polaris/runtime/command-cache").exists()


async def test_cache_alias_exposes_only_exact_leaf_and_private_paths_stay_private(cache_workspace: Path) -> None:
    tools = _tools(cache_workspace, "read_write")
    result = await _execute(
        tools,
        'test "$POLARIS_COMMAND_CACHE" = /polaris-command-cache && '
        'test "$HOME" = /sandbox-home && test "$XDG_CACHE_HOME" = /sandbox-cache && test "$TMPDIR" = /tmp && '
        'test "$POLARIS_SANDBOX_OUTPUT" = /sandbox-output && '
        "test ! -e .polaris/runtime/engineering/sessions.sqlite3 && "
        "test ! -e /polaris-command-cache/../engineering/sessions.sqlite3 && "
        'printf private > "$HOME/private" && printf private > "$XDG_CACHE_HOME/private" && '
        'printf private > "$TMPDIR/private"',
    )
    assert result.ok, result.content
    next_call = await _execute(
        tools, 'test ! -e "$HOME/private" && test ! -e "$XDG_CACHE_HOME/private" && test ! -e "$TMPDIR/private"'
    )
    assert next_call.ok, next_call.content


@pytest.mark.parametrize("termination", ["failed", "timeout"])
async def test_cache_data_survives_failed_and_timedout_calls(cache_workspace: Path, termination: str) -> None:
    tools = _tools(cache_workspace, "read_write")
    suffix = "exit 7" if termination == "failed" else "sleep 5"
    result = await _execute(tools, 'printf retained > "$POLARIS_COMMAND_CACHE/value"; ' + suffix, timeout=1)
    assert not result.ok
    assert result.timed_out is (termination == "timeout")
    next_call = await _execute(tools, 'cat "$POLARIS_COMMAND_CACHE/value"')
    assert next_call.ok and "retained" in next_call.content


async def test_sdk_relative_and_alias_absolute_links_work(cache_workspace: Path) -> None:
    tools = _tools(cache_workspace, "read_write")
    first = await _execute(
        tools,
        'mkdir -p "$POLARIS_COMMAND_CACHE/sdk/bin"; '
        "printf '#!/bin/sh\nprintf linked' > \"$POLARIS_COMMAND_CACHE/sdk/real\"; "
        'chmod 755 "$POLARIS_COMMAND_CACHE/sdk/real"; '
        'ln -s ../real "$POLARIS_COMMAND_CACHE/sdk/bin/relative"; '
        'ln -s /polaris-command-cache/sdk/bin/relative "$POLARIS_COMMAND_CACHE/absolute"',
    )
    assert first.ok, first.content
    second = await _execute(tools, '"$POLARIS_COMMAND_CACHE/sdk/bin/relative" && "$POLARIS_COMMAND_CACHE/absolute"')
    assert second.ok and "linkedlinked" in second.content


@pytest.mark.parametrize("topology", ["outside", "protected", "broken", "loop", "fifo", "hardlink"])
def test_unsafe_cache_topology_fails_closed(cache_workspace: Path, topology: str) -> None:
    cache = resolve_command_cache(cache_workspace, "read_write", create=True)
    assert cache is not None
    entry = cache.root / "unsafe"
    if topology == "outside":
        outside = cache_workspace / "outside"
        outside.write_text("untouched", encoding="utf-8")
        entry.symlink_to(outside)
    elif topology == "protected":
        entry.symlink_to(cache_workspace / ".polaris/runtime/engineering/sessions.sqlite3")
    elif topology == "broken":
        entry.symlink_to("missing")
    elif topology == "loop":
        entry.symlink_to("unsafe")
    elif topology == "fifo":
        os.mkfifo(entry)
    else:
        entry.hardlink_to(cache_workspace / ".polaris/runtime/engineering/sessions.sqlite3")
    with pytest.raises(CommandCacheUnavailableError):
        cache.validate()
    assert entry.is_symlink() or entry.exists(), "unsafe tree remains available for reconciliation"


def test_hardlinks_entirely_inside_cache_are_admitted(cache_workspace: Path) -> None:
    cache = resolve_command_cache(cache_workspace, "read_write", create=True)
    assert cache is not None
    (cache.root / "one").write_text("same", encoding="utf-8")
    (cache.root / "two").hardlink_to(cache.root / "one")
    cache.validate()


@pytest.mark.parametrize(
    "component",
    [".polaris", ".polaris/runtime", ".polaris/runtime/command-cache", ".polaris/runtime/command-cache/data"],
)
def test_pinned_ancestry_replacement_is_rejected(cache_workspace: Path, component: str) -> None:
    cache = resolve_command_cache(cache_workspace, "read_write", create=True)
    assert cache is not None
    original = cache_workspace / component
    old = original.with_name(original.name + "-original")
    original.rename(old)
    original.mkdir()
    with pytest.raises(CommandCacheUnavailableError):
        cache.validate()


async def test_command_leaving_outward_link_fails_and_does_not_expose_control(cache_workspace: Path) -> None:
    tools = _tools(cache_workspace, "read_write")
    result = await _execute(
        tools,
        'ln -s "' + str(cache_workspace / ".polaris/runtime/engineering/sessions.sqlite3") + '" '
        '"$POLARIS_COMMAND_CACHE/escape"; test ! -r "$POLARIS_COMMAND_CACHE/escape"',
    )
    assert not result.ok and "symlink leaves the cache domain" in result.content
    assert (cache_workspace / ".polaris/runtime/command-cache/data/escape").is_symlink()
    assert (cache_workspace / ".polaris/runtime/engineering/sessions.sqlite3").read_text(
        encoding="utf-8"
    ) == "protected"


def test_sandbox_rejects_cache_from_another_workspace(cache_workspace: Path) -> None:
    cache = resolve_command_cache(cache_workspace, "read_write", create=True)
    other = cache_workspace.parent / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="belong to this workspace"):
        build_workspace_sandbox_argv(
            ["/bin/true"],
            workspace=other,
            cwd=other,
            read_roots=(other,),
            write_roots=(),
            network_allowed=False,
            command_cache=cache,
        )


def test_disabled_resolver_does_not_even_observe_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    import polaris.kernelone.process.command_cache as module

    def forbidden(*_args, **_kwargs):
        raise AssertionError("disabled cache must not observe layout or filesystem")

    monkeypatch.setattr(module, "resolve_runtime_path", forbidden)
    monkeypatch.setattr(module, "_directory_identity", forbidden)
    assert resolve_command_cache(Path("/absent"), "disabled") is None


async def test_coroutine_cancel_revalidates_after_actual_drain(cache_workspace: Path) -> None:
    tools = _tools(cache_workspace, "read_write")
    task = asyncio.create_task(
        tools._process(
            ["/bin/sh", "-c", 'ln -s /protected "$POLARIS_COMMAND_CACHE/unsafe"; printf ready > ready; sleep 20'],
            timeout=30,
            writes=(cache_workspace,),
            command_cache_write=True,
        )
    )
    try:
        for _ in range(500):
            if (cache_workspace / "ready").exists():
                break
            await asyncio.sleep(0.01)
        assert (cache_workspace / "ready").exists(), "contained process must execute before cancellation"
        task.cancel()
        with pytest.raises(CommandCacheUnavailableError, match="symlink leaves"):
            await task
        assert (cache_workspace / ".polaris/runtime/command-cache/data/unsafe").is_symlink()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
