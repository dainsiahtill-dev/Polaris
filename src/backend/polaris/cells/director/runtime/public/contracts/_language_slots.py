"""Repair language-slot query and result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.public.contracts._helpers import (
    _default_repairer_module_name,
    _require_non_empty,
    _to_dict_copy,
    _to_tuple_str,
)


@dataclass(frozen=True)
class QueryDirectorRepairLanguageSlotsV1:
    """Query shape for reserved deterministic repair language slots."""

    include_items: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_items", bool(self.include_items))


@dataclass(frozen=True)
class DirectorRepairLanguageSlotV1:
    """Public projection of one future language repair extension slot."""

    language: str
    aliases: tuple[str, ...] = ()
    file_extensions: tuple[str, ...] = ()
    file_names: tuple[str, ...] = ()
    diagnostic_sources: tuple[str, ...] = ()
    preferred_archetypes: tuple[str, ...] = ()
    repairer_module: str = ""
    implementation_status: str = "reserved_only"
    registration_policy: str = "bench_verified_rule_required"
    authoritative_source_tools: tuple[str, ...] = ()
    executable_runtime_source_tools: tuple[str, ...] = ()
    notes: str = ""
    slot_owner_cell: str = "director.runtime"
    bench_evidence_required: bool = True
    rule_authoring_status: str = "reserved_only"
    next_action: str = "add_bench_verified_rule_metadata_then_runtime_binding"

    def __post_init__(self) -> None:
        object.__setattr__(self, "language", _require_non_empty("language", self.language))
        object.__setattr__(self, "aliases", _to_tuple_str(list(self.aliases)))
        object.__setattr__(self, "file_extensions", _to_tuple_str(list(self.file_extensions)))
        object.__setattr__(self, "file_names", _to_tuple_str(list(self.file_names)))
        object.__setattr__(self, "diagnostic_sources", _to_tuple_str(list(self.diagnostic_sources)))
        object.__setattr__(self, "preferred_archetypes", _to_tuple_str(list(self.preferred_archetypes)))
        object.__setattr__(
            self,
            "repairer_module",
            _require_non_empty(
                "repairer_module",
                self.repairer_module or _default_repairer_module_name(self.language),
            ),
        )
        object.__setattr__(
            self, "implementation_status", _require_non_empty("implementation_status", self.implementation_status)
        )
        object.__setattr__(
            self,
            "registration_policy",
            _require_non_empty("registration_policy", self.registration_policy),
        )
        object.__setattr__(self, "authoritative_source_tools", _to_tuple_str(list(self.authoritative_source_tools)))
        object.__setattr__(
            self,
            "executable_runtime_source_tools",
            _to_tuple_str(list(self.executable_runtime_source_tools)),
        )
        object.__setattr__(self, "notes", str(self.notes or "").strip())
        object.__setattr__(self, "slot_owner_cell", _require_non_empty("slot_owner_cell", self.slot_owner_cell))
        object.__setattr__(self, "bench_evidence_required", bool(self.bench_evidence_required))
        object.__setattr__(
            self,
            "rule_authoring_status",
            _require_non_empty("rule_authoring_status", self.rule_authoring_status),
        )
        object.__setattr__(self, "next_action", _require_non_empty("next_action", self.next_action))

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "aliases": list(self.aliases),
            "file_extensions": list(self.file_extensions),
            "file_names": list(self.file_names),
            "diagnostic_sources": list(self.diagnostic_sources),
            "preferred_archetypes": list(self.preferred_archetypes),
            "repairer_module": self.repairer_module,
            "implementation_status": self.implementation_status,
            "registration_policy": self.registration_policy,
            "authoritative_source_tools": list(self.authoritative_source_tools),
            "executable_runtime_source_tools": list(self.executable_runtime_source_tools),
            "notes": self.notes,
            "slot_owner_cell": self.slot_owner_cell,
            "bench_evidence_required": self.bench_evidence_required,
            "rule_authoring_status": self.rule_authoring_status,
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class DirectorRepairLanguageSlotsResultV1:
    """Read-only catalog of reserved language repair extension slots."""

    schema_version: str
    source: str
    access: str
    items: tuple[DirectorRepairLanguageSlotV1, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_language_slots_no_rule_registration"
    authoritative_rule_registration: bool = False
    agi_execution_authority: bool = False
    writes_allowed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "items", tuple(self.items or ()))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "authoritative_rule_registration", False)
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "authoritative_rule_registration": False,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "items": [item.to_dict() for item in self.items],
            "summary": dict(self.summary),
        }
