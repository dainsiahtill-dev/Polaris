"""Real PM persistence checks: workspace ownership, atomic replacement and UTF-8.

Adapter rejection and post-effect failures must propagate without replay.
Test adapters use the store-local injection seam; global registry stays intact.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.runtime.state_owner.internal import pm_contract_store
from polaris.cells.runtime.state_owner.internal.pm_contract_store import (
    ensure_engine_dispatch_contracts,
    read_json_safe,
    write_json_atomic,
)
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.storage import resolve_logical_path
from polaris.kernelone.storage.io_paths import resolve_artifact_path

# The KernelOne default-adapter registry is process-global shared state.  This
# module must never publish into it, so the names that mutate it are checked
# against this module's own source.
_REGISTRY_MUTATION_NAMES = frozenset(
    {
        "set_default_adapter",
        "reset_default_adapter",
        "set_adapter_factory",
    }
)


def test_pm_contract_store_roundtrip_under_runtime_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    payload = {"tasks": [{"id": "t-1", "title": "migrate-kfs"}], "version": 1}
    logical_path = "runtime/contracts/pm_tasks.contract.json"

    adapter = LocalFileSystemAdapter()
    _install_store_adapter(monkeypatch, adapter)
    write_json_atomic(logical_path, payload)

    absolute_path = Path(resolve_logical_path(str(tmp_path), logical_path))
    assert absolute_path.is_file()
    assert read_json_safe(logical_path) == payload


def _build_isolated_workspace(tmp_path: Path, name: str = "workspace") -> Path:
    """Create an isolated workspace that owns its own canonical runtime root.

    The workspace is a real project layout: ``<workspace>/.polaris/runtime``
    is its single runtime root, exactly as ``storage.layout`` resolves it.

    The workspace also carries the platform's own workspace sentinel (``docs``)
    so that every consumer that discovers the workspace by sentinel - the
    docs cell that validates the runtime plan, for example - resolves *this*
    workspace instead of walking further up into an enclosing repository.
    That keeps the example independent of where the temporary directory
    happens to live.
    """
    workspace = (tmp_path / name).resolve()
    (workspace / ".polaris" / "runtime").mkdir(parents=True, exist_ok=True)
    (workspace / "docs").mkdir(parents=True, exist_ok=True)
    return workspace


def test_pm_contract_store_rejects_non_kfs_path(tmp_path: Path) -> None:
    """A path outside every managed root of its owner is refused.

    The example is deliberately *inside* the project ``.polaris`` single-root
    layout, so it can only be classified by the storage layout itself and not
    by the accident of where a temporary directory happens to live.  The
    retired bare ``<workspace>/runtime`` layout belongs to no managed root of
    its own workspace, which makes it a genuinely unmanaged example.
    """
    workspace = _build_isolated_workspace(tmp_path)
    outside_path = workspace / "runtime" / "pm_state.json"

    with pytest.raises(ValueError, match="KernelFileSystem managed roots"):
        write_json_atomic(str(outside_path), {"status": "invalid-path"})

    assert read_json_safe(str(outside_path)) is None
    assert not outside_path.exists()


def test_pm_contract_store_accepts_managed_path_in_the_same_layout(
    tmp_path: Path,
) -> None:
    """The counterexample of the rejection test is accepted.

    The managed and unmanaged examples live in the same isolated workspace,
    so the rejection above is caused by the path's position in the storage
    layout and not by the surrounding environment.
    """
    workspace = _build_isolated_workspace(tmp_path)
    managed_path = workspace / ".polaris" / "runtime" / "contracts" / "pm_tasks.contract.json"
    payload = {"status": "managed"}

    write_json_atomic(str(managed_path), payload)

    assert managed_path.is_file()
    assert read_json_safe(str(managed_path)) == payload


def test_pm_contract_store_accepts_nested_runtime_artefact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A nested ``runtime`` segment inside a canonical runtime root is legal.

    ``<project>/.polaris/runtime/runs/<run_id>/runtime/result.json`` contains a
    second ``runtime`` component.  It is an ordinary path segment, not a
    workspace marker: the project that owns the canonical runtime root owns
    the artefact, and the artefact maps to
    ``runtime/runs/<run_id>/runtime/result.json``.

    Treating the nested ``runtime`` as a workspace marker would identify the
    run directory as a workspace and refuse a path that the owning project
    does manage.
    """
    workspace = _build_isolated_workspace(tmp_path)
    runtime_root = workspace / ".polaris" / "runtime"
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)

    artefact = runtime_root / "runs" / "r1" / "runtime" / "result.json"
    payload = {"status": "nested-runtime"}

    write_json_atomic(str(artefact), payload)

    assert artefact.is_file()
    assert read_json_safe(str(artefact)) == payload


def test_pm_contract_store_binds_nested_workspace_to_its_own_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A nested workspace owns its own artefacts, not the enclosing one.

    The inner project lives inside the outer workspace's metadata tree.  The
    inner project's canonical runtime root is the owner of its artefacts, so
    the store must bind the write to the inner project and never to the
    enclosing workspace.
    """
    outer = tmp_path / "outer"
    inner = outer / ".polaris" / "inner-project"
    (inner / ".polaris" / "runtime" / "contracts").mkdir(parents=True)
    (inner / "docs").mkdir(parents=True)

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(inner / ".polaris" / "runtime"))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)

    contract = inner / ".polaris" / "runtime" / "contracts" / "pm_tasks.contract.json"
    payload = {"status": "nested-workspace"}

    write_json_atomic(str(contract), payload)

    assert contract.is_file()
    assert read_json_safe(str(contract)) == payload


def test_pm_contract_store_accepts_artefact_beside_a_nested_metadata_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A run directory that carries its own metadata tree stays owned.

    ``<project>/.polaris/runtime/runs/<run_id>/`` may itself contain a
    ``.polaris`` tree.  That nested tree is runtime *data* of the project,
    not an independent workspace, so it must not steal the ownership of the
    artefacts next to it.

    The artefact ``<project>/.polaris/runtime/runs/<run_id>/runtime/result.json``
    is accepted and maps to ``runtime/runs/<run_id>/runtime/result.json``,
    exactly as the project's own ``KernelFileSystem`` maps it.
    """
    project = tmp_path / "project"
    (project / ".polaris" / "runtime" / "runs" / "r1" / ".polaris" / "runtime").mkdir(parents=True)

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(project / ".polaris" / "runtime"))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)

    artefact = project / ".polaris" / "runtime" / "runs" / "r1" / "runtime" / "result.json"
    payload = {"status": "run-dir-metadata-tree"}

    write_json_atomic(str(artefact), payload)

    assert artefact.is_file()
    assert read_json_safe(str(artefact)) == payload


def test_engine_dispatch_contracts_backfill_missing_runtime_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The dispatch prerequisites are persisted in the canonical runtime root.

    The workspace is an isolated project layout, so the contract paths are
    the ones the production caller resolves: ``runtime/contracts/...`` and
    ``runtime/runs/<run_id>/contracts/...`` below the workspace's own
    ``.polaris/runtime`` root.
    """
    workspace = _build_isolated_workspace(tmp_path)
    runtime_root = workspace / ".polaris" / "runtime"
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)

    normalized = {
        "overall_goal": "Stabilize PM to Director dispatch",
        "tasks": [
            {
                "id": "PM-DISPATCH-1",
                "title": "Run Director after PM planning",
                "goal": "Create dispatch prerequisites from PM output.",
                "target_files": ["src/backend/polaris/cells/runtime/state_owner/internal/pm_contract_store.py"],
                "execution_checklist": ["Persist runtime contracts", "Validate runtime plan"],
                "acceptance": ["Director dispatch can start"],
            }
        ],
    }
    runtime_pm_tasks = resolve_artifact_path(str(workspace), "", "runtime/contracts/pm_tasks.contract.json")
    run_pm_tasks = resolve_artifact_path(str(workspace), "", "runtime/runs/pm-00001/contracts/pm_tasks.contract.json")
    runtime_plan = resolve_artifact_path(str(workspace), "", "runtime/contracts/plan.md")

    ensure_engine_dispatch_contracts(
        normalized=normalized,
        run_pm_tasks=run_pm_tasks,
        runtime_pm_tasks_full=runtime_pm_tasks,
        runtime_plan_full=runtime_plan,
    )

    plan_text = Path(runtime_plan).read_text(encoding="utf-8")
    assert "Generated from the PM task contract" in plan_text
    assert "Run Director after PM planning" in plan_text
    assert read_json_safe(runtime_pm_tasks) == normalized
    assert read_json_safe(run_pm_tasks) == normalized


# ---------------------------------------------------------------------------
# Helpers for the atomic-write behaviour tests
# ---------------------------------------------------------------------------


class _RecordingLocalAdapter(LocalFileSystemAdapter):
    """Transparent subclass of the real local adapter that records its calls.

    The physical behaviour is the production one (temp file plus rename); only
    the calls are observed so that the test can assert how the store asked for
    the write.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        atomic: bool = False,
    ) -> int:
        self.calls.append({"path": path, "atomic": atomic, "encoding": encoding})
        return super().write_text(path, content, encoding=encoding, atomic=atomic)


class _NonCompliantAdapter(LocalFileSystemAdapter):
    """Adapter whose ``write_text`` predates the protocol ``atomic`` keyword."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8") -> int:  # type: ignore[override]
        self.calls.append(path)
        raise AssertionError("a rejected atomic write must never reach the adapter")


class _PostWriteErrorAdapter(LocalFileSystemAdapter):
    """Adapter that commits the atomic replacement and then raises."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        atomic: bool = False,
    ) -> int:
        self.calls.append(path)
        # The replacement is committed first, so the failure below happens
        # *after* the physical effect and must not be retried.
        super().write_text(path, content, encoding=encoding, atomic=atomic)
        raise OSError("synthetic adapter failure after the replacement was committed")


def _install_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Create an isolated workspace and make it the current working directory.

    The workspace is the process' current directory so that the store resolves
    relative logical paths against it.  The runtime root env is pinned to the
    canonical project-local root of that workspace, because the storage layout
    refuses to write anywhere else.
    """
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(workspace / ".polaris" / "runtime"))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RAMDISK_ROOT", raising=False)
    return workspace


def _install_store_adapter(monkeypatch: pytest.MonkeyPatch, adapter: Any) -> Any:
    """Inject ``adapter`` into the PM store's own adapter seam.

    ``pm_contract_store._build_kernel_fs_for_path`` resolves the filesystem
    adapter through the module-level ``_get_fs_adapter`` function, so that
    function is the narrowest injection point: the double stays inside the PM
    store's boundary, the process-wide KernelOne default-adapter registry is
    never written, and pytest restores the original function when the test
    ends.

    Args:
        monkeypatch: The pytest monkeypatch fixture used for the injection.
        adapter: The adapter the store must use for the duration of the test.

    Returns:
        The injected adapter, so a caller can keep a direct reference.
    """
    monkeypatch.setattr(pm_contract_store, "_get_fs_adapter", lambda: adapter)
    return adapter


def _record_renames(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record every physical rename performed while the patch is active."""
    renames: list[tuple[str, str]] = []
    original_replace = os.replace

    def counting_replace(src: Any, dst: Any) -> None:
        renames.append((str(src), str(dst)))
        original_replace(src, dst)

    monkeypatch.setattr(os, "replace", counting_replace)
    return renames


def _physical_target(workspace: Path, logical_path: str) -> Path:
    return Path(resolve_logical_path(str(workspace), logical_path))


def _registry_mutation_calls(tree: ast.AST) -> list[str]:
    """Return the global-registry mutation calls found in a parsed module.

    Both call forms are matched: an attribute call such as
    ``registry.set_default_adapter(...)`` and a name call such as
    ``set_default_adapter(...)`` reached through a direct import.
    """
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _REGISTRY_MUTATION_NAMES:
            offenders.append(func.attr)
        elif isinstance(func, ast.Name) and func.id in _REGISTRY_MUTATION_NAMES:
            offenders.append(func.id)
    return offenders


def test_pm_contract_store_write_performs_single_atomic_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The contract write performs exactly one physical atomic replacement."""
    workspace = _install_workspace(monkeypatch, tmp_path)
    adapter = _RecordingLocalAdapter()
    _install_store_adapter(monkeypatch, adapter)
    renames = _record_renames(monkeypatch)
    write_json_atomic("runtime/contracts/pm_tasks.contract.json", {"version": 1, "tasks": []})

    assert len(adapter.calls) == 1, "the contract write must reach the adapter exactly once"
    assert adapter.calls[0]["atomic"] is True, "the contract write must request an atomic write"
    assert len(renames) == 1, "the contract write must perform exactly one physical replacement"

    target = _physical_target(workspace, "runtime/contracts/pm_tasks.contract.json")
    assert renames[0][1] == str(target)
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1, "tasks": []}

    # The temporary file used for the replacement must not survive.
    assert sorted(item.name for item in target.parent.iterdir()) == [target.name]


def test_pm_contract_store_atomic_write_is_utf8_and_cold_readable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The committed payload is UTF-8 and complete for a cold reader."""
    workspace = _install_workspace(monkeypatch, tmp_path)
    payload = {"goal": "迁移到原子写入", "owner": "polaris", "count": 3}
    logical_path = "runtime/contracts/pm_tasks.contract.json"

    _install_store_adapter(monkeypatch, LocalFileSystemAdapter())
    write_json_atomic(logical_path, payload)

    target = _physical_target(workspace, logical_path)
    raw = target.read_bytes()
    text = raw.decode("utf-8")
    assert "迁移到原子写入" in text
    assert "\\u" not in text, "ensure_ascii=False must keep non-ASCII characters unescaped"
    assert text.endswith("\n")
    assert json.loads(text) == payload

    # A cold reader is a separate process that shares no in-process state with
    # the writer, so it observes only what reached the filesystem.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,sys;raw=open(sys.argv[1],'rb').read();"
            "sys.stdout.write(json.dumps(json.loads(raw.decode('utf-8')),ensure_ascii=False))",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(completed.stdout) == payload


def test_pm_contract_store_rejects_non_compliant_adapter_without_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An adapter that cannot accept the atomic call shape is rejected."""
    _install_workspace(monkeypatch, tmp_path)
    adapter = _NonCompliantAdapter()
    _install_store_adapter(monkeypatch, adapter)
    with pytest.raises(NotImplementedError):
        write_json_atomic("runtime/contracts/pm_tasks.contract.json", {"version": 1})

    assert adapter.calls == [], "a rejected atomic write must not reach the adapter"
    target = _physical_target(Path.cwd(), "runtime/contracts/pm_tasks.contract.json")
    assert not target.exists()


def test_pm_contract_store_propagates_post_write_error_without_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failure after the replacement stays visible and is never replayed."""
    workspace = _install_workspace(monkeypatch, tmp_path)
    adapter = _PostWriteErrorAdapter()
    _install_store_adapter(monkeypatch, adapter)
    with pytest.raises(OSError, match="synthetic adapter failure after the replacement"):
        write_json_atomic("runtime/contracts/pm_tasks.contract.json", {"version": 2})

    assert len(adapter.calls) == 1, "a failed write must not be replayed through a second writer"

    # The effect did happen before the error, and the store must not hide it
    # behind a reported success: the payload is on disk with the committed value.
    target = _physical_target(workspace, "runtime/contracts/pm_tasks.contract.json")
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 2}


def test_pm_contract_store_honours_custom_compliant_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A custom protocol-compliant adapter receives the atomic write request."""
    workspace = _install_workspace(monkeypatch, tmp_path)
    adapter = _RecordingLocalAdapter()
    _install_store_adapter(monkeypatch, adapter)
    write_json_atomic("runtime/contracts/pm_tasks.contract.json", {"version": 3, "ok": True})

    assert len(adapter.calls) == 1
    assert adapter.calls[0]["atomic"] is True
    assert adapter.calls[0]["encoding"] == "utf-8"

    target = _physical_target(workspace, "runtime/contracts/pm_tasks.contract.json")
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 3, "ok": True}


def test_pm_contract_store_reads_back_the_atomically_written_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A normal read observes exactly the payload the atomic write committed."""
    workspace = _install_workspace(monkeypatch, tmp_path)
    logical_path = "runtime/contracts/pm_tasks.contract.json"
    payload = {"version": 5, "tasks": [{"id": "t-9"}], "note": "回读"}

    _install_store_adapter(monkeypatch, LocalFileSystemAdapter())
    write_json_atomic(logical_path, payload)

    assert read_json_safe(logical_path) == payload

    target = _physical_target(workspace, logical_path)
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_pm_contract_store_adapter_injection_leaves_the_global_registry_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An injected double must never be published into the global registry."""
    from polaris.kernelone.fs import registry as fs_registry

    _install_workspace(monkeypatch, tmp_path)
    adapter = _RecordingLocalAdapter()
    registry_adapter_before = fs_registry._default_adapter
    registry_attempted_before = fs_registry._initialization_attempted

    _install_store_adapter(monkeypatch, adapter)
    write_json_atomic("runtime/contracts/pm_tasks.contract.json", {"version": 4})

    assert adapter.calls, "the injected adapter must be the writer that was used"
    assert fs_registry._default_adapter is registry_adapter_before
    assert fs_registry._initialization_attempted == registry_attempted_before
    assert fs_registry._default_adapter is not adapter


def test_pm_contract_store_test_module_never_writes_the_global_adapter_registry() -> None:
    """This module must not publish a test double into the shared registry.

    The registry is process-global state shared with every other test module
    of the session, so a static check on this module's own source is the
    direct guard against reintroducing the publication.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    offenders = _registry_mutation_calls(ast.parse(source))

    assert offenders == [], f"this module must not mutate the global adapter registry: {offenders}"


@pytest.mark.parametrize("pin_runtime_root", [False, True])
def test_pm_contract_store_creates_fresh_project_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pin_runtime_root: bool,
) -> None:
    outer = _build_isolated_workspace(tmp_path, "outer")
    project = outer / ".polaris" / "projects" / "fresh"
    project.mkdir(parents=True)
    runtime_root = project / ".polaris" / "runtime"
    contract = runtime_root / "contracts" / "pm_tasks.contract.json"
    if pin_runtime_root:
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    else:
        monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    _install_store_adapter(monkeypatch, LocalFileSystemAdapter())
    assert not (project / ".polaris").exists()

    write_json_atomic(str(contract), {"project": "首次写入"})

    assert json.loads(contract.read_text(encoding="utf-8")) == {"project": "首次写入"}
    assert read_json_safe(str(contract)) == {"project": "首次写入"}
    assert not (outer / ".polaris" / "runtime" / "contracts" / "pm_tasks.contract.json").exists()


def test_pm_contract_store_fresh_project_rejects_a_foreign_runtime_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outer = _build_isolated_workspace(tmp_path, "outer")
    project = outer / ".polaris" / "projects" / "fresh"
    project.mkdir(parents=True)
    contract = project / ".polaris" / "runtime" / "contracts" / "pm_tasks.contract.json"
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(outer / ".polaris" / "runtime"))
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    _install_store_adapter(monkeypatch, LocalFileSystemAdapter())

    with pytest.raises(ValueError, match="KernelFileSystem managed roots"):
        write_json_atomic(str(contract), {"project": "wrong-owner"})

    assert not contract.exists()


@pytest.mark.parametrize("metadata_dir", [".project-state", ".ProjectState"])
def test_pm_contract_store_uses_the_configured_canonical_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metadata_dir: str,
) -> None:
    from polaris.kernelone import _runtime_config

    monkeypatch.setattr(_runtime_config, "_WORKSPACE_METADATA_DIR_NAME", metadata_dir)
    project = tmp_path / "project"
    (project / ".polaris" / "runtime").mkdir(parents=True)
    runtime_root = project / metadata_dir / "runtime"
    (runtime_root / "runs" / "r1" / metadata_dir / "runtime").mkdir(parents=True)
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    _install_store_adapter(monkeypatch, LocalFileSystemAdapter())
    contract = runtime_root / "runs" / "r1" / "runtime" / "result.json"

    write_json_atomic(str(contract), {"runtime": "configured"})

    assert read_json_safe(str(contract)) == {"runtime": "configured"}
    assert not (project / ".polaris" / "runtime" / "runs").exists()
