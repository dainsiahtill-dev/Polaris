"""Public Rust deterministic repair plan builders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ._constants import (
    _KNOWN_RUST_DEPENDENCIES,
    RUST_CRATE_IMPORT_REWRITE_SOURCE_TOOL,
    RUST_CRATE_IMPORT_SOURCE_TOOL,
    RUST_DEPENDENCY_SOURCE_TOOL,
    RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
    RUST_FIELD_RENAME_SUGGESTION_SOURCE_TOOL,
    RUST_INCOMPATIBLE_COPY_DERIVE_SOURCE_TOOL,
    RUST_LINE_SUGGESTION_SOURCE_TOOL,
    RUST_METHOD_SELF_SIGNATURE_SOURCE_TOOL,
    RUST_MISSING_BINARY_ENTRYPOINT_SOURCE_TOOL,
    RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
    RUST_MISSING_MODULE_FILE_STUB,
    RUST_MISSING_TRAIT_DERIVE_SOURCE_TOOL,
    RUST_SERDE_DERIVE_SOURCE_TOOL,
    RUST_TRAIT_IMPORT_SOURCE_TOOL,
    RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL,
    RUST_UNUSED_IMPORT_SOURCE_TOOL,
    RUST_WRONG_CRATE_PATH_SOURCE_TOOL,
)
from ._helpers import (
    _build_rust_crate_import_plan,
    _canonical_rust_crate_name,
    _cargo_dependency_declared,
    _declared_rust_binary_entrypoint_paths,
    _diagnostics_indicate_missing_rust_binary,
    _expand_rust_derive_prerequisites,
    _file_replace_operations,
    _insert_cargo_dependency,
    _normalize_repair_path,
    _parse_rust_duplicate_module_file_targets,
    _parse_rust_field_rename_suggestions,
    _parse_rust_incompatible_copy_derive_locations,
    _parse_rust_line_suggestions,
    _parse_rust_missing_trait_derive_targets,
    _parse_rust_serde_derive_targets,
    _parse_rust_trait_import_suggestions,
    _parse_rust_unused_import_warnings,
    _parse_rust_wrong_crate_path_suggestions,
    _read_cargo_manifest_from_base,
    _rust_binary_entrypoint_path_is_safe,
    _rust_dependency_packages_to_add,
    _rust_duplicate_module_delete_candidate,
    _rust_field_rename_suggestion_operation,
    _rust_file_for_module_symbol,
    _rust_incompatible_copy_derive_operation,
    _rust_line_suggestion_operation,
    _rust_method_self_signature_location,
    _rust_method_self_signature_operation,
    _rust_missing_binary_entrypoint_stub,
    _rust_missing_binary_paths_from_diagnostic,
    _rust_missing_module_file_candidate,
    _rust_missing_trait_derive_candidate_files,
    _rust_missing_trait_derive_operation,
    _rust_serde_derive_operation,
    _rust_trait_import_operation,
    _rust_unresolved_pub_use_operations,
    _rust_unresolved_pub_use_symbols,
    _rust_unused_import_operation,
    _rust_wrong_crate_path_operation,
    rust_local_structure_operations,
)


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
    """Build span-based edits for the adapter-hosted Rust crate import source tool."""

    return _build_rust_crate_import_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=RUST_CRATE_IMPORT_SOURCE_TOOL,
        rule_id="rust.unresolved_import_path",
        repair_kind="rust_crate_import_path",
        depends_on=(),
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
    """Build write-file ops for missing Rust binary entrypoints (declared or implied).

    Covers:
    - ``[[bin]]`` paths declared in Cargo.toml but absent from base_files
    - Quality diagnostics for no-usable-bin / lib-only runnable crates (default
      ``src/main.rs`` when cargo-shaped ``can't find bin`` evidence is present)
    """

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

    binary_paths = list(_declared_rust_binary_entrypoint_paths(cargo))
    # Diagnostic-driven paths (absolute or relative) for declared-missing and
    # no-usable-bin quality evidence that does not require a [[bin]] section.
    for diagnostic in diagnostics or ():
        for candidate in _rust_missing_binary_paths_from_diagnostic(diagnostic):
            if candidate not in binary_paths:
                binary_paths.append(candidate)
    if not binary_paths and _diagnostics_indicate_missing_rust_binary(diagnostics):
        binary_paths.append("src/main.rs")

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
    working = dict(normalized_base)
    line_ops_by_path: dict[str, list[RepairOperation]] = {}
    for diagnostic in diagnostics:
        diagnostic_planned = False
        for path, line_number, code in _parse_rust_line_suggestions((diagnostic,)):
            content = working.get(path)
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
            working[path] = content[: operation.span_start] + str(operation.replacement) + content[operation.span_end :]
            line_ops_by_path.setdefault(path, []).append(operation)
            diagnostic_planned = True
        if diagnostic_planned:
            planned_diagnostics.append(diagnostic)
    local_ops = rust_local_structure_operations(
        base_files=working,
        diagnostics=diagnostics,
    )
    local_paths = {operation.path for operation in local_ops}
    for operation in local_ops:
        working[operation.path] = str(operation.replacement)
        planned_diagnostics.extend(diagnostics)
    for path, line_ops in line_ops_by_path.items():
        if path in local_paths:
            continue
        operations.extend(line_ops)
    for operation in local_ops:
        original = normalized_base.get(operation.path)
        repaired = working.get(operation.path)
        if original is None or repaired is None or repaired == original:
            continue
        operations.extend(
            _file_replace_operations(
                path=operation.path,
                original=original,
                repaired=repaired,
                diagnostic_id=next((item.diagnostic_id for item in planned_diagnostics), ""),
            )
        )

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
    """Build span-based edits that add ordinary missing trait derives to Rust structs/enums."""

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
        expanded_traits = _expand_rust_derive_prerequisites(traits)
        diagnostic_planned = False
        for path, content in _rust_missing_trait_derive_candidate_files(
            base_files=normalized_base,
            diagnostic=diagnostic,
        ):
            operation = _rust_missing_trait_derive_operation(
                path=path,
                content=content,
                symbol=symbol,
                traits=expanded_traits,
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
