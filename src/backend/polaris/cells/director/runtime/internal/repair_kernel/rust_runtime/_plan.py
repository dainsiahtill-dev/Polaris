from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..composer import PatchComposer
from ..contracts import RepairAdvisorNote
from ..diagnostics import normalize_artifact_quality_errors
from ..rust_ast import (
    RUST_MISSING_FIELDS_SOURCE_TOOL,
    RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL,
    build_rust_missing_fields_plan,
    build_rust_struct_literal_missing_field_plan,
)
from ..rust_export_facade import (
    RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
    RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
    build_rust_lib_root_facade_plan,
    build_rust_missing_lib_target_plan,
)
from ..rust_syntax import (
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
    RUST_MISSING_TRAIT_DERIVE_SOURCE_TOOL,
    RUST_SERDE_DERIVE_SOURCE_TOOL,
    RUST_TRAIT_IMPORT_SOURCE_TOOL,
    RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL,
    RUST_UNUSED_IMPORT_SOURCE_TOOL,
    RUST_WRONG_CRATE_PATH_SOURCE_TOOL,
    build_rust_crate_import_plan,
    build_rust_crate_import_rewrite_plan,
    build_rust_dependency_plan,
    build_rust_duplicate_module_file_plan,
    build_rust_field_rename_suggestion_plan,
    build_rust_incompatible_copy_derive_plan,
    build_rust_line_suggestion_plan,
    build_rust_method_self_signature_plan,
    build_rust_missing_binary_entrypoint_plan,
    build_rust_missing_module_file_plan,
    build_rust_missing_trait_derive_plan,
    build_rust_serde_derive_plan,
    build_rust_trait_import_plan,
    build_rust_unresolved_pub_use_plan,
    build_rust_unused_import_plan,
    build_rust_wrong_crate_path_plan,
)
from ._helpers import (
    _composition_operations_for_rust_line_suggestions,
    _composition_operations_for_rust_trait_imports,
    _composition_operations_for_rust_unique_context,
    _normalize_base_files,
)
from ._types import (
    RustCrateImportPlanning,
    RustCrateImportRewritePlanning,
    RustDependencyPlanning,
    RustDuplicateModuleFilePlanning,
    RustFieldRenameSuggestionPlanning,
    RustIncompatibleCopyDerivePlanning,
    RustLibRootFacadePlanning,
    RustLineSuggestionPlanning,
    RustMethodSelfSignaturePlanning,
    RustMissingBinaryEntrypointPlanning,
    RustMissingFieldsPlanning,
    RustMissingLibTargetPlanning,
    RustMissingModuleFilePlanning,
    RustMissingTraitDerivePlanning,
    RustSerdeDerivePlanning,
    RustStructLiteralMissingFieldPlanning,
    RustTraitImportPlanning,
    RustUnresolvedPubUsePlanning,
    RustUnusedImportPlanning,
    RustWrongCratePathPlanning,
)


def plan_rust_dependency_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustDependencyPlanning:
    """Plan Rust dependency repair through typed diagnostics."""

    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_dependency_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(base_files, plan.operations) if plan is not None else None
    return RustDependencyPlanning(
        source_tool=RUST_DEPENDENCY_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_missing_binary_entrypoint_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustMissingBinaryEntrypointPlanning:
    """Plan Rust missing binary entrypoint repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_missing_binary_entrypoint_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(normalized_base, plan.operations) if plan is not None else None
    return RustMissingBinaryEntrypointPlanning(
        source_tool=RUST_MISSING_BINARY_ENTRYPOINT_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_missing_lib_target_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustMissingLibTargetPlanning:
    """Plan the safe Rust lib target subset: missing default ``src/lib.rs`` only."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_missing_lib_target_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(normalized_base, plan.operations) if plan is not None else None
    return RustMissingLibTargetPlanning(
        source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_lib_root_facade_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustLibRootFacadePlanning:
    """Plan the safe Rust lib-root facade subset: one span-based rewrite or export insert."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_lib_root_facade_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(normalized_base, plan.operations) if plan is not None else None
    return RustLibRootFacadePlanning(
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_duplicate_module_file_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustDuplicateModuleFilePlanning:
    """Plan Rust E0761 duplicate module file repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_duplicate_module_file_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(normalized_base, plan.operations) if plan is not None else None
    return RustDuplicateModuleFilePlanning(
        source_tool=RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_missing_module_file_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustMissingModuleFilePlanning:
    """Plan Rust missing module file repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_missing_module_file_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(normalized_base, plan.operations) if plan is not None else None
    return RustMissingModuleFilePlanning(
        source_tool=RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_struct_literal_missing_field_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustStructLiteralMissingFieldPlanning:
    """Plan generated Rust struct literal missing-field repair through AST spans."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_struct_literal_missing_field_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(normalized_base, plan.operations) if plan is not None else None
    return RustStructLiteralMissingFieldPlanning(
        source_tool=RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_missing_fields_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustMissingFieldsPlanning:
    """Plan explicit-type Rust missing-field declaration repair through AST spans."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_missing_fields_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(normalized_base, plan.operations) if plan is not None else None
    return RustMissingFieldsPlanning(
        source_tool=RUST_MISSING_FIELDS_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_crate_import_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustCrateImportPlanning:
    """Plan the Rust crate import repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_crate_import_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(normalized_base, plan.operations) if plan is not None else None
    return RustCrateImportPlanning(
        source_tool=RUST_CRATE_IMPORT_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_crate_import_rewrite_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustCrateImportRewritePlanning:
    """Plan Rust local crate import rewrite repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_crate_import_rewrite_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(normalized_base, plan.operations) if plan is not None else None
    return RustCrateImportRewritePlanning(
        source_tool=RUST_CRATE_IMPORT_REWRITE_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_incompatible_copy_derive_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustIncompatibleCopyDerivePlanning:
    """Plan Rust incompatible Copy derive repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_incompatible_copy_derive_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = (
        PatchComposer().compose(normalized_base, _composition_operations_for_rust_unique_context(plan))
        if plan is not None
        else None
    )
    return RustIncompatibleCopyDerivePlanning(
        source_tool=RUST_INCOMPATIBLE_COPY_DERIVE_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_method_self_signature_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustMethodSelfSignaturePlanning:
    """Plan Rust method self-signature repair through typed diagnostics."""

    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_method_self_signature_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(base_files, plan.operations) if plan is not None else None
    return RustMethodSelfSignaturePlanning(
        source_tool=RUST_METHOD_SELF_SIGNATURE_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_line_suggestion_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustLineSuggestionPlanning:
    """Plan Rust compiler line-suggestion repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_line_suggestion_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = (
        PatchComposer().compose(normalized_base, _composition_operations_for_rust_line_suggestions(plan))
        if plan is not None
        else None
    )
    return RustLineSuggestionPlanning(
        source_tool=RUST_LINE_SUGGESTION_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_field_rename_suggestion_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustFieldRenameSuggestionPlanning:
    """Plan Rust field rename suggestion repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_field_rename_suggestion_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = (
        PatchComposer().compose(normalized_base, _composition_operations_for_rust_unique_context(plan))
        if plan is not None
        else None
    )
    return RustFieldRenameSuggestionPlanning(
        source_tool=RUST_FIELD_RENAME_SUGGESTION_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_wrong_crate_path_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustWrongCratePathPlanning:
    """Plan Rust wrong crate path repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_wrong_crate_path_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = (
        PatchComposer().compose(normalized_base, _composition_operations_for_rust_unique_context(plan))
        if plan is not None
        else None
    )
    return RustWrongCratePathPlanning(
        source_tool=RUST_WRONG_CRATE_PATH_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_serde_derive_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustSerdeDerivePlanning:
    """Plan Rust serde derive repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_serde_derive_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = (
        PatchComposer().compose(normalized_base, _composition_operations_for_rust_unique_context(plan))
        if plan is not None
        else None
    )
    return RustSerdeDerivePlanning(
        source_tool=RUST_SERDE_DERIVE_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_missing_trait_derive_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustMissingTraitDerivePlanning:
    """Plan Rust ordinary missing trait derive repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_missing_trait_derive_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = (
        PatchComposer().compose(normalized_base, _composition_operations_for_rust_unique_context(plan))
        if plan is not None
        else None
    )
    return RustMissingTraitDerivePlanning(
        source_tool=RUST_MISSING_TRAIT_DERIVE_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_trait_import_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustTraitImportPlanning:
    """Plan Rust trait import repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_trait_import_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = (
        PatchComposer().compose(normalized_base, _composition_operations_for_rust_trait_imports(plan))
        if plan is not None
        else None
    )
    return RustTraitImportPlanning(
        source_tool=RUST_TRAIT_IMPORT_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_unused_import_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustUnusedImportPlanning:
    """Plan Rust unused import repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_unused_import_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = (
        PatchComposer().compose(normalized_base, _composition_operations_for_rust_unique_context(plan))
        if plan is not None
        else None
    )
    return RustUnusedImportPlanning(
        source_tool=RUST_UNUSED_IMPORT_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def plan_rust_unresolved_pub_use_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustUnresolvedPubUsePlanning:
    """Plan Rust unresolved public re-export repair through typed diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_unresolved_pub_use_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(normalized_base, plan.operations) if plan is not None else None
    return RustUnresolvedPubUsePlanning(
        source_tool=RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )
