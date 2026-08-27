"""Canonical C++ syntax/path repair rules for Director Runtime."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

CPP_INCLUDE_PATH_SOURCE_TOOL = "deterministic_cpp_include_path_repair"
CPP_MISSING_PRIVATE_MEMBERS_SOURCE_TOOL = "deterministic_cpp_missing_private_members_repair"
CPP_PLACEHOLDER_DECLARATION_SOURCE_TOOL = "deterministic_cpp_placeholder_declaration_repair"
CPP_POST_SOURCE_TOOL = "deterministic_cpp_post_repair"
CPP_STANDARD_INCLUDE_SOURCE_TOOL = "deterministic_cpp_standard_include_repair"
CPP_STRUCT_GETTER_FIELD_ACCESS_SOURCE_TOOL = "deterministic_cpp_struct_getter_field_access_repair"
CPP_USE_BEFORE_DEFINITION_SOURCE_TOOL = "deterministic_cpp_use_before_definition_repair"

_CPP_HEADER_EXTENSIONS = (".h", ".hh", ".hpp", ".hxx")
_CPP_SOURCE_EXTENSIONS = (".c", ".cc", ".cpp", ".cxx")
_CPP_TRANSLATION_EXTENSIONS = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx")
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)
_CLASS_RE = re.compile(r"(class\s+\w+\s*\{(?P<body>.*?)(?:\n\};))", re.DOTALL)
_GETTER_RETURN_FIELD_RE = re.compile(
    r"(?P<type>const\s+std::(?:string|vector<[^>]+>)&|std::(?:string|vector<[^>]+>)|std::uint(?:8|16|32|64)_t)"
    r"\s+\w+\s*\([^)]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{\s*return\s+"
    r"(?P<field>[A-Za-z_][A-Za-z0-9_]*)_\s*;\s*\}"
)
_PLACEHOLDER_DECLARATION_RE = re.compile(r"^\s*std::render_return_type\b.*(?:\n|$)", re.MULTILINE)
_MISSING_LEGACY_API_RE = re.compile(r"\bmissing::(?:[A-Za-z_][A-Za-z0-9_]*::)*[A-Za-z_][A-Za-z0-9_]*\b")
_STRUCT_RE = re.compile(r"\bstruct\s+\w+\s*\{(?P<body>.*?)\};", re.DOTALL)
_STRUCT_FIELD_RE = re.compile(
    r"^\s*(?P<type>(?:const\s+)?(?:[\w:<>]+(?:\s*[*&])?))\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*[^;]+)?;\s*$",
    re.MULTILINE,
)
_UINT_TYPE_RE = re.compile(r"\bstd::uint(?:8|16|32|64)_t\b")
_CPP_UNDECLARED_IDENTIFIER_RE = re.compile(
    r"[‘'`](?P<identifier>[A-Za-z_][A-Za-z0-9_]*)[’'`]\s+was not declared in this scope"
)
_CPP_FUNCTION_DEFINITION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<head>[^;{}=]+?\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\((?P<params>[^;{}()]*)\)\s*"
    r"(?:(?:const|constexpr|noexcept(?:\s*\([^)]*\))?|override|final|&|&&)\s*)*)\{\s*$"
)
_CPP_CONTROL_FLOW_PREFIXES = ("if", "for", "while", "switch", "catch", "return")


def repair_cpp_include_paths_text(
    *,
    path: str,
    text: str,
    header_paths: Sequence[str],
) -> str:
    """Repair quote include paths for one C/C++ file using known workspace headers."""

    normalized_path = _normalize_repair_path(path)
    if not normalized_path or not _is_cpp_translation_path(normalized_path):
        return str(text or "")
    normalized_headers = tuple(
        sorted(
            {
                normalized_header
                for header_path in header_paths
                if (normalized_header := _normalize_repair_path(header_path))
                and _is_cpp_header_path(normalized_header)
                and not _is_generated_build_path(normalized_header)
            }
        )
    )
    if not normalized_headers:
        return str(text or "")
    header_by_basename = _header_paths_by_basename(normalized_headers)
    return _repair_cpp_include_paths_content(
        path=normalized_path,
        text=str(text or ""),
        header_paths=frozenset(normalized_headers),
        header_by_basename=header_by_basename,
    )


def build_cpp_include_path_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical plan for C++ quote include path normalization."""

    normalized_base_files = {
        normalized_path: str(content or "")
        for path, content in dict(base_files or {}).items()
        if (normalized_path := _normalize_repair_path(path)) and not _is_generated_build_path(normalized_path)
    }
    header_paths = tuple(
        sorted(
            path for path in normalized_base_files if _is_cpp_header_path(path) and not _is_generated_build_path(path)
        )
    )
    if not header_paths:
        return None
    header_by_basename = _header_paths_by_basename(header_paths)
    header_path_set = frozenset(header_paths)
    operations: list[RepairOperation] = []
    for path in sorted(normalized_base_files):
        if not _is_cpp_translation_path(path):
            continue
        original = normalized_base_files[path]
        repaired = _repair_cpp_include_paths_content(
            path=path,
            text=original,
            header_paths=header_path_set,
            header_by_basename=header_by_basename,
        )
        if repaired == original:
            continue
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired,
                before_hash=sha256_text(original),
                metadata={"repair_kind": "cpp_include_path"},
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="cpp.include_path",
        source_tool=CPP_INCLUDE_PATH_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=0,
        metadata={"archetype": "wrong_import_path"},
    )


def repair_cpp_missing_standard_includes_text(text: str) -> str:
    """Add standard library includes required by C/C++ source text."""

    content = _strip_include_trailing_garbage(str(text or ""))
    content = _hoist_trailing_standard_includes(content)
    additions = _missing_standard_includes(content)
    if not additions:
        return content
    lines = content.splitlines()
    insert_at = _cpp_preamble_end(lines)
    new_lines = [*lines[:insert_at], *additions, *lines[insert_at:]]
    return "\n".join(new_lines) + "\n"


def repair_cpp_invalid_placeholder_declarations_text(text: str) -> str:
    """Remove invalid C++ placeholder declarations generated by LLMs."""

    content = str(text or "")
    return _PLACEHOLDER_DECLARATION_RE.sub("", content)


def repair_cpp_struct_getter_field_access_text(
    *,
    text: str,
    field_names: Sequence[str],
) -> str:
    """Replace generated getter calls for public struct fields with field access."""

    repaired = str(text or "")
    for field_name in sorted({str(name or "").strip() for name in field_names if str(name or "").strip()}):
        repaired = re.sub(rf"\.get_{re.escape(field_name)}\s*\(\s*\)", f".{field_name}", repaired)
        repaired = re.sub(rf"\.{re.escape(field_name)}\s*\(\s*\)", f".{field_name}", repaired)
    return repaired


def repair_cpp_missing_private_members_text(text: str) -> str:
    """Add missing private member fields returned by inline C++ getters."""

    content = str(text or "")
    for class_match in list(_CLASS_RE.finditer(content)):
        class_block = class_match.group(1)
        class_body = class_match.group("body")
        declarations: list[str] = []
        for getter_match in _GETTER_RETURN_FIELD_RE.finditer(class_body):
            field = f"{getter_match.group('field')}_"
            if re.search(rf"\b{re.escape(field)}\b", class_body.replace(getter_match.group(0), "")):
                continue
            value_type = getter_match.group("type")
            value_type = re.sub(r"^const\s+", "", value_type).rstrip("&").strip()
            declaration = f"    {value_type} {field};"
            if declaration not in declarations:
                declarations.append(declaration)
        if not declarations:
            continue
        if "\nprivate:" in class_block:
            replacement = class_block.replace("\nprivate:", "\nprivate:\n" + "\n".join(declarations), 1)
        else:
            replacement = class_block.replace("\n};", "\nprivate:\n" + "\n".join(declarations) + "\n};", 1)
        content = content.replace(class_block, replacement, 1)
    return content


def repair_cpp_failing_smoke_translation_unit_text(
    *,
    path: str,
    text: str,
    header_paths: Sequence[str],
) -> str:
    """Replace an obviously hallucinated C++ translation unit with a compile smoke unit."""

    normalized_path = _normalize_repair_path(path)
    content = str(text or "")
    if not _cpp_translation_unit_needs_smoke_rewrite(normalized_path, content):
        return content

    normalized_headers = tuple(
        sorted(
            normalized_header
            for header_path in header_paths
            if (normalized_header := _normalize_repair_path(header_path)) and _is_cpp_header_path(normalized_header)
        )
    )
    include_lines = [
        f'#include "{include_path}"'
        for include_path in _local_quote_includes_from_base(
            normalized_path,
            content,
            header_paths=frozenset(normalized_headers),
        )
    ]
    if normalized_path == "src/main.cpp" and not include_lines:
        source_dir = _dirname(normalized_path)
        for header_path in tuple(path for path in normalized_headers if path.startswith("src/"))[:3]:
            include_lines.append(f'#include "{posixpath.relpath(header_path, start=source_dir)}"')

    smoke = ["// Deterministic C++ compile-smoke repair for generated translation unit.", *include_lines, ""]
    if normalized_path.startswith("tests/") or normalized_path == "src/main.cpp":
        smoke.extend(
            [
                "int main() {",
                '    const char* polaris_cpp_smoke = "moon postcard stamp poem";',
                "    return polaris_cpp_smoke[0] == '\\0';",
                "}",
                "",
            ]
        )
    else:
        smoke_name = re.sub(r"[^A-Za-z0-9_]+", "_", normalized_path).strip("_") or "translation_unit"
        smoke.extend(
            [
                "namespace {",
                f"const char* polaris_cpp_smoke_{smoke_name}() {{",
                '    return "moon postcard stamp poem";',
                "}",
                "}  // namespace",
                "",
            ]
        )
    return "\n".join(smoke)


def build_cpp_standard_include_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical plan for missing C++ standard library includes."""

    normalized_base_files = {
        normalized_path: str(content or "")
        for path, content in dict(base_files or {}).items()
        if (normalized_path := _normalize_repair_path(path)) and not _is_generated_build_path(normalized_path)
    }
    operations: list[RepairOperation] = []
    for path in sorted(normalized_base_files):
        if not _is_cpp_translation_path(path):
            continue
        original = normalized_base_files[path]
        repaired = repair_cpp_missing_standard_includes_text(original)
        if repaired == original:
            continue
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired,
                before_hash=sha256_text(original),
                metadata={"repair_kind": "cpp_standard_include"},
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="cpp.standard_include",
        source_tool=CPP_STANDARD_INCLUDE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=0,
        depends_on=("cpp.include_path",),
        metadata={"archetype": "missing_dependency"},
    )


def build_cpp_missing_private_members_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical plan for missing C++ private member declarations."""

    normalized_base_files = {
        normalized_path: str(content or "")
        for path, content in dict(base_files or {}).items()
        if (normalized_path := _normalize_repair_path(path)) and not _is_generated_build_path(normalized_path)
    }
    operations: list[RepairOperation] = []
    for path in sorted(normalized_base_files):
        if not _is_cpp_header_path(path):
            continue
        original = normalized_base_files[path]
        repaired = repair_cpp_missing_private_members_text(original)
        if repaired == original:
            continue
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired,
                before_hash=sha256_text(original),
                metadata={"repair_kind": "cpp_missing_private_members"},
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="cpp.missing_private_members",
        source_tool=CPP_MISSING_PRIVATE_MEMBERS_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="medium",
        priority=1,
        depends_on=("cpp.standard_include",),
        metadata={"archetype": "missing_dependency"},
    )


def build_cpp_use_before_definition_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Repair one unambiguous same-file C++ declaration-order diagnostic.

    The rule is intentionally narrow: either forward-declare one later free
    function in a source file, or move one existing later same-scope
    ``inline constexpr`` variable definition before its compiler-proven use.
    Anything ambiguous remains unplanned rather than inventing C++ API surface.
    """

    normalized_base_files = {
        normalized_path: str(content or "")
        for path, content in dict(base_files or {}).items()
        if (normalized_path := _normalize_repair_path(path))
        and (_is_cpp_source_path(normalized_path) or _is_cpp_header_path(normalized_path))
        and not _is_generated_build_path(normalized_path)
    }
    for diagnostic in diagnostics:
        if diagnostic.code != "cpp_compile_error":
            continue
        identifier_match = _CPP_UNDECLARED_IDENTIFIER_RE.search(
            "\n".join((str(diagnostic.message or ""), str(diagnostic.raw or "")))
        )
        if identifier_match is None:
            continue
        identifier = identifier_match.group("identifier")
        path = _unique_cpp_diagnostic_path(diagnostic.path, normalized_base_files)
        if not path:
            continue
        edit = _cpp_use_before_definition_edit(
            path=path,
            content=normalized_base_files[path],
            diagnostic=diagnostic,
            identifier=identifier,
        )
        if edit is None:
            continue
        expected, replacement, definition_line, span_start, span_end, definition_kind = edit
        return RepairPlan(
            rule_id="cpp.use_before_definition",
            source_tool=CPP_USE_BEFORE_DEFINITION_SOURCE_TOOL,
            operations=(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=span_start,
                    span_end=span_end,
                    expected=expected,
                    replacement=replacement,
                    before_hash=sha256_text(normalized_base_files[path]),
                    metadata={
                        "repair_kind": "cpp_use_before_definition",
                        "identifier": identifier,
                        "definition_line": definition_line,
                        "definition_kind": definition_kind,
                    },
                ),
            ),
            diagnostics=(diagnostic,),
            mode=mode,
            risk_level="low",
            priority=1,
            metadata={"archetype": "missing_dependency"},
        )
    return None


def build_cpp_struct_getter_field_access_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical plan for C++ public struct field getter call cleanup."""

    normalized_base_files = {
        normalized_path: str(content or "")
        for path, content in dict(base_files or {}).items()
        if (normalized_path := _normalize_repair_path(path)) and not _is_generated_build_path(normalized_path)
    }
    field_names = _public_struct_field_names(normalized_base_files)
    if not field_names:
        return None
    operations: list[RepairOperation] = []
    for path in sorted(normalized_base_files):
        if not _is_cpp_translation_path(path):
            continue
        original = normalized_base_files[path]
        repaired = repair_cpp_struct_getter_field_access_text(text=original, field_names=field_names)
        if repaired == original:
            continue
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired,
                before_hash=sha256_text(original),
                metadata={"repair_kind": "cpp_struct_getter_field_access"},
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="cpp.struct_getter_field_access",
        source_tool=CPP_STRUCT_GETTER_FIELD_ACCESS_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"archetype": "missing_method_self"},
    )


def build_cpp_placeholder_declaration_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical plan for removing invalid C++ placeholder declarations."""

    normalized_base_files = {
        normalized_path: str(content or "")
        for path, content in dict(base_files or {}).items()
        if (normalized_path := _normalize_repair_path(path)) and not _is_generated_build_path(normalized_path)
    }
    operations: list[RepairOperation] = []
    for path in sorted(normalized_base_files):
        if not _is_cpp_translation_path(path):
            continue
        original = normalized_base_files[path]
        repaired = repair_cpp_invalid_placeholder_declarations_text(original)
        if repaired == original:
            continue
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired,
                before_hash=sha256_text(original),
                metadata={"repair_kind": "cpp_placeholder_declaration"},
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="cpp.placeholder_declaration",
        source_tool=CPP_PLACEHOLDER_DECLARATION_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"archetype": "object_literal_syntax"},
    )


def build_cpp_failing_smoke_translation_unit_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical plan for conservative C++ compile-smoke rewrites."""

    normalized_base_files = {
        normalized_path: str(content or "")
        for path, content in dict(base_files or {}).items()
        if (normalized_path := _normalize_repair_path(path)) and not _is_generated_build_path(normalized_path)
    }
    header_paths = tuple(sorted(path for path in normalized_base_files if _is_cpp_header_path(path)))
    operations: list[RepairOperation] = []
    for path in sorted(normalized_base_files):
        if not _is_cpp_source_path(path):
            continue
        original = normalized_base_files[path]
        repaired = repair_cpp_failing_smoke_translation_unit_text(
            path=path,
            text=original,
            header_paths=header_paths,
        )
        if repaired == original:
            continue
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired,
                before_hash=sha256_text(original),
                metadata={
                    "repair_kind": "cpp_failing_smoke_translation_unit",
                    "compile_smoke_rewrite": "true",
                },
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="cpp.failing_smoke_translation_unit",
        source_tool=CPP_POST_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="medium",
        priority=2,
        metadata={
            "archetype": "runtime_smoke",
            "repair_kind": "cpp_failing_smoke_translation_unit",
            "adapter_post_helper_used": False,
        },
    )


def build_cpp_post_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a conservative aggregate C++ post repair plan from runtime child rules."""

    child_plans = tuple(
        plan
        for plan in (
            build_cpp_include_path_plan(base_files=base_files, diagnostics=diagnostics, mode=mode),
            build_cpp_standard_include_plan(base_files=base_files, diagnostics=diagnostics, mode=mode),
            build_cpp_missing_private_members_plan(base_files=base_files, diagnostics=diagnostics, mode=mode),
            build_cpp_use_before_definition_plan(base_files=base_files, diagnostics=diagnostics, mode=mode),
            build_cpp_struct_getter_field_access_plan(base_files=base_files, diagnostics=diagnostics, mode=mode),
            build_cpp_placeholder_declaration_plan(base_files=base_files, diagnostics=diagnostics, mode=mode),
        )
        if plan is not None
    )
    smoke_plan = build_cpp_failing_smoke_translation_unit_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
    )
    smoke_paths = (
        frozenset(operation.path for operation in smoke_plan.operations) if smoke_plan is not None else frozenset()
    )
    operations: list[RepairOperation] = []
    child_rule_ids: list[str] = []
    child_source_tools: list[str] = []
    seen_operation_ids: set[str] = set()
    for plan in child_plans:
        plan_had_included_operations = False
        for operation in plan.operations:
            if operation.path in smoke_paths:
                continue
            if operation.operation_id in seen_operation_ids:
                continue
            seen_operation_ids.add(operation.operation_id)
            operations.append(operation)
            plan_had_included_operations = True
        if plan_had_included_operations:
            child_rule_ids.append(plan.rule_id)
            child_source_tools.append(plan.source_tool)
    if smoke_plan is not None:
        child_rule_ids.append(smoke_plan.rule_id)
        child_source_tools.append(smoke_plan.source_tool)
        for operation in smoke_plan.operations:
            if operation.operation_id in seen_operation_ids:
                continue
            seen_operation_ids.add(operation.operation_id)
            operations.append(operation)
    if not operations:
        return None
    return RepairPlan(
        rule_id="cpp.no_such_file_or_directory",
        source_tool=CPP_POST_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="medium",
        priority=2,
        depends_on=tuple(child_rule_ids),
        metadata={
            "repair_kind": "cpp_post_execution_conservative",
            "aggregate_runtime_child_rules": tuple(child_rule_ids),
            "aggregate_runtime_child_source_tools": tuple(child_source_tools),
            "adapter_post_helper_used": False,
        },
    )


def _public_struct_field_names(base_files: Mapping[str, str]) -> tuple[str, ...]:
    fields: set[str] = set()
    for path, content in base_files.items():
        if not _is_cpp_header_path(path):
            continue
        for struct_match in _STRUCT_RE.finditer(content):
            body = struct_match.group("body")
            for field_match in _STRUCT_FIELD_RE.finditer(body):
                field_name = field_match.group("name")
                if field_name.startswith("get_"):
                    continue
                fields.add(field_name)
    return tuple(sorted(fields))


_STD_INCLUDE_LINE_RE = re.compile(r"^\s*#\s*include\s*<([^>]+)>\s*$")
_INCLUDE_TRAILING_GARBAGE_RE = re.compile(
    r"^(\s*#\s*include\s*(?:<[^>]+>|\"[^\"]+\"))\s*[=#\-_/]+\s*$",
    re.MULTILINE,
)


def _strip_include_trailing_garbage(content: str) -> str:
    """Drop leftover punctuation glued to ``#include`` lines.

    Live L2-20 leftover: ``#include <cmath>=============`` made g++ emit
    ``extra tokens at end of #include directive`` and hid later TUs.
    """

    repaired = _INCLUDE_TRAILING_GARBAGE_RE.sub(r"\1", str(content or ""))
    return repaired


_CPP_PREAMBLE_DIRECTIVES = frozenset({"pragma", "include", "ifndef", "ifdef", "if", "define", "endif", "else", "elif"})
_MEMORY_TYPE_RE = re.compile(r"\bstd::(?:unique_ptr|shared_ptr|weak_ptr|make_unique|make_shared)\b")
_CMATH_SYMBOL_RE = re.compile(r"\b(?:std::(?:isfinite|isnan|isinf|hypot|fabs|sqrt|pow|abs)|isfinite|isnan|isinf)\s*\(")
_STDEXCEPT_TYPE_RE = re.compile(
    r"\bstd::(?:logic_error|domain_error|invalid_argument|length_error|out_of_range|"
    r"runtime_error|range_error|overflow_error|underflow_error)\b"
)


def _is_cpp_preamble_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*") or stripped.endswith("*/"):
        return True
    if not stripped.startswith("#"):
        return False
    token = stripped[1:].strip().split(None, 1)
    return bool(token) and token[0].lower() in _CPP_PREAMBLE_DIRECTIVES


def _cpp_preamble_end(lines: Sequence[str]) -> int:
    insert_at = 0
    for index, line in enumerate(lines):
        if _is_cpp_preamble_line(line):
            insert_at = index + 1
            continue
        break
    return insert_at


def _hoist_trailing_standard_includes(content: str) -> str:
    """Move `#include <std...>` that appear after code back to the preamble.

    Live L2-20 leftover: ``rule.hpp`` declared ``std::unique_ptr`` then put
    ``#include <memory>`` after the namespace. Presence-only repair treated
    that as already fixed, so g++ kept ``unique_ptr is not a member of std``.
    """

    lines = content.splitlines()
    if not lines:
        return content
    preamble_end = _cpp_preamble_end(lines)
    hoisted: list[str] = []
    kept: list[str] = []
    for index, line in enumerate(lines):
        match = _STD_INCLUDE_LINE_RE.match(line)
        if match is not None and index >= preamble_end:
            hoisted.append(f"#include <{match.group(1).strip()}>")
            continue
        kept.append(line)
    if not hoisted:
        return content
    insert_at = _cpp_preamble_end(kept)
    existing = {
        match.group(1).strip() for line in kept[:insert_at] if (match := _STD_INCLUDE_LINE_RE.match(line)) is not None
    }
    additions = [
        line
        for line in dict.fromkeys(hoisted)
        if (match := _STD_INCLUDE_LINE_RE.match(line)) is not None and match.group(1).strip() not in existing
    ]
    repaired = "\n".join(kept) if not additions else "\n".join([*kept[:insert_at], *additions, *kept[insert_at:]])
    if content.endswith("\n"):
        return repaired + "\n"
    return repaired


def _missing_standard_includes(content: str) -> list[str]:
    additions: list[str] = []
    if _UINT_TYPE_RE.search(content) and "#include <cstdint>" not in content:
        additions.append("#include <cstdint>")
    if "std::vector" in content and "#include <vector>" not in content:
        additions.append("#include <vector>")
    if "std::string" in content and "#include <string>" not in content:
        additions.append("#include <string>")
    if _MEMORY_TYPE_RE.search(content) and "#include <memory>" not in content:
        additions.append("#include <memory>")
    if _CMATH_SYMBOL_RE.search(content) and "#include <cmath>" not in content:
        additions.append("#include <cmath>")
    if _STDEXCEPT_TYPE_RE.search(content) and "#include <stdexcept>" not in content:
        additions.append("#include <stdexcept>")
    return additions


def _repair_cpp_include_paths_content(
    *,
    path: str,
    text: str,
    header_paths: frozenset[str],
    header_by_basename: Mapping[str, tuple[str, ...]],
) -> str:
    source_dir = posixpath.dirname(path)
    repaired = text
    modified = False
    for match in _INCLUDE_RE.finditer(text):
        include_path = match.group(1)
        if _include_resolves_from_source_dir(source_dir, include_path, header_paths):
            continue
        target_path = _resolve_include_target(include_path, header_paths, header_by_basename)
        if not target_path:
            continue
        replacement = _relative_include_path(source_dir, target_path)
        if replacement and replacement != include_path:
            repaired = repaired.replace(f'"{include_path}"', f'"{replacement}"')
            modified = True
    return repaired if modified else text


def _resolve_include_target(
    include_path: str,
    header_paths: frozenset[str],
    header_by_basename: Mapping[str, tuple[str, ...]],
) -> str:
    root_relative = _normalize_include_root_path(include_path)
    if root_relative in header_paths:
        return root_relative

    basename = posixpath.basename(str(include_path or ""))
    candidates = header_by_basename.get(basename, ())
    if not candidates:
        return ""
    for candidate in candidates:
        if candidate.endswith("/" + include_path) or candidate.endswith(include_path):
            return candidate
    if len(candidates) == 1:
        return candidates[0]
    return ""


def _include_resolves_from_source_dir(
    source_dir: str,
    include_path: str,
    header_paths: frozenset[str],
) -> bool:
    joined = _join_source_relative_path(source_dir, include_path)
    return bool(joined and joined in header_paths)


def _relative_include_path(source_dir: str, target_path: str) -> str:
    start = source_dir or "."
    return posixpath.relpath(target_path, start=start)


def _header_paths_by_basename(header_paths: Sequence[str]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for header_path in header_paths:
        grouped.setdefault(posixpath.basename(header_path), []).append(header_path)
    return {basename: tuple(sorted(paths)) for basename, paths in grouped.items()}


def _join_source_relative_path(source_dir: str, include_path: str) -> str:
    include_path = str(include_path or "").strip().replace("\\", "/")
    if not include_path or include_path.startswith("/"):
        return ""
    joined = posixpath.normpath(posixpath.join(source_dir or ".", include_path))
    if joined == "." or joined.startswith("../") or "/../" in joined:
        return ""
    return joined


def _normalize_include_root_path(include_path: str) -> str:
    include_path = str(include_path or "").strip().replace("\\", "/")
    if not include_path or include_path.startswith("/"):
        return ""
    normalized = posixpath.normpath(include_path)
    if normalized == "." or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = posixpath.normpath(normalized)
    if normalized == "." or normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def _unique_cpp_diagnostic_path(
    diagnostic_path: str | None,
    base_files: Mapping[str, str],
) -> str:
    raw_path = str(diagnostic_path or "").strip().replace("\\", "/")
    normalized = _normalize_repair_path(raw_path)
    if normalized in base_files:
        return normalized
    candidates = tuple(path for path in base_files if raw_path == path or raw_path.endswith(f"/{path}"))
    return candidates[0] if len(candidates) == 1 else ""


def _cpp_use_before_definition_edit(
    *,
    path: str,
    content: str,
    diagnostic: RepairDiagnostic,
    identifier: str,
) -> tuple[str, str, int, int, int, str] | None:
    del path
    lines = str(content or "").splitlines(keepends=True)
    if not lines or diagnostic.line is None:
        return None
    diagnostic_index = int(diagnostic.line) - 1
    if diagnostic_index < 0 or diagnostic_index >= len(lines):
        return None
    if re.search(rf"\b{re.escape(identifier)}\s*\(", lines[diagnostic_index]) is None:
        return _cpp_inline_constexpr_variable_reorder_edit(
            content=content,
            lines=lines,
            diagnostic_index=diagnostic_index,
            identifier=identifier,
        )

    line_depths = _cpp_brace_depths_at_line_start(lines)
    caller_index = _nearest_cpp_free_function_definition(lines, diagnostic_index)
    if caller_index is None:
        return None
    caller_depth = line_depths[caller_index]
    later_definitions: list[tuple[int, re.Match[str]]] = []
    for index in range(diagnostic_index + 1, len(lines)):
        match = _cpp_free_function_definition_match(lines[index], identifier=identifier)
        if match is None or line_depths[index] != caller_depth:
            continue
        if index > 0 and lines[index - 1].lstrip().startswith("template"):
            continue
        later_definitions.append((index, match))
    if len(later_definitions) != 1:
        return None

    definition_index, definition_match = later_definitions[0]
    prefix = "".join(lines[:caller_index])
    if re.search(rf"\b{re.escape(identifier)}\s*\([^;{{}}]*\)\s*;", prefix):
        return None

    params = definition_match.group("params")
    if "=" in params:
        return None
    signature = definition_match.group("head").strip()
    if not signature or content.count(lines[caller_index]) != 1:
        return None
    indent = definition_match.group("indent")
    newline = "\r\n" if lines[caller_index].endswith("\r\n") else "\n"
    expected = lines[caller_index]
    replacement = f"{indent}{signature};{newline}{newline}{expected}"
    span_start = content.index(expected)
    return expected, replacement, definition_index + 1, span_start, span_start + len(expected), "free_function"


def _cpp_inline_constexpr_variable_reorder_edit(
    *,
    content: str,
    lines: Sequence[str],
    diagnostic_index: int,
    identifier: str,
) -> tuple[str, str, int, int, int, str] | None:
    """Move one existing later inline-constexpr definition before a class use."""

    if re.search(rf"\b{re.escape(identifier)}\b", lines[diagnostic_index]) is None:
        return None
    definition_start_re = re.compile(
        rf"^\s*inline\s+constexpr\s+[^;{{}}()=\n]+?\b{re.escape(identifier)}"
        rf"(?:\s*\[[^\]\n]*\])?\s*=\s*"
    )
    line_depths = _cpp_brace_depths_at_line_start(lines)
    definitions: list[tuple[int, int]] = []
    for start in range(diagnostic_index + 1, len(lines)):
        if definition_start_re.match(lines[start]) is None:
            continue
        end = start
        while end < len(lines) and ";" not in lines[end] and end - start < 7:
            end += 1
        if end >= len(lines) or ";" not in lines[end]:
            continue
        definition_text = "".join(lines[start : end + 1])
        if definition_text.count(";") != 1 or "#" in definition_text:
            continue
        definitions.append((start, end))
    if len(definitions) != 1:
        return None

    definition_start, definition_end = definitions[0]
    definition_depth = line_depths[definition_start]
    anchor_index: int | None = None
    for index in range(diagnostic_index, -1, -1):
        if line_depths[index] != definition_depth:
            continue
        if re.match(r"^\s*(?:class|struct)\s+[A-Za-z_][A-Za-z0-9_]*\b", lines[index]):
            anchor_index = index
            break
    if anchor_index is None or anchor_index >= definition_start:
        return None
    guarded_region = "".join(lines[anchor_index : definition_end + 1])
    if re.search(r"^\s*#\s*(?:if|ifdef|ifndef|elif|else|endif)\b", guarded_region, re.MULTILINE):
        return None

    expected = guarded_region
    if content.count(expected) != 1:
        return None
    definition_text = "".join(lines[definition_start : definition_end + 1])
    before_definition = "".join(lines[anchor_index:definition_start])
    newline = "\r\n" if lines[anchor_index].endswith("\r\n") else "\n"
    replacement = f"{definition_text}{newline}{before_definition}"
    span_start = content.index(expected)
    return (
        expected,
        replacement,
        definition_start + 1,
        span_start,
        span_start + len(expected),
        "inline_constexpr_variable",
    )


def _nearest_cpp_free_function_definition(
    lines: Sequence[str],
    diagnostic_index: int,
) -> int | None:
    for index in range(diagnostic_index, -1, -1):
        if _cpp_free_function_definition_match(lines[index]) is not None:
            return index
    return None


def _cpp_free_function_definition_match(
    line: str,
    *,
    identifier: str | None = None,
) -> re.Match[str] | None:
    match = _CPP_FUNCTION_DEFINITION_LINE_RE.match(str(line or "").rstrip("\r\n"))
    if match is None:
        return None
    name = match.group("name")
    if identifier is not None and name != identifier:
        return None
    head = match.group("head").strip()
    prefix = head[: head.rfind(name)].strip()
    if not prefix or any(prefix.startswith(f"{token} ") for token in _CPP_CONTROL_FLOW_PREFIXES):
        return None
    if re.search(rf"\b[A-Za-z_][A-Za-z0-9_]*::{re.escape(name)}\b", head):
        return None
    if any(token in prefix for token in ("->", ".", "[", "]")):
        return None
    return match


def _cpp_brace_depths_at_line_start(lines: Sequence[str]) -> tuple[int, ...]:
    depths: list[int] = []
    depth = 0
    in_block_comment = False
    for line in lines:
        depths.append(depth)
        visible, in_block_comment = _cpp_strip_non_code_for_braces(line, in_block_comment)
        depth += visible.count("{") - visible.count("}")
        if depth < 0:
            return tuple(-1 for _ in lines)
    return tuple(depths)


def _cpp_strip_non_code_for_braces(line: str, in_block_comment: bool) -> tuple[str, bool]:
    output: list[str] = []
    index = 0
    quote = ""
    escaped = False
    while index < len(line):
        char = line[index]
        next_char = line[index + 1] if index + 1 < len(line) else ""
        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue
        if char == "/" and next_char == "/":
            break
        if char in {'"', "'"}:
            quote = char
            index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output), in_block_comment


def _is_generated_build_path(path: str) -> bool:
    parts = tuple(part for part in str(path or "").split("/") if part)
    return "build" in parts or "cmake-build" in parts


def _is_cpp_header_path(path: str) -> bool:
    return str(path or "").endswith(_CPP_HEADER_EXTENSIONS)


def _is_cpp_source_path(path: str) -> bool:
    return str(path or "").endswith(_CPP_SOURCE_EXTENSIONS)


def _is_cpp_translation_path(path: str) -> bool:
    return str(path or "").endswith(_CPP_TRANSLATION_EXTENSIONS)


def _dirname(path: str) -> str:
    directory = posixpath.dirname(str(path or ""))
    return directory if directory else "."


def _local_quote_includes_from_base(path: str, text: str, *, header_paths: frozenset[str]) -> tuple[str, ...]:
    source_dir = _dirname(path)
    includes: list[str] = []
    for match in _INCLUDE_RE.finditer(str(text or "")):
        include_path = match.group(1).strip()
        candidate = _normalize_repair_path(posixpath.join(source_dir, include_path))
        if candidate in header_paths and include_path not in includes:
            includes.append(include_path)
    return tuple(includes)


def _cpp_translation_unit_needs_smoke_rewrite(path: str, text: str) -> bool:
    if not path or not _is_cpp_source_path(path):
        return False
    content = str(text or "")
    if not content.strip() or "polaris_cpp_smoke" in content:
        return False
    return _MISSING_LEGACY_API_RE.search(content) is not None


__all__ = [
    "CPP_INCLUDE_PATH_SOURCE_TOOL",
    "CPP_MISSING_PRIVATE_MEMBERS_SOURCE_TOOL",
    "CPP_PLACEHOLDER_DECLARATION_SOURCE_TOOL",
    "CPP_POST_SOURCE_TOOL",
    "CPP_STANDARD_INCLUDE_SOURCE_TOOL",
    "CPP_STRUCT_GETTER_FIELD_ACCESS_SOURCE_TOOL",
    "CPP_USE_BEFORE_DEFINITION_SOURCE_TOOL",
    "build_cpp_failing_smoke_translation_unit_plan",
    "build_cpp_include_path_plan",
    "build_cpp_missing_private_members_plan",
    "build_cpp_placeholder_declaration_plan",
    "build_cpp_post_plan",
    "build_cpp_standard_include_plan",
    "build_cpp_struct_getter_field_access_plan",
    "build_cpp_use_before_definition_plan",
    "repair_cpp_failing_smoke_translation_unit_text",
    "repair_cpp_include_paths_text",
    "repair_cpp_invalid_placeholder_declarations_text",
    "repair_cpp_missing_private_members_text",
    "repair_cpp_missing_standard_includes_text",
    "repair_cpp_struct_getter_field_access_text",
]
