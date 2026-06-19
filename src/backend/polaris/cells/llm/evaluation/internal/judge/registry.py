"""Reusable validator plugin framework and the global validator registry.

This module defines the validator plugin architecture used by the deterministic
judge: the :class:`ValidatorCategory` enum, :class:`ValidatorMetadata`,
:class:`CompositeValidator`, and the :class:`ValidatorRegistry` class along with
the single process-wide registry instance.

It is a leaf foundation module: it depends only on the standard library and the
benchmark model types, so it can be imported by every other judge module
without circular-import risk.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..benchmark_models import ObservedBenchmarkRun

# Type aliases for validator system
ValidatorFunc = Callable[[str, ObservedBenchmarkRun, list[str]], tuple[bool, str]]
ValidatorResult = tuple[bool, str]


class ValidatorCategory(Enum):
    """Categories for validators, used for scoring and organization."""

    SAFETY = "safety"
    CONTRACT = "contract"
    EVIDENCE = "evidence"
    TOOLING = "tooling"


@dataclass(frozen=True)
class ValidatorMetadata:
    """Metadata for a validator, providing descriptive information.

    Attributes:
        category: The validation category (safety, contract, evidence, tooling).
        critical: Whether failure of this validator blocks overall pass.
        description: Human-readable description of what the validator checks.
        tags: Optional tuple of tags for grouping/organizing validators.
    """

    category: ValidatorCategory = ValidatorCategory.CONTRACT
    critical: bool = False
    description: str = ""
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Convert metadata to dictionary representation."""
        return {
            "category": self.category.value,
            "critical": self.critical,
            "description": self.description,
            "tags": list(self.tags),
        }


@dataclass
class CompositeValidator:
    """A validator that combines multiple validators.

    This allows composing complex validation logic from simpler building blocks.

    Attributes:
        name: Unique identifier for the composite validator.
        metadata: Metadata for the composite validator.
        validators: List of validator names to execute in sequence.
        require_all: If True, all validators must pass; if False, at least one must pass.
        _func: The underlying validation function (lazily computed).
    """

    name: str
    metadata: ValidatorMetadata
    validators: tuple[str, ...]
    require_all: bool = True
    _func: Callable[[str, ObservedBenchmarkRun, list[str]], tuple[bool, str]] | None = field(default=None, repr=False)

    def get_func(
        self, registry: ValidatorRegistry
    ) -> Callable[[str, ObservedBenchmarkRun, list[str]], tuple[bool, str]]:
        """Get or create the composite validation function.

        Args:
            registry: The validator registry to look up validators from.

        Returns:
            A validation function that runs all composed validators.
        """
        if self._func is None:
            self._func = self._create_composite_func(registry)
        return self._func

    def _create_composite_func(
        self, registry: ValidatorRegistry
    ) -> Callable[[str, ObservedBenchmarkRun, list[str]], tuple[bool, str]]:
        """Create the composite validation function.

        Args:
            registry: The validator registry.

        Returns:
            A function that runs all composed validators.
        """

        def composite_func(
            output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]
        ) -> tuple[bool, str]:
            results: list[tuple[str, bool, str]] = []
            for validator_name in self.validators:
                result = registry.validate(validator_name, output_text, observed, known_paths)
                results.append((validator_name, result[0], result[1]))

            if self.require_all:
                failed = [(name, msg) for name, ok, msg in results if not ok]
                if failed:
                    names = ", ".join(name for name, _ in failed)
                    return False, f"Composite '{self.name}' failed: {names}"
                return True, f"Composite '{self.name}' passed: all {len(results)} validators succeeded"
            else:
                passed = [(name, msg) for name, ok, msg in results if ok]
                if passed:
                    names = ", ".join(name for name, _ in passed)
                    return True, f"Composite '{self.name}' passed: {names} succeeded"
                failed = [(name, msg) for name, ok, msg in results if not ok]
                names = ", ".join(name for name, _ in failed)
                return False, f"Composite '{self.name}' failed: all validators failed ({names})"

        return composite_func


class ValidatorRegistry:
    """Registry for validators with plugin architecture.

    This class provides:
    - Automatic registration via @validator decorator
    - Metadata-driven validation configuration
    - Composite validator support
    - Query methods for listing and retrieving validators

    Example:
        @validator_registry.register("my_validator", category=ValidatorCategory.SAFETY, critical=True)
        def my_validator(output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]) -> tuple[bool, str]:
            return (True, "Validation passed")
    """

    _validators: dict[str, tuple[ValidatorMetadata, ValidatorFunc]]
    _composites: dict[str, CompositeValidator]

    def __init__(self) -> None:
        self._validators = {}
        self._composites = {}

    def register(
        self,
        name: str | None = None,
        *,
        category: ValidatorCategory | str = ValidatorCategory.CONTRACT,
        critical: bool = False,
        description: str = "",
        tags: tuple[str, ...] = (),
    ) -> Callable[[ValidatorFunc], ValidatorFunc]:
        """Decorator to register a validator function.

        Can be used as:
        - @validator_registry.register() - uses function name
        - @validator_registry.register("custom_name") - uses provided name
        - @validator_registry.register(category=ValidatorCategory.SAFETY) - with metadata

        Args:
            name: Optional name for the validator. Defaults to function.__name__.
            category: Validation category (safety, contract, evidence, tooling).
            critical: Whether failure blocks overall pass.
            description: Human-readable description.
            tags: Optional tags for grouping.

        Returns:
            Decorator function that registers the validator.

        Example:
            @validator_registry.register(category=ValidatorCategory.SAFETY, critical=True)
            def no_errors(output_text: str, observed: ObservedBenchmarkRun, known_paths: list[str]) -> tuple[bool, str]:
                return ("error" not in output_text.lower(), "no errors in output")
        """

        def decorator(func: ValidatorFunc) -> ValidatorFunc:
            validator_name = name or func.__name__

            # Convert string category to enum if needed
            if isinstance(category, str):
                try:
                    category_enum = ValidatorCategory(category)
                except ValueError:
                    category_enum = ValidatorCategory.CONTRACT
            else:
                category_enum = category

            metadata = ValidatorMetadata(
                category=category_enum,
                critical=critical,
                description=description or func.__doc__ or "",
                tags=tags,
            )
            self._validators[validator_name] = (metadata, func)
            return func

        return decorator

    def register_composite(
        self,
        name: str,
        validators: tuple[str, ...],
        *,
        category: ValidatorCategory | str = ValidatorCategory.CONTRACT,
        critical: bool = False,
        description: str = "",
        tags: tuple[str, ...] = (),
        require_all: bool = True,
    ) -> CompositeValidator:
        """Register a composite validator that combines multiple validators.

        Args:
            name: Unique identifier for the composite validator.
            validators: Tuple of validator names to combine.
            category: Validation category.
            critical: Whether failure blocks overall pass.
            description: Human-readable description.
            tags: Optional tags for grouping.
            require_all: If True, all must pass; if False, at least one must pass.

        Returns:
            The created CompositeValidator instance.

        Example:
            registry.register_composite(
                "safe_and_valid",
                validators=("no_prompt_leakage", "pm_plan_json"),
                category=ValidatorCategory.SAFETY,
                description="Validates both safety and contract requirements"
            )
        """
        if isinstance(category, str):
            try:
                category_enum = ValidatorCategory(category)
            except ValueError:
                category_enum = ValidatorCategory.CONTRACT
        else:
            category_enum = category

        metadata = ValidatorMetadata(
            category=category_enum,
            critical=critical,
            description=description,
            tags=tags,
        )
        composite = CompositeValidator(
            name=name,
            metadata=metadata,
            validators=validators,
            require_all=require_all,
        )
        self._composites[name] = composite
        return composite

    def get(self, name: str) -> tuple[ValidatorMetadata, ValidatorFunc] | None:
        """Get a validator by name.

        Args:
            name: The validator name to look up.

        Returns:
            Tuple of (metadata, function) if found, None otherwise.
        """
        return self._validators.get(name)

    def get_composite(self, name: str) -> CompositeValidator | None:
        """Get a composite validator by name.

        Args:
            name: The composite validator name.

        Returns:
            The CompositeValidator if found, None otherwise.
        """
        return self._composites.get(name)

    def get_metadata(self, name: str) -> ValidatorMetadata | None:
        """Get only the metadata for a validator.

        Args:
            name: The validator name.

        Returns:
            ValidatorMetadata if found, None otherwise.
        """
        result = self._validators.get(name)
        if result is not None:
            return result[0]
        composite = self._composites.get(name)
        if composite is not None:
            return composite.metadata
        return None

    def list_validators(
        self, *, category: ValidatorCategory | None = None, tags: tuple[str, ...] | None = None
    ) -> list[str]:
        """List all registered validator names, optionally filtered.

        Args:
            category: Optional category filter.
            tags: Optional tags filter (validator must have all specified tags).

        Returns:
            List of validator names matching the filters.
        """
        results: list[str] = []

        # Filter simple validators
        for name, (metadata, _) in self._validators.items():
            if category is not None and metadata.category != category:
                continue
            if tags is not None and not all(tag in metadata.tags for tag in tags):
                continue
            results.append(name)

        # Include composites matching filters
        for name, composite in self._composites.items():
            if name in results:  # Don't double-count
                continue
            if category is not None and composite.metadata.category != category:
                continue
            if tags is not None and not all(tag in composite.metadata.tags for tag in tags):
                continue
            results.append(name)

        return sorted(results)

    def validate(
        self, name: str, output: str, observed: ObservedBenchmarkRun, known_paths: list[str]
    ) -> tuple[bool, str]:
        """Execute a validator by name.

        Args:
            name: The validator name to execute.
            output: The output text to validate.
            observed: The observed benchmark run.
            known_paths: List of known valid paths.

        Returns:
            Tuple of (passed, message).
        """
        # Check simple validators
        result = self._validators.get(name)
        if result is not None:
            _, func = result
            return func(output, observed, known_paths)

        # Check composite validators
        composite = self._composites.get(name)
        if composite is not None:
            func = composite.get_func(self)
            return func(output, observed, known_paths)

        return False, f"Unknown validator: {name}"

    def unregister(self, name: str) -> bool:
        """Unregister a validator.

        Args:
            name: The validator name to remove.

        Returns:
            True if removed, False if not found.
        """
        if name in self._validators:
            del self._validators[name]
            return True
        if name in self._composites:
            del self._composites[name]
            return True
        return False

    def clear(self) -> None:
        """Clear all registered validators. Mainly for testing."""
        self._validators.clear()
        self._composites.clear()

    @property
    def validator_count(self) -> int:
        """Number of registered validators (simple + composite)."""
        return len(self._validators) + len(self._composites)


# Create the global registry instance
_validator_registry_instance: ValidatorRegistry = ValidatorRegistry()
#: Global validator registry instance for automatic registration
validator_registry: ValidatorRegistry = _validator_registry_instance

#: Backward compatible alias - prefer validator_registry
VALIDATOR_REGISTRY = validator_registry
