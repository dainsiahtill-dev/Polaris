"""Regression tests for C++ verifier transcript normalization."""

from __future__ import annotations

from polaris.cells.director.runtime.internal.repair_kernel import normalize_artifact_quality_errors


def test_nested_cmake_transcript_separates_warning_from_linker_failure() -> None:
    """Escaped unittest output must not create ``/n/tmp`` pseudo-paths.

    L3-24 r41 wrapped CMake stderr inside one unittest skip reason.  Literal
    ``\\n`` separators were parsed as path characters, while the first warning
    swallowed every linker error.  Keep compiler warning and linker failure as
    separate causal diagnostics.
    """

    nested = (
        'setUpClass (test_product.TestCliSmoke) ... skipped "build failed: '
        "cmake build failed (rc=2)\\nstdout:\\n[100%] Linking CXX executable app\\n"
        "stderr:\\n/tmp/project/src/cipher.cpp:17:8: warning: sign conversion [-Wsign-conversion]\\n"
        "/usr/bin/ld: CMakeFiles/app.dir/src/main.cpp.o: undefined reference to "
        "`pkg::moon_token(pkg::MoonPhase)'\\n"
        "collect2: error: ld returned 1 exit status\\n\""
    )
    direct = (
        "/tmp/project/src/cipher.cpp:17:8: warning: sign conversion [-Wsign-conversion]\n"
        "/usr/bin/ld: CMakeFiles/app.dir/src/main.cpp.o: undefined reference to "
        "`pkg::moon_token(pkg::MoonPhase)'\n"
        "collect2: error: ld returned 1 exit status"
    )

    diagnostics = normalize_artifact_quality_errors((direct, nested))

    assert [(item.code, item.path) for item in diagnostics] == [
        ("cpp_compile_error", "/tmp/project/src/cipher.cpp"),
        ("cpp_linker_undefined_reference", None),
    ]
    assert "moon_token" in diagnostics[1].message
    assert all(not str(item.path or "").startswith("/n/") for item in diagnostics)
