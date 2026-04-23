from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import polaris.infrastructure.log_pipeline.writer as writer_module
import pytest
from polaris.cells.roles.kernel.public.service import emit_llm_event as emit_application_llm_event
from polaris.infrastructure.log_pipeline.llm_realtime_bridge import (
    LogPipelineLLMRealtimeBridge,
)
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.events.io_events import emit_llm_event as emit_kernel_llm_event
from polaris.kernelone.events.realtime_bridge import set_llm_realtime_bridge
from polaris.kernelone.fs.registry import set_default_adapter
from polaris.kernelone.storage import resolve_storage_roots
from polaris.kernelone.storage.layout import StorageRoots as _StorageRoots


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            record = str(line).strip()
            if not record:
                continue
            rows.append(json.loads(record))
    return rows


@pytest.fixture(autouse=True)
def _configure_runtime_bridge(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    # Clear storage-roots cache to prevent cross-test pollution
    # (test 1 may cache KERNELONE_WORKSPACE roots that interfere with test 2's temp paths).
    import polaris.kernelone.storage.layout as _layout_mod
    _layout_mod._storage_roots_cache.clear()

    monkeypatch.setattr(writer_module, "PUBLISH_ENABLED", False)
    set_default_adapter(LocalFileSystemAdapter())
    set_llm_realtime_bridge(LogPipelineLLMRealtimeBridge())
    try:
        yield
    finally:
        set_llm_realtime_bridge(None)


def test_application_llm_events_publish_to_canonical_runtime_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "runtime_root"
    runtime_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(workspace))

    run_id = "RUN-APP-LLM-001"
    emit_application_llm_event(
        event_type="llm_call_start",
        role="architect",
        run_id=run_id,
        model="gpt-5.4",
        metadata={"iteration": 1},
    )

    resolved_runtime_root = Path(resolve_storage_roots(str(workspace)).runtime_root)
    journal_path = resolved_runtime_root / "runs" / run_id / "logs" / "journal.norm.jsonl"
    rows = _read_jsonl(journal_path)

    assert rows
    latest = rows[-1]
    assert latest["channel"] == "llm"
    assert latest["domain"] == "llm"
    assert latest["actor"] == "architect"
    assert "projection_event:llm_waiting" in latest["tags"]
    assert "llm_event:llm_call_start" in latest["tags"]
    assert latest["raw"]["stream_event"] == "llm_waiting"
    assert latest["raw"]["event_type"] == "llm_call_start"


def test_kernel_io_llm_events_publish_tool_activity_to_canonical_runtime_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import polaris.kernelone.storage.layout as _layout_mod

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "runtime_root"
    runtime_root.mkdir(parents=True, exist_ok=True)
    workspace_str = str(workspace.resolve())

    def _fake_resolve(ws: str, ramdisk_root=None):
        return _StorageRoots(
            workspace_abs=workspace_str,
            workspace_key="test",
            storage_layout_mode="kernelone",
            home_root=str((tmp_path / ".polaris").resolve()),
            global_root=str((tmp_path / ".polaris" / "global").resolve()),
            config_root=str((tmp_path / ".polaris" / "config").resolve()),
            projects_root=str((tmp_path / ".polaris" / "projects").resolve()),
            project_root=str((tmp_path / ".polaris" / "projects" / "test").resolve()),
            project_persistent_root=workspace_str,
            runtime_projects_root=str((tmp_path / ".polaris" / "runtime_projects").resolve()),
            runtime_project_root=str((tmp_path / ".polaris" / "runtime_projects" / "test").resolve()),
            workspace_persistent_root=workspace_str,
            runtime_base=str((tmp_path / ".polaris" / "runtime").resolve()),
            runtime_root=str(runtime_root.resolve()),
            runtime_mode="file",
            history_root=str((tmp_path / ".polaris" / "history").resolve()),
        )

    # io_events imports resolve_storage_roots via:
    #   from polaris.kernelone.storage import resolve_storage_roots
    #   -> __getattr__ in polaris.kernelone.storage.__init__ -> layout.resolve_storage_roots
    # After import, io_events holds a direct reference to layout.resolve_storage_roots.
    # Patching _layout_mod.resolve_storage_roots covers all consumers, including io_events.
    monkeypatch.setattr(_layout_mod, "resolve_storage_roots", _fake_resolve)

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    monkeypatch.setenv("KERNELONE_WORKSPACE", workspace_str)

    llm_events_path = (
        workspace
        / ".polaris"
        / "runtime"
        / "events"
        / "llm.events.jsonl"
    )
    llm_events_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = "RUN-KERNEL-LLM-001"

    emit_kernel_llm_event(
        str(llm_events_path),
        event="tool_execute",
        role="director",
        data={
            "tool_name": "write_file",
            "args": {"path": "tui_runtime.md"},
        },
        run_id=run_id,
        iteration=2,
        source="unit_test",
    )

    assert llm_events_path.exists()

    resolved_runtime_root = Path(resolve_storage_roots(str(workspace)).runtime_root)
    journal_path = resolved_runtime_root / "runs" / run_id / "logs" / "journal.norm.jsonl"
    rows = _read_jsonl(journal_path)

    assert rows
    latest = rows[-1]
    assert latest["channel"] == "llm"
    assert latest["actor"] == "director"
    assert "projection_event:tool_call" in latest["tags"]
    assert latest["raw"]["stream_event"] == "tool_call"
    assert latest["raw"]["iteration"] == 2
    assert "write_file" in latest["message"]
