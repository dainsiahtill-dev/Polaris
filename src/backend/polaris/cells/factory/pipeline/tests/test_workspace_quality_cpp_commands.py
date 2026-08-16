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
