"""Test script for the new architecture.

Verifies that the new infrastructure and domain layers work correctly.
"""

import sys
import tempfile
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "backend"))

from infrastructure import get_storage_adapter, PolarisSettings, get_settings
from infrastructure.storage import resolve_runtime_path, resolve_storage_roots
from domain.models import Task, TaskStatus, TaskPriority
from domain.services import BackgroundTaskService


def test_storage_adapter():
    """Test the new StorageAdapter."""
    print("Testing StorageAdapter...")

    with tempfile.TemporaryDirectory() as tmpdir:
        adapter = get_storage_adapter(tmpdir)

        # Test path resolution
        runtime_path = adapter.resolve_path("runtime/test/file.json")
        assert "runtime" in runtime_path, f"Expected 'runtime' in {runtime_path}"

        # Test write/read
        test_file = "runtime/test/data.json"
        test_data = {"key": "value", "number": 42}
        adapter.write_json(test_file, test_data)

        read_data = adapter.read_json(test_file)
        assert read_data == test_data, f"Expected {test_data}, got {read_data}"

        # Test text operations
        text_file = "runtime/test/hello.txt"
        adapter.write_text(text_file, "Hello, World!")
        text = adapter.read_text(text_file)
        assert text == "Hello, World!", f"Expected 'Hello, World!', got {text}"

        # Test JSONL
        jsonl_file = "runtime/test/events.jsonl"
        adapter.append_jsonl(jsonl_file, {"event": "test1"})
        adapter.append_jsonl(jsonl_file, {"event": "test2"})
        records = adapter.read_jsonl(jsonl_file)
        assert len(records) == 2, f"Expected 2 records, got {len(records)}"

    print("  StorageAdapter: PASSED")


def test_settings():
    """Test the new Settings."""
    print("Testing Settings...")

    # Clear cache to get fresh settings
    get_settings.cache_clear()

    settings = get_settings()
    assert isinstance(settings, PolarisSettings)

    # Test defaults
    assert settings.max_iterations == 50
    assert settings.background_max_concurrent == 2
    assert settings.default_model == "claude-sonnet-4"

    # Test computed properties
    home = settings.get_home()
    assert ".polaris" in home or "polaris" in home.lower()

    print("  Settings: PASSED")


def test_task_model():
    """Test the unified Task model."""
    print("Testing Task model...")

    # Create task
    task = Task(
        id="task-123",
        title="Test Task",
        goal="Test the task model",
        status=TaskStatus.PENDING,
        priority=TaskPriority.HIGH,
        dependencies=["dep-1", "dep-2"],
    )

    # Test properties
    assert task.can_start is False  # Has dependencies, can't start
    assert task.is_terminal is False
    assert task.is_blocked is True  # Has dependencies, so it's blocked

    # Test state transitions
    task.started_at = __import__('datetime').datetime.now()
    task.status = TaskStatus.IN_PROGRESS

    assert task.is_terminal is False

    # Complete task
    task.complete("Task completed successfully", ["evidence-1"])
    assert task.status == TaskStatus.COMPLETED
    assert task.is_terminal is True
    assert "evidence-1" in task.evidence_refs

    # Test serialization
    data = task.to_dict()
    assert data["id"] == "task-123"
    assert data["status"] == "COMPLETED"
    assert data["priority"] == "high"

    # Test deserialization
    restored = Task.from_dict(data)
    assert restored.id == task.id
    assert restored.status == task.status
    assert restored.priority == task.priority

    print("  Task model: PASSED")


def test_background_task_service():
    """Test the unified BackgroundTaskService."""
    print("Testing BackgroundTaskService...")

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = get_storage_adapter(tmpdir)
        service = BackgroundTaskService.with_defaults(storage, max_concurrent=2)

        # This is a basic smoke test - full async testing would require pytest-asyncio
        assert service is not None
        assert service._semaphore._value == 2

    print("  BackgroundTaskService: PASSED (smoke test)")


def test_backward_compatibility():
    """Test that new layout module is backward compatible."""
    print("Testing backward compatibility...")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Test old API still works
        roots = resolve_storage_roots(tmpdir)
        assert roots.workspace_abs == os.path.abspath(tmpdir)
        assert roots.workspace_key is not None

        # Test path resolution
        path = resolve_runtime_path(tmpdir, "runtime/contracts/plan.md")
        assert os.path.isabs(path)
        assert "runtime" in path

    print("  Backward compatibility: PASSED")


def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing New Architecture")
    print("=" * 60)

    try:
        test_storage_adapter()
        test_settings()
        test_task_model()
        test_background_task_service()
        test_backward_compatibility()

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
        return 0

    except AssertionError as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
