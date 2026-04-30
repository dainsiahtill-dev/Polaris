import asyncio
import os
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Add backend directory to sys.path so we can import app modules
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# =============================================================================
# Pytest Configuration
# =============================================================================


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "asyncio: mark test as async")
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "slow: mark test as slow")
    config.addinivalue_line(
        "filterwarnings",
        "ignore:.*iscoroutinefunction.*:DeprecationWarning:nats\\.aio\\.client",
    )


# =============================================================================
# Basic Fixtures
# =============================================================================


@pytest.fixture
def mock_workspace():
    """Create a temporary workspace directory for testing file operations."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def temp_file():
    """Create a temporary file for testing."""
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def async_context():
    """Provide an async context for tests that need it."""
    yield


# =============================================================================
# Mock LLM Infrastructure
# =============================================================================


@dataclass
class MockLLMResponse:
    """Mock LLM response for testing."""

    content: str
    model: str = "mock-model"
    provider: str = "mock"
    token_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class MockLLMProvider:
    """Mock LLM provider for testing."""

    def __init__(self, responses: list | None = None) -> None:
        self.responses = responses or []
        self.call_history: list = []
        self._response_index = 0

    def add_response(self, content: str, **kwargs) -> None:
        """Add a mock response."""
        self.responses.append(MockLLMResponse(content=content, **kwargs))

    async def invoke(self, prompt: str, **kwargs) -> MockLLMResponse:
        """Mock invoke method."""
        self.call_history.append({"prompt": prompt, "kwargs": kwargs})
        if self._response_index < len(self.responses):
            response = self.responses[self._response_index]
            self._response_index += 1
            return response
        return MockLLMResponse(content="Default mock response")

    async def invoke_stream(self, prompt: str, **kwargs):
        """Mock streaming invoke."""
        self.call_history.append({"prompt": prompt, "kwargs": kwargs, "stream": True})
        content = "Mock stream response"
        words = content.split()
        for word in words:
            yield MockLLMResponse(content=word + " ")

    def get_call_count(self) -> int:
        """Get number of calls made."""
        return len(self.call_history)

    def get_last_call(self) -> dict[str, Any] | None:
        """Get the last call details."""
        return self.call_history[-1] if self.call_history else None


@pytest.fixture
def mock_llm_provider():
    """Create a mock LLM provider."""
    return MockLLMProvider()


@pytest.fixture
def mock_role_profile():
    """Create a mock role profile for testing."""
    profile = MagicMock()
    profile.role_id = "test_role"
    profile.model = "gpt-4"
    profile.provider = "openai"
    profile.temperature = 0.7
    profile.max_tokens = 4000
    return profile


@pytest.fixture
def mock_context_request():
    """Create a mock context request for testing."""
    context = MagicMock()
    context.messages = []
    context.token_estimate = 0
    context.context_files = []
    return context


# =============================================================================
# App State Fixtures
# =============================================================================


@pytest.fixture
def mock_app_state():
    """Create a mock AppState for testing."""
    from polaris.bootstrap.config import Settings
    from polaris.cells.runtime.state_owner.internal.state import AppState

    settings = Settings(workspace="/tmp/test_workspace")
    state = AppState(settings=settings)
    return state


@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    from polaris.bootstrap.config import Settings

    return Settings(workspace="/tmp/test_workspace")


# =============================================================================
# Service Fixtures
# =============================================================================


@pytest.fixture
def mock_task_board(mock_workspace):
    """Create a mock TaskBoard for testing."""
    from polaris.cells.runtime.task_runtime.public.task_board_contract import TaskBoard

    return TaskBoard(workspace=mock_workspace)


@pytest.fixture
def mock_pm_service():
    """Create a mock PM service."""
    service = MagicMock()
    service.get_status.return_value = {"running": False, "mode": ""}
    return service


@pytest.fixture
def mock_director_service():
    """Create a mock Director service."""
    service = MagicMock()
    service.get_status.return_value = {"state": "idle"}
    return service


# =============================================================================
# HTTP/Request Fixtures
# =============================================================================


@pytest.fixture
def mock_http_client():
    """Create a mock HTTP client."""
    client = MagicMock()
    client.post = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {}))
    client.get = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {}))
    return client


@pytest.fixture
def mock_fastapi_request():
    """Create a mock FastAPI request."""
    request = MagicMock()
    request.headers = {"authorization": "Bearer test_token"}
    request.client = MagicMock(host="127.0.0.1")
    request.url = MagicMock(path="/test")
    request.method = "GET"
    return request


# =============================================================================
# Utility Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def test_data_dir():
    """Return the path to test data directory."""
    path = os.path.join(os.path.dirname(__file__), "test_data")
    os.makedirs(path, exist_ok=True)
    return path


@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    """Reset circuit breakers before each test."""
    from polaris.infrastructure.llm.providers.provider_helpers import _CIRCUIT_BREAKER_REGISTRY

    _CIRCUIT_BREAKER_REGISTRY.clear()
    yield
    _CIRCUIT_BREAKER_REGISTRY.clear()


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Set default environment variables for testing."""
    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    yield


@pytest.fixture(autouse=True)
def configure_default_kernel_fs_adapter():
    """Inject the default KernelOne filesystem adapter for test execution."""
    from polaris.infrastructure.storage import LocalFileSystemAdapter
    from polaris.kernelone.fs import set_default_adapter

    set_default_adapter(LocalFileSystemAdapter())
    yield


# =============================================================================
# Singleton Reset Fixtures (DI Pattern)
# =============================================================================


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset all singleton instances before and after each test.

    This fixture provides test isolation by clearing all global singleton
    instances. It uses the DI factory reset functions for consistent behavior.

    Order of reset matters: reset dependent singletons before their dependencies.
    """
    # Reset before test
    from polaris.infrastructure.di import (
        reset_kernel_audit_runtime_for_test,
        reset_metrics_collector_for_test,
        reset_omniscient_audit_bus_for_test,
        reset_provider_manager_for_test,
        reset_role_action_registry_for_test,
        reset_role_profile_registry_for_test,
        reset_theme_manager_for_test,
        reset_tool_spec_registry_for_test,
    )

    reset_tool_spec_registry_for_test()
    reset_theme_manager_for_test()
    reset_metrics_collector_for_test()
    reset_provider_manager_for_test()
    reset_role_profile_registry_for_test()
    reset_role_action_registry_for_test()
    reset_kernel_audit_runtime_for_test()
    reset_omniscient_audit_bus_for_test()

    yield

    # Reset after test for clean slate
    reset_tool_spec_registry_for_test()
    reset_theme_manager_for_test()
    reset_metrics_collector_for_test()
    reset_provider_manager_for_test()
    reset_role_profile_registry_for_test()
    reset_role_action_registry_for_test()
    reset_kernel_audit_runtime_for_test()
    reset_omniscient_audit_bus_for_test()


@pytest.fixture
def tool_spec_registry():
    """Fresh ToolSpecRegistry instance for each test.

    Use this fixture when you need a ToolSpecRegistry with clean state.
    """
    from polaris.kernelone.tools.tool_spec_registry import ToolSpecRegistry

    # Ensure clean state
    ToolSpecRegistry.clear()
    return ToolSpecRegistry


@pytest.fixture
def theme_manager():
    """Fresh ThemeManager instance for each test.

    Use this fixture when you need a ThemeManager with independent state.
    """
    from polaris.delivery.cli.textual.styles import ThemeManager

    # Clear singleton
    ThemeManager._instance = None
    return ThemeManager()


@pytest.fixture
def metrics_collector():
    """Fresh MetricsCollector instance for each test.

    Use this fixture when you need a MetricsCollector with clean metrics.
    """
    from polaris.cells.roles.kernel.internal.metrics import MetricsCollector

    # Reset global metrics and singleton
    MetricsCollector.reset()
    with MetricsCollector._lock:
        MetricsCollector._instance = None

    return MetricsCollector()


@pytest.fixture
def kernel_audit_runtime(mock_workspace):
    """Fresh KernelAuditRuntime instance for each test.

    Use this fixture when you need a KernelAuditRuntime with isolated state.

    Args:
        mock_workspace: Temporary workspace path from mock_workspace fixture.
    """
    from pathlib import Path

    from polaris.kernelone.audit.runtime import KernelAuditRuntime

    runtime_root = Path(mock_workspace)
    return KernelAuditRuntime(runtime_root, _create_mock_audit_store(runtime_root))


def _create_mock_audit_store(runtime_root: Path):
    """Create a mock audit store for testing."""
    from polaris.kernelone.audit.contracts import KernelAuditStorePort

    class MockAuditStore(KernelAuditStorePort):
        def __init__(self, root: Path) -> None:
            self._root = root
            self._events: list = []

        def append(self, event):
            self._events.append(event)
            return event

        def query(self, **kwargs):
            return self._events[: kwargs.get("limit", 100)]

        def export_json(self, **kwargs):
            return {"events": [e.to_dict() for e in self._events]}

        def export_csv(self, **kwargs):
            return ""

        def verify_chain(self):
            from polaris.kernelone.audit.contracts import KernelChainVerificationResult

            return KernelChainVerificationResult(valid=True, total_events=len(self._events))

        def get_stats(self, **kwargs):
            return {"total_events": len(self._events)}

        def cleanup_old_logs(self, **kwargs):
            return {"deleted": 0}

    return MockAuditStore(runtime_root)


@pytest.fixture
def omniscient_audit_bus():
    """Fresh OmniscientAuditBus instance for each test.

    Use this fixture when you need an OmniscientAuditBus with isolated state.
    """
    from polaris.kernelone.audit.omniscient.bus import OmniscientAuditBus

    # Use unique name to avoid collision with other tests
    bus_name = f"test_{uuid.uuid4().hex[:8]}"

    return OmniscientAuditBus(name=bus_name)
