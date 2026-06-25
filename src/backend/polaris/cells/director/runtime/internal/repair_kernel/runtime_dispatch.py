"""Generic runtime dispatcher for deterministic Director repair rules."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .contracts import (
    CompositionResult,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairExecutionResult,
    RepairPlan,
)
from .cpp_runtime import (
    CppIncludePathPlanning,
    CppIncludePathRun,
    CppMissingPrivateMembersPlanning,
    CppMissingPrivateMembersRun,
    CppPlaceholderDeclarationPlanning,
    CppPlaceholderDeclarationRun,
    CppStandardIncludePlanning,
    CppStandardIncludeRun,
    CppStructGetterFieldAccessPlanning,
    CppStructGetterFieldAccessRun,
    plan_cpp_include_path_repair,
    plan_cpp_missing_private_members_repair,
    plan_cpp_placeholder_declaration_repair,
    plan_cpp_standard_include_repair,
    plan_cpp_struct_getter_field_access_repair,
    run_cpp_include_path_repair,
    run_cpp_missing_private_members_repair,
    run_cpp_placeholder_declaration_repair,
    run_cpp_standard_include_repair,
    run_cpp_struct_getter_field_access_repair,
)
from .cpp_syntax import (
    CPP_INCLUDE_PATH_SOURCE_TOOL,
    CPP_MISSING_PRIVATE_MEMBERS_SOURCE_TOOL,
    CPP_PLACEHOLDER_DECLARATION_SOURCE_TOOL,
    CPP_STANDARD_INCLUDE_SOURCE_TOOL,
    CPP_STRUCT_GETTER_FIELD_ACCESS_SOURCE_TOOL,
)
from .diagnostics import normalize_artifact_quality_errors
from .executor import EditFileFn, WriteFileFn
from .generic_hygiene_runtime import (
    PatchResidueCleanupPlanning,
    PatchResidueCleanupRun,
    plan_patch_residue_cleanup_repair,
    run_patch_residue_cleanup_repair,
)
from .generic_hygiene_syntax import PATCH_RESIDUE_CLEANUP_SOURCE_TOOL
from .go_runtime import (
    GoBareImportStringPlanning,
    GoBareImportStringRun,
    plan_go_bare_import_string_repair,
    run_go_bare_import_string_repair,
)
from .go_syntax import GO_BARE_IMPORT_STRING_SOURCE_TOOL
from .java_runtime import (
    JavaAccessorAliasPlanning,
    JavaAccessorAliasRun,
    plan_java_accessor_alias_repair,
    run_java_accessor_alias_repair,
)
from .java_syntax import JAVA_ACCESSOR_ALIAS_SOURCE_TOOL
from .policy_gate import PolicyDecision
from .rust_runtime import (
    RustDependencyPlanning,
    RustDependencyRun,
    plan_rust_dependency_repair,
    run_rust_dependency_repair,
)
from .rust_syntax import RUST_DEPENDENCY_SOURCE_TOOL
from .typescript_runtime import (
    TypeScriptDuplicateObjectPropertyPlanning,
    TypeScriptDuplicateObjectPropertyRun,
    TypeScriptEnumMemberSeparatorPlanning,
    TypeScriptEnumMemberSeparatorRun,
    TypeScriptNullableCanvasContextPlanning,
    TypeScriptNullableCanvasContextRun,
    TypeScriptObjectLiteralCommaPlanning,
    TypeScriptObjectLiteralCommaRun,
    plan_typescript_duplicate_object_property_repair,
    plan_typescript_enum_member_separator_repair,
    plan_typescript_nullable_canvas_context_repair,
    plan_typescript_object_literal_comma_repair,
    run_typescript_duplicate_object_property_repair,
    run_typescript_enum_member_separator_repair,
    run_typescript_nullable_canvas_context_repair,
    run_typescript_object_literal_comma_repair,
)
from .typescript_syntax import (
    TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL,
    TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL,
    TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL,
    TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
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
            workspace, base_files, artifact_quality_errors, writer, editor, allowed_paths, notes, mode
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


def _normalize_source_tool(source_tool: str) -> str:
    return str(source_tool or "").strip()


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


def _run_typescript_object_literal_comma(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
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


def _run_go_bare_import_string(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
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


def _run_rust_dependency(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
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


def _run_patch_residue_cleanup(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
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


def _run_cpp_include_path(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
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


def _run_java_accessor_alias(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
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


def _runtime_planning_from_cpp(
    planning: CppIncludePathPlanning
    | CppMissingPrivateMembersPlanning
    | CppPlaceholderDeclarationPlanning
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


def _runtime_planning_from_rust(planning: RustDependencyPlanning) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_rust(run: RustDependencyRun) -> RuntimeRepairRun:
    return RuntimeRepairRun(
        planning=_runtime_planning_from_rust(run.planning),
        ok=run.ok,
        execution_result=run.execution_result,
        plan_decision=run.plan_decision,
        composition_decision=run.composition_decision,
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _runtime_planning_from_patch_residue_cleanup(planning: PatchResidueCleanupPlanning) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_patch_residue_cleanup(run: PatchResidueCleanupRun) -> RuntimeRepairRun:
    return RuntimeRepairRun(
        planning=_runtime_planning_from_patch_residue_cleanup(run.planning),
        ok=run.ok,
        execution_result=run.execution_result,
        plan_decision=run.plan_decision,
        composition_decision=run.composition_decision,
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _runtime_planning_from_java(planning: JavaAccessorAliasPlanning) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_java(run: JavaAccessorAliasRun) -> RuntimeRepairRun:
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
    planning: TypeScriptDuplicateObjectPropertyPlanning
    | TypeScriptEnumMemberSeparatorPlanning
    | TypeScriptNullableCanvasContextPlanning
    | TypeScriptObjectLiteralCommaPlanning,
) -> RuntimeRepairPlanning:
    return RuntimeRepairPlanning(
        source_tool=planning.source_tool,
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _runtime_run_from_typescript(
    run: TypeScriptDuplicateObjectPropertyRun
    | TypeScriptEnumMemberSeparatorRun
    | TypeScriptNullableCanvasContextRun
    | TypeScriptObjectLiteralCommaRun,
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
    RUST_DEPENDENCY_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=RUST_DEPENDENCY_SOURCE_TOOL,
        language="rust",
        rule_id="rust.unlinked_crate_dependency",
        planner=_plan_rust_dependency,
        runner=_run_rust_dependency,
    ),
    PATCH_RESIDUE_CLEANUP_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=PATCH_RESIDUE_CLEANUP_SOURCE_TOOL,
        language="generic",
        rule_id="generic.patch_residue_cleanup",
        planner=_plan_patch_residue_cleanup,
        runner=_run_patch_residue_cleanup,
    ),
    JAVA_ACCESSOR_ALIAS_SOURCE_TOOL: RuntimeRepairBinding(
        source_tool=JAVA_ACCESSOR_ALIAS_SOURCE_TOOL,
        language="java",
        rule_id="java.common_accessor_aliases",
        planner=_plan_java_accessor_alias,
        runner=_run_java_accessor_alias,
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
}


__all__ = [
    "RuntimeRepairBinding",
    "RuntimeRepairPlanning",
    "RuntimeRepairRun",
    "plan_runtime_repair",
    "run_runtime_repair",
    "runtime_repair_bindings",
    "runtime_repair_source_tools",
]
