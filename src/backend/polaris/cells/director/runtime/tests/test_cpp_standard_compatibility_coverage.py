"""Coverage guard for compiler-proven C++ language-standard incompatibility."""

from __future__ import annotations

from polaris.cells.director.runtime.public import (
    QueryDirectorRepairCoverageV1,
    query_director_repair_coverage,
)


def _coverage(*raw: str) -> dict[str, object]:
    return query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=raw)
    ).to_dict()


def test_cpp20_only_api_does_not_match_missing_standard_include_rule() -> None:
    """L3-24 r52: adding an include cannot make std::span legal under C++17."""

    truncated_primary = "include/pkg/cipher.hpp:34:38: error: ‘std::span’ has not been declared\n"
    complete_compiler_blob = (
        "include/pkg/cipher.hpp:34:38: error: ‘std::span’ has not been declared\n"
        "src/cipher.cpp:84:59: note: ‘std::span’ is only available from C++20 onwards\n"
    )
    payload = _coverage(truncated_primary, complete_compiler_blob)
    cpp_items = [
        item
        for item in payload["items"]
        if item["diagnostic"]["code"] == "cpp_language_standard_incompatibility"
    ]

    assert cpp_items
    assert all("cpp.standard_include" not in item["matched_rule_ids"] for item in cpp_items)
    assert all(
        "deterministic_cpp_standard_include_repair" not in item["matched_source_tools"] for item in cpp_items
    )
    assert all(item["diagnostic"]["metadata"]["required_standard"] == "c++20" for item in cpp_items)


def test_cpp20_chrono_calendar_api_without_compiler_note_is_standard_incompatibility() -> None:
    """L3-24 r73: GCC omits the C++20 note for ``year_month_day`` under C++17."""

    raw = (
        "include/invisible_ink/moon_phase.hpp:43:41: error: "
        "‘year_month_day’ is not a member of ‘std::chrono’\n"
        "43 | MoonPhase computeMoonPhase(std::chrono::year_month_day date);\n"
    )
    payload = _coverage(raw)
    items = payload["items"]

    assert len(items) == 1
    assert items[0]["diagnostic"]["code"] == "cpp_language_standard_incompatibility"
    assert items[0]["diagnostic"]["metadata"]["incompatible_symbol"] == (
        "std::chrono::year_month_day"
    )
    assert items[0]["diagnostic"]["metadata"]["required_standard"] == "c++20"
    assert "cpp.standard_include" not in items[0]["matched_rule_ids"]
    assert "deterministic_cpp_standard_include_repair" not in items[0]["matched_source_tools"]


def test_actual_missing_standard_include_remains_covered() -> None:
    raw = (
        "src/models/seed.hpp:2:24: error: ‘uint32_t’ in namespace ‘std’ does not name a type\n"
        "2 | std::uint32_t seed();\n"
    )
    payload = _coverage(raw)
    item = payload["items"][0]

    assert "cpp.standard_include" in item["matched_rule_ids"]
    assert "deterministic_cpp_standard_include_repair" in item["matched_source_tools"]


def test_unrelated_cpp_error_inside_std_string_function_does_not_claim_standard_include() -> None:
    """L3-24 r53: source-line ``std::string`` is not missing-include evidence."""

    raw = (
        "/workspace/src/diary.cpp: In member function "
        "‘bool DiaryEngine::write(const std::string&, const std::string&)’:\n"
        "/workspace/src/diary.cpp:24:13: error: ‘cipher_’ was not declared in this scope; "
        "did you mean ‘Cipher’?\n"
        "   24 |     Ink ink(cipher_);\n"
        "      |             ^~~~~~~\n"
        "      |             Cipher\n"
    )
    payload = _coverage(raw)
    item = payload["items"][0]

    assert item["diagnostic"]["code"] == "cpp_compile_error"
    assert "cpp.standard_include" not in item["matched_rule_ids"]
    assert "deterministic_cpp_standard_include_repair" not in item["matched_source_tools"]


def test_multi_error_cpp_transcript_preserves_same_message_at_distinct_lines() -> None:
    """L3-24 r53: both unresolved ``cipher_`` occurrences remain actionable."""

    raw = (
        "src/diary.cpp:24:13: error: ‘cipher_’ was not declared in this scope; did you mean ‘Cipher’?\n"
        "   24 |     Ink ink(cipher_);\n"
        "src/diary.cpp:46:13: error: ‘cipher_’ was not declared in this scope; did you mean ‘Cipher’?\n"
        "   46 |     Ink ink(cipher_);\n"
    )
    payload = _coverage(raw)
    cpp_items = [
        item for item in payload["items"] if item["diagnostic"]["code"] == "cpp_compile_error"
    ]

    assert len(cpp_items) == 2
    assert {item["diagnostic"]["line"] for item in cpp_items} == {24, 46}
    assert all("cpp.standard_include" not in item["matched_rule_ids"] for item in cpp_items)
