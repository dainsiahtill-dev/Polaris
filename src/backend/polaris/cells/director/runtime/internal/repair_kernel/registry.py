"""Rule registry and diagnostic coverage reporting for Director repairs."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .contracts import RepairDiagnostic
from .cpp_syntax import (
    CPP_INCLUDE_PATH_SOURCE_TOOL,
    CPP_MISSING_PRIVATE_MEMBERS_SOURCE_TOOL,
    CPP_PLACEHOLDER_DECLARATION_SOURCE_TOOL,
    CPP_STANDARD_INCLUDE_SOURCE_TOOL,
    CPP_STRUCT_GETTER_FIELD_ACCESS_SOURCE_TOOL,
)
from .generic_hygiene_syntax import (
    DECLARED_TARGET_CONTRACT_SOURCE_TOOL,
    MISSING_DECLARED_TARGET_SOURCE_TOOL,
    PATCH_RESIDUE_CLEANUP_SOURCE_TOOL,
    PRE_MATERIALIZATION_DECLARED_TARGET_SOURCE_TOOL,
    QUALITY_REPAIR_SOURCE_TOOL,
    RUNTIME_DEPENDENCY_SOURCE_TOOL,
    SCAFFOLD_MARKER_CLEANUP_SOURCE_TOOL,
    SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL,
    SCAFFOLD_RESIDUE_CLEANUP_SOURCE_TOOL,
)
from .go_syntax import (
    GO_BARE_LOCAL_IMPORT_SOURCE_TOOL,
    GO_DEDUP_SOURCE_TOOL,
    GO_ERROR_STRING_HELPER_SOURCE_TOOL,
    GO_MODULE_IMPORT_SOURCE_TOOL,
    GO_NESTED_IMPORT_SOURCE_TOOL,
    GO_SUBPATH_IMPORT_SOURCE_TOOL,
    GO_UNUSED_IMPORT_SOURCE_TOOL,
)
from .java_syntax import JAVA_POST_SOURCE_TOOL, JAVA_TEST_DEPENDENCY_SOURCE_TOOL
from .javascript_syntax import (
    JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL,
    JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL,
    JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
    JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL,
    JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
    NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL,
    NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
)
from .python_syntax import (
    PYTHON_PACKAGE_CHILD_REEXPORT_SOURCE_TOOL,
    PYTHON_PACKAGE_SHADOW_BRIDGE_SOURCE_TOOL,
    PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL,
    PYTHON_UNITTEST_MISSING_TARGET_SOURCE_TOOL,
    PYTHON_UNITTEST_RUNTIME_FAILURE_SOURCE_TOOL,
    PYTHON_UNRESOLVED_IMPORT_SYMBOL_SOURCE_TOOL,
)
from .rust_syntax import (
    RUST_CRATE_IMPORT_REWRITE_SOURCE_TOOL,
    RUST_CRATE_IMPORT_SOURCE_TOOL,
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
from .strategy_catalog import deterministic_repair_source_tool_known
from .typescript_syntax import (
    HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
    JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL,
    TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL,
    TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL,
    TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL,
    TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL,
    TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL,
    TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL,
    TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL,
    TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL,
    TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
    TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL,
    TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
    TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
    TYPESCRIPT_NUMBER_PROPERTY_CALL_SOURCE_TOOL,
    TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL,
    TYPESCRIPT_READONLY_ASSIGNMENT_SOURCE_TOOL,
    TYPESCRIPT_REEXPORT_SOURCE_TOOL,
    TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,
    TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL,
    TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
    TYPESCRIPT_SCAFFOLD_SOURCE_TOOL,
    TYPESCRIPT_SHORTHAND_PROPERTY_SCOPE_SOURCE_TOOL,
    TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL,
    TYPESCRIPT_STRING_LITERAL_SUGGESTION_SOURCE_TOOL,
    TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL,
    TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL,
    TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL,
    TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL,
    TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
    TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL,
    TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL,
    TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
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
        TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL,
        TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL,
        TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL,
        TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL,
        TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
        TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
        TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
        TYPESCRIPT_REEXPORT_SOURCE_TOOL,
        TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,
        TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL,
        TYPESCRIPT_SCAFFOLD_SOURCE_TOOL,
        TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL,
        TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL,
        TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL,
        TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL,
        TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL,
        TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL,
        TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
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
    raw_terms: tuple[str, ...] = ()
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
        object.__setattr__(self, "raw_terms", tuple(term.lower() for term in _tuple_str(self.raw_terms)))
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
        if self.excluded_message_terms:
            message_haystack = (diagnostic.message or diagnostic.raw).lower()
            if any(term in message_haystack for term in self.excluded_message_terms):
                return False
        if self.excluded_raw_terms:
            raw_haystack = (diagnostic.raw or "").lower()
            if any(term in raw_haystack for term in self.excluded_raw_terms):
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
            "excluded_message_terms": list(self.excluded_message_terms),
            "excluded_raw_terms": list(self.excluded_raw_terms),
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
            "matched_source_tools": [rule.source_tool for rule in self.matched_rules],
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
            "runtime_blocker_reasons": [blocker["reason"] for blocker in runtime_blockers],
            "runtime_blockers": runtime_blockers,
        }


def _runtime_blockers_for_matched_rules(rules: Sequence[RepairRuleDefinition]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for rule in rules:
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
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="go.nested_import_keyword",
                source_tool=GO_NESTED_IMPORT_SOURCE_TOOL,
                language="go",
                phase="dependency_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                depends_on=("go.bare_import_string",),
                diagnostic_codes=("go_compile_error",),
                raw_terms=("import (", 'import "'),
                risk_level="low",
                description="Removes extra import keywords inside generated Go import blocks.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="go.bare_local_import",
                source_tool=GO_BARE_LOCAL_IMPORT_SOURCE_TOOL,
                language="go",
                phase="dependency_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=2,
                depends_on=("go.nested_import_keyword",),
                diagnostic_codes=("go_compile_error",),
                message_terms=("is not in std",),
                risk_level="low",
                description="Adds the module prefix to generated Go imports that point at local packages.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="go.import_subpath",
                source_tool=GO_SUBPATH_IMPORT_SOURCE_TOOL,
                language="go",
                phase="dependency_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=3,
                depends_on=("go.bare_local_import",),
                diagnostic_codes=("go_compile_error",),
                message_terms=("no required module",),
                risk_level="low",
                description="Rewrites hallucinated Go module import subpaths to real local package directories.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="generic.patch_residue_cleanup",
                source_tool=PATCH_RESIDUE_CLEANUP_SOURCE_TOOL,
                language="generic",
                phase="cleanup",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=0,
                raw_terms=("patch_file",),
                risk_level="low",
                description="Removes leaked patch protocol residue from scoped generated source files.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="generic.scaffold_marker_cleanup",
                source_tool=SCAFFOLD_MARKER_CLEANUP_SOURCE_TOOL,
                language="generic",
                phase="cleanup",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=0,
                raw_terms=("audit-seed",),
                risk_level="low",
                description="Rewrites deterministic scaffold marker strings through span-based text operations.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="generic.scaffold_marker_quality_cleanup",
                source_tool=SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL,
                language="generic",
                phase="cleanup",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=0,
                raw_terms=("deterministic scaffold marker",),
                risk_level="low",
                description="Rewrites scaffold marker strings only in files named by quality diagnostics.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="generic.scaffold_marker_placeholder_quality_cleanup",
                source_tool=SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL,
                language="generic",
                phase="cleanup",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=0,
                raw_terms=("generic/placeholder content detected",),
                risk_level="low",
                description="Rewrites placeholder scaffold markers only in files named by quality diagnostics.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="generic.scaffold_residue_cleanup",
                source_tool=SCAFFOLD_RESIDUE_CLEANUP_SOURCE_TOOL,
                language="generic",
                phase="cleanup",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=0,
                raw_terms=("scaffold", "residue", "audit-seed"),
                risk_level="low",
                description="Runtime-owned scaffold residue cleanup for audit-seed cleanup tasks.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="generic.missing_declared_target",
                source_tool=MISSING_DECLARED_TARGET_SOURCE_TOOL,
                language="generic",
                phase="target_contract",
                archetype=RepairArchetype.MISSING_DECLARED_TARGET,
                priority=1,
                diagnostic_codes=("declared_target_missing",),
                raw_terms=("src/",),
                risk_level="low",
                description="Creates missing declared target files only from nearby source files in base_files.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="generic.declared_target_contract",
                source_tool=DECLARED_TARGET_CONTRACT_SOURCE_TOOL,
                language="generic",
                phase="target_contract",
                archetype=RepairArchetype.MISSING_DECLARED_TARGET,
                priority=1,
                diagnostic_codes=("declared_target_missing",),
                raw_terms=("src/",),
                risk_level="low",
                description="Target-contract create-file repair using nearby source content and policy scope.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="generic.pre_materialization_declared_target",
                source_tool=PRE_MATERIALIZATION_DECLARED_TARGET_SOURCE_TOOL,
                language="generic",
                phase="pre_materialization",
                archetype=RepairArchetype.MISSING_DECLARED_TARGET,
                priority=1,
                diagnostic_codes=("declared_target_missing",),
                raw_terms=("src/",),
                risk_level="low",
                description="Pre-materialization declared-target repair limited to approved manifest/model targets.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="generic.runtime_dependency",
                source_tool=RUNTIME_DEPENDENCY_SOURCE_TOOL,
                language="dependency",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                raw_terms=("undeclared runtime import",),
                risk_level="medium",
                description="Adds known package.json runtime/dev dependencies through structured JSON operations.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="typescript.node_builtin_types_dependency",
                source_tool=RUNTIME_DEPENDENCY_SOURCE_TOOL,
                language="generic",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("artifact_quality_error",),
                raw_terms=("typescript node builtin import", "@types/node"),
                risk_level="medium",
                description="Adds @types/node when artifact quality detects TypeScript Node builtin usage.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="package_json_dev_dependency_json_set"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.node_scheme_module_types_dependency",
                source_tool=RUNTIME_DEPENDENCY_SOURCE_TOOL,
                language="typescript",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("typescript_ts2307",),
                raw_terms=("node:", "type declarations"),
                risk_level="medium",
                description="Adds @types/node when TypeScript cannot resolve a node: builtin module.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="package_json_dev_dependency_json_set"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.node_global_types_dependency",
                source_tool=RUNTIME_DEPENDENCY_SOURCE_TOOL,
                language="typescript",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("typescript_ts2580",),
                message_terms=("type definitions for node",),
                risk_level="medium",
                description="Adds @types/node when TypeScript reports missing Node global type definitions.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="package_json_dev_dependency_json_set"),
            ),
            RepairRuleDefinition(
                rule_id="generic.quality_repair",
                source_tool=QUALITY_REPAIR_SOURCE_TOOL,
                language="generic",
                phase="quality_repair",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=1,
                raw_terms=("deterministic scaffold marker",),
                risk_level="low",
                description="Conservative aggregate for generic cleanup-only quality repairs.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="go.module_import_path",
                source_tool=GO_MODULE_IMPORT_SOURCE_TOOL,
                language="go",
                phase="dependency_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=0,
                diagnostic_codes=("go_compile_error",),
                message_terms=("no required module",),
                risk_level="medium",
                description="Repairs Go module import paths that should point at local project packages.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="go.module_import_path_not_in_std",
                source_tool=GO_MODULE_IMPORT_SOURCE_TOOL,
                language="go",
                phase="dependency_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=0,
                diagnostic_codes=("go_compile_error",),
                message_terms=("is not in std", "/"),
                risk_level="medium",
                description="Repairs Go imports whose wrong module prefix is reported as a stdlib miss.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="go.deduplicate_generated_declarations",
                source_tool=GO_DEDUP_SOURCE_TOOL,
                language="go",
                phase="code_repair",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=2,
                diagnostic_codes=("go_compile_error",),
                message_terms=("redeclared in this block",),
                risk_level="medium",
                description="Removes safe duplicate generated Go declarations after compiler redeclaration diagnostics.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="go.unused_import",
                source_tool=GO_UNUSED_IMPORT_SOURCE_TOOL,
                language="go",
                phase="code_repair",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=2,
                diagnostic_codes=("go_compile_error",),
                message_terms=("imported and not used",),
                risk_level="low",
                description="Removes compiler-reported unused Go imports through span-based text replacement.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="unused_import_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="go.error_string_helper",
                source_tool=GO_ERROR_STRING_HELPER_SOURCE_TOOL,
                language="go",
                phase="code_repair",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=3,
                depends_on=("go.unused_import",),
                diagnostic_codes=("go_compile_error",),
                message_terms=("undefined:", "errstring"),
                risk_level="low",
                description=(
                    "Adds a missing Go error-string helper type only when a compiler undefined "
                    "identifier diagnostic matches an error-string literal conversion pattern."
                ),
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="go_error_string_helper_text_insert"),
            ),
            RepairRuleDefinition(
                rule_id="cpp.include_path",
                source_tool=CPP_INCLUDE_PATH_SOURCE_TOOL,
                language="cpp",
                phase="post_materialization",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=0,
                diagnostic_codes=("cpp_compile_error",),
                message_terms=("file not found",),
                risk_level="low",
                description="Repairs C++ quote include paths that do not match generated header layout.",
                runtime_plan_available=True,
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
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="cpp.standard_include",
                source_tool=CPP_STANDARD_INCLUDE_SOURCE_TOOL,
                language="cpp",
                phase="post_materialization",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=0,
                depends_on=("cpp.include_path",),
                diagnostic_codes=("cpp_compile_error",),
                raw_terms=("std::",),
                risk_level="low",
                description="Adds missing C++ standard library includes for generated std:: type usage.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="cpp.placeholder_declaration",
                source_tool=CPP_PLACEHOLDER_DECLARATION_SOURCE_TOOL,
                language="cpp",
                phase="post_materialization",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("cpp_compile_error",),
                raw_terms=("std::render_return_type",),
                risk_level="low",
                description="Removes invalid generated C++ placeholder declarations.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="cpp.missing_private_members",
                source_tool=CPP_MISSING_PRIVATE_MEMBERS_SOURCE_TOOL,
                language="cpp",
                phase="post_materialization",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                depends_on=("cpp.standard_include",),
                diagnostic_codes=("cpp_compile_error",),
                raw_terms=("return", "_"),
                risk_level="medium",
                description="Adds missing private member declarations for generated inline C++ getters.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="cpp.struct_getter_field_access",
                source_tool=CPP_STRUCT_GETTER_FIELD_ACCESS_SOURCE_TOOL,
                language="cpp",
                phase="post_materialization",
                archetype=RepairArchetype.MISSING_METHOD_SELF,
                priority=1,
                diagnostic_codes=("cpp_compile_error",),
                raw_terms=("get_", "no member"),
                risk_level="low",
                description="Rewrites generated getter calls for public C++ struct fields to direct field access.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="html.typescript_module_script",
                source_tool=HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
                language="html",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                raw_terms=("typescript", "module script"),
                risk_level="low",
                description="Covers HTML entrypoint script module adjustments for TypeScript-generated projects.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="html_typescript_module_script_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="java.cannot_find_symbol",
                source_tool=JAVA_POST_SOURCE_TOOL,
                language="java",
                phase="post_materialization",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("java_compile_error",),
                message_terms=("cannot find symbol",),
                risk_level="medium",
                description="Covers Java post-pass repairs for unresolved symbols after generation.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="java.eof_truncation_closure",
                source_tool=JAVA_POST_SOURCE_TOOL,
                language="java",
                phase="quality_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("java_compile_error",),
                message_terms=("reached end of file while parsing",),
                risk_level="low",
                description="Repairs bounded generated Java EOF truncation by removing an incomplete tail statement and closing braces.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="java_eof_truncation_span_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="java.numeric_constant_literal_type",
                source_tool=JAVA_POST_SOURCE_TOOL,
                language="java",
                phase="quality_repair",
                archetype=RepairArchetype.NULLABLE_TYPE_MISMATCH,
                priority=1,
                diagnostic_codes=("java_compile_error",),
                message_terms=("possible lossy conversion from double to int",),
                risk_level="low",
                description="Repairs generated Java int constants initialized with decimal literals by widening the constant type.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="java_numeric_constant_type_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="java.missing_symbol_compatibility",
                source_tool=JAVA_POST_SOURCE_TOOL,
                language="java",
                phase="quality_repair",
                archetype=RepairArchetype.MISSING_METHOD_SELF,
                priority=1,
                diagnostic_codes=("java_compile_error",),
                message_terms=("cannot find symbol",),
                risk_level="medium",
                description="Adds conservative Java compatibility aliases for generated tests that reference missing threshold constants or will* score helpers.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="java_missing_symbol_compatibility_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="java.common_accessor_aliases",
                source_tool="deterministic_java_accessor_alias_repair",
                language="java",
                phase="post_materialization",
                archetype=RepairArchetype.MISSING_METHOD_SELF,
                priority=1,
                diagnostic_codes=("java_compile_error",),
                raw_terms=("gettemperament",),
                risk_level="low",
                description="Adds deterministic Java accessor aliases used by generated tests.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="java.junit_test_dependency",
                source_tool=JAVA_TEST_DEPENDENCY_SOURCE_TOOL,
                language="java",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("java_compile_error",),
                message_terms=("org.junit", "does not exist"),
                risk_level="medium",
                description="Rewrites JUnit-dependent Java test sources to plain Java executable tests.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="java.junit_jupiter_test_dependency",
                source_tool=JAVA_TEST_DEPENDENCY_SOURCE_TOOL,
                language="java",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("java_compile_error",),
                message_terms=("org.junit.jupiter",),
                risk_level="medium",
                description="Covers JUnit Jupiter import diagnostics in generated Java tests.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="java.junit_test_symbol_dependency",
                source_tool=JAVA_TEST_DEPENDENCY_SOURCE_TOOL,
                language="java",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("java_compile_error",),
                message_terms=("cannot find symbol", "test"),
                risk_level="medium",
                description="Covers missing JUnit Test symbol diagnostics in generated Java tests.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="java.package_does_not_exist",
                source_tool=JAVA_POST_SOURCE_TOOL,
                language="java",
                phase="post_materialization",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("java_compile_error",),
                message_terms=("package", "does not exist"),
                risk_level="medium",
                description="Covers Java post-pass repairs for unresolved package/import diagnostics.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="javascript.commonjs_esm_entrypoint",
                source_tool=JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL,
                language="javascript",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("javascript_module_error",),
                message_terms=("require is not defined",),
                risk_level="medium",
                description="Covers generated JavaScript entrypoints that mix CommonJS with ESM package mode.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="javascript.cannot_find_module",
                source_tool=NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL,
                language="javascript",
                phase="test_contract",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("javascript_module_error",),
                message_terms=("cannot find module",),
                risk_level="medium",
                description="Covers Node test/entrypoint module path contract repairs.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="javascript.npm_script_contract",
                source_tool=NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
                language="javascript",
                phase="test_contract",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                raw_terms=("npm package manifest script",),
                risk_level="low",
                description="Repairs package.json script contracts through structured JSON operations.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="javascript.npm_script_port_conflict",
                source_tool=NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
                language="javascript",
                phase="runtime_smoke",
                archetype=RepairArchetype.RUNTIME_CONTRACT,
                priority=1,
                diagnostic_codes=("workspace_validation_failed",),
                raw_terms=("eaddrinuse",),
                risk_level="low",
                description="Repairs fixed-port package start scripts when npm start fails with EADDRINUSE.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="javascript.typescript_test_runner_script_contract",
                source_tool=NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
                language="javascript",
                phase="test_contract",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("artifact_quality_error",),
                raw_terms=("npm run test", "strip-types"),
                risk_level="low",
                description=(
                    "Repairs generated TypeScript npm test scripts that depend on an unstable "
                    "direct .ts loader instead of the compiled verifier."
                ),
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="javascript.npm_script_typescript_source_require_contract",
                source_tool=NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
                language="javascript",
                phase="test_contract",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("artifact_quality_error", "javascript_module_error"),
                raw_terms=("cannot find module", "./src/"),
                risk_level="low",
                description="Repairs npm test scripts that require TypeScript source paths without a loader.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="javascript.node_test_script_contract",
                source_tool=NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL,
                language="javascript",
                phase="test_contract",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                raw_terms=("scripts/test.mjs",),
                risk_level="medium",
                description="Replaces over-strict generated Node test contract scripts with substantive checks.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="javascript.test_missing_target",
                source_tool=JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
                language="javascript",
                phase="target_contract",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("declared_target_missing",),
                raw_terms=("tests/", ".js"),
                risk_level="low",
                description="Creates missing declared JavaScript frontend smoke targets from existing HTML/JS files.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="javascript.node_test_missing_target",
                source_tool=JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
                language="javascript",
                phase="target_contract",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("artifact_quality_error",),
                raw_terms=("npm run test", "module_not_found"),
                risk_level="low",
                description=("Creates missing local Node smoke test targets referenced by package.json test scripts."),
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="node_test_target_write_file"),
            ),
            RepairRuleDefinition(
                rule_id="javascript.node_test_missing_directory_target",
                source_tool=JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
                language="javascript",
                phase="target_contract",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("artifact_quality_error", "workspace_validation_failed"),
                raw_terms=("npm", "could not find", "test"),
                risk_level="low",
                description=(
                    "Creates a concrete Node smoke test file when package.json points at a missing "
                    "local tests directory."
                ),
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="node_test_directory_target_write_file"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.node_test_missing_directory_target",
                source_tool=JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
                language="typescript",
                phase="target_contract",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("workspace_validation_failed",),
                raw_terms=("npm", "could not find", "test"),
                risk_level="low",
                description=(
                    "Creates a TypeScript source smoke test when a compiled Node test directory "
                    "target such as dist/__tests__ is missing."
                ),
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="node_test_directory_target_write_file"),
            ),
            RepairRuleDefinition(
                rule_id="javascript.missing_named_export",
                source_tool=JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
                language="javascript",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("javascript_module_error",),
                message_terms=("does not provide an export named",),
                risk_level="low",
                description="Covers JavaScript missing named export repair metadata.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="javascript.dom_global_runtime_guard",
                source_tool=JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL,
                language="javascript",
                phase="runtime_smoke",
                archetype=RepairArchetype.RUNTIME_CONTRACT,
                priority=1,
                diagnostic_codes=("javascript_dom_global_in_node_runtime",),
                message_terms=("dom global", "not available in node"),
                risk_level="low",
                description=("Guards browser-only bootstrap calls when Node smoke/start executes a browser bundle."),
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="javascript.missing_method_runtime",
                source_tool=JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL,
                language="javascript",
                phase="runtime_smoke",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("javascript_module_error",),
                message_terms=("is not a function",),
                risk_level="medium",
                description="Adds conservative method aliases for traceable JavaScript runtime TypeError failures.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="python.unittest_missing_target",
                source_tool=PYTHON_UNITTEST_MISSING_TARGET_SOURCE_TOOL,
                language="python",
                phase="target_contract",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("declared_target_missing",),
                raw_terms=("tests/", "test_", ".py"),
                risk_level="low",
                description="Creates missing declared Python unittest smoke targets from existing module files.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="python.readme_required_token",
                source_tool=PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL,
                language="python",
                phase="quality_repair",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("python_assertionerror",),
                raw_terms=("readme missing required token",),
                risk_level="low",
                description="Appends verifier-required tokens to existing README documentation.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="python.module_not_found",
                source_tool=PYTHON_PACKAGE_SHADOW_BRIDGE_SOURCE_TOOL,
                language="python",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("python_modulenotfounderror",),
                message_terms=("no module named",),
                risk_level="medium",
                description="Covers Python package/module import bridge repairs.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="python.package_child_reexport",
                source_tool=PYTHON_PACKAGE_CHILD_REEXPORT_SOURCE_TOOL,
                language="python",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("python_importerror",),
                message_terms=("cannot import name", "__init__.py"),
                risk_level="medium",
                description="Re-exports symbols found in child package modules from package __init__.py.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="python.runtime_attribute_error",
                source_tool=PYTHON_UNITTEST_RUNTIME_FAILURE_SOURCE_TOOL,
                language="python",
                phase="runtime_smoke",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("python_attributeerror",),
                risk_level="medium",
                description="Covers Python runtime smoke repairs for generated API/member mismatches.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="python.runtime_smoke_failure",
                source_tool=PYTHON_UNITTEST_RUNTIME_FAILURE_SOURCE_TOOL,
                language="python",
                phase="runtime_smoke",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("python_runtime_smoke_failed",),
                risk_level="medium",
                description="Covers Python runtime smoke verifier failures after generation.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="python.unresolved_import_symbol",
                source_tool=PYTHON_UNRESOLVED_IMPORT_SYMBOL_SOURCE_TOOL,
                language="python",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("cross_artifact_unresolved_import_symbol",),
                raw_terms=("unresolved import symbol",),
                excluded_raw_terms=(".ts", ".tsx", ".js", ".jsx"),
                risk_level="medium",
                description=(
                    "Repairs Python cross-artifact import symbols only through real similar aliases; "
                    "missing interface contracts fail closed for CE amendment."
                ),
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.missing_trait_derive",
                source_tool=RUST_MISSING_TRAIT_DERIVE_SOURCE_TOOL,
                language="rust",
                phase="code_repair",
                archetype=RepairArchetype.INCOMPATIBLE_DERIVE,
                priority=1,
                diagnostic_codes=("rust_e0277",),
                message_terms=("the trait bound", "is not satisfied"),
                risk_level="low",
                description="Adds ordinary missing Rust trait derives as span-based text_replace edits.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.serde_derive",
                source_tool=RUST_SERDE_DERIVE_SOURCE_TOOL,
                language="rust",
                phase="code_repair",
                archetype=RepairArchetype.INCOMPATIBLE_DERIVE,
                priority=1,
                diagnostic_codes=("rust_e0277",),
                raw_terms=("consider adding", "#[derive(serde::"),
                risk_level="low",
                description="Adds missing serde Serialize/Deserialize derives as span-based text_replace edits.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.incompatible_copy_derive",
                source_tool=RUST_INCOMPATIBLE_COPY_DERIVE_SOURCE_TOOL,
                language="rust",
                phase="code_repair",
                archetype=RepairArchetype.INCOMPATIBLE_DERIVE,
                priority=1,
                raw_terms=("the trait `Copy` cannot be implemented", "-->", ".rs"),
                risk_level="low",
                description="Removes invalid Rust Copy derive tokens as span-based text_replace edits.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.method_self_signature",
                source_tool=RUST_METHOD_SELF_SIGNATURE_SOURCE_TOOL,
                language="rust",
                phase="code_repair",
                archetype=RepairArchetype.MISSING_METHOD_SELF,
                priority=1,
                raw_terms=("expected parameter name", "-->", ".rs"),
                risk_level="low",
                description="Repairs generated Rust method receiver signatures such as `(&)` to `(&self)`.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.missing_binary_entrypoint",
                source_tool=RUST_MISSING_BINARY_ENTRYPOINT_SOURCE_TOOL,
                language="rust",
                phase="structural_repair",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                raw_terms=("couldn't read", "No such file or directory"),
                risk_level="low",
                description="Creates missing Rust binary entrypoint files declared by Cargo [[bin]] targets.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.missing_module_file",
                source_tool=RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
                language="rust",
                phase="structural_repair",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("rust_e0583",),
                message_terms=("file not found for module",),
                raw_terms=("to create the module", "create file"),
                risk_level="low",
                description="Creates comment-only Rust module topology stubs from rustc E0583 help paths.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.missing_lib_target_src_lib",
                source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
                language="rust",
                phase="structural_repair",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                raw_terms=("can't find library", "at path", "src/lib.rs"),
                excluded_raw_terms=("file not found for module", "to create the module"),
                risk_level="low",
                description="Creates a comment-only default src/lib.rs when Cargo's library target file is absent.",
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "src_lib_rs_missing_file_only",
                    "custom_lib_path_supported": False,
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="rust.missing_lib_target_src_lib_manifest",
                source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
                language="rust",
                phase="structural_repair",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                raw_terms=("[lib].path", "src/lib.rs", "missing", "library target"),
                excluded_raw_terms=("file not found for module", "to create the module"),
                risk_level="low",
                description="Covers manifest validation signals for a missing default src/lib.rs target.",
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "src_lib_rs_missing_file_only",
                    "custom_lib_path_supported": False,
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="rust.missing_lib_target",
                source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
                language="rust",
                phase="structural_repair",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                raw_terms=("lib", "path", ".rs"),
                excluded_raw_terms=("file not found for module", "to create the module"),
                risk_level="medium",
                description=(
                    "Covers missing Cargo library target files from rustc library target path "
                    "diagnostics or missing [lib].path-style manifest validation."
                ),
                runtime_plan_available=False,
                metadata={
                    "rule_status": "metadata_rule_registered",
                    "metadata_only": True,
                    "executable_runtime_binding": False,
                    "planner_helper_available": False,
                    "legacy_materialization_runner": False,
                },
            ),
            RepairRuleDefinition(
                rule_id="rust.duplicate_module_file",
                source_tool=RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
                language="rust",
                phase="structural_repair",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=2,
                diagnostic_codes=("rust_e0761",),
                risk_level="medium",
                description=(
                    "Repairs Rust E0761 duplicate module files through a policy-gated delete_file "
                    "operation when one side is generated/comment-only and the sibling contains real Rust code."
                ),
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "planner_helper_available": True,
                    "executable_runtime_binding": True,
                    "delete_file_global_validation_required": True,
                },
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
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.unresolved_known_dependency_import",
                source_tool="deterministic_rust_dependency_repair",
                language="rust",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=0,
                diagnostic_codes=("rust_e0432",),
                message_terms=("unresolved import",),
                raw_terms=("serde",),
                risk_level="medium",
                description="Repairs unresolved Rust imports for runtime-cataloged dependency crates.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.unresolved_import_path",
                source_tool=RUST_CRATE_IMPORT_SOURCE_TOOL,
                language="rust",
                phase="dependency_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=0,
                diagnostic_codes=("rust_e0432",),
                message_terms=("unresolved import",),
                risk_level="medium",
                description="Repairs Rust crate/module import path mismatches.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.crate_import_rewrite",
                source_tool=RUST_CRATE_IMPORT_REWRITE_SOURCE_TOOL,
                language="rust",
                phase="dependency_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=0,
                depends_on=("rust.unlinked_crate_dependency",),
                diagnostic_codes=("rust_e0433",),
                raw_terms=("cannot find crate",),
                risk_level="low",
                description="Rewrites wrong local Rust crate prefixes to the canonical Cargo crate name.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.wrong_crate_path",
                source_tool=RUST_WRONG_CRATE_PATH_SOURCE_TOOL,
                language="rust",
                phase="dependency_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=0,
                depends_on=("rust.unlinked_crate_dependency",),
                diagnostic_codes=("rust_e0432",),
                raw_terms=("help:", "a similar path exists", "use ", "-->", ".rs"),
                risk_level="low",
                description="Applies cargo wrong crate path suggestions as span-based text_replace edits.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.unresolved_pub_use",
                source_tool=RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL,
                language="rust",
                phase="export_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=2,
                depends_on=("rust.unresolved_import_path",),
                diagnostic_codes=("rust_e0432",),
                raw_terms=("no `", " in "),
                excluded_raw_terms=(" in the root",),
                risk_level="medium",
                description="Repairs stale Rust public re-exports after module generation.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.lib_root_facade_root_import",
                source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
                language="rust",
                phase="export_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=2,
                diagnostic_codes=("rust_e0432",),
                message_terms=("unresolved import",),
                raw_terms=(" in the root",),
                risk_level="medium",
                description=(
                    "Covers Rust crate-root unresolved import diagnostics that require a lib.rs "
                    "facade export rather than stale pub-use deletion."
                ),
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "single_pub_use_export_insert_after_declared_module",
                    "legacy_materialization_runner": False,
                },
            ),
            RepairRuleDefinition(
                rule_id="rust.lib_root_facade_export",
                source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
                language="generic",
                phase="export_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=2,
                raw_terms=("lib.rs", "expose"),
                risk_level="medium",
                description="Covers verifier signals that require src/lib.rs to expose a generated public API.",
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "single_pub_use_export_insert_after_declared_module",
                    "legacy_materialization_runner": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="rust.lib_root_facade_path_rewrite",
                source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
                language="rust",
                phase="export_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=2,
                raw_terms=("lib-root path rewrite",),
                risk_level="low",
                description="Rewrites crate::lib or canonical_crate::lib paths through one span-based text_replace.",
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "crate_lib_prefix_path_rewrite_only",
                    "legacy_materialization_runner": False,
                },
            ),
            RepairRuleDefinition(
                rule_id="rust.unused_import",
                source_tool=RUST_UNUSED_IMPORT_SOURCE_TOOL,
                language="rust",
                phase="code_repair",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=2,
                raw_terms=("warning:", "unused import", "-->", ".rs"),
                risk_level="low",
                description=(
                    "Removes or comments Rust unused import residue from generated code as "
                    "span-based text_replace edits."
                ),
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.trait_import",
                source_tool=RUST_TRAIT_IMPORT_SOURCE_TOOL,
                language="rust",
                phase="export_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=3,
                depends_on=("rust.unresolved_pub_use",),
                diagnostic_codes=("rust_e0599",),
                raw_terms=("help:", "trait", "implemented but not in scope", "use "),
                risk_level="low",
                description="Applies Rust compiler trait import suggestions as span-based text_replace edits.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.line_suggestion",
                source_tool=RUST_LINE_SUGGESTION_SOURCE_TOOL,
                language="rust",
                phase="code_repair",
                archetype=RepairArchetype.MISSING_METHOD_SELF,
                priority=3,
                raw_terms=("help:", "-->", ".rs", " | "),
                risk_level="low",
                description="Applies Rust compiler single-line help suggestions as span-based text_replace edits.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.field_rename_suggestion",
                source_tool=RUST_FIELD_RENAME_SUGGESTION_SOURCE_TOOL,
                language="rust",
                phase="code_repair",
                archetype=RepairArchetype.MISSING_METHOD_SELF,
                priority=2,
                diagnostic_codes=("rust_e0609",),
                raw_terms=("no field", "help:", "similar name exists", "-->", ".rs"),
                risk_level="low",
                description="Applies Rust E0609 field rename suggestions as span-based text_replace edits.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="rust.missing_struct_field_declaration",
                source_tool=RUST_MISSING_FIELDS_SOURCE_TOOL,
                language="rust",
                phase="code_repair",
                archetype=RepairArchetype.MISSING_METHOD_SELF,
                priority=3,
                diagnostic_codes=("rust_e0609",),
                message_terms=("no field", "on type"),
                raw_terms=("-->", ".rs"),
                excluded_raw_terms=("similar name exists",),
                risk_level="medium",
                description=(
                    "Covers Rust E0609 field access diagnostics where rustc reports no matching "
                    "struct field and offers no similar-name rename suggestion."
                ),
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="rust.post_execution_convergence",
                source_tool=RUST_POST_SOURCE_TOOL,
                language="rust",
                phase="multi_phase_convergence",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=4,
                depends_on=(
                    "rust.unresolved_import_path",
                    "rust.missing_struct_field_declaration",
                    "rust.struct_literal_missing_field_initializer",
                ),
                raw_terms=("rust post execution convergence",),
                risk_level="medium",
                description=(
                    "Owns the legacy Rust post-execution aggregate label in the runtime dispatcher. "
                    "The planner is intentionally fail-closed unless a split executable Rust rule "
                    "produces a safe plan."
                ),
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": False,
                    "aggregate_label": True,
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="rust.struct_literal_missing_field_initializer",
                source_tool=RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL,
                language="rust",
                phase="code_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=3,
                diagnostic_codes=("rust_e0063",),
                message_terms=("missing field", "initializer"),
                raw_terms=("-->", ".rs"),
                risk_level="medium",
                description=(
                    "Covers Rust E0063 struct literal diagnostics where generated initializers omit required fields."
                ),
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "planner_scope": "generated_marker_single_literal_safe_initializer_only",
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="typescript.hyphenated_identifier",
                source_tool=TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.INVALID_IDENTIFIER,
                priority=1,
                diagnostic_codes=("typescript_ts1005",),
                message_terms=(",", "expected"),
                risk_level="low",
                description=(
                    "Repairs illegal hyphenated TypeScript variable identifiers reported as TS1005 when the "
                    "diagnostic line has a safe const/let/var declaration."
                ),
                runtime_plan_available=True,
                metadata={
                    **_executable_runtime_metadata(scope="same_file_hyphenated_variable_identifier"),
                    "candidate_match_requires_source_line_confirmation": True,
                },
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
                rule_id="typescript.object_literal_property_semicolon",
                source_tool=TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("typescript_return_object_property_semicolon",),
                message_terms=("semicolon-terminated", "property"),
                risk_level="low",
                description="Repairs semicolon-terminated properties inside TypeScript return object literals.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="typescript.javascript_annotation_cleanup",
                source_tool=JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL,
                language="javascript",
                phase="quality_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                raw_terms=("unexpected token ':'",),
                risk_level="low",
                description="Removes generated TypeScript annotations that leaked into JavaScript files.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="javascript_typescript_annotation_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.typeorm_model_normalization",
                source_tool=TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL,
                language="typescript",
                phase="dependency_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                raw_terms=("typeorm", "undeclared runtime import"),
                risk_level="medium",
                description="Normalizes generated TypeORM model sources when runtime dependency repair reports typeorm.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="typeorm_model_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.commonjs_package_type",
                source_tool=TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                raw_terms=("commonjs", "package.json", "type"),
                risk_level="medium",
                description="Aligns package.json type with CommonJS TypeScript compiler output.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="package_json_type_json_set"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.entrypoint",
                source_tool=TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL,
                language="typescript",
                phase="target_contract",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                raw_terms=("typescript entrypoint",),
                risk_level="low",
                description="Creates a missing TypeScript source entrypoint for a compiled package entrypoint.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="typescript_entrypoint_write_file"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.escaped_newline",
                source_tool=TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=1,
                raw_terms=("escaped newline",),
                risk_level="low",
                description="Repairs generated escaped newline residue in TypeScript line comments.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="escaped_newline_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.member_alias",
                source_tool=TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.MISSING_METHOD_SELF,
                priority=1,
                diagnostic_codes=("typescript_ts2339",),
                message_terms=("property", "does not exist"),
                risk_level="low",
                description="Rewrites generated TypeScript member access to a safe existing alias when one is traceable.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="member_alias_line_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.import_specifier_keyword",
                source_tool=TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=1,
                diagnostic_codes=("typescript_ts1003",),
                message_terms=("identifier expected",),
                risk_level="low",
                description=(
                    "Normalizes generated TypeScript named import specifiers that incorrectly include "
                    "export/import type keywords inside the import clause."
                ),
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="named_import_specifier_keyword_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.missing_member",
                source_tool=TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.MISSING_METHOD_SELF,
                priority=1,
                diagnostic_codes=("typescript_ts2339",),
                message_terms=("property", "does not exist"),
                risk_level="medium",
                description="Adds conservative generated TypeScript members to a traceable declaration.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="missing_member_declaration_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.object_literal_missing_member_implementation",
                source_tool=TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("typescript_ts2739", "typescript_ts2741"),
                message_terms=("missing", "type"),
                risk_level="medium",
                description=(
                    "Adds object-literal method implementations when an interface method was declared "
                    "but a same-file factory Object.freeze return does not implement it."
                ),
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="object_literal_member_implementation_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.reexport",
                source_tool=TYPESCRIPT_REEXPORT_SOURCE_TOOL,
                language="typescript",
                phase="export_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=2,
                raw_terms=("no exported member",),
                risk_level="medium",
                description="Adds a TypeScript re-export when a unique runtime export source is traceable.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="unique_symbol_reexport_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.reexported_type_binding",
                source_tool=TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,
                language="typescript",
                phase="export_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=2,
                diagnostic_codes=("typescript_ts2304",),
                message_terms=("cannot find name",),
                risk_level="medium",
                description="Adds missing TypeScript type imports for symbols exported elsewhere in the project.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="reexported_type_import_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.relative_import_case",
                source_tool=TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                raw_terms=("unresolved relative import",),
                risk_level="low",
                description="Rewrites generated TypeScript relative imports to the exact file-system casing.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="relative_import_case_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.html_container_selector",
                source_tool=TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL,
                language="html",
                phase="target_runtime",
                archetype=RepairArchetype.RUNTIME_CONTRACT,
                priority=1,
                diagnostic_codes=("html_container_contract_failed",),
                risk_level="low",
                description=(
                    "Broadens generated TypeScript/HTML verifier container-id regexes only when actual HTML "
                    "container ids contain the declared selector tokens."
                ),
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="html_container_selector_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.scaffold",
                source_tool=TYPESCRIPT_SCAFFOLD_SOURCE_TOOL,
                language="typescript",
                phase="target_contract",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                raw_terms=("package.json", "tsconfig.json", "missing"),
                risk_level="low",
                description="Creates missing package.json or tsconfig.json scaffolds for generated TypeScript projects.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="typescript_manifest_scaffold_write_file"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.sourcefile_diagnostics",
                source_tool=TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=1,
                raw_terms=("sourcefile", "diagnostics"),
                risk_level="low",
                description="Repairs generated TypeScript SourceFile diagnostics API usage.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="sourcefile_diagnostics_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.missing_closing_brace",
                source_tool=TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("typescript_ts1005",),
                message_terms=("}", "expected"),
                risk_level="low",
                description="Covers missing closing brace syntax repairs reported as TS1005.",
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "bounded_eof_closing_brace_insert",
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="typescript.duplicate_object_property",
                source_tool="deterministic_typescript_duplicate_object_property_repair",
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("typescript_ts1117",),
                message_terms=("object literal", "multiple properties"),
                risk_level="low",
                description="Removes duplicate single-line TypeScript object literal properties reported as TS1117.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="typescript.enum_member_separator",
                source_tool="deterministic_typescript_enum_member_separator_repair",
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("typescript_ts1357",),
                message_terms=("enum member", "followed"),
                risk_level="low",
                description="Repairs TypeScript enum members that need a comma, initializer, or closing brace separator.",
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="typescript.missing_export",
                source_tool=TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("typescript_ts2305",),
                message_terms=("no exported member",),
                risk_level="low",
                description="Covers TypeScript missing export repairs for generated modules.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="missing_export_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.unresolved_import_symbol_missing_export",
                source_tool=TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("cross_artifact_unresolved_import_symbol",),
                raw_terms=("unresolved import symbol", ".ts"),
                risk_level="low",
                description=(
                    "Routes TypeScript cross-artifact unresolved symbol diagnostics through the interface "
                    "contract plane before any missing-export repair."
                ),
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="missing_export_unresolved_import_symbol"),
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
                runtime_plan_available=True,
            ),
            RepairRuleDefinition(
                rule_id="typescript.nullable_dom_global",
                source_tool="deterministic_typescript_nullable_canvas_context_repair",
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.NULLABLE_TYPE_MISMATCH,
                priority=1,
                diagnostic_codes=("typescript_ts18047", "typescript_ts18048"),
                message_terms=("possibly",),
                risk_level="low",
                description="Adds narrow guards for nullable browser globals such as window/document.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="nullable_dom_global_guard"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.number_to_string_argument",
                source_tool=TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.NULLABLE_TYPE_MISMATCH,
                priority=1,
                diagnostic_codes=("typescript_ts2345",),
                message_terms=("argument of type", "number", "not assignable", "string"),
                risk_level="low",
                description="Wraps the reported generated number argument in String(...) for TS2345 string parameters.",
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "diagnostic_line_argument_span_text_replace",
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="typescript.number_property_call",
                source_tool=TYPESCRIPT_NUMBER_PROPERTY_CALL_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.NULLABLE_TYPE_MISMATCH,
                priority=1,
                diagnostic_codes=("typescript_ts2349",),
                message_terms=("not callable",),
                risk_level="low",
                description="Removes a zero-argument call from a generated numeric property access reported as TS2349.",
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "same_line_zero_arg_property_call_text_replace",
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="typescript.readonly_assignment",
                source_tool=TYPESCRIPT_READONLY_ASSIGNMENT_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("typescript_ts2540",),
                message_terms=("cannot assign to", "read-only property"),
                risk_level="low",
                description="Removes a same-file readonly property modifier when generated code mutates that property.",
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "same_file_readonly_property_modifier_text_replace",
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="typescript.shorthand_property_scope",
                source_tool=TYPESCRIPT_SHORTHAND_PROPERTY_SCOPE_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("typescript_ts18004",),
                message_terms=("no value exists in scope", "shorthand property"),
                risk_level="low",
                description="Removes same-line object-literal shorthand properties when no value exists in scope.",
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "same_line_object_literal_shorthand_delete",
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="typescript.string_literal_suggestion",
                source_tool=TYPESCRIPT_STRING_LITERAL_SUGGESTION_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("typescript_ts2820",),
                message_terms=("not assignable to type", "did you mean"),
                risk_level="low",
                description="Applies TypeScript's exact same-line string literal suggestion for TS2820 diagnostics.",
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "same_line_string_literal_suggestion_text_replace",
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="typescript.unknown_member_access",
                source_tool=TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.NULLABLE_TYPE_MISMATCH,
                priority=1,
                diagnostic_codes=("typescript_ts18046",),
                message_terms=("is of type", "unknown"),
                risk_level="low",
                description=(
                    "Narrows an existing unknown-typed TypeScript member to a minimal structural type "
                    "when the diagnostic line proves indexed or array-like access."
                ),
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "existing_unknown_member_type_text_replace",
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="typescript.canvas_scale_return_type",
                source_tool=TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.NULLABLE_TYPE_MISMATCH,
                priority=1,
                diagnostic_codes=("typescript_ts2345",),
                message_terms=("argument of type", "number", "not assignable", "(n: number) => number"),
                risk_level="low",
                description="Narrows scaleToCanvas return types from numeric sx/sy values to mapper functions.",
                runtime_plan_available=True,
                metadata={
                    "rule_status": "executable_runtime",
                    "metadata_only": False,
                    "executable_runtime_binding": True,
                    "planner_helper_available": True,
                    "runtime_plan_scope": "scale_to_canvas_return_type_text_replace",
                    "unsafe_cases_fail_closed": True,
                },
            ),
            RepairRuleDefinition(
                rule_id="typescript.tsconfig_lib",
                source_tool=TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL,
                language="typescript",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                raw_terms=("tsconfig", "lib"),
                risk_level="medium",
                description="Adds TypeScript compiler lib/module options required by DOM or import.meta diagnostics.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="tsconfig_json_compiler_options_json_set"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.tsconfig_rootdir",
                source_tool=TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL,
                language="typescript",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("typescript_ts6059",),
                message_terms=("rootDir",),
                risk_level="medium",
                description="Widens TypeScript rootDir when compiler-included test sources sit outside src/.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="tsconfig_json_rootdir_json_set"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.tsconfig_es2021_lib",
                source_tool=TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL,
                language="typescript",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("typescript_ts2550",),
                raw_terms=("replaceall",),
                risk_level="medium",
                description="Raises TypeScript compiler lib/target when generated code uses ES2021 built-ins.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="tsconfig_json_compiler_options_json_set"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.import_meta_module_option",
                source_tool=TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL,
                language="typescript",
                phase="dependency_resolution",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("typescript_ts1343",),
                message_terms=("import.meta", "module"),
                risk_level="medium",
                description="Aligns tsconfig module option when generated TypeScript uses import.meta.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="tsconfig_json_compiler_options_json_set"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.uninitialized_property",
                source_tool=TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.NULLABLE_TYPE_MISMATCH,
                priority=1,
                diagnostic_codes=("typescript_ts2564",),
                message_terms=("not definitely assigned",),
                risk_level="low",
                description="Adds conservative defaults for generated TypeScript properties reported as uninitialized.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="uninitialized_property_line_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.unique_export_import",
                source_tool=TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
                language="typescript",
                phase="export_resolution",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=2,
                raw_terms=("unique export", "import"),
                risk_level="medium",
                description="Rewrites TypeScript imports when a unique export target can be identified.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="unique_export_import_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.duplicate_export_import_binding",
                source_tool=TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
                language="typescript",
                phase="export_resolution",
                archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
                priority=1,
                diagnostic_codes=("typescript_ts2300",),
                message_terms=("duplicate identifier",),
                risk_level="low",
                description=(
                    "Removes duplicate TypeScript value re-export bindings when the same symbol is imported "
                    "and locally exported by the barrel file."
                ),
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="duplicate_export_import_binding_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.unused_import",
                source_tool=TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=2,
                raw_terms=("unused import",),
                risk_level="low",
                description="Removes generated unused TypeScript import residue through span-based text replacement.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="unused_import_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.unused_parameter",
                source_tool=TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=1,
                diagnostic_codes=("typescript_ts6133",),
                message_terms=("declared", "never read"),
                risk_level="low",
                description=(
                    "Prefixes generated unused TypeScript parameters with an underscore so strict builds "
                    "can distinguish intentional API placeholders from unread locals."
                ),
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="unused_parameter_line_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.unresolved_relative_unused_import",
                source_tool=TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=2,
                raw_terms=("unresolved relative import",),
                risk_level="low",
                description="Removes generated unresolved TypeScript relative imports when no target can be resolved.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="unused_relative_import_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.zod_type_class_collision",
                source_tool=TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.GENERATED_RESIDUE,
                priority=1,
                raw_terms=("zod", "type", "class"),
                risk_level="low",
                description="Repairs generated TypeScript Zod type/class naming collisions.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="zod_type_class_collision_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.too_few_arguments",
                source_tool=TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.NULLABLE_TYPE_MISMATCH,
                priority=1,
                diagnostic_codes=("typescript_ts2554",),
                message_terms=("expected", "arguments"),
                risk_level="low",
                description="Covers generated TypeScript calls with too few arguments.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="too_few_arguments_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.unresolved_identifier",
                source_tool=TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL,
                language="typescript",
                phase="quality_repair",
                archetype=RepairArchetype.WRONG_IMPORT_PATH,
                priority=1,
                diagnostic_codes=("typescript_ts2304",),
                message_terms=("cannot find name",),
                risk_level="low",
                description="Covers generated TypeScript unresolved identifier repairs.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="unresolved_identifier_text_replace"),
            ),
            RepairRuleDefinition(
                rule_id="typescript.vitest_globals",
                source_tool=TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL,
                language="typescript",
                phase="test_contract",
                archetype=RepairArchetype.MISSING_DEPENDENCY,
                priority=1,
                diagnostic_codes=("typescript_ts2582",),
                message_terms=("cannot find name",),
                risk_level="medium",
                description="Covers Vitest global typing repairs for describe/it/expect diagnostics.",
                runtime_plan_available=True,
                metadata=_executable_runtime_metadata(scope="vitest_globals_import_and_manifest_repair"),
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


def _repair_source_tool_known(source_tool: str) -> bool:
    normalized = str(source_tool or "").strip()
    return deterministic_repair_source_tool_known(normalized) or normalized in _RUNTIME_MIGRATION_SOURCE_TOOLS


def _tuple_str(value: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or ()) if str(item or "").strip())


def _suggest_rule_family(diagnostic: RepairDiagnostic) -> str:
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
