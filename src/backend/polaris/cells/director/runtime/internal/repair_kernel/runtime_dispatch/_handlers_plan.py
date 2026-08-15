"""Plan-handler wrappers for runtime_dispatch bindings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from ..composer import PatchComposer
from ..contracts import (
    RepairAdvisorNote,
    RepairDiagnostic,
)
from ..cpp_runtime import (
    plan_cpp_include_path_repair,
    plan_cpp_missing_private_members_repair,
    plan_cpp_placeholder_declaration_repair,
    plan_cpp_post_repair,
    plan_cpp_standard_include_repair,
    plan_cpp_struct_getter_field_access_repair,
)
from ..diagnostics import normalize_artifact_quality_errors
from ..generic_hygiene_runtime import (
    plan_generic_hygiene_repair,
    plan_patch_residue_cleanup_repair,
)
from ..go_runtime import (
    plan_go_bare_import_string_repair,
    plan_go_bare_local_import_repair,
    plan_go_dedup_repair,
    plan_go_error_string_helper_repair,
    plan_go_missing_stdlib_import_repair,
    plan_go_module_import_repair,
    plan_go_nested_import_repair,
    plan_go_printf_stringer_repair,
    plan_go_subpath_import_repair,
    plan_go_test_assertion_align_repair,
    plan_go_undefined_selector_repair,
    plan_go_unused_import_repair,
)
from ..java_runtime import (
    plan_java_accessor_alias_repair,
    plan_java_post_repair,
)
from ..java_syntax import (
    JAVA_TEST_DEPENDENCY_SOURCE_TOOL,
    build_java_test_dependency_plan,
)
from ..javascript_runtime import (
    plan_javascript_dom_global_runtime_guard_repair,
    plan_javascript_esm_commonjs_entrypoint_repair,
    plan_javascript_missing_export_repair,
    plan_javascript_missing_method_runtime_repair,
    plan_javascript_test_missing_target_repair,
    plan_node_test_script_contract_repair,
    plan_npm_script_contract_repair,
    plan_typescript_local_js_import_repair,
)
from ..python_runtime import (
    plan_python_missing_module_alias_repair,
    plan_python_package_child_reexport_repair,
    plan_python_package_shadow_bridge_repair,
    plan_python_readme_required_token_repair,
    plan_python_unittest_missing_target_repair,
    plan_python_unittest_runtime_failure_repair,
    plan_python_unresolved_import_symbol_repair,
)
from ..rust_runtime import (
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
from ..rust_syntax import (
    RUST_POST_SOURCE_TOOL,
)
from ..typescript_runtime import (
    plan_typescript_canvas_scale_return_type_repair,
    plan_typescript_duplicate_object_property_repair,
    plan_typescript_enum_member_separator_repair,
    plan_typescript_missing_closing_brace_repair,
    plan_typescript_nullable_canvas_context_repair,
    plan_typescript_number_to_string_argument_repair,
    plan_typescript_object_literal_comma_repair,
    plan_typescript_readonly_assignment_repair,
)
from ._adapters import (
    _runtime_planning_from_cpp,
    _runtime_planning_from_generic_hygiene,
    _runtime_planning_from_go,
    _runtime_planning_from_java,
    _runtime_planning_from_javascript,
    _runtime_planning_from_patch_residue_cleanup,
    _runtime_planning_from_python,
    _runtime_planning_from_rust,
    _runtime_planning_from_typescript,
)
from ._paths import _normalize_runtime_base_files
from ._types import (
    RuntimePlannerFn,
    RuntimeRepairPlanning,
    RuntimeTypedPlannerFn,
)


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


def _plan_typescript_local_js_import(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_typescript_local_js_import_repair(
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


def _plan_javascript_dom_global_runtime_guard_typed(
    base_files: Mapping[str, str],
    repair_diagnostics: Sequence[RepairDiagnostic],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_javascript_dom_global_runtime_guard_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
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


def _plan_python_missing_module_alias(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_python_missing_module_alias_repair(
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


def _plan_go_error_string_helper_typed(
    base_files: Mapping[str, str],
    repair_diagnostics: Sequence[RepairDiagnostic],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_error_string_helper_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_missing_stdlib_import(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_missing_stdlib_import_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_missing_stdlib_import_typed(
    base_files: Mapping[str, str],
    repair_diagnostics: Sequence[RepairDiagnostic],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_missing_stdlib_import_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_printf_stringer(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_printf_stringer_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_printf_stringer_typed(
    base_files: Mapping[str, str],
    repair_diagnostics: Sequence[RepairDiagnostic],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_printf_stringer_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_test_assertion_align(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_test_assertion_align_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_test_assertion_align_typed(
    base_files: Mapping[str, str],
    repair_diagnostics: Sequence[RepairDiagnostic],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_test_assertion_align_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_undefined_selector(
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_undefined_selector_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_planning_from_go(planning)


def _plan_go_undefined_selector_typed(
    base_files: Mapping[str, str],
    repair_diagnostics: Sequence[RepairDiagnostic],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairPlanning:
    planning = plan_go_undefined_selector_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
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


def _plan_generic_hygiene_typed(source_tool: str) -> RuntimeTypedPlannerFn:
    def planner(
        base_files: Mapping[str, str],
        repair_diagnostics: Sequence[RepairDiagnostic],
        artifact_quality_errors: Sequence[str],
        advisor_notes: Sequence[RepairAdvisorNote] | None,
        mode: str,
    ) -> RuntimeRepairPlanning:
        planning = plan_generic_hygiene_repair(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=artifact_quality_errors,
            repair_diagnostics=repair_diagnostics,
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
