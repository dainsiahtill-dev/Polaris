"""Machine-readable catalog for Director deterministic repair strategies.

The repair implementation is intentionally hard-coded and code-enforced. This
catalog gives those hard-coded strategies a stable audit contract so new repair
rules cannot silently appear as untracked ``source_tool`` strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS: frozenset[str] = frozenset(
    {
        "deterministic_cpp_include_path_repair",
        "deterministic_cpp_missing_private_members_repair",
        "deterministic_cpp_placeholder_declaration_repair",
        "deterministic_cpp_post_repair",
        "deterministic_cpp_standard_include_repair",
        "deterministic_cpp_struct_getter_field_access_repair",
        "deterministic_declared_target_contract_repair",
        "deterministic_go_bare_import_repair",
        "deterministic_go_bare_import_string_repair",
        "deterministic_go_dedup_repair",
        "deterministic_go_error_string_helper_repair",
        "deterministic_go_module_import_repair",
        "deterministic_go_nested_import_repair",
        "deterministic_go_subpath_repair",
        "deterministic_go_unused_import_repair",
        "deterministic_html_typescript_module_script_repair",
        "deterministic_java_accessor_alias_repair",
        "deterministic_java_post_repair",
        "deterministic_java_test_dependency_repair",
        "deterministic_javascript_dom_global_runtime_guard_repair",
        "deterministic_javascript_esm_commonjs_entrypoint_repair",
        "deterministic_javascript_missing_export_repair",
        "deterministic_javascript_missing_method_runtime_repair",
        "deterministic_javascript_test_missing_target_repair",
        "deterministic_javascript_typescript_annotation_repair",
        "deterministic_missing_declared_target_repair",
        "deterministic_node_test_script_contract_repair",
        "deterministic_npm_script_contract_repair",
        "deterministic_patch_residue_cleanup",
        "deterministic_pre_materialization_declared_target_repair",
        "deterministic_python_package_child_reexport_repair",
        "deterministic_python_missing_module_alias_repair",
        "deterministic_python_package_shadow_bridge_repair",
        "deterministic_python_readme_required_token_repair",
        "deterministic_python_unittest_missing_target_repair",
        "deterministic_python_unittest_runtime_failure_repair",
        "deterministic_quality_repair",
        "deterministic_runtime_dependency_repair",
        "deterministic_rust_crate_import_repair",
        "deterministic_rust_crate_import_rewrite_repair",
        "deterministic_rust_dependency_repair",
        "deterministic_rust_derive_repair",
        "deterministic_rust_duplicate_module_file_repair",
        "deterministic_rust_field_rename_suggestion_repair",
        "deterministic_rust_incompatible_copy_derive_repair",
        "deterministic_rust_lib_root_facade_repair",
        "deterministic_rust_line_suggestion_repair",
        "deterministic_rust_method_self_signature_repair",
        "deterministic_rust_missing_binary_entrypoint_repair",
        "deterministic_rust_missing_fields_repair",
        "deterministic_rust_missing_lib_target_repair",
        "deterministic_rust_missing_module_file_repair",
        "deterministic_rust_serde_derive_repair",
        "deterministic_rust_struct_literal_missing_field_repair",
        "deterministic_rust_trait_import_repair",
        "deterministic_rust_unresolved_pub_use_repair",
        "deterministic_rust_unused_import_repair",
        "deterministic_rust_wrong_crate_path_repair",
        "deterministic_scaffold_marker_cleanup",
        "deterministic_scaffold_marker_quality_cleanup",
        "deterministic_scaffold_residue_cleanup",
        "deterministic_typeorm_model_normalization_repair",
        "deterministic_typescript_branded_literal_cast_repair",
        "deterministic_typescript_canvas_scale_return_type_repair",
        "deterministic_typescript_commonjs_package_type_repair",
        "deterministic_typescript_strict_null_relaxation_repair",
        "deterministic_typescript_config_key_split_repair",
        "deterministic_typescript_dom_local_shim_cleanup_repair",
        "deterministic_typescript_duplicate_object_property_repair",
        "deterministic_typescript_entrypoint_repair",
        "deterministic_typescript_enum_member_separator_repair",
        "deterministic_typescript_escaped_newline_repair",
        "deterministic_typescript_expect_error_placement_repair",
        "deterministic_typescript_export_ambiguity_repair",
        "deterministic_typescript_hyphenated_identifier_repair",
        "deterministic_typescript_html_container_selector_repair",
        "deterministic_typescript_identifier_suggestion_repair",
        "deterministic_typescript_implicit_return_type_repair",
        "deterministic_typescript_import_specifier_keyword_repair",
        "deterministic_typescript_literal_union_value_facade_repair",
        "deterministic_typescript_local_js_import_repair",
        "deterministic_typescript_member_alias_repair",
        "deterministic_typescript_missing_closing_brace_repair",
        "deterministic_typescript_missing_export_repair",
        "deterministic_typescript_missing_member_repair",
        "deterministic_typescript_missing_relative_module_repair",
        "deterministic_typescript_invalid_module_augmentation_repair",
        "deterministic_typescript_nullable_canvas_context_repair",
        "deterministic_typescript_number_property_call_repair",
        "deterministic_typescript_number_to_string_argument_repair",
        "deterministic_typescript_object_assign_assertion_repair",
        "deterministic_typescript_object_literal_missing_props_repair",
        "deterministic_typescript_param_object_property_repair",
        "deterministic_typescript_private_constructor_access_repair",
        "deterministic_typescript_private_property_access_repair",
        "deterministic_typescript_readonly_array_mutation_repair",
        "deterministic_typescript_readonly_assignment_repair",
        "deterministic_typescript_duplicate_function_repair",
        "deterministic_typescript_json_as_source_repair",
        "deterministic_typescript_literal_union_expand_repair",
        "deterministic_typescript_init_property_alias_repair",
        "deterministic_typescript_arg_type_function_alias_repair",
        "deterministic_typescript_argument_shape_adapter_repair",
        "deterministic_typescript_reexport_repair",
        "deterministic_typescript_reexported_type_binding_repair",
        "deterministic_typescript_relative_import_case_repair",
        "deterministic_typescript_return_object_semicolon_repair",
        "deterministic_typescript_scaffold_repair",
        "deterministic_typescript_shorthand_property_scope_repair",
        "deterministic_typescript_sourcefile_diagnostics_repair",
        "deterministic_typescript_string_literal_suggestion_repair",
        "deterministic_typescript_test_block_residue_repair",
        "deterministic_typescript_too_few_arguments_repair",
        "deterministic_typescript_truncated_eof_repair",
        "deterministic_typescript_type_inference_required_repair",
        "deterministic_typescript_tsconfig_lib_repair",
        "deterministic_typescript_tsconfig_rootdir_repair",
        "deterministic_typescript_unknown_member_access_repair",
        "deterministic_typescript_uninitialized_property_repair",
        "deterministic_typescript_unique_export_import_repair",
        "deterministic_typescript_unresolved_identifier_repair",
        "deterministic_typescript_unused_import_repair",
        "deterministic_typescript_unused_local_repair",
        "deterministic_typescript_value_used_as_type_repair",
        "deterministic_typescript_vitest_globals_repair",
        "deterministic_typescript_zod_type_class_collision_repair",
        "deterministic_unresolved_import_symbol_repair",
    }
)


@dataclass(frozen=True)
class DeterministicRepairStrategy:
    """Audit profile for one hard-coded Director repair strategy."""

    source_tool: str
    language: str
    phase: str
    concern: str
    risk_level: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable representation."""

        return {
            "source_tool": self.source_tool,
            "language": self.language,
            "phase": self.phase,
            "concern": self.concern,
            "risk_level": self.risk_level,
        }


def deterministic_repair_source_tool_known(source_tool: str) -> bool:
    """Return whether a repair ``source_tool`` is registered."""

    return str(source_tool or "").strip() in KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS


def describe_deterministic_repair_strategy(source_tool: str) -> DeterministicRepairStrategy:
    """Build the audit profile for a registered deterministic repair tool."""

    token = str(source_tool or "").strip()
    if token not in KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS:
        return DeterministicRepairStrategy(
            source_tool=token,
            language="unknown",
            phase="unknown",
            concern="unregistered",
            risk_level="high",
        )
    return DeterministicRepairStrategy(
        source_tool=token,
        language=_infer_language(token),
        phase=_infer_phase(token),
        concern=_infer_concern(token),
        risk_level=_infer_risk_level(token),
    )


def deterministic_repair_strategy_catalog() -> list[dict[str, str]]:
    """Return all registered hard-coded repair strategies in stable order."""

    return [
        describe_deterministic_repair_strategy(source_tool).to_dict()
        for source_tool in sorted(KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS)
    ]


def summarize_deterministic_repair_source_tools(source_tools: list[str]) -> list[dict[str, Any]]:
    """Return compact, deduplicated strategy profiles for repair summaries."""

    seen: set[str] = set()
    profiles: list[dict[str, Any]] = []
    for raw_tool in source_tools:
        source_tool = str(raw_tool or "").strip()
        if not source_tool or source_tool in seen:
            continue
        seen.add(source_tool)
        profile: dict[str, Any] = describe_deterministic_repair_strategy(source_tool).to_dict()
        profile["registered"] = deterministic_repair_source_tool_known(source_tool)
        profiles.append(profile)
    return profiles


def _infer_language(source_tool: str) -> str:
    if "typescript" in source_tool or "zod" in source_tool or "typeorm" in source_tool:
        return "typescript"
    if "javascript" in source_tool or "node_" in source_tool or "npm_" in source_tool:
        return "javascript"
    if "_python_" in source_tool or source_tool.endswith("_import_symbol_repair"):
        return "python"
    if "_rust_" in source_tool:
        return "rust"
    if "_go_" in source_tool:
        return "go"
    if "_cpp_" in source_tool:
        return "cpp"
    if "_java_" in source_tool:
        return "java"
    if "html_" in source_tool:
        return "html"
    if "runtime_dependency" in source_tool:
        return "dependency"
    return "generic"


def _infer_phase(source_tool: str) -> str:
    if "pre_materialization" in source_tool:
        return "pre_materialization"
    if (
        "include_path" in source_tool
        or "missing_private_members" in source_tool
        or "placeholder_declaration" in source_tool
        or "standard_include" in source_tool
        or "struct_getter" in source_tool
    ):
        return "post_materialization"
    if "post_repair" in source_tool:
        return "post_materialization"
    if "cleanup" in source_tool or "residue" in source_tool:
        return "cleanup"
    if "declared_target" in source_tool:
        return "target_contract"
    if "dependency" in source_tool:
        return "dependency_resolution"
    if "_rust_" in source_tool:
        if any(
            term in source_tool
            for term in (
                "duplicate_module_file",
                "lib_root_facade",
                "missing_binary_entrypoint",
                "missing_lib_target",
                "missing_module_file",
            )
        ):
            return "structural_repair"
        if "crate_import" in source_tool or "wrong_crate_path" in source_tool:
            return "dependency_resolution"
        if any(
            term in source_tool
            for term in (
                "derive",
                "field_rename_suggestion",
                "line_suggestion",
                "method_self_signature",
                "missing_fields",
                "struct_literal_missing_field",
                "unused_import",
            )
        ):
            return "code_repair"
    if "test" in source_tool or "unittest" in source_tool or "vitest" in source_tool:
        return "test_contract"
    return "quality_repair"


def _infer_concern(source_tool: str) -> str:
    if "missing" in source_tool:
        return "missing_symbol_or_file"
    if "include" in source_tool:
        return "dependency_manifest" if "standard_include" in source_tool else "module_boundary"
    if "placeholder" in source_tool:
        return "syntax_normalization"
    if "struct_getter" in source_tool:
        return "missing_symbol_or_file"
    if "private_members" in source_tool:
        return "missing_symbol_or_file"
    if "import" in source_tool or "export" in source_tool or "reexport" in source_tool:
        return "module_boundary"
    if "dependency" in source_tool:
        return "dependency_manifest"
    if "script" in source_tool or "entrypoint" in source_tool:
        return "entrypoint_or_script"
    if "syntax" in source_tool or "closing_brace" in source_tool or "semicolon" in source_tool:
        return "syntax_normalization"
    if "cleanup" in source_tool or "residue" in source_tool or "scaffold" in source_tool:
        return "generated_residue"
    if "post_repair" in source_tool:
        return "language_postpass"
    return "quality_gate"


def _infer_risk_level(source_tool: str) -> str:
    if "dependency" in source_tool or "package_type" in source_tool or "tsconfig" in source_tool:
        return "medium"
    if "cleanup" in source_tool or "residue" in source_tool:
        return "low"
    if "post_repair" in source_tool:
        return "medium"
    return "low"


__all__ = [
    "KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS",
    "DeterministicRepairStrategy",
    "describe_deterministic_repair_strategy",
    "deterministic_repair_source_tool_known",
    "deterministic_repair_strategy_catalog",
    "summarize_deterministic_repair_source_tools",
]
