from __future__ import annotations

import importlib


def test_core_io_modules_support_package_imports(tmp_path) -> None:
    io_flags = importlib.import_module("polaris.kernelone.fs.control_flags")
    io_jsonl_ops = importlib.import_module("polaris.kernelone.fs.jsonl.ops")
    io_memory = importlib.import_module("polaris.kernelone.fs.memory_snapshot")
    io_paths = importlib.import_module("polaris.kernelone.storage.io_paths")
    io_plan_template = importlib.import_module("polaris.cells.docs.court_workflow.internal.plan_template")
    io_workspace_integrity = importlib.import_module("polaris.cells.workspace.integrity.public.service")
    io_text = importlib.import_module("polaris.kernelone.fs.text_ops")
    io_tools = importlib.import_module("polaris.kernelone.tools.io_tools")

    assert isinstance(io_flags._fsync_enabled(), bool)
    assert isinstance(io_jsonl_ops._fsync_enabled(), bool)
    assert io_memory.read_memory_snapshot(str(tmp_path / "missing.json")) is None
    assert io_paths.build_cache_root("", str(tmp_path))
    assert io_plan_template is not None
    workspace_status = io_workspace_integrity.workspace_status_path(str(tmp_path)).replace("\\", "/")
    assert workspace_status.endswith("meta/workspace_status.json")
    assert io_text.read_file_safe(str(tmp_path / "missing.txt")) == ""
    assert hasattr(io_tools, "build_utf8_env")
