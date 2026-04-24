"""Tests for polaris.cells.audit.verdict.internal.artifact_service."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from polaris.cells.audit.verdict.internal.artifact_service import (
    ARTIFACT_REGISTRY,
    LEGACY_KEY_MAPPING,
    LEGACY_PATH_ALIASES,
    ArtifactService,
    create_artifact_service,
    get_artifact_key,
    get_artifact_path,
    get_artifact_policy_metadata,
    list_artifact_keys,
    should_archive_artifact,
    should_compress_artifact,
)


class TestArtifactRegistry:
    """Artifact registry tests."""

    def test_all_keys_have_paths(self) -> None:
        for key in ARTIFACT_REGISTRY:
            assert ARTIFACT_REGISTRY[key], f"Key {key} has empty path"

    def test_legacy_key_mapping_resolves(self) -> None:
        assert LEGACY_KEY_MAPPING["PLAN"] == "contract.plan"
        assert LEGACY_KEY_MAPPING["PM_TASKS_CONTRACT"] == "contract.pm_tasks"
        assert LEGACY_KEY_MAPPING["RUNTIME_EVENTS"] == "audit.events.runtime"

    def test_legacy_path_aliases(self) -> None:
        assert "runtime/contracts/pm_tasks.json" in LEGACY_PATH_ALIASES
        assert LEGACY_PATH_ALIASES["runtime/contracts/pm_tasks.json"] == "contract.pm_tasks"

    def test_list_artifact_keys_sorted(self) -> None:
        keys = list_artifact_keys()
        assert keys == sorted(keys)

    def test_get_artifact_path_valid_key(self) -> None:
        path = get_artifact_path("contract.plan")
        assert path == "runtime/contracts/plan.md"

    def test_get_artifact_path_legacy_key(self) -> None:
        path = get_artifact_path("PLAN")
        assert path == "runtime/contracts/plan.md"

    def test_get_artifact_path_invalid_key_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown artifact key"):
            get_artifact_path("INVALID_KEY_THAT_DOES_NOT_EXIST")

    def test_get_artifact_key_valid_path(self) -> None:
        key = get_artifact_key("runtime/contracts/plan.md")
        assert key == "contract.plan"

    def test_get_artifact_key_unknown_path(self) -> None:
        key = get_artifact_key("unknown/path/file.md")
        assert key is None


class TestPolicyMetadata:
    """Policy metadata tests."""

    def test_get_artifact_policy_metadata_valid(self) -> None:
        metadata = get_artifact_policy_metadata("contract.plan")
        assert metadata is not None
        assert "category" in metadata
        assert "lifecycle" in metadata
        assert "compress" in metadata
        assert "archive_on_terminal" in metadata

    def test_get_artifact_policy_metadata_legacy_key(self) -> None:
        metadata = get_artifact_policy_metadata("PLAN")
        assert metadata is not None
        assert metadata == get_artifact_policy_metadata("contract.plan")

    def test_get_artifact_policy_metadata_unknown_key(self) -> None:
        metadata = get_artifact_policy_metadata("unknown.key")
        assert metadata is None

    def test_should_compress_artifact_compress_true(self) -> None:
        assert should_compress_artifact("audit.events.runtime") is True

    def test_should_compress_artifact_compress_false(self) -> None:
        assert should_compress_artifact("contract.plan") is False

    def test_should_archive_artifact_archive_true(self) -> None:
        assert should_archive_artifact("contract.pm_tasks") is True

    def test_should_archive_artifact_archive_false(self) -> None:
        assert should_archive_artifact("contract.plan") is False


class TestArtifactService:
    """ArtifactService tests."""

    @pytest.fixture
    def workspace(self) -> Generator[Path, None, None]:
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_init_with_relative_path(self) -> None:
        service = ArtifactService(".")
        assert service.workspace == os.path.abspath(".")

    def test_init_with_absolute_path(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        assert service.workspace == os.path.abspath(workspace)

    def test_init_with_cache_root(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace), cache_root="/tmp/cache")
        assert service.cache_root == os.path.abspath("/tmp/cache")

    def test_get_path(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        path = service.get_path("contract.plan")
        assert "plan.md" in path

    def test_exists_false_for_nonexistent(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        assert service.exists("contract.plan") is False

    def test_exists_true_after_write(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        service.write_plan("# Test Plan")
        assert service.exists("contract.plan") is True

    def test_write_and_read_plan(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        content = "# Test Plan\n\nThis is a test."
        service.write_plan(content)
        read_content = service.read_plan()
        assert read_content == content

    def test_read_nonexistent_plan_returns_empty(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        content = service.read_plan()
        assert content == ""

    def test_write_and_read_gap_report(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        content = "# Gap Report\n\nNo gaps found."
        service.write_gap_report(content)
        read_content = service.read_gap_report()
        assert read_content == content

    def test_write_and_read_task_contract(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        data = {"tasks": [{"id": "t1", "status": "pending"}], "overall_goal": "test"}
        service.write_task_contract(data)
        read_data = service.read_task_contract()
        assert read_data is not None
        assert read_data["overall_goal"] == "test"
        assert len(read_data["tasks"]) == 1

    def test_read_nonexistent_task_contract_returns_none(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        data = service.read_task_contract()
        assert data is None

    def test_write_and_read_pm_report(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        content = "# PM Report\n\nTasks completed."
        service.write_pm_report(content)
        read_content = service.read_pm_report()
        assert read_content == content

    def test_write_and_read_pm_state(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        data = {"current_task": "t1", "completed": []}
        service.write_pm_state(data)
        read_data = service.read_pm_state()
        assert read_data is not None
        assert read_data["current_task"] == "t1"

    def test_write_and_read_director_result(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        data = {"success": True, "files_modified": ["a.py"]}
        service.write_director_result(data)
        read_data = service.read_director_result()
        assert read_data is not None
        assert read_data["success"] is True

    def test_write_and_read_director_status(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        data = {"running": True, "phase": "planning"}
        service.write_director_status(data)
        read_data = service.read_director_status()
        assert read_data is not None
        assert read_data["running"] is True

    def test_write_and_read_director_runlog(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        content = "# Runlog\n\nStep 1: Planning"
        service.write_director_runlog(content)
        read_content = service.read_director_runlog()
        assert read_content == content

    def test_write_and_read_qa_review(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        content = "# QA Review\n\nAll checks passed."
        service.write_qa_review(content)
        read_content = service.read_qa_review()
        assert read_content == content

    def test_write_and_read_last_state(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        data = {"turn": 5, "context": {"key": "value"}}
        service.write_last_state(data)
        read_data = service.read_last_state()
        assert read_data is not None
        assert read_data["turn"] == 5

    def test_write_and_read_engine_status(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        data = {"status": "idle", "memory_usage_mb": 100}
        service.write_engine_status(data)
        read_data = service.read_engine_status()
        assert read_data is not None
        assert read_data["status"] == "idle"

    def test_control_flag_pm_stop(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        assert service.is_pm_stop_requested() is False
        service.write_pm_stop_flag()
        assert service.is_pm_stop_requested() is True
        result = service.clear_pm_stop_flag()
        assert result is True
        assert service.is_pm_stop_requested() is False

    def test_control_flag_director_stop(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        assert service.is_director_stop_requested() is False
        service.write_director_stop_flag()
        assert service.is_director_stop_requested() is True
        result = service.clear_director_stop_flag()
        assert result is True
        assert service.is_director_stop_requested() is False

    def test_control_flag_pause(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        assert service.is_paused() is False
        service.write_pause_flag()
        assert service.is_paused() is True
        result = service.clear_pause_flag()
        assert result is True
        assert service.is_paused() is False

    def test_clear_flag_when_not_set(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        result = service.clear_pm_stop_flag()
        assert result is False

    def test_write_and_read_agents_draft(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        content = "# AGENTS Draft\n\nAgent definitions."
        service.write_agents_draft(content)
        read_content = service.read_agents_draft()
        assert read_content == content

    def test_write_and_read_agents_feedback(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        content = "# AGENTS Feedback\n\nSuggestions."
        service.write_agents_feedback(content)
        read_content = service.read_agents_feedback()
        assert read_content == content

    def test_write_and_read_runtime_events(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        events = [
            {"event_type": "task_started", "timestamp": "2024-01-01T00:00:00Z"},
            {"event_type": "task_completed", "timestamp": "2024-01-01T00:01:00Z"},
        ]
        service.write_runtime_events(events)
        read_events = service.read_runtime_events()
        assert len(read_events) == 2
        assert read_events[0]["event_type"] == "task_started"

    def test_read_runtime_events_with_limit(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        events = [{"event_type": f"event_{i}"} for i in range(10)]
        service.write_runtime_events(events)
        read_events = service.read_runtime_events(limit=3)
        assert len(read_events) == 3

    def test_read_runtime_events_nonexistent_returns_empty(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        events = service.read_runtime_events()
        assert events == []

    def test_get_runtime_events_path(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        path = service.get_runtime_events_path()
        assert "runtime.events.jsonl" in path

    def test_get_pm_events_path(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        path = service.get_pm_events_path()
        assert "pm.events.jsonl" in path

    def test_get_dialogue_transcript_path(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        path = service.get_dialogue_transcript_path()
        assert "dialogue.transcript.jsonl" in path

    def test_get_pm_llm_events_path(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        path = service.get_pm_llm_events_path()
        assert "pm.llm.events.jsonl" in path

    def test_get_director_llm_events_path(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        path = service.get_director_llm_events_path()
        assert "director.llm.events.jsonl" in path

    def test_generic_write_text_and_read(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        content = "Generic text content"
        service.write_text("contract.plan", content)
        read_content = service.read_text("contract.plan")
        assert read_content == content

    def test_generic_write_json_and_read(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        data = {"key": "value", "number": 42}
        service.write_json("runtime.state.last", data)
        read_data = service.read_json("runtime.state.last")
        assert read_data is not None
        assert read_data["key"] == "value"
        assert read_data["number"] == 42

    def test_read_json_invalid_key_raises(self, workspace: Path) -> None:
        service = ArtifactService(str(workspace))
        with pytest.raises(KeyError):
            service.read_json("contract.nonexistent")


class TestCreateArtifactService:
    """Factory function tests."""

    def test_create_with_defaults(self) -> None:
        service = create_artifact_service(".")
        assert isinstance(service, ArtifactService)

    def test_create_with_cache_root(self) -> None:
        service = create_artifact_service(".", cache_root="/tmp/cache")
        assert isinstance(service, ArtifactService)
        assert service.cache_root == os.path.abspath("/tmp/cache")


class TestMultipleServiceInstances:
    """Test that multiple instances are isolated."""

    def test_instances_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            service1 = ArtifactService(tmp1)
            service2 = ArtifactService(tmp2)

            service1.write_plan("# Plan 1")
            service2.write_plan("# Plan 2")

            assert service1.read_plan() == "# Plan 1"
            assert service2.read_plan() == "# Plan 2"
