"""Official C++ quality must compile, honor any-case CMake lists, and run tests.

Live L2-15: syntax-only g++ hid cmake + tests/test_product.py. PM wrote
``cmakelists.txt``; Linux cmake requires exact ``CMakeLists.txt``. Do not
hand-edit generated projects.
"""

from __future__ import annotations

from pathlib import Path

from polaris.cells.factory.pipeline.internal.factory_workspace_quality import WorkspaceQualityRunner


def test_cpp_quality_commands_empty_without_sources_or_manifest(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("plain", encoding="utf-8")
    assert WorkspaceQualityRunner(tmp_path)._cpp_workspace_quality_commands() == []


def test_cpp_quality_commands_include_syntax_cmake_and_unittest(tmp_path: Path) -> None:
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "cmakelists.txt").write_text("cmake_minimum_required(VERSION 3.16)\n", encoding="utf-8")
    (tests / "test_product.py").write_text(
        "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self) -> None:\n        self.assertTrue(True)\n",
        encoding="utf-8",
    )

    commands = WorkspaceQualityRunner(tmp_path)._cpp_workspace_quality_commands()
    assert len(commands) == 3
    assert commands[0][0].endswith("python") or "python" in Path(commands[0][0]).name
    assert commands[0][1] == "-c"
    assert "fsyntax-only" in commands[0][2]
    assert "official CMakeLists.txt" in commands[1][2]
    assert commands[2][:4] == [commands[0][0], "-m", "unittest", "discover"]


def test_unclaimed_residual_targets_include_unittest_traceback_test_file(tmp_path: Path) -> None:
    """After cmake links, leftover must lease the failing unittest file.

    Live L2-15 remint-19: polaris_app linked, then unittest KeyError/assert
    pointed at ``tests/test_product.py``. That is not a g++ site.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        workspace_quality_unclaimed_failing_tu_targets,
        workspace_quality_unclaimed_residual_targets,
    )

    (tmp_path / "tests").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "tests" / "test_product.py").write_text("import unittest\n", encoding="utf-8")
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    blob = (
        "ERROR: test_more_robots_yield_larger_energy_vector "
        "(test_product.PatrolChessCliTests)\n"
        '  File "/tmp/ws/tests/test_product.py", line 161, in '
        "test_more_robots_yield_larger_energy_vector\n"
        '    self.assertEqual(len(small["energy"]), 2)\n'
        "KeyError: 'energy'\n"
    )
    leftover = workspace_quality_unclaimed_residual_targets(
        [blob],
        claimed_targets=["src/models/queue.cpp"],
        workspace=tmp_path,
    )
    seeded = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=[],
        workspace=tmp_path,
    )
    leftover_tests_only = workspace_quality_unclaimed_residual_targets(
        [blob],
        claimed_targets=["src/main.cpp"],
        workspace=tmp_path,
    )
    seeded_after_claimed_main = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["src/main.cpp"],
        workspace=tmp_path,
    )
    assert leftover == ["src/main.cpp"]
    assert seeded[0] == "src/main.cpp"
    assert "tests/test_product.py" in seeded
    # After two same-owner stagnations, residual leftover may fall through
    # to the unittest file. Immediate leftover_tus rotate must NOT — live
    # remint-21 R2/R4 leased tests/ while CLI still aborted in main.cpp.
    assert leftover_tests_only == ["tests/test_product.py"]
    assert seeded_after_claimed_main[0] == "src/main.cpp"


def test_failing_tu_stays_when_only_claimed_compile_tu_still_red(tmp_path: Path) -> None:
    """Claimed filter must not drop the only still-red ### TU.

    Live L2-15 remint-21 R3: g++ still failed ``src/main.cpp``
    (``Robot::Robot(int, Energy)``), claimed was that same path, leftover_tus
    then leased ``tests/test_product.py``. R4 equal_count_swap while compile
    was red.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        workspace_quality_unclaimed_failing_tu_targets,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "tests" / "test_product.py").write_text("import unittest\n", encoding="utf-8")
    residual = [
        "### FAILING_TUS src/main.cpp\n"
        "### src/main.cpp\n"
        "src/main.cpp:243:56: error: no matching function for call to "
        "'patrol_chess::models::Robot::Robot(int, patrol_chess::models::Energy)'\n"
        "======================================================================\n"
        "FAIL: test_default_invocation_produces_deterministic_report "
        "(test_product.PatrolChessCliTests)\n"
        '  File "/tmp/ws/tests/test_product.py", line 118, in '
        "test_default_invocation_produces_deterministic_report\n"
        "    self.assertEqual(result.returncode, 0, msg=result.stderr)\n"
    ]
    tus = workspace_quality_unclaimed_failing_tu_targets(
        residual,
        claimed_targets=["src/main.cpp"],
        workspace=tmp_path,
    )
    assert tus[0] == "src/main.cpp"
    assert "tests/test_product.py" not in tus[:1]


def test_failing_tu_rotates_runtime_ctor_throw_to_type_home(tmp_path: Path) -> None:
    """CLI abort ``Type::Type:`` is the type home after one claimed entrypoint.

    Live L2-15 remint-22: eight rounds stayed on ``src/main.cpp`` while
    unittest aborted ``Patrol::Patrol: requires at least 2 distinct nodes``.
    leftover_tus kept returning claimed ``src/main.cpp`` and reset the
    stagnation breaker.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        leftover_targets_should_force_owner_rotate,
        workspace_quality_unclaimed_failing_tu_targets,
    )

    models = tmp_path / "src" / "models"
    models.mkdir(parents=True)
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (models / "patrol.cpp").write_text("Patrol::Patrol(std::vector<Node> nodes) {}\n", encoding="utf-8")
    (models / "patrol.hpp").write_text("class Patrol {};\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_product.py").write_text("import unittest\n", encoding="utf-8")
    blob = (
        "AssertionError: -6 != 0 : terminate called after throwing an instance "
        "of 'std::invalid_argument'\n"
        "  what():  Patrol::Patrol: requires at least 2 distinct nodes\n"
        '  File "/tmp/ws/tests/test_product.py", line 118, in '
        "test_default_invocation_produces_deterministic_report\n"
    )
    seeded = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=[],
        workspace=tmp_path,
    )
    after_main = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["src/main.cpp"],
        workspace=tmp_path,
    )
    after_patrol = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["src/models/patrol.cpp"],
        workspace=tmp_path,
    )
    assert seeded[0] == "src/main.cpp"
    assert after_main[0] == "src/models/patrol.cpp"
    assert leftover_targets_should_force_owner_rotate(after_main, ["src/main.cpp"])
    assert after_patrol[0] in {"src/models/patrol.cpp", "src/models/patrol.hpp"}
    assert not leftover_targets_should_force_owner_rotate(after_patrol, ["src/models/patrol.cpp"])


def test_unclaimed_residual_targets_include_linker_undefined_reference_tus(tmp_path: Path) -> None:
    """Official cmake --build residuals must lease the .cpp that failed to link.

    Live L2-15 remint-18: syntax and CMakeLists.txt passed, then ld reported
    ``queue.cpp:(.text): undefined reference to Command::is_valid`` and
    ``generator.cpp:(.text): undefined reference to Generator::can_step``.
    Those are not ``path:line: error:`` sites, so leftover stayed empty.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        workspace_quality_unclaimed_residual_targets,
    )

    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine" / "generator.cpp").write_text("int g;\n", encoding="utf-8")
    (tmp_path / "src" / "models" / "queue.cpp").write_text("int q;\n", encoding="utf-8")
    leftover = workspace_quality_unclaimed_residual_targets(
        [
            "/usr/bin/ld: libpatrol_chess_core.a(queue.cpp.o): in function `Queue::push':\n"
            "queue.cpp:(.text+0x12dc): undefined reference to `patrol_chess::models::Command::is_valid() const'\n"
            "/usr/bin/ld: libpatrol_chess_core.a(generator.cpp.o): in function `Generator::step_internal':\n"
            "generator.cpp:(.text+0x8ca): undefined reference to `patrol_chess::engine::Generator::can_step(patrol_chess::models::Energy const&) const'\n"
        ],
        claimed_targets=["src/main.cpp"],
        workspace=tmp_path,
    )
    assert leftover == ["src/models/queue.cpp", "src/engine/generator.cpp"]


def test_unclaimed_residual_targets_skip_stagnant_engine_owner(tmp_path: Path) -> None:
    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        workspace_quality_unclaimed_residual_targets,
    )

    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "engine" / "generator.cpp").write_text("int g;\n", encoding="utf-8")
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "cmakelists.txt").write_text("cmake_minimum_required(VERSION 3.16)\n", encoding="utf-8")
    leftover = workspace_quality_unclaimed_residual_targets(
        [
            "### src/engine/generator.cpp\n"
            "src/engine/generator.cpp:86:18: error: class Robot has no member named energy\n"
            "### src/main.cpp\n"
            "src/models/energy.hpp:28:1: error: unused leftover header must not win\n"
            "src/models/queue.hpp:67:35: note: initializing argument 1 of Queue::push_back\n"
            "src/models/patrol.hpp:29:5: note: candidate: Patrol::Patrol(std::string, std::vector<Position>)\n"
            "src/main.cpp:196:42: error: no matching function for call to Patrol::Patrol\n"
            "cmakelists.txt:1:1: error: official CMakeLists.txt basename required (found cmakelists.txt)\n"
        ],
        claimed_targets=["src/engine/generator.cpp", "src/engine/generator.hpp"],
        workspace=tmp_path,
    )
    assert leftover[0] == "src/main.cpp"
    assert "CMakeLists.txt" in leftover
    assert "cmakelists.txt" in leftover
    assert "src/models/queue.hpp" not in leftover
    assert "src/models/patrol.hpp" not in leftover


def test_unclaimed_residual_targets_rotate_off_mutating_header_owner(tmp_path: Path) -> None:
    """Header progress must not hide remaining ### translation units.

    Live L2-15 remint-9 kept leasing queue.hpp/.cpp after a mutation because
    leftover only ran on no-op / two-stagnation. ### src/main.cpp stayed red.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        workspace_quality_unclaimed_failing_tu_targets,
        workspace_quality_unclaimed_residual_targets,
    )

    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "models" / "queue.hpp").write_text("class Queue {};\n", encoding="utf-8")
    (tmp_path / "src" / "models" / "queue.cpp").write_text("class Queue;\n", encoding="utf-8")
    (tmp_path / "src" / "engine" / "generator.cpp").write_text("int g;\n", encoding="utf-8")
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    residual = [
        "### src/engine/generator.cpp\n"
        "src/engine/generator.hpp:115:24: error: Queue has not been declared\n"
        "### src/main.cpp\n"
        "src/main.cpp:204:47: error: 'models' is not a member of '{anonymous}::patrol_chess'\n"
    ]
    leftover = workspace_quality_unclaimed_residual_targets(
        residual,
        claimed_targets=["src/models/queue.hpp", "src/models/queue.cpp"],
        workspace=tmp_path,
    )
    tus = workspace_quality_unclaimed_failing_tu_targets(
        residual,
        claimed_targets=["src/models/queue.hpp", "src/models/queue.cpp"],
        workspace=tmp_path,
    )
    assert leftover == ["src/engine/generator.cpp", "src/main.cpp"]
    assert tus == ["src/engine/generator.cpp", "src/main.cpp"]
    assert "src/models/queue.hpp" not in leftover


def test_unclaimed_failing_tus_read_index_before_truncated_body(tmp_path: Path) -> None:
    """Huge first-TU stderr must not hide later ### units.

    Live L2-15: unclosed namespace made generator.cpp emit 75 stdexcept
    errors. Trim kept only ``### src/engine/generator.cpp`` and leftover
    never saw ``### src/main.cpp``.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        workspace_quality_unclaimed_failing_tu_targets,
    )

    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "src" / "engine" / "generator.cpp").write_text("int g;\n", encoding="utf-8")
    tus = workspace_quality_unclaimed_failing_tu_targets(
        ["### FAILING_TUS src/engine/generator.cpp src/main.cpp\n### src/engine/generator.cpp\n" + ("x" * 4000) + "\n"],
        claimed_targets=["src/models/queue.cpp"],
        workspace=tmp_path,
    )
    assert tus == ["src/engine/generator.cpp", "src/main.cpp"]


def test_unclaimed_failing_tus_lease_unclosed_namespace_header_first(tmp_path: Path) -> None:
    """X::std pollution is an unclosed header, not a use-site TU defect.

    Live L2-15 remint-10 rotated generator.cpp <-> main.cpp for 5 stagnant
    rounds while queue.hpp still had ``namespace A { namespace B {`` and
    one closer. Every TU then compiled as A::std.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        workspace_quality_unclaimed_failing_tu_targets,
        workspace_quality_unclosed_namespace_headers,
    )

    models = tmp_path / "src" / "models"
    engine = tmp_path / "src" / "engine"
    models.mkdir(parents=True)
    engine.mkdir(parents=True)
    (models / "queue.hpp").write_text(
        "#ifndef Q\n#define Q\n#include <string>\n"
        "namespace patrol_chess {\nnamespace models {\n"
        "class Queue { public: int size() const; };\n"
        "}  // namespace patrol_chess::models\n"
        "#endif\n",
        encoding="utf-8",
    )
    (engine / "generator.cpp").write_text("int g;\n", encoding="utf-8")
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    assert workspace_quality_unclosed_namespace_headers(tmp_path) == ["src/models/queue.hpp"]
    tus = workspace_quality_unclaimed_failing_tu_targets(
        [
            "### FAILING_TUS src/engine/generator.cpp src/main.cpp\n"
            "note:   template<class _Tp> constexpr const _Tp& "
            "patrol_chess::std::min’ conflicts with\n"
        ],
        claimed_targets=["src/engine/generator.cpp"],
        workspace=tmp_path,
    )
    assert tus[0] == "src/models/queue.hpp"
    assert "src/main.cpp" in tus


def test_cpp_syntax_script_emits_failing_tu_index_first(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    commands = WorkspaceQualityRunner(tmp_path)._cpp_workspace_quality_commands()
    script = commands[0][2]
    assert "### FAILING_TUS" in script
    assert "failed_paths" in script


def test_cpp_quality_commands_detect_lowercase_cmake_lists(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "cmakelists.txt").write_text("cmake_minimum_required(VERSION 3.16)\n", encoding="utf-8")
    runner = WorkspaceQualityRunner(tmp_path)
    names = [path.name for path in runner._cpp_manifest_candidates()]
    assert names == ["cmakelists.txt"]
    assert any(
        "CMakeLists.txt" in (command[2] if len(command) > 2 else "")
        for command in runner._cpp_workspace_quality_commands()
    )
