from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from ..contracts import RepairAdvisorNote
from ..executor import DeleteFileFn, EditFileFn, TransactionalRepairExecutor, WriteFileFn
from ..policy_gate import RepairPolicyContext, RepairPolicyGate
from ._helpers import _normalize_base_files, _normalize_repair_path
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
from ._types import (
    RustCrateImportRewriteRun,
    RustCrateImportRun,
    RustDependencyRun,
    RustDuplicateModuleFileRun,
    RustFieldRenameSuggestionRun,
    RustIncompatibleCopyDeriveRun,
    RustLibRootFacadeRun,
    RustLineSuggestionRun,
    RustMethodSelfSignatureRun,
    RustMissingBinaryEntrypointRun,
    RustMissingFieldsRun,
    RustMissingLibTargetRun,
    RustMissingModuleFileRun,
    RustMissingTraitDeriveRun,
    RustSerdeDeriveRun,
    RustStructLiteralMissingFieldRun,
    RustTraitImportRun,
    RustUnresolvedPubUseRun,
    RustUnusedImportRun,
    RustWrongCratePathRun,
)


def run_rust_dependency_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustDependencyRun:
    """Run Rust dependency repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_dependency_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustDependencyRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust dependency repair plan.",
        )
    if planning.composition is None:
        return RustDependencyRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust dependency repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustDependencyRun(
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
    )
    return RustDependencyRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_missing_binary_entrypoint_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustMissingBinaryEntrypointRun:
    """Run Rust missing binary entrypoint repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_missing_binary_entrypoint_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustMissingBinaryEntrypointRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust missing binary entrypoint repair plan.",
        )
    if planning.composition is None:
        return RustMissingBinaryEntrypointRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust missing binary entrypoint repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    default_allowed_paths = tuple(normalized_base.keys()) + tuple(
        operation.path for operation in planning.plan.operations
    )
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            _normalize_repair_path(str(path or ""))
            for path in (allowed_paths if allowed_paths is not None else default_allowed_paths)
        )
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustMissingBinaryEntrypointRun(
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
    )
    return RustMissingBinaryEntrypointRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_missing_lib_target_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustMissingLibTargetRun:
    """Run the safe Rust missing lib target subset through Plan->Compose->Policy->Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_missing_lib_target_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustMissingLibTargetRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No safe Rust missing lib target repair plan.",
        )
    if planning.composition is None:
        return RustMissingLibTargetRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust missing lib target repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    default_allowed_paths = tuple(normalized_base.keys()) + tuple(
        operation.path for operation in planning.plan.operations
    )
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            _normalize_repair_path(str(path or ""))
            for path in (allowed_paths if allowed_paths is not None else default_allowed_paths)
        )
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustMissingLibTargetRun(
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
    )
    return RustMissingLibTargetRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_lib_root_facade_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustLibRootFacadeRun:
    """Run the Rust lib-root facade repair through editor-only text_replace."""

    del writer
    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_lib_root_facade_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustLibRootFacadeRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No safe Rust lib-root facade repair plan.",
        )
    if planning.composition is None:
        return RustLibRootFacadeRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust lib-root facade repair composition was not produced.",
        )
    if editor is None:
        return RustLibRootFacadeRun(
            planning=planning,
            ok=False,
            error_code="repair_editor_required",
            error_message="Rust lib-root facade repair requires span-based edit_file execution.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            _normalize_repair_path(str(path or ""))
            for path in (allowed_paths if allowed_paths is not None else normalized_base.keys())
        )
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustLibRootFacadeRun(
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
        writer=None,
        editor=editor,
    )
    return RustLibRootFacadeRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_duplicate_module_file_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    deleter: DeleteFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustDuplicateModuleFileRun:
    """Run Rust E0761 duplicate module file repair through Plan->Compose->Policy->Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_duplicate_module_file_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustDuplicateModuleFileRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust duplicate module file repair plan.",
        )
    if planning.composition is None:
        return RustDuplicateModuleFileRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust duplicate module file repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    default_allowed_paths = tuple(normalized_base.keys()) + tuple(
        operation.path for operation in planning.plan.operations
    )
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            _normalize_repair_path(str(path or ""))
            for path in (allowed_paths if allowed_paths is not None else default_allowed_paths)
        )
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustDuplicateModuleFileRun(
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
    return RustDuplicateModuleFileRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_missing_module_file_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustMissingModuleFileRun:
    """Run Rust missing module file repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_missing_module_file_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustMissingModuleFileRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust missing module file repair plan.",
        )
    if planning.composition is None:
        return RustMissingModuleFileRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust missing module file repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    default_allowed_paths = tuple(normalized_base.keys()) + tuple(
        operation.path for operation in planning.plan.operations
    )
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            _normalize_repair_path(str(path or ""))
            for path in (allowed_paths if allowed_paths is not None else default_allowed_paths)
        )
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustMissingModuleFileRun(
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
    )
    return RustMissingModuleFileRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_struct_literal_missing_field_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustStructLiteralMissingFieldRun:
    """Run generated Rust struct literal missing-field repair through Plan->Compose->Policy->Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_struct_literal_missing_field_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustStructLiteralMissingFieldRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No safe generated Rust struct literal missing-field repair plan.",
        )
    if planning.composition is None:
        return RustStructLiteralMissingFieldRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust struct literal missing-field repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustStructLiteralMissingFieldRun(
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
    )
    return RustStructLiteralMissingFieldRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_missing_fields_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustMissingFieldsRun:
    """Run explicit-type Rust missing-field repair through Plan->Compose->Policy->Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_missing_fields_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustMissingFieldsRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No safe explicit-type Rust missing-field repair plan.",
        )
    if planning.composition is None:
        return RustMissingFieldsRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust missing-field repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustMissingFieldsRun(
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
    )
    return RustMissingFieldsRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_crate_import_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustCrateImportRun:
    """Run the Rust crate import repair through Plan->Compose->Policy->Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_crate_import_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustCrateImportRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust crate import repair plan.",
        )
    if planning.composition is None:
        return RustCrateImportRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust crate import repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustCrateImportRun(
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
    )
    return RustCrateImportRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_crate_import_rewrite_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustCrateImportRewriteRun:
    """Run Rust local crate import rewrite repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_crate_import_rewrite_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustCrateImportRewriteRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust local crate import rewrite repair plan.",
        )
    if planning.composition is None:
        return RustCrateImportRewriteRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust local crate import rewrite repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustCrateImportRewriteRun(
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
    )
    return RustCrateImportRewriteRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_incompatible_copy_derive_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustIncompatibleCopyDeriveRun:
    """Run Rust incompatible Copy derive repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_incompatible_copy_derive_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustIncompatibleCopyDeriveRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust incompatible Copy derive repair plan.",
        )
    if planning.composition is None:
        return RustIncompatibleCopyDeriveRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust incompatible Copy derive repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustIncompatibleCopyDeriveRun(
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
    )
    return RustIncompatibleCopyDeriveRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_method_self_signature_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustMethodSelfSignatureRun:
    """Run Rust method self-signature repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_method_self_signature_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustMethodSelfSignatureRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust method self-signature repair plan.",
        )
    if planning.composition is None:
        return RustMethodSelfSignatureRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust method self-signature repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustMethodSelfSignatureRun(
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
    )
    return RustMethodSelfSignatureRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_line_suggestion_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustLineSuggestionRun:
    """Run Rust compiler line-suggestion repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_line_suggestion_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustLineSuggestionRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust line-suggestion repair plan.",
        )
    if planning.composition is None:
        return RustLineSuggestionRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust line-suggestion repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustLineSuggestionRun(
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
    )
    return RustLineSuggestionRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_field_rename_suggestion_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustFieldRenameSuggestionRun:
    """Run Rust field rename suggestion repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_field_rename_suggestion_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustFieldRenameSuggestionRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust field rename suggestion repair plan.",
        )
    if planning.composition is None:
        return RustFieldRenameSuggestionRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust field rename suggestion repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustFieldRenameSuggestionRun(
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
    )
    return RustFieldRenameSuggestionRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_wrong_crate_path_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustWrongCratePathRun:
    """Run Rust wrong crate path repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_wrong_crate_path_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustWrongCratePathRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust wrong crate path repair plan.",
        )
    if planning.composition is None:
        return RustWrongCratePathRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust wrong crate path repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustWrongCratePathRun(
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
    )
    return RustWrongCratePathRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_serde_derive_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustSerdeDeriveRun:
    """Run Rust serde derive repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_serde_derive_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustSerdeDeriveRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust serde derive repair plan.",
        )
    if planning.composition is None:
        return RustSerdeDeriveRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust serde derive repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustSerdeDeriveRun(
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
    )
    return RustSerdeDeriveRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_missing_trait_derive_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustMissingTraitDeriveRun:
    """Run Rust ordinary missing trait derive repair through Plan->Compose->Policy->Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_missing_trait_derive_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustMissingTraitDeriveRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust missing trait derive repair plan.",
        )
    if planning.composition is None:
        return RustMissingTraitDeriveRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust missing trait derive repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustMissingTraitDeriveRun(
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
    )
    return RustMissingTraitDeriveRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_trait_import_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustTraitImportRun:
    """Run Rust trait import repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_trait_import_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustTraitImportRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust trait import repair plan.",
        )
    if planning.composition is None:
        return RustTraitImportRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust trait import repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustTraitImportRun(
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
    )
    return RustTraitImportRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_unused_import_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustUnusedImportRun:
    """Run Rust unused import repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_unused_import_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustUnusedImportRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust unused import repair plan.",
        )
    if planning.composition is None:
        return RustUnusedImportRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust unused import repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustUnusedImportRun(
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
    )
    return RustUnusedImportRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_rust_unresolved_pub_use_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustUnresolvedPubUseRun:
    """Run Rust unresolved public re-export repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_unresolved_pub_use_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustUnresolvedPubUseRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust unresolved public re-export repair plan.",
        )
    if planning.composition is None:
        return RustUnresolvedPubUseRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust unresolved public re-export repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustUnresolvedPubUseRun(
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
    )
    return RustUnresolvedPubUseRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )
