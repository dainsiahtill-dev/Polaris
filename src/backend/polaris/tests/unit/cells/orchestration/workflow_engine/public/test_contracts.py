"""Tests for polaris.cells.orchestration.workflow_engine.public.contracts."""

from __future__ import annotations

import pytest
from polaris.cells.orchestration.workflow_engine.public.contracts import (
    CellActivityRegistryOps,
    CellHandlerRegistry,
    CellWorkflowRegistryOps,
)


class TestCellWorkflowRegistryOps:
    def test_list_workflows_not_implemented(self) -> None:
        ops = CellWorkflowRegistryOps()
        with pytest.raises(NotImplementedError):
            ops.list_workflows()

    def test_get_not_implemented(self) -> None:
        ops = CellWorkflowRegistryOps()
        with pytest.raises(NotImplementedError):
            ops.get("foo")


class TestCellActivityRegistryOps:
    def test_list_activities_not_implemented(self) -> None:
        ops = CellActivityRegistryOps()
        with pytest.raises(NotImplementedError):
            ops.list_activities()

    def test_get_not_implemented(self) -> None:
        ops = CellActivityRegistryOps()
        with pytest.raises(NotImplementedError):
            ops.get("bar")


class TestCellHandlerRegistry:
    def test_dataclass_fields(self) -> None:
        wf = CellWorkflowRegistryOps()
        act = CellActivityRegistryOps()
        reg = CellHandlerRegistry(workflows=wf, activities=act)
        assert reg.workflows is wf
        assert reg.activities is act
