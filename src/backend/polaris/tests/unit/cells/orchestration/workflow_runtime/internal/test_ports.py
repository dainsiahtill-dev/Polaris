"""Tests for workflow_runtime internal ports module."""

from __future__ import annotations

import pytest
from polaris.cells.orchestration.workflow_runtime.internal.ports import (
    RoleAdapterFactoryPort,
    RoleOrchestrationAdapter,
)


class ConcreteAdapter(RoleOrchestrationAdapter):
    @property
    def role_id(self) -> str:
        return "test"

    async def execute(
        self,
        task_id: str,
        input_data: dict,
        context: dict,
    ) -> dict:
        return {"ok": True}

    def get_capabilities(self) -> list[str]:
        return ["cap1"]


class TestRoleOrchestrationAdapter:
    def test_concrete_adapter(self) -> None:
        adapter = ConcreteAdapter()
        assert adapter.role_id == "test"
        assert adapter.get_capabilities() == ["cap1"]


class TestRoleAdapterFactoryPort:
    def test_register_and_get(self) -> None:
        factory = RoleAdapterFactoryPort()
        adapter = ConcreteAdapter()
        with pytest.raises(NotImplementedError):
            factory.register("test", adapter)

    def test_get_raises(self) -> None:
        factory = RoleAdapterFactoryPort()
        with pytest.raises(NotImplementedError):
            factory.get("test")

    def test_list_registered_raises(self) -> None:
        factory = RoleAdapterFactoryPort()
        with pytest.raises(NotImplementedError):
            factory.list_registered()
