"""Generic syntax gate (I3-r18): broken code must be CAUGHT, not shipped.

Regression anchor: r18 shipped main.js with `alive: true;` (a ';' inside an
object literal) because the grep-based verify never ran node --check.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from polaris.kernelone.quality.syntax_gate import (
    check_file_syntax,
    first_syntax_failure,
    syntax_checker_for,
)

_HAS_NODE = shutil.which("node") is not None
_HAS_TSC = shutil.which("tsc") is not None

_BROKEN_JS = """\
const bricks = [];
bricks.push({
    x: 1,
    alive: true;
});
"""

_VALID_JS = """\
const bricks = [];
bricks.push({ x: 1, alive: true });
"""

_BROKEN_TS = """\
interface FlowerState {
  wilted: boolean;
}

const state: FlowerState = {
  wilted: false;
};
"""

_VALID_TS = """\
interface FlowerState {
  wilted: boolean;
}

const state: FlowerState = {
  wilted: false,
};
"""

_VALID_TS_ES2020 = """\
interface Plant {
  id: string;
  humidity: number;
}

const plants: Plant[] = [{ id: "moon-flower", humidity: 0.8 }];
const index = plants.findIndex((plant) => plant.id === "moon-flower");
const plant = plants.find((item) => item.humidity > 0.5);
console.log(index, plant?.id);
"""


class TestExtensionMapping:
    def test_known_extensions(self) -> None:
        ts_checker = [
            "tsc",
            "--noEmit",
            "--pretty",
            "false",
            "--skipLibCheck",
            "--target",
            "ES2020",
            "--module",
            "commonjs",
            "--lib",
            "ES2020,DOM",
        ]
        assert syntax_checker_for("main.js") == ["node", "--check"]
        assert syntax_checker_for("a.mjs") == ["node", "--check"]
        assert syntax_checker_for("flower.ts") == ts_checker
        assert syntax_checker_for("garden.tsx") == ts_checker
        assert syntax_checker_for("mod.py")[1:] == ["-m", "py_compile"]

    def test_unknown_extension_has_no_checker(self) -> None:
        assert syntax_checker_for("readme.md") is None
        assert syntax_checker_for("style.css") is None


class TestPythonSyntax:
    """py_compile uses sys.executable, so these always run."""

    def test_valid_python_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "ok.py"
        f.write_text("x = 1\n", encoding="utf-8")
        result = check_file_syntax(str(f))
        assert result.checked is True
        assert result.ok is True

    def test_broken_python_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.py"
        f.write_text("def f(:\n    pass\n", encoding="utf-8")
        result = check_file_syntax(str(f))
        assert result.checked is True
        assert result.ok is False
        assert result.error  # a non-empty, quotable message


class TestUncheckableDegradesToPass:
    def test_unknown_extension_not_checked_not_blocked(self, tmp_path: Path) -> None:
        f = tmp_path / "notes.txt"
        f.write_text("anything", encoding="utf-8")
        result = check_file_syntax(str(f))
        assert result.checked is False
        assert result.ok is True  # gate must not block on "could not check"

    def test_missing_file_not_checked_not_blocked(self, tmp_path: Path) -> None:
        result = check_file_syntax(str(tmp_path / "missing.py"))
        assert result.checked is False
        assert result.ok is True


@pytest.mark.skipif(not _HAS_NODE, reason="node not available")
class TestJavascriptSyntax:
    def test_valid_js_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "main.js"
        f.write_text(_VALID_JS, encoding="utf-8")
        result = check_file_syntax(str(f))
        assert result.checked is True
        assert result.ok is True

    def test_broken_js_object_literal_semicolon_fails(self, tmp_path: Path) -> None:
        # The exact r18 failure shape.
        f = tmp_path / "main.js"
        f.write_text(_BROKEN_JS, encoding="utf-8")
        result = check_file_syntax(str(f))
        assert result.checked is True
        assert result.ok is False
        assert "SyntaxError" in result.error or "Unexpected" in result.error

    def test_first_syntax_failure_finds_broken_file(self, tmp_path: Path) -> None:
        (tmp_path / "good.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "main.js").write_text(_BROKEN_JS, encoding="utf-8")
        (tmp_path / "readme.md").write_text("# docs", encoding="utf-8")
        failure = first_syntax_failure(str(tmp_path), ["good.py", "main.js", "readme.md"])
        assert failure is not None
        assert failure.path.endswith("main.js")
        assert failure.ok is False


@pytest.mark.skipif(not _HAS_TSC, reason="tsc not available")
class TestTypescriptSyntax:
    def test_valid_ts_passes(self, tmp_path: Path) -> None:
        f = tmp_path / "flower.ts"
        f.write_text(_VALID_TS, encoding="utf-8")
        result = check_file_syntax(str(f))
        assert result.checked is True
        assert result.ok is True

    def test_es2020_array_helpers_are_not_syntax_failures(self, tmp_path: Path) -> None:
        f = tmp_path / "garden.ts"
        f.write_text(_VALID_TS_ES2020, encoding="utf-8")
        result = check_file_syntax(str(f))
        assert result.checked is True
        assert result.ok is True

    def test_broken_ts_object_literal_semicolon_fails(self, tmp_path: Path) -> None:
        f = tmp_path / "flower.ts"
        f.write_text(_BROKEN_TS, encoding="utf-8")
        result = check_file_syntax(str(f))
        assert result.checked is True
        assert result.ok is False
        assert "TS1005" in result.error or "',' expected" in result.error


def test_first_syntax_failure_returns_none_when_all_parse(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "readme.md").write_text("# docs", encoding="utf-8")
    assert first_syntax_failure(str(tmp_path), ["good.py", "readme.md"]) is None
