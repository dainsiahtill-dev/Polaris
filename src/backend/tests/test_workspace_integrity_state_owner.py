"""Verify that workspace_status.json is written through a single entry point.

Rules enforced:
1. `write_workspace_status` and `clear_workspace_status` exist only in
   `workspace.integrity.internal.workspace_service` (the state owner).
2. `fs_utils` does NOT expose write/clear/read status functions.
3. `plan_template` does NOT define its own write/clear/read status functions.
4. The public surface of `workspace.integrity` delegates to `workspace_service`.
5. `docs.court_workflow.public.service` does NOT re-export write/clear/read.
6. Round-trip write → read → clear works correctly through the canonical path.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module_defines_function(module_path: str, fn_name: str) -> bool:
    """Return True if the module itself defines (not merely imports) fn_name."""
    mod = importlib.import_module(module_path)
    fn = getattr(mod, fn_name, None)
    if fn is None:
        return False
    src_file = inspect.getfile(fn)
    # Normalise to forward slashes for cross-platform comparison
    return module_path.replace(".", "/") in src_file.replace("\\", "/")


# ---------------------------------------------------------------------------
# 1. Canonical writer lives in workspace_service
# ---------------------------------------------------------------------------

def test_write_status_defined_in_workspace_service():
    assert _module_defines_function(
        "polaris.cells.workspace.integrity.internal.workspace_service",
        "write_workspace_status",
    ), "write_workspace_status must be defined in workspace_service, not imported"


def test_clear_status_defined_in_workspace_service():
    assert _module_defines_function(
        "polaris.cells.workspace.integrity.internal.workspace_service",
        "clear_workspace_status",
    ), "clear_workspace_status must be defined in workspace_service, not imported"


def test_read_status_defined_in_workspace_service():
    assert _module_defines_function(
        "polaris.cells.workspace.integrity.internal.workspace_service",
        "read_workspace_status",
    ), "read_workspace_status must be defined in workspace_service, not imported"


# ---------------------------------------------------------------------------
# 2. fs_utils does NOT own write/clear/read status
# ---------------------------------------------------------------------------

def test_fs_utils_does_not_define_write_workspace_status():
    import polaris.cells.workspace.integrity.internal.fs_utils as fs_utils
    assert not hasattr(fs_utils, "write_workspace_status"), (
        "fs_utils must not expose write_workspace_status"
    )


def test_fs_utils_does_not_define_clear_workspace_status():
    import polaris.cells.workspace.integrity.internal.fs_utils as fs_utils
    assert not hasattr(fs_utils, "clear_workspace_status"), (
        "fs_utils must not expose clear_workspace_status"
    )


def test_fs_utils_does_not_define_read_workspace_status():
    import polaris.cells.workspace.integrity.internal.fs_utils as fs_utils
    assert not hasattr(fs_utils, "read_workspace_status"), (
        "fs_utils must not expose read_workspace_status"
    )


# ---------------------------------------------------------------------------
# 3. plan_template does NOT define its own status writers
# ---------------------------------------------------------------------------

def test_plan_template_does_not_define_write_workspace_status():
    assert not _module_defines_function(
        "polaris.cells.docs.court_workflow.internal.plan_template",
        "write_workspace_status",
    ), "plan_template must not define write_workspace_status"


def test_plan_template_does_not_define_clear_workspace_status():
    assert not _module_defines_function(
        "polaris.cells.docs.court_workflow.internal.plan_template",
        "clear_workspace_status",
    ), "plan_template must not define clear_workspace_status"


def test_plan_template_does_not_define_read_workspace_status():
    assert not _module_defines_function(
        "polaris.cells.docs.court_workflow.internal.plan_template",
        "read_workspace_status",
    ), "plan_template must not define read_workspace_status"


# ---------------------------------------------------------------------------
# 4. Public surface of workspace.integrity delegates to workspace_service
# ---------------------------------------------------------------------------

def test_public_service_write_is_from_workspace_service():
    from polaris.cells.workspace.integrity.internal import workspace_service as ws
    from polaris.cells.workspace.integrity.public import service as pub

    assert pub.write_workspace_status is ws.write_workspace_status, (
        "public service must re-export write_workspace_status from workspace_service"
    )


def test_public_service_clear_is_from_workspace_service():
    from polaris.cells.workspace.integrity.internal import workspace_service as ws
    from polaris.cells.workspace.integrity.public import service as pub

    assert pub.clear_workspace_status is ws.clear_workspace_status, (
        "public service must re-export clear_workspace_status from workspace_service"
    )


def test_public_service_read_is_from_workspace_service():
    from polaris.cells.workspace.integrity.internal import workspace_service as ws
    from polaris.cells.workspace.integrity.public import service as pub

    assert pub.read_workspace_status is ws.read_workspace_status, (
        "public service must re-export read_workspace_status from workspace_service"
    )


# ---------------------------------------------------------------------------
# 5. court_workflow public service does NOT re-export status writers
# ---------------------------------------------------------------------------

def test_court_workflow_public_does_not_export_write():
    import polaris.cells.docs.court_workflow.public.service as cw
    assert not hasattr(cw, "write_workspace_status"), (
        "court_workflow public service must not export write_workspace_status"
    )


def test_court_workflow_public_does_not_export_clear():
    import polaris.cells.docs.court_workflow.public.service as cw
    assert not hasattr(cw, "clear_workspace_status"), (
        "court_workflow public service must not export clear_workspace_status"
    )


def test_court_workflow_public_does_not_export_read():
    import polaris.cells.docs.court_workflow.public.service as cw
    assert not hasattr(cw, "read_workspace_status"), (
        "court_workflow public service must not export read_workspace_status"
    )


# ---------------------------------------------------------------------------
# 6. Round-trip: write → read → clear through canonical entry point
# ---------------------------------------------------------------------------

def test_write_read_clear_round_trip(tmp_path):
    from polaris.cells.workspace.integrity.internal.workspace_service import (
        clear_workspace_status,
        read_workspace_status,
        write_workspace_status,
    )

    workspace = str(tmp_path)

    write_workspace_status(
        workspace,
        status="NEEDS_DOCS_INIT",
        reason="test round-trip",
        actions=["INIT_DOCS_WIZARD"],
    )

    data = read_workspace_status(workspace)
    assert data is not None, "read_workspace_status must return a dict after write"
    assert data["status"] == "NEEDS_DOCS_INIT"
    assert data["reason"] == "test round-trip"
    assert data["actions"] == ["INIT_DOCS_WIZARD"]
    assert "timestamp" in data, "payload must contain 'timestamp' key"
    assert "workspace_path" in data

    # Status file must exist on disk
    from polaris.cells.workspace.integrity.internal.fs_utils import workspace_status_path
    path = workspace_status_path(workspace)
    assert os.path.isfile(path), f"status file must exist at {path}"

    # Verify it is valid UTF-8 JSON
    raw = open(path, encoding="utf-8").read()
    parsed = json.loads(raw)
    assert parsed["status"] == "NEEDS_DOCS_INIT"

    clear_workspace_status(workspace)
    assert not os.path.isfile(path), "clear_workspace_status must remove the file"
    assert read_workspace_status(workspace) is None


def test_write_with_extra_fields(tmp_path):
    from polaris.cells.workspace.integrity.internal.workspace_service import (
        clear_workspace_status,
        read_workspace_status,
        write_workspace_status,
    )

    workspace = str(tmp_path)
    write_workspace_status(
        workspace,
        status="READY",
        reason="all good",
        extra={"schema_version": 2, "env": "test"},
    )
    data = read_workspace_status(workspace)
    assert data is not None
    assert data["schema_version"] == 2
    assert data["env"] == "test"
    clear_workspace_status(workspace)


def test_clear_on_nonexistent_file_is_safe(tmp_path):
    from polaris.cells.workspace.integrity.internal.workspace_service import (
        clear_workspace_status,
    )
    # Must not raise even when the file does not exist
    clear_workspace_status(str(tmp_path))


def test_read_on_missing_file_returns_none(tmp_path):
    from polaris.cells.workspace.integrity.internal.workspace_service import (
        read_workspace_status,
    )
    assert read_workspace_status(str(tmp_path)) is None
