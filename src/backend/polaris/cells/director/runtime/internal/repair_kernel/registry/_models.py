"""Rule registry and diagnostic coverage reporting for Director repairs."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from polaris.cells.control_plane.run_ledger.public import TaskBoundaryFailureClassV1

from ..contracts import RepairDiagnostic
from ..java_syntax import JAVA_TEST_DEPENDENCY_SOURCE_TOOL
from ..rust_syntax import (
    RUST_CRATE_IMPORT_REWRITE_SOURCE_TOOL,
    RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
    RUST_FIELD_RENAME_SUGGESTION_SOURCE_TOOL,
    RUST_INCOMPATIBLE_COPY_DERIVE_SOURCE_TOOL,
    RUST_LINE_SUGGESTION_SOURCE_TOOL,
    RUST_METHOD_SELF_SIGNATURE_SOURCE_TOOL,
    RUST_MISSING_BINARY_ENTRYPOINT_SOURCE_TOOL,
    RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
    RUST_MISSING_TRAIT_DERIVE_SOURCE_TOOL,
    RUST_POST_SOURCE_TOOL,
    RUST_SERDE_DERIVE_SOURCE_TOOL,
    RUST_TRAIT_IMPORT_SOURCE_TOOL,
    RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL,
    RUST_UNUSED_IMPORT_SOURCE_TOOL,
    RUST_WRONG_CRATE_PATH_SOURCE_TOOL,
)
from ..strategy_catalog import deterministic_repair_source_tool_known
from ..typescript_syntax import (
    HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
    JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL,
    TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL,
    TYPESCRIPT_ARG_TYPE_FUNCTION_ALIAS_SOURCE_TOOL,
    TYPESCRIPT_ARGUMENT_SHAPE_ADAPTER_SOURCE_TOOL,
    TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL,
    TYPESCRIPT_CONFIG_KEY_SPLIT_SOURCE_TOOL,
    TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL,
    TYPESCRIPT_DUPLICATE_FUNCTION_SOURCE_TOOL,
    TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL,
    TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL,
    TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL,
    TYPESCRIPT_EXPORT_AMBIGUITY_SOURCE_TOOL,
    TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL,
    TYPESCRIPT_IDENTIFIER_SUGGESTION_SOURCE_TOOL,
    TYPESCRIPT_IMPLICIT_RETURN_TYPE_SOURCE_TOOL,
    TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL,
    TYPESCRIPT_INIT_PROPERTY_ALIAS_SOURCE_TOOL,
    TYPESCRIPT_INVALID_MODULE_AUGMENTATION_SOURCE_TOOL,
    TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL,
    TYPESCRIPT_LITERAL_UNION_EXPAND_SOURCE_TOOL,
    TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
    TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
    TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
    TYPESCRIPT_MISSING_RELATIVE_MODULE_SOURCE_TOOL,
    TYPESCRIPT_OBJECT_ASSIGN_ASSERTION_SOURCE_TOOL,
    TYPESCRIPT_OBJECT_LITERAL_MISSING_PROPS_SOURCE_TOOL,
    TYPESCRIPT_PARAM_OBJECT_PROPERTY_SOURCE_TOOL,
    TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL,
    TYPESCRIPT_PRIVATE_PROPERTY_ACCESS_SOURCE_TOOL,
    TYPESCRIPT_READONLY_ARRAY_MUTATION_SOURCE_TOOL,
    TYPESCRIPT_REEXPORT_SOURCE_TOOL,
    TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,
    TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL,
    TYPESCRIPT_SCAFFOLD_SOURCE_TOOL,
    TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL,
    TYPESCRIPT_TEST_BLOCK_RESIDUE_SOURCE_TOOL,
    TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL,
    TYPESCRIPT_TRUNCATED_EOF_SOURCE_TOOL,
    TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL,
    TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL,
    TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL,
    TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
    TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL,
    TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL,
    TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
    TYPESCRIPT_UNUSED_LOCAL_SOURCE_TOOL,
    TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL,
    TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL,
    TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL,
)

RUST_MISSING_FIELDS_SOURCE_TOOL = "deterministic_rust_missing_fields_repair"
RUST_MISSING_LIB_TARGET_SOURCE_TOOL = "deterministic_rust_missing_lib_target_repair"
RUST_LIB_ROOT_FACADE_SOURCE_TOOL = "deterministic_rust_lib_root_facade_repair"
RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL = "deterministic_rust_struct_literal_missing_field_repair"


class RepairArchetype(str, Enum):
    """Language-independent repair pattern family."""

    INCOMPATIBLE_DERIVE = "incompatible_derive"
    MISSING_DEPENDENCY = "missing_dependency"
    MISSING_METHOD_SELF = "missing_method_self"
    NULLABLE_TYPE_MISMATCH = "nullable_type_mismatch"
    OBJECT_LITERAL_SYNTAX = "object_literal_syntax"
    INVALID_IDENTIFIER = "invalid_identifier"
    GENERATED_RESIDUE = "generated_residue"
    MISSING_DECLARED_TARGET = "missing_declared_target"
    WRONG_IMPORT_PATH = "wrong_import_path"
    RUNTIME_CONTRACT = "runtime_contract"


def _slot_non_empty(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("repair language slot field must be non-empty")
    return normalized


def _slot_tuple_str(value: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or ()) if str(item or "").strip())


_AMBIGUOUS_REPAIR_LANGUAGE_EXTENSIONS = frozenset((".m",))
_REPAIR_LANGUAGE_SLOT_MODULE_PREFIX = "polaris.cells.director.runtime.internal.repair_kernel"
_REPAIR_LANGUAGE_SLOT_REGISTRATION_POLICY = "bench_verified_rule_required"
_RUNTIME_MIGRATION_SOURCE_TOOLS = frozenset(
    {
        JAVA_TEST_DEPENDENCY_SOURCE_TOOL,
        RUST_CRATE_IMPORT_REWRITE_SOURCE_TOOL,
        RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
        RUST_FIELD_RENAME_SUGGESTION_SOURCE_TOOL,
        RUST_INCOMPATIBLE_COPY_DERIVE_SOURCE_TOOL,
        RUST_LINE_SUGGESTION_SOURCE_TOOL,
        RUST_METHOD_SELF_SIGNATURE_SOURCE_TOOL,
        RUST_MISSING_BINARY_ENTRYPOINT_SOURCE_TOOL,
        RUST_MISSING_FIELDS_SOURCE_TOOL,
        RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
        RUST_MISSING_TRAIT_DERIVE_SOURCE_TOOL,
        RUST_POST_SOURCE_TOOL,
        RUST_SERDE_DERIVE_SOURCE_TOOL,
        RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL,
        RUST_TRAIT_IMPORT_SOURCE_TOOL,
        RUST_UNUSED_IMPORT_SOURCE_TOOL,
        RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL,
        RUST_WRONG_CRATE_PATH_SOURCE_TOOL,
        HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
        JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL,
        TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL,
        TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL,
        TYPESCRIPT_CONFIG_KEY_SPLIT_SOURCE_TOOL,
        TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL,
        TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL,
        TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL,
        TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL,
        TYPESCRIPT_EXPORT_AMBIGUITY_SOURCE_TOOL,
        TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL,
        TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL,
        TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
        TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
        TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
        TYPESCRIPT_MISSING_RELATIVE_MODULE_SOURCE_TOOL,
        TYPESCRIPT_INVALID_MODULE_AUGMENTATION_SOURCE_TOOL,
        TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL,
        TYPESCRIPT_PRIVATE_PROPERTY_ACCESS_SOURCE_TOOL,
        TYPESCRIPT_DUPLICATE_FUNCTION_SOURCE_TOOL,
        TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL,
        TYPESCRIPT_IMPLICIT_RETURN_TYPE_SOURCE_TOOL,
        TYPESCRIPT_OBJECT_ASSIGN_ASSERTION_SOURCE_TOOL,
        TYPESCRIPT_READONLY_ARRAY_MUTATION_SOURCE_TOOL,
        TYPESCRIPT_PARAM_OBJECT_PROPERTY_SOURCE_TOOL,
        TYPESCRIPT_TRUNCATED_EOF_SOURCE_TOOL,
        TYPESCRIPT_OBJECT_LITERAL_MISSING_PROPS_SOURCE_TOOL,
        TYPESCRIPT_IDENTIFIER_SUGGESTION_SOURCE_TOOL,
        TYPESCRIPT_ARGUMENT_SHAPE_ADAPTER_SOURCE_TOOL,
        TYPESCRIPT_UNUSED_LOCAL_SOURCE_TOOL,
        TYPESCRIPT_LITERAL_UNION_EXPAND_SOURCE_TOOL,
        TYPESCRIPT_INIT_PROPERTY_ALIAS_SOURCE_TOOL,
        TYPESCRIPT_ARG_TYPE_FUNCTION_ALIAS_SOURCE_TOOL,
        TYPESCRIPT_REEXPORT_SOURCE_TOOL,
        TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,
        TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL,
        TYPESCRIPT_SCAFFOLD_SOURCE_TOOL,
        TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL,
        TYPESCRIPT_TEST_BLOCK_RESIDUE_SOURCE_TOOL,
        TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL,
        TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL,
        TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL,
        TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL,
        TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL,
        TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
        TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL,
        TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL,
        TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
        TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL,
        TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL,
    }
)


def _default_repairer_module(language: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", language.lower()).strip("_")
    return f"{_REPAIR_LANGUAGE_SLOT_MODULE_PREFIX}.{normalized}_runtime"


@dataclass(frozen=True)
class RepairLanguageSlot:
    """Reserved language extension slot for future deterministic repair rules."""

    language: str
    aliases: tuple[str, ...] = ()
    file_extensions: tuple[str, ...] = ()
    file_names: tuple[str, ...] = ()
    diagnostic_sources: tuple[str, ...] = ()
    preferred_archetypes: tuple[RepairArchetype, ...] = ()
    notes: str = ""
    repairer_module: str = ""
    registration_policy: str = _REPAIR_LANGUAGE_SLOT_REGISTRATION_POLICY

    def __post_init__(self) -> None:
        object.__setattr__(self, "language", _slot_non_empty(self.language).lower())
        object.__setattr__(self, "aliases", tuple(alias.lower() for alias in _slot_tuple_str(self.aliases)))
        object.__setattr__(
            self,
            "file_extensions",
            tuple(extension.lower() for extension in _slot_tuple_str(self.file_extensions)),
        )
        object.__setattr__(self, "file_names", tuple(name.lower() for name in _slot_tuple_str(self.file_names)))
        object.__setattr__(
            self,
            "diagnostic_sources",
            tuple(source.lower() for source in _slot_tuple_str(self.diagnostic_sources)),
        )
        object.__setattr__(self, "preferred_archetypes", tuple(self.preferred_archetypes or ()))
        object.__setattr__(self, "notes", str(self.notes or "").strip())
        object.__setattr__(
            self,
            "repairer_module",
            str(self.repairer_module or _default_repairer_module(self.language)).strip(),
        )
        object.__setattr__(
            self,
            "registration_policy",
            str(self.registration_policy or _REPAIR_LANGUAGE_SLOT_REGISTRATION_POLICY).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "aliases": list(self.aliases),
            "file_extensions": list(self.file_extensions),
            "file_names": list(self.file_names),
            "diagnostic_sources": list(self.diagnostic_sources),
            "preferred_archetypes": [archetype.value for archetype in self.preferred_archetypes],
            "notes": self.notes,
            "repairer_module": self.repairer_module,
            "registration_policy": self.registration_policy,
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
        preferred_archetypes=(
            RepairArchetype.WRONG_IMPORT_PATH,
            RepairArchetype.MISSING_DEPENDENCY,
            RepairArchetype.OBJECT_LITERAL_SYNTAX,
        ),
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
    RepairLanguageSlot(
        language="dockerfile",
        aliases=("docker", "containerfile"),
        file_extensions=(".dockerfile",),
        file_names=("Dockerfile", "Containerfile"),
        diagnostic_sources=("docker build", "hadolint"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved container build script slot; keep image/build fixes policy-gated.",
    ),
    RepairLanguageSlot(
        language="make",
        aliases=("makefile",),
        file_extensions=(".mk",),
        file_names=("Makefile", "GNUmakefile"),
        diagnostic_sources=("gmake",),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved build scripting slot for target/dependency repairs.",
    ),
    RepairLanguageSlot(
        language="yaml",
        aliases=("yml",),
        file_extensions=(".yaml", ".yml"),
        diagnostic_sources=("yamllint", "yaml parser"),
        preferred_archetypes=(RepairArchetype.OBJECT_LITERAL_SYNTAX, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved configuration language slot for structured manifest repairs.",
    ),
    RepairLanguageSlot(
        language="json",
        aliases=("jsonc",),
        file_extensions=(".json", ".jsonc"),
        diagnostic_sources=("json parser", "jsonschema"),
        preferred_archetypes=(RepairArchetype.OBJECT_LITERAL_SYNTAX, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved structured manifest slot; use structured JSON operations, never regex rewrites.",
    ),
    RepairLanguageSlot(
        language="toml",
        file_extensions=(".toml",),
        diagnostic_sources=("toml parser",),
        preferred_archetypes=(RepairArchetype.OBJECT_LITERAL_SYNTAX, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved manifest slot for Cargo, Python, and toolchain configuration repairs.",
    ),
    RepairLanguageSlot(
        language="nix",
        file_extensions=(".nix",),
        diagnostic_sources=("nix build", "nix flake", "statix", "deadnix"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved reproducible build DSL slot for import and dependency repairs.",
    ),
    RepairLanguageSlot(
        language="starlark",
        aliases=("bazel", "bzl"),
        file_extensions=(".bzl", ".star"),
        file_names=("BUILD", "BUILD.bazel", "WORKSPACE", "MODULE.bazel"),
        diagnostic_sources=("bazel build", "bazel test", "buildifier"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved Bazel/Starlark slot for load/package/target repairs.",
    ),
    RepairLanguageSlot(
        language="clojure",
        aliases=("clj", "cljs", "cljc"),
        file_extensions=(".clj", ".cljs", ".cljc", ".edn"),
        diagnostic_sources=("clojure", "lein", "clj-kondo"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved Lisp/JVM slot for namespace and dependency repairs.",
    ),
    RepairLanguageSlot(
        language="elm",
        file_extensions=(".elm",),
        diagnostic_sources=("elm make", "elm-test"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved frontend functional language slot for import/package repairs.",
    ),
    RepairLanguageSlot(
        language="rescript",
        aliases=("res", "resi"),
        file_extensions=(".res", ".resi"),
        diagnostic_sources=("rescript",),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved typed frontend language slot for module/import repairs.",
    ),
    RepairLanguageSlot(
        language="gleam",
        file_extensions=(".gleam",),
        diagnostic_sources=("gleam", "gleam test"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved BEAM language slot for import/module/package repairs.",
    ),
    RepairLanguageSlot(
        language="solidity",
        aliases=("sol",),
        file_extensions=(".sol",),
        diagnostic_sources=("solc", "hardhat", "forge test"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved smart contract language slot; security-sensitive fixes need strict receipts.",
    ),
    RepairLanguageSlot(
        language="vyper",
        aliases=("vy",),
        file_extensions=(".vy",),
        diagnostic_sources=("vyper",),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved smart contract language slot; security-sensitive fixes need strict receipts.",
    ),
    RepairLanguageSlot(
        language="qml",
        file_extensions=(".qml",),
        diagnostic_sources=("qml", "qmllint"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.OBJECT_LITERAL_SYNTAX),
        notes="Reserved UI scripting slot for import/module syntax repairs.",
    ),
    RepairLanguageSlot(
        language="proto",
        aliases=("protobuf",),
        file_extensions=(".proto",),
        diagnostic_sources=("protoc", "buf lint", "buf breaking"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.MISSING_DEPENDENCY),
        notes="Reserved schema language slot for import/package repairs.",
    ),
    RepairLanguageSlot(
        language="graphql",
        aliases=("gql",),
        file_extensions=(".graphql", ".gql"),
        diagnostic_sources=("graphql", "graphql-codegen"),
        preferred_archetypes=(RepairArchetype.WRONG_IMPORT_PATH, RepairArchetype.OBJECT_LITERAL_SYNTAX),
        notes="Reserved schema/query language slot for import/schema repairs.",
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
    message_any_terms: tuple[str, ...] = ()
    raw_terms: tuple[str, ...] = ()
    metadata_terms: Mapping[str, str] = field(default_factory=dict)
    excluded_message_terms: tuple[str, ...] = ()
    excluded_raw_terms: tuple[str, ...] = ()
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
        object.__setattr__(
            self, "message_any_terms", tuple(term.lower() for term in _tuple_str(self.message_any_terms))
        )
        object.__setattr__(self, "raw_terms", tuple(term.lower() for term in _tuple_str(self.raw_terms)))
        object.__setattr__(
            self,
            "metadata_terms",
            {
                str(key or "").strip(): str(value or "").strip().casefold()
                for key, value in dict(self.metadata_terms or {}).items()
                if str(key or "").strip() and str(value or "").strip()
            },
        )
        object.__setattr__(
            self,
            "excluded_message_terms",
            tuple(term.lower() for term in _tuple_str(self.excluded_message_terms)),
        )
        object.__setattr__(
            self,
            "excluded_raw_terms",
            tuple(term.lower() for term in _tuple_str(self.excluded_raw_terms)),
        )
        object.__setattr__(self, "risk_level", _non_empty(self.risk_level))
        object.__setattr__(self, "description", str(self.description or "").strip())
        object.__setattr__(self, "runtime_plan_available", bool(self.runtime_plan_available))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if not _repair_source_tool_known(self.source_tool):
            raise ValueError(f"unregistered repair source_tool: {self.source_tool}")

    def matches(self, diagnostic: RepairDiagnostic) -> bool:
        """Return whether this rule claims coverage for a diagnostic."""

        diagnostic_language = _infer_diagnostic_language(diagnostic)
        if self.language not in {"generic", "unknown", "dependency"} and diagnostic_language not in {
            "unknown",
            self.language,
        }:
            return False
        diagnostic_code = diagnostic.code.lower()
        if self.diagnostic_codes and diagnostic_code not in self.diagnostic_codes:
            return False
        if self.message_terms:
            haystack = (diagnostic.message or diagnostic.raw).lower()
            if not all(term in haystack for term in self.message_terms):
                return False
        if self.message_any_terms:
            haystack = (diagnostic.message or diagnostic.raw).lower()
            if not any(term in haystack for term in self.message_any_terms):
                return False
        if self.raw_terms:
            raw_haystack = (diagnostic.raw or "").lower()
            if not all(term in raw_haystack for term in self.raw_terms):
                return False
        if self.metadata_terms:
            diagnostic_metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
            for key, expected in self.metadata_terms.items():
                if str(diagnostic_metadata.get(key) or "").strip().casefold() != expected:
                    return False
        if self.excluded_message_terms:
            message_haystack = (diagnostic.message or diagnostic.raw).lower()
            if any(term in message_haystack for term in self.excluded_message_terms):
                return False
        if self.excluded_raw_terms:
            raw_haystack = (diagnostic.raw or "").lower()
            if any(term in raw_haystack for term in self.excluded_raw_terms):
                return False
        return bool(
            self.diagnostic_codes
            or self.message_terms
            or self.message_any_terms
            or self.raw_terms
            or self.metadata_terms
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rule_id": self.rule_id,
            "source_tool": self.source_tool,
            "language": self.language,
            "phase": self.phase,
            "archetype": self.archetype.value,
            "priority": self.priority,
            "depends_on": list(self.depends_on),
            "diagnostic_codes": list(self.diagnostic_codes),
            "message_terms": list(self.message_terms),
            "message_any_terms": list(self.message_any_terms),
            "raw_terms": list(self.raw_terms),
            "excluded_message_terms": list(self.excluded_message_terms),
            "excluded_raw_terms": list(self.excluded_raw_terms),
            "risk_level": self.risk_level,
            "description": self.description,
            "runtime_plan_available": self.runtime_plan_available,
            "metadata": dict(self.metadata),
        }
        if self.metadata_terms:
            payload["metadata_terms"] = dict(self.metadata_terms)
        return payload


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
        diagnostic_language = _infer_diagnostic_language(self.diagnostic)
        diagnostic_phase = _infer_diagnostic_phase(self.diagnostic, diagnostic_archetype)
        diagnostic_code = str(self.diagnostic.code or "unknown")
        slot = _repair_language_slot_for_language(diagnostic_language)
        runtime_blockers = _runtime_blockers_for_matched_rules(self.matched_rules)
        return {
            "diagnostic": self.diagnostic.to_dict(),
            "known_rule_matched": self.known_rule_matched,
            "executable_runtime_plan_matched": self.executable_runtime_plan_matched,
            "metadata_only_match": self.metadata_only_match,
            "matched_rule_ids": [rule.rule_id for rule in self.matched_rules],
            "matched_source_tools": list(dict.fromkeys(rule.source_tool for rule in self.matched_rules)),
            "runtime_plan_rule_ids": [rule.rule_id for rule in self.matched_rules if rule.runtime_plan_available],
            "archetypes": sorted({rule.archetype.value for rule in self.matched_rules}),
            "phases": sorted({rule.phase for rule in self.matched_rules}),
            "languages": sorted({rule.language for rule in self.matched_rules}),
            "language": diagnostic_language,
            "diagnostic_code": diagnostic_code,
            "diagnostic_archetype": diagnostic_archetype,
            "diagnostic_phase": diagnostic_phase,
            "diagnostic_language": diagnostic_language,
            "archetype_suggestion": diagnostic_archetype,
            "phase_suggestion": diagnostic_phase,
            "suggested_rule_family": diagnostic_archetype,
            "reserved_slot_available": slot is not None,
            "slot_status": _coverage_slot_status(slot),
            "recommended_route": _coverage_recommended_route(
                slot=slot,
                diagnostic_language=diagnostic_language,
                diagnostic_archetype=diagnostic_archetype,
                known_rule_matched=self.known_rule_matched,
                executable_runtime_plan_matched=self.executable_runtime_plan_matched,
                metadata_only_match=self.metadata_only_match,
            ),
            "coverage_status": _coverage_status(self),
            "runtime_blocker_reasons": list(dict.fromkeys(str(blocker["reason"]) for blocker in runtime_blockers)),
            "runtime_blockers": runtime_blockers,
        }


def _runtime_blockers_for_matched_rules(rules: Sequence[RepairRuleDefinition]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for rule in rules:
        if rule.archetype == RepairArchetype.MISSING_DECLARED_TARGET and not rule.runtime_plan_available:
            blockers.append(
                {
                    "reason": "task_boundary_required",
                    "source_tool": rule.source_tool,
                    "rule_id": rule.rule_id,
                    "message": (
                        "Declared target files that were never materialized must be handled "
                        "by TaskBoundary/Director orchestration, not repair-kernel file fabrication."
                    ),
                    "metadata": {
                        "responsible_layer": "task_boundary",
                        "failure_class": TaskBoundaryFailureClassV1.INCOMPLETE_MATERIALIZATION.value,
                        "runtime_executable": False,
                    },
                }
            )
        if (
            rule.source_tool == RUST_MISSING_FIELDS_SOURCE_TOOL
            and rule.rule_id == "rust.missing_struct_field_declaration"
            and not rule.runtime_plan_available
        ):
            blockers.append(
                {
                    "reason": "type_inference_required",
                    "source_tool": rule.source_tool,
                    "rule_id": rule.rule_id,
                    "message": "Rust E0609 does not provide a reliable type for the missing struct field.",
                    "metadata": {
                        "field_type_source": "not_inferred",
                        "type_guessing_allowed": False,
                        "runtime_executable": False,
                    },
                }
            )
    return blockers


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

    @property
    def coverage_gaps(self) -> tuple[dict[str, Any], ...]:
        return tuple(_coverage_gap_payload(item) for item in self.items if not item.known_rule_matched)

    def to_dict(self) -> dict[str, Any]:
        coverage_gaps = self.coverage_gaps
        return {
            "total_diagnostics": self.total_diagnostics,
            "covered_diagnostic_count": self.covered_diagnostic_count,
            "uncovered_diagnostic_count": self.uncovered_diagnostic_count,
            "coverage_gap_count": len(coverage_gaps),
            "rule_discovery_required": bool(coverage_gaps),
            "coverage_gap_languages": sorted(
                {str(gap.get("diagnostic_language") or "unknown") for gap in coverage_gaps}
            ),
            "coverage_gap_archetypes": sorted(
                {str(gap.get("diagnostic_archetype") or "unknown") for gap in coverage_gaps}
            ),
            "coverage_gap_diagnostic_codes": sorted(
                {str(gap.get("diagnostic_code") or "unknown") for gap in coverage_gaps}
            ),
            "coverage_gap_handoff_recommendations": sorted(
                {str(gap.get("handoff_recommendation") or "coverage_triage_required") for gap in coverage_gaps}
            ),
            "coverage_gap_recommended_routes": sorted(
                {str(gap.get("recommended_route") or "llm_repair") for gap in coverage_gaps}
            ),
            "coverage_gap_slot_statuses": sorted(
                {str(gap.get("slot_status") or "reserved_slot_missing") for gap in coverage_gaps}
            ),
            "executable_runtime_plan_diagnostic_count": self.executable_runtime_plan_diagnostic_count,
            "metadata_only_diagnostic_count": self.metadata_only_diagnostic_count,
            "items": [item.to_dict() for item in self.items],
            "uncovered_diagnostics": [item.diagnostic.to_dict() for item in self.items if not item.known_rule_matched],
            "coverage_gaps": list(coverage_gaps),
        }


def _coverage_gap_payload(item: RepairDiagnosticCoverage) -> dict[str, Any]:
    payload = item.to_dict()
    diagnostic = dict(payload["diagnostic"])
    diagnostic_language = str(payload["diagnostic_language"])
    diagnostic_archetype = str(payload["diagnostic_archetype"])
    diagnostic_code = str(diagnostic.get("code") or "unknown")
    slot = _repair_language_slot_for_language(diagnostic_language)
    handoff_recommendation = _coverage_gap_handoff_recommendation(
        slot=slot,
        diagnostic_archetype=diagnostic_archetype,
    )
    recommended_route = _coverage_recommended_route(
        slot=slot,
        diagnostic_language=diagnostic_language,
        diagnostic_archetype=diagnostic_archetype,
        known_rule_matched=False,
        executable_runtime_plan_matched=False,
        metadata_only_match=False,
    )
    return {
        "diagnostic": diagnostic,
        "diagnostic_id": str(diagnostic.get("diagnostic_id") or ""),
        "diagnostic_code": diagnostic_code,
        "known_rule_matched": False,
        "executable_runtime_plan_matched": False,
        "metadata_only_match": False,
        "language": diagnostic_language,
        "diagnostic_language": diagnostic_language,
        "diagnostic_phase": str(payload["diagnostic_phase"]),
        "diagnostic_archetype": diagnostic_archetype,
        "phase_suggestion": str(payload["diagnostic_phase"]),
        "archetype_suggestion": diagnostic_archetype,
        "suggested_rule_family": str(payload["suggested_rule_family"]),
        "reserved_slot_available": slot is not None,
        "slot_status": _coverage_slot_status(slot),
        "reserved_language_slot_matched": slot is not None,
        "reserved_language_slot": slot.to_dict() if slot is not None else {},
        "reserved_repairer_module": slot.repairer_module if slot is not None else "",
        "reserved_slot_registration_policy": slot.registration_policy if slot is not None else "",
        "recommended_next_owner": _recommended_next_owner(slot=slot, diagnostic_archetype=diagnostic_archetype),
        "recommended_route": recommended_route,
        "handoff_recommendation": handoff_recommendation,
        "llm_advisory_recommended": handoff_recommendation.startswith("llm_"),
        "agi_advisory_recommended": handoff_recommendation == "agi_advisory_non_authoritative",
        "authoritative_rule_registration_allowed": False,
        "recommended_registration_path": (
            slot.registration_policy if slot is not None else "coverage_report_then_bench_verified_rule"
        ),
        "missing_capability": "deterministic_repair_rule",
        "audit_reason": "known_rule_matched=false",
        "coverage_status": "coverage_gap",
    }


def _repair_language_slot_for_language(language: str) -> RepairLanguageSlot | None:
    normalized = str(language or "").strip().lower()
    if not normalized or normalized == "unknown":
        return None
    for slot in repair_language_slots():
        if slot.language == normalized or normalized in slot.aliases:
            return slot
    return None


def _recommended_next_owner(*, slot: RepairLanguageSlot | None, diagnostic_archetype: str) -> str:
    if slot is not None:
        return "runtime_rule"
    if str(diagnostic_archetype or "") == "unknown":
        return "llm"
    return "agi_advisory"


def _coverage_slot_status(slot: RepairLanguageSlot | None) -> str:
    return "reserved_slot_available" if slot is not None else "reserved_slot_missing"


def _coverage_status(item: RepairDiagnosticCoverage) -> str:
    if not item.known_rule_matched:
        return "coverage_gap"
    if item.executable_runtime_plan_matched:
        return "executable_runtime"
    return "metadata_only_not_executable"


def _coverage_recommended_route(
    *,
    slot: RepairLanguageSlot | None,
    diagnostic_language: str,
    diagnostic_archetype: str,
    known_rule_matched: bool,
    executable_runtime_plan_matched: bool,
    metadata_only_match: bool,
) -> str:
    if executable_runtime_plan_matched:
        return "runtime_rule"
    if metadata_only_match:
        if diagnostic_archetype == RepairArchetype.MISSING_DECLARED_TARGET.value:
            return "task_boundary"
        return "runtime_rule"
    if not known_rule_matched:
        if slot is not None:
            return "runtime_rule"
        if str(diagnostic_language or "unknown") != "unknown":
            return "add_reserved_slot"
        if str(diagnostic_archetype or "unknown") != "unknown":
            return "agi_advisory"
        return "llm_repair"
    if slot is not None:
        return "runtime_rule"
    if str(diagnostic_language or "unknown") != "unknown":
        return "add_reserved_slot"
    if str(diagnostic_archetype or "unknown") != "unknown":
        return "agi_advisory"
    return "llm_repair"


def _coverage_gap_handoff_recommendation(
    *,
    slot: RepairLanguageSlot | None,
    diagnostic_archetype: str,
) -> str:
    archetype = str(diagnostic_archetype or "unknown").strip() or "unknown"
    if slot is not None and archetype != "unknown":
        return "runtime_rule_backlog"
    if slot is not None:
        return "llm_triage_then_runtime_rule"
    if archetype == "unknown":
        return "llm_triage"
    return "agi_advisory_non_authoritative"


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


def _executable_runtime_metadata(*, scope: str = "typescript_syntax_runtime_builder") -> dict[str, Any]:
    return {
        "rule_status": "executable_runtime",
        "metadata_only": False,
        "executable_runtime_binding": True,
        "planner_helper_available": True,
        "runtime_plan_scope": scope,
        "unsafe_cases_fail_closed": True,
    }


def repair_language_slots() -> tuple[RepairLanguageSlot, ...]:
    """Return reserved language extension slots for future repair rules."""

    return _DEFAULT_REPAIR_LANGUAGE_SLOTS


def _non_empty(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("repair rule field must be non-empty")
    return normalized


def _repair_source_tool_known(source_tool: str) -> bool:
    normalized = str(source_tool or "").strip()
    return deterministic_repair_source_tool_known(normalized) or normalized in _RUNTIME_MIGRATION_SOURCE_TOOLS


def _tuple_str(value: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or ()) if str(item or "").strip())


def _suggest_rule_family(diagnostic: RepairDiagnostic) -> str:
    metadata_archetype = _diagnostic_metadata_archetype(diagnostic)
    if metadata_archetype:
        return metadata_archetype
    code = diagnostic.code.lower()
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    language = _infer_diagnostic_language(diagnostic)
    if code == "declared_target_missing":
        return RepairArchetype.MISSING_DECLARED_TARGET.value
    if code == "rust_e0277" or "derive" in message:
        return RepairArchetype.INCOMPATIBLE_DERIVE.value
    if code == "rust_e0433" or "unlinked crate" in message or "no required module" in message:
        return RepairArchetype.MISSING_DEPENDENCY.value
    if language == "rust" and _looks_like_rust_missing_method_self(message):
        return RepairArchetype.MISSING_METHOD_SELF.value
    if language == "go" and "imported and not used" in message:
        return RepairArchetype.GENERATED_RESIDUE.value
    if language == "java" and "reached end of file while parsing" in message:
        return RepairArchetype.OBJECT_LITERAL_SYNTAX.value
    if "import" in message or "unresolved" in message:
        return RepairArchetype.WRONG_IMPORT_PATH.value
    if "null" in message or "undefined" in message:
        return RepairArchetype.NULLABLE_TYPE_MISMATCH.value
    if "dependency" in message or "crate" in message or "module" in message:
        return RepairArchetype.MISSING_DEPENDENCY.value
    if code.startswith("typescript_ts") or (language == "typescript" and "expected" in message):
        return RepairArchetype.OBJECT_LITERAL_SYNTAX.value
    return "unknown"


def _diagnostic_metadata_archetype(diagnostic: RepairDiagnostic) -> str:
    for key in (
        "diagnostic_archetype",
        "archetype",
        "archetype_suggestion",
        "suggested_rule_family",
    ):
        value = str(diagnostic.metadata.get(key) or "").strip()
        if not value:
            continue
        normalized = value.lower()
        if normalized in RepairArchetype._value2member_map_:
            return normalized
    return ""


def _looks_like_rust_missing_method_self(message: str) -> bool:
    has_receiver_hint = "`&self`" in message or "`&mut self`" in message or "&self" in message
    has_missing_receiver_shape = "`&)`" in message or "(&)" in message or "found `&`" in message
    return has_receiver_hint and ("expected" in message or has_missing_receiver_shape)


def _infer_diagnostic_language(diagnostic: RepairDiagnostic) -> str:
    metadata_language = str(diagnostic.metadata.get("language") or "").strip().lower()
    if metadata_language:
        return metadata_language
    code = diagnostic.code.lower()
    path = str(diagnostic.path or "").lower()
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    if re.search(r"(?:file://)?[^\s:]+\.(?:cjs|js|mjs):\d+", message):
        return "javascript"
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
    basename = path.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    if basename in slot.file_names:
        return True
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
    if archetype == RepairArchetype.MISSING_DECLARED_TARGET.value:
        return "target_contract"
    if archetype == RepairArchetype.MISSING_DEPENDENCY.value:
        return "dependency_resolution"
    if archetype == RepairArchetype.INCOMPATIBLE_DERIVE.value:
        return "code_repair"
    if archetype == RepairArchetype.WRONG_IMPORT_PATH.value:
        return "quality_repair"
    if archetype == RepairArchetype.GENERATED_RESIDUE.value:
        return "code_repair"
    if archetype == RepairArchetype.NULLABLE_TYPE_MISMATCH.value:
        return "quality_repair"
    if archetype == RepairArchetype.OBJECT_LITERAL_SYNTAX.value:
        return "quality_repair"
    if archetype == RepairArchetype.MISSING_METHOD_SELF.value:
        return "quality_repair"
    typed_phase = _infer_typed_diagnostic_phase(diagnostic)
    if typed_phase:
        return typed_phase
    return "unknown"


def _infer_typed_diagnostic_phase(diagnostic: RepairDiagnostic) -> str:
    code = str(diagnostic.code or "").strip().lower()
    if code == "declared_target_missing":
        return "target_contract"
    quality_repair_prefixes = (
        "cpp_",
        "go_",
        "javascript_",
        "npm_",
        "python_",
        "rust_",
        "typescript_",
    )
    if any(code.startswith(prefix) for prefix in quality_repair_prefixes):
        return "quality_repair"
    source = str(diagnostic.source or "").strip().lower()
    if source not in {"artifact_quality", "runtime_smoke", "workspace_quality"}:
        return ""
    path = str(diagnostic.path or "").strip().lower().replace("\\", "/")
    if not path:
        return ""
    for slot in repair_language_slots():
        if _slot_path_matches(slot=slot, path=path):
            return "quality_repair"
    return ""
