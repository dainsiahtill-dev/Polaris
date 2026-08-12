"""Executable Rust repair runtime bindings.

Lossless package successor of the former ``rust_runtime`` module.
Cell-private: lives under ``director.runtime.internal.repair_kernel``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..composer import PatchComposer
from ..contracts import (
    CompositionResult,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairExecutionResult,
    RepairOperation,
    RepairPlan,
)
from ..diagnostics import normalize_artifact_quality_errors
from ..executor import DeleteFileFn, EditFileFn, TransactionalRepairExecutor, WriteFileFn
from ..policy_gate import PolicyDecision, RepairPolicyContext, RepairPolicyGate
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
from ._plan import (
    plan_rust_crate_import_repair,
    plan_rust_crate_import_rewrite_repair,
    plan_rust_dependency_repair,
    plan_rust_duplicate_module_file_repair,
    plan_rust_field_rename_suggestion_repair,
    plan_rust_incompatible_copy_derive_repair,
    plan_rust_lib_root_facade_repair,
    plan_rust_line_suggestion_repair,
    plan_rust_method_self_signature_repair,
    plan_rust_missing_binary_entrypoint_repair,
    plan_rust_missing_fields_repair,
    plan_rust_missing_lib_target_repair,
    plan_rust_missing_module_file_repair,
    plan_rust_missing_trait_derive_repair,
    plan_rust_serde_derive_repair,
    plan_rust_struct_literal_missing_field_repair,
    plan_rust_trait_import_repair,
    plan_rust_unresolved_pub_use_repair,
    plan_rust_unused_import_repair,
    plan_rust_wrong_crate_path_repair,
)
from ._run import (
    run_rust_crate_import_repair,
    run_rust_crate_import_rewrite_repair,
    run_rust_dependency_repair,
    run_rust_duplicate_module_file_repair,
    run_rust_field_rename_suggestion_repair,
    run_rust_incompatible_copy_derive_repair,
    run_rust_lib_root_facade_repair,
    run_rust_line_suggestion_repair,
    run_rust_method_self_signature_repair,
    run_rust_missing_binary_entrypoint_repair,
    run_rust_missing_fields_repair,
    run_rust_missing_lib_target_repair,
    run_rust_missing_module_file_repair,
    run_rust_missing_trait_derive_repair,
    run_rust_serde_derive_repair,
    run_rust_struct_literal_missing_field_repair,
    run_rust_trait_import_repair,
    run_rust_unresolved_pub_use_repair,
    run_rust_unused_import_repair,
    run_rust_wrong_crate_path_repair,
)
from ._types import (
    RustCrateImportPlanning,
    RustCrateImportRewritePlanning,
    RustCrateImportRewriteRun,
    RustCrateImportRun,
    RustDependencyPlanning,
    RustDependencyRun,
    RustDuplicateModuleFilePlanning,
    RustDuplicateModuleFileRun,
    RustFieldRenameSuggestionPlanning,
    RustFieldRenameSuggestionRun,
    RustIncompatibleCopyDerivePlanning,
    RustIncompatibleCopyDeriveRun,
    RustLibRootFacadePlanning,
    RustLibRootFacadeRun,
    RustLineSuggestionPlanning,
    RustLineSuggestionRun,
    RustMethodSelfSignaturePlanning,
    RustMethodSelfSignatureRun,
    RustMissingBinaryEntrypointPlanning,
    RustMissingBinaryEntrypointRun,
    RustMissingFieldsPlanning,
    RustMissingFieldsRun,
    RustMissingLibTargetPlanning,
    RustMissingLibTargetRun,
    RustMissingModuleFilePlanning,
    RustMissingModuleFileRun,
    RustMissingTraitDerivePlanning,
    RustMissingTraitDeriveRun,
    RustSerdeDerivePlanning,
    RustSerdeDeriveRun,
    RustStructLiteralMissingFieldPlanning,
    RustStructLiteralMissingFieldRun,
    RustTraitImportPlanning,
    RustTraitImportRun,
    RustUnresolvedPubUsePlanning,
    RustUnresolvedPubUseRun,
    RustUnusedImportPlanning,
    RustUnusedImportRun,
    RustWrongCratePathPlanning,
    RustWrongCratePathRun,
)

# Re-bind private helpers onto the package namespace only if original dir() exposed them.
# Original private names start with '_' and are excluded from the surface oracle.
