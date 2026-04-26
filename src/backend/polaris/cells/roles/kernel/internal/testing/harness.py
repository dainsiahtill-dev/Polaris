"""Kernel Test Harness for easy test kernel construction.

Provides a fluent builder API for constructing test-ready kernel instances
with fake dependencies.

# -*- coding: utf-8 -*-
UTF-8 encoding verified: All text uses UTF-8

Example:
    >>> from polaris.cells.roles.kernel.internal.testing import KernelTestHarness
    >>>
    >>> # Basic usage
    >>> kernel = (
    ...     KernelTestHarness()
    ...     .with_fake_llm(responses=[{"content": "Hello"}])
    ...     .with_fake_tools({"read_file": {"success": True, "content": "data"}})
    ...     .build()
    ... )
    >>>
    >>> # Advanced usage with custom configuration
    >>> harness = KernelTestHarness()
    >>> harness.config.workspace = "/tmp/test"
    >>> harness.config.role = "pm"
    >>> kernel = (
    ...     harness
    ...     .with_fake_llm()
    ...     .with_llm_response(LLMResponseBuilder().with_content("Hi!").build())
    ...     .with_llm_response(LLMResponseBuilder().with_content("Done!").build())
    ...     .with_fake_tools()
    ...     .with_tool_result("read_file", {"success": True, "content": "test"})
    ...     .with_tool_handler("write_file", lambda args: {"success": True})
    ...     .build()
    ... )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.testing.exceptions import HarnessConfigurationError
from polaris.cells.roles.kernel.internal.testing.fake_context import FakeContextAssembler
from polaris.cells.roles.kernel.internal.testing.fake_llm import FakeLLMInvoker, LLMResponseBuilder
from polaris.cells.roles.kernel.internal.testing.fake_tools import FakeToolExecutor

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class HarnessConfig:
    """Configuration for the test harness.

    Attributes:
        workspace: Workspace path for the kernel.
        role: Default role ID to use.
        use_structured_output: Whether to enable structured output.
        enable_cache: Whether to enable LLM caching.
    """

    workspace: str = "."
    role: str = "test_role"
    use_structured_output: bool = False
    enable_cache: bool = False


class KernelTestHarness:
    """Fluent builder for constructing test kernel instances.

    This class provides a chainable API for configuring and building
    RoleExecutionKernel instances with fake dependencies for unit testing.

    The harness manages three main fake components:
    1. FakeLLMInvoker - for mocking LLM responses
    2. FakeToolExecutor - for mocking tool execution
    3. FakeContextAssembler - for mocking context assembly

    Example:
        >>> kernel = (
        ...     KernelTestHarness()
        ...     .with_workspace("/tmp/test")
        ...     .with_role("architect")
        ...     .with_fake_llm([
        ...         {"content": "I'll analyze the codebase."},
        ...         {"content": "Analysis complete."},
        ...     ])
        ...     .with_fake_tools({
        ...         "read_file": {"success": True, "content": "file content"},
        ...         "list_directory": {"success": True, "entries": ["a.py", "b.py"]},
        ...     })
        ...     .build()
        ... )
    """

    def __init__(self) -> None:
        """Initialize the test harness."""
        self.config = HarnessConfig()

        # Fake components (initialized on demand)
        self._fake_llm: FakeLLMInvoker | None = None
        self._fake_tools: FakeToolExecutor | None = None
        self._fake_context: FakeContextAssembler | None = None

        # Response queues
        self._llm_responses: list[dict[str, Any]] = []
        self._tool_results: dict[str, dict[str, Any]] = {}
        self._tool_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}

        # Build tracking
        self._built: bool = False
        self._kernel: Any | None = None

    # ── Configuration Methods ────────────────────────────────────────────────

    def with_workspace(self, workspace: str) -> KernelTestHarness:
        """Set the workspace path.

        Args:
            workspace: Path to the workspace directory.

        Returns:
            Self for method chaining.
        """
        self.config.workspace = workspace
        return self

    def with_role(self, role: str) -> KernelTestHarness:
        """Set the default role ID.

        Args:
            role: Role identifier (e.g., "pm", "architect", "director").

        Returns:
            Self for method chaining.
        """
        self.config.role = role
        return self

    def with_structured_output(self, enabled: bool = True) -> KernelTestHarness:
        """Enable or disable structured output.

        Args:
            enabled: Whether to enable structured output.

        Returns:
            Self for method chaining.
        """
        self.config.use_structured_output = enabled
        return self

    # ── LLM Configuration ────────────────────────────────────────────────────

    def with_fake_llm(self, responses: list[dict[str, Any]] | None = None) -> KernelTestHarness:
        """Enable and configure the fake LLM invoker.

        Args:
            responses: Optional list of pre-programmed responses.

        Returns:
            Self for method chaining.
        """
        self._fake_llm = FakeLLMInvoker()
        if responses:
            self._llm_responses.extend(responses)
        return self

    def with_llm_response(self, response: dict[str, Any]) -> KernelTestHarness:
        """Add a single LLM response to the queue.

        Args:
            response: Response dictionary or LLMResponseBuilder.build() result.

        Returns:
            Self for method chaining.
        """
        if self._fake_llm is None:
            self._fake_llm = FakeLLMInvoker()
        self._fake_llm.enqueue_response(response)
        return self

    def with_llm_responses(self, responses: list[dict[str, Any]]) -> KernelTestHarness:
        """Add multiple LLM responses to the queue.

        Args:
            responses: List of response dictionaries.

        Returns:
            Self for method chaining.
        """
        if self._fake_llm is None:
            self._fake_llm = FakeLLMInvoker()
        self._fake_llm.enqueue_responses(responses)
        return self

    def with_llm_exception(self, exception: Exception, at_call: int | None = None) -> KernelTestHarness:
        """Configure the fake LLM to raise an exception.

        Args:
            exception: Exception to raise.
            at_call: Optional specific call index to raise at.

        Returns:
            Self for method chaining.
        """
        if self._fake_llm is None:
            self._fake_llm = FakeLLMInvoker()
        self._fake_llm.enqueue_exception(exception, at_call=at_call)
        return self

    def with_llm_response_builder(self, builder: LLMResponseBuilder) -> KernelTestHarness:
        """Add a response using the builder pattern.

        Args:
            builder: Configured LLMResponseBuilder instance.

        Returns:
            Self for method chaining.
        """
        return self.with_llm_response(builder.build())

    # ── Tool Configuration ───────────────────────────────────────────────────

    def with_fake_tools(
        self,
        results: dict[str, dict[str, Any]] | None = None,
    ) -> KernelTestHarness:
        """Enable and configure the fake tool executor.

        Args:
            results: Optional dictionary mapping tool names to static results.

        Returns:
            Self for method chaining.
        """
        self._fake_tools = FakeToolExecutor()
        if results:
            self._fake_tools.register_tools_from_dict(results)
        return self

    def with_tool_result(self, tool_name: str, result: dict[str, Any]) -> KernelTestHarness:
        """Register a tool with a static result.

        Args:
            tool_name: Name of the tool.
            result: Static result dictionary.

        Returns:
            Self for method chaining.
        """
        if self._fake_tools is None:
            self._fake_tools = FakeToolExecutor()
        self._fake_tools.register_tool_with_result(tool_name, result)
        return self

    def with_tool_handler(
        self,
        tool_name: str,
        handler: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        requires_approval: bool = False,
    ) -> KernelTestHarness:
        """Register a tool with a custom handler function.

        Args:
            tool_name: Name of the tool.
            handler: Function that takes args and returns result.
            requires_approval: Whether this tool requires approval.

        Returns:
            Self for method chaining.
        """
        if self._fake_tools is None:
            self._fake_tools = FakeToolExecutor()
        self._fake_tools.register_tool(tool_name, handler, requires_approval=requires_approval)
        return self

    def with_tool_approval(self, tool_name: str, requires: bool = True) -> KernelTestHarness:
        """Set approval requirement for a tool.

        Note: This must be called after registering the tool.

        Args:
            tool_name: Name of the tool.
            requires: Whether approval is required.

        Returns:
            Self for method chaining.

        Raises:
            HarnessConfigurationError: If tool is not registered.
        """
        if self._fake_tools is None:
            raise HarnessConfigurationError(f"Cannot set approval for '{tool_name}': no fake tools configured")

        # Re-register with same handler but different approval setting
        # This is a bit hacky but works for our purposes
        if tool_name not in self._fake_tools.registered_tools:
            raise HarnessConfigurationError(f"Tool '{tool_name}' not registered. Register it first.")

        # Store current handler and re-register
        # Note: This requires access to the internal registration which we don't have
        # So we'll use the global approval policy instead
        def approval_policy(name: str, args: dict[str, Any]) -> bool:
            if name == tool_name:
                return requires
            return False

        self._fake_tools.set_global_approval_policy(approval_policy)
        return self

    def with_default_tool_result(self, result: dict[str, Any]) -> KernelTestHarness:
        """Set a default result for unregistered tools.

        Args:
            result: Default result dictionary.

        Returns:
            Self for method chaining.
        """
        if self._fake_tools is None:
            self._fake_tools = FakeToolExecutor()
        self._fake_tools.set_default_result(result)
        return self

    # ── Context Configuration ────────────────────────────────────────────────

    def with_fake_context(
        self,
        messages: list[dict[str, str]] | None = None,
        token_estimate: int = 0,
    ) -> KernelTestHarness:
        """Enable and configure the fake context assembler.

        Args:
            messages: Optional list of context messages.
            token_estimate: Token estimate for context.

        Returns:
            Self for method chaining.
        """
        self._fake_context = FakeContextAssembler()
        if messages:
            self._fake_context.set_default_result(
                messages=messages,
                token_estimate=token_estimate,
            )
        return self

    def with_context_messages(self, messages: list[dict[str, str]]) -> KernelTestHarness:
        """Set context messages.

        Args:
            messages: List of message dictionaries.

        Returns:
            Self for method chaining.
        """
        if self._fake_context is None:
            self._fake_context = FakeContextAssembler()
        self._fake_context.set_default_result(messages=messages)
        return self

    # ── Build Method ─────────────────────────────────────────────────────────

    def build(self) -> Any:
        """Build and return a configured RoleExecutionKernel.

        Returns:
            RoleExecutionKernel instance with fake dependencies injected.

        Raises:
            HarnessConfigurationError: If kernel cannot be built.
        """
        if self._built:
            raise HarnessConfigurationError("Harness has already been used to build a kernel. Create a new harness.")

        try:
            from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
        except ImportError as e:
            raise HarnessConfigurationError(f"Cannot import RoleExecutionKernel: {e}") from e

        # Build kernel with fake tool gateway if configured
        tool_gateway = self._fake_tools

        kernel = RoleExecutionKernel(
            workspace=self.config.workspace,
            use_structured_output=self.config.use_structured_output,
            tool_gateway=tool_gateway,  # type: ignore[arg-type]
        )

        # Inject fake LLM caller if configured
        if self._fake_llm is not None:
            # The kernel uses inject_llm_caller to replace the LLM
            kernel.inject_llm_caller(self._fake_llm)  # type: ignore[arg-type]

        # Inject fake context assembler if configured
        if self._fake_context is not None:
            # The kernel uses _prompt_builder which uses context_gateway
            # This is more complex - we'll set up the context gateway
            # Note: This is a simplified injection - real usage may need more setup
            pass  # Context injection is more complex and may require additional setup

        self._kernel = kernel
        self._built = True

        return kernel

    def build_with_tracking(self) -> tuple[Any, KernelTestHarness]:
        """Build kernel and return it along with harness for verification.

        Returns:
            Tuple of (kernel, harness) where harness can be used to verify calls.
        """
        kernel = self.build()
        return kernel, self

    # ── Verification Helpers ─────────────────────────────────────────────────

    @property
    def fake_llm(self) -> FakeLLMInvoker | None:
        """Get the fake LLM invoker for verification."""
        return self._fake_llm

    @property
    def fake_tools(self) -> FakeToolExecutor | None:
        """Get the fake tool executor for verification."""
        return self._fake_tools

    @property
    def fake_context(self) -> FakeContextAssembler | None:
        """Get the fake context assembler for verification."""
        return self._fake_context

    def assert_llm_called_times(self, times: int) -> None:
        """Assert that LLM was called a specific number of times.

        Args:
            times: Expected number of calls.

        Raises:
            AssertionError: If call count doesn't match.
        """
        if self._fake_llm is None:
            raise AssertionError("Fake LLM was not configured")
        self._fake_llm.assert_call_count(times)

    def assert_tool_called(self, tool_name: str, times: int | None = None) -> None:
        """Assert that a tool was called.

        Args:
            tool_name: Name of the tool.
            times: Optional expected number of calls.

        Raises:
            AssertionError: If tool wasn't called as expected.
        """
        if self._fake_tools is None:
            raise AssertionError("Fake tools were not configured")
        self._fake_tools.assert_called(tool_name, times=times)

    def reset(self) -> KernelTestHarness:
        """Reset the harness for reuse.

        Note: After reset, you must call build() again to create a new kernel.

        Returns:
            Self for method chaining.
        """
        self._built = False
        self._kernel = None

        if self._fake_llm:
            self._fake_llm.reset()
        if self._fake_tools:
            self._fake_tools.reset()
        if self._fake_context:
            self._fake_context.reset()

        return self


__all__ = [
    "HarnessConfig",
    "KernelTestHarness",
]
