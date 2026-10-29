"""TaskRuntime-owned prospective inventory operation identity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast

from polaris.kernelone.effect.immutable_payload import (
    DirectedEffectImmutableItemsV1,
    DirectedEffectImmutableMapV1,
    hash_directed_effect_arguments,
    require_directed_effect_immutable_items,
)

from .contracts import (
    DirectedEffectInventoryEffectTypeV1,
    DirectedEffectInventoryExecutionModeV1,
    DirectedEffectInventoryIntentV1,
)

_LOWER_HEX: Final[frozenset[str]] = frozenset("0123456789abcdef")


def _require_token(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _require_hash(name: str, value: str) -> str:
    normalized = _require_token(name, value)
    if len(normalized) != 64 or not set(normalized).issubset(_LOWER_HEX):
        raise ValueError(f"{name} must be a lowercase 64-character SHA-256 digest")
    return normalized


@dataclass(frozen=True, slots=True)
class DirectedEffectOperationSubjectV1:
    """Prospective pre-seal operation identity and normalized descriptor."""

    workspace: str
    turn_id: str
    batch_id: str
    tool_call_id: str
    inventory_ordinal: int
    normalized_tool_name: str
    normalized_arguments: DirectedEffectImmutableItemsV1
    effect_type: DirectedEffectInventoryEffectTypeV1
    execution_mode: DirectedEffectInventoryExecutionModeV1
    prospective_operation_hash: str

    def __post_init__(self) -> None:
        for field_name in (
            "workspace",
            "turn_id",
            "batch_id",
            "tool_call_id",
            "normalized_tool_name",
            "effect_type",
            "execution_mode",
        ):
            object.__setattr__(self, field_name, _require_token(field_name, getattr(self, field_name)))
        if (
            isinstance(self.inventory_ordinal, bool)
            or not isinstance(self.inventory_ordinal, int)
            or self.inventory_ordinal < 0
        ):
            raise ValueError("inventory_ordinal must be a non-negative integer")
        object.__setattr__(
            self,
            "normalized_arguments",
            require_directed_effect_immutable_items("normalized_arguments", self.normalized_arguments),
        )
        object.__setattr__(
            self,
            "prospective_operation_hash",
            _require_hash("prospective_operation_hash", self.prospective_operation_hash),
        )
        DirectedEffectInventoryIntentV1(
            ordinal=self.inventory_ordinal,
            tool_call_id=self.tool_call_id,
            normalized_tool_name=self.normalized_tool_name,
            effect_type=self.effect_type,
            execution_mode=self.execution_mode,
            intended_effect_fingerprint=self.prospective_operation_hash,
            policy_verdict_hash=self.prospective_operation_hash,
            expected_receipt_binding_hash=self.prospective_operation_hash,
        )


def hash_directed_effect_operation_subject(
    subject: DirectedEffectOperationSubjectV1,
) -> str:
    """Hash one prospective operation without trusting its supplied digest."""

    if type(subject) is not DirectedEffectOperationSubjectV1:
        raise TypeError("subject must be exactly DirectorEffectPolicyOperationSubjectV1")
    return cast(
        "str",
        (
            hash_directed_effect_arguments(
                (
                    ("batch_id", subject.batch_id),
                    ("effect_type", subject.effect_type),
                    ("execution_mode", subject.execution_mode),
                    ("inventory_ordinal", subject.inventory_ordinal),
                    (
                        "normalized_arguments",
                        DirectedEffectImmutableMapV1(items=subject.normalized_arguments),
                    ),
                    ("normalized_tool_name", subject.normalized_tool_name),
                    ("tool_call_id", subject.tool_call_id),
                    ("turn_id", subject.turn_id),
                    ("workspace", subject.workspace),
                )
            )
        ),
    )
