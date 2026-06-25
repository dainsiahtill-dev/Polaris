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


def _slot_non_empty(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("repair language slot field must be non-empty")
    return normalized


def _slot_tuple_str(value: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or ()) if str(item or "").strip())


@dataclass(frozen=True)
class RepairLanguageSlot:
    """Reserved language extension slot for future deterministic repair rules."""

    language: str
    aliases: tuple[str, ...] = ()
    file_extensions: tuple[str, ...] = ()
    diagnostic_sources: tuple[str, ...] = ()
    preferred_archetypes: tuple[RepairArchetype, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "language", _slot_non_empty(self.language).lower())
        object.__setattr__(self, "aliases", tuple(alias.lower() for alias in _slot_tuple_str(self.aliases)))
        object.__setattr__(
            self,
            "file_extensions",
            tuple(extension.lower() for extension in _slot_tuple_str(self.file_extensions)),
        )
        object.__setattr__(
            self,
            "diagnostic_sources",
            tuple(source.lower() for source in _slot_tuple_str(self.diagnostic_sources)),
        )
        object.__setattr__(self, "preferred_archetypes", tuple(self.preferred_archetypes or ()))
        object.__setattr__(self, "notes", str(self.notes or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "aliases": list(self.aliases),
            "file_extensions": list(self.file_extensions),
            "diagnostic_sources": list(self.diagnostic_sources),
            "preferred_archetypes": [archetype.value for archetype in self.preferred_archetypes],
            "notes": self.notes,
        }


_DEFAULT_REPAIR_LANGUAGE_SLOTS: tuple[RepairLanguageSlot, ...] = (
    RepairLanguageSlot(
        language="typescript",
        aliases=("ts", "tsx"),
        file_extensions=(".ts", ".tsx"),
        diagnostic_sources=("tsc", "vite", "vitest"),
        preferred_archetypes=(
            RepairArchetype.OBJECT_LITERAL_SYNTAX,
            RepairArchetype.WRONG_IMPORT_PATH,
            RepairArchetype.NULLABLE_TYPE_MISMATCH,
            RepairArchetype.MISSING_DEPENDENCY,
        ),
    ),
    RepairLanguageSlot(
        language="javascript",
        aliases=("js", "jsx", "node"),
        file_extensions=(".js", ".jsx", ".mjs", ".cjs"),
        diagnostic_sources=("node", "npm", "vitest", "jest"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="go",
        file_extensions=(".go",),
        diagnostic_sources=("go", "go test", "go vet"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="rust",
        aliases=("rs", "cargo"),
        file_extensions=(".rs",),
        diagnostic_sources=("rustc", "cargo check", "cargo test"),
        preferred_archetypes=(
            RepairArchetype.WRONG_IMPORT_PATH,
            RepairArchetype.MISSING_DEPENDENCY,
            RepairArchetype.INCOMPATIBLE_DERIVE,
            RepairArchetype.MISSING_METHOD_SELF,
        ),
    ),
    RepairLanguageSlot(
        language="cpp",
        aliases=("c++", "cc", "cxx"),
        file_extensions=(".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"),
        diagnostic_sources=("clang", "gcc", "cmake", "ctest"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="c",
        file_extensions=(".c", ".h"),
        diagnostic_sources=("clang", "gcc", "cmake", "make"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="java",
        file_extensions=(".java",),
        diagnostic_sources=("javac", "maven", "gradle", "junit"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="python",
        aliases=("py",),
        file_extensions=(".py",),
        diagnostic_sources=("pytest", "python", "mypy", "ruff"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="shell",
        aliases=("bash", "sh", "zsh"),
        file_extensions=(".sh", ".bash", ".zsh"),
        diagnostic_sources=("bash", "shellcheck"),
        preferred_archetypes=(RepairArchetype.OBJECT_LITERAL_SYNTAX, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="sql",
        file_extensions=(".sql",),
        diagnostic_sources=("sqlite", "postgres", "mysql"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.OBJECT_LITERAL_SYNTAX),
    ),
    RepairLanguageSlot(
        language="csharp",
        aliases=("c#", "dotnet"),
        file_extensions=(".cs",),
        diagnostic_sources=("dotnet", "msbuild", "xunit"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="php",
        file_extensions=(".php",),
        diagnostic_sources=("php", "composer", "phpunit"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="ruby",
        file_extensions=(".rb",),
        diagnostic_sources=("ruby", "bundler", "rspec"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="swift",
        file_extensions=(".swift",),
        diagnostic_sources=("swift", "xcodebuild"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="kotlin",
        file_extensions=(".kt", ".kts"),
        diagnostic_sources=("kotlinc", "gradle"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="dart",
        file_extensions=(".dart",),
        diagnostic_sources=("dart", "flutter"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
    RepairLanguageSlot(
        language="lua",
        file_extensions=(".lua",),
        diagnostic_sources=("lua", "luacheck"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.OBJECT_LITERAL_SYNTAX),
    ),
    RepairLanguageSlot(
        language="r",
        file_extensions=(".r", ".R"),
        diagnostic_sources=("rscript", "testthat"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
    ),
)


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
    raw_terms: tuple[str, ...] = ()
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
        object.__setattr__(self, "raw_terms", tuple(term.lower() for term in _tuple_str(self.raw_terms)))
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
            if not all(term in haystack for term in self.message_terms):
                return False
        if self.raw_terms:
            raw_haystack = (diagnostic.raw or "").lower()
            if not all(term in raw_haystack for term in self.raw_terms):
                return False
        return bool(self.diagnostic_codes or self.message_terms or self.raw_terms)

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
            "raw_terms": list(self.raw_terms),
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
                rule_id="go.bare_import_string",
                source_tool="deterministic_go_bare_import_string_repair",
                language="go",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=0,
                diagnostic_codes=("go_compile_error",),
                message_terms=("import path must be string",),
                risk_level="low",
                description="Repairs generated Go imports missing quoted import paths.",
            ),
            RepairRuleDefinition(
                rule_id="go.module_import_path",
                source_tool="deterministic_go_module_import_repair",
                language="go",
                phase="dependency_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=0,
                diagnostic_codes=("go_compile_error",),
                message_terms=("no required module",),
                risk_level="medium",
                description="Repairs Go module import paths that should point at local project packages.",
            ),
            RepairRuleDefinition(
                rule_id="rust.incompatible_derive",
                source_tool="deterministic_rust_derive_repair",
                language="rust",
                phase="code_repair",
                archetype=RepairArchetype.INCOMPATIBLE_DERIVE,
                priority=1,
                diagnostic_codes=("rust_e0277",),
                risk_level="medium",
                description="Repairs Rust derive mismatches such as serde derives or invalid Copy/Eq derives.",
            ),
            RepairRuleDefinition(
                rule_id="rust.method_self_signature",
                source_tool="deterministic_rust_post_repair",
                language="rust",
                phase="code_repair",
                archetype=RepairArchetype.MISSING_METHOD_SELF,
                priority=1,
                raw_terms=("&self", "found `&`"),
                risk_level="low",
                description="Repairs generated Rust method receiver signatures such as `(&)` to `(&self)`.",
            ),
            RepairRuleDefinition(
                rule_id="rust.unlinked_crate_dependency",
                source_tool="deterministic_rust_dependency_repair",
                language="rust",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=0,
                diagnostic_codes=("rust_e0433",),
                message_terms=("unresolved module", "unlinked crate"),
                risk_level="medium",
                description="Repairs missing Rust dependency declarations for known crates.",
            ),
            RepairRuleDefinition(
                rule_id="rust.unresolved_import_path",
                source_tool="deterministic_rust_crate_import_repair",
                language="rust",
                phase="dependency_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=0,
                diagnostic_codes=("rust_e0432",),
                message_terms=("unresolved import",),
                risk_level="medium",
                description="Repairs Rust crate/module import path mismatches.",
            ),
            RepairRuleDefinition(
                rule_id="rust.unresolved_pub_use",
                source_tool="deterministic_rust_unresolved_pub_use_repair",
                language="rust",
                phase="export_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=2,
                depends_on=("rust.unresolved_import_path",),
                diagnostic_codes=("rust_e0432",),
                raw_terms=("no", "in the root"),
                risk_level="medium",
                description="Repairs stale Rust public re-exports after module generation.",
            ),
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


def repair_language_slots() -> tuple[RepairLanguageSlot, ...]:
    """Return reserved language extension slots for future repair rules."""

    return _DEFAULT_REPAIR_LANGUAGE_SLOTS


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
    if code == "rust_e0277" or "derive" in message:
        return RepairArchetype.INCOMPATIBLE_DERIVE.value
    if code == "rust_e0433" or "unlinked crate" in message or "no required module" in message:
        return RepairArchetype.MISSING_DEPENDENCY.value
    if language == "rust" and _looks_like_rust_missing_method_self(message):
        return RepairArchetype.MISSING_METHOD_SELF.value
    if "import" in message or "unresolved" in message:
        return RepairArchetype.WRONG_IMPORT_PATH.value
    if "null" in message or "undefined" in message:
        return RepairArchetype.NULLABLE_TYPE_MISMATCH.value
    if "dependency" in message or "crate" in message or "module" in message:
        return RepairArchetype.MISSING_DEPENDENCY.value
    if code.startswith("typescript_ts") or (language == "typescript" and "expected" in message):
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
    slot_language = _infer_language_from_slots(code=code, path=path, message=message)
    if slot_language:
        return slot_language
    if code.startswith("typescript_") or path.endswith((".ts", ".tsx")):
        return "typescript"
    if code.startswith("rust_") or path.endswith(".rs") or "rust" in message or ".rs" in message:
        return "rust"
    if code.startswith("go_") or path.endswith(".go"):
        return "go"
    if code.startswith("python_") or path.endswith(".py"):
        return "python"
    return "unknown"


def _infer_language_from_slots(*, code: str, path: str, message: str) -> str:
    for slot in repair_language_slots():
        code_prefixes = (f"{slot.language}_", *(f"{alias}_" for alias in slot.aliases))
        if any(code.startswith(prefix) for prefix in code_prefixes):
            return slot.language
        if slot.file_extensions and path.endswith(slot.file_extensions):
            return slot.language
        if slot.diagnostic_sources and any(len(source) > 2 and source in message for source in slot.diagnostic_sources):
            return slot.language
    return ""


def _infer_diagnostic_phase(diagnostic: RepairDiagnostic, archetype: str) -> str:
    if archetype == RepairArchetype.MISSING_DEPENDENCY.value:
        return "dependency_resolution"
    if archetype == RepairArchetype.INCOMPATIBLE_DERIVE.value:
        return "code_repair"
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
