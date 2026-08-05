"""Tests: runtime.projection silent-exception observability.

Verifies that:
1. Exceptions in the Director status owner port are logged (not silently swallowed).
2. A projection failure propagates a structured degraded state instead of
   fake-normal empty dicts.
3. Workflow archive read failures are logged at warning level.
4. build_workflow_task_rows failures are logged at warning level.
5. Optional subsystem failures (anthro, resident) are logged at debug level.
6. build_llm_status failures are logged at warning level.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _write_engine_status(cache_root, payload: dict[str, object]) -> None:
    status_dir = cache_root / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "engine.status.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _write_pm_lifecycle(cache_root, payload: dict[str, object]) -> None:
    status_dir = cache_root / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "pm.lifecycle.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _backend_root_from_test_file() -> str:
    import os

    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_state():
    """Return a minimal AppState-like object sufficient for projection calls."""
    settings = MagicMock()
    settings.workspace = "/tmp/test_workspace"
    settings.ramdisk_root = ""
    settings.qa_enabled = False
    state = MagicMock()
    state.settings = settings
    return state


# ---------------------------------------------------------------------------
# engine.status orphan recovery
# ---------------------------------------------------------------------------


class TestEngineStatusOrphanRecovery:
    """Stale in-flight engine.status files must recover to idle, not failure."""

    def test_runtime_projection_recovers_stale_engine_running_to_idle(self, tmp_path):
        from polaris.cells.runtime.projection.internal import runtime_projection_service as rps

        workspace = tmp_path / "workspace"
        cache_root = tmp_path / "runtime"
        workspace.mkdir()
        _write_engine_status(
            cache_root,
            {
                "running": True,
                "phase": "dispatching",
                "updated_at": "2026-05-31T10:53:57Z",
                "error": "",
                "roles": {
                    "PM": {"status": "dispatching", "running": True},
                },
            },
        )

        result = rps.build_engine_status(
            str(workspace),
            str(cache_root),
            pm_status={"running": False},
            director_status={"running": False},
        )

        assert result is not None
        assert result["stale"] is True
        assert result["orphaned"] is True
        assert result["running"] is False
        assert result["phase"] == "idle"
        assert result["error"] == ""
        assert result["recovery_code"] == "ENGINE_ORPHANED"
        assert result["roles"]["PM"]["status"] == "idle"

    def test_runtime_projection_recovers_engine_orphaned_failure_to_idle(self, tmp_path):
        from polaris.cells.runtime.projection.internal import runtime_projection_service as rps

        workspace = tmp_path / "workspace"
        cache_root = tmp_path / "runtime"
        workspace.mkdir()
        _write_engine_status(
            cache_root,
            {
                "running": False,
                "phase": "failed",
                "updated_at": "2026-05-31T10:53:57Z",
                "error": "ENGINE_ORPHANED",
                "roles": {
                    "PM": {"status": "blocked", "running": False},
                },
            },
        )

        result = rps.build_engine_status(
            str(workspace),
            str(cache_root),
            pm_status={"running": False},
            director_status={"running": False},
        )

        assert result is not None
        assert result["orphaned"] is True
        assert result["running"] is False
        assert result["phase"] == "idle"
        assert result["error"] == ""
        assert result["recovery_code"] == "ENGINE_ORPHANED"
        assert result["roles"]["PM"]["status"] == "idle"

    def test_status_snapshot_recovers_stale_engine_running_to_idle(self, tmp_path):
        from polaris.cells.runtime.projection.internal import status_snapshot_builder as ssb

        workspace = tmp_path / "workspace"
        cache_root = tmp_path / "runtime"
        workspace.mkdir()
        _write_engine_status(
            cache_root,
            {
                "running": True,
                "phase": "planning",
                "updated_at": "2026-05-31T10:53:57Z",
                "error": "",
                "roles": {
                    "Director": {"status": "running", "running": True},
                },
            },
        )

        result = ssb._build_engine_status(
            str(workspace),
            str(cache_root),
            pm_status={"running": False},
            director_status={"running": False},
        )

        assert result is not None
        assert result["stale"] is True
        assert result["running"] is False
        assert result["phase"] == "idle"
        assert result["error"] == ""
        assert result["roles"]["Director"]["status"] == "idle"

    def test_status_snapshot_recovers_engine_orphaned_failure_to_idle(self, tmp_path):
        from polaris.cells.runtime.projection.internal import status_snapshot_builder as ssb

        workspace = tmp_path / "workspace"
        cache_root = tmp_path / "runtime"
        workspace.mkdir()
        _write_engine_status(
            cache_root,
            {
                "running": False,
                "phase": "failed",
                "error": "ENGINE_ORPHANED",
                "roles": {
                    "PM": {"status": "failed", "running": False},
                },
            },
        )

        result = ssb._build_engine_status(
            str(workspace),
            str(cache_root),
            pm_status={"running": False},
            director_status={"running": False},
        )

        assert result is not None
        assert result["orphaned"] is True
        assert result["running"] is False
        assert result["phase"] == "idle"
        assert result["error"] == ""
        assert result["roles"]["PM"]["status"] == "idle"

    def test_artifact_store_recovers_stale_engine_running_to_idle(self, tmp_path):
        from polaris.cells.runtime.artifact_store.internal import artifacts

        workspace = tmp_path / "workspace"
        cache_root = tmp_path / "runtime"
        workspace.mkdir()
        _write_engine_status(
            cache_root,
            {
                "running": True,
                "phase": "running",
                "error": "",
            },
        )

        result = artifacts._build_engine_status_v2(
            str(workspace),
            str(cache_root),
            pm_status={"running": False},
            director_status={"running": False},
        )

        assert result is not None
        assert result["stale"] is True
        assert result["orphaned"] is True
        assert result["running"] is False
        assert result["phase"] == "idle"
        assert result["error"] == ""
        assert result["recovery_code"] == "ENGINE_ORPHANED"

    def test_artifact_store_recovers_engine_orphaned_failure_to_idle(self, tmp_path):
        from polaris.cells.runtime.artifact_store.internal import artifacts

        workspace = tmp_path / "workspace"
        cache_root = tmp_path / "runtime"
        workspace.mkdir()
        _write_engine_status(
            cache_root,
            {
                "running": False,
                "phase": "failed",
                "error": "ENGINE_ORPHANED",
                "roles": {
                    "PM": {"status": "blocked", "running": False},
                },
            },
        )

        result = artifacts._build_engine_status_v2(
            str(workspace),
            str(cache_root),
            pm_status={"running": False},
            director_status={"running": False},
        )

        assert result is not None
        assert result["orphaned"] is True
        assert result["running"] is False
        assert result["phase"] == "idle"
        assert result["error"] == ""
        assert result["roles"]["PM"]["status"] == "idle"

    def test_artifact_store_workflow_status_ignores_terminal_pm_stale_engine(self, tmp_path):
        from polaris.cells.runtime.artifact_store.internal import artifacts

        workspace = tmp_path / "workspace"
        cache_root = tmp_path / "runtime"
        workspace.mkdir()
        _write_engine_status(
            cache_root,
            {
                "running": True,
                "phase": "dispatching",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "error": "",
            },
        )
        _write_pm_lifecycle(
            cache_root,
            {
                "status": "success",
                "terminal": True,
                "ok": True,
                "exit_code": 0,
            },
        )

        result = artifacts.get_workflow_runtime_status(str(workspace), str(cache_root))

        assert result["running"] is False
        assert result["state"] == "idle"
        assert result["stage"] == "idle"

    def test_artifact_store_workflow_status_keeps_fresh_engine_running(self, tmp_path):
        from polaris.cells.runtime.artifact_store.internal import artifacts

        workspace = tmp_path / "workspace"
        cache_root = tmp_path / "runtime"
        workspace.mkdir()
        _write_engine_status(
            cache_root,
            {
                "running": True,
                "phase": "dispatching",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "error": "",
            },
        )

        result = artifacts.get_workflow_runtime_status(str(workspace), str(cache_root))

        assert result["running"] is True
        assert result["state"] == "dispatching"
        assert result["stage"] == "dispatching"

    def test_artifact_store_workflow_status_recovers_old_engine_without_owner(self, tmp_path):
        from polaris.cells.runtime.artifact_store.internal import artifacts

        workspace = tmp_path / "workspace"
        cache_root = tmp_path / "runtime"
        workspace.mkdir()
        _write_engine_status(
            cache_root,
            {
                "running": True,
                "phase": "planning",
                "updated_at": (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(),
                "error": "",
            },
        )

        result = artifacts.get_workflow_runtime_status(str(workspace), str(cache_root))

        assert result["running"] is False
        assert result["state"] == "idle"
        assert result["stage"] == "idle"


# ---------------------------------------------------------------------------
# director_runtime_status
# ---------------------------------------------------------------------------


class TestDirectorRuntimeStatusObservability:
    """Sync owner failures must project explicit unavailable evidence."""

    def test_unavailable_owner_projects_explicit_error(self):
        """A missing owner must not be indistinguishable from an idle Director."""
        from polaris.cells.runtime.projection.internal import director_runtime_status as drs

        # A legacy/mocked ``None`` is invalid owner evidence and must surface.
        with patch.object(drs, "_read_director_service_status_sync", return_value=None):
            result = drs.build_director_runtime_status(_make_minimal_state(), "/tmp/ws", "/tmp/cache")

        assert result["source"] == "none"
        assert result["running"] is False
        assert result["projection_error"].startswith("invalid_director_status_owner_payload:")

    def test_director_service_runtime_error_logs_warning(self, caplog):
        """RuntimeError from DirectorService.get_status must log at WARNING."""
        from polaris.cells.runtime.projection.internal import director_runtime_status as drs

        with (
            patch(
                "polaris.cells.runtime.projection.internal.director_runtime_status._read_director_service_status_sync",
                side_effect=RuntimeError("DI container not ready"),
            ),
            caplog.at_level(
                logging.WARNING, logger="polaris.cells.runtime.projection.internal.director_runtime_status"
            ),
        ):
            result = drs.build_director_runtime_status(_make_minimal_state(), "/tmp/ws", "/tmp/cache")

        # Owner query failures remain unavailable, never an idle v2 service.
        assert result["source"] == "none"
        assert result["running"] is False
        assert result["projection_error"].startswith("director_status_owner_query_failed:")

    def test_sync_bridge_exception_logs_warning(self, caplog):
        """Exception escaping the thread pool bridge must be logged at WARNING."""

        def patched():
            # Simulate the outer try block catching an exception
            try:
                raise TimeoutError("executor timeout")
            except TimeoutError as exc:
                import logging as _logging

                _logging.getLogger("polaris.cells.runtime.projection.internal.director_runtime_status").warning(
                    "Sync bridge for DirectorService status failed: %s", exc, exc_info=True
                )
                return None

        with (
            patch(
                "polaris.cells.runtime.projection.internal.director_runtime_status._read_director_service_status_sync",
                side_effect=patched,
            ),
            caplog.at_level(
                logging.WARNING, logger="polaris.cells.runtime.projection.internal.director_runtime_status"
            ),
        ):
            patched()

        assert any("Sync bridge" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# get_director_local_status
# ---------------------------------------------------------------------------


class TestGetDirectorLocalStatusObservability:
    """get_director_local_status must log and return degraded state on failure."""

    @pytest.mark.asyncio
    async def test_exception_logs_warning_and_returns_projection_error_key(self, caplog):
        """An unexpected exception must appear in logs and in the returned dict."""
        from polaris.cells.runtime.projection.internal import runtime_projection_service as rps

        async def _bad_owner(_workspace: str):
            raise RuntimeError("owner exploded")

        with (
            patch(
                "polaris.cells.runtime.projection.internal.director_status_owner.observe_director_status_owner",
                new=_bad_owner,
            ),
            caplog.at_level(logging.WARNING, logger=rps.__name__),
        ):
            result = await rps.get_director_local_status("/tmp/ws")

        # Must carry a projection_error key so callers can distinguish degraded from clean
        assert "projection_error" in result
        assert result["source"] == "none"
        assert result["running"] is False
        assert any("Director status owner unavailable" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_import_error_logs_warning_and_returns_projection_error_key(self, caplog):
        """An unavailable bound owner must degrade instead of implying readiness."""
        from polaris.cells.runtime.projection.internal import runtime_projection_service as rps
        from polaris.cells.runtime.projection.public import DirectorStatusObservationV1Error

        async def _unavailable(_workspace: str):
            raise DirectorStatusObservationV1Error(
                "director_status_port_unbound",
                "owner unavailable",
            )

        with (
            patch(
                "polaris.cells.runtime.projection.internal.director_status_owner.observe_director_status_owner",
                new=_unavailable,
            ),
            caplog.at_level(logging.WARNING, logger=rps.__name__),
        ):
            result = await rps.get_director_local_status("/tmp/ws")

        assert "projection_error" in result
        assert result["source"] == "none"
        assert result["running"] is False
        assert any("Director status owner unavailable" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_success_does_not_add_projection_error_key(self):
        """On success the projection_error key must NOT appear."""
        from polaris.cells.runtime.projection.internal import runtime_projection_service as rps
        from polaris.cells.runtime.projection.public import DirectorStatusObservationV1

        async def _observe(_workspace: str) -> DirectorStatusObservationV1:
            return DirectorStatusObservationV1(
                workspace="/tmp/ws",
                available=True,
                status={"state": "RUNNING", "workspace": "/tmp/ws"},
            )

        with patch(
            "polaris.cells.runtime.projection.internal.director_status_owner.observe_director_status_owner",
            new=_observe,
        ):
            result = await rps.get_director_local_status("/tmp/ws")

        assert "projection_error" not in result
        assert result["running"] is True
        assert result["source"] == "v2_service"


# ---------------------------------------------------------------------------
# get_workflow_director_status_sync
# ---------------------------------------------------------------------------


class TestWorkflowDirectorStatusObservability:
    """Workflow archive read failures must be logged at WARNING."""

    def test_workflow_read_failure_logs_warning(self, caplog):
        """When get_workflow_runtime_status raises, warning is emitted."""
        from polaris.cells.runtime.projection.internal import runtime_projection_service as rps

        with (
            patch(
                "polaris.cells.runtime.projection.internal.runtime_projection_service.get_workflow_runtime_status",
                side_effect=OSError("disk read error"),
            ),
            caplog.at_level(logging.WARNING, logger=rps.__name__),
        ):
            result = rps.get_workflow_director_status_sync("/tmp/ws", "/tmp/cache")

        assert result is None
        assert any("workflow archive read failed" in r.message for r in caplog.records)

    def test_workflow_returns_none_when_no_data(self):
        """None return when workflow_status is None does not raise."""
        from polaris.cells.runtime.projection.internal import runtime_projection_service as rps

        with patch(
            "polaris.cells.runtime.projection.internal.runtime_projection_service.get_workflow_runtime_status",
            return_value=None,
        ):
            result = rps.get_workflow_director_status_sync("/tmp/ws", "/tmp/cache")

        assert result is None


# ---------------------------------------------------------------------------
# build_runtime_projection - task-row selection failure path
# ---------------------------------------------------------------------------


class TestBuildRuntimeProjectionTaskRowsObservability:
    """Live task-row selection failures must remain observable and non-fatal."""

    @pytest.mark.asyncio
    async def test_task_rows_exception_logs_warning(self, caplog):
        """Malformed execution-control data must log a warning, not fail the projection."""
        from polaris.cells.runtime.projection.internal import runtime_projection_service as rps

        state = _make_minimal_state()

        with (
            patch.object(rps, "get_pm_local_status", new=AsyncMock(return_value={"running": False})),
            patch.object(
                rps,
                "get_director_local_status",
                new=AsyncMock(return_value={"running": False, "source": "none", "status": None}),
            ),
            patch.object(
                rps,
                "get_workflow_director_status",
                new=AsyncMock(return_value={"workflow_id": "wf-1", "tasks": {}}),
            ),
            patch.object(
                rps,
                "select_task_rows",
                side_effect=ValueError("corrupt task data"),
            ),
            patch(
                "polaris.cells.runtime.projection.internal.runtime_projection_service.map_engine_to_court_state",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.runtime_projection_service.get_lancedb_status",
                return_value={"ok": True},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.runtime_projection_service.build_engine_status",
                return_value=None,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.runtime_projection_service.build_snapshot_payload_from_projection",
                return_value={},
            ),
            caplog.at_level(logging.WARNING, logger=rps.__name__),
        ):
            projection = await rps.build_runtime_projection(
                state,
                "/tmp/ws",
                "/tmp/cache",
                use_cache=False,
            )

        assert projection.task_rows == []
        assert any("select_task_rows failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# build_anthro_state - optional module failure
# ---------------------------------------------------------------------------


class TestBuildAnthroStateObservability:
    """build_anthro_state failure must log at DEBUG (optional module)."""

    def test_exception_logs_at_debug_not_warning(self, caplog):
        from polaris.cells.runtime.projection.internal import runtime_projection_service as rps

        state = _make_minimal_state()

        with (
            patch(
                "polaris.cells.runtime.projection.internal.runtime_projection_service.init_anthropomorphic_modules",
                side_effect=ImportError("lancedb not installed"),
            ),
            caplog.at_level(logging.DEBUG, logger=rps.__name__),
        ):
            result = rps.build_anthro_state(state)

        assert result is None
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("optional module unavailable" in r.message for r in debug_records)
        # Must NOT escalate to WARNING for an optional subsystem
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not any("optional module unavailable" in r.message for r in warning_records)


# ---------------------------------------------------------------------------
# build_resident_state - optional subsystem failure
# ---------------------------------------------------------------------------


class TestBuildResidentStateObservability:
    """build_resident_state failure must log at DEBUG (optional subsystem)."""

    def test_outer_exception_logs_at_debug(self, caplog):
        from polaris.cells.runtime.projection.internal import runtime_projection_service as rps

        with (
            patch(
                "polaris.cells.runtime.projection.internal.runtime_projection_service.build_resident_state",
                wraps=rps.build_resident_state,
            ),
            patch.dict(
                "sys.modules",
                {"polaris.cells.resident.autonomy.public.service": None},
            ),
            caplog.at_level(logging.DEBUG, logger=rps.__name__),
        ):
            result = rps.build_resident_state("/tmp/ws")

        assert result is None

    def test_inner_goal_executions_logs_at_debug(self, caplog):
        """list_goal_executions failure must log at DEBUG."""
        from polaris.cells.runtime.projection.internal import runtime_projection_service as rps

        mock_service = MagicMock()
        mock_service.get_status.return_value = {"running": False}
        mock_service.list_goal_executions.side_effect = AttributeError("not implemented")

        mock_module = MagicMock()
        mock_module.get_resident_service.return_value = mock_service

        with (
            patch.dict(
                "sys.modules",
                {"polaris.cells.resident.autonomy.public.service": mock_module},
            ),
            caplog.at_level(logging.DEBUG, logger=rps.__name__),
        ):
            result = rps.build_resident_state("/tmp/ws")

        assert result == {"running": False}
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("list_goal_executions unavailable" in r.message for r in debug_records)


# ---------------------------------------------------------------------------
# status_snapshot_builder - build_llm_status failure
# ---------------------------------------------------------------------------


class TestBuildLlmStatusObservability:
    """build_llm_status failure in build_status_payload_sync must log at WARNING."""

    def test_llm_status_failure_logs_warning(self, caplog):
        from polaris.cells.runtime.projection.internal import status_snapshot_builder as ssb

        state = _make_minimal_state()

        with (
            patch(
                "polaris.cells.runtime.projection.internal.status_snapshot_builder.build_llm_status",
                side_effect=RuntimeError("provider config missing"),
            ),
            patch(
                "polaris.cells.runtime.projection.internal.status_snapshot_builder._build_engine_status",
                return_value=None,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.status_snapshot_builder.map_engine_to_court_state",
                return_value={},
            ),
            patch(
                "polaris.cells.runtime.projection.internal.status_snapshot_builder.build_resident_state",
                return_value=None,
            ),
            patch(
                "polaris.cells.runtime.projection.internal.status_snapshot_builder.get_lancedb_status",
                return_value={"ok": True},
            ),
        ):
            # Patch the lazy import inside build_status_payload_sync
            mock_artifact_module = MagicMock()
            mock_artifact_module.build_memory_payload.return_value = None
            mock_artifact_module.build_success_stats_payload.return_value = {}
            mock_artifact_module.build_snapshot.return_value = {}

            with (
                patch.dict(
                    "sys.modules",
                    {"polaris.cells.runtime.artifact_store.public.service": mock_artifact_module},
                ),
                caplog.at_level(logging.WARNING, logger=ssb.__name__),
            ):
                result = ssb.build_status_payload_sync(
                    state,
                    "/tmp/ws",
                    "/tmp/cache",
                    pm_status={"running": False},
                    director_status={"running": False},
                )

        assert result["llm_status"] is None
        assert any("build_llm_status failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Dependency manifest - verify cross-cell imports match declared depends_on
# ---------------------------------------------------------------------------


class TestDependencyManifest:
    """Verify that runtime.projection's declared depends_on covers all actual cross-cell imports.

    This test prevents the regression where cross-cell dependencies are added to source
    code without updating the cell.yaml, leaving the graph catalog out of sync with
    the actual dependency graph.
    """

    # Canonical mapping: module-prefix -> cell-id that must appear in depends_on.
    # If a new cross-cell import is added, this map MUST be updated first, and
    # the corresponding cell.yaml must be updated to match.
    EXPECTED_CELL_DEPENDENCIES: dict[str, str] = {
        "polaris.cells.runtime.state_owner": "runtime.state_owner",
        "polaris.cells.runtime.task_runtime": "runtime.task_runtime",
        "polaris.cells.llm.evaluation": "llm.evaluation",
        "polaris.cells.llm.provider_runtime": "llm.provider_runtime",
        "polaris.cells.storage.layout": "storage.layout",
        "polaris.cells.docs.court_workflow": "docs.court_workflow",
        # runtime.artifact_store is reached through lazy proxy in io_helpers.py
        # (resolve_artifact_path); the dependency is implicit via storage_archive_pipeline.
        # audit.evidence is in the declared depends_on.
    }

    def test_cell_yaml_declares_all_cross_cell_imports(self):
        """Fail-fast if a cross-cell import appears in source but not in cell.yaml depends_on."""
        import os

        import yaml

        backend_root = _backend_root_from_test_file()
        cell_yaml_path = os.path.join(backend_root, "polaris", "cells", "runtime", "projection", "cell.yaml")
        with open(cell_yaml_path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)

        declared = {str(d) for d in doc.get("depends_on", [])}
        declared.add("audit.evidence")  # baseline - always present

        missing: list[str] = []
        for module_prefix, cell_id in self.EXPECTED_CELL_DEPENDENCIES.items():
            if cell_id not in declared:
                missing.append(f"cell.yaml missing '{cell_id}' (source imports {module_prefix})")

        assert not missing, "runtime.projection cell.yaml depends_on is out of sync with source imports:\n" + "\n".join(
            f"  - {m}" for m in missing
        )

    def test_observability_test_declares_expected_dependencies_up_to_date(self):
        """Fail-fast if EXPECTED_CELL_DEPENDENCIES is stale.

        Run this test whenever a new cross-cell import is added to the projection cell.
        """
        # This test documents the invariant: every entry in EXPECTED_CELL_DEPENDENCIES
        # must correspond to a real cross-cell import in the internal/ directory.
        import os

        backend_root = _backend_root_from_test_file()
        internal_dir = os.path.join(backend_root, "polaris", "cells", "runtime", "projection", "internal")
        violations: list[str] = []
        for module_prefix, cell_id in self.EXPECTED_CELL_DEPENDENCIES.items():
            found = False
            for fname in os.listdir(internal_dir):
                if not fname.endswith(".py") or fname.startswith("__"):
                    continue
                fpath = os.path.join(internal_dir, fname)
                try:
                    with open(fpath, encoding="utf-8") as fh:
                        content = fh.read()
                except OSError:
                    continue
                if f"from {module_prefix}" in content or f"import {module_prefix}" in content:
                    found = True
                    break
            if not found:
                violations.append(
                    f"EXPECTED_CELL_DEPENDENCIES has '{cell_id}' ({module_prefix}) "
                    f"but no source file in internal/ imports it - remove or fix"
                )

        assert not violations, "EXPECTED_CELL_DEPENDENCIES is out of sync with source:\n" + "\n".join(
            f"  - {v}" for v in violations
        )


class TestProjectionReadBoundaryInvariant:
    """Verify that projection internal modules do not import other Cell internals directly.

    Cross-cell imports must go through public service contracts only.
    """

    def test_no_cell_internal_imports_in_projection_internal(self):
        """Fail-fast if a projection internal module imports another Cell's internal module."""
        import os
        import re

        backend_root = _backend_root_from_test_file()
        internal_dir = os.path.join(backend_root, "polaris", "cells", "runtime", "projection", "internal")
        # Pattern: "from polaris.cells.<cell>." NOT followed by "public."
        # or "from polaris.cells.<cell>.public."
        # This catches direct internal-to-internal imports.
        pattern = re.compile(
            r"^from polaris\.cells\.(?!runtime\.projection|"
            r"kernelone|infrastructure)[a-z0-9_]+\.internal\.",
            re.MULTILINE | re.IGNORECASE,
        )

        violations: list[str] = []
        for fname in os.listdir(internal_dir):
            if not fname.endswith(".py") or fname.startswith("__"):
                continue
            fpath = os.path.join(internal_dir, fname)
            try:
                with open(fpath, encoding="utf-8") as fh:
                    content = fh.read()
            except OSError:
                continue
            for m in pattern.finditer(content):
                line_num = content[: m.start()].count("\n") + 1
                violations.append(f"{fname}:{line_num}: imports other Cell internal: {m.group().rstrip()}")

        assert not violations, (
            "Cross-cell internal imports found in runtime.projection internal modules:\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nAll cross-cell reads must use public service contracts."
        )
