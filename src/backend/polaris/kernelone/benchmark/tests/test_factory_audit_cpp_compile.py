"""Regression tests for the C++ compile audit gate (include-root parity)."""

from __future__ import annotations

import shutil

import pytest
from polaris.kernelone.benchmark.factory_audit import _check_cpp_compile


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ toolchain is not available")
def test_cpp_compile_resolves_src_rooted_includes(tmp_path) -> None:
    """CMake-style ``src/`` include roots must resolve in the audit gate.

    Live L1-06: generated projects include headers as
    ``#include "engine/generator.hpp"`` relative to ``src/`` (the conventional
    ``target_include_directories`` layout).  The platform workspace-quality
    check already passes ``-I . -I src -I include``; the audit gate without
    those roots reported ``No such file or directory`` — a measurement
    misattribution that masked the real cross-file symbol defects.
    """

    engine_dir = tmp_path / "src" / "engine"
    engine_dir.mkdir(parents=True)
    (engine_dir / "generator.hpp").write_text(
        "#pragma once\n#include <string>\n\nnamespace moonpost {\nstruct Moon {\n    std::string phase;\n};\n}\n",
        encoding="utf-8",
    )
    (engine_dir / "generator.cpp").write_text(
        '#include "engine/generator.hpp"\n\nnamespace moonpost {\nstd::string phase_of(const Moon& moon) { return moon.phase; }\n}\n',
        encoding="utf-8",
    )

    ok, detail = _check_cpp_compile(str(tmp_path))

    assert ok is True, detail


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ toolchain is not available")
def test_cpp_compile_still_fails_on_real_symbol_errors(tmp_path) -> None:
    """Include-root parity must not weaken the gate for genuine defects."""

    engine_dir = tmp_path / "src" / "engine"
    engine_dir.mkdir(parents=True)
    (engine_dir / "generator.hpp").write_text(
        "#pragma once\n\nnamespace moonpost {\nstruct Moon {};\n}\n",
        encoding="utf-8",
    )
    (engine_dir / "generator.cpp").write_text(
        '#include "engine/generator.hpp"\n\nnamespace moonpost {\nstd::string phase_of(const Moon&) { return Moon::missing_phase; }\n}\n',
        encoding="utf-8",
    )

    ok, detail = _check_cpp_compile(str(tmp_path))

    assert ok is False
    assert "fail syntax check" in detail
