"""Tests for polaris.cells.factory.pipeline.public.contracts.

Covers dataclass validation, frozen semantics, tuple coercion, and error types.
All tests are pure — no I/O required.
"""

from __future__ import annotations

from typing import Any

import pytest

from polaris.cells.factory.pipeline.public.contracts import (
    CancelFactoryRunCommandV1,
    FactoryPipelineError,
    FactoryRunCompletedEventV1,
    FactoryRunResultV1,
    FactoryRunStartedEventV1,
    GetFactoryRunStatusQueryV1,
    ListFactoryRunsQueryV1,
    ProjectionBackMappingRefreshResultV1,
    ProjectionExperimentResultV1,
    ProjectionReprojectionResultV1,
    RefreshProjectionBackMappingCommandV1,
    ReprojectProjectionExperimentCommandV1,
    RunProjectionExperimentCommandV1,
    StartFactoryRunCommandV1,
)


class TestStartFactoryRunCommandV1:
    """Tests for StartFactoryRunCommandV1."""

    def test_valid_construction(self) -> None:
        cmd = StartFactoryRunCommandV1(workspace="/ws", run_name="test", stages=("a", "b"))
        assert cmd.workspace == "/ws"
        assert cmd.run_name == "test"
        assert cmd.stages == ("a", "b")

    def test_stages_coerced_to_tuple(self) -> None:
        cmd = StartFactoryRunCommandV1(workspace="/ws", run_name="test", stages=["a", "b"])
        assert isinstance(cmd.stages, tuple)

    def test_stages_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="stages must not be empty"):
            StartFactoryRunCommandV1(workspace="/ws", run_name="test", stages=[])

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            StartFactoryRunCommandV1(workspace="", run_name="test", stages=("a",))

    def test_empty_run_name_raises(self) -> None:
        with pytest.raises(ValueError, match="run_name"):
            StartFactoryRunCommandV1(workspace="/ws", run_name="", stages=("a",))

    def test_whitespace_stripped_from_stages(self) -> None:
        cmd = StartFactoryRunCommandV1(workspace="/ws", run_name="test", stages=(" a ", ""))
        assert cmd.stages == ("a",)

    def test_frozen_cannot_mutate(self) -> None:
        cmd = StartFactoryRunCommandV1(workspace="/ws", run_name="test", stages=("a",))
        with pytest.raises(AttributeError):
            cmd.workspace = "/other"


class TestCancelFactoryRunCommandV1:
    """Tests for CancelFactoryRunCommandV1."""

    def test_valid_construction(self) -> None:
        cmd = CancelFactoryRunCommandV1(workspace="/ws", run_id="r1", reason="timeout")
        assert cmd.reason == "timeout"

    def test_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            CancelFactoryRunCommandV1(workspace="/ws", run_id="r1", reason="")

    def test_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="run_id"):
            CancelFactoryRunCommandV1(workspace="/ws", run_id="", reason="timeout")


class TestGetFactoryRunStatusQueryV1:
    """Tests for GetFactoryRunStatusQueryV1."""

    def test_valid_construction(self) -> None:
        q = GetFactoryRunStatusQueryV1(workspace="/ws", run_id="r1")
        assert q.run_id == "r1"

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            GetFactoryRunStatusQueryV1(workspace="", run_id="r1")


class TestListFactoryRunsQueryV1:
    """Tests for ListFactoryRunsQueryV1."""

    def test_defaults(self) -> None:
        q = ListFactoryRunsQueryV1(workspace="/ws")
        assert q.limit == 50
        assert q.offset == 0

    def test_limit_too_low_raises(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            ListFactoryRunsQueryV1(workspace="/ws", limit=0)

    def test_negative_offset_raises(self) -> None:
        with pytest.raises(ValueError, match="offset"):
            ListFactoryRunsQueryV1(workspace="/ws", offset=-1)


class TestRunProjectionExperimentCommandV1:
    """Tests for RunProjectionExperimentCommandV1."""

    def test_defaults(self) -> None:
        cmd = RunProjectionExperimentCommandV1(workspace="/ws", scenario_id="s1", requirement="req")
        assert cmd.project_slug == "projection_lab"
        assert cmd.use_pm_llm is True
        assert cmd.run_verification is True
        assert cmd.overwrite is False

    def test_bool_coercion(self) -> None:
        cmd = RunProjectionExperimentCommandV1(
            workspace="/ws", scenario_id="s1", requirement="req", use_pm_llm=0, overwrite=1
        )
        assert cmd.use_pm_llm is False
        assert cmd.overwrite is True


class TestRefreshProjectionBackMappingCommandV1:
    """Tests for RefreshProjectionBackMappingCommandV1."""

    def test_valid(self) -> None:
        cmd = RefreshProjectionBackMappingCommandV1(workspace="/ws", experiment_id="e1")
        assert cmd.experiment_id == "e1"

    def test_empty_experiment_id_raises(self) -> None:
        with pytest.raises(ValueError, match="experiment_id"):
            RefreshProjectionBackMappingCommandV1(workspace="/ws", experiment_id="")


class TestReprojectProjectionExperimentCommandV1:
    """Tests for ReprojectProjectionExperimentCommandV1."""

    def test_valid(self) -> None:
        cmd = ReprojectProjectionExperimentCommandV1(workspace="/ws", experiment_id="e1", requirement="req")
        assert cmd.requirement == "req"


class TestFactoryRunStartedEventV1:
    """Tests for FactoryRunStartedEventV1."""

    def test_valid(self) -> None:
        evt = FactoryRunStartedEventV1(event_id="e1", workspace="/ws", run_id="r1", started_at="2024-01-01")
        assert evt.event_id == "e1"

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            FactoryRunStartedEventV1(event_id="", workspace="/ws", run_id="r1", started_at="ts")


class TestFactoryRunCompletedEventV1:
    """Tests for FactoryRunCompletedEventV1."""

    def test_valid(self) -> None:
        evt = FactoryRunCompletedEventV1(
            event_id="e1", workspace="/ws", run_id="r1", status="done", completed_at="ts"
        )
        assert evt.status == "done"
        assert evt.error_message is None

    def test_with_error_message(self) -> None:
        evt = FactoryRunCompletedEventV1(
            event_id="e1", workspace="/ws", run_id="r1", status="failed", completed_at="ts", error_message="oops"
        )
        assert evt.error_message == "oops"


class TestFactoryRunResultV1:
    """Tests for FactoryRunResultV1."""

    def test_defaults(self) -> None:
        res = FactoryRunResultV1(ok=True, workspace="/ws", run_id="r1", status="done")
        assert res.completed_stages == ()
        assert res.artifact_paths == ()

    def test_tuple_coercion(self) -> None:
        res = FactoryRunResultV1(
            ok=True, workspace="/ws", run_id="r1", status="done", completed_stages=["a", "b"]
        )
        assert res.completed_stages == ("a", "b")

    def test_empty_strings_filtered(self) -> None:
        res = FactoryRunResultV1(
            ok=True, workspace="/ws", run_id="r1", status="done", artifact_paths=["a", "", " b "]
        )
        assert res.artifact_paths == ("a", "b")


class TestProjectionExperimentResultV1:
    """Tests for ProjectionExperimentResultV1."""

    def test_to_dict(self) -> None:
        res = ProjectionExperimentResultV1(
            ok=True, workspace="/ws", experiment_id="e1", scenario_id="s1", project_root="/prj"
        )
        d = res.to_dict()
        assert d["ok"] is True
        assert d["workspace"] == "/ws"
        assert d["verification_ok"] is False

    def test_tuple_fields(self) -> None:
        res = ProjectionExperimentResultV1(
            ok=True,
            workspace="/ws",
            experiment_id="e1",
            scenario_id="s1",
            project_root="/prj",
            generated_files=["a.py"],
        )
        assert res.generated_files == ("a.py",)


class TestProjectionBackMappingRefreshResultV1:
    """Tests for ProjectionBackMappingRefreshResultV1."""

    def test_to_dict(self) -> None:
        res = ProjectionBackMappingRefreshResultV1(
            workspace="/ws", experiment_id="e1", project_root="/prj"
        )
        d = res.to_dict()
        assert d["workspace"] == "/ws"
        assert d["changed_files"] == []

    def test_mapping_fields_coerced(self) -> None:
        res = ProjectionBackMappingRefreshResultV1(
            workspace="/ws",
            experiment_id="e1",
            project_root="/prj",
            changed_files=[{"path": "a.py"}],
        )
        assert res.changed_files == ({"path": "a.py"},)


class TestProjectionReprojectionResultV1:
    """Tests for ProjectionReprojectionResultV1."""

    def test_to_dict(self) -> None:
        res = ProjectionReprojectionResultV1(
            ok=True, workspace="/ws", experiment_id="e1", scenario_id="s1", project_root="/prj"
        )
        d = res.to_dict()
        assert d["ok"] is True
        assert d["impacted_cell_ids"] == []


class TestFactoryPipelineError:
    """Tests for FactoryPipelineError."""

    def test_message(self) -> None:
        err = FactoryPipelineError("something failed")
        assert str(err) == "something failed"

    def test_code_default(self) -> None:
        err = FactoryPipelineError("fail")
        assert err.code == "factory_pipeline_error"

    def test_custom_code(self) -> None:
        err = FactoryPipelineError("fail", code="custom_code")
        assert err.code == "custom_code"

    def test_details(self) -> None:
        err = FactoryPipelineError("fail", details={"key": "value"})
        assert err.details == {"key": "value"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError):
            FactoryPipelineError("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError):
            FactoryPipelineError("fail", code="")
