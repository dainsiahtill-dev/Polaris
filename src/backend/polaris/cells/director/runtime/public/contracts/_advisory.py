"""Repair advisory policy and validation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.public.contracts._helpers import (
    _require_non_empty,
    _to_dict_copy,
    _to_tuple_str,
)


@dataclass(frozen=True)
class QueryDirectorRepairAdvisoryPolicyV1:
    """Query shape for the non-authoritative AGI repair advisory policy."""

    include_field_lists: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_field_lists", bool(self.include_field_lists))


@dataclass(frozen=True)
class DirectorRepairAdvisoryPolicyResultV1:
    """Read-only policy projection for future AGI repair advisory overlays."""

    schema_version: str
    source: str
    access: str
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_advisory_no_writes_no_registration"
    agi_execution_authority: bool = False
    writes_allowed: bool = False
    registration_allowed: bool = False
    authoritative_receipts_allowed: bool = False
    allowed_suggested_rule_fields: tuple[str, ...] = ()
    forbidden_metadata_fields: tuple[str, ...] = ()
    forbidden_suggested_rule_fields: tuple[str, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "authoritative_receipts_allowed", False)
        object.__setattr__(
            self, "allowed_suggested_rule_fields", _to_tuple_str(list(self.allowed_suggested_rule_fields))
        )
        object.__setattr__(self, "forbidden_metadata_fields", _to_tuple_str(list(self.forbidden_metadata_fields)))
        object.__setattr__(
            self,
            "forbidden_suggested_rule_fields",
            _to_tuple_str(list(self.forbidden_suggested_rule_fields)),
        )
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "registration_allowed": False,
            "authoritative_receipts_allowed": False,
            "allowed_suggested_rule_fields": list(self.allowed_suggested_rule_fields),
            "forbidden_metadata_fields": list(self.forbidden_metadata_fields),
            "forbidden_suggested_rule_fields": list(self.forbidden_suggested_rule_fields),
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class QueryDirectorRepairAdvisoryValidationV1:
    """Read-only query for validating a future AGI repair advisory payload."""

    advisor_source: str
    message: str
    confidence: float = 0.0
    suggested_rules: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "advisor_source", _require_non_empty("advisor_source", self.advisor_source))
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "confidence", max(0.0, min(float(self.confidence), 1.0)))
        object.__setattr__(self, "suggested_rules", tuple(dict(item or {}) for item in self.suggested_rules))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class DirectorRepairAdvisoryValidationResultV1:
    """Read-only validation result for non-authoritative AGI repair advisory payloads."""

    schema_version: str
    source: str
    access: str
    ok: bool
    normalized_advisory: Mapping[str, Any] | None = None
    errors: tuple[str, ...] = ()
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_advisory_validation_no_writes_no_registration"
    agi_execution_authority: bool = False
    writes_allowed: bool = False
    registration_allowed: bool = False
    authoritative_receipts_allowed: bool = False
    summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(
            self,
            "normalized_advisory",
            dict(self.normalized_advisory) if self.normalized_advisory is not None else None,
        )
        object.__setattr__(self, "errors", _to_tuple_str(list(self.errors)))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "authoritative_receipts_allowed", False)
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "ok": self.ok,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "registration_allowed": False,
            "authoritative_receipts_allowed": False,
            "normalized_advisory": dict(self.normalized_advisory) if self.normalized_advisory is not None else None,
            "errors": list(self.errors),
            "summary": dict(self.summary),
        }
