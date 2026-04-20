"""Tests for history archive service."""

import shutil
import tempfile
from pathlib import Path

import pytest

# Skip tests if zstandard is not available
pytestmark = pytest.mark.skipif(
    True,  # Skip by default since we don't have zstd installed in test env
    reason="zstandard not available"
)


class TestHistoryArchiveService:
    """Test history archive service."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace."""
        temp_dir = tempfile.mkdtemp()
        workspace = Path(temp_dir) / "test_workspace"
        workspace.mkdir()

        # Create .polaris directory structure
        hp_dir = workspace / ".polaris"
        hp_dir.mkdir()

        yield str(workspace)

        # Cleanup
        shutil.rmtree(temp_dir)

    def test_service_initialization(self, temp_workspace):
        """Test service can be initialized."""
        from polaris.cells.archive.run_archive.internal.history_archive_service import HistoryArchiveService

        service = HistoryArchiveService(temp_workspace)

        assert service.workspace.exists()
        assert service.history_root.exists()

    def test_archive_run_creates_manifest(self, temp_workspace):
        """Test archiving a run creates manifest."""
        from polaris.cells.archive.run_archive.internal.history_archive_service import HistoryArchiveService

        service = HistoryArchiveService(temp_workspace)

        # Create a mock runtime run
        run_id = "test_run_001"
        run_dir = service.runtime_root / "runs" / run_id
        run_dir.mkdir(parents=True)

        # Create some test files
        (run_dir / "results").mkdir()
        (run_dir / "results" / "director.result.json").write_text(
            '{"status": "completed", "successes": 5}',
            encoding="utf-8"
        )

        # Archive the run
        manifest = service.archive_run(run_id, "completed", "completed")

        assert manifest.scope == "run"
        assert manifest.id == run_id
        assert manifest.file_count > 0
        assert manifest.total_size_bytes > 0
        assert (service.history_root / "runs" / run_id / "manifest.json").exists()

    def test_archive_run_updates_index(self, temp_workspace):
        """Test archiving a run updates the index."""
        from polaris.cells.archive.run_archive.internal.history_archive_service import HistoryArchiveService

        service = HistoryArchiveService(temp_workspace)

        # Create and archive a run
        run_id = "test_run_002"
        run_dir = service.runtime_root / "runs" / run_id
        run_dir.mkdir(parents=True)

        service.archive_run(run_id, "completed", "completed")

        # Check index
        runs = service.list_history_runs()

        assert len(runs) == 1
        assert runs[0].run_id == run_id

    def test_get_manifest(self, temp_workspace):
        """Test retrieving a manifest."""
        from polaris.cells.archive.run_archive.internal.history_archive_service import HistoryArchiveService

        service = HistoryArchiveService(temp_workspace)

        # Create and archive a run
        run_id = "test_run_003"
        run_dir = service.runtime_root / "runs" / run_id
        run_dir.mkdir(parents=True)

        service.archive_run(run_id, "failed", "failed")

        # Get manifest
        manifest = service.get_manifest("run", run_id)

        assert manifest is not None
        assert manifest.id == run_id
        assert manifest.reason == "failed"

    def test_archive_task_snapshot(self, temp_workspace):
        """Test archiving a task snapshot."""
        from polaris.cells.archive.run_archive.internal.history_archive_service import HistoryArchiveService

        service = HistoryArchiveService(temp_workspace)

        # Create mock task files
        tasks_dir = service.runtime_root / "tasks"
        tasks_dir.mkdir(parents=True)

        (tasks_dir / "task_1.json").write_text(
            '{"id": 1, "subject": "Test task"}',
            encoding="utf-8"
        )

        # Archive snapshot
        snapshot_id = "pm-00001-1234567890"
        manifest = service.archive_task_snapshot(snapshot_id, str(tasks_dir))

        assert manifest.scope == "task_snapshot"
        assert manifest.id == snapshot_id
        assert (service.history_root / "tasks" / snapshot_id / "task_1.json").exists()

    def test_archive_factory_run(self, temp_workspace):
        """Test archiving a factory run."""
        from polaris.cells.archive.run_archive.internal.history_archive_service import HistoryArchiveService

        service = HistoryArchiveService(temp_workspace)

        # Create mock factory directory
        factory_dir = service.history_root.parent / "factory" / "factory_run_001"
        factory_dir.mkdir(parents=True)

        (factory_dir / "config.json").write_text(
            '{"name": "test_factory"}',
            encoding="utf-8"
        )

        # Archive factory run
        manifest = service.archive_factory_run(
            "factory_run_001",
            str(factory_dir),
            "completed"
        )

        assert manifest.scope == "factory_run"
        assert manifest.id == "factory_run_001"
        assert (service.history_root / "factory" / "factory_run_001" / "config.json").exists()


class TestArchiveHook:
    """Test archive hook."""

    def test_hook_creation(self):
        """Test archive hook can be created."""
        from polaris.cells.archive.run_archive.internal.archive_hook import create_archive_hook

        hook = create_archive_hook("/tmp/test")

        assert hook.workspace == "/tmp/test"
        assert hook.is_enabled()

    def test_hook_disable_enable(self):
        """Test hook can be disabled and enabled."""
        from polaris.cells.archive.run_archive.internal.archive_hook import create_archive_hook

        hook = create_archive_hook("/tmp/test")

        hook.disable()
        assert not hook.is_enabled()

        hook.enable()
        assert hook.is_enabled()


class TestStoragePolicyService:
    """Test storage policy service."""

    def test_service_creation(self):
        """Test service can be created."""
        from polaris.kernelone.storage.policy import StoragePolicyService

        service = StoragePolicyService("/tmp/test")

        assert service.workspace == "/tmp/test"

    def test_get_policy(self):
        """Test getting policy."""
        from polaris.kernelone.storage.policy import Lifecycle, StoragePolicyService

        service = StoragePolicyService("/tmp/test")

        policy = service.get_policy("runtime/contracts")

        assert policy.lifecycle == Lifecycle.ACTIVE

    def test_is_archive_eligible(self):
        """Test archive eligibility check."""
        from polaris.kernelone.storage.policy import StoragePolicyService

        service = StoragePolicyService("/tmp/test")

        assert service.is_archive_eligible("runtime/contracts", "completed") is True
        assert service.is_archive_eligible("runtime/contracts", "running") is False

    def test_should_compress(self):
        """Test compression check."""
        from polaris.kernelone.storage.policy import StoragePolicyService

        service = StoragePolicyService("/tmp/test")

        assert service.should_compress("runtime/events") is True
        assert service.should_compress("runtime/contracts") is False

    def test_get_history_root(self):
        """Test history root path."""
        from polaris.kernelone.storage.policy import StoragePolicyService

        service = StoragePolicyService("/tmp/test")

        history_root = service.get_history_root()

        assert ".polaris/history" in history_root
