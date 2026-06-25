"""Rule registry and diagnostic coverage reporting for Director repairs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .contracts import RepairDiagnostic
from .strategy_catalog import deterministic_repair_source_tool_known
from .typescript_syntax import TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL


class RepairArchetype(str, Enum):
    """Language-independent repair pattern family."""

    INCOMPATIBLE_DERIVE = "incompatible_derive"
    MISSING_DEPENDENCY = "missing_dependency"
    MISSING_METHOD_SELF = "missing_method_self"
    NULLABLE_TYPE_MISMATCH = "nullable_type_mismatch"
    OBJECT_LITERAL_SYNTAX = "object_literal_syntax"
    WRONG_IMPORT_PATH = "wrong_import_path"


@dataclass(frozen=True)
class RepairRuleDefinition:
    """Declarative metadata and matcher for one deterministic repair rule."""

    rule_id: str
    source_tool: str
    language: str
    phase: str
    archetype: RepairArchetype
    priority: int = 1
    depends_on: tuple[str, ...] = ()
    diagnostic_codes: tuple[str, ...] = ()
    message_terms: tuple[str, ...] = ()
    risk_level: str = "low"
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rule_id", _non_empty(self.rule_id))
        object.__setattr__(self, "source_tool", _non_empty(self.source_tool))
        object.__setattr__(self, "language", _non_empty(self.language))
        object.__setattr__(self, "phase", _non_empty(self.phase))
        object.__setattr__(self, "priority", max(0, int(self.priority)))
        object.__setattr__(self, "depends_on", _tuple_str(self.depends_on))
        object.__setattr__(self, "diagnostic_codes", tuple(code.lower() for code in _tuple_str(self.diagnostic_codes)))
        object.__setattr__(self, "message_terms", tuple(term.lower() for term in _tuple_str(self.message_terms)))
        object.__setattr__(self, "risk_level", _non_empty(self.risk_level))
        object.__setattr__(self, "description", str(self.description or "").strip())
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if not deterministic_repair_source_tool_known(self.source_tool):
            raise ValueError(f"unregistered repair source_tool: {self.source_tool}")

    def matches(self, diagnostic: RepairDiagnostic) -> bool:
        """Return whether this rule claims coverage for a diagnostic."""

        diagnostic_code = diagnostic.code.lower()
        if self.diagnostic_codes and diagnostic_code not in self.diagnostic_codes:
            return False
        if self.message_terms:
            haystack = (diagnostic.message or diagnostic.raw).lower()
            return all(term in haystack for term in self.message_terms)
        return bool(self.diagnostic_codes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "source_tool": self.source_tool,
            "language": self.language,
            "phase": self.phase,
            "archetype": self.archetype.value,
            "priority": self.priority,
            "depends_on": list(self.depends_on),
            "diagnostic_codes": list(self.diagnostic_codes),
            "message_terms": list(self.message_terms),
            "risk_level": self.risk_level,
            "description": self.description,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RepairDiagnosticCoverage:
    """Coverage result for one diagnostic."""

    diagnostic: RepairDiagnostic
    matched_rules: tuple[RepairRuleDefinition, ...] = ()

    @property
    def known_rule_matched(self) -> bool:
        return bool(self.matched_rules)

    def to_dict(self) -> dict[str, Any]:
        diagnostic_archetype = _suggest_rule_family(self.diagnostic)
        return {
            "diagnostic": self.diagnostic.to_dict(),
            "known_rule_matched": self.known_rule_matched,
            "matched_rule_ids": [rule.rule_id for rule in self.matched_rules],
            "matched_source_tools": [rule.source_tool for rule in self.matched_rules],
            "archetypes": sorted({rule.archetype.value for rule in self.matched_rules}),
            "phases": sorted({rule.phase for rule in self.matched_rules}),
            "languages": sorted({rule.language for rule in self.matched_rules}),
            "diagnostic_archetype": diagnostic_archetype,
            "diagnostic_phase": _infer_diagnostic_phase(self.diagnostic, diagnostic_archetype),
            "diagnostic_language": _infer_diagnostic_language(self.diagnostic),
            "suggested_rule_family": diagnostic_archetype,
        }


@dataclass(frozen=True)
class RepairCoverageReport:
    """Aggregate diagnostic coverage report."""

    items: tuple[RepairDiagnosticCoverage, ...]

    @property
    def total_diagnostics(self) -> int:
        return len(self.items)

    @property
    def covered_diagnostic_count(self) -> int:
        return sum(1 for item in self.items if item.known_rule_matched)

    @property
    def uncovered_diagnostic_count(self) -> int:
        return self.total_diagnostics - self.covered_diagnostic_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_diagnostics": self.total_diagnostics,
            "covered_diagnostic_count": self.covered_diagnostic_count,
            "uncovered_diagnostic_count": self.uncovered_diagnostic_count,
            "items": [item.to_dict() for item in self.items],
            "uncovered_diagnostics": [item.diagnostic.to_dict() for item in self.items if not item.known_rule_matched],
        }


class RepairRuleRegistry:
    """Registry for deterministic repair rule definitions."""

    def __init__(self, rules: Sequence[RepairRuleDefinition] = ()) -> None:
        self._rules: dict[str, RepairRuleDefinition] = {}
        for rule in rules:
            self.register(rule)

    def register(self, rule: RepairRuleDefinition) -> None:
        """Register a rule, failing closed on duplicate ids."""

        if rule.rule_id in self._rules:
            raise ValueError(f"duplicate repair rule_id: {rule.rule_id}")
        self._rules[rule.rule_id] = rule

    def rules(self) -> tuple[RepairRuleDefinition, ...]:
        return tuple(self._rules[key] for key in sorted(self._rules))

    def match_diagnostic(self, diagnostic: RepairDiagnostic) -> tuple[RepairRuleDefinition, ...]:
        return tuple(rule for rule in self.rules() if rule.matches(diagnostic))

    def coverage(self, diagnostics: Sequence[RepairDiagnostic]) -> RepairCoverageReport:
        return RepairCoverageReport(
            items=tuple(
                RepairDiagnosticCoverage(diagnostic=diagnostic, matched_rules=self.match_diagnostic(diagnostic))
                for diagnostic in diagnostics
            )
        )

    def catalog(self) -> list[dict[str, Any]]:
        return [rule.to_dict() for rule in self.rules()]


def default_repair_rule_registry() -> RepairRuleRegistry:
    """Return the initial Director Runtime repair rule registry."""

    return RepairRuleRegistry(
        (
            RepairRuleDefinition(
                rule_id="typescript.object_literal_missing_comma",
                source_tool=TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("typescript_ts1005",),
                message_terms=(",", "expected"),
                risk_level="low",
                description="Repairs object-literal comma omissions reported as TS1005.",
            ),
        )
    )


def build_repair_coverage_report(
    diagnostics: Sequence[RepairDiagnostic],
    registry: RepairRuleRegistry | None = None,
) -> RepairCoverageReport:
    """Build a coverage report for diagnostics using the configured registry."""

    return (registry or default_repair_rule_registry()).coverage(diagnostics)


def _non_empty(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("repair rule field must be non-empty")
    return normalized


def _tuple_str(value: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or ()) if str(item or "").strip())


def _suggest_rule_family(diagnostic: RepairDiagnostic) -> str:
    code = diagnostic.code.lower()
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    language = _infer_diagnostic_language(diagnostic)
    if language == "rust" and _looks_like_rust_missing_method_self(message):
        return RepairArchetype.MISSING_METHOD_SELF.value
    if "import" in message or "unresolved" in message:
        return RepairArchetype.WRONG_IMPORT_PATH.value
    if "null" in message or "undefined" in message:
        return RepairArchetype.NULLABLE_TYPE_MISMATCH.value
    if "dependency" in message or "crate" in message or "module" in message:
        return RepairArchetype.MISSING_DEPENDENCY.value
    if code.startswith("typescript_ts") or "expected" in message:
        return RepairArchetype.OBJECT_LITERAL_SYNTAX.value
    return "unknown"


def _looks_like_rust_missing_method_self(message: str) -> bool:
    has_receiver_hint = "`&self`" in message or "`&mut self`" in message or "&self" in message
    has_missing_receiver_shape = "`&)`" in message or "(&)" in message or "found `&`" in message
    return has_receiver_hint and ("expected" in message or has_missing_receiver_shape)


def _infer_diagnostic_language(diagnostic: RepairDiagnostic) -> str:
    code = diagnostic.code.lower()
    path = str(diagnostic.path or "").lower()
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    if code.startswith("typescript_") or path.endswith((".ts", ".tsx")):
        return "typescript"
    if code.startswith("rust_") or path.endswith(".rs") or "rust" in message or ".rs" in message:
        return "rust"
    if code.startswith("go_") or path.endswith(".go"):
        return "go"
    if code.startswith("python_") or path.endswith(".py"):
        return "python"
    return "unknown"


def _infer_diagnostic_phase(diagnostic: RepairDiagnostic, archetype: str) -> str:
    if archetype == RepairArchetype.MISSING_DEPENDENCY.value:
        return "dependency_resolution"
    if archetype == RepairArchetype.WRONG_IMPORT_PATH.value:
        return "quality_repair"
    if archetype == RepairArchetype.NULLABLE_TYPE_MISMATCH.value:
        return "quality_repair"
    if archetype == RepairArchetype.OBJECT_LITERAL_SYNTAX.value:
        return "quality_repair"
    if archetype == RepairArchetype.MISSING_METHOD_SELF.value:
        return "quality_repair"
    if diagnostic.code == "declared_target_missing":
        return "target_contract"
    return "unknown"


__all__ = [
    "RepairArchetype",
    "RepairCoverageReport",
    "RepairDiagnosticCoverage",
    "RepairRuleDefinition",
    "RepairRuleRegistry",
    "build_repair_coverage_report",
    "default_repair_rule_registry",
]
