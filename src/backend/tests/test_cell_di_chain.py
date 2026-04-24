"""Behavior tests for Cell DI container wiring.

Tests cover DIContainer singleton/factory resolution, public contract imports,
and the bootstrap assembly wiring invariants.
"""

from __future__ import annotations

import pytest
from polaris.infrastructure.di.container import DIContainer, get_container


class _DummyService:
    def __init__(self, value: str = "default") -> None:
        self.value = value


class TestDIContainer:
    """DIContainer registers and resolves services correctly."""

    def test_register_instance_provides_singleton(self) -> None:
        container = DIContainer()
        container.register_instance(_DummyService, _DummyService("singleton_value"))

        resolved = container.resolve(_DummyService)
        assert resolved.value == "singleton_value"

    def test_resolve_returns_same_instance_for_singleton(self) -> None:
        container = DIContainer()
        container.register_instance(_DummyService, _DummyService("shared"))

        a = container.resolve(_DummyService)
        b = container.resolve(_DummyService)
        assert a is b  # Same instance

    @pytest.mark.asyncio
    async def test_resolve_async_non_singleton_returns_new_instance(self) -> None:
        """Non-singleton factories create a new instance on each resolve_async call."""
        container = DIContainer()
        call_count = 0

        def counting_factory(c: DIContainer) -> _DummyService:
            nonlocal call_count
            call_count += 1
            return _DummyService(f"call_{call_count}")

        container.register_factory(_DummyService, counting_factory, is_singleton=False)

        a = await container.resolve_async(_DummyService)
        b = await container.resolve_async(_DummyService)
        assert a is not b  # Different instances
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_resolve_async_singleton_factory_called_once(self) -> None:
        """Singleton factory is called only once; subsequent resolves return the same instance."""
        container = DIContainer()
        call_count = 0

        def counting_factory(c: DIContainer) -> _DummyService:
            nonlocal call_count
            call_count += 1
            return _DummyService(f"call_{call_count}")

        container.register_factory(_DummyService, counting_factory, is_singleton=True)

        a = await container.resolve_async(_DummyService)
        b = await container.resolve_async(_DummyService)
        assert a is b  # Same instance
        assert call_count == 1, "Singleton factory must only be called once"

    def test_resolve_raises_for_unregistered_type(self) -> None:
        container = DIContainer()
        with pytest.raises(KeyError):
            container.resolve(_DummyService)


class TestDIContainerPublicContracts:
    """Public contract imports are accessible without triggering internal violations."""

    def test_audit_evidence_public_imports(self) -> None:
        # Verify public service exports load without importing internal modules directly
        from polaris.cells.audit.evidence.public.service import (
            EvidenceService,
            build_error_evidence,
            build_file_evidence,
            create_evidence_bundle_service,
        )

        assert EvidenceService is not None
        assert callable(build_error_evidence)
        assert callable(build_file_evidence)
        assert callable(create_evidence_bundle_service)

    def test_pm_planning_public_pipeline_imports(self) -> None:
        from polaris.cells.orchestration.pm_planning.public.pipeline import (
            PmInvokeBackendPort,
            PmStatePort,
            run_pm_planning_iteration,
        )

        # These are the public contract types
        assert PmInvokeBackendPort is not None
        assert PmStatePort is not None
        assert run_pm_planning_iteration is not None

    def test_pm_dispatch_public_imports(self) -> None:
        from polaris.cells.orchestration.pm_dispatch.public import (
            DispatchPmTasksCommandV1,
            ErrorClassifier,
            resolve_director_dispatch_tasks,
        )

        assert DispatchPmTasksCommandV1 is not None
        assert resolve_director_dispatch_tasks is not None
        assert ErrorClassifier is not None

    def test_director_execution_public_imports(self) -> None:
        from polaris.cells.director.execution.public.service import (
            DirectorService,
            TaskService,
        )

        assert DirectorService is not None
        assert TaskService is not None


class TestBootstrapAssembly:
    """Bootstrap assembly provides the global container."""

    @pytest.mark.asyncio
    async def test_get_container_returns_global_instance(self) -> None:
        container_a = await get_container()
        container_b = await get_container()
        assert container_a is container_b  # Same global instance

    def test_bootstrap_assembly_imports_without_error(self) -> None:
        # Verify all public exports are present in the assembly module
        from polaris.bootstrap import assembly

        assert hasattr(assembly, "assemble_core_services")
        assert hasattr(assembly, "get_container")
        assert hasattr(assembly, "PMService")
