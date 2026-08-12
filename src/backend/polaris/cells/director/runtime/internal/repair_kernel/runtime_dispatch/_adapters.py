"""Language-result adapters for runtime_dispatch."""

from __future__ import annotations

from ..cpp_runtime import (
    CppIncludePathPlanning,
    CppIncludePathRun,
    CppMissingPrivateMembersPlanning,
    CppMissingPrivateMembersRun,
    CppPlaceholderDeclarationPlanning,
    CppPlaceholderDeclarationRun,
    CppPostPlanning,
    CppPostRun,
    CppStandardIncludePlanning,
    CppStandardIncludeRun,
    CppStructGetterFieldAccessPlanning,
    CppStructGetterFieldAccessRun,
)
from ..generic_hygiene_runtime import (
    GenericHygienePlanning,
    GenericHygieneRun,
    PatchResidueCleanupPlanning,
    PatchResidueCleanupRun,
)
from ..go_runtime import (
    GoBareImportStringPlanning,
    GoBareImportStringRun,
)
from ..java_runtime import (
    JavaAccessorAliasPlanning,
    JavaAccessorAliasRun,
    JavaPostPlanning,
    JavaPostRun,
)
from ..javascript_runtime import (
    JavaScriptRepairPlanning,
    JavaScriptRepairRun,
)
from ..python_runtime import (
    PythonRepairPlanning,
    PythonRepairRun,
)
from ..rust_runtime import (
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
from ..typescript_runtime import (
    TypeScriptCanvasScaleReturnTypePlanning,
    TypeScriptCanvasScaleReturnTypeRun,
    TypeScriptDuplicateObjectPropertyPlanning,
    TypeScriptDuplicateObjectPropertyRun,
    TypeScriptEnumMemberSeparatorPlanning,
    TypeScriptEnumMemberSeparatorRun,
    TypeScriptMissingClosingBracePlanning,
    TypeScriptMissingClosingBraceRun,
    TypeScriptNullableCanvasContextPlanning,
    TypeScriptNullableCanvasContextRun,
    TypeScriptNumberToStringArgumentPlanning,
    TypeScriptNumberToStringArgumentRun,
    TypeScriptObjectLiteralCommaPlanning,
    TypeScriptObjectLiteralCommaRun,
    TypeScriptReadonlyAssignmentPlanning,
    TypeScriptReadonlyAssignmentRun,
    TypeScriptRuntimePlanning,
    TypeScriptRuntimeRun,
)
from ._types import RuntimeRepairPlanning, RuntimeRepairRun


def _runtime_planning_from_cpp(
    planning: CppIncludePathPlanning
    | CppMissingPrivateMembersPlanning
    | CppPlaceholderDeclarationPlanning
    | CppPostPlanning
    | CppStandardIncludePlanning
    | CppStructGetterFieldAccessPlanning,
) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_cpp(
    run: CppIncludePathRun
    | CppMissingPrivateMembersRun
    | CppPlaceholderDeclarationRun
    | CppPostRun
    | CppStandardIncludeRun
    | CppStructGetterFieldAccessRun,
) -> RuntimeRepairRun:
    return RuntimeRepairRun(
        planning=_runtime_planning_from_cpp(run.planning),
        ok=run.ok,
        execution_result=run.execution_result,
        plan_decision=run.plan_decision,
        composition_decision=run.composition_decision,
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _runtime_planning_from_go(planning: GoBareImportStringPlanning) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_go(run: GoBareImportStringRun) -> RuntimeRepairRun:
    return RuntimeRepairRun(
        planning=_runtime_planning_from_go(run.planning),
        ok=run.ok,
        execution_result=run.execution_result,
        plan_decision=run.plan_decision,
        composition_decision=run.composition_decision,
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _runtime_planning_from_rust(
    planning: RustCrateImportPlanning
    | RustCrateImportRewritePlanning
    | RustDependencyPlanning
    | RustDuplicateModuleFilePlanning
    | RustFieldRenameSuggestionPlanning
    | RustIncompatibleCopyDerivePlanning
    | RustLibRootFacadePlanning
    | RustLineSuggestionPlanning
    | RustMethodSelfSignaturePlanning
    | RustMissingBinaryEntrypointPlanning
    | RustMissingLibTargetPlanning
    | RustMissingModuleFilePlanning
    | RustMissingFieldsPlanning
    | RustMissingTraitDerivePlanning
    | RustSerdeDerivePlanning
    | RustStructLiteralMissingFieldPlanning
    | RustTraitImportPlanning
    | RustUnusedImportPlanning
    | RustUnresolvedPubUsePlanning
    | RustWrongCratePathPlanning,
) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_rust(
    run: RustCrateImportRun
    | RustCrateImportRewriteRun
    | RustDependencyRun
    | RustDuplicateModuleFileRun
    | RustFieldRenameSuggestionRun
    | RustIncompatibleCopyDeriveRun
    | RustLibRootFacadeRun
    | RustLineSuggestionRun
    | RustMethodSelfSignatureRun
    | RustMissingBinaryEntrypointRun
    | RustMissingLibTargetRun
    | RustMissingModuleFileRun
    | RustMissingFieldsRun
    | RustMissingTraitDeriveRun
    | RustSerdeDeriveRun
    | RustStructLiteralMissingFieldRun
    | RustTraitImportRun
    | RustUnusedImportRun
    | RustUnresolvedPubUseRun
    | RustWrongCratePathRun,
) -> RuntimeRepairRun:
    return RuntimeRepairRun(
        planning=_runtime_planning_from_rust(run.planning),
        ok=run.ok,
        execution_result=run.execution_result,
        plan_decision=run.plan_decision,
        composition_decision=run.composition_decision,
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _runtime_planning_from_generic_hygiene(planning: GenericHygienePlanning) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_generic_hygiene(run: GenericHygieneRun) -> RuntimeRepairRun:
    return RuntimeRepairRun(
        planning=_runtime_planning_from_generic_hygiene(run.planning),
        ok=run.ok,
        execution_result=run.execution_result,
        plan_decision=run.plan_decision,
        composition_decision=run.composition_decision,
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _runtime_planning_from_patch_residue_cleanup(planning: PatchResidueCleanupPlanning) -> RuntimeRepairPlanning:
    return _runtime_planning_from_generic_hygiene(planning)


def _runtime_run_from_patch_residue_cleanup(run: PatchResidueCleanupRun) -> RuntimeRepairRun:
    return _runtime_run_from_generic_hygiene(run)


def _runtime_planning_from_java(planning: JavaAccessorAliasPlanning | JavaPostPlanning) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_java(run: JavaAccessorAliasRun | JavaPostRun) -> RuntimeRepairRun:
    return RuntimeRepairRun(
        planning=_runtime_planning_from_java(run.planning),
        ok=run.ok,
        execution_result=run.execution_result,
        plan_decision=run.plan_decision,
        composition_decision=run.composition_decision,
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _runtime_planning_from_typescript(
    planning: TypeScriptCanvasScaleReturnTypePlanning
    | TypeScriptDuplicateObjectPropertyPlanning
    | TypeScriptEnumMemberSeparatorPlanning
    | TypeScriptMissingClosingBracePlanning
    | TypeScriptNullableCanvasContextPlanning
    | TypeScriptNumberToStringArgumentPlanning
    | TypeScriptObjectLiteralCommaPlanning
    | TypeScriptReadonlyAssignmentPlanning,
) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_typescript(
    run: TypeScriptCanvasScaleReturnTypeRun
    | TypeScriptDuplicateObjectPropertyRun
    | TypeScriptEnumMemberSeparatorRun
    | TypeScriptMissingClosingBraceRun
    | TypeScriptNullableCanvasContextRun
    | TypeScriptNumberToStringArgumentRun
    | TypeScriptObjectLiteralCommaRun
    | TypeScriptReadonlyAssignmentRun,
) -> RuntimeRepairRun:
    return RuntimeRepairRun(
        planning=_runtime_planning_from_typescript(run.planning),
        ok=run.ok,
        execution_result=run.execution_result,
        plan_decision=run.plan_decision,
        composition_decision=run.composition_decision,
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _runtime_planning_from_typescript_runtime(planning: TypeScriptRuntimePlanning) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_typescript_runtime(run: TypeScriptRuntimeRun) -> RuntimeRepairRun:
    return RuntimeRepairRun(
        planning=_runtime_planning_from_typescript_runtime(run.planning),
        ok=run.ok,
        execution_result=run.execution_result,
        plan_decision=run.plan_decision,
        composition_decision=run.composition_decision,
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _runtime_planning_from_javascript(planning: JavaScriptRepairPlanning) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_javascript(run: JavaScriptRepairRun) -> RuntimeRepairRun:
    return RuntimeRepairRun(
        planning=_runtime_planning_from_javascript(run.planning),
        ok=run.ok,
        execution_result=run.execution_result,
        plan_decision=run.plan_decision,
        composition_decision=run.composition_decision,
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _runtime_planning_from_python(planning: PythonRepairPlanning) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_python(run: PythonRepairRun) -> RuntimeRepairRun:
    return RuntimeRepairRun(
        planning=_runtime_planning_from_python(run.planning),
        ok=run.ok,
        execution_result=run.execution_result,
        plan_decision=run.plan_decision,
        composition_decision=run.composition_decision,
        error_code=run.error_code,
        error_message=run.error_message,
    )
