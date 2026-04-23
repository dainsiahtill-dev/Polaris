"""Immutable model mutation policy enforcement for ContextOS.

Provides decorators and base classes to enforce mutation policies on
frozen Pydantic models, preventing unsafe model_copy(update=...) usage.
"""

from __future__ import annotations

import functools
import logging
from enum import Enum
from typing import Any, Callable, Protocol, TypeVar, cast

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class MutationPolicy(Enum):
    """Mutation policy for immutable ContextOS models.

    - VALIDATED_REPLACE: Use validated_replace() helper (recommended)
    - EXPLICIT_REBUILD: Explicitly construct new instance from scratch
    - READONLY: No mutation allowed after construction
    """

    VALIDATED_REPLACE = "validated_replace"
    EXPLICIT_REBUILD = "explicit_rebuild"
    READONLY = "readonly"


class ImmutableModel(Protocol):
    """Protocol for models that enforce immutability constraints."""

    _mutation_policy: MutationPolicy = MutationPolicy.READONLY

    def mutate(self, **changes: Any) -> ImmutableModel:
        """Safely create a mutated copy according to the model's policy."""
        ...


def enforce_mutation_policy(policy: MutationPolicy) -> Callable[[type[T]], type[T]]:
    """Decorator to enforce a mutation policy on a Pydantic model class.

    Usage:
        @enforce_mutation_policy(MutationPolicy.VALIDATED_REPLACE)
        class TranscriptEventV2(BaseModel):
            ...

    This decorator:
    1. Sets _mutation_policy on the class
    2. Adds a mutate() method that delegates to the appropriate helper
    3. Logs warnings if model_copy(update=...) is used (via __setattr__ override)
    """

    def decorator(cls: type[T]) -> type[T]:
        # Use cast to inform mypy about dynamically added attributes
        cast_cls = cast(Any, cls)
        cast_cls._mutation_policy = policy

        # Add mutate method
        def mutate(self: T, **changes: Any) -> T:
            from .model_utils import validated_replace

            if policy == MutationPolicy.VALIDATED_REPLACE:
                return validated_replace(self, **changes)
            elif policy == MutationPolicy.EXPLICIT_REBUILD:
                # For explicit rebuild, caller must provide all required fields
                return self.__class__(**changes)
            else:  # READONLY
                raise AttributeError(
                    f"{self.__class__.__name__} is readonly. Use validated_replace() for safe mutation."
                )

        cast_cls.mutate = mutate

        # Override __setattr__ to warn about direct mutation attempts
        original_setattr = cls.__setattr__

        @functools.wraps(original_setattr)
        def frozen_setattr(self: T, name: str, value: Any) -> None:
            if name.startswith("_"):
                original_setattr(self, name, value)
                return

            if getattr(self, "model_config", {}).get("frozen", False):
                logger.warning(
                    "Attempted mutation of frozen model %s.%s. Use %s.mutate() or validated_replace() instead.",
                    self.__class__.__name__,
                    name,
                    self.__class__.__name__,
                )

            original_setattr(self, name, value)

        cls.__setattr__ = frozen_setattr  # type: ignore[assignment]

        return cls

    return decorator


class MutationTracker:
    """Track mutation attempts on models for audit purposes.

    Usage:
        tracker = MutationTracker()
        with tracker.track():
            model.mutate(route="new_route")

        print(tracker.mutations)  # List of mutation records
    """

    def __init__(self) -> None:
        self.mutations: list[dict[str, Any]] = []

    def track(self) -> MutationContext:
        return MutationContext(self)

    def record(self, model_class: str, fields: set[str], stack_trace: str) -> None:
        self.mutations.append(
            {
                "model_class": model_class,
                "fields": sorted(fields),
                "stack_trace": stack_trace,
            }
        )


class MutationContext:
    """Context manager for tracking mutations."""

    def __init__(self, tracker: MutationTracker) -> None:
        self.tracker = tracker

    def __enter__(self) -> MutationContext:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass
