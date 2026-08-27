"""Typed CE-owned cross-task behavior authority contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from polaris.cells.chief_engineer.blueprint.public.contracts._helpers import (
    _normalize_relative_portfolio_path,
    _require_completion_token,
    _require_non_empty,
    _require_provenance_sha256,
    _require_safe_filename_token,
    _strict_unique_string_tuple,
)

_SHARED_BEHAVIOR_SCHEMA: Literal["chief_engineer.shared_behavior_contract.v1"] = (
    "chief_engineer.shared_behavior_contract.v1"
)


@dataclass(frozen=True, slots=True)
class ChiefEngineerBehaviorExampleV1:
    """One concrete behavior example shared by implementers and verifiers."""

    given: str
    when: str
    then: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "given", _require_non_empty("given", self.given))
        object.__setattr__(self, "when", _require_non_empty("when", self.when))
        object.__setattr__(self, "then", _require_non_empty("then", self.then))

    def to_dict(self) -> dict[str, str]:
        return {"given": self.given, "when": self.when, "then": self.then}


@dataclass(frozen=True, slots=True)
class ChiefEngineerBehaviorInvariantV1:
    """One domain-neutral owned semantic invariant, optionally cross-task."""

    invariant_id: str
    statement: str
    owner_task_id: str
    consumer_task_ids: tuple[str, ...]
    covered_obligation_ids: tuple[str, ...]
    verification_examples: tuple[ChiefEngineerBehaviorExampleV1, ...]

    def __post_init__(self) -> None:
        invariant_id = _require_completion_token("invariant_id", self.invariant_id)
        owner_task_id = _require_completion_token("owner_task_id", self.owner_task_id)
        consumer_task_ids = _strict_unique_string_tuple("consumer_task_ids", self.consumer_task_ids)
        if owner_task_id in consumer_task_ids:
            raise ValueError("consumer_task_ids must not contain owner_task_id")
        covered_obligation_ids = _strict_unique_string_tuple(
            "covered_obligation_ids", self.covered_obligation_ids, require_items=True
        )
        if not isinstance(self.verification_examples, (list, tuple)) or not self.verification_examples:
            raise ValueError("verification_examples must contain at least one example")
        examples = tuple(self.verification_examples)
        if any(type(example) is not ChiefEngineerBehaviorExampleV1 for example in examples):
            raise TypeError("verification_examples must contain exact ChiefEngineerBehaviorExampleV1 values")
        outcomes: dict[tuple[str, str], str] = {}
        for example in examples:
            key = (example.given, example.when)
            prior = outcomes.setdefault(key, example.then)
            if prior != example.then:
                raise ValueError("verification_examples contain conflicting outcomes for the same input")
        object.__setattr__(self, "invariant_id", invariant_id)
        object.__setattr__(self, "statement", _require_non_empty("statement", self.statement))
        object.__setattr__(self, "owner_task_id", owner_task_id)
        object.__setattr__(self, "consumer_task_ids", consumer_task_ids)
        object.__setattr__(self, "covered_obligation_ids", covered_obligation_ids)
        object.__setattr__(self, "verification_examples", examples)

    def to_dict(self) -> dict[str, Any]:
        return {
            "invariant_id": self.invariant_id,
            "statement": self.statement,
            "owner_task_id": self.owner_task_id,
            "consumer_task_ids": list(self.consumer_task_ids),
            "covered_obligation_ids": list(self.covered_obligation_ids),
            "verification_examples": [example.to_dict() for example in self.verification_examples],
        }


def shared_behavior_contract_seed(
    invariants: tuple[ChiefEngineerBehaviorInvariantV1, ...],
    task_bindings: Mapping[str, tuple[str, ...]],
) -> dict[str, Any]:
    """Return canonical content used by both hash validation and persistence."""

    return {
        "schema_version": _SHARED_BEHAVIOR_SCHEMA,
        "invariants": [item.to_dict() for item in invariants],
        "task_bindings": {task_id: list(refs) for task_id, refs in sorted(task_bindings.items())},
        "authority": "chief_engineer_cross_task_behavior",
        "authoritative": True,
    }


def shared_behavior_contract_hash(
    invariants: tuple[ChiefEngineerBehaviorInvariantV1, ...],
    task_bindings: Mapping[str, tuple[str, ...]],
) -> str:
    payload = json.dumps(
        shared_behavior_contract_seed(invariants, task_bindings),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ChiefEngineerSharedBehaviorContractV1:
    """Immutable CE behavior authority projected into every linked task."""

    contract_id: str
    contract_ref: str
    contract_hash: str
    invariants: tuple[ChiefEngineerBehaviorInvariantV1, ...]
    task_bindings: Mapping[str, tuple[str, ...]]
    schema_version: Literal["chief_engineer.shared_behavior_contract.v1"] = _SHARED_BEHAVIOR_SCHEMA
    authority: Literal["chief_engineer_cross_task_behavior"] = "chief_engineer_cross_task_behavior"
    authoritative: Literal[True] = True

    def __post_init__(self) -> None:
        if self.schema_version != _SHARED_BEHAVIOR_SCHEMA:
            raise ValueError(f"schema_version must equal {_SHARED_BEHAVIOR_SCHEMA!r}")
        contract_id = _require_safe_filename_token("contract_id", self.contract_id)
        path_part, separator, fragment = _require_non_empty("contract_ref", self.contract_ref).partition("#")
        if separator != "#" or fragment != "shared_behavior_contract":
            raise ValueError("contract_ref must target the shared_behavior_contract fragment")
        contract_ref = f"{_normalize_relative_portfolio_path('contract_ref', path_part)}#shared_behavior_contract"
        if not isinstance(self.invariants, (list, tuple)):
            raise TypeError("invariants must be a list or tuple")
        invariants = tuple(self.invariants)
        if any(type(item) is not ChiefEngineerBehaviorInvariantV1 for item in invariants):
            raise TypeError("invariants must contain exact ChiefEngineerBehaviorInvariantV1 values")
        invariant_ids = [item.invariant_id for item in invariants]
        if len(invariant_ids) != len(set(invariant_ids)):
            raise ValueError("invariants must not contain duplicate invariant_id values")
        if not isinstance(self.task_bindings, Mapping):
            raise TypeError("task_bindings must be a mapping")
        task_bindings = {
            _require_completion_token("task_bindings task_id", str(task_id)): _strict_unique_string_tuple(
                f"task_bindings[{task_id!r}]", refs
            )
            for task_id, refs in self.task_bindings.items()
        }
        known_ids = set(invariant_ids)
        unknown_refs = sorted({ref for refs in task_bindings.values() for ref in refs if ref not in known_ids})
        if unknown_refs:
            raise ValueError(f"task_bindings reference unknown invariants: {unknown_refs}")
        expected_hash = shared_behavior_contract_hash(invariants, task_bindings)
        contract_hash = _require_provenance_sha256("contract_hash", self.contract_hash)
        if contract_hash != expected_hash:
            raise ValueError("contract_hash must match canonical shared behavior content")
        object.__setattr__(self, "contract_id", contract_id)
        object.__setattr__(self, "contract_ref", contract_ref)
        object.__setattr__(self, "contract_hash", contract_hash)
        object.__setattr__(self, "invariants", invariants)
        object.__setattr__(self, "task_bindings", task_bindings)

    def to_reference(self) -> dict[str, str]:
        return {
            "schema_version": "chief_engineer.shared_behavior_contract.reference.v1",
            "shared_behavior_contract_id": self.contract_id,
            "shared_behavior_contract_ref": self.contract_ref,
            "shared_behavior_contract_hash": self.contract_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **shared_behavior_contract_seed(self.invariants, self.task_bindings),
            "shared_behavior_contract_id": self.contract_id,
            "shared_behavior_contract_ref": self.contract_ref,
            "shared_behavior_contract_hash": self.contract_hash,
        }
