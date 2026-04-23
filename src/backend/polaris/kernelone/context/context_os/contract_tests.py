"""Contract test framework for ContextOS cross-boundary interfaces.

Provides base classes and utilities for testing Protocol implementations
and ensuring backward compatibility when introducing new strategies.
"""

from __future__ import annotations

import inspect
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, TypeVar, cast, get_type_hints

import pytest

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ContractTestCase(ABC):
    """Base class for contract tests on Protocol implementations.

    Usage:
        class TestSelectorPolicyContract(ContractTestCase):
            protocol = SelectorPolicy

            def get_implementations(self):
                return [DefaultSelectorPolicy(), GreedySelectorPolicy()]

            def test_select_returns_list(self, implementation):
                result = implementation.select(...)
                assert isinstance(result, list)

    Each test method will be run against all implementations automatically.
    """

    protocol: type[Any]

    @abstractmethod
    def get_implementations(self) -> list[Any]:
        """Return list of protocol implementations to test."""
        ...

    def test_implements_all_methods(self, implementation: Any) -> None:
        """Verify implementation has all required protocol methods."""
        protocol_methods = self._get_protocol_methods()
        impl_methods = dir(implementation)

        missing = [m for m in protocol_methods if m not in impl_methods]
        if missing:
            pytest.fail(f"{type(implementation).__name__} missing protocol methods: {missing}")

    def test_method_signatures_match(self, implementation: Any) -> None:
        """Verify method signatures match protocol definition."""
        get_type_hints(self.protocol)
        impl_hints = get_type_hints(type(implementation))

        for method_name in self._get_protocol_methods():
            if method_name not in impl_hints:
                continue

            # Basic signature compatibility check
            protocol_sig = inspect.signature(getattr(self.protocol, method_name))
            impl_sig = inspect.signature(getattr(implementation, method_name))

            proto_params = list(protocol_sig.parameters.keys())
            impl_params = list(impl_sig.parameters.keys())

            # Implementation can have additional params with defaults
            if len(impl_params) < len(proto_params):
                pytest.fail(
                    f"{type(implementation).__name__}.{method_name} "
                    f"has fewer parameters than protocol: "
                    f"expected {proto_params}, got {impl_params}"
                )

    @abstractmethod
    def test_default_behavior_preserved(self, implementation: Any) -> None:
        """Verify default behavior matches baseline (to be overridden)."""
        # Subclasses should override this with domain-specific checks
        pass

    def _get_protocol_methods(self) -> list[str]:
        """Get list of method names defined in the protocol."""
        return [
            name
            for name, member in inspect.getmembers(self.protocol)
            if inspect.isfunction(member) or hasattr(member, "__isabstractmethod__")
        ]

    @pytest.fixture(params=[])
    def implementation(self, request: pytest.FixtureRequest) -> Any:
        """Pytest fixture that yields each implementation."""
        return request.param

    def generate_tests(self, metafunc: pytest.Metafunc) -> None:
        """Generate test cases for all implementations."""
        if "implementation" in metafunc.fixturenames:
            implementations = self.get_implementations()
            metafunc.parametrize("implementation", implementations)


def contract_test(protocol: type[Any]) -> Callable[[type[T]], type[T]]:
    """Decorator to mark a test class as a contract test for a protocol.

    Usage:
        @contract_test(SelectorPolicy)
        class TestSelectorPolicy:
            def test_select_behavior(self):
                ...
    """

    def decorator(cls: type[T]) -> type[T]:
        cast_cls = cast(Any, cls)
        cast_cls._protocol_under_test = protocol
        cast_cls._is_contract_test = True
        return cls

    return decorator


class BackwardCompatibilityTest(ContractTestCase):
    """Test that new implementations preserve default behavior.

    Usage:
        class TestExplorationPolicyBackwardCompat(BackwardCompatibilityTest):
            legacy_class = OldExplorationPolicy
            new_class = NewExplorationPolicy

            def test_default_output_matches(self):
                legacy = self.legacy_class()
                new = self.new_class()

                legacy_result = legacy.select(self.test_context)
                new_result = new.select(self.test_context)

                assert legacy_result == new_result
    """

    legacy_class: type[Any]
    new_class: type[Any]

    def get_implementations(self) -> list[Any]:
        return [self.legacy_class(), self.new_class()]

    @abstractmethod
    def test_default_behavior_preserved(self, implementation: Any) -> None:
        """Must be implemented to verify backward compatibility."""
        ...


# Pytest hook to auto-discover contract tests
def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Mark contract tests for special handling."""
    for item in items:
        if hasattr(item, "cls") and item.cls and hasattr(item.cls, "_is_contract_test"):
            item.add_marker(pytest.mark.contract_test)
