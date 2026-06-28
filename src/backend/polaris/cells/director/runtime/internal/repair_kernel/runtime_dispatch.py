"""Generic runtime dispatcher for deterministic Director repair rules."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .composer import PatchComposer
from .contracts import (
    CompositionResult,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairExecutionResult,
    RepairPlan,
    RepairReceipt,
)
from .cpp_runtime import (
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
    plan_cpp_include_path_repair,
    plan_cpp_missing_private_members_repair,
    plan_cpp_placeholder_declaration_repair,
    plan_cpp_post_repair,
    plan_cpp_standard_include_repair,
    plan_cpp_struct_getter_field_access_repair,
    run_cpp_include_path_repair,
    run_cpp_missing_private_members_repair,
    run_cpp_placeholder_declaration_repair,
    run_cpp_post_repair,
    run_cpp_standard_include_repair,
    run_cpp_struct_getter_field_access_repair,
)
from .cpp_syntax import (
    CPP_INCLUDE_PATH_SOURCE_TOOL,
    CPP_MISSING_PRIVATE_MEMBERS_SOURCE_TOOL,
    CPP_PLACEHOLDER_DECLARATION_SOURCE_TOOL,
    CPP_POST_SOURCE_TOOL,
    CPP_STANDARD_INCLUDE_SOURCE_TOOL,
    CPP_STRUCT_GETTER_FIELD_ACCESS_SOURCE_TOOL,
)
from .diagnostics import normalize_artifact_quality_errors
from .executor import DeleteFileFn, EditFileFn, TransactionalRepairExecutor, WriteFileFn
from .generic_hygiene_runtime import (
    GenericHygienePlanning,
    GenericHygieneRun,
    PatchResidueCleanupPlanning,
    PatchResidueCleanupRun,
    plan_generic_hygiene_repair,
    plan_patch_residue_cleanup_repair,
    run_generic_hygiene_repair,
    run_patch_residue_cleanup_repair,
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
from .go_runtime import (
    GoBareImportStringPlanning,
    GoBareImportStringRun,
    plan_go_bare_import_string_repair,
    plan_go_bare_local_import_repair,
    plan_go_dedup_repair,
    plan_go_error_string_helper_repair,
    plan_go_module_import_repair,
    plan_go_nested_import_repair,
    plan_go_subpath_import_repair,
    plan_go_unused_import_repair,
    run_go_bare_import_string_repair,
    run_go_bare_local_import_repair,
    run_go_dedup_repair,
    run_go_error_string_helper_repair,
    run_go_module_import_repair,
    run_go_nested_import_repair,
    run_go_subpath_import_repair,
    run_go_unused_import_repair,
)
from .go_syntax import (
    GO_BARE_IMPORT_STRING_SOURCE_TOOL,
    GO_BARE_LOCAL_IMPORT_SOURCE_TOOL,
    GO_DEDUP_SOURCE_TOOL,
    GO_ERROR_STRING_HELPER_SOURCE_TOOL,
    GO_MODULE_IMPORT_SOURCE_TOOL,
    GO_NESTED_IMPORT_SOURCE_TOOL,
    GO_SUBPATH_IMPORT_SOURCE_TOOL,
    GO_UNUSED_IMPORT_SOURCE_TOOL,
)
from .java_runtime import (
    JavaAccessorAliasPlanning,
    JavaAccessorAliasRun,
    JavaPostPlanning,
    JavaPostRun,
    plan_java_accessor_alias_repair,
    plan_java_post_repair,
    run_java_accessor_alias_repair,
    run_java_post_repair,
)
from .java_syntax import (
    JAVA_ACCESSOR_ALIAS_SOURCE_TOOL,
    JAVA_POST_SOURCE_TOOL,
    JAVA_TEST_DEPENDENCY_SOURCE_TOOL,
    build_java_test_dependency_plan,
)
from .javascript_runtime import (
    JavaScriptRepairPlanning,
    JavaScriptRepairRun,
    plan_javascript_dom_global_runtime_guard_repair,
    plan_javascript_esm_commonjs_entrypoint_repair,
    plan_javascript_missing_export_repair,
    plan_javascript_missing_method_runtime_repair,
    plan_javascript_test_missing_target_repair,
    plan_node_test_script_contract_repair,
    plan_npm_script_contract_repair,
    run_javascript_dom_global_runtime_guard_repair,
    run_javascript_esm_commonjs_entrypoint_repair,
    run_javascript_missing_export_repair,
    run_javascript_missing_method_runtime_repair,
    run_javascript_test_missing_target_repair,
    run_node_test_script_contract_repair,
    run_npm_script_contract_repair,
)
from .javascript_syntax import (
    JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL,
    JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL,
    JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
    JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL,
    JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
    NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL,
    NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
)
from .policy_gate import PolicyDecision, RepairPolicyContext, RepairPolicyGate
from .python_runtime import (
    PythonRepairPlanning,
    PythonRepairRun,
    plan_python_package_child_reexport_repair,
    plan_python_package_shadow_bridge_repair,
    plan_python_readme_required_token_repair,
    plan_python_unittest_missing_target_repair,
    plan_python_unittest_runtime_failure_repair,
    plan_python_unresolved_import_symbol_repair,
    run_python_package_child_reexport_repair,
    run_python_package_shadow_bridge_repair,
    run_python_readme_required_token_repair,
    run_python_unittest_missing_target_repair,
    run_python_unittest_runtime_failure_repair,
    run_python_unresolved_import_symbol_repair,
)
from .python_syntax import (
    PYTHON_PACKAGE_CHILD_REEXPORT_SOURCE_TOOL,
    PYTHON_PACKAGE_SHADOW_BRIDGE_SOURCE_TOOL,
    PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL,
    PYTHON_UNITTEST_MISSING_TARGET_SOURCE_TOOL,
    PYTHON_UNITTEST_RUNTIME_FAILURE_SOURCE_TOOL,
    PYTHON_UNRESOLVED_IMPORT_SYMBOL_SOURCE_TOOL,
)
from .registry import RepairCoverageReport, build_repair_coverage_report
from .rust_ast import RUST_MISSING_FIELDS_SOURCE_TOOL, RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL
from .rust_export_facade import RUST_LIB_ROOT_FACADE_SOURCE_TOOL, RUST_MISSING_LIB_TARGET_SOURCE_TOOL
from .rust_runtime import (
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
from .rust_syntax import (
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
    RUST_POST_SOURCE_TOOL,
    RUST_SERDE_DERIVE_SOURCE_TOOL,
    RUST_TRAIT_IMPORT_SOURCE_TOOL,
    RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL,
    RUST_UNUSED_IMPORT_SOURCE_TOOL,
    RUST_WRONG_CRATE_PATH_SOURCE_TOOL,
)
from .scheduler import (
    BaseFilesProviderFn,
    PlannerFn,
    RepairConvergenceResult,
    RepairConvergenceScheduler,
    VerifierFn,
    convergence_envelope_metadata,
)
from .typescript_runtime import (
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
    plan_typescript_canvas_scale_return_type_repair,
    plan_typescript_duplicate_object_property_repair,
    plan_typescript_enum_member_separator_repair,
    plan_typescript_missing_closing_brace_repair,
    plan_typescript_nullable_canvas_context_repair,
    plan_typescript_number_to_string_argument_repair,
    plan_typescript_object_literal_comma_repair,
    plan_typescript_readonly_assignment_repair,
    plan_typescript_runtime_repair_for_source_tool,
    run_typescript_canvas_scale_return_type_repair,
    run_typescript_duplicate_object_property_repair,
    run_typescript_enum_member_separator_repair,
    run_typescript_missing_closing_brace_repair,
    run_typescript_nullable_canvas_context_repair,
    run_typescript_number_to_string_argument_repair,
    run_typescript_object_literal_comma_repair,
    run_typescript_readonly_assignment_repair,
    run_typescript_runtime_repair_for_source_tool,
)
from .typescript_syntax import (
    HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
    JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL,
    TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL,
    TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL,
    TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL,
    TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL,
    TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL,
    TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL,
    TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL,
    TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL,
    TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL,
    TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL,
    TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
    TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL,
    TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
    TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
    TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL,
    TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL,
    TYPESCRIPT_READONLY_ASSIGNMENT_SOURCE_TOOL,
    TYPESCRIPT_REEXPORT_SOURCE_TOOL,
    TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,
    TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL,
    TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
    TYPESCRIPT_SCAFFOLD_SOURCE_TOOL,
    TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL,
    TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL,
    TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL,
    TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL,
    TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL,
    TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
    TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL,
    TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
    TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL,
    TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL,
)

RuntimePlannerFn = Callable[
    [Mapping[str, str], Sequence[str], Sequence[RepairAdvisorNote] | None, str],
    "RuntimeRepairPlanning",
]
RuntimeRunnerFn = Callable[
    [
        str | Path,
        Mapping[str, str],
        Sequence[str],
        WriteFileFn,
        EditFileFn | None,
        DeleteFileFn | None,
        Sequence[str] | None,
        Sequence[RepairAdvisorNote] | None,
        str,
    ],
    "RuntimeRepairRun",
]


@dataclass(frozen=True)
class RuntimeRepairPlanning:
    """Language-neutral planning result for one deterministic repair source tool."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RuntimeRepairRun:
    """Language-neutral execution result for one deterministic repair source tool."""

    planning: RuntimeRepairPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RuntimeRepairBinding:
    """Registered runtime planner/runner binding for one source tool."""

    source_tool: str
    language: str
    rule_id: str
    planner: RuntimePlannerFn
    runner: RuntimeRunnerFn

    def to_dict(self) -> dict[str, str]:
        return {
            "source_tool": self.source_tool,
            "language": self.language,
            "rule_id": self.rule_id,
        }


@dataclass(frozen=True)
class _DeleterBoundRepairExecutor(TransactionalRepairExecutor):
    deleter: DeleteFileFn

    def execute(
        self,
        *,
        workspace: Path,
        plan: RepairPlan,
        composition: CompositionResult,
        writer: WriteFileFn | None = None,
        editor: EditFileFn | None = None,
        deleter: DeleteFileFn | None = None,
    ) -> RepairExecutionResult:
        del deleter
        return TransactionalRepairExecutor().execute(
            workspace=workspace,
            plan=plan,
            composition=composition,
            writer=writer,
            editor=editor,
            deleter=self.deleter,
        )


def plan_runtime_repair(
    *,
    source_tool: str,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RuntimeRepairPlanning:
    """Plan one deterministic repair through a language-neutral runtime entrypoint."""

    normalized_source_tool = _normalize_source_tool(source_tool)
    notes = tuple(advisor_notes or ())
    binding = _RUNTIME_REPAIR_BINDINGS.get(normalized_source_tool)
    if binding is not None:
        return binding.planner(base_files, artifact_quality_errors, notes, mode)

    return RuntimeRepairPlanning(
        source_tool=normalized_source_tool,
        diagnostics=tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ()))),
        plan=None,
        composition=None,
        advisor_notes=notes,
        error_code="unsupported_repair_source_tool",
        error_message=f"No runtime planner is registered for source_tool={normalized_source_tool!r}.",
    )


def run_runtime_repair(
    *,
    source_tool: str,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    deleter: DeleteFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RuntimeRepairRun:
    """Run one deterministic repair through a language-neutral runtime entrypoint."""

    normalized_source_tool = _normalize_source_tool(source_tool)
    notes = tuple(advisor_notes or ())
    binding = _RUNTIME_REPAIR_BINDINGS.get(normalized_source_tool)
    if binding is not None:
        return binding.runner(
            workspace, base_files, artifact_quality_errors, writer, editor, deleter, allowed_paths, notes, mode
        )

    planning = RuntimeRepairPlanning(
        source_tool=normalized_source_tool,
        diagnostics=tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ()))),
        plan=None,
        composition=None,
        advisor_notes=notes,
        error_code="unsupported_repair_source_tool",
        error_message=f"No runtime executor is registered for source_tool={normalized_source_tool!r}.",
    )
    return RuntimeRepairRun(
        planning=planning,
        ok=False,
        error_code=planning.error_code,
        error_message=planning.error_message,
    )


def build_runtime_repair_convergence_planner(
    *,
    source_tools: Sequence[str],
    base_files: Mapping[str, str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PlannerFn:
    """Wrap executable runtime bindings as a convergence scheduler planner."""

    normalized_source_tools = _normalize_source_tools(source_tools)
    notes = tuple(advisor_notes or ())

    def planner(diagnostics: tuple[RepairDiagnostic, ...], round_number: int) -> tuple[RepairPlan, ...]:
        coverage_report = build_repair_coverage_report(diagnostics)
        plans: list[RepairPlan] = []
        for source_tool in normalized_source_tools:
            binding = _RUNTIME_REPAIR_BINDINGS.get(source_tool)
            if binding is None:
                continue
            tool_diagnostics = _executable_diagnostics_for_source_tool(coverage_report, source_tool)
            if not tool_diagnostics:
                continue
            artifact_quality_errors = _artifact_quality_errors_from_diagnostics(tool_diagnostics)
            planning = binding.planner(base_files, artifact_quality_errors, notes, mode)
            if planning.plan is None:
                continue
            plans.append(
                _with_runtime_convergence_plan_metadata(
                    planning.plan,
                    source_tool=source_tool,
                    round_number=round_number,
                    planning=planning,
                )
            )
        return tuple(plans)

    return planner


def run_runtime_repair_convergence(
    *,
    source_tools: Sequence[str],
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    verifier: VerifierFn,
    writer: WriteFileFn | None = None,
    editor: EditFileFn | None = None,
    deleter: DeleteFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
    max_rounds: int = 3,
    planner: PlannerFn | None = None,
    base_files_provider: BaseFilesProviderFn | None = None,
    previous_receipts: Sequence[RepairReceipt] = (),
) -> RepairConvergenceResult:
    """Run runtime repairs through the typed convergence scheduler envelope."""

    normalized_source_tools = _normalize_source_tools(source_tools)
    initial_diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    initial_coverage_report = build_repair_coverage_report(initial_diagnostics)
    native_coverage_gate_status = _native_coverage_gate_status(initial_coverage_report, normalized_source_tools)
    if planner is None and native_coverage_gate_status is not None:
        return RepairConvergenceResult(
            status=native_coverage_gate_status,
            final_diagnostics=initial_diagnostics,
            max_rounds=max_rounds,
            metadata=_runtime_convergence_metadata(
                status=native_coverage_gate_status,
                source_tools=normalized_source_tools,
                planner_override=False,
                extra={
                    **_coverage_metadata(
                        initial_coverage_report,
                        source_tools=normalized_source_tools,
                    ),
                    "error_code": native_coverage_gate_status,
                    "stopped_reason": native_coverage_gate_status,
                },
            ),
        )
    unsupported_source_tools = tuple(
        source_tool for source_tool in normalized_source_tools if source_tool not in _RUNTIME_REPAIR_BINDINGS
    )
    if unsupported_source_tools and planner is None:
        return RepairConvergenceResult(
            status="unsupported_repair_source_tool",
            final_diagnostics=initial_diagnostics,
            max_rounds=max_rounds,
            metadata=_runtime_convergence_metadata(
                status="unsupported_repair_source_tool",
                source_tools=normalized_source_tools,
                planner_override=False,
                extra={
                    **_coverage_metadata(
                        initial_coverage_report,
                        source_tools=normalized_source_tools,
                    ),
                    "error_code": "unsupported_repair_source_tool",
                    "unsupported_source_tools": list(unsupported_source_tools),
                },
            ),
        )
    if not normalized_source_tools and planner is None:
        return RepairConvergenceResult(
            status="stuck_no_plans",
            final_diagnostics=initial_diagnostics,
            max_rounds=max_rounds,
            metadata=_runtime_convergence_metadata(
                status="stuck_no_plans",
                source_tools=normalized_source_tools,
                planner_override=False,
                extra={
                    **_coverage_metadata(
                        initial_coverage_report,
                        source_tools=normalized_source_tools,
                    ),
                    "stopped_reason": "no_source_tools_supplied",
                },
            ),
        )

    selected_planner = planner or build_runtime_repair_convergence_planner(
        source_tools=normalized_source_tools,
        base_files=base_files,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    selected_base_files_provider = base_files_provider or (lambda _plan: base_files)
    scheduler_executor = _DeleterBoundRepairExecutor(deleter) if deleter is not None else None
    result = RepairConvergenceScheduler(max_rounds=max_rounds, executor=scheduler_executor).run(
        workspace=Path(workspace),
        verifier=verifier,
        planner=selected_planner,
        base_files_provider=selected_base_files_provider,
        writer=writer,
        editor=editor,
        allowed_paths=tuple(allowed_paths or ()),
        previous_receipts=tuple(previous_receipts or ()),
    )
    final_coverage_report = build_repair_coverage_report(result.final_diagnostics)
    final_coverage_gate_status = (
        _native_coverage_gate_status(final_coverage_report, normalized_source_tools)
        if planner is None and result.status == "stuck_no_plans"
        else None
    )
    if final_coverage_gate_status is not None:
        result = RepairConvergenceResult(
            status=final_coverage_gate_status,
            final_diagnostics=result.final_diagnostics,
            rounds=result.rounds,
            receipts=result.receipts,
            max_rounds=result.max_rounds,
            metadata={
                **dict(result.metadata),
                "scheduler_status": result.status,
                "stopped_reason": final_coverage_gate_status,
            },
        )
    return _with_runtime_convergence_metadata(
        result,
        source_tools=normalized_source_tools,
        planner_override=planner is not None,
        base_files_provider_kind="custom" if base_files_provider is not None else "static_mapping",
        initial_coverage_report=initial_coverage_report,
        final_coverage_report=final_coverage_report,
    )


def _normalize_source_tool(source_tool: str) -> str:
    return str(source_tool or "").strip()


def _normalize_source_tools(source_tools: Sequence[str]) -> tuple[str, ...]:
    raw_source_tools = (source_tools,) if isinstance(source_tools, str) else tuple(source_tools or ())
    normalized: list[str] = []
    seen: set[str] = set()
    for source_tool in raw_source_tools:
        value = _normalize_source_tool(source_tool)
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return tuple(normalized)


def _artifact_quality_errors_from_diagnostics(diagnostics: Sequence[RepairDiagnostic]) -> tuple[str, ...]:
    return tuple(
        diagnostic.raw or diagnostic.message or diagnostic.code
        for diagnostic in diagnostics or ()
        if diagnostic.raw or diagnostic.message or diagnostic.code
    )


def _executable_diagnostics_for_source_tool(
    report: RepairCoverageReport,
    source_tool: str,
) -> tuple[RepairDiagnostic, ...]:
    return tuple(
        item.diagnostic
        for item in report.items
        if any(rule.source_tool == source_tool and rule.runtime_plan_available for rule in item.matched_rules)
    )


def _selected_executable_runtime_plan_diagnostic_count(
    report: RepairCoverageReport,
    source_tools: Sequence[str],
) -> int:
    selected = {str(source_tool) for source_tool in source_tools}
    return sum(
        1
        for item in report.items
        if any(rule.source_tool in selected and rule.runtime_plan_available for rule in item.matched_rules)
    )


def _native_coverage_gate_status(
    report: RepairCoverageReport,
    source_tools: Sequence[str],
) -> str | None:
    if report.total_diagnostics == 0:
        return None
    if report.uncovered_diagnostic_count == report.total_diagnostics:
        return "coverage_gap_uncovered_diagnostics"
    if _selected_executable_runtime_plan_diagnostic_count(report, source_tools) == 0:
        return "stuck_no_executable_runtime_plan"
    return None


def _coverage_gate_status_label(
    report: RepairCoverageReport,
    source_tools: Sequence[str],
) -> str:
    gate_status = _native_coverage_gate_status(report, source_tools)
    if gate_status is not None:
        return gate_status
    if report.total_diagnostics == 0:
        return "no_diagnostics"
    if report.uncovered_diagnostic_count > 0:
        return "partial_coverage_gaps_with_executable_runtime_plan"
    return "covered_with_executable_runtime_plan"


def _coverage_metadata(
    report: RepairCoverageReport,
    *,
    source_tools: Sequence[str],
) -> dict[str, Any]:
    payload = report.to_dict()
    selected_executable_count = _selected_executable_runtime_plan_diagnostic_count(report, source_tools)
    return {
        "coverage_gate_native": True,
        "coverage_report": payload,
        "coverage_gap_count": int(payload["coverage_gap_count"]),
        "coverage_gaps": list(payload["coverage_gaps"]),
        "uncovered_diagnostics": list(payload["uncovered_diagnostics"]),
        "total_diagnostics": report.total_diagnostics,
        "covered_diagnostic_count": report.covered_diagnostic_count,
        "uncovered_diagnostic_count": report.uncovered_diagnostic_count,
        "executable_runtime_plan_diagnostic_count": report.executable_runtime_plan_diagnostic_count,
        "metadata_only_diagnostic_count": report.metadata_only_diagnostic_count,
        "selected_executable_runtime_plan_diagnostic_count": selected_executable_count,
        "coverage_fully_covered": report.total_diagnostics == 0 or report.uncovered_diagnostic_count == 0,
        "coverage_has_executable_runtime_plan": selected_executable_count > 0,
        "coverage_gate_status": _coverage_gate_status_label(report, source_tools),
    }


def _final_coverage_metadata(
    report: RepairCoverageReport,
    *,
    source_tools: Sequence[str],
) -> dict[str, Any]:
    payload = report.to_dict()
    return {
        "final_coverage_report": payload,
        "residual_coverage_gap_count": int(payload["coverage_gap_count"]),
        "residual_coverage_gaps": list(payload["coverage_gaps"]),
        "residual_uncovered_diagnostics": list(payload["uncovered_diagnostics"]),
        "residual_uncovered_diagnostic_count": report.uncovered_diagnostic_count,
        "residual_executable_runtime_plan_diagnostic_count": report.executable_runtime_plan_diagnostic_count,
        "residual_metadata_only_diagnostic_count": report.metadata_only_diagnostic_count,
        "residual_selected_executable_runtime_plan_diagnostic_count": (
            _selected_executable_runtime_plan_diagnostic_count(report, source_tools)
        ),
        "residual_coverage_fully_covered": report.total_diagnostics == 0 or report.uncovered_diagnostic_count == 0,
        "residual_coverage_gate_status": _coverage_gate_status_label(report, source_tools),
    }


def _with_runtime_convergence_plan_metadata(
    plan: RepairPlan,
    *,
    source_tool: str,
    round_number: int,
    planning: RuntimeRepairPlanning,
) -> RepairPlan:
    return RepairPlan(
        rule_id=plan.rule_id,
        source_tool=plan.source_tool,
        operations=plan.operations,
        diagnostics=plan.diagnostics,
        plan_id=plan.plan_id,
        mode=plan.mode,
        risk_level=plan.risk_level,
        priority=plan.priority,
        depends_on=plan.depends_on,
        advisor_notes=plan.advisor_notes,
        metadata={
            **dict(plan.metadata),
            "runtime_convergence_planner": True,
            "runtime_convergence_source_tool": source_tool,
            "runtime_convergence_round_number": round_number,
            "runtime_planning_error_code": planning.error_code,
            "runtime_planning_error_message": planning.error_message,
        },
    )


def _runtime_convergence_metadata(
    *,
    status: str,
    source_tools: Sequence[str],
    planner_override: bool,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    binding_payloads = [
        _RUNTIME_REPAIR_BINDINGS[source_tool].to_dict()
        for source_tool in source_tools
        if source_tool in _RUNTIME_REPAIR_BINDINGS
    ]
    return {
        **convergence_envelope_metadata(preferred_entrypoint="run_runtime_repair_convergence"),
        "legacy_single_repair_entrypoint": "run_runtime_repair",
        "source_tools": list(source_tools),
        "runtime_bindings": binding_payloads,
        "runtime_binding_count": len(binding_payloads),
        "planner_override": bool(planner_override),
        "status": str(status or "unknown"),
        "converged": status in {"already_clean", "converged"},
        "unconverged": status not in {"already_clean", "converged"},
        "produces_tool_results_only": False,
        **dict(extra or {}),
    }


def _with_runtime_convergence_metadata(
    result: RepairConvergenceResult,
    *,
    source_tools: Sequence[str],
    planner_override: bool,
    base_files_provider_kind: str,
    initial_coverage_report: RepairCoverageReport,
    final_coverage_report: RepairCoverageReport,
) -> RepairConvergenceResult:
    failed_revalidation_count = sum(1 for receipt in result.receipts if receipt.status == "failed_revalidation")
    metadata = {
        **dict(result.metadata),
        **_runtime_convergence_metadata(
            status=result.status,
            source_tools=source_tools,
            planner_override=planner_override,
            extra={
                "base_files_provider": base_files_provider_kind,
                "receipt_count": len(result.receipts),
                "round_count": len(result.rounds),
                "final_error_count": len(result.final_diagnostics),
                "failed_revalidation_receipt_count": failed_revalidation_count,
                **_coverage_metadata(
                    initial_coverage_report,
                    source_tools=source_tools,
                ),
                **_final_coverage_metadata(
                    final_coverage_report,
                    source_tools=source_tools,
                ),
            },
        ),
    }
    return RepairConvergenceResult(
        status=result.status,
        final_diagnostics=result.final_diagnostics,
        rounds=result.rounds,
        receipts=result.receipts,
        max_rounds=result.max_rounds,
        metadata=metadata,
    )


def runtime_repair_bindings() -> tuple[dict[str, str], ...]:
    """Return registered executable runtime repair bindings without callables."""

    return tuple(_RUNTIME_REPAIR_BINDINGS[source_tool].to_dict() for source_tool in sorted(_RUNTIME_REPAIR_BINDINGS))


def runtime_repair_source_tools() -> tuple[str, ...]:
    """Return source tools with executable runtime bindings."""

    return tuple(sorted(_RUNTIME_REPAIR_BINDINGS))


def _plan_typescript_object_literal_comma(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_typescript_object_literal_comma_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_typescript(planning)


def _plan_typescript_nullable_canvas_context(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_typescript_nullable_canvas_context_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_typescript(planning)


def _plan_typescript_duplicate_object_property(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_typescript_duplicate_object_property_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_typescript(planning)


def _plan_typescript_enum_member_separator(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_typescript_enum_member_separator_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_typescript(planning)


def _plan_typescript_missing_closing_brace(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_typescript_missing_closing_brace_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_typescript(planning)


def _plan_typescript_number_to_string_argument(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_typescript_number_to_string_argument_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_typescript(planning)


def _plan_typescript_readonly_assignment(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_typescript_readonly_assignment_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_typescript(planning)


def _plan_typescript_canvas_scale_return_type(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_typescript_canvas_scale_return_type_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_typescript(planning)


def _plan_npm_script_contract(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_npm_script_contract_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_javascript(planning)


def _plan_node_test_script_contract(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_node_test_script_contract_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_javascript(planning)


def _plan_javascript_test_missing_target(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_javascript_test_missing_target_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_javascript(planning)


def _plan_javascript_missing_export(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_javascript_missing_export_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_javascript(planning)


def _plan_javascript_esm_commonjs_entrypoint(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_javascript_esm_commonjs_entrypoint_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_javascript(planning)


def _plan_javascript_dom_global_runtime_guard(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_javascript_dom_global_runtime_guard_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_javascript(planning)


def _plan_javascript_missing_method_runtime(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_javascript_missing_method_runtime_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_javascript(planning)


def _plan_python_unittest_missing_target(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_python_unittest_missing_target_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_python(planning)


def _plan_python_unittest_runtime_failure(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_python_unittest_runtime_failure_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_python(planning)


def _plan_python_readme_required_token(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_python_readme_required_token_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_python(planning)


def _plan_python_package_child_reexport(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_python_package_child_reexport_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_python(planning)


def _plan_python_package_shadow_bridge(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_python_package_shadow_bridge_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_python(planning)


def _plan_python_unresolved_import_symbol(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_python_unresolved_import_symbol_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_python(planning)


def _plan_go_bare_import_string(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_bare_import_string_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_nested_import(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_nested_import_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_bare_local_import(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_bare_local_import_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_module_import(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_module_import_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_dedup(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_dedup_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_subpath_import(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_subpath_import_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_unused_import(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_unused_import_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_error_string_helper(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_error_string_helper_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_rust_dependency(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_dependency_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_crate_import(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_crate_import_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_crate_import_rewrite(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_crate_import_rewrite_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_duplicate_module_file(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_duplicate_module_file_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_incompatible_copy_derive(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_incompatible_copy_derive_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_method_self_signature(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_method_self_signature_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_missing_binary_entrypoint(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_missing_binary_entrypoint_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_missing_lib_target(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_missing_lib_target_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_lib_root_facade(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_lib_root_facade_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_missing_module_file(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_missing_module_file_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_struct_literal_missing_field(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_struct_literal_missing_field_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_missing_fields(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_missing_fields_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_post(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    del base_files, mode
    return RuntimeRepairPlanning(
        source_tool=RUST_POST_SOURCE_TOOL,
        diagnostics=tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ()))),
        plan=None,
        composition=None,
        advisor_notes=tuple(advisor_notes or ()),
    )


def _plan_rust_line_suggestion(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_line_suggestion_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_field_rename_suggestion(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_field_rename_suggestion_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_wrong_crate_path(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_wrong_crate_path_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_serde_derive(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_serde_derive_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_missing_trait_derive(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_missing_trait_derive_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_trait_import(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_trait_import_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_unused_import(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_unused_import_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_rust_unresolved_pub_use(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_rust_unresolved_pub_use_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_rust(planning)


def _plan_patch_residue_cleanup(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_patch_residue_cleanup_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_patch_residue_cleanup(planning)


def _plan_generic_hygiene(source_tool: str) -> RuntimePlannerFn:
    def planner(
        base_files: Mapping[str, str],
        artifact_quality_errors: Sequence[str],
        advisor_notes: Sequence[RepairAdvisorNote] | None,
        mode: str,
    ) -> RuntimeRepairPlanning:
        planning = plan_generic_hygiene_repair(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=artifact_quality_errors,
            advisor_notes=advisor_notes,
            mode=mode,
        )
        return _runtime_planning_from_generic_hygiene(planning)

    return planner


def _plan_cpp_include_path(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_cpp_include_path_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_cpp(planning)


def _plan_cpp_standard_include(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_cpp_standard_include_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_cpp(planning)


def _plan_cpp_placeholder_declaration(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_cpp_placeholder_declaration_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_cpp(planning)


def _plan_cpp_missing_private_members(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_cpp_missing_private_members_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_cpp(planning)


def _plan_cpp_struct_getter_field_access(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_cpp_struct_getter_field_access_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_cpp(planning)


def _plan_cpp_post(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_cpp_post_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_cpp(planning)


def _plan_java_accessor_alias(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_java_accessor_alias_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_java(planning)


def _plan_java_post(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_java_post_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_java(planning)


def _plan_java_test_dependency(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    normalized_base = _normalize_runtime_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    notes = tuple(advisor_notes or ())
    plan = build_java_test_dependency_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    if plan is None:
        return RuntimeRepairPlanning(
            source_tool=JAVA_TEST_DEPENDENCY_SOURCE_TOOL,
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            advisor_notes=notes,
        )
    if notes:
        plan = replace(plan, advisor_notes=notes)
    composition = PatchComposer().compose(normalized_base, plan.operations)
    return RuntimeRepairPlanning(
        source_tool=plan.source_tool,
        diagnostics=tuple(plan.diagnostics),
        plan=plan,
        composition=composition,
        advisor_notes=notes,
    )


def _run_typescript_object_literal_comma(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_object_literal_comma_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_nullable_canvas_context(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_nullable_canvas_context_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_duplicate_object_property(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_duplicate_object_property_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_enum_member_separator(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_enum_member_separator_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_missing_closing_brace(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_missing_closing_brace_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_number_to_string_argument(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_number_to_string_argument_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_readonly_assignment(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    del deleter
    run = run_typescript_readonly_assignment_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_canvas_scale_return_type(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_canvas_scale_return_type_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _plan_typescript_runtime_source_tool(source_tool: str) -> RuntimePlannerFn:
    normalized_source_tool = str(source_tool or "").strip()

    def planner(
        base_files: Mapping[str, str],
        artifact_quality_errors: Sequence[str],
        advisor_notes: Sequence[RepairAdvisorNote] | None,
        mode: str,
    ) -> RuntimeRepairPlanning:
        planning = plan_typescript_runtime_repair_for_source_tool(
            source_tool=normalized_source_tool,
            base_files=base_files,
            artifact_quality_errors=artifact_quality_errors,
            advisor_notes=advisor_notes,
            mode=mode,
        )
        return _runtime_planning_from_typescript_runtime(planning)

    return planner


def _run_typescript_runtime_source_tool(source_tool: str) -> RuntimeRunnerFn:
    normalized_source_tool = str(source_tool or "").strip()

    def runner(
        workspace: str | Path,
        base_files: Mapping[str, str],
        artifact_quality_errors: Sequence[str],
        writer: WriteFileFn,
        editor: EditFileFn | None,
        deleter: DeleteFileFn | None,
        allowed_paths: Sequence[str] | None,
        advisor_notes: Sequence[RepairAdvisorNote] | None,
        mode: str,
    ) -> RuntimeRepairRun:
        del deleter
        run = run_typescript_runtime_repair_for_source_tool(
            source_tool=normalized_source_tool,
            workspace=workspace,
            base_files=base_files,
            artifact_quality_errors=artifact_quality_errors,
            writer=writer,
            editor=editor,
            allowed_paths=allowed_paths,
            advisor_notes=advisor_notes,
            mode=mode,
        )
        return _runtime_run_from_typescript_runtime(run)

    return runner


def _run_npm_script_contract(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_npm_script_contract_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_node_test_script_contract(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_node_test_script_contract_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_javascript_test_missing_target(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_javascript_test_missing_target_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_javascript_missing_export(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_javascript_missing_export_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_javascript_esm_commonjs_entrypoint(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_javascript_esm_commonjs_entrypoint_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_javascript_dom_global_runtime_guard(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_javascript_dom_global_runtime_guard_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_javascript_missing_method_runtime(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_javascript_missing_method_runtime_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_python_unittest_missing_target(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_python_unittest_missing_target_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_python(run)


def _run_python_unittest_runtime_failure(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_python_unittest_runtime_failure_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_python(run)


def _run_python_readme_required_token(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_python_readme_required_token_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_python(run)


def _run_python_package_child_reexport(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_python_package_child_reexport_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_python(run)


def _run_python_package_shadow_bridge(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_python_package_shadow_bridge_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_python(run)


def _run_python_unresolved_import_symbol(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_python_unresolved_import_symbol_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_python(run)


def _run_go_bare_import_string(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_go_bare_import_string_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_go(run)


def _run_go_nested_import(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_go_nested_import_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_go(run)


def _run_go_bare_local_import(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_go_bare_local_import_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_go(run)


def _run_go_subpath_import(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_go_subpath_import_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_go(run)


def _run_go_module_import(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_go_module_import_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_go(run)


def _run_go_dedup(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_go_dedup_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_go(run)


def _run_go_unused_import(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_go_unused_import_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_go(run)


def _run_go_error_string_helper(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_go_error_string_helper_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_go(run)


def _run_rust_dependency(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_dependency_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_crate_import(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_crate_import_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_crate_import_rewrite(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_crate_import_rewrite_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_duplicate_module_file(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_duplicate_module_file_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        deleter=deleter,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_incompatible_copy_derive(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_incompatible_copy_derive_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_method_self_signature(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_method_self_signature_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_missing_binary_entrypoint(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_missing_binary_entrypoint_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_missing_lib_target(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_missing_lib_target_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_lib_root_facade(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    del deleter
    run = run_rust_lib_root_facade_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_missing_module_file(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_missing_module_file_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_struct_literal_missing_field(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_struct_literal_missing_field_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_missing_fields(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_missing_fields_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_post(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    del workspace, writer, editor, deleter, allowed_paths
    planning = _plan_rust_post(
        base_files,
        artifact_quality_errors,
        advisor_notes,
        mode,
    )
    return RuntimeRepairRun(
        planning=planning,
        ok=False,
        error_code="repair_not_planned",
        error_message="No conservative Rust post-execution aggregate repair plan was produced.",
    )


def _run_rust_line_suggestion(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_line_suggestion_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_field_rename_suggestion(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_field_rename_suggestion_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_wrong_crate_path(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_wrong_crate_path_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_serde_derive(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_serde_derive_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_missing_trait_derive(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_missing_trait_derive_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_trait_import(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_trait_import_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_unused_import(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_unused_import_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_rust_unresolved_pub_use(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_rust_unresolved_pub_use_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_rust(run)


def _run_patch_residue_cleanup(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_patch_residue_cleanup_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_patch_residue_cleanup(run)


def _run_generic_hygiene(source_tool: str) -> RuntimeRunnerFn:
    def runner(
        workspace: str | Path,
        base_files: Mapping[str, str],
        artifact_quality_errors: Sequence[str],
        writer: WriteFileFn,
        editor: EditFileFn | None,
        deleter: DeleteFileFn | None,
        allowed_paths: Sequence[str] | None,
        advisor_notes: Sequence[RepairAdvisorNote] | None,
        mode: str,
    ) -> RuntimeRepairRun:
        run = run_generic_hygiene_repair(
            source_tool=source_tool,
            workspace=workspace,
            base_files=base_files,
            artifact_quality_errors=artifact_quality_errors,
            writer=writer,
            editor=editor,
            deleter=deleter,
            allowed_paths=allowed_paths,
            advisor_notes=advisor_notes,
            mode=mode,
        )
        return _runtime_run_from_generic_hygiene(run)

    return runner


def _run_cpp_include_path(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_cpp_include_path_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_cpp(run)


def _run_cpp_standard_include(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_cpp_standard_include_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_cpp(run)


def _run_cpp_placeholder_declaration(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_cpp_placeholder_declaration_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_cpp(run)


def _run_cpp_missing_private_members(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_cpp_missing_private_members_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_cpp(run)


def _run_cpp_struct_getter_field_access(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_cpp_struct_getter_field_access_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_cpp(run)


def _run_cpp_post(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_cpp_post_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_cpp(run)


def _run_java_accessor_alias(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_java_accessor_alias_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_java(run)


def _run_java_post(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_java_post_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_java(run)


def _run_java_test_dependency(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    normalized_base = _normalize_runtime_base_files(base_files)
    planning = _plan_java_test_dependency(
        normalized_base,
        artifact_quality_errors,
        advisor_notes,
        mode,
    )
    if planning.plan is None:
        return RuntimeRepairRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Java test dependency repair plan.",
        )
    if planning.composition is None:
        return RuntimeRepairRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Java test dependency repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            _normalize_runtime_repair_path(str(path or "")) for path in (allowed_paths or normalized_base.keys())
        )
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RuntimeRepairRun(
            planning=planning,
            ok=False,
            plan_decision=plan_decision,
            composition_decision=composition_decision,
            error_code="repair_policy_denied",
            error_message="Director Runtime repair policy denied the plan or composition.",
        )

    execution_result = TransactionalRepairExecutor().execute(
        workspace=Path(str(workspace)).resolve(),
        plan=planning.plan,
        composition=planning.composition,
        writer=writer,
        editor=editor,
        deleter=deleter,
    )
    return RuntimeRepairRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


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


def _normalize_runtime_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    return {
        _normalize_runtime_repair_path(str(path or "")): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_runtime_repair_path(str(path or ""))
    }


def _normalize_runtime_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


_TYPESCRIPT_RUNTIME_MIGRATION_BINDINGS: tuple[tuple[str, str, str], ...] = (
    (HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL, "html", "html.typescript_module_script"),
    (JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL, "javascript", "typescript.javascript_annotation_cleanup"),
    (TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL, "typescript", "typescript.typeorm_model_normalization"),
    (TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL, "typescript", "typescript.commonjs_package_type"),
    (TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL, "typescript", "typescript.entrypoint"),
    (TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL, "typescript", "typescript.escaped_newline"),
    (TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL, "typescript", "typescript.hyphenated_identifier"),
    (TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL, "html", "typescript.html_container_selector"),
    (
        TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL,
        "typescript",
        "typescript.import_specifier_keyword",
    ),
    (TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL, "typescript", "typescript.member_alias"),
    (TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL, "typescript", "typescript.missing_export"),
    (TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL, "typescript", "typescript.missing_member"),
    (TYPESCRIPT_REEXPORT_SOURCE_TOOL, "typescript", "typescript.reexport"),
    (TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL, "typescript", "typescript.reexported_type_binding"),
    (TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL, "typescript", "typescript.relative_import_case"),
    (TYPESCRIPT_SCAFFOLD_SOURCE_TOOL, "typescript", "typescript.scaffold"),
    (TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL, "typescript", "typescript.sourcefile_diagnostics"),
    (TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL, "typescript", "typescript.too_few_arguments"),
    (TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL, "typescript", "typescript.tsconfig_lib"),
    (TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL, "typescript", "typescript.tsconfig_rootdir"),
    (TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL, "typescript", "typescript.uninitialized_property"),
    (TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL, "typescript", "typescript.unique_export_import"),
    (TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL, "typescript", "typescript.unresolved_identifier"),
    (TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL, "typescript", "typescript.unused_import"),
    (TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL, "typescript", "typescript.vitest_globals"),
    (TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL, "typescript", "typescript.zod_type_class_collision"),
)


_RUNTIME_REPAIR_BINDINGS: dict[str, RuntimeRepairBinding] = {
    CPP_INCLUDE_PATH_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=CPP_INCLUDE_PATH_SOURCE_TOOL,
        language="cpp",
        rule_id="cpp.include_path",
        planner=_plan_cpp_include_path,
        runner=_run_cpp_include_path,
    ),
    CPP_MISSING_PRIVATE_MEMBERS_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=CPP_MISSING_PRIVATE_MEMBERS_SOURCE_TOOL,
        language="cpp",
        rule_id="cpp.missing_private_members",
        planner=_plan_cpp_missing_private_members,
        runner=_run_cpp_missing_private_members,
    ),
    CPP_PLACEHOLDER_DECLARATION_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=CPP_PLACEHOLDER_DECLARATION_SOURCE_TOOL,
        language="cpp",
        rule_id="cpp.placeholder_declaration",
        planner=_plan_cpp_placeholder_declaration,
        runner=_run_cpp_placeholder_declaration,
    ),
    CPP_POST_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=CPP_POST_SOURCE_TOOL,
        language="cpp",
        rule_id="cpp.post_execution_conservative",
        planner=_plan_cpp_post,
        runner=_run_cpp_post,
    ),
    CPP_STANDARD_INCLUDE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=CPP_STANDARD_INCLUDE_SOURCE_TOOL,
        language="cpp",
        rule_id="cpp.standard_include",
        planner=_plan_cpp_standard_include,
        runner=_run_cpp_standard_include,
    ),
    CPP_STRUCT_GETTER_FIELD_ACCESS_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=CPP_STRUCT_GETTER_FIELD_ACCESS_SOURCE_TOOL,
        language="cpp",
        rule_id="cpp.struct_getter_field_access",
        planner=_plan_cpp_struct_getter_field_access,
        runner=_run_cpp_struct_getter_field_access,
    ),
    GO_BARE_IMPORT_STRING_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=GO_BARE_IMPORT_STRING_SOURCE_TOOL,
        language="go",
        rule_id="go.bare_import_string",
        planner=_plan_go_bare_import_string,
        runner=_run_go_bare_import_string,
    ),
    GO_BARE_LOCAL_IMPORT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=GO_BARE_LOCAL_IMPORT_SOURCE_TOOL,
        language="go",
        rule_id="go.bare_local_import",
        planner=_plan_go_bare_local_import,
        runner=_run_go_bare_local_import,
    ),
    GO_DEDUP_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=GO_DEDUP_SOURCE_TOOL,
        language="go",
        rule_id="go.intra_file_dedup",
        planner=_plan_go_dedup,
        runner=_run_go_dedup,
    ),
    GO_MODULE_IMPORT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=GO_MODULE_IMPORT_SOURCE_TOOL,
        language="go",
        rule_id="go.module_import_path",
        planner=_plan_go_module_import,
        runner=_run_go_module_import,
    ),
    GO_NESTED_IMPORT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=GO_NESTED_IMPORT_SOURCE_TOOL,
        language="go",
        rule_id="go.nested_import_keyword",
        planner=_plan_go_nested_import,
        runner=_run_go_nested_import,
    ),
    GO_SUBPATH_IMPORT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=GO_SUBPATH_IMPORT_SOURCE_TOOL,
        language="go",
        rule_id="go.import_subpath",
        planner=_plan_go_subpath_import,
        runner=_run_go_subpath_import,
    ),
    GO_UNUSED_IMPORT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=GO_UNUSED_IMPORT_SOURCE_TOOL,
        language="go",
        rule_id="go.unused_import",
        planner=_plan_go_unused_import,
        runner=_run_go_unused_import,
    ),
    GO_ERROR_STRING_HELPER_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=GO_ERROR_STRING_HELPER_SOURCE_TOOL,
        language="go",
        rule_id="go.error_string_helper",
        planner=_plan_go_error_string_helper,
        runner=_run_go_error_string_helper,
    ),
    RUST_DEPENDENCY_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_DEPENDENCY_SOURCE_TOOL,
        language="rust",
        rule_id="rust.unlinked_crate_dependency",
        planner=_plan_rust_dependency,
        runner=_run_rust_dependency,
    ),
    RUST_CRATE_IMPORT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_CRATE_IMPORT_SOURCE_TOOL,
        language="rust",
        rule_id="rust.unresolved_import_path",
        planner=_plan_rust_crate_import,
        runner=_run_rust_crate_import,
    ),
    RUST_CRATE_IMPORT_REWRITE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_CRATE_IMPORT_REWRITE_SOURCE_TOOL,
        language="rust",
        rule_id="rust.crate_import_rewrite",
        planner=_plan_rust_crate_import_rewrite,
        runner=_run_rust_crate_import_rewrite,
    ),
    RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
        language="rust",
        rule_id="rust.duplicate_module_file",
        planner=_plan_rust_duplicate_module_file,
        runner=_run_rust_duplicate_module_file,
    ),
    RUST_INCOMPATIBLE_COPY_DERIVE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_INCOMPATIBLE_COPY_DERIVE_SOURCE_TOOL,
        language="rust",
        rule_id="rust.incompatible_copy_derive",
        planner=_plan_rust_incompatible_copy_derive,
        runner=_run_rust_incompatible_copy_derive,
    ),
    RUST_LINE_SUGGESTION_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_LINE_SUGGESTION_SOURCE_TOOL,
        language="rust",
        rule_id="rust.line_suggestion",
        planner=_plan_rust_line_suggestion,
        runner=_run_rust_line_suggestion,
    ),
    RUST_LIB_ROOT_FACADE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        language="rust",
        rule_id="rust.lib_root_facade_path_rewrite",
        planner=_plan_rust_lib_root_facade,
        runner=_run_rust_lib_root_facade,
    ),
    RUST_FIELD_RENAME_SUGGESTION_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_FIELD_RENAME_SUGGESTION_SOURCE_TOOL,
        language="rust",
        rule_id="rust.field_rename_suggestion",
        planner=_plan_rust_field_rename_suggestion,
        runner=_run_rust_field_rename_suggestion,
    ),
    RUST_WRONG_CRATE_PATH_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_WRONG_CRATE_PATH_SOURCE_TOOL,
        language="rust",
        rule_id="rust.wrong_crate_path",
        planner=_plan_rust_wrong_crate_path,
        runner=_run_rust_wrong_crate_path,
    ),
    RUST_METHOD_SELF_SIGNATURE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_METHOD_SELF_SIGNATURE_SOURCE_TOOL,
        language="rust",
        rule_id="rust.method_self_signature",
        planner=_plan_rust_method_self_signature,
        runner=_run_rust_method_self_signature,
    ),
    RUST_MISSING_BINARY_ENTRYPOINT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_MISSING_BINARY_ENTRYPOINT_SOURCE_TOOL,
        language="rust",
        rule_id="rust.missing_binary_entrypoint",
        planner=_plan_rust_missing_binary_entrypoint,
        runner=_run_rust_missing_binary_entrypoint,
    ),
    RUST_MISSING_LIB_TARGET_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_MISSING_LIB_TARGET_SOURCE_TOOL,
        language="rust",
        rule_id="rust.missing_lib_target_src_lib",
        planner=_plan_rust_missing_lib_target,
        runner=_run_rust_missing_lib_target,
    ),
    RUST_MISSING_MODULE_FILE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_MISSING_MODULE_FILE_SOURCE_TOOL,
        language="rust",
        rule_id="rust.missing_module_file",
        planner=_plan_rust_missing_module_file,
        runner=_run_rust_missing_module_file,
    ),
    RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_STRUCT_LITERAL_MISSING_FIELD_SOURCE_TOOL,
        language="rust",
        rule_id="rust.struct_literal_missing_field_initializer",
        planner=_plan_rust_struct_literal_missing_field,
        runner=_run_rust_struct_literal_missing_field,
    ),
    RUST_MISSING_FIELDS_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_MISSING_FIELDS_SOURCE_TOOL,
        language="rust",
        rule_id="rust.missing_struct_field_declaration",
        planner=_plan_rust_missing_fields,
        runner=_run_rust_missing_fields,
    ),
    RUST_POST_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_POST_SOURCE_TOOL,
        language="rust",
        rule_id="rust.post_execution_convergence",
        planner=_plan_rust_post,
        runner=_run_rust_post,
    ),
    RUST_SERDE_DERIVE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_SERDE_DERIVE_SOURCE_TOOL,
        language="rust",
        rule_id="rust.serde_derive",
        planner=_plan_rust_serde_derive,
        runner=_run_rust_serde_derive,
    ),
    RUST_MISSING_TRAIT_DERIVE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_MISSING_TRAIT_DERIVE_SOURCE_TOOL,
        language="rust",
        rule_id="rust.missing_trait_derive",
        planner=_plan_rust_missing_trait_derive,
        runner=_run_rust_missing_trait_derive,
    ),
    RUST_TRAIT_IMPORT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_TRAIT_IMPORT_SOURCE_TOOL,
        language="rust",
        rule_id="rust.trait_import",
        planner=_plan_rust_trait_import,
        runner=_run_rust_trait_import,
    ),
    RUST_UNUSED_IMPORT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_UNUSED_IMPORT_SOURCE_TOOL,
        language="rust",
        rule_id="rust.unused_import",
        planner=_plan_rust_unused_import,
        runner=_run_rust_unused_import,
    ),
    RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL,
        language="rust",
        rule_id="rust.unresolved_pub_use",
        planner=_plan_rust_unresolved_pub_use,
        runner=_run_rust_unresolved_pub_use,
    ),
    DECLARED_TARGET_CONTRACT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=DECLARED_TARGET_CONTRACT_SOURCE_TOOL,
        language="generic",
        rule_id="generic.declared_target_contract",
        planner=_plan_generic_hygiene(DECLARED_TARGET_CONTRACT_SOURCE_TOOL),
        runner=_run_generic_hygiene(DECLARED_TARGET_CONTRACT_SOURCE_TOOL),
    ),
    MISSING_DECLARED_TARGET_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=MISSING_DECLARED_TARGET_SOURCE_TOOL,
        language="generic",
        rule_id="generic.missing_declared_target",
        planner=_plan_generic_hygiene(MISSING_DECLARED_TARGET_SOURCE_TOOL),
        runner=_run_generic_hygiene(MISSING_DECLARED_TARGET_SOURCE_TOOL),
    ),
    PATCH_RESIDUE_CLEANUP_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=PATCH_RESIDUE_CLEANUP_SOURCE_TOOL,
        language="generic",
        rule_id="generic.patch_residue_cleanup",
        planner=_plan_patch_residue_cleanup,
        runner=_run_patch_residue_cleanup,
    ),
    PRE_MATERIALIZATION_DECLARED_TARGET_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=PRE_MATERIALIZATION_DECLARED_TARGET_SOURCE_TOOL,
        language="generic",
        rule_id="generic.pre_materialization_declared_target",
        planner=_plan_generic_hygiene(PRE_MATERIALIZATION_DECLARED_TARGET_SOURCE_TOOL),
        runner=_run_generic_hygiene(PRE_MATERIALIZATION_DECLARED_TARGET_SOURCE_TOOL),
    ),
    QUALITY_REPAIR_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=QUALITY_REPAIR_SOURCE_TOOL,
        language="generic",
        rule_id="generic.quality_repair",
        planner=_plan_generic_hygiene(QUALITY_REPAIR_SOURCE_TOOL),
        runner=_run_generic_hygiene(QUALITY_REPAIR_SOURCE_TOOL),
    ),
    RUNTIME_DEPENDENCY_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUNTIME_DEPENDENCY_SOURCE_TOOL,
        language="dependency",
        rule_id="generic.runtime_dependency",
        planner=_plan_generic_hygiene(RUNTIME_DEPENDENCY_SOURCE_TOOL),
        runner=_run_generic_hygiene(RUNTIME_DEPENDENCY_SOURCE_TOOL),
    ),
    SCAFFOLD_MARKER_CLEANUP_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=SCAFFOLD_MARKER_CLEANUP_SOURCE_TOOL,
        language="generic",
        rule_id="generic.scaffold_marker_cleanup",
        planner=_plan_generic_hygiene(SCAFFOLD_MARKER_CLEANUP_SOURCE_TOOL),
        runner=_run_generic_hygiene(SCAFFOLD_MARKER_CLEANUP_SOURCE_TOOL),
    ),
    SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL,
        language="generic",
        rule_id="generic.scaffold_marker_quality_cleanup",
        planner=_plan_generic_hygiene(SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL),
        runner=_run_generic_hygiene(SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL),
    ),
    SCAFFOLD_RESIDUE_CLEANUP_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=SCAFFOLD_RESIDUE_CLEANUP_SOURCE_TOOL,
        language="generic",
        rule_id="generic.scaffold_residue_cleanup",
        planner=_plan_generic_hygiene(SCAFFOLD_RESIDUE_CLEANUP_SOURCE_TOOL),
        runner=_run_generic_hygiene(SCAFFOLD_RESIDUE_CLEANUP_SOURCE_TOOL),
    ),
    JAVA_ACCESSOR_ALIAS_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=JAVA_ACCESSOR_ALIAS_SOURCE_TOOL,
        language="java",
        rule_id="java.common_accessor_aliases",
        planner=_plan_java_accessor_alias,
        runner=_run_java_accessor_alias,
    ),
    JAVA_POST_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=JAVA_POST_SOURCE_TOOL,
        language="java",
        rule_id="java.post_execution_conservative",
        planner=_plan_java_post,
        runner=_run_java_post,
    ),
    JAVA_TEST_DEPENDENCY_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=JAVA_TEST_DEPENDENCY_SOURCE_TOOL,
        language="java",
        rule_id="java.junit_test_dependency",
        planner=_plan_java_test_dependency,
        runner=_run_java_test_dependency,
    ),
    NPM_SCRIPT_CONTRACT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
        language="javascript",
        rule_id="javascript.npm_script_contract",
        planner=_plan_npm_script_contract,
        runner=_run_npm_script_contract,
    ),
    NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL,
        language="javascript",
        rule_id="javascript.node_test_script_contract",
        planner=_plan_node_test_script_contract,
        runner=_run_node_test_script_contract,
    ),
    JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
        language="javascript",
        rule_id="javascript.test_missing_target",
        planner=_plan_javascript_test_missing_target,
        runner=_run_javascript_test_missing_target,
    ),
    JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
        language="javascript",
        rule_id="javascript.missing_named_export",
        planner=_plan_javascript_missing_export,
        runner=_run_javascript_missing_export,
    ),
    JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL,
        language="javascript",
        rule_id="javascript.commonjs_esm_entrypoint",
        planner=_plan_javascript_esm_commonjs_entrypoint,
        runner=_run_javascript_esm_commonjs_entrypoint,
    ),
    JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL,
        language="javascript",
        rule_id="javascript.dom_global_runtime_guard",
        planner=_plan_javascript_dom_global_runtime_guard,
        runner=_run_javascript_dom_global_runtime_guard,
    ),
    JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL,
        language="javascript",
        rule_id="javascript.missing_method_runtime",
        planner=_plan_javascript_missing_method_runtime,
        runner=_run_javascript_missing_method_runtime,
    ),
    PYTHON_UNITTEST_MISSING_TARGET_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=PYTHON_UNITTEST_MISSING_TARGET_SOURCE_TOOL,
        language="python",
        rule_id="python.unittest_missing_target",
        planner=_plan_python_unittest_missing_target,
        runner=_run_python_unittest_missing_target,
    ),
    PYTHON_UNITTEST_RUNTIME_FAILURE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=PYTHON_UNITTEST_RUNTIME_FAILURE_SOURCE_TOOL,
        language="python",
        rule_id="python.unittest_runtime_failure",
        planner=_plan_python_unittest_runtime_failure,
        runner=_run_python_unittest_runtime_failure,
    ),
    PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=PYTHON_README_REQUIRED_TOKEN_SOURCE_TOOL,
        language="python",
        rule_id="python.readme_required_token",
        planner=_plan_python_readme_required_token,
        runner=_run_python_readme_required_token,
    ),
    PYTHON_PACKAGE_CHILD_REEXPORT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=PYTHON_PACKAGE_CHILD_REEXPORT_SOURCE_TOOL,
        language="python",
        rule_id="python.package_child_reexport",
        planner=_plan_python_package_child_reexport,
        runner=_run_python_package_child_reexport,
    ),
    PYTHON_PACKAGE_SHADOW_BRIDGE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=PYTHON_PACKAGE_SHADOW_BRIDGE_SOURCE_TOOL,
        language="python",
        rule_id="python.package_shadow_bridge",
        planner=_plan_python_package_shadow_bridge,
        runner=_run_python_package_shadow_bridge,
    ),
    PYTHON_UNRESOLVED_IMPORT_SYMBOL_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=PYTHON_UNRESOLVED_IMPORT_SYMBOL_SOURCE_TOOL,
        language="python",
        rule_id="python.unresolved_import_symbol",
        planner=_plan_python_unresolved_import_symbol,
        runner=_run_python_unresolved_import_symbol,
    ),
    **{
        source_tool: RuntimeRepairBinding(
            source_tool=source_tool,
            language=language,
            rule_id=rule_id,
            planner=_plan_typescript_runtime_source_tool(source_tool),
            runner=_run_typescript_runtime_source_tool(source_tool),
        )
        for source_tool, language, rule_id in _TYPESCRIPT_RUNTIME_MIGRATION_BINDINGS
    },
    TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL,
        language="typescript",
        rule_id="typescript.canvas_scale_return_type",
        planner=_plan_typescript_canvas_scale_return_type,
        runner=_run_typescript_canvas_scale_return_type,
    ),
    TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
        language="typescript",
        rule_id="typescript.object_literal_missing_comma",
        planner=_plan_typescript_object_literal_comma,
        runner=_run_typescript_object_literal_comma,
    ),
    TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL,
        language="typescript",
        rule_id="typescript.nullable_canvas_context",
        planner=_plan_typescript_nullable_canvas_context,
        runner=_run_typescript_nullable_canvas_context,
    ),
    TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL,
        language="typescript",
        rule_id="typescript.duplicate_object_property",
        planner=_plan_typescript_duplicate_object_property,
        runner=_run_typescript_duplicate_object_property,
    ),
    TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL,
        language="typescript",
        rule_id="typescript.enum_member_separator",
        planner=_plan_typescript_enum_member_separator,
        runner=_run_typescript_enum_member_separator,
    ),
    TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL,
        language="typescript",
        rule_id="typescript.missing_closing_brace",
        planner=_plan_typescript_missing_closing_brace,
        runner=_run_typescript_missing_closing_brace,
    ),
    TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL,
        language="typescript",
        rule_id="typescript.number_to_string_argument",
        planner=_plan_typescript_number_to_string_argument,
        runner=_run_typescript_number_to_string_argument,
    ),
    TYPESCRIPT_READONLY_ASSIGNMENT_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=TYPESCRIPT_READONLY_ASSIGNMENT_SOURCE_TOOL,
        language="typescript",
        rule_id="typescript.readonly_assignment",
        planner=_plan_typescript_readonly_assignment,
        runner=_run_typescript_readonly_assignment,
    ),
}


__all__ = [
    "RuntimeRepairBinding",
    "RuntimeRepairPlanning",
    "RuntimeRepairRun",
    "build_runtime_repair_convergence_planner",
    "plan_runtime_repair",
    "run_runtime_repair",
    "run_runtime_repair_convergence",
    "runtime_repair_bindings",
    "runtime_repair_source_tools",
]
