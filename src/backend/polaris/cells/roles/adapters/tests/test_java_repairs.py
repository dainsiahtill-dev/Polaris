from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.deterministic_repairs.java_repairs import (
    run_all_java_post_repairs,
)


def test_java_post_repairs_add_common_accessor_aliases(tmp_path: Path) -> None:
    monster_path = tmp_path / "src/main/java/polaris/factory/domain/RhythmMonster.java"
    monster_path.parent.mkdir(parents=True)
    monster_path.write_text(
        "package polaris.factory.domain;\n"
        "public final class RhythmMonster {\n"
        "    private int temperament;\n"
        "    private int sleepyLevel;\n"
        "    public int getTemperament() {\n"
        "        return temperament;\n"
        "    }\n"
        "    public int getSleepyLevel() {\n"
        "        return sleepyLevel;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    pattern_path = tmp_path / "src/main/java/polaris/factory/domain/BeatPattern.java"
    pattern_path.write_text(
        "package polaris.factory.domain;\n"
        "public final class BeatPattern {\n"
        "    public static final int HIT = 1;\n"
        "    public static final int REST = 0;\n"
        "    public int length() {\n"
        "        return 4;\n"
        "    }\n"
        "    public int get(int index) {\n"
        "        return index == 0 ? HIT : REST;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    repairs = run_all_java_post_repairs(tmp_path)

    assert {item["file"] for item in repairs} == {
        "src/main/java/polaris/factory/domain/RhythmMonster.java",
        "src/main/java/polaris/factory/domain/BeatPattern.java",
    }
    monster = monster_path.read_text(encoding="utf-8")
    assert "public int temperament()" in monster
    assert "public int sleepyLevel()" in monster
    pattern = pattern_path.read_text(encoding="utf-8")
    assert "public boolean isHit(int index)" in pattern
    assert "public boolean isRest(int index)" in pattern
    assert "public int countRests()" in pattern
