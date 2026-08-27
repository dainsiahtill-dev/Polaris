from pathlib import Path

from polaris.cells.roles.adapters.internal.director.quality_gate._prompt_and_targets import (
    _build_materialization_quality_repair_message,
)


def _cpp_target(tmp_path: Path) -> Path:
    target = tmp_path / "invisible_diary" / "cli.cpp"
    target.parent.mkdir()
    lines = [f"// line {index}" for index in range(1, 121)]
    lines[17] = '#include "cipher.hpp"'
    lines[71] = "std::unique_ptr<Cipher> cipher_ptr;"
    lines[111] = "Cipher cipher;"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def test_primary_diagnostic_prioritizes_compiler_error_over_include_note(tmp_path: Path) -> None:
    _cpp_target(tmp_path)

    message = _build_materialization_quality_repair_message(
        original_message="Repair the C++ compile failure.",
        artifact_quality_errors=[
            "invisible_diary/cli.cpp:112:10: error: no matching function for call to Cipher::Cipher()\n"
            "In file included from invisible_diary/cli.cpp:18:\n"
            "invisible_diary/cipher.hpp:58:12: note: candidate expects 1 argument"
        ],
        changed_files=["invisible_diary/cli.cpp"],
        repair_target_files=["invisible_diary/cli.cpp"],
        workspace_full=str(tmp_path),
    )

    primary = message.split("PRIMARY DIAGNOSTIC SITE(S):", 1)[1].split("CURRENT UTF-8 CONTENT OF REPAIR TARGETS:", 1)[0]
    assert primary.index("invisible_diary/cli.cpp:112") < primary.index("invisible_diary/cli.cpp:18")


def test_primary_diagnostic_handles_literal_newline_embedded_compiler_output(tmp_path: Path) -> None:
    """Nested unittest skip text must not promote every path on one physical line."""
    _cpp_target(tmp_path)
    nested = (
        "setUpClass skipped 'cmake build failed: "
        "invisible_diary/cli.cpp:112:10: error: no matching function for call to Cipher::Cipher()"
        "\\nIn file included from invisible_diary/cli.cpp:18:"
        "\\ninvisible_diary/cipher.hpp:58:12: note: candidate expects 1 argument'"
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the nested C++ compile failure.",
        artifact_quality_errors=[nested],
        changed_files=["invisible_diary/cli.cpp"],
        repair_target_files=["invisible_diary/cli.cpp"],
        workspace_full=str(tmp_path),
    )

    primary = message.split("PRIMARY DIAGNOSTIC SITE(S):", 1)[1].split("CURRENT UTF-8 CONTENT OF REPAIR TARGETS:", 1)[0]
    assert primary.index("invisible_diary/cli.cpp:112") < primary.index("invisible_diary/cli.cpp:18")


def test_compiler_regression_history_is_negative_not_actionable_context(tmp_path: Path) -> None:
    _cpp_target(tmp_path)
    current = "invisible_diary/cli.cpp:112:10: error: no matching function for call to Cipher::Cipher()"
    historical = (
        "invisible_diary/cli.cpp: In function ‘int run_encode()’:\n"
        "invisible_diary/cli.cpp:72:10: error: no matching function for call to Cipher::Cipher()\n"
        "invisible_diary/cli.cpp: In function ‘int run_decode()’:\n"
        "invisible_diary/cli.cpp:112:10: error: no matching function for call to Cipher::Cipher()"
    )
    current = (
        "invisible_diary/cli.cpp: In function ‘int run_decode()’:\n"
        "invisible_diary/cli.cpp:112:10: error: no matching function for call to Cipher::Cipher()"
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the remaining C++ compile failure.",
        artifact_quality_errors=[current],
        regression_guard_errors=[historical],
        changed_files=["invisible_diary/cli.cpp"],
        repair_target_files=["invisible_diary/cli.cpp"],
        workspace_full=str(tmp_path),
    )

    guard = message.split("REGRESSION GUARDS FROM THE PREVIOUS REPAIR ROUND:", 1)[1].split(
        "EDIT CONSISTENCY PREFLIGHT", 1
    )[0]
    assert "RESOLVED compiler guard; do not act on it" in guard
    assert "[int run_encode()]" in guard
    assert "[int run_decode()]" not in guard
    assert "REGRESSION GUARD VERIFIER SOURCE CONTEXT" not in message


def test_literal_newline_compiler_history_is_not_replayed_as_behavior_context(tmp_path: Path) -> None:
    _cpp_target(tmp_path)
    current = (
        "invisible_diary/cli.cpp: In function ‘int run_decode()’:\n"
        "invisible_diary/cli.cpp:112:10: error: no matching function for call to Cipher::Cipher()"
    )
    nested_history = (
        "setUpClass skipped 'cmake build failed: "
        "invisible_diary/cli.cpp: In function ‘int run_encode()’:"
        "\\ninvisible_diary/cli.cpp:72:10: error: no matching function for call to Cipher::Cipher()"
        "\\ninvisible_diary/cli.cpp: In function ‘int run_decode()’:"
        "\\ninvisible_diary/cli.cpp:112:10: error: no matching function for call to Cipher::Cipher()'"
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the remaining C++ compile failure.",
        artifact_quality_errors=[current],
        regression_guard_errors=[nested_history],
        changed_files=["invisible_diary/cli.cpp"],
        repair_target_files=["invisible_diary/cli.cpp"],
        workspace_full=str(tmp_path),
    )

    guard = message.split("REGRESSION GUARDS FROM THE PREVIOUS REPAIR ROUND:", 1)[1].split(
        "EDIT CONSISTENCY PREFLIGHT", 1
    )[0]
    assert "RESOLVED compiler guard; do not act on it" in guard
    assert "[int run_encode()]" in guard
    assert "[int run_decode()]" not in guard
    assert "setUpClass skipped" not in guard


def test_rejected_compiler_candidate_does_not_replay_rolled_back_source_context(tmp_path: Path) -> None:
    _cpp_target(tmp_path)

    message = _build_materialization_quality_repair_message(
        original_message="Repair the remaining C++ compile failure.",
        artifact_quality_errors=[
            "invisible_diary/cli.cpp:112:10: error: no matching function for call to Cipher::Cipher()"
        ],
        candidate_rejection_errors=[
            "invisible_diary/cli.cpp:72:8: error: unique_ptr is not a member of std\n"
            "invisible_diary/cli.cpp:112:10: error: no matching function for call to Cipher::Cipher()"
        ],
        changed_files=["invisible_diary/cli.cpp"],
        repair_target_files=["invisible_diary/cli.cpp"],
        workspace_full=str(tmp_path),
    )

    rejection = message.split("PREVIOUS CANDIDATE REJECTED BEFORE COMMIT:", 1)[1].split(
        "EDIT CONSISTENCY PREFLIGHT", 1
    )[0]
    assert "REJECTED-ONLY compiler signature; do not repair as current" in rejection
    assert "unique_ptr is not a member of std" in rejection
    assert "cli.cpp:112" not in rejection
    assert "REJECTED CANDIDATE VERIFIER SOURCE CONTEXT" not in message
