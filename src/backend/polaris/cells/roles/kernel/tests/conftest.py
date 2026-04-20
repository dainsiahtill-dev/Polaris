"""Pytest configuration and shared fixtures for Kernel Cell tests.

This module provides:
    - Unified _build_kernel() helper (replaces 4 duplicate implementations)
    - Singleton reset fixtures
    - Shared test utilities

Duplicate implementations consolidated here:
    - test_kernel_stream_tool_loop.py:_build_kernel
    - test_run_stream_parity.py:_build_kernel
    - test_stream_visible_output_contract.py:_build_kernel
    - test_turn_engine_policy_convergence.py:_build_kernel
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel


# Ensure polaris is importable
_BACKEND_ROOT = Path(__file__).resolve().parents[5]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Stub Registry for Tests
# ─────────────────────────────────────────────────────────────────────────────


class _StubRegistry:
    """Minimal registry stub for testing RoleExecutionKernel.

    This replaces the need for a real profile registry in tests.
    """

    def __init__(self, profile: object) -> None:
        self._profile = profile

    def get_profile_or_raise(self, _role: str) -> object:
        return self._profile


# ─────────────────────────────────────────────────────────────────────────────
# Unified _build_kernel() Helper
# ─────────────────────────────────────────────────────────────────────────────


def _build_kernel(
    *,
    role_id: str = "pm",
    model: str = "gpt-4o-mini",
    tool_whitelist: list[str] | None = None,
    tool_policy_overrides: dict[str, Any] | None = None,
    prompt_builder: Any | None = None,
    llm_invoker: Any | None = None,
    include_context_policy: bool = True,
) -> RoleExecutionKernel:
    """Build a fully-configured RoleExecutionKernel for testing.

    This is the canonical helper for creating test kernels. It replaces
    4 duplicate implementations across the test suite.

    Args:
        role_id: Role identifier (default: "pm")
        model: Model name (default: "gpt-4o-mini")
        tool_whitelist: List of allowed tool names
        tool_policy_overrides: Additional tool_policy fields to override
        prompt_builder: Optional DI prompt_builder
        llm_invoker: Optional DI llm_invoker
        include_context_policy: Whether to include context_policy (default: True)

    Returns:
        Configured RoleExecutionKernel with mock dependencies

    Example:
        kernel = _build_kernel(role_id="director", tool_whitelist=["read_file", "write_file"])

        # With custom prompt builder
        kernel = _build_kernel(prompt_builder=my_mock_pb)

        # With custom llm_invoker
        kernel = _build_kernel(llm_invoker=my_mock_invoker)
    """
    from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel

    if tool_whitelist is None:
        tool_whitelist = ["read_file"]

    # Build tool_policy
    tool_policy_defaults = {
        "policy_id": f"{role_id}-policy-v1",
        "whitelist": tool_whitelist,
    }
    if tool_policy_overrides:
        tool_policy_defaults.update(tool_policy_overrides)

    profile_attrs: dict[str, Any] = {
        "role_id": role_id,
        "model": model,
        "provider_id": "openai",
        "version": "1.0.0",
        "tool_policy": SimpleNamespace(**tool_policy_defaults),
    }

    # Add context_policy for RoleContextGateway compatibility
    if include_context_policy:
        profile_attrs["context_policy"] = SimpleNamespace(
            max_context_tokens=100000,
            max_history_turns=20,
            compression_strategy="none",
            include_project_structure=False,
            include_task_history=False,
        )

    profile = SimpleNamespace(**profile_attrs)
    kernel = RoleExecutionKernel(
        workspace=".",
        registry=_StubRegistry(profile),  # type: ignore[arg-type]
        prompt_builder=prompt_builder,
        llm_invoker=llm_invoker,
    )

    # Inject mock prompt builder if not provided (to avoid lazy init issues)
    if prompt_builder is None:
        mock_pb = SimpleNamespace(
            build_system_prompt=lambda _p, _a, **kw: "system-prompt",
            build_fingerprint=lambda _p, _a: SimpleNamespace(full_hash="fp-test", core_hash="fp-test"),
            build_retry_prompt=lambda _p, _a, **kw: "retry-prompt",
        )
        kernel._prompt_builder = mock_pb  # type: ignore[assignment]
        kernel._get_prompt_builder = lambda: mock_pb  # type: ignore

    return kernel


# ─────────────────────────────────────────────────────────────────────────────
# Mock LLM Caller Factory
# ─────────────────────────────────────────────────────────────────────────────


class MockLLMCaller:
    """Mock LLM caller for testing with configurable responses.

    Example:
        caller = MockLLMCaller()
        caller.add_response(content="Hello!", tool_calls=[])
        caller.add_response(content="Done", tool_calls=[
            {"id": "call_1", "name": "read_file", "arguments": '{"path": "a.py"}'}
        ])

        kernel = _build_kernel(llm_invoker=caller)
    """

    def __init__(self) -> None:
        self._responses: list[dict[str, Any]] = []
        self._call_history: list[dict[str, Any]] = []

    def add_response(
        self,
        *,
        content: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add a mock response to the queue."""
        self._responses.append(
            {
                "content": content,
                "tool_calls": tool_calls or [],
                "error": error,
                "metadata": metadata or {},
            }
        )

    async def call(self, **kwargs: Any) -> Any:
        """Return the next mock response."""
        self._call_history.append(kwargs)
        if self._responses:
            resp = self._responses[0]
            return SimpleNamespace(
                content=resp["content"],
                tool_calls=resp["tool_calls"],
                error=resp["error"],
                metadata=resp["metadata"],
            )
        return SimpleNamespace(content="", tool_calls=[], error=None)

    async def call_stream(self, **kwargs: Any) -> Any:
        """Yield mock stream chunks."""
        self._call_history.append({**kwargs, "_stream": True})
        if self._responses:
            resp = self._responses[0]
            for word in resp["content"].split():
                yield {"type": "chunk", "content": word + " "}

    def get_call_count(self) -> int:
        return len(self._call_history)

    def get_last_call(self) -> dict[str, Any] | None:
        return self._call_history[-1] if self._call_history else None

    def pop_response(self) -> dict[str, Any] | None:
        """Pop the first response from the queue."""
        return self._responses.pop(0) if self._responses else None


@pytest.fixture
def mock_llm_caller() -> MockLLMCaller:
    """Create a MockLLMCaller for tests."""
    return MockLLMCaller()


# ─────────────────────────────────────────────────────────────────────────────
# Singleton Reset Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_kernel_singletons() -> Any:
    """Reset module-level singletons before and after each test.

    This fixture ensures that tests don't pollute each other through
    module-level singleton state. Saves and restores previous state to
    prevent cross-test contamination.
    """
    saved_adapter = None
    adapter_existed = False

    # Save previous state before test
    try:
        import polaris.kernelone.events.typed.bus_adapter as bus_adapter_mod

        if hasattr(bus_adapter_mod, "_default_adapter"):
            saved_adapter = bus_adapter_mod._default_adapter
            adapter_existed = True
    except (ImportError, AttributeError):
        pass

    yield

    # Restore or reset after test
    try:
        import polaris.kernelone.events.typed.bus_adapter as bus_adapter_mod

        if adapter_existed:
            bus_adapter_mod.__dict__["_default_adapter"] = saved_adapter
        elif hasattr(bus_adapter_mod, "_default_adapter"):
            bus_adapter_mod.__dict__["_default_adapter"] = None
    except (ImportError, AttributeError):
        pass


@pytest.fixture(autouse=True)
def mock_model_catalog() -> Any:
    """Mock ModelCatalog to return default context window for any model.

    This prevents ``ValueError: Context window not configured for model '...'``
    in tests that don't provide real LLM config files.
    """
    from unittest.mock import MagicMock, patch

    mock_spec = MagicMock()
    mock_spec.max_context_tokens = 128000

    mock_catalog = MagicMock()
    mock_catalog.resolve.return_value = mock_spec

    with patch(
        "polaris.kernelone.llm.engine.model_catalog.ModelCatalog",
        return_value=mock_catalog,
    ):
        yield mock_catalog


@pytest.fixture
def isolated_kernel(
    mock_llm_caller: MockLLMCaller,
) -> RoleExecutionKernel:
    """Create an isolated kernel with a mock LLM caller.

    This is the preferred way to create test kernels as it provides
    a fresh mock LLM caller for each test.
    """
    return _build_kernel(llm_invoker=mock_llm_caller)


# ─────────────────────────────────────────────────────────────────────────────
# Shared Test Helpers
# ─────────────────────────────────────────────────────────────────────────────


def make_turn_request(
    message: str = "hello",
    *,
    mode: str = "chat",
    workspace: str = ".",
    history: list[Any] | None = None,
    context_override: dict[str, Any] | None = None,
) -> Any:
    """Create a RoleTurnRequest for testing.

    Args:
        message: User message
        mode: Execution mode ("chat", "workflow", etc.)
        workspace: Workspace path
        history: Conversation history
        context_override: Context override dict

    Returns:
        RoleTurnRequest instance
    """
    from polaris.cells.roles.profile.public.service import RoleExecutionMode, RoleTurnRequest

    mode_map: dict[str, RoleExecutionMode] = {
        "chat": RoleExecutionMode.CHAT,
        "workflow": RoleExecutionMode.WORKFLOW,
    }

    return RoleTurnRequest(
        mode=mode_map.get(mode, RoleExecutionMode.CHAT),
        workspace=workspace,
        message=message,
        history=history or [],
        context_override=context_override or {},
    )


def canonical_tool_call(
    tool: str,
    args: dict[str, Any] | None = None,
    call_id: str = "",
) -> Any:
    """Create a CanonicalToolCall for testing.

    Args:
        tool: Tool name
        args: Tool arguments
        call_id: Unique call identifier

    Returns:
        CanonicalToolCall instance
    """
    from polaris.cells.roles.kernel.internal.tool_call_protocol import CanonicalToolCall

    return CanonicalToolCall(tool=tool, args=args or {}, raw="")


def patch_prompt_builder(kernel: RoleExecutionKernel) -> SimpleNamespace:
    """Patch a kernel's prompt builder with mock implementations.

    This is useful for tests that need to verify prompt builder behavior.

    Args:
        kernel: RoleExecutionKernel to patch

    Returns:
        Mock prompt builder that was set on the kernel
    """
    mock_pb = SimpleNamespace(
        build_system_prompt=lambda _p, _a, **kw: "test-system-prompt",
        build_fingerprint=lambda _p, _a: SimpleNamespace(full_hash="fp-test-patch", core_hash="fp-test-patch"),
        build_retry_prompt=lambda _p, _a, **kw: "retry-prompt",
    )
    kernel._prompt_builder = mock_pb  # type: ignore[assignment]
    return mock_pb
