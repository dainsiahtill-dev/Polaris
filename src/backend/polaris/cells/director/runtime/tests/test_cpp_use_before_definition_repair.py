"""Regression coverage for C++ same-translation-unit declaration ordering."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.director.runtime.internal.repair_kernel import RepairOperation
from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairCoverageV1,
    RunDirectorRepairCommandV1,
    plan_director_repair,
    query_director_repair_coverage,
    run_director_repair,
)

SOURCE_TOOL = "deterministic_cpp_use_before_definition_repair"
RULE_ID = "cpp.use_before_definition"
RELATIVE_PATH = "src/moon_phase.cpp"
DIAGNOSTIC = (
    "src/moon_phase.cpp:7:38: error: "
    "‘ymd_to_days’ was not declared in this scope\n"
    "    7 |     return static_cast<std::int64_t>(ymd_to_days(value));\n"
)
SOURCE = (
    "#include <cstdint>\n"
    "namespace invisible_ink {\n"
    "namespace {\n"
    "struct ymd { int year; int month; int day; };\n"
    "\n"
    "std::int64_t days_from_civil(ymd value) noexcept {\n"
    "    return static_cast<std::int64_t>(ymd_to_days(value));\n"
    "}\n"
    "\n"
    "std::int64_t ymd_to_days(ymd value) noexcept {\n"
    "    return value.year + value.month + value.day;\n"
    "}\n"
    "}  // namespace\n"
    "}  // namespace invisible_ink\n"
)

HEADER_PATH = "include/lunaris/invisible_diary.hpp"
VARIABLE_DIAGNOSTIC = (
    "include/lunaris/invisible_diary.hpp:6:33: error: ‘DEFAULT_CIPHER_ALPHABET’ was not declared in this scope"
)
VARIABLE_SOURCE = (
    "#pragma once\n"
    "namespace lunaris::diary {\n"
    "class Cipher {\n"
    "public:\n"
    "    explicit Cipher(\n"
    "        const char* alphabet = DEFAULT_CIPHER_ALPHABET\n"
    "    );\n"
    "};\n"
    "\n"
    "inline constexpr const char* DEFAULT_CIPHER_ALPHABET =\n"
    '    "abcdefghijklmnopqrstuvwxyz";\n'
    "}\n"
)


def test_cpp_use_before_definition_coverage_routes_to_precise_runtime_rule() -> None:
    payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(DIAGNOSTIC,))
    ).to_dict()
    item = payload["items"][0]

    assert RULE_ID in item["matched_rule_ids"]
    assert SOURCE_TOOL in item["matched_source_tools"]
    assert "cpp.missing_private_members" not in item["matched_rule_ids"]
    assert item["executable_runtime_plan_matched"] is True


def test_cpp_use_before_definition_public_plan_inserts_forward_declaration() -> None:
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=SOURCE_TOOL,
            base_files={RELATIVE_PATH: SOURCE},
            artifact_quality_errors=(DIAGNOSTIC,),
            mode="shadow",
        )
    ).to_dict()

    assert result["ok"] is True
    assert result["planned"] is True
    assert result["plan_summary"]["rule_id"] == RULE_ID
    assert result["composition_summary"]["patch_count"] == 1
    assert result["effect_plan"]["effects"][0]["tool_name"] == "edit_file"
    content_after = result["composition_summary"]["patches"][0]["content_after"]
    assert (
        "std::int64_t ymd_to_days(ymd value) noexcept;\n\nstd::int64_t days_from_civil(ymd value) noexcept {"
    ) in content_after


def test_cpp_use_before_definition_public_run_uses_precise_edit_and_receipt(tmp_path: Path) -> None:
    target = tmp_path / RELATIVE_PATH
    target.parent.mkdir(parents=True)
    target.write_text(SOURCE, encoding="utf-8")
    edit_calls: list[RepairOperation] = []
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append((path, content))
        return {"ok": False, "file": path, "error": "write_file must not be used"}

    def editor(operation: RepairOperation) -> dict[str, object]:
        assert operation.expected is not None
        assert operation.replacement is not None
        before = target.read_text(encoding="utf-8")
        assert before.count(operation.expected) == 1
        target.write_text(
            before.replace(operation.expected, operation.replacement, 1),
            encoding="utf-8",
        )
        edit_calls.append(operation)
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-cpp-declaration-order",
            workspace=str(tmp_path),
            source_tool=SOURCE_TOOL,
            base_files={RELATIVE_PATH: SOURCE},
            artifact_quality_errors=(DIAGNOSTIC,),
            allowed_paths=(RELATIVE_PATH,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert write_calls == []
    assert len(edit_calls) == 1
    assert edit_calls[0].kind == "text_replace"
    assert len(result.receipts) == 1
    assert result.receipts[0].source_tool == SOURCE_TOOL
    assert result.receipts[0].files_changed == (RELATIVE_PATH,)
    assert "ymd_to_days(ymd value) noexcept;" in target.read_text(encoding="utf-8")


def test_cpp_use_before_definition_fails_closed_for_qualified_member_definition() -> None:
    source = (
        "struct Widget { int calculate(int) const; };\n"
        "int caller(Widget& widget) { return calculate(1); }\n"
        "int Widget::calculate(int value) const { return value; }\n"
    )
    diagnostic = "src/widget.cpp:2:38: error: ‘calculate’ was not declared in this scope"

    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=SOURCE_TOOL,
            base_files={"src/widget.cpp": source},
            artifact_quality_errors=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert result["ok"] is False
    assert result["planned"] is False
    assert result["effect_plan"] is None
    assert result["composition_summary"]["patch_count"] == 0


def test_cpp_use_before_definition_coverage_accepts_inline_constexpr_header_variable() -> None:
    payload = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=(VARIABLE_DIAGNOSTIC,))
    ).to_dict()
    item = payload["items"][0]

    assert RULE_ID in item["matched_rule_ids"]
    assert SOURCE_TOOL in item["matched_source_tools"]
    assert item["executable_runtime_plan_matched"] is True


def test_cpp_use_before_definition_plan_moves_existing_inline_constexpr_definition() -> None:
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=SOURCE_TOOL,
            base_files={HEADER_PATH: VARIABLE_SOURCE},
            artifact_quality_errors=(VARIABLE_DIAGNOSTIC,),
            mode="shadow",
        )
    ).to_dict()

    assert result["ok"] is True
    assert result["planned"] is True
    assert result["plan_summary"]["rule_id"] == RULE_ID
    patch = result["composition_summary"]["patches"][0]
    content_after = patch["content_after"]
    assert content_after.count("inline constexpr const char* DEFAULT_CIPHER_ALPHABET") == 1
    assert content_after.index("inline constexpr const char* DEFAULT_CIPHER_ALPHABET") < content_after.index(
        "class Cipher"
    )


def test_cpp_use_before_definition_variable_run_uses_precise_edit_and_receipt(tmp_path: Path) -> None:
    target = tmp_path / HEADER_PATH
    target.parent.mkdir(parents=True)
    target.write_text(VARIABLE_SOURCE, encoding="utf-8")
    edit_calls: list[RepairOperation] = []
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append((path, content))
        return {"ok": False, "file": path, "error": "write_file must not be used"}

    def editor(operation: RepairOperation) -> dict[str, object]:
        assert operation.expected is not None
        assert operation.replacement is not None
        before = target.read_text(encoding="utf-8")
        assert before.count(operation.expected) == 1
        target.write_text(
            before.replace(operation.expected, operation.replacement, 1),
            encoding="utf-8",
        )
        edit_calls.append(operation)
        return {"ok": True, "file": operation.path, "operation": "edit_file"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-cpp-variable-order",
            workspace=str(tmp_path),
            source_tool=SOURCE_TOOL,
            base_files={HEADER_PATH: VARIABLE_SOURCE},
            artifact_quality_errors=(VARIABLE_DIAGNOSTIC,),
            allowed_paths=(HEADER_PATH,),
        ),
        writer=writer,
        editor=editor,
    )

    assert result.ok is True
    assert write_calls == []
    assert len(edit_calls) == 1
    assert edit_calls[0].kind == "text_replace"
    assert len(result.receipts) == 1
    assert result.receipts[0].files_changed == (HEADER_PATH,)


def test_cpp_use_before_definition_variable_fails_closed_for_conditional_definition() -> None:
    source = VARIABLE_SOURCE.replace(
        'inline constexpr const char* DEFAULT_CIPHER_ALPHABET =\n    "abcdefghijklmnopqrstuvwxyz";',
        "#if USE_CUSTOM_ALPHABET\n"
        "inline constexpr const char* DEFAULT_CIPHER_ALPHABET =\n"
        '    "abcdefghijklmnopqrstuvwxyz";\n'
        "#endif",
    )

    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=SOURCE_TOOL,
            base_files={HEADER_PATH: source},
            artifact_quality_errors=(VARIABLE_DIAGNOSTIC,),
            mode="shadow",
        )
    ).to_dict()

    assert result["ok"] is False
    assert result["planned"] is False
    assert result["effect_plan"] is None
