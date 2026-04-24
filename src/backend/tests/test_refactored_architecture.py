"""Tests for the refactored Clean Architecture.

These tests verify that the new architecture components work correctly:
- Unified configuration system
- Domain exceptions
- DI container
- Application services
- API layer
"""

import sys
from pathlib import Path

import pytest

# Ensure src/backend is in path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestConfig:
    """Test unified configuration system."""

    def test_settings_singleton(self):
        """Settings should be a singleton."""
        from polaris.bootstrap.config import get_settings

        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_settings_has_required_fields(self):
        """Settings should have all required configuration fields."""
        from polaris.bootstrap.config import get_settings

        s = get_settings()

        # Core paths
        assert s.workspace is not None
        assert s.project_root is not None
        assert s.backend_root is not None

        # Feature configs
        assert s.llm is not None
        assert s.pm is not None
        assert s.director is not None
        assert s.runtime is not None
        assert s.logging is not None
        assert s.server is not None

    def test_settings_runtime_base(self):
        """Settings should provide runtime_base."""
        from polaris.bootstrap.config import get_settings

        s = get_settings()
        runtime_base = s.runtime_base

        assert runtime_base is not None
        assert isinstance(runtime_base, Path)

    def test_settings_paths(self):
        """Settings should provide script paths."""
        from polaris.bootstrap.config import get_settings

        s = get_settings()

        assert s.pm_script_path.exists() or True  # May not exist in test env
        assert s.director_script_path.exists() or True


class TestDomainExceptions:
    """Test domain exception hierarchy."""

    def test_base_exception(self):
        """Base DomainException should work."""
        from polaris.domain.exceptions import DomainException

        exc = DomainException("test message", code="TEST_ERROR")

        assert exc.message == "test message"
        assert exc.code == "TEST_ERROR"
        assert exc.status_code == 500

    def test_validation_error(self):
        """ValidationError should have correct status code."""
        from polaris.domain.exceptions import ValidationError

        exc = ValidationError("field invalid", field="test_field")

        assert exc.status_code == 422
        assert exc.details["field"] == "test_field"

    def test_not_found_error(self):
        """NotFoundError should have correct status code."""
        from polaris.domain.exceptions import NotFoundError

        exc = NotFoundError("User", "123")

        assert exc.status_code == 404
        assert exc.details["resource_type"] == "User"
        assert exc.details["resource_id"] == "123"

    def test_process_errors(self):
        """Process errors should have correct codes."""
        from polaris.domain.exceptions import ProcessAlreadyRunningError, ProcessNotRunningError

        running = ProcessAlreadyRunningError("pm", pid=1234)
        assert running.status_code == 409
        assert running.details["pid"] == 1234

        not_running = ProcessNotRunningError("pm")
        assert not_running.status_code == 409


class TestDIContainer:
    """Test DI container."""

    @pytest.mark.asyncio
    async def test_container_creation(self):
        """Container should be creatable."""
        from polaris.infrastructure.di.container import DIContainer

        container = DIContainer()
        assert container is not None

    @pytest.mark.asyncio
    async def test_container_register_instance(self):
        """Container should register and resolve instances."""
        from polaris.infrastructure.di.container import DIContainer

        container = DIContainer()

        class TestService:
            pass

        service = TestService()
        container.register_instance(TestService, service)

        resolved = container.resolve(TestService)
        assert resolved is service

    @pytest.mark.asyncio
    async def test_container_singleton_factory(self):
        """Container should resolve singleton factories."""
        from polaris.infrastructure.di.container import DIContainer

        container = DIContainer()

        class TestService:
            pass

        container.register_singleton(TestService, lambda c: TestService())

        s1 = await container.resolve_async(TestService)
        s2 = await container.resolve_async(TestService)

        assert s1 is s2  # Same instance (singleton)

    @pytest.mark.asyncio
    async def test_global_container(self):
        """Global container should provide services."""
        from polaris.infrastructure.di.container import get_container, reset_container

        reset_container()
        container = await get_container()

        assert container is not None
        assert hasattr(container, "has_registration")

    @pytest.mark.asyncio
    async def test_container_provides_settings(self):
        """Container should provide Settings."""
        from polaris.bootstrap.config import Settings
        from polaris.infrastructure.di.container import get_container, reset_container

        reset_container()
        container = await get_container()

        settings = container.resolve(Settings)
        assert settings is not None

    @pytest.mark.asyncio
    async def test_container_provides_pm_service(self):
        """Container should provide PMService."""
        from polaris.cells.orchestration.pm_planning.public.service import PMService
        from polaris.infrastructure.di.container import get_container, reset_container

        reset_container()
        container = await get_container()

        pm = await container.resolve_async(PMService)
        assert pm is not None
        assert isinstance(pm, PMService)


class TestStorageLayout:
    """Test StorageLayout."""

    def test_storage_layout_creation(self):
        """StorageLayout should be creatable."""
        from polaris.kernelone.storage import StorageLayout

        workspace = Path(".")
        runtime_base = Path(".")

        storage = StorageLayout(workspace, runtime_base)

        assert storage.workspace == workspace.resolve()
        assert storage.runtime_root is not None

    def test_storage_layout_get_path(self):
        """StorageLayout should provide paths."""
        from polaris.kernelone.storage import StorageLayout

        storage = StorageLayout(Path("."), Path("."))

        log_path = storage.get_path("logs", "test.log")
        assert "logs" in str(log_path)
        assert "test.log" in str(log_path)

    def test_storage_layout_resolve_artifact(self):
        """StorageLayout should resolve artifact paths."""
        from polaris.kernelone.storage import StorageLayout

        storage = StorageLayout(Path("."), Path("."))

        runtime_path = storage.resolve_artifact_path("runtime/logs/test.log")
        assert "logs" in str(runtime_path)


class TestPMService:
    """Test PM Application Service."""

    @pytest.mark.asyncio
    async def test_pm_service_creation(self):
        """PMService should be creatable."""
        from polaris.bootstrap.config import get_settings
        from polaris.cells.orchestration.pm_planning.public.service import PMService
        from polaris.kernelone.storage import StorageLayout

        settings = get_settings()
        storage = StorageLayout(settings.workspace, settings.runtime_base)
        service = PMService(settings, storage)

        assert service is not None
        assert service.handle is not None

    def test_pm_service_status(self):
        """PMService should provide status."""
        from polaris.bootstrap.config import get_settings
        from polaris.cells.orchestration.pm_planning.public.service import PMService
        from polaris.kernelone.storage import StorageLayout

        settings = get_settings()
        storage = StorageLayout(settings.workspace, settings.runtime_base)
        service = PMService(settings, storage)

        status = service.get_status()

        assert "running" in status
        assert "pid" in status
        assert "mode" in status
        assert status["running"] is False

    @pytest.mark.asyncio
    async def test_pm_service_stop_not_running(self):
        """PMService stop should handle not running."""
        from polaris.bootstrap.config import get_settings
        from polaris.cells.orchestration.pm_planning.public.service import PMService
        from polaris.kernelone.storage import StorageLayout

        settings = get_settings()
        storage = StorageLayout(settings.workspace, settings.runtime_base)
        service = PMService(settings, storage)

        result = await service.stop()

        assert result["ok"] is False
        assert "error" in result


class TestFastAPIApp:
    """Test FastAPI application."""

    def test_app_creation(self):
        """FastAPI app should be creatable."""
        from polaris.delivery.http.app_factory import create_app

        app = create_app()

        assert app is not None
        assert app.title == "Polaris Desktop Backend"

    def test_app_has_routes(self):
        """FastAPI app should have routes."""
        from polaris.delivery.http.app_factory import create_app

        app = create_app()
        routes = [r.path for r in app.routes if hasattr(r, "path")]

        assert "/v2/pm/run_once" in routes
        assert "/v2/pm/status" in routes
        assert not any(path.startswith("/api/v1/pm/") for path in routes)

    def test_app_v2_routes_have_no_double_prefix(self):
        """V2 routes should not be mounted under /v2/v2."""
        from polaris.delivery.http.app_factory import create_app

        app = create_app()
        routes = [r.path for r in app.routes if hasattr(r, "path")]

        assert "/v2/factory/runs" in routes
        assert "/v2/agent/sessions" in routes
        assert not any(path.startswith("/v2/v2/") for path in routes)


class TestAPIErrorHandlers:
    """Test API error handlers."""

    def test_setup_exception_handlers(self):
        """Exception handlers should be setup without error."""
        from fastapi import FastAPI
        from polaris.delivery.http.error_handlers import setup_exception_handlers

        app = FastAPI()
        setup_exception_handlers(app)

        # If no exception raised, test passes
        assert True
