"""Python/Go/Rust/C++/Java/generic run-handler wrappers for runtime_dispatch."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from ..contracts import (
    RepairAdvisorNote,
    RepairDiagnostic,
)
from ..cpp_runtime import (
    run_cpp_include_path_repair,
    run_cpp_missing_private_members_repair,
    run_cpp_placeholder_declaration_repair,
    run_cpp_post_repair,
    run_cpp_standard_include_repair,
    run_cpp_struct_getter_field_access_repair,
)
from ..executor import DeleteFileFn, EditFileFn, TransactionalRepairExecutor, WriteFileFn
from ..generic_hygiene_runtime import (
    run_generic_hygiene_repair,
    run_patch_residue_cleanup_repair,
)
from ..go_runtime import (
    run_go_bare_import_string_repair,
    run_go_bare_local_import_repair,
    run_go_dedup_repair,
    run_go_error_string_helper_repair,
    run_go_missing_stdlib_import_repair,
    run_go_module_import_repair,
    run_go_nested_import_repair,
    run_go_printf_stringer_repair,
    run_go_subpath_import_repair,
    run_go_test_assertion_align_repair,
    run_go_undefined_selector_repair,
    run_go_unused_import_repair,
)
from ..java_runtime import (
    run_java_accessor_alias_repair,
    run_java_post_repair,
)
from ..policy_gate import RepairPolicyContext, RepairPolicyGate
from ..python_runtime import (
    run_python_missing_module_alias_repair,
    run_python_package_child_reexport_repair,
    run_python_package_shadow_bridge_repair,
    run_python_readme_required_token_repair,
    run_python_unittest_missing_target_repair,
    run_python_unittest_runtime_failure_repair,
    run_python_unresolved_import_symbol_repair,
)
from ..rust_runtime import (
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
from ._adapters import (
    _runtime_run_from_cpp,
    _runtime_run_from_generic_hygiene,
    _runtime_run_from_go,
    _runtime_run_from_java,
    _runtime_run_from_patch_residue_cleanup,
    _runtime_run_from_python,
    _runtime_run_from_rust,
)
from ._handlers_plan import (
    _plan_java_test_dependency,
    _plan_rust_post,
)
from ._paths import _normalize_runtime_base_files, _normalize_runtime_repair_path
from ._types import (
    RuntimeRepairRun,
    RuntimeRunnerFn,
    RuntimeTypedRunnerFn,
)


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


def _run_python_missing_module_alias(
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
    run = run_python_missing_module_alias_repair(
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


def _run_go_error_string_helper_typed(
    workspace: str | Path,
    base_files: Mapping[str, str],
    repair_diagnostics: Sequence[RepairDiagnostic],
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
        repair_diagnostics=repair_diagnostics,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_go(run)


def _run_go_missing_stdlib_import(
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
    run = run_go_missing_stdlib_import_repair(
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


def _run_go_missing_stdlib_import_typed(
    workspace: str | Path,
    base_files: Mapping[str, str],
    repair_diagnostics: Sequence[RepairDiagnostic],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_go_missing_stdlib_import_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_go(run)


def _run_go_printf_stringer(
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
    run = run_go_printf_stringer_repair(
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


def _run_go_printf_stringer_typed(
    workspace: str | Path,
    base_files: Mapping[str, str],
    repair_diagnostics: Sequence[RepairDiagnostic],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_go_printf_stringer_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_go(run)


def _run_go_test_assertion_align(
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
    run = run_go_test_assertion_align_repair(
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


def _run_go_test_assertion_align_typed(
    workspace: str | Path,
    base_files: Mapping[str, str],
    repair_diagnostics: Sequence[RepairDiagnostic],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_go_test_assertion_align_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_go(run)


def _run_go_undefined_selector(
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
    run = run_go_undefined_selector_repair(
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


def _run_go_undefined_selector_typed(
    workspace: str | Path,
    base_files: Mapping[str, str],
    repair_diagnostics: Sequence[RepairDiagnostic],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_go_undefined_selector_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
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


def _run_generic_hygiene_typed(source_tool: str) -> RuntimeTypedRunnerFn:
    def runner(
        workspace: str | Path,
        base_files: Mapping[str, str],
        repair_diagnostics: Sequence[RepairDiagnostic],
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
            repair_diagnostics=repair_diagnostics,
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
