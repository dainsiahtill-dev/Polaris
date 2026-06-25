"""Rule registry and diagnostic coverage reporting for Director repairs."""

from __future__ import annotations

import re
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


_AMBIGUOUS_REPAIR_LANGUAGE_EXTENSIONS = frozenset((".m",))


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
        language="html",
        file_extensions=(".html", ".htm"),
        diagnostic_sources=("html", "vite"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.OBJECT_LITERAL_SYNTAX),
        notes="Reserved markup/runtime entrypoint slot for script module repairs.",
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
    RepairLanguageSlot(
        language="vue",
        aliases=("vue_sfc",),
        file_extensions=(".vue",),
        diagnostic_sources=("vue-tsc", "vue compiler", "vite"),
        preferred_archetypes=(
            RepairArchetype.OBJECT_LITERAL_SYNTAX,
            RepairArchetype.WRONG_IMPORT_PATH,
            RepairArchetype.NULLABLE_TYPE_MISMATCH,
            RepairArchetype.MISSING_DEPENDENCY,
        ),
        notes="Reserved single-file component slot; add bench-proven framework adapters before execution.",
    ),
    RepairLanguageSlot(
        language="svelte",
        file_extensions=(".svelte",),
        diagnostic_sources=("svelte-check", "svelte compiler", "vite"),
        preferred_archetypes=(
            RepairArchetype.OBJECT_LITERAL_SYNTAX,
            RepairArchetype.WRONG_IMPORT_PATH,
            RepairArchetype.NULLABLE_TYPE_MISMATCH,
            RepairArchetype.MISSING_DEPENDENCY,
        ),
        notes="Reserved single-file component slot; no authoritative rules until bench evidence exists.",
    ),
    RepairLanguageSlot(
        language="scala",
        file_extensions=(".scala", ".sc"),
        diagnostic_sources=("scalac", "sbt", "scala-cli"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved JVM language slot for future import/package/dependency repairs.",
    ),
    RepairLanguageSlot(
        language="groovy",
        file_extensions=(".groovy", ".gradle"),
        diagnostic_sources=("groovyc", "gradle"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved JVM scripting/build slot; Gradle repairs must remain receipt-backed.",
    ),
    RepairLanguageSlot(
        language="elixir",
        aliases=("ex", "exs"),
        file_extensions=(".ex", ".exs"),
        diagnostic_sources=("elixir", "mix compile", "mix test"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved BEAM language slot for module alias and dependency repairs.",
    ),
    RepairLanguageSlot(
        language="erlang",
        aliases=("erl",),
        file_extensions=(".erl", ".hrl"),
        diagnostic_sources=("erlc", "rebar3", "eunit"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved BEAM language slot for include/module dependency repairs.",
    ),
    RepairLanguageSlot(
        language="haskell",
        aliases=("hs",),
        file_extensions=(".hs", ".lhs"),
        diagnostic_sources=("ghc", "cabal", "stack test"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved functional language slot for module/import/package repairs.",
    ),
    RepairLanguageSlot(
        language="ocaml",
        aliases=("ml", "mli"),
        file_extensions=(".ml", ".mli"),
        diagnostic_sources=("ocamlc", "dune", "opam"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved functional language slot for module and package repairs.",
    ),
    RepairLanguageSlot(
        language="fsharp",
        aliases=("f#",),
        file_extensions=(".fs", ".fsi", ".fsx"),
        diagnostic_sources=("dotnet fsi", "fsc", "msbuild"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved .NET language slot for namespace/package repairs.",
    ),
    RepairLanguageSlot(
        language="zig",
        file_extensions=(".zig",),
        diagnostic_sources=("zig build", "zig test"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved systems language slot for import/build dependency repairs.",
    ),
    RepairLanguageSlot(
        language="nim",
        file_extensions=(".nim", ".nims"),
        diagnostic_sources=("nim c", "nimble"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved systems/scripting language slot for import/package repairs.",
    ),
    RepairLanguageSlot(
        language="crystal",
        aliases=("cr",),
        file_extensions=(".cr",),
        diagnostic_sources=("crystal build", "shards"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved compiled scripting language slot for require/shard repairs.",
    ),
    RepairLanguageSlot(
        language="perl",
        aliases=("pl", "pm"),
        file_extensions=(".pl", ".pm", ".t"),
        diagnostic_sources=("perl", "prove", "cpanm"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved scripting language slot for module/use dependency repairs.",
    ),
    RepairLanguageSlot(
        language="powershell",
        aliases=("pwsh", "ps1"),
        file_extensions=(".ps1", ".psm1", ".psd1"),
        diagnostic_sources=("powershell", "pwsh", "pester"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.OBJECT_LITERAL_SYNTAX),
        notes="Reserved shell scripting slot; fixes must be verifier-backed and platform-safe.",
    ),
    RepairLanguageSlot(
        language="julia",
        aliases=("jl",),
        file_extensions=(".jl",),
        diagnostic_sources=("julia", "pkg.test"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved scientific scripting slot for using/import/package repairs.",
    ),
    RepairLanguageSlot(
        language="objective_c",
        aliases=("objc", "objective-c"),
        file_extensions=(".m", ".mm"),
        diagnostic_sources=("clang", "objective-c", "xcodebuild"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved Apple native slot; distinguish from C/C++ by file extension and diagnostics.",
    ),
    RepairLanguageSlot(
        language="fortran",
        aliases=("f90", "f95"),
        file_extensions=(".f", ".for", ".f90", ".f95", ".f03", ".f08"),
        diagnostic_sources=("gfortran", "flang", "fpm test"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved scientific compiled language slot for module/use repairs.",
    ),
    RepairLanguageSlot(
        language="matlab",
        aliases=("octave",),
        file_extensions=(".m",),
        diagnostic_sources=("matlab", "octave"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.OBJECT_LITERAL_SYNTAX),
        notes="Reserved scientific scripting slot; Objective-C .m ambiguity must be resolved by diagnostics.",
    ),
    RepairLanguageSlot(
        language="terraform",
        aliases=("hcl",),
        file_extensions=(".tf", ".tfvars", ".hcl"),
        diagnostic_sources=("terraform validate", "terraform plan", "hcl"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.OBJECT_LITERAL_SYNTAX),
        notes="Reserved infrastructure DSL slot; not a production repair path without policy-gated receipts.",
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
    runtime_plan_available: bool = False
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
        object.__setattr__(self, "runtime_plan_available", bool(self.runtime_plan_available))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if not deterministic_repair_source_tool_known(self.source_tool):
            raise ValueError(f"unregistered repair source_tool: {self.source_tool}")

    def matches(self, diagnostic: RepairDiagnostic) -> bool:
        """Return whether this rule claims coverage for a diagnostic."""

        diagnostic_language = _infer_diagnostic_language(diagnostic)
        if self.language not in {"generic", "unknown"} and diagnostic_language not in {"unknown", self.language}:
            return False
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
            "runtime_plan_available": self.runtime_plan_available,
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

    @property
    def executable_runtime_plan_matched(self) -> bool:
        return any(rule.runtime_plan_available for rule in self.matched_rules)

    @property
    def metadata_only_match(self) -> bool:
        return self.known_rule_matched and not self.executable_runtime_plan_matched

    def to_dict(self) -> dict[str, Any]:
        diagnostic_archetype = _suggest_rule_family(self.diagnostic)
        return {
            "diagnostic": self.diagnostic.to_dict(),
            "known_rule_matched": self.known_rule_matched,
            "executable_runtime_plan_matched": self.executable_runtime_plan_matched,
            "metadata_only_match": self.metadata_only_match,
            "matched_rule_ids": [rule.rule_id for rule in self.matched_rules],
            "matched_source_tools": [rule.source_tool for rule in self.matched_rules],
            "runtime_plan_rule_ids": [rule.rule_id for rule in self.matched_rules if rule.runtime_plan_available],
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

    @property
    def executable_runtime_plan_diagnostic_count(self) -> int:
        return sum(1 for item in self.items if item.executable_runtime_plan_matched)

    @property
    def metadata_only_diagnostic_count(self) -> int:
        return sum(1 for item in self.items if item.metadata_only_match)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_diagnostics": self.total_diagnostics,
            "covered_diagnostic_count": self.covered_diagnostic_count,
            "uncovered_diagnostic_count": self.uncovered_diagnostic_count,
            "executable_runtime_plan_diagnostic_count": self.executable_runtime_plan_diagnostic_count,
            "metadata_only_diagnostic_count": self.metadata_only_diagnostic_count,
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
                rule_id="cpp.header_not_found",
                source_tool="deterministic_cpp_post_repair",
                language="cpp",
                phase="post_materialization",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("cpp_compile_error",),
                message_terms=("file not found",),
                risk_level="medium",
                description="Covers C++ post-pass repairs for missing include/header paths.",
            ),
            RepairRuleDefinition(
                rule_id="cpp.no_such_file_or_directory",
                source_tool="deterministic_cpp_post_repair",
                language="cpp",
                phase="post_materialization",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("cpp_compile_error",),
                message_terms=("no such file",),
                risk_level="medium",
                description="Covers C++ post-pass repairs for include paths reported by GCC-style diagnostics.",
            ),
            RepairRuleDefinition(
                rule_id="html.typescript_module_script",
                source_tool="deterministic_html_typescript_module_script_repair",
                language="html",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                raw_terms=("typescript", "module script"),
                risk_level="low",
                description="Covers HTML entrypoint script module adjustments for TypeScript-generated projects.",
            ),
            RepairRuleDefinition(
                rule_id="java.cannot_find_symbol",
                source_tool="deterministic_java_post_repair",
                language="java",
                phase="post_materialization",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("java_compile_error",),
                message_terms=("cannot find symbol",),
                risk_level="medium",
                description="Covers Java post-pass repairs for unresolved symbols after generation.",
            ),
            RepairRuleDefinition(
                rule_id="java.package_does_not_exist",
                source_tool="deterministic_java_post_repair",
                language="java",
                phase="post_materialization",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("java_compile_error",),
                message_terms=("package", "does not exist"),
                risk_level="medium",
                description="Covers Java post-pass repairs for unresolved package/import diagnostics.",
            ),
            RepairRuleDefinition(
                rule_id="javascript.commonjs_esm_entrypoint",
                source_tool="deterministic_javascript_esm_commonjs_entrypoint_repair",
                language="javascript",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("javascript_module_error",),
                message_terms=("require is not defined",),
                risk_level="medium",
                description="Covers generated JavaScript entrypoints that mix CommonJS with ESM package mode.",
            ),
            RepairRuleDefinition(
                rule_id="javascript.cannot_find_module",
                source_tool="deterministic_node_test_script_contract_repair",
                language="javascript",
                phase="test_contract",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("javascript_module_error",),
                message_terms=("cannot find module",),
                risk_level="medium",
                description="Covers Node test/entrypoint module path contract repairs.",
            ),
            RepairRuleDefinition(
                rule_id="javascript.missing_named_export",
                source_tool="deterministic_javascript_missing_export_repair",
                language="javascript",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("javascript_module_error",),
                message_terms=("does not provide an export named",),
                risk_level="low",
                description="Covers JavaScript missing named export repair metadata.",
            ),
            RepairRuleDefinition(
                rule_id="python.module_not_found",
                source_tool="deterministic_python_package_shadow_bridge_repair",
                language="python",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("python_modulenotfounderror",),
                message_terms=("no module named",),
                risk_level="medium",
                description="Covers Python package/module import bridge repairs.",
            ),
            RepairRuleDefinition(
                rule_id="python.runtime_attribute_error",
                source_tool="deterministic_python_unittest_runtime_failure_repair",
                language="python",
                phase="runtime_smoke",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("python_attributeerror",),
                risk_level="medium",
                description="Covers Python runtime smoke repairs for generated API/member mismatches.",
            ),
            RepairRuleDefinition(
                rule_id="python.runtime_smoke_failure",
                source_tool="deterministic_python_unittest_runtime_failure_repair",
                language="python",
                phase="runtime_smoke",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("python_runtime_smoke_failed",),
                risk_level="medium",
                description="Covers Python runtime smoke verifier failures after generation.",
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
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="typescript.missing_closing_brace",
                source_tool="deterministic_typescript_missing_closing_brace_repair",
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("typescript_ts1005",),
                message_terms=("}", "expected"),
                risk_level="low",
                description="Covers missing closing brace syntax repairs reported as TS1005.",
            ),
            RepairRuleDefinition(
                rule_id="typescript.missing_export",
                source_tool="deterministic_typescript_missing_export_repair",
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("typescript_ts2305",),
                message_terms=("no exported member",),
                risk_level="low",
                description="Covers TypeScript missing export repairs for generated modules.",
            ),
            RepairRuleDefinition(
                rule_id="typescript.nullable_canvas_context",
                source_tool="deterministic_typescript_nullable_canvas_context_repair",
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.NULLABLE_TYPE_MISMATCH,
                priority=1,
                diagnostic_codes=("typescript_ts2345",),
                message_terms=("null",),
                risk_level="low",
                description="Covers nullable canvas/context argument repairs.",
            ),
            RepairRuleDefinition(
                rule_id="typescript.too_few_arguments",
                source_tool="deterministic_typescript_too_few_arguments_repair",
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.NULLABLE_TYPE_MISMATCH,
                priority=1,
                diagnostic_codes=("typescript_ts2554",),
                message_terms=("expected", "arguments"),
                risk_level="low",
                description="Covers generated TypeScript calls with too few arguments.",
            ),
            RepairRuleDefinition(
                rule_id="typescript.unresolved_identifier",
                source_tool="deterministic_typescript_unresolved_identifier_repair",
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("typescript_ts2304",),
                message_terms=("cannot find name",),
                risk_level="low",
                description="Covers generated TypeScript unresolved identifier repairs.",
            ),
            RepairRuleDefinition(
                rule_id="typescript.vitest_globals",
                source_tool="deterministic_typescript_vitest_globals_repair",
                language="typescript",
                phase="test_contract",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("typescript_ts2582",),
                message_terms=("cannot find name",),
                risk_level="medium",
                description="Covers Vitest global typing repairs for describe/it/expect diagnostics.",
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
    for slot in repair_language_slots():
        if _slot_path_matches(slot=slot, path=path):
            return slot.language
    for slot in repair_language_slots():
        if _slot_ambiguous_path_matches(slot=slot, path=path) and _slot_diagnostic_source_matches(
            slot=slot, message=message
        ):
            return slot.language
    for slot in repair_language_slots():
        if slot.diagnostic_sources and any(
            _diagnostic_source_matches(message=message, source=source) for source in slot.diagnostic_sources
        ):
            return slot.language
    return ""


def _slot_path_matches(*, slot: RepairLanguageSlot, path: str) -> bool:
    return any(
        extension not in _AMBIGUOUS_REPAIR_LANGUAGE_EXTENSIONS and path.endswith(extension)
        for extension in slot.file_extensions
    )


def _slot_ambiguous_path_matches(*, slot: RepairLanguageSlot, path: str) -> bool:
    return any(
        extension in _AMBIGUOUS_REPAIR_LANGUAGE_EXTENSIONS and path.endswith(extension)
        for extension in slot.file_extensions
    )


def _slot_diagnostic_source_matches(*, slot: RepairLanguageSlot, message: str) -> bool:
    return any(_diagnostic_source_matches(message=message, source=source) for source in slot.diagnostic_sources)


def _diagnostic_source_matches(*, message: str, source: str) -> bool:
    normalized_source = str(source or "").strip().lower()
    if len(normalized_source) <= 2:
        return False
    return re.search(rf"(?<![a-z0-9_]){re.escape(normalized_source)}(?![a-z0-9_])", message) is not None


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
    "RepairLanguageSlot",
    "RepairRuleDefinition",
    "RepairRuleRegistry",
    "build_repair_coverage_report",
    "default_repair_rule_registry",
    "repair_language_slots",
]
