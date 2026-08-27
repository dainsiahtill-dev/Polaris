"""Typed contracts for owner-scoped Chief Engineer semantic repair."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from ._behavior import ChiefEngineerBehaviorExampleV1, ChiefEngineerBehaviorInvariantV1
from ._completion import ArtifactObligationV1, EntrypointObligationV1
from ._helpers import (
    _require_completion_token,
    _require_non_empty,
    _require_provenance_sha256,
    _require_safe_filename_token,
    _strict_unique_string_tuple,
)

ChiefEngineerSemanticRepairOperationV1 = Literal[
    "artifact_upsert",
    "entrypoint_upsert",
    "behavior_invariant_upsert",
    "task_behavior_ref_replace",
]

_ALLOWED_OPERATIONS = frozenset(
    {
        "artifact_upsert",
        "entrypoint_upsert",
        "behavior_invariant_upsert",
        "task_behavior_ref_replace",
    }
)


def _canonical_payload_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _exact_mapping(name: str, value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return deepcopy(dict(value))


def _require_task_set_hash(task_ids: tuple[str, ...], task_set_hash: str) -> str:
    expected = _canonical_payload_hash(list(task_ids))
    actual = _require_provenance_sha256("task_set_hash", task_set_hash)
    if actual != expected:
        raise ValueError("task_set_hash must match canonical task_ids")
    return actual


@dataclass(frozen=True, slots=True)
class ChiefEngineerPortfolioStructuralRecoveryV1:
    """Auditable, content-preserving recovery of malformed CE tool arguments."""

    source_payload: Mapping[str, Any]
    payload: Mapping[str, Any]
    repair_codes: tuple[str, ...]
    source_hash: str = field(init=False)
    recovered_hash: str = field(init=False)
    recovered: bool = field(init=False)
    schema_version: Literal["chief_engineer.portfolio_structural_recovery.v1"] = (
        "chief_engineer.portfolio_structural_recovery.v1"
    )

    def __post_init__(self) -> None:
        source_payload = _exact_mapping("source_payload", self.source_payload)
        payload = _exact_mapping("payload", self.payload)
        repair_codes = _strict_unique_string_tuple("repair_codes", self.repair_codes)
        source_hash = _canonical_payload_hash(source_payload)
        recovered_hash = _canonical_payload_hash(payload)
        recovered = bool(repair_codes) and source_hash != recovered_hash
        if bool(repair_codes) != recovered:
            raise ValueError("repair_codes must describe a content-changing recovery")
        object.__setattr__(self, "source_payload", source_payload)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "repair_codes", repair_codes)
        object.__setattr__(self, "source_hash", source_hash)
        object.__setattr__(self, "recovered_hash", recovered_hash)
        object.__setattr__(self, "recovered", recovered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_hash": self.source_hash,
            "recovered_hash": self.recovered_hash,
            "recovered": self.recovered,
            "repair_codes": list(self.repair_codes),
        }


@dataclass(frozen=True, slots=True)
class ChiefEngineerSemanticRepairCandidateV1:
    """One schema-valid CE provider candidate bound to immutable authority."""

    workspace: str
    project_id: str
    run_id: str
    pm_contract_hash: str
    task_ids: tuple[str, ...]
    task_set_hash: str
    candidate: Mapping[str, Any]
    candidate_hash: str = field(init=False)
    schema_version: Literal["chief_engineer.semantic_repair_candidate.v1"] = (
        "chief_engineer.semantic_repair_candidate.v1"
    )

    def __post_init__(self) -> None:
        task_ids = _strict_unique_string_tuple("task_ids", self.task_ids, require_items=True)
        candidate = _exact_mapping("candidate", self.candidate)
        construction_plan = _exact_mapping("candidate.construction_plan", candidate.get("construction_plan"))
        task_plans = _exact_mapping("candidate.construction_plan.task_plans", construction_plan.get("task_plans"))
        unknown_task_plans = set(task_plans) - set(task_ids)
        if unknown_task_plans:
            raise ValueError(f"candidate task_plans contain unknown task ids: {sorted(unknown_task_plans)}")
        _exact_mapping("candidate.project_completion_contract", candidate.get("project_completion_contract"))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "project_id", _require_safe_filename_token("project_id", self.project_id))
        object.__setattr__(self, "run_id", _require_safe_filename_token("run_id", self.run_id))
        object.__setattr__(
            self, "pm_contract_hash", _require_provenance_sha256("pm_contract_hash", self.pm_contract_hash)
        )
        object.__setattr__(self, "task_ids", task_ids)
        object.__setattr__(self, "task_set_hash", _require_task_set_hash(task_ids, self.task_set_hash))
        object.__setattr__(self, "candidate", candidate)
        object.__setattr__(self, "candidate_hash", _canonical_payload_hash(candidate))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ChiefEngineerSemanticRepairCandidateV1:
        if set(payload) != {
            "schema_version",
            "workspace",
            "project_id",
            "run_id",
            "pm_contract_hash",
            "task_ids",
            "task_set_hash",
            "candidate",
            "candidate_hash",
        }:
            raise ValueError("semantic repair candidate fields are invalid")
        if payload["schema_version"] != "chief_engineer.semantic_repair_candidate.v1":
            raise ValueError("semantic repair candidate schema_version is invalid")
        candidate = cls(
            workspace=payload["workspace"],
            project_id=payload["project_id"],
            run_id=payload["run_id"],
            pm_contract_hash=payload["pm_contract_hash"],
            task_ids=payload["task_ids"],
            task_set_hash=payload["task_set_hash"],
            candidate=payload["candidate"],
        )
        if payload["candidate_hash"] != candidate.candidate_hash:
            raise ValueError("persisted candidate_hash does not match candidate")
        return candidate

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace": self.workspace,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "pm_contract_hash": self.pm_contract_hash,
            "task_ids": list(self.task_ids),
            "task_set_hash": self.task_set_hash,
            "candidate": deepcopy(dict(self.candidate)),
            "candidate_hash": self.candidate_hash,
        }


@dataclass(frozen=True, slots=True)
class ChiefEngineerSemanticRepairDiagnosisV1:
    """Stable semantic diagnosis authorizing only enumerated patch operations."""

    candidate_hash: str
    diagnostic_codes: tuple[str, ...]
    allowed_operations: tuple[ChiefEngineerSemanticRepairOperationV1, ...]
    diagnosis_hash: str = field(init=False)
    schema_version: Literal["chief_engineer.semantic_repair_diagnosis.v1"] = (
        "chief_engineer.semantic_repair_diagnosis.v1"
    )

    def __post_init__(self) -> None:
        candidate_hash = _require_provenance_sha256("candidate_hash", self.candidate_hash)
        diagnostic_codes = _strict_unique_string_tuple("diagnostic_codes", self.diagnostic_codes, require_items=True)
        allowed_operations = _strict_unique_string_tuple(
            "allowed_operations", self.allowed_operations, require_items=True
        )
        unknown = set(allowed_operations) - _ALLOWED_OPERATIONS
        if unknown:
            raise ValueError(f"unsupported semantic repair operations: {sorted(unknown)}")
        seed = {
            "schema_version": self.schema_version,
            "candidate_hash": candidate_hash,
            "diagnostic_codes": list(diagnostic_codes),
            "allowed_operations": list(allowed_operations),
        }
        object.__setattr__(self, "candidate_hash", candidate_hash)
        object.__setattr__(self, "diagnostic_codes", diagnostic_codes)
        object.__setattr__(self, "allowed_operations", allowed_operations)
        object.__setattr__(self, "diagnosis_hash", _canonical_payload_hash(seed))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_hash": self.candidate_hash,
            "diagnostic_codes": list(self.diagnostic_codes),
            "allowed_operations": list(self.allowed_operations),
            "diagnosis_hash": self.diagnosis_hash,
        }


@dataclass(frozen=True, slots=True)
class ChiefEngineerSemanticRepairPatchV1:
    """Typed CE semantic patch with diagnosis-scoped entrypoint replacement."""

    base_candidate_hash: str
    diagnosis_hash: str
    artifact_upserts: tuple[ArtifactObligationV1, ...] = ()
    entrypoint_upserts: tuple[EntrypointObligationV1, ...] = ()
    entrypoint_remove_obligation_ids: tuple[str, ...] = ()
    behavior_invariant_upserts: tuple[ChiefEngineerBehaviorInvariantV1, ...] = ()
    task_behavior_ref_replacements: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    operations: tuple[ChiefEngineerSemanticRepairOperationV1, ...] = field(init=False)
    patch_hash: str = field(init=False)
    schema_version: Literal["chief_engineer.semantic_repair_patch.v1"] = "chief_engineer.semantic_repair_patch.v1"

    def __post_init__(self) -> None:
        base_hash = _require_provenance_sha256("base_candidate_hash", self.base_candidate_hash)
        diagnosis_hash = _require_provenance_sha256("diagnosis_hash", self.diagnosis_hash)
        typed_groups = (
            ("artifact_upserts", self.artifact_upserts, ArtifactObligationV1),
            ("entrypoint_upserts", self.entrypoint_upserts, EntrypointObligationV1),
            ("behavior_invariant_upserts", self.behavior_invariant_upserts, ChiefEngineerBehaviorInvariantV1),
        )
        for name, values, expected_type in typed_groups:
            if not isinstance(values, (list, tuple)) or any(type(value) is not expected_type for value in values):
                raise TypeError(f"{name} must contain exact {expected_type.__name__} values")
        refs = {
            _require_completion_token("task_behavior_ref_replacements task_id", task_id): _strict_unique_string_tuple(
                f"task_behavior_ref_replacements[{task_id!r}]", values
            )
            for task_id, values in self.task_behavior_ref_replacements.items()
        }
        entrypoint_removals = _strict_unique_string_tuple(
            "entrypoint_remove_obligation_ids",
            self.entrypoint_remove_obligation_ids,
        )
        operations: list[ChiefEngineerSemanticRepairOperationV1] = []
        if self.artifact_upserts:
            operations.append("artifact_upsert")
        if self.entrypoint_upserts or entrypoint_removals:
            operations.append("entrypoint_upsert")
        if self.behavior_invariant_upserts:
            operations.append("behavior_invariant_upsert")
        if refs:
            operations.append("task_behavior_ref_replace")
        if not operations:
            raise ValueError("semantic repair patch must contain at least one operation")
        seed = {
            "schema_version": self.schema_version,
            "base_candidate_hash": base_hash,
            "diagnosis_hash": diagnosis_hash,
            "artifact_upserts": [value.to_dict() for value in self.artifact_upserts],
            "entrypoint_upserts": [value.to_dict() for value in self.entrypoint_upserts],
            "entrypoint_remove_obligation_ids": list(entrypoint_removals),
            "behavior_invariant_upserts": [value.to_dict() for value in self.behavior_invariant_upserts],
            "task_behavior_ref_replacements": {key: list(value) for key, value in sorted(refs.items())},
            "operations": operations,
        }
        object.__setattr__(self, "base_candidate_hash", base_hash)
        object.__setattr__(self, "diagnosis_hash", diagnosis_hash)
        object.__setattr__(self, "artifact_upserts", tuple(self.artifact_upserts))
        object.__setattr__(self, "entrypoint_upserts", tuple(self.entrypoint_upserts))
        object.__setattr__(self, "entrypoint_remove_obligation_ids", entrypoint_removals)
        object.__setattr__(self, "behavior_invariant_upserts", tuple(self.behavior_invariant_upserts))
        object.__setattr__(self, "task_behavior_ref_replacements", refs)
        object.__setattr__(self, "operations", tuple(operations))
        object.__setattr__(self, "patch_hash", _canonical_payload_hash(seed))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "base_candidate_hash": self.base_candidate_hash,
            "diagnosis_hash": self.diagnosis_hash,
            "artifact_upserts": [value.to_dict() for value in self.artifact_upserts],
            "entrypoint_upserts": [value.to_dict() for value in self.entrypoint_upserts],
            "entrypoint_remove_obligation_ids": list(self.entrypoint_remove_obligation_ids),
            "behavior_invariant_upserts": [value.to_dict() for value in self.behavior_invariant_upserts],
            "task_behavior_ref_replacements": {
                key: list(value) for key, value in sorted(self.task_behavior_ref_replacements.items())
            },
            "operations": list(self.operations),
            "patch_hash": self.patch_hash,
        }

    @classmethod
    def from_provider_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        allowed_operations: tuple[ChiefEngineerSemanticRepairOperationV1, ...] | None = None,
    ) -> ChiefEngineerSemanticRepairPatchV1:
        """Parse only diagnosis-authorized groups from the exact provider envelope."""

        expected_fields = {
            "base_candidate_hash",
            "diagnosis_hash",
            "artifact_upserts",
            "entrypoint_upserts",
            "behavior_invariant_upserts",
            "task_behavior_ref_replacements",
        }
        optional_fields = {"entrypoint_remove_obligation_ids"}
        if not expected_fields.issubset(payload) or set(payload) - expected_fields - optional_fields:
            raise ValueError("semantic repair provider patch fields are invalid")
        all_operations: frozenset[ChiefEngineerSemanticRepairOperationV1] = frozenset(
            {
                "artifact_upsert",
                "entrypoint_upsert",
                "behavior_invariant_upsert",
                "task_behavior_ref_replace",
            }
        )
        enabled_operations = all_operations if allowed_operations is None else frozenset(allowed_operations)
        unknown_operations = enabled_operations - all_operations
        if unknown_operations:
            raise ValueError(f"semantic repair allowed_operations are invalid: {sorted(unknown_operations)}")

        def rows(name: str) -> tuple[Mapping[str, Any], ...]:
            value = payload.get(name)
            if not isinstance(value, list):
                raise TypeError(f"{name} must be a list")
            if any(not isinstance(item, Mapping) for item in value):
                raise TypeError(f"{name} must contain mappings")
            return tuple(value)

        artifact_rows = rows("artifact_upserts") if "artifact_upsert" in enabled_operations else ()
        entrypoint_rows = rows("entrypoint_upserts") if "entrypoint_upsert" in enabled_operations else ()
        raw_entrypoint_removals = (
            payload.get("entrypoint_remove_obligation_ids", [])
            if "entrypoint_upsert" in enabled_operations
            else []
        )
        if not isinstance(raw_entrypoint_removals, list) or any(
            not isinstance(value, str) for value in raw_entrypoint_removals
        ):
            raise TypeError("entrypoint_remove_obligation_ids must contain strings")
        behavior_rows = (
            rows("behavior_invariant_upserts") if "behavior_invariant_upsert" in enabled_operations else ()
        )
        refs: Mapping[Any, Any]
        if "task_behavior_ref_replace" in enabled_operations:
            raw_refs = payload.get("task_behavior_ref_replacements")
            if not isinstance(raw_refs, Mapping):
                raise TypeError("task_behavior_ref_replacements must be a mapping")
            if any(not isinstance(value, list) for value in raw_refs.values()):
                raise TypeError("task_behavior_ref_replacements values must be lists")
            refs = raw_refs
        else:
            refs = {}

        def exact(row: Mapping[str, Any], fields: set[str], name: str) -> None:
            if set(row) != fields:
                raise ValueError(f"{name} fields are invalid")

        artifacts: list[ArtifactObligationV1] = []
        for row in artifact_rows:
            exact(
                row,
                {"obligation_id", "path", "semantic_role", "applicability", "owner_task_id"},
                "artifact_upsert",
            )
            artifacts.append(
                ArtifactObligationV1(
                    obligation_id=row["obligation_id"],
                    path=row["path"],
                    semantic_role=row["semantic_role"],
                    applicability=row["applicability"],
                    owner_task_id=row["owner_task_id"],
                )
            )

        entrypoints: list[EntrypointObligationV1] = []
        entrypoint_fields = {
            "obligation_id",
            "kind",
            "applicability",
            "owner_task_id",
            "source_path",
            "runtime_path",
            "command",
        }
        for row in entrypoint_rows:
            exact(row, entrypoint_fields, "entrypoint_upsert")
            entrypoints.append(
                EntrypointObligationV1(
                    obligation_id=row["obligation_id"],
                    kind=row["kind"],
                    applicability=row["applicability"],
                    owner_task_id=row["owner_task_id"],
                    source_path=row["source_path"],
                    runtime_path=row["runtime_path"],
                    command=row["command"],
                )
            )

        behaviors: list[ChiefEngineerBehaviorInvariantV1] = []
        behavior_fields = {
            "invariant_id",
            "statement",
            "owner_task_id",
            "consumer_task_ids",
            "covered_obligation_ids",
            "verification_examples",
        }
        for row in behavior_rows:
            exact(row, behavior_fields, "behavior_invariant_upsert")
            owner_task_id = row["owner_task_id"]
            raw_consumer_task_ids = row["consumer_task_ids"]
            consumer_task_ids = raw_consumer_task_ids
            if (
                isinstance(owner_task_id, str)
                and isinstance(raw_consumer_task_ids, list)
                and owner_task_id in raw_consumer_task_ids
            ):
                remaining_consumers = [
                    task_id for task_id in raw_consumer_task_ids if task_id != owner_task_id
                ]
                # Removing a redundant self-reference is mechanical. An empty
                # result is valid for a task-local invariant in a one-task
                # portfolio; inventing a sibling consumer is never allowed.
                consumer_task_ids = remaining_consumers
            example_rows = row["verification_examples"]
            if not isinstance(example_rows, list) or any(not isinstance(item, Mapping) for item in example_rows):
                raise TypeError("verification_examples must contain mappings")
            examples: list[ChiefEngineerBehaviorExampleV1] = []
            for example in example_rows:
                exact(example, {"given", "when", "then"}, "verification_example")
                examples.append(
                    ChiefEngineerBehaviorExampleV1(
                        given=example["given"],
                        when=example["when"],
                        then=example["then"],
                    )
                )
            behaviors.append(
                ChiefEngineerBehaviorInvariantV1(
                    invariant_id=row["invariant_id"],
                    statement=row["statement"],
                    owner_task_id=owner_task_id,
                    consumer_task_ids=tuple(consumer_task_ids),
                    covered_obligation_ids=tuple(row["covered_obligation_ids"]),
                    verification_examples=tuple(examples),
                )
            )

        return cls(
            base_candidate_hash=payload["base_candidate_hash"],
            diagnosis_hash=payload["diagnosis_hash"],
            artifact_upserts=tuple(artifacts),
            entrypoint_upserts=tuple(entrypoints),
            entrypoint_remove_obligation_ids=tuple(raw_entrypoint_removals),
            behavior_invariant_upserts=tuple(behaviors),
            task_behavior_ref_replacements={str(key): tuple(value) for key, value in refs.items()},
        )


@dataclass(frozen=True, slots=True)
class ChiefEngineerSemanticRepairReceiptV1:
    """Immutable proof that typed composition preserved all untouched sections."""

    before_candidate_hash: str
    patch_hash: str
    after_candidate_hash: str
    diagnosis_hash: str
    changed_semantic_ids: tuple[str, ...]
    unchanged_section_hashes: Mapping[str, str]
    receipt_hash: str = field(init=False)
    schema_version: Literal["chief_engineer.semantic_repair_receipt.v1"] = "chief_engineer.semantic_repair_receipt.v1"

    def __post_init__(self) -> None:
        before_hash = _require_provenance_sha256("before_candidate_hash", self.before_candidate_hash)
        patch_hash = _require_provenance_sha256("patch_hash", self.patch_hash)
        after_hash = _require_provenance_sha256("after_candidate_hash", self.after_candidate_hash)
        diagnosis_hash = _require_provenance_sha256("diagnosis_hash", self.diagnosis_hash)
        changed_ids = _strict_unique_string_tuple("changed_semantic_ids", self.changed_semantic_ids, require_items=True)
        section_hashes = {
            _require_completion_token("unchanged_section_hashes key", key): _require_provenance_sha256(
                f"unchanged_section_hashes[{key!r}]", value
            )
            for key, value in self.unchanged_section_hashes.items()
        }
        seed = {
            "schema_version": self.schema_version,
            "before_candidate_hash": before_hash,
            "patch_hash": patch_hash,
            "after_candidate_hash": after_hash,
            "diagnosis_hash": diagnosis_hash,
            "changed_semantic_ids": list(changed_ids),
            "unchanged_section_hashes": dict(sorted(section_hashes.items())),
        }
        object.__setattr__(self, "before_candidate_hash", before_hash)
        object.__setattr__(self, "patch_hash", patch_hash)
        object.__setattr__(self, "after_candidate_hash", after_hash)
        object.__setattr__(self, "diagnosis_hash", diagnosis_hash)
        object.__setattr__(self, "changed_semantic_ids", changed_ids)
        object.__setattr__(self, "unchanged_section_hashes", section_hashes)
        object.__setattr__(self, "receipt_hash", _canonical_payload_hash(seed))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "before_candidate_hash": self.before_candidate_hash,
            "patch_hash": self.patch_hash,
            "after_candidate_hash": self.after_candidate_hash,
            "diagnosis_hash": self.diagnosis_hash,
            "changed_semantic_ids": list(self.changed_semantic_ids),
            "unchanged_section_hashes": dict(sorted(self.unchanged_section_hashes.items())),
            "receipt_hash": self.receipt_hash,
        }


def chief_engineer_semantic_repair_task_set_hash(task_ids: tuple[str, ...]) -> str:
    """Return canonical task-set CAS identity for candidate construction."""

    normalized = _strict_unique_string_tuple("task_ids", task_ids, require_items=True)
    return _canonical_payload_hash(list(normalized))


__all__ = [
    "ChiefEngineerPortfolioStructuralRecoveryV1",
    "ChiefEngineerSemanticRepairCandidateV1",
    "ChiefEngineerSemanticRepairDiagnosisV1",
    "ChiefEngineerSemanticRepairOperationV1",
    "ChiefEngineerSemanticRepairPatchV1",
    "ChiefEngineerSemanticRepairReceiptV1",
    "chief_engineer_semantic_repair_task_set_hash",
]
