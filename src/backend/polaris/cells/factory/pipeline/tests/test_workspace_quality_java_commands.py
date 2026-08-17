"""Official Java quality must compile .java and leftover depth onto prod sources.

Live L2-16: catalog language=java, six .java files, no pom. Quality fell
through to Python compileall + unittest. After tests went green the only
residual was ``delivery_depth_contract_failed`` (prod_files=5 < 6,
prod_lines=477 < 500). leftover leased tests/ then
``workspace_quality_repair_canonical_owner_missing``. Do not hand-edit
generated projects.
"""

from __future__ import annotations

from pathlib import Path

from polaris.cells.factory.pipeline.internal.factory_workspace_quality import WorkspaceQualityRunner


def test_java_quality_commands_empty_without_sources(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("plain", encoding="utf-8")
    assert WorkspaceQualityRunner(tmp_path)._java_workspace_quality_commands() == []


def test_java_quality_commands_include_javac_and_unittest(tmp_path: Path) -> None:
    src = tmp_path / "src" / "main" / "java" / "polaris" / "factory"
    tests = tmp_path / "tests"
    src.mkdir(parents=True)
    tests.mkdir()
    (src / "main.java").write_text("class Main { public static void main(String[] args) {} }\n", encoding="utf-8")
    (tests / "test_product.py").write_text(
        "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self) -> None:\n        self.assertTrue(True)\n",
        encoding="utf-8",
    )

    runner = WorkspaceQualityRunner(tmp_path)
    commands = runner._java_workspace_quality_commands()
    assert len(commands) == 2
    assert commands[0][0].endswith("python") or "python" in Path(commands[0][0]).name
    assert commands[0][1] == "-c"
    assert "javac" in commands[0][2]
    assert "### FAILING_TUS" in commands[0][2]
    assert commands[1][:4] == [commands[0][0], "-m", "unittest", "discover"]
    all_commands = runner.workspace_quality_commands({})
    assert any("javac" in (command[2] if len(command) > 2 else "") for command in all_commands)
    assert not any(command[:3] == [commands[0][0], "-m", "compileall"] for command in all_commands)
    assert '"test"' in commands[0][2] or "'test'" in commands[0][2]


def test_java_public_class_filename_leftover_stays_on_prod_sources(tmp_path: Path) -> None:
    """javac public-class residuals must not rotate onto src/test after prod claim.

    Live L2-16 remint-1: FAILING_TUS listed plantenginetest.java after the
    five prod files were claimed, so R2/R4/R6 leased TASK-1-tests.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        leftover_targets_should_force_owner_rotate,
        workspace_quality_unclaimed_failing_tu_targets,
    )

    prod = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "domain"
    test_java = tmp_path / "src" / "test" / "java" / "polaris" / "factory"
    prod.mkdir(parents=True)
    test_java.mkdir(parents=True)
    (prod / "melodymodel.java").write_text("public final class Melody {}\n", encoding="utf-8")
    (test_java / "plantenginetest.java").write_text("public class PlantEngineTest {}\n", encoding="utf-8")
    blob = (
        "### FAILING_TUS src/main/java/polaris/factory/domain/melodymodel.java "
        "src/test/java/polaris/factory/plantenginetest.java\n"
        "src/main/java/polaris/factory/domain/melodymodel.java:17: error: "
        "class Melody is public, should be declared in a file named Melody.java\n"
        "src/test/java/polaris/factory/plantenginetest.java:54: error: "
        "class PlantEngineTest is public, should be declared in a file named "
        "PlantEngineTest.java\n"
    )
    after_prod = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["src/main/java/polaris/factory/domain/melodymodel.java"],
        workspace=tmp_path,
    )
    official = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=[],
        workspace=tmp_path,
    )
    assert official[0] == "src/main/java/polaris/factory/domain/Melody.java"
    assert after_prod[0] == "src/main/java/polaris/factory/domain/Melody.java"
    assert leftover_targets_should_force_owner_rotate(
        after_prod,
        ["src/main/java/polaris/factory/domain/melodymodel.java"],
    )


def test_java_leftover_drops_lowercase_when_official_sibling_exists(tmp_path: Path) -> None:
    engine = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "engine"
    engine.mkdir(parents=True)
    (engine / "plantengine.java").write_text("public final class PlantEngine {}\n", encoding="utf-8")
    (engine / "PlantEngine.java").write_text("public final class PlantEngine {}\n", encoding="utf-8")
    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        workspace_quality_unclaimed_failing_tu_targets,
    )

    tus = workspace_quality_unclaimed_failing_tu_targets(
        [
            "### FAILING_TUS src/main/java/polaris/factory/engine/plantengine.java "
            "src/main/java/polaris/factory/engine/PlantEngine.java\n"
            "src/main/java/polaris/factory/engine/plantengine.java:36: error: "
            "class PlantEngine is public, should be declared in a file named PlantEngine.java\n"
        ],
        claimed_targets=[],
        workspace=tmp_path,
    )
    assert "src/main/java/polaris/factory/engine/PlantEngine.java" in tus
    assert "src/main/java/polaris/factory/engine/plantengine.java" not in tus


def test_java_missing_package_symbol_leftover_official_type_file(tmp_path: Path) -> None:
    """``cannot find symbol class Plant`` in package P must lease P/Plant.java.

    Live L2-16 remint-5 wrote Melody.java then stagnated on PlantEngine
    ``cannot find symbol class Plant/Season`` without leftover naming
    Plant.java / Season.java.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        leftover_targets_should_force_owner_rotate,
        workspace_quality_unclaimed_failing_tu_targets,
    )

    domain = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "domain"
    engine = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "engine"
    domain.mkdir(parents=True)
    engine.mkdir(parents=True)
    (domain / "plantmodel.java").write_text("class plantmodel {}\n", encoding="utf-8")
    (domain / "seasonmodel.java").write_text("class seasonmodel {}\n", encoding="utf-8")
    (engine / "PlantEngine.java").write_text("class PlantEngine {}\n", encoding="utf-8")
    blob = (
        "### FAILING_TUS src/main/java/polaris/factory/engine/PlantEngine.java\n"
        "src/main/java/polaris/factory/engine/PlantEngine.java:5: error: cannot find symbol\n"
        "import polaris.factory.domain.Plant;\n"
        "                             ^\n"
        "  symbol:   class Plant\n"
        "  location: package polaris.factory.domain\n"
        "src/main/java/polaris/factory/engine/PlantEngine.java:6: error: cannot find symbol\n"
        "  symbol:   class Season\n"
        "  location: package polaris.factory.domain\n"
    )
    tus = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["src/main/java/polaris/factory/engine/PlantEngine.java"],
        workspace=tmp_path,
    )
    assert tus[0] == "src/main/java/polaris/factory/domain/Plant.java"
    assert "src/main/java/polaris/factory/domain/Season.java" in tus
    assert leftover_targets_should_force_owner_rotate(
        tus,
        ["src/main/java/polaris/factory/engine/PlantEngine.java"],
    )


def test_delivery_depth_shortfall_leftover_leases_prod_java_not_tests(tmp_path: Path) -> None:
    """Depth-only residual must rotate onto production sources.

    Live L2-16 remint-0: leftover leased plantenginetest.java / test_product.py
    for six of eight rounds while prod_lines stayed under 500.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        leftover_targets_should_force_owner_rotate,
        workspace_quality_unclaimed_failing_tu_targets,
    )

    prod = tmp_path / "src" / "main" / "java" / "polaris" / "factory"
    prod.mkdir(parents=True)
    (tmp_path / "src" / "test" / "java" / "polaris" / "factory").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (prod / "main.java").write_text("class Main {}\n", encoding="utf-8")
    (prod / "plantmodel.java").write_text("class Plant {}\n", encoding="utf-8")
    (tmp_path / "src" / "test" / "java" / "polaris" / "factory" / "plantenginetest.java").write_text(
        "class T {}\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_product.py").write_text("import unittest\n", encoding="utf-8")
    blob = (
        "delivery_depth_contract_failed: implementation depth metrics: "
        "prod_files=5, prod_lines=477, test_files=2, test_assertions=16, "
        "minimums={'min_prod_files': 6, 'min_prod_lines': 500}; "
        "failures: production_source_lines=477 < 500\n"
        '  File "/tmp/ws/tests/test_product.py", line 40, in test_cli_help\n'
    )
    seeded = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=[],
        workspace=tmp_path,
    )
    after_tests = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["src/test/java/polaris/factory/plantenginetest.java", "tests/test_product.py"],
        workspace=tmp_path,
    )
    assert seeded[0].startswith("src/main/java/")
    assert "tests/test_product.py" not in seeded[:2]
    assert after_tests[0].startswith("src/main/java/")
    assert leftover_targets_should_force_owner_rotate(
        after_tests,
        ["src/test/java/polaris/factory/plantenginetest.java", "tests/test_product.py"],
    )


def test_java_failing_tus_leftover_stays_on_compile_use_sites(tmp_path: Path) -> None:
    """Existing compile TUs beat unittest staging / already-written types.

    Live L2-16 remint-9: official javac listed PlantEngine.java + main.java
    (cannot find ``Note``). Unittest staging also named MelodyModel Plant/
    Season. leftover must not prepend those existing domain files.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        leftover_targets_should_force_owner_rotate,
        workspace_quality_unclaimed_failing_tu_targets,
    )

    domain = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "domain"
    engine = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "engine"
    factory_root = tmp_path / "src" / "main" / "java" / "polaris" / "factory"
    domain.mkdir(parents=True)
    engine.mkdir(parents=True)
    (domain / "Plant.java").write_text("public final class Plant {}\n", encoding="utf-8")
    (domain / "Season.java").write_text("public final class Season {}\n", encoding="utf-8")
    (domain / "Melody.java").write_text(
        "public final class Melody { public static final class Note {}\n}\n",
        encoding="utf-8",
    )
    (domain / "MelodyModel.java").write_text("public final class MelodyModel {}\n", encoding="utf-8")
    (engine / "PlantEngine.java").write_text("public final class PlantEngine {}\n", encoding="utf-8")
    (factory_root / "main.java").write_text("class Main {}\n", encoding="utf-8")
    blob = (
        "### FAILING_TUS src/main/java/polaris/factory/engine/PlantEngine.java "
        "src/main/java/polaris/factory/main.java\n"
        "src/main/java/polaris/factory/engine/PlantEngine.java:85: error: cannot find symbol\n"
        "        List<Note> notes = buildNotes(plant, season, normalizedGrowth);\n"
        "             ^\n"
        "  symbol:   class Note\n"
        "  location: class PlantEngine\n"
        "src/main/java/polaris/factory/main.java:111: error: cannot find symbol\n"
        "        MelodyModel melody;\n"
        "        ^\n"
        "  symbol:   class MelodyModel\n"
        "  location: class Main\n"
        "AssertionError: 1 != 0 : javac failed:\n"
        f"stderr={tmp_path}/build/staging/polaris/factory/domain/MelodyModel.java:88: "
        "error: cannot find symbol\n"
        "    private final Plant plant;\n"
        "  symbol:   class Plant\n"
        "  location: class MelodyModel\n"
    )
    tus = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=[],
        workspace=tmp_path,
    )
    assert tus[:2] == [
        "src/main/java/polaris/factory/engine/PlantEngine.java",
        "src/main/java/polaris/factory/main.java",
    ]
    assert "src/main/java/polaris/factory/domain/Plant.java" not in tus
    assert not leftover_targets_should_force_owner_rotate(
        tus,
        [
            "src/main/java/polaris/factory/engine/PlantEngine.java",
            "src/main/java/polaris/factory/main.java",
        ],
    )


def test_java_unittest_staging_leftover_stays_on_test_helper_when_official_green(
    tmp_path: Path,
) -> None:
    """Official javac green + unittest staging red stays on tests/test_product.py.

    Live L2-16 remint-24: official javac passed 9 files. Staging javac of
    PlantEngine.java failed. leftover leased PlantEngine and R8 deferred
    to TASK-1-source-modules.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        leftover_targets_should_force_owner_rotate,
        workspace_quality_unclaimed_failing_tu_targets,
    )

    engine = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "engine"
    engine.mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (engine / "PlantEngine.java").write_text("public final class PlantEngine {}\n", encoding="utf-8")
    (tmp_path / "tests" / "test_product.py").write_text("import unittest\n", encoding="utf-8")
    blob = (
        "FAIL: test_cli_help_exits_zero (test_product.CliInvocationTest.test_cli_help_exits_zero)\n"
        f'  File "{tmp_path}/tests/test_product.py", line 287, in test_cli_help_exits_zero\n'
        "    out_dir = self._compile_main()\n"
        "AssertionError: 1 != 0 : javac failed:\n"
        f"stderr={tmp_path}/build/staging/engine/PlantEngine.java:201: error: cannot find symbol\n"
        "        return Melody.of(notes, tempoBpm, key);\n"
        "  symbol:   variable Melody\n"
        "  location: class PlantEngine\n"
    )
    tus = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["tests/test_product.py"],
        workspace=tmp_path,
    )
    assert tus[:1] == ["tests/test_product.py"]
    assert "src/main/java/polaris/factory/engine/PlantEngine.java" not in tus
    assert not leftover_targets_should_force_owner_rotate(tus, ["tests/test_product.py"])
    after_claim = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["tests/test_product.py"],
        workspace=tmp_path,
    )
    assert after_claim[:1] == ["tests/test_product.py"]


def test_java_nested_type_does_not_invent_toplevel_note_file(tmp_path: Path) -> None:
    """Missing ``class Note`` in package P is Melody.Note, not Note.java.

    Live L2-16 remint-11: leftover prepended domain/Note.java, official
    write_file invented a top-level Note, then PlantEngine oscillated
    between Melody.Note and MelodyModel.Note.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        leftover_targets_should_force_owner_rotate,
        workspace_quality_unclaimed_failing_tu_targets,
    )

    domain = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "domain"
    engine = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "engine"
    domain.mkdir(parents=True)
    engine.mkdir(parents=True)
    (domain / "Melody.java").write_text(
        "package polaris.factory.domain;\n"
        "public final class Melody { public static final class Note { public Note(String p, int d) {} } }\n",
        encoding="utf-8",
    )
    (engine / "PlantEngine.java").write_text(
        "package polaris.factory.engine;\npublic final class PlantEngine {}\n",
        encoding="utf-8",
    )
    blob = (
        "### FAILING_TUS src/main/java/polaris/factory/engine/PlantEngine.java\n"
        "src/main/java/polaris/factory/engine/PlantEngine.java:85: error: cannot find symbol\n"
        "        List<Note> notes = buildNotes();\n"
        "             ^\n"
        "  symbol:   class Note\n"
        "  location: class PlantEngine\n"
        "  symbol:   class Note\n"
        "  location: package polaris.factory.domain\n"
    )
    tus = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=[],
        workspace=tmp_path,
    )
    assert tus[0] == "src/main/java/polaris/factory/engine/PlantEngine.java"
    assert "src/main/java/polaris/factory/domain/Note.java" not in tus
    assert not leftover_targets_should_force_owner_rotate(
        tus,
        ["src/main/java/polaris/factory/engine/PlantEngine.java"],
    )


def test_java_compile_tus_ignore_unrelated_public_type_basename(tmp_path: Path) -> None:
    """Still-red FAILING_TUS must not rotate onto an unrelated official file.

    Live L2-16 remint-12 R6: leftover prepended missing PlantModel.java from
    a public-type residual on plantmodel.java while PlantEngine.java was
    still the compile TU.
    """

    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        leftover_targets_should_force_owner_rotate,
        workspace_quality_unclaimed_failing_tu_targets,
    )

    domain = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "domain"
    engine = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "engine"
    domain.mkdir(parents=True)
    engine.mkdir(parents=True)
    (domain / "plantmodel.java").write_text("public final class PlantModel {}\n", encoding="utf-8")
    (engine / "PlantEngine.java").write_text("public final class PlantEngine {}\n", encoding="utf-8")
    blob = (
        "### FAILING_TUS src/main/java/polaris/factory/engine/PlantEngine.java\n"
        "src/main/java/polaris/factory/engine/PlantEngine.java:68: error: cannot find symbol\n"
        "    public Melody compose(Plant plant, Season season) {\n"
        "           ^\n"
        "  symbol:   class Melody\n"
        "  location: class PlantEngine\n"
        "src/main/java/polaris/factory/domain/plantmodel.java:1: error: "
        "class PlantModel is public, should be declared in a file named PlantModel.java\n"
    )
    tus = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=["src/main/java/polaris/factory/engine/PlantEngine.java"],
        workspace=tmp_path,
    )
    assert tus[0] == "src/main/java/polaris/factory/engine/PlantEngine.java"
    assert "src/main/java/polaris/factory/domain/PlantModel.java" not in tus
    assert not leftover_targets_should_force_owner_rotate(
        tus,
        ["src/main/java/polaris/factory/engine/PlantEngine.java"],
    )
