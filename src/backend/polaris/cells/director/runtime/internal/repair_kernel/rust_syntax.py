"""Rust deterministic repair planners owned by Director Runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

import tomllib

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

RUST_CRATE_IMPORT_SOURCE_TOOL = "deterministic_rust_crate_import_repair"
RUST_CRATE_IMPORT_REWRITE_SOURCE_TOOL = "deterministic_rust_crate_import_rewrite_repair"
RUST_DEPENDENCY_SOURCE_TOOL = "deterministic_rust_dependency_repair"
RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL = "deterministic_rust_duplicate_module_file_repair"
RUST_FIELD_RENAME_SUGGESTION_SOURCE_TOOL = "deterministic_rust_field_rename_suggestion_repair"
RUST_INCOMPATIBLE_COPY_DERIVE_SOURCE_TOOL = "deterministic_rust_incompatible_copy_derive_repair"
RUST_LINE_SUGGESTION_SOURCE_TOOL = "deterministic_rust_line_suggestion_repair"
RUST_METHOD_SELF_SIGNATURE_SOURCE_TOOL = "deterministic_rust_method_self_signature_repair"
RUST_MISSING_BINARY_ENTRYPOINT_SOURCE_TOOL = "deterministic_rust_missing_binary_entrypoint_repair"
RUST_MISSING_MODULE_FILE_SOURCE_TOOL = "deterministic_rust_missing_module_file_repair"
RUST_MISSING_TRAIT_DERIVE_SOURCE_TOOL = "deterministic_rust_derive_repair"
RUST_POST_SOURCE_TOOL = "deterministic_rust_post_repair"
RUST_SERDE_DERIVE_SOURCE_TOOL = "deterministic_rust_serde_derive_repair"
RUST_TRAIT_IMPORT_SOURCE_TOOL = "deterministic_rust_trait_import_repair"
RUST_UNUSED_IMPORT_SOURCE_TOOL = "deterministic_rust_unused_import_repair"
RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL = "deterministic_rust_unresolved_pub_use_repair"
RUST_WRONG_CRATE_PATH_SOURCE_TOOL = "deterministic_rust_wrong_crate_path_repair"
RUST_MISSING_MODULE_FILE_STUB = (
    "// Polaris marker: rust.missing_module_file\n// Created from rustc E0583 as an empty module topology stub.\n"
)

_RUST_UNRESOLVED_IMPORT_RE = re.compile(
    r"unresolved import [`'\"](?P<import>[A-Za-z_][A-Za-z0-9_:]*)[`'\"]",
    re.IGNORECASE,
)
_RUST_UNRESOLVED_CRATE_RE = re.compile(
    r"(?:cannot find (?:module or )?crate|use of unresolved module or unlinked crate) "
    r"[`'\"](?P<crate>[A-Za-z_][A-Za-z0-9_]*)[`'\"]",
    re.IGNORECASE,
)
_RUST_SERDE_DERIVE_SUGGESTION_RE = re.compile(
    r"consider adding [`'\"]#\[derive\(serde::(?P<trait>Serialize|Deserialize)\)\][`'\"] "
    r"to your [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)::(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"] type",
    re.IGNORECASE,
)
_RUST_MISSING_TRAIT_BOUND_RE = re.compile(
    r"the trait bound [`'\"](?:[A-Za-z_][A-Za-z0-9_]*::)*(?P<symbol>[A-Za-z_][A-Za-z0-9_]*):\s*"
    r"(?P<trait>(?:[A-Za-z_][A-Za-z0-9_]*::)*[A-Za-z_][A-Za-z0-9_]*)[`'\"] is not satisfied",
    re.IGNORECASE,
)
_RUST_DERIVABLE_TRAIT_NAMES = frozenset(
    {
        "Clone",
        "Copy",
        "Debug",
        "Default",
        "Eq",
        "Hash",
        "Ord",
        "PartialEq",
        "PartialOrd",
    }
)
_KNOWN_RUST_DEPENDENCIES: dict[str, str] = {
    "serde": 'serde = { version = "1.0", features = ["derive"] }',
    "serde_json": 'serde_json = "1.0"',
}
_RUST_METHOD_SELF_LOCATION_RE = re.compile(
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE | re.MULTILINE,
)
_RUST_LOCATION_RE = re.compile(
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE,
)
_RUST_MISSING_MODULE_FILE_RE = re.compile(
    r"file not found for module [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)[`'\"]",
    re.IGNORECASE,
)
_RUST_E0583_HELP_LINE_RE = re.compile(
    r"to create the module [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)[`'\"].*?"
    r"create file (?P<candidates>[^\n]+)",
    re.IGNORECASE,
)
_RUST_QUOTED_RS_PATH_RE = re.compile(r'"(?P<path>[^"\n]+\.rs)"', re.IGNORECASE)
_RUST_DUPLICATE_MODULE_FILE_RE = re.compile(
    r"(?:error\[E0761\]:\s*)?file for module [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)[`'\"]\s+"
    r"found at both [`'\"](?P<first>[^`'\"\n]+\.rs)[`'\"]\s+and\s+"
    r"[`'\"](?P<second>[^`'\"\n]+\.rs)[`'\"]",
    re.IGNORECASE,
)
_RUST_INCOMPATIBLE_COPY_LOCATION_RE = re.compile(
    r"the trait [`'\"]Copy[`'\"] cannot be implemented.*?"
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_RUST_DERIVE_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)#\[derive\((?P<items>[^)\r\n]*)\)\](?P<trailing>[^\r\n]*)(?P<newline>\r\n|\n|\r)?$"
)
_RUST_COPY_DERIVE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])Copy(?![A-Za-z0-9_])")
_RUST_NO_SYMBOL_RE = re.compile(
    r"no [`'\"](?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"] in [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_:]*)[`'\"]",
    re.IGNORECASE,
)
_RUST_PUB_USE_STATEMENT_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)pub\s+use\s+(?P<path>[A-Za-z_][A-Za-z0-9_:]*)::"
    r"(?P<tail>[^;\n]+);[ \t]*(?P<newline>\n?)"
)
_RUST_METHOD_SELF_SIGNATURE_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\(\s*&mut\s*\)"), "(&mut self)", "mut_self"),
    (re.compile(r"\(\s*&\s*\)"), "(&self)", "self"),
)
_RUST_FIELD_METHOD_LINE_SUGGESTION_RE = re.compile(
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):\d+.*?"
    r"help:\s+one of the expressions' fields has a method of the same name.*?"
    r"^\s*(?P=line)\s+\|\s(?P<code>[^\n]+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_RUST_FULL_LINE_SUGGESTION_RE = re.compile(
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):\d+.*?"
    r"help:\s+(?:consider borrowing here|try dereferencing|consider removing the borrow).*?"
    r"^\s*(?P=line)\s+\|\s(?P<code>[^\n]+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_RUST_FIELD_RENAME_ERROR_RE = re.compile(
    r"error\[E0609\]:\s*no field [`'\"](?P<wrong>[A-Za-z_][A-Za-z0-9_]*)[`'\"]"
    r".*?^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_RUST_FIELD_RENAME_PLUS_LINE_RE = re.compile(
    r"^\s*(?P<line>\d+)\s+\+\s(?P<code>[^\n]+)",
    re.MULTILINE,
)
_RUST_FIELD_ACCESS_RE = re.compile(r"\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)\b")
_RUST_USE_IMPORT_LINE_RE = re.compile(r"^use\s+[^;\r\n]+;$")
_RUST_USE_IMPORT_IN_TEXT_RE = re.compile(r"\b(?P<import>use\s+[^;\r\n]+;)")
_RUST_UNUSED_IMPORT_RE = re.compile(
    r"warning:\s*unused\s+import:\s*[`'\"](?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"].*?"
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_RUST_REAL_ITEM_RE = re.compile(
    r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|unsafe\s+|extern\s+)*"
    r"(?:struct|enum|trait|impl|fn|mod|use|const|static|type|macro_rules!)\b"
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def build_rust_crate_import_rewrite_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build span-based edits rewriting wrong local crate prefixes."""

    return _build_rust_crate_import_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=RUST_CRATE_IMPORT_REWRITE_SOURCE_TOOL,
        rule_id="rust.crate_import_rewrite",
        repair_kind="rust_crate_import_rewrite",
        depends_on=("rust.unlinked_crate_dependency",),
    )


def build_rust_crate_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build span-based edits for the legacy Rust crate import source tool."""

    return _build_rust_crate_import_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=RUST_CRATE_IMPORT_SOURCE_TOOL,
        rule_id="rust.unresolved_import_path",
        repair_kind="rust_crate_import_path",
        depends_on=(),
    )


def _build_rust_crate_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
    source_tool: str,
    rule_id: str,
    repair_kind: str,
    depends_on: Sequence[str],
) -> RepairPlan | None:
    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    cargo = _read_cargo_manifest_from_base(normalized_base)
    if not cargo:
        return None
    canonical_crate = _canonical_rust_crate_name(cargo)
    if not canonical_crate:
        return None

    missing_crates = _parse_unresolved_rust_crates(diagnostics)
    if not missing_crates:
        return None

    declared_dependencies = _declared_rust_dependencies(cargo)
    has_local_lib = _cargo_declares_local_rust_lib(normalized_base, cargo)
    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen_spans: set[tuple[str, int, int]] = set()
    for missing_crate, diagnostic in missing_crates:
        if missing_crate == canonical_crate or missing_crate in declared_dependencies:
            continue
        if not _rust_crate_names_look_related(missing_crate, canonical_crate) and not (
            has_local_lib and _rust_crate_prefix_used_in_binary_entrypoint(normalized_base, missing_crate)
        ):
            continue
        diagnostic_planned = False
        for operation in _rust_crate_import_rewrite_operations(
            base_files=normalized_base,
            missing_crate=missing_crate,
            canonical_crate=canonical_crate,
            diagnostic=diagnostic,
        ):
            span_key = (operation.path, int(operation.span_start or 0), int(operation.span_end or 0))
            if span_key in seen_spans:
                continue
            seen_spans.add(span_key)
            operations.append(operation)
            diagnostic_planned = True
        if diagnostic_planned:
            planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id=rule_id,
        source_tool=source_tool,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="low",
        priority=0,
        depends_on=tuple(depends_on),
        metadata={
            "repair_kind": repair_kind,
            "edit_strategy": "text_replace",
            "span_based": True,
            "canonical_crate": canonical_crate,
            "diagnostic_count": len(planned_diagnostics),
        },
    )


def build_rust_dependency_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical plan for known missing Rust dependencies."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    cargo_text = normalized_base.get("Cargo.toml")
    if cargo_text is None:
        return None

    packages = _rust_dependency_packages_to_add(normalized_base, diagnostics)
    missing = [package for package in packages if not _cargo_dependency_declared(cargo_text, package)]
    if not missing:
        return None

    repaired = cargo_text
    for package in missing:
        repaired = _insert_cargo_dependency(repaired, _KNOWN_RUST_DEPENDENCIES[package])
    if repaired == cargo_text:
        return None

    return RepairPlan(
        rule_id="rust.unlinked_crate_dependency",
        source_tool=RUST_DEPENDENCY_SOURCE_TOOL,
        operations=(
            RepairOperation(
                kind="write_file",
                path="Cargo.toml",
                content=repaired,
                before_hash=sha256_text(cargo_text),
                metadata={"repair_kind": "rust_dependency", "packages": tuple(missing)},
            ),
        ),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="medium",
        priority=0,
    )


def build_rust_missing_binary_entrypoint_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build write-file operations for declared Rust binary targets missing an entrypoint."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    cargo_text = normalized_base.get("Cargo.toml")
    if cargo_text is None:
        return None
    cargo = _read_cargo_manifest_from_base(normalized_base)
    if not cargo:
        return None

    binary_paths = _declared_rust_binary_entrypoint_paths(cargo)
    missing_paths = tuple(
        path for path in binary_paths if _rust_binary_entrypoint_path_is_safe(path) and not normalized_base.get(path)
    )
    if not missing_paths:
        return None

    crate_name = _canonical_rust_crate_name(cargo) or "app"
    operations = tuple(
        RepairOperation(
            kind="write_file",
            path=path,
            content=_rust_missing_binary_entrypoint_stub(crate_name),
            before_hash=sha256_text(normalized_base.get(path, "")),
            metadata={
                "repair_kind": "rust_missing_binary_entrypoint",
                "edit_strategy": "write_file",
                "write_file_reason": "new_file_or_empty_file",
                "create_file_rollback_strategy": "restore_empty_before_content_via_policy_gated_writer",
                "declared_in": "Cargo.toml",
                "crate_name": crate_name,
            },
        )
        for path in missing_paths
    )
    return RepairPlan(
        rule_id="rust.missing_binary_entrypoint",
        source_tool=RUST_MISSING_BINARY_ENTRYPOINT_SOURCE_TOOL,
        operations=operations,
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "repair_kind": "rust_missing_binary_entrypoint",
            "edit_strategy": "write_file",
            "write_file_reason": "new_file_or_empty_file",
            "create_file_rollback_strategy": "restore_empty_before_content_via_policy_gated_writer",
            "declared_binary_paths": missing_paths,
            "diagnostic_count": len(tuple(diagnostics or ())),
        },
    )


def build_rust_duplicate_module_file_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a shadow/composition-only delete plan for Rust E0761 duplicate modules."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    if not diagnostics:
        return None

    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen_delete_paths: set[str] = set()
    for module_name, first_path, second_path, diagnostic in _parse_rust_duplicate_module_file_targets(diagnostics):
        first_content = normalized_base.get(first_path)
        second_content = normalized_base.get(second_path)
        if first_content is None or second_content is None:
            continue

        candidate = _rust_duplicate_module_delete_candidate(
            first_path=first_path,
            first_content=first_content,
            second_path=second_path,
            second_content=second_content,
        )
        if candidate is None:
            continue
        delete_path, sibling_path, evidence_kind = candidate
        if delete_path in seen_delete_paths:
            continue
        seen_delete_paths.add(delete_path)
        operations.append(
            RepairOperation(
                kind="delete_file",
                path=delete_path,
                before_hash=sha256_text(normalized_base[delete_path]),
                metadata={
                    "repair_kind": "rust_duplicate_module_file",
                    "edit_strategy": "delete_file",
                    "delete_file_reason": "rust_e0761_duplicate_module_file",
                    "delete_candidate_evidence": evidence_kind,
                    "module_name": module_name,
                    "duplicate_paths": (first_path, second_path),
                    "sibling_path": sibling_path,
                    "diagnostic_id": diagnostic.diagnostic_id,
                    "execution_authority": "director_runtime",
                    "delete_file_global_validation_required": True,
                },
            )
        )
        planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id="rust.duplicate_module_file",
        source_tool=RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="medium",
        priority=2,
        metadata={
            "repair_kind": "rust_duplicate_module_file",
            "edit_strategy": "delete_file",
            "diagnostic_code": "rust_e0761",
            "diagnostic_count": len(planned_diagnostics),
            "execution_authority": "director_runtime",
            "runtime_plan_available": True,
            "delete_file_global_validation_required": True,
        },
    )


def build_rust_missing_module_file_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build comment-only module file stubs from rustc E0583 help candidates."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen_paths: set[str] = set()
    for diagnostic in diagnostics:
        candidate = _rust_missing_module_file_candidate(
            base_files=normalized_base,
            diagnostic=diagnostic,
        )
        if candidate is None:
            continue
        path, module_name = candidate
        if path in seen_paths:
            continue
        seen_paths.add(path)
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=RUST_MISSING_MODULE_FILE_STUB,
                before_hash=sha256_text(""),
                metadata={
                    "repair_kind": "rust_missing_module_file",
                    "edit_strategy": "write_file",
                    "write_file_reason": "new_file_or_empty_file",
                    "create_file_rollback_strategy": "delete_created_file_via_repair_executor",
                    "module_name": module_name,
                    "declaring_file": diagnostic.path or "",
                    "declaring_line": diagnostic.line,
                    "rustc_help_candidate": True,
                    "comment_only_stub": True,
                    "symbol_generation": False,
                },
            )
        )
        planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id="rust.missing_module_file",
        source_tool=RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "repair_kind": "rust_missing_module_file",
            "edit_strategy": "write_file",
            "write_file_reason": "new_file_or_empty_file",
            "comment_only_stub": True,
            "symbol_generation": False,
            "diagnostic_count": len(planned_diagnostics),
        },
    )


def build_rust_serde_derive_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build span-based edits that add missing serde derives to Rust structs/enums."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    if not diagnostics:
        return None

    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen_spans: set[tuple[str, int, int]] = set()
    for module, symbol, traits, diagnostic in _parse_rust_serde_derive_targets(diagnostics):
        candidate = _rust_file_for_module_symbol(
            base_files=normalized_base,
            module=module,
            symbol=symbol,
        )
        if candidate is None:
            continue
        path, content = candidate
        operation = _rust_serde_derive_operation(
            path=path,
            content=content,
            symbol=symbol,
            traits=traits,
            diagnostic=diagnostic,
        )
        if operation is None:
            continue
        span_key = (operation.path, int(operation.span_start or 0), int(operation.span_end or 0))
        if span_key in seen_spans:
            continue
        seen_spans.add(span_key)
        operations.append(operation)
        planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id="rust.serde_derive",
        source_tool=RUST_SERDE_DERIVE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "repair_kind": "rust_serde_derive",
            "edit_strategy": "text_replace",
            "span_based": True,
            "diagnostic_count": len(planned_diagnostics),
        },
    )


def build_rust_method_self_signature_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a span-based plan for Rust method receivers missing ``self``."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    if not diagnostics:
        return None

    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen_spans: set[tuple[str, int, int]] = set()
    for diagnostic in diagnostics:
        location = _rust_method_self_signature_location(diagnostic)
        if location is None:
            continue
        path, line_number = location
        content = normalized_base.get(path)
        if content is None:
            continue
        operation = _rust_method_self_signature_operation(
            path=path,
            content=content,
            line_number=line_number,
            diagnostic=diagnostic,
        )
        if operation is None:
            continue
        span_key = (operation.path, int(operation.span_start or 0), int(operation.span_end or 0))
        if span_key in seen_spans:
            continue
        seen_spans.add(span_key)
        operations.append(operation)
        planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id="rust.method_self_signature",
        source_tool=RUST_METHOD_SELF_SIGNATURE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "repair_kind": "rust_method_self_signature",
            "edit_strategy": "span_text_replace",
            "diagnostic_count": len(planned_diagnostics),
        },
    )


def build_rust_line_suggestion_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a span-based plan for Rust compiler single-line help suggestions."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    if not diagnostics:
        return None

    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen_spans: set[tuple[str, int, int]] = set()
    for diagnostic in diagnostics:
        diagnostic_planned = False
        for path, line_number, code in _parse_rust_line_suggestions((diagnostic,)):
            content = normalized_base.get(path)
            if content is None:
                continue
            operation = _rust_line_suggestion_operation(
                path=path,
                content=content,
                line_number=line_number,
                code=code,
                diagnostic=diagnostic,
            )
            if operation is None:
                continue
            span_key = (operation.path, int(operation.span_start or 0), int(operation.span_end or 0))
            if span_key in seen_spans:
                continue
            seen_spans.add(span_key)
            operations.append(operation)
            diagnostic_planned = True
        if diagnostic_planned:
            planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id="rust.line_suggestion",
        source_tool=RUST_LINE_SUGGESTION_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="low",
        priority=3,
        metadata={
            "repair_kind": "rust_line_suggestion",
            "edit_strategy": "text_replace",
            "span_based": True,
            "diagnostic_count": len(planned_diagnostics),
        },
    )


def build_rust_field_rename_suggestion_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build span-based edits for Rust E0609 field rename suggestions."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    if not diagnostics:
        return None

    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen_spans: set[tuple[str, int, int]] = set()
    for diagnostic in diagnostics:
        diagnostic_planned = False
        for (
            path,
            line_number,
            column_number,
            wrong_field,
            correct_field,
            suggested_code,
        ) in _parse_rust_field_rename_suggestions((diagnostic,)):
            content = normalized_base.get(path)
            if content is None:
                continue
            operation = _rust_field_rename_suggestion_operation(
                path=path,
                content=content,
                line_number=line_number,
                column_number=column_number,
                wrong_field=wrong_field,
                correct_field=correct_field,
                suggested_code=suggested_code,
                diagnostic=diagnostic,
            )
            if operation is None:
                continue
            span_key = (operation.path, int(operation.span_start or 0), int(operation.span_end or 0))
            if span_key in seen_spans:
                continue
            seen_spans.add(span_key)
            operations.append(operation)
            diagnostic_planned = True
        if diagnostic_planned:
            planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id="rust.field_rename_suggestion",
        source_tool=RUST_FIELD_RENAME_SUGGESTION_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="low",
        priority=2,
        metadata={
            "repair_kind": "rust_field_rename_suggestion",
            "edit_strategy": "text_replace",
            "span_based": True,
            "diagnostic_count": len(planned_diagnostics),
        },
    )


def build_rust_missing_trait_derive_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build span-based edits that add ordinary missing trait derives to Rust structs."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    if not diagnostics:
        return None

    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen_spans: set[tuple[str, int, int, str]] = set()
    for symbol, traits, diagnostic in _parse_rust_missing_trait_derive_targets(diagnostics):
        diagnostic_planned = False
        for path, content in _rust_missing_trait_derive_candidate_files(
            base_files=normalized_base,
            diagnostic=diagnostic,
        ):
            operation = _rust_missing_trait_derive_operation(
                path=path,
                content=content,
                symbol=symbol,
                traits=traits,
                diagnostic=diagnostic,
            )
            if operation is None:
                continue
            span_key = (
                operation.path,
                int(operation.span_start or 0),
                int(operation.span_end or 0),
                ",".join(operation.metadata.get("traits_added") or ()),
            )
            if span_key in seen_spans:
                continue
            seen_spans.add(span_key)
            operations.append(operation)
            diagnostic_planned = True
            break
        if diagnostic_planned:
            planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id="rust.missing_trait_derive",
        source_tool=RUST_MISSING_TRAIT_DERIVE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "repair_kind": "rust_missing_trait_derive",
            "edit_strategy": "text_replace",
            "span_based": True,
            "diagnostic_count": len(planned_diagnostics),
        },
    )


def _rust_missing_trait_derive_candidate_files(
    *,
    base_files: Mapping[str, str],
    diagnostic: RepairDiagnostic,
) -> tuple[tuple[str, str], ...]:
    diagnostic_path = _normalize_repair_path(str(diagnostic.path or ""))
    candidates: list[tuple[str, str]] = []
    if diagnostic_path.endswith(".rs") and diagnostic_path in base_files:
        candidates.append((diagnostic_path, base_files[diagnostic_path]))
    for path, content in sorted(base_files.items()):
        if not path.endswith(".rs") or path == diagnostic_path:
            continue
        candidates.append((path, content))
    return tuple(candidates)


def build_rust_incompatible_copy_derive_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a span-based plan that removes invalid Rust ``Copy`` derive tokens."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    if not diagnostics:
        return None

    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen_spans: set[tuple[str, int, int]] = set()
    for diagnostic in diagnostics:
        diagnostic_planned = False
        for path, line_number in _parse_rust_incompatible_copy_derive_locations((diagnostic,)):
            content = normalized_base.get(path)
            if content is None:
                continue
            operation = _rust_incompatible_copy_derive_operation(
                path=path,
                content=content,
                line_number=line_number,
                diagnostic=diagnostic,
            )
            if operation is None:
                continue
            span_key = (operation.path, int(operation.span_start or 0), int(operation.span_end or 0))
            if span_key in seen_spans:
                continue
            seen_spans.add(span_key)
            operations.append(operation)
            diagnostic_planned = True
        if diagnostic_planned:
            planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id="rust.incompatible_copy_derive",
        source_tool=RUST_INCOMPATIBLE_COPY_DERIVE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "repair_kind": "rust_incompatible_copy_derive",
            "edit_strategy": "text_replace",
            "span_based": True,
            "diagnostic_count": len(planned_diagnostics),
        },
    )


def build_rust_wrong_crate_path_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a span-based plan for cargo wrong crate path suggestions."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    if not diagnostics:
        return None

    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen_spans: set[tuple[str, int, int]] = set()
    for diagnostic in diagnostics:
        diagnostic_planned = False
        for path, line_number, suggestion in _parse_rust_wrong_crate_path_suggestions((diagnostic,)):
            content = normalized_base.get(path)
            if content is None:
                continue
            operation = _rust_wrong_crate_path_operation(
                path=path,
                content=content,
                line_number=line_number,
                suggestion=suggestion,
                diagnostic=diagnostic,
            )
            if operation is None:
                continue
            span_key = (operation.path, int(operation.span_start or 0), int(operation.span_end or 0))
            if span_key in seen_spans:
                continue
            seen_spans.add(span_key)
            operations.append(operation)
            diagnostic_planned = True
        if diagnostic_planned:
            planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id="rust.wrong_crate_path",
        source_tool=RUST_WRONG_CRATE_PATH_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="low",
        priority=0,
        depends_on=("rust.unlinked_crate_dependency",),
        metadata={
            "repair_kind": "rust_wrong_crate_path",
            "edit_strategy": "text_replace",
            "span_based": True,
            "diagnostic_count": len(planned_diagnostics),
        },
    )


def build_rust_trait_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a span-based plan for Rust trait import suggestions."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    if not diagnostics:
        return None

    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        diagnostic_planned = False
        for path, import_line in _parse_rust_trait_import_suggestions((diagnostic,)):
            if path not in normalized_base or not path.endswith(".rs"):
                continue
            key = (path, import_line)
            if key in seen:
                continue
            operation = _rust_trait_import_operation(
                path=path,
                content=normalized_base[path],
                import_line=import_line,
                diagnostic=diagnostic,
            )
            if operation is None:
                continue
            seen.add(key)
            operations.append(operation)
            diagnostic_planned = True
        if diagnostic_planned:
            planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id="rust.trait_import",
        source_tool=RUST_TRAIT_IMPORT_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="low",
        priority=3,
        metadata={
            "repair_kind": "rust_trait_import",
            "edit_strategy": "text_replace",
            "span_based": True,
            "diagnostic_count": len(planned_diagnostics),
        },
    )


def build_rust_unused_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a span-based plan for Rust unused import warnings."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    if not diagnostics:
        return None

    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen_spans: set[tuple[str, int, int]] = set()
    for diagnostic in diagnostics:
        diagnostic_planned = False
        for path, line_number, symbol in _parse_rust_unused_import_warnings((diagnostic,)):
            if path not in normalized_base or not path.endswith(".rs"):
                continue
            operation = _rust_unused_import_operation(
                path=path,
                content=normalized_base[path],
                line_number=line_number,
                symbol=symbol,
                diagnostic=diagnostic,
            )
            if operation is None:
                continue
            span_key = (operation.path, int(operation.span_start or 0), int(operation.span_end or 0))
            if span_key in seen_spans:
                continue
            seen_spans.add(span_key)
            operations.append(operation)
            diagnostic_planned = True
        if diagnostic_planned:
            planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id="rust.unused_import",
        source_tool=RUST_UNUSED_IMPORT_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="low",
        priority=2,
        metadata={
            "repair_kind": "rust_unused_import",
            "edit_strategy": "text_replace",
            "span_based": True,
            "diagnostic_count": len(planned_diagnostics),
        },
    )


def build_rust_unresolved_pub_use_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a span-based plan that removes stale Rust public re-exports."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    if not diagnostics:
        return None

    missing_symbols = _rust_unresolved_pub_use_symbols(diagnostics)
    if not missing_symbols:
        return None

    operations: list[RepairOperation] = []
    for path, content in sorted(normalized_base.items()):
        if not path.endswith(".rs") or "/target/" in f"/{path}/":
            continue
        operations.extend(
            _rust_unresolved_pub_use_operations(
                path=path,
                content=content,
                missing_symbols=missing_symbols,
            )
        )

    if not operations:
        return None

    return RepairPlan(
        rule_id="rust.unresolved_pub_use",
        source_tool=RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="medium",
        priority=2,
        metadata={
            "repair_kind": "rust_unresolved_pub_use",
            "edit_strategy": "span_text_replace",
            "span_based": True,
            "symbols": tuple(missing_symbols),
        },
    )


def _rust_dependency_packages_to_add(
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> list[str]:
    packages: list[str] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        for match in _RUST_UNRESOLVED_IMPORT_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            root = match.group("import").split("::", 1)[0]
            if root in _KNOWN_RUST_DEPENDENCIES and root not in seen:
                seen.add(root)
                packages.append(root)

    source_text = "\n".join(
        content for path, content in sorted(base_files.items()) if path.endswith(".rs") and "/target/" not in path
    )
    if "serde_json::" in source_text and "serde_json" not in seen:
        seen.add("serde_json")
        packages.append("serde_json")
    return packages


def _rust_unresolved_pub_use_symbols(diagnostics: Sequence[RepairDiagnostic]) -> tuple[str, ...]:
    symbols: list[str] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        if not _rust_unresolved_pub_use_diagnostic_path_is_safe(diagnostic):
            continue
        text = f"{diagnostic.message}\n{diagnostic.raw}"
        candidates: list[str] = []
        for match in _RUST_NO_SYMBOL_RE.finditer(text):
            candidates.append(match.group("symbol"))
        for match in _RUST_UNRESOLVED_IMPORT_RE.finditer(text):
            imported = str(match.group("import") or "")
            if "::" in imported:
                candidates.append(imported.rsplit("::", 1)[-1])
        for candidate in candidates:
            symbol = str(candidate or "").strip()
            if not _is_rust_identifier(symbol) or symbol in seen:
                continue
            seen.add(symbol)
            symbols.append(symbol)
    return tuple(symbols)


def _rust_unresolved_pub_use_diagnostic_path_is_safe(diagnostic: RepairDiagnostic) -> bool:
    path = _normalize_repair_path(str(diagnostic.path or ""))
    if diagnostic.path and not path:
        return False
    location = _RUST_METHOD_SELF_LOCATION_RE.search(str(diagnostic.raw or diagnostic.message or ""))
    return not (location is not None and not _normalize_repair_path(location.group("path")))


def _rust_unresolved_pub_use_operations(
    *,
    path: str,
    content: str,
    missing_symbols: Sequence[str],
) -> list[RepairOperation]:
    missing = set(missing_symbols)
    operations: list[RepairOperation] = []
    for match in _RUST_PUB_USE_STATEMENT_RE.finditer(content):
        replacement, removed = _repair_rust_pub_use_statement(match.group(0), missing)
        if not removed or replacement == match.group(0):
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=match.start(),
                span_end=match.end(),
                expected=match.group(0),
                replacement=replacement,
                before_hash=sha256_text(content),
                metadata={
                    "repair_kind": "rust_unresolved_pub_use",
                    "edit_strategy": "span_text_replace",
                    "span_based": True,
                    "symbols_removed": tuple(removed),
                    "unique_context": match.group(0),
                },
            )
        )
    return operations


def _repair_rust_pub_use_statement(statement: str, missing_symbols: set[str]) -> tuple[str, tuple[str, ...]]:
    match = _RUST_PUB_USE_STATEMENT_RE.match(statement)
    if match is None:
        return statement, ()
    tail = str(match.group("tail") or "").strip()
    newline = str(match.group("newline") or "")
    if tail.startswith("{") and tail.endswith("}"):
        items = [item.strip() for item in tail[1:-1].split(",") if item.strip()]
        kept: list[str] = []
        removed: list[str] = []
        for item in items:
            symbol = item.split(" as ", 1)[0].strip()
            if symbol in missing_symbols:
                removed.append(symbol)
            else:
                kept.append(item)
        if not removed:
            return statement, ()
        if not kept:
            return "", tuple(removed)
        replacement = f"{match.group('indent')}pub use {match.group('path')}::{{{', '.join(kept)}}};{newline}"
        return replacement, tuple(removed)

    symbol = tail.split(" as ", 1)[0].strip()
    if symbol in missing_symbols:
        return "", (symbol,)
    return statement, ()


def _parse_rust_serde_derive_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[str, str, frozenset[str], RepairDiagnostic], ...]:
    targets: dict[tuple[str, str], tuple[set[str], RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        text = _ANSI_ESCAPE_RE.sub("", f"{diagnostic.message}\n{diagnostic.raw}")
        for match in _RUST_SERDE_DERIVE_SUGGESTION_RE.finditer(text):
            module = str(match.group("module") or "").strip()
            symbol = str(match.group("symbol") or "").strip()
            trait = str(match.group("trait") or "").strip()
            if not _is_rust_identifier(module) or not _is_rust_identifier(symbol):
                continue
            if trait not in {"Serialize", "Deserialize"}:
                continue
            traits, first_diagnostic = targets.setdefault((module, symbol), (set(), diagnostic))
            traits.add(f"serde::{trait}")
            targets[(module, symbol)] = (traits, first_diagnostic)
    return tuple(
        (module, symbol, frozenset(sorted(traits)), diagnostic)
        for (module, symbol), (traits, diagnostic) in targets.items()
    )


def _parse_rust_missing_trait_derive_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[str, frozenset[str], RepairDiagnostic], ...]:
    targets: dict[str, tuple[set[str], RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        text = _ANSI_ESCAPE_RE.sub("", f"{diagnostic.message}\n{diagnostic.raw}")
        for match in _RUST_MISSING_TRAIT_BOUND_RE.finditer(text):
            symbol = str(match.group("symbol") or "").strip()
            trait = str(match.group("trait") or "").strip()
            trait_name = trait.rsplit("::", 1)[-1]
            if not _is_rust_identifier(symbol) or not _is_rust_identifier(trait_name):
                continue
            if trait_name in {"Serialize", "Deserialize"} or trait.startswith("serde::"):
                continue
            if trait_name not in _RUST_DERIVABLE_TRAIT_NAMES:
                continue
            traits, first_diagnostic = targets.setdefault(symbol, (set(), diagnostic))
            traits.add(trait_name)
            targets[symbol] = (traits, first_diagnostic)
    return tuple((symbol, frozenset(sorted(traits)), diagnostic) for symbol, (traits, diagnostic) in targets.items())


def _rust_file_for_module_symbol(
    *,
    base_files: Mapping[str, str],
    module: str,
    symbol: str,
) -> tuple[str, str] | None:
    symbol_pattern = re.compile(rf"(?m)^\s*(?:pub\s+)?(?:struct|enum)\s+{re.escape(symbol)}\b")
    candidates: list[tuple[str, str]] = []
    for path, content in sorted(base_files.items()):
        if not path.endswith(".rs") or not path.startswith("src/") or "/target/" in f"/{path}/":
            continue
        if path.rsplit("/", 1)[-1].rsplit(".", 1)[0] == module:
            candidates.insert(0, (path, content))
        else:
            candidates.append((path, content))
    for path, content in candidates:
        if symbol_pattern.search(content):
            return path, content
    return None


def _rust_missing_trait_derive_operation(
    *,
    path: str,
    content: str,
    symbol: str,
    traits: frozenset[str],
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs") or not _is_rust_identifier(symbol) or not traits:
        return None
    lines = content.splitlines(keepends=True)
    declaration_re = re.compile(rf"^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+{re.escape(symbol)}\b")
    for item_index, line in enumerate(lines):
        if declaration_re.match(line) is None:
            continue
        derive_index = _rust_existing_derive_line_index(lines, item_index)
        if derive_index is not None:
            expected = lines[derive_index]
            replacement, added = _add_rust_derive_traits_to_line(expected, traits)
            target_index = derive_index
            struct_line = line
            unique_context = f"{expected}{struct_line}"
        else:
            expected = line
            indent = line[: len(line) - len(line.lstrip())]
            newline = _line_ending(line) or "\n"
            replacement = f"{indent}#[derive({', '.join(sorted(traits))})]{newline}{line}"
            added = len(traits)
            target_index = item_index
            unique_context = expected
        if added <= 0 or replacement == expected:
            return None
        line_start = sum(len(item) for item in lines[:target_index])
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=line_start,
            span_end=line_start + len(expected),
            expected=expected,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "rust_missing_trait_derive",
                "edit_strategy": "text_replace",
                "span_based": True,
                "symbol": symbol,
                "struct_name": symbol,
                "traits_added": tuple(sorted(traits)),
                "derive_line_existing": derive_index is not None,
                "unique_context": unique_context,
                "diagnostic_id": diagnostic.diagnostic_id,
            },
        )
    return None


def _rust_serde_derive_operation(
    *,
    path: str,
    content: str,
    symbol: str,
    traits: frozenset[str],
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs") or not _is_rust_identifier(symbol) or not traits:
        return None
    lines = content.splitlines(keepends=True)
    declaration_re = re.compile(rf"^\s*(?:pub\s+)?(?:struct|enum)\s+{re.escape(symbol)}\b")
    for item_index, line in enumerate(lines):
        if declaration_re.match(line) is None:
            continue
        derive_index = _rust_existing_derive_line_index(lines, item_index)
        if derive_index is not None:
            expected = lines[derive_index]
            replacement, added = _add_rust_derive_traits_to_line(expected, traits)
            target_index = derive_index
        else:
            expected = line
            indent = line[: len(line) - len(line.lstrip())]
            newline = _line_ending(line) or "\n"
            replacement = f"{indent}#[derive({', '.join(sorted(traits))})]{newline}{line}"
            added = len(traits)
            target_index = item_index
        if added <= 0 or replacement == expected or content.count(expected) != 1:
            return None
        line_start = sum(len(item) for item in lines[:target_index])
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=line_start,
            span_end=line_start + len(expected),
            expected=expected,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "rust_serde_derive",
                "edit_strategy": "text_replace",
                "span_based": True,
                "symbol": symbol,
                "traits_added": tuple(sorted(traits)),
                "derive_line_existing": derive_index is not None,
                "unique_context": expected,
                "diagnostic_id": diagnostic.diagnostic_id,
            },
        )
    return None


def _rust_existing_derive_line_index(lines: Sequence[str], item_index: int) -> int | None:
    index = item_index - 1
    while index >= 0 and not lines[index].strip():
        index -= 1
    if index >= 0 and re.match(r"^\s*#\[derive\([^)]*\)\]\s*$", lines[index]):
        return index
    return None


def _add_rust_derive_traits_to_line(line: str, traits: frozenset[str]) -> tuple[str, int]:
    match = re.match(r"^(?P<indent>\s*)#\[derive\((?P<body>[^)]*)\)\](?P<newline>\r\n|\n|\r)?$", line)
    if match is None:
        return line, 0
    items = [item.strip() for item in str(match.group("body") or "").split(",") if item.strip()]
    added = 0
    for trait in sorted(traits):
        short = trait.rsplit("::", 1)[-1]
        if any(item in (trait, short) or item.endswith(f"::{short}") for item in items):
            continue
        items.append(trait)
        added += 1
    if added <= 0:
        return line, 0
    newline = match.group("newline") or ""
    return f"{match.group('indent')}#[derive({', '.join(items)})]{newline}", added


def _is_rust_identifier(value: str) -> bool:
    return re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(value or "")) is not None


def _cargo_dependency_declared(cargo_text: str, package: str) -> bool:
    return bool(re.search(rf"(?m)^\s*{re.escape(package)}\s*=", cargo_text))


def _insert_cargo_dependency(cargo_text: str, dependency_line: str) -> str:
    dependency_header = re.search(r"(?m)^\[dependencies\]\s*$", cargo_text)
    if not dependency_header:
        suffix = "" if cargo_text.endswith("\n") else "\n"
        return f"{cargo_text}{suffix}\n[dependencies]\n{dependency_line}\n"
    insert_at = dependency_header.end()
    return f"{cargo_text[:insert_at]}\n{dependency_line}{cargo_text[insert_at:]}"


def _rust_method_self_signature_location(diagnostic: RepairDiagnostic) -> tuple[str, int] | None:
    text = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    if "expected parameter name" not in text:
        return None
    path = _normalize_repair_path(str(diagnostic.path or ""))
    line = diagnostic.line
    if not path or line is None:
        match = _RUST_METHOD_SELF_LOCATION_RE.search(str(diagnostic.raw or diagnostic.message or ""))
        if match:
            path = _normalize_repair_path(match.group("path"))
            line = _to_int(match.group("line"))
    if not path or line is None or int(line) <= 0:
        return None
    return path, int(line)


def _rust_method_self_signature_operation(
    *,
    path: str,
    content: str,
    line_number: int,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    lines = content.splitlines(keepends=True)
    index = line_number - 1
    if index < 0 or index >= len(lines):
        return None
    line = lines[index]
    if "fn " not in line:
        return None
    line_start = sum(len(item) for item in lines[:index])
    for pattern, replacement, receiver_kind in _RUST_METHOD_SELF_SIGNATURE_PATTERNS:
        match = pattern.search(line)
        if match is None:
            continue
        start = line_start + match.start()
        end = line_start + match.end()
        expected = match.group(0)
        context_start = max(0, match.start() - 24)
        context_end = min(len(line), match.end() + 24)
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=start,
            span_end=end,
            expected=expected,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "rust_method_self_signature",
                "edit_strategy": "span_text_replace",
                "line": line_number,
                "receiver_kind": receiver_kind,
                "diagnostic_id": diagnostic.diagnostic_id,
                "unique_context": line[context_start:context_end],
            },
        )
    return None


def _parse_rust_line_suggestions(diagnostics: Sequence[RepairDiagnostic]) -> list[tuple[str, int, str]]:
    suggestions: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int, str]] = set()
    text = _ANSI_ESCAPE_RE.sub(
        "",
        "\n".join(str(diagnostic.raw or diagnostic.message or "") for diagnostic in diagnostics or ()),
    )
    for pattern in (_RUST_FIELD_METHOD_LINE_SUGGESTION_RE, _RUST_FULL_LINE_SUGGESTION_RE):
        for match in pattern.finditer(text):
            path = _normalize_repair_path(str(match.group("path") or ""))
            line_number = _to_int(match.group("line"))
            code = str(match.group("code") or "").rstrip()
            if not path or not path.endswith(".rs") or line_number is None or int(line_number) <= 0:
                continue
            if not code.strip():
                continue
            key = (path, int(line_number), code)
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(key)
    return suggestions


def _parse_rust_field_rename_suggestions(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[tuple[str, int, int, str, str, str]]:
    suggestions: list[tuple[str, int, int, str, str, str]] = []
    seen: set[tuple[str, int, str, str]] = set()
    text = _ANSI_ESCAPE_RE.sub(
        "",
        "\n".join(str(diagnostic.raw or diagnostic.message or "") for diagnostic in diagnostics or ()),
    )
    blocks = re.split(r"(?=error\[E\d+\])", text)
    for block in blocks:
        error_match = _RUST_FIELD_RENAME_ERROR_RE.search(block)
        if error_match is None:
            continue
        wrong_field = str(error_match.group("wrong") or "")
        if not _is_rust_identifier(wrong_field):
            continue
        path = _normalize_repair_path(str(error_match.group("path") or ""))
        line_number = _to_int(error_match.group("line"))
        column_number = _to_int(error_match.group("column"))
        if not path or not path.endswith(".rs") or line_number is None or int(line_number) <= 0:
            continue
        if column_number is None or int(column_number) <= 0:
            continue
        lower_block = block.lower()
        if "help:" not in lower_block or "similar name exists" not in lower_block:
            continue
        for plus_match in _RUST_FIELD_RENAME_PLUS_LINE_RE.finditer(block):
            plus_line_number = _to_int(plus_match.group("line"))
            if plus_line_number != int(line_number):
                continue
            suggested_code = str(plus_match.group("code") or "").rstrip()
            correct_field = _rust_field_rename_correct_field(
                wrong_field=wrong_field,
                suggested_code=suggested_code,
            )
            if not correct_field or correct_field == wrong_field:
                continue
            key = (path, int(line_number), wrong_field, correct_field)
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(
                (
                    path,
                    int(line_number),
                    int(column_number),
                    wrong_field,
                    correct_field,
                    suggested_code,
                )
            )
            break
    return suggestions


def _rust_field_rename_correct_field(*, wrong_field: str, suggested_code: str) -> str:
    if f".{wrong_field}" in suggested_code:
        return ""
    candidates = [
        str(match.group("field") or "")
        for match in _RUST_FIELD_ACCESS_RE.finditer(suggested_code)
        if _is_rust_identifier(str(match.group("field") or ""))
    ]
    if not candidates:
        return ""
    return candidates[-1]


def _parse_rust_trait_import_suggestions(diagnostics: Sequence[RepairDiagnostic]) -> list[tuple[str, str]]:
    suggestions: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    text = _ANSI_ESCAPE_RE.sub(
        "",
        "\n".join(str(diagnostic.raw or diagnostic.message or "") for diagnostic in diagnostics or ()),
    )
    current_path = ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        location = _RUST_LOCATION_RE.match(line)
        if location is not None:
            current_path = _normalize_repair_path(str(location.group("path") or ""))
            continue

        lower = line.lower()
        if "help:" not in lower or "trait" not in lower:
            continue
        if "is implemented but not in scope" not in lower and "perhaps you want to import it" not in lower:
            continue
        if "perhaps add a use for it" not in lower and "perhaps you want to import it" not in lower:
            continue

        import_line = _rust_import_line_from_suggestion_lines(lines[index : index + 8])
        if not current_path or not current_path.endswith(".rs") or not _is_strict_rust_use_import_line(import_line):
            continue
        key = (current_path, import_line)
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(key)
    return suggestions


def _parse_rust_wrong_crate_path_suggestions(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[tuple[str, int, str]]:
    suggestions: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int, str]] = set()
    text = _ANSI_ESCAPE_RE.sub(
        "",
        "\n".join(str(diagnostic.raw or diagnostic.message or "") for diagnostic in diagnostics or ()),
    )
    for block in re.split(r"(?=error\[E\d+\])", text):
        block_lines = block.splitlines()
        location = next(
            (_RUST_LOCATION_RE.match(line) for line in block_lines if _RUST_LOCATION_RE.match(line) is not None),
            None,
        )
        if location is None:
            continue
        path = _normalize_repair_path(str(location.group("path") or ""))
        line_number = _to_int(location.group("line"))
        if not path or not path.endswith(".rs") or line_number is None or int(line_number) <= 0:
            continue
        for index, line in enumerate(block_lines):
            lower = line.lower()
            if "help:" not in lower or "a similar path exists" not in lower:
                continue
            suggestion = _rust_import_line_from_suggestion_lines(block_lines[index : index + 8])
            if not _is_strict_rust_use_import_line(suggestion):
                continue
            key = (path, int(line_number), suggestion)
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(key)
            break
    return suggestions


def _parse_rust_unused_import_warnings(diagnostics: Sequence[RepairDiagnostic]) -> list[tuple[str, int, str]]:
    warnings: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int, str]] = set()
    text = _ANSI_ESCAPE_RE.sub(
        "",
        "\n".join(str(diagnostic.raw or diagnostic.message or "") for diagnostic in diagnostics or ()),
    )
    for match in _RUST_UNUSED_IMPORT_RE.finditer(text):
        path = _normalize_repair_path(str(match.group("path") or ""))
        line_number = _to_int(match.group("line"))
        symbol = str(match.group("symbol") or "").strip()
        if not path or not path.endswith(".rs") or line_number is None or int(line_number) <= 0:
            continue
        if not _is_rust_identifier(symbol):
            continue
        key = (path, int(line_number), symbol)
        if key in seen:
            continue
        seen.add(key)
        warnings.append(key)
    return warnings


def _parse_rust_incompatible_copy_derive_locations(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[tuple[str, int]]:
    locations: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    text = _ANSI_ESCAPE_RE.sub(
        "",
        "\n".join(str(diagnostic.raw or diagnostic.message or "") for diagnostic in diagnostics or ()),
    )
    if "the trait `Copy` cannot be implemented" not in text:
        return locations
    for match in _RUST_INCOMPATIBLE_COPY_LOCATION_RE.finditer(text):
        path = _normalize_repair_path(str(match.group("path") or ""))
        line_number = _to_int(match.group("line"))
        if not path or not path.endswith(".rs") or line_number is None or int(line_number) <= 0:
            continue
        key = (path, int(line_number))
        if key in seen:
            continue
        seen.add(key)
        locations.append(key)
    return locations


def _rust_import_line_from_suggestion_lines(lines: Sequence[str]) -> str:
    for line in lines:
        match = _RUST_USE_IMPORT_IN_TEXT_RE.search(str(line or ""))
        if match is None:
            continue
        return str(match.group("import") or "").strip()
    return ""


def _rust_incompatible_copy_derive_operation(
    *,
    path: str,
    content: str,
    line_number: int,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs"):
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return None

    for offset in range(0, 5):
        derive_index = line_index - offset
        if derive_index < 0 or derive_index >= len(lines):
            continue
        expected = lines[derive_index]
        replacement = _repair_rust_copy_derive_line(expected)
        if replacement is None or replacement == expected:
            continue
        if content.count(expected) != 1:
            return None
        line_start = sum(len(item) for item in lines[:derive_index])
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=line_start,
            span_end=line_start + len(expected),
            expected=expected,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "rust_incompatible_copy_derive",
                "edit_strategy": "text_replace",
                "span_based": True,
                "line_number": line_number,
                "derive_line_number": derive_index + 1,
                "unique_context": True,
                "diagnostic_id": diagnostic.diagnostic_id,
            },
        )
    return None


def _repair_rust_copy_derive_line(line: str) -> str | None:
    match = _RUST_DERIVE_LINE_RE.match(line)
    if match is None:
        return None
    items = str(match.group("items") or "")
    if _RUST_COPY_DERIVE_TOKEN_RE.search(items) is None:
        return None
    repaired = re.sub(r",\s*Copy\b", "", line)
    repaired = re.sub(r"\bCopy\s*,\s*", "", repaired)
    repaired = re.sub(r"\bCopy\b", "", repaired)
    if repaired == line or _RUST_DERIVE_LINE_RE.match(repaired) is None:
        return None
    return repaired


def _rust_field_rename_suggestion_operation(
    *,
    path: str,
    content: str,
    line_number: int,
    column_number: int,
    wrong_field: str,
    correct_field: str,
    suggested_code: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs") or not _is_rust_identifier(wrong_field) or not _is_rust_identifier(correct_field):
        return None
    if wrong_field == correct_field:
        return None
    lines = content.splitlines(keepends=True)
    index = line_number - 1
    if index < 0 or index >= len(lines):
        return None
    expected_line = lines[index]
    newline = _line_ending(expected_line)
    line_body = expected_line[: len(expected_line) - len(newline)] if newline else expected_line
    suggested_body = str(suggested_code or "").rstrip()
    field_access = f".{wrong_field}"
    correct_candidates = list(
        dict.fromkeys(
            candidate
            for candidate in (
                correct_field,
                *(str(match.group("field") or "") for match in _RUST_FIELD_ACCESS_RE.finditer(suggested_body)),
            )
            if _is_rust_identifier(candidate) and candidate != wrong_field
        )
    )

    candidate_spans: list[tuple[int, int, str]] = []
    search_start = 0
    while True:
        found = line_body.find(field_access, search_start)
        if found < 0:
            break
        for candidate_field in correct_candidates:
            replacement_access = f".{candidate_field}"
            candidate = f"{line_body[:found]}{replacement_access}{line_body[found + len(field_access) :]}"
            if candidate.strip() == suggested_body.strip():
                candidate_spans.append((found + 1, found + len(field_access), candidate_field))
        search_start = found + len(field_access)

    if len(candidate_spans) != 1:
        return None

    line_start = sum(len(item) for item in lines[:index])
    relative_start, relative_end, matched_correct_field = candidate_spans[0]
    replacement_access = f".{matched_correct_field}"
    span_start = line_start + relative_start
    span_end = line_start + relative_end
    if content[span_start:span_end] != wrong_field:
        return None
    unique_context = _unique_context_for_rust_span(content, span_start, span_end)
    if not unique_context:
        return None

    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=wrong_field,
        replacement=matched_correct_field,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_field_rename_suggestion",
            "edit_strategy": "text_replace",
            "span_based": True,
            "line_number": line_number,
            "column_number": column_number,
            "wrong_field": wrong_field,
            "correct_field": matched_correct_field,
            "field_access_before": field_access,
            "field_access_after": replacement_access,
            "suggested_code": suggested_body,
            "source_span_start": span_start,
            "source_span_end": span_end,
            "unique_context": unique_context,
            "unique_context_hash": sha256_text(unique_context),
            "diagnostic_id": diagnostic.diagnostic_id,
        },
    )


def _rust_line_suggestion_operation(
    *,
    path: str,
    content: str,
    line_number: int,
    code: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs"):
        return None
    lines = content.splitlines(keepends=True)
    index = line_number - 1
    if index < 0 or index >= len(lines):
        return None
    expected = lines[index]
    replacement = f"{str(code or '').rstrip()}{_line_ending(expected)}"
    if expected == replacement:
        return None
    if content.count(expected) != 1:
        return None
    line_start = sum(len(item) for item in lines[:index])
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=line_start,
        span_end=line_start + len(expected),
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_line_suggestion",
            "edit_strategy": "text_replace",
            "span_based": True,
            "line_number": line_number,
            "unique_context": True,
            "diagnostic_id": diagnostic.diagnostic_id,
        },
    )


def _rust_wrong_crate_path_operation(
    *,
    path: str,
    content: str,
    line_number: int,
    suggestion: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs") or not _is_strict_rust_use_import_line(suggestion):
        return None
    lines = content.splitlines(keepends=True)
    index = line_number - 1
    if index < 0 or index >= len(lines):
        return None
    expected = lines[index]
    if content.count(expected) != 1:
        return None
    newline = _line_ending(expected)
    body = expected[: len(expected) - len(newline)] if newline else expected
    indent = body[: len(body) - len(body.lstrip(" \t"))]
    if not _is_strict_rust_use_import_line(body.strip()):
        return None
    replacement = f"{indent}{suggestion}{newline}"
    if replacement == expected:
        return None
    line_start = sum(len(item) for item in lines[:index])
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=line_start,
        span_end=line_start + len(expected),
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_wrong_crate_path",
            "edit_strategy": "text_replace",
            "span_based": True,
            "line_number": line_number,
            "suggestion": suggestion,
            "unique_context": True,
            "diagnostic_id": diagnostic.diagnostic_id,
        },
    )


def _rust_unused_import_operation(
    *,
    path: str,
    content: str,
    line_number: int,
    symbol: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs") or not _is_rust_identifier(symbol):
        return None
    lines = content.splitlines(keepends=True)
    index = line_number - 1
    if index < 0 or index >= len(lines):
        return None
    expected = lines[index]
    if content.count(expected) != 1:
        return None
    replacement = _repair_rust_unused_import_line(expected, symbol)
    if replacement is None or replacement == expected:
        return None
    line_start = sum(len(item) for item in lines[:index])
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=line_start,
        span_end=line_start + len(expected),
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_unused_import",
            "edit_strategy": "text_replace",
            "span_based": True,
            "line_number": line_number,
            "symbol": symbol,
            "unique_context": True,
            "diagnostic_id": diagnostic.diagnostic_id,
        },
    )


def _repair_rust_unused_import_line(line: str, symbol: str) -> str | None:
    newline = _line_ending(line)
    body = line[: len(line) - len(newline)] if newline else line
    indent = body[: len(body) - len(body.lstrip(" \t"))]
    stripped = body.strip()
    if not stripped.startswith("use ") or not stripped.endswith(";"):
        return None

    group_match = re.match(r"^(?P<prefix>use\s+.+?::\{)(?P<items>[^{};]+)(?P<suffix>\};)$", stripped)
    if group_match is not None:
        items = [item.strip() for item in str(group_match.group("items") or "").split(",") if item.strip()]
        kept: list[str] = []
        removed = False
        for item in items:
            candidate = item.split(" as ", 1)[0].strip()
            if candidate == symbol:
                removed = True
                continue
            kept.append(item)
        if not removed:
            return None
        if kept:
            return f"{indent}{group_match.group('prefix')}{', '.join(kept)}{group_match.group('suffix')}{newline}"
        return f"{indent}// [repair-unused] {stripped}{newline}"

    single_match = re.match(
        r"^use\s+.+::(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?;$",
        stripped,
    )
    if single_match is None or single_match.group("symbol") != symbol:
        return None
    return f"{indent}// [repair-unused] {stripped}{newline}"


def _rust_trait_import_operation(
    *,
    path: str,
    content: str,
    import_line: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs") or not _is_strict_rust_use_import_line(import_line):
        return None
    lines = content.splitlines(keepends=True)
    if any(line.strip() == import_line for line in lines):
        return None

    insert_index = _rust_use_insert_index(lines)
    anchor = _rust_trait_import_anchor(lines, insert_index, import_line)
    if anchor is None:
        return None
    anchor_index, expected, replacement = anchor
    if not expected or content.count(expected) != 1:
        return None

    span_start = sum(len(item) for item in lines[:anchor_index])
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_start + len(expected),
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_trait_import",
            "edit_strategy": "text_replace",
            "span_based": True,
            "import_line": import_line,
            "insert_index": insert_index,
            "unique_context": True,
            "diagnostic_id": diagnostic.diagnostic_id,
        },
    )


def _rust_trait_import_anchor(
    lines: Sequence[str],
    insert_index: int,
    import_line: str,
) -> tuple[int, str, str] | None:
    newline = _rust_file_newline(lines)
    if insert_index < len(lines):
        expected = lines[insert_index]
        return insert_index, expected, f"{import_line}{newline}{expected}"
    if not lines:
        return None
    anchor_index = len(lines) - 1
    expected = lines[anchor_index]
    separator = "" if _line_ending(expected) else newline
    return anchor_index, expected, f"{expected}{separator}{import_line}{newline}"


def _rust_use_insert_index(lines: Sequence[str]) -> int:
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == "" or stripped.startswith("//!") or stripped.startswith("#!["):
            index += 1
            continue
        break

    insert_index = index
    seen_use = False
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("use ") and stripped.endswith(";"):
            seen_use = True
            insert_index = index + 1
            index += 1
            continue
        if seen_use and stripped == "":
            insert_index = index + 1
            index += 1
            continue
        break
    return insert_index


def _rust_file_newline(lines: Sequence[str]) -> str:
    for line in lines:
        newline = _line_ending(line)
        if newline:
            return newline
    return "\n"


def _read_cargo_manifest_from_base(base_files: Mapping[str, str]) -> dict[str, object]:
    try:
        payload = tomllib.loads(str(base_files.get("Cargo.toml") or ""))
    except (RuntimeError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _canonical_rust_crate_name(cargo: Mapping[str, object]) -> str:
    lib = cargo.get("lib")
    if isinstance(lib, dict):
        lib_name = str(lib.get("name") or "").strip()
        if lib_name:
            return _rust_identifier_from_manifest_name(lib_name)
    package = cargo.get("package")
    if not isinstance(package, dict):
        return ""
    package_name = str(package.get("name") or "").strip()
    return _rust_identifier_from_manifest_name(package_name)


def _rust_identifier_from_manifest_name(name: str) -> str:
    normalized = str(name or "").replace("-", "_")
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", normalized)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
        return ""
    return normalized


def _declared_rust_dependencies(cargo: Mapping[str, object]) -> set[str]:
    dependency_names: set[str] = set()
    for key in ("dependencies", "dev-dependencies", "build-dependencies"):
        value = cargo.get(key)
        if isinstance(value, dict):
            dependency_names.update(_rust_identifier_from_manifest_name(str(name)) for name in value)
    target = cargo.get("target")
    if isinstance(target, dict):
        for target_payload in target.values():
            if not isinstance(target_payload, dict):
                continue
            for key in ("dependencies", "dev-dependencies", "build-dependencies"):
                value = target_payload.get(key)
                if isinstance(value, dict):
                    dependency_names.update(_rust_identifier_from_manifest_name(str(name)) for name in value)
    dependency_names.discard("")
    return dependency_names


def _declared_rust_binary_entrypoint_paths(cargo: Mapping[str, object]) -> tuple[str, ...]:
    bins = cargo.get("bin")
    if not isinstance(bins, list):
        return ()
    paths: list[str] = []
    seen: set[str] = set()
    for entry in bins:
        if not isinstance(entry, dict):
            continue
        path = _normalize_repair_path(str(entry.get("path") or ""))
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return tuple(paths)


def _rust_binary_entrypoint_path_is_safe(path: str) -> bool:
    normalized = _normalize_repair_path(path)
    return bool(normalized and normalized == path and normalized.endswith(".rs"))


def _rust_missing_binary_entrypoint_stub(crate_name: str) -> str:
    safe_name = _rust_identifier_from_manifest_name(crate_name) or "app"
    return (
        f"// Auto-generated binary entry point for {safe_name}\n"
        "fn main() {\n"
        f'    println!("{safe_name} binary entry point");\n'
        "}\n"
    )


def _rust_missing_module_file_candidate(
    *,
    base_files: Mapping[str, str],
    diagnostic: RepairDiagnostic,
) -> tuple[str, str] | None:
    if str(diagnostic.code or "").lower() != "rust_e0583":
        return None
    diagnostic_text = _ANSI_ESCAPE_RE.sub("", f"{diagnostic.message}\n{diagnostic.raw}")
    message_match = _RUST_MISSING_MODULE_FILE_RE.search(diagnostic_text)
    if message_match is None:
        return None
    module_name = str(message_match.group("module") or "").strip()
    declaring_path = _normalize_repair_path(str(diagnostic.path or ""))
    if not module_name or not declaring_path or not declaring_path.endswith(".rs"):
        return None
    if declaring_path not in base_files or diagnostic.line is None:
        return None
    if not _rust_diagnostic_line_declares_module(
        content=base_files[declaring_path],
        line_number=int(diagnostic.line),
        module_name=module_name,
    ):
        return None

    raw_text = _ANSI_ESCAPE_RE.sub("", str(diagnostic.raw or ""))
    for candidate_path in _rust_e0583_help_candidate_paths(raw_text, module_name):
        if candidate_path in base_files:
            continue
        if _rust_missing_module_file_create_path_is_safe(candidate_path):
            return candidate_path, module_name
    return None


def _rust_diagnostic_line_declares_module(*, content: str, line_number: int, module_name: str) -> bool:
    if line_number <= 0:
        return False
    lines = str(content or "").splitlines()
    if line_number > len(lines):
        return False
    line = lines[line_number - 1]
    return (
        re.fullmatch(
            rf"\s*(?:pub\s+)?mod\s+{re.escape(module_name)}\s*;\s*(?://.*)?",
            line,
        )
        is not None
    )


def _rust_e0583_help_candidate_paths(raw_text: str, module_name: str) -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for help_match in _RUST_E0583_HELP_LINE_RE.finditer(raw_text):
        if str(help_match.group("module") or "") != module_name:
            continue
        candidates = str(help_match.group("candidates") or "")
        for path_match in _RUST_QUOTED_RS_PATH_RE.finditer(candidates):
            normalized = _normalize_repair_path(str(path_match.group("path") or ""))
            if normalized and normalized not in seen:
                seen.add(normalized)
                paths.append(normalized)
    return tuple(paths)


def _rust_missing_module_file_create_path_is_safe(path: str) -> bool:
    raw = str(path or "").strip().replace("\\", "/")
    normalized = _normalize_repair_path(raw)
    if not normalized or normalized != raw.lstrip("./") or not normalized.endswith(".rs"):
        return False
    if re.match(r"^[A-Za-z]:/", raw):
        return False
    raw_parts = normalized.split("/")
    parts = tuple(part for part in raw_parts if part)
    if not parts or len(parts) != len(raw_parts):
        return False
    return not any(part in {".", "..", "target", "build", "out"} for part in parts)


def _parse_rust_duplicate_module_file_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[str, str, str, RepairDiagnostic], ...]:
    targets: list[tuple[str, str, str, RepairDiagnostic]] = []
    seen: set[tuple[str, str, str]] = set()
    for diagnostic in diagnostics:
        if str(diagnostic.code or "").lower() != "rust_e0761":
            continue
        text = _ANSI_ESCAPE_RE.sub("", f"{diagnostic.message}\n{diagnostic.raw}")
        for match in _RUST_DUPLICATE_MODULE_FILE_RE.finditer(text):
            module_name = str(match.group("module") or "").strip()
            first_path = _normalize_repair_path(str(match.group("first") or ""))
            second_path = _normalize_repair_path(str(match.group("second") or ""))
            if not _is_rust_identifier(module_name):
                continue
            if not first_path or not second_path or first_path == second_path:
                continue
            if not first_path.endswith(".rs") or not second_path.endswith(".rs"):
                continue
            key = (module_name, first_path, second_path)
            if key in seen:
                continue
            seen.add(key)
            targets.append((module_name, first_path, second_path, diagnostic))
    return tuple(targets)


def _rust_duplicate_module_delete_candidate(
    *,
    first_path: str,
    first_content: str,
    second_path: str,
    second_content: str,
) -> tuple[str, str, str] | None:
    first_evidence = _rust_duplicate_module_delete_evidence(first_content)
    second_evidence = _rust_duplicate_module_delete_evidence(second_content)
    first_has_item = _rust_file_has_real_rust_item(first_content)
    second_has_item = _rust_file_has_real_rust_item(second_content)

    if first_evidence and not first_has_item and second_has_item:
        return first_path, second_path, first_evidence
    if second_evidence and not second_has_item and first_has_item:
        return second_path, first_path, second_evidence
    return None


def _rust_duplicate_module_delete_evidence(content: str) -> str:
    text = str(content or "")
    if not text.strip():
        return "empty"
    if _rust_file_has_real_rust_item(text):
        return ""
    if _rust_file_has_polaris_marker_comment(text):
        return "polaris_marker"
    if _rust_file_is_comment_only(text):
        return "comment_only"
    return ""


def _rust_file_has_real_rust_item(content: str) -> bool:
    for line in _rust_non_comment_lines(content):
        if line.startswith("#[") or line.startswith("#!["):
            continue
        if _RUST_REAL_ITEM_RE.match(line):
            return True
    return False


def _rust_file_is_comment_only(content: str) -> bool:
    return bool(str(content or "").strip()) and not tuple(_rust_non_comment_lines(content))


def _rust_file_has_polaris_marker_comment(content: str) -> bool:
    for raw_line in str(content or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith(("//", "/*", "*")) and "polaris" in stripped.lower():
            return True
    return False


def _rust_non_comment_lines(content: str) -> tuple[str, ...]:
    lines: list[str] = []
    in_block_comment = False
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        while line:
            if in_block_comment:
                end_index = line.find("*/")
                if end_index < 0:
                    line = ""
                    continue
                line = line[end_index + 2 :].lstrip()
                in_block_comment = False
                continue
            if line.startswith("//"):
                line = ""
                continue
            if line.startswith("/*"):
                end_index = line.find("*/", 2)
                if end_index < 0:
                    in_block_comment = True
                    line = ""
                    continue
                line = line[end_index + 2 :].lstrip()
                continue
            if line.startswith("*"):
                line = ""
                continue
            lines.append(line)
            line = ""
    return tuple(lines)


def _parse_unresolved_rust_crates(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[tuple[str, RepairDiagnostic]]:
    seen: set[str] = set()
    crates: list[tuple[str, RepairDiagnostic]] = []
    for diagnostic in diagnostics:
        text = _ANSI_ESCAPE_RE.sub("", str(diagnostic.raw or diagnostic.message or ""))
        for match in _RUST_UNRESOLVED_CRATE_RE.finditer(text):
            crate = str(match.group("crate") or "").strip()
            if not crate or crate in seen:
                continue
            seen.add(crate)
            crates.append((crate, diagnostic))
    return crates


def _cargo_declares_local_rust_lib(base_files: Mapping[str, str], cargo: Mapping[str, object]) -> bool:
    lib = cargo.get("lib")
    if isinstance(lib, dict):
        configured = str(lib.get("path") or "src/lib.rs").strip() or "src/lib.rs"
        return _normalize_repair_path(configured) in base_files
    return "src/lib.rs" in base_files


def _rust_crate_prefix_used_in_binary_entrypoint(base_files: Mapping[str, str], missing_crate: str) -> bool:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(missing_crate)}(?=::)")
    for path, content in base_files.items():
        if (path == "src/main.rs" or (path.startswith("src/bin/") and path.endswith(".rs"))) and pattern.search(
            content
        ):
            return True
    return False


def _rust_crate_names_look_related(missing: str, canonical: str) -> bool:
    missing_tokens = _crate_name_tokens(missing)
    canonical_tokens = _crate_name_tokens(canonical)
    if len(missing_tokens) < 2 or not canonical_tokens:
        return False
    overlap = missing_tokens & canonical_tokens
    return missing_tokens.issubset(canonical_tokens) or len(overlap) >= 2


def _crate_name_tokens(name: str) -> set[str]:
    return {token for token in re.split(r"[_\W]+", str(name or "").lower()) if token}


def _rust_crate_import_rewrite_operations(
    *,
    base_files: Mapping[str, str],
    missing_crate: str,
    canonical_crate: str,
    diagnostic: RepairDiagnostic,
) -> tuple[RepairOperation, ...]:
    prefix_pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(missing_crate)}(?=::)")
    extern_pattern = re.compile(rf"\bextern\s+crate\s+{re.escape(missing_crate)}\b")
    operations: list[RepairOperation] = []
    for path, content in sorted(base_files.items()):
        if not path.endswith(".rs") or "target" in path.split("/"):
            continue
        for match in prefix_pattern.finditer(content):
            operation = _rust_crate_import_rewrite_operation(
                path=path,
                content=content,
                span_start=match.start(),
                span_end=match.end(),
                expected=missing_crate,
                replacement=canonical_crate,
                missing_crate=missing_crate,
                canonical_crate=canonical_crate,
                match_kind="crate_prefix",
                diagnostic=diagnostic,
            )
            if operation is not None:
                operations.append(operation)
        for match in extern_pattern.finditer(content):
            operation = _rust_crate_import_rewrite_operation(
                path=path,
                content=content,
                span_start=match.start(),
                span_end=match.end(),
                expected=match.group(0),
                replacement=f"extern crate {canonical_crate}",
                missing_crate=missing_crate,
                canonical_crate=canonical_crate,
                match_kind="extern_crate",
                diagnostic=diagnostic,
            )
            if operation is not None:
                operations.append(operation)
    return tuple(operations)


def _rust_crate_import_rewrite_operation(
    *,
    path: str,
    content: str,
    span_start: int,
    span_end: int,
    expected: str,
    replacement: str,
    missing_crate: str,
    canonical_crate: str,
    match_kind: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    context = _unique_span_context(content, span_start, span_end)
    if context is None:
        return None
    context_before, context_after = context
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_crate_import_rewrite",
            "edit_strategy": "text_replace",
            "span_based": True,
            "missing_crate": missing_crate,
            "canonical_crate": canonical_crate,
            "match_kind": match_kind,
            "expected_context_before": context_before,
            "expected_context_after": context_after,
            "diagnostic_id": diagnostic.diagnostic_id,
        },
    )


def _unique_span_context(content: str, span_start: int, span_end: int) -> tuple[str, str] | None:
    line_start = content.rfind("\n", 0, span_start) + 1
    line_end = content.find("\n", span_end)
    if line_end == -1:
        line_end = len(content)
    else:
        line_end += 1
    before = content[line_start:span_start]
    after = content[span_end:line_end]
    probe = f"{before}{content[span_start:span_end]}{after}"
    if probe and content.count(probe) == 1:
        return before, after

    for radius in (24, 48, 96, 160):
        before_start = max(0, span_start - radius)
        after_end = min(len(content), span_end + radius)
        before = content[before_start:span_start]
        after = content[span_end:after_end]
        probe = f"{before}{content[span_start:span_end]}{after}"
        if probe and content.count(probe) == 1:
            return before, after
    return None


def _is_strict_rust_use_import_line(value: str) -> bool:
    line = str(value or "").strip()
    if "\n" in line or "\r" in line:
        return False
    return _RUST_USE_IMPORT_LINE_RE.fullmatch(line) is not None


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return ""


def _unique_context_for_rust_span(content: str, span_start: int, span_end: int) -> str:
    if span_start < 0 or span_end < span_start or span_end > len(content):
        return ""
    for radius in (24, 48, 96, 192, 384, 768, 1536):
        context_start = max(0, span_start - radius)
        context_end = min(len(content), span_end + radius)
        probe = content[context_start:context_end]
        if probe and content.find(probe) >= 0 and content.find(probe, content.find(probe) + 1) < 0:
            return probe
    return ""


def _to_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/"):
        return ""
    if any(part == ".." for part in normalized.split("/")):
        return ""
    return normalized
