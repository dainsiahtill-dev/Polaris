#!/usr/bin/env python3
"""Unit tests for the V12 RepoIntelligence localization helpers in arch_b_converge.

Covers the new empty-traceback-frame localization path (assertion-failure tests where
``implicated_files`` is empty): test-file resolution, AST test-symbol extraction, the
across-round hypothesis cascade, and graceful degradation of candidate ranking. The full
measured A/B (official Docker harness) is a separate verification step — see the blueprint
SWEBENCH_V12_LOCALIZATION_CONVERGENCE_BLUEPRINT_20260607.md.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import arch_b_converge as abc

# ── _resolve_test_file ──────────────────────────────────────────────────────────────────


def test_resolve_path_style_node_id() -> None:
    repo = {"tests/test_widget.py", "mypkg/widget.py"}
    assert abc._resolve_test_file("tests/test_widget.py::WidgetTests::test_make", repo) == "tests/test_widget.py"


def test_resolve_dotted_module_node_id() -> None:
    repo = {"tests/foo/test_bar.py", "src/bar.py"}
    assert abc._resolve_test_file("tests.foo.test_bar::test_x", repo) == "tests/foo/test_bar.py"


def test_resolve_testbed_prefix_stripped() -> None:
    repo = {"tests/test_widget.py"}
    assert abc._resolve_test_file("/testbed/tests/test_widget.py::test_make", repo) == "tests/test_widget.py"


def test_resolve_suffix_match() -> None:
    repo = {"src/pkg/tests/test_x.py"}
    assert abc._resolve_test_file("pkg/tests/test_x.py::test_x", repo) == "src/pkg/tests/test_x.py"


def test_resolve_unknown_returns_empty() -> None:
    assert abc._resolve_test_file("tests/nope.py::test_x", {"tests/test_widget.py"}) == ""


def test_resolve_django_unittest_node_id() -> None:
    # django FAIL_TO_PASS dialect: "method (dotted.module.ClassName)" — no "::".
    repo = {"tests/model_forms/tests.py", "django/forms/models.py"}
    node = "test_limit_choices_to_no_duplicates (model_forms.tests.LimitChoicesToTests)"
    assert abc._resolve_test_file(node, repo) == "tests/model_forms/tests.py"


# ── _split_test_node ────────────────────────────────────────────────────────────────────


def test_split_test_node_pytest_path() -> None:
    assert abc._split_test_node("tests/test_x.py::Cls::test_m") == ("tests/test_x.py", "test_m")


def test_split_test_node_pytest_dotted() -> None:
    assert abc._split_test_node("pkg.mod::test_m") == ("pkg.mod", "test_m")


def test_split_test_node_django_unittest() -> None:
    assert abc._split_test_node("test_m (pkg.mod.tests.ClassName)") == ("pkg.mod.tests", "test_m")


# ── _test_func_nodes ────────────────────────────────────────────────────────────────────


def test_func_nodes_finds_method_and_strips_param() -> None:
    tree = ast.parse("class T:\n    def test_make(self):\n        pass\n    def test_other(self):\n        pass\n")
    nodes = abc._test_func_nodes(tree, "test_make[case-1]")
    assert len(nodes) == 1
    assert isinstance(nodes[0], ast.FunctionDef)
    assert nodes[0].name == "test_make"


def test_func_nodes_missing_returns_empty() -> None:
    tree = ast.parse("def test_a():\n    pass\n")
    assert abc._test_func_nodes(tree, "test_b") == []


# ── _extract_test_symbols ───────────────────────────────────────────────────────────────


def test_extract_test_symbols_surfaces_subject_drops_framework_noise(tmp_path: Path) -> None:
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_widget.py").write_text(
        "import unittest\n"
        "from mypkg.widget import WidgetFactory\n\n"
        "class WidgetTests(unittest.TestCase):\n"
        "    def test_make(self):\n"
        "        factory = WidgetFactory()\n"
        "        widget = factory.build_widget(name='x')\n"
        "        self.assertEqual(widget.render_label(), 'x')\n",
        encoding="utf-8",
    )
    syms = abc._extract_test_symbols(
        tmp_path,
        ["tests/test_widget.py::WidgetTests::test_make"],
        {"tests/test_widget.py"},
    )
    # the code under test is surfaced...
    assert "WidgetFactory" in syms
    assert "build_widget" in syms
    assert "render_label" in syms
    # ...while test-framework noise is filtered out.
    assert "assertEqual" not in syms
    assert "self" not in syms


def test_extract_test_symbols_missing_file_degrades(tmp_path: Path) -> None:
    # node id resolves to nothing on disk -> empty, never raises.
    assert abc._extract_test_symbols(tmp_path, ["tests/gone.py::test_x"], {"tests/gone.py"}) == []


# ── _next_hypothesis (across-round cascade) ─────────────────────────────────────────────


def test_next_hypothesis_skips_tried() -> None:
    assert abc._next_hypothesis(["a.py", "b.py", "c.py"], ["a.py"]) == "b.py"


def test_next_hypothesis_exhausted_returns_empty() -> None:
    assert abc._next_hypothesis(["a.py", "b.py"], ["a.py", "b.py"]) == ""


def test_next_hypothesis_empty_candidates() -> None:
    assert abc._next_hypothesis([], []) == ""


# ── _candidate_files graceful degradation ───────────────────────────────────────────────


def test_candidate_files_degrades_to_empty_off_repo(tmp_path: Path) -> None:
    # An empty, non-git directory: RepoIntelligence finds nothing and git grep fails;
    # localization must degrade to [] without raising (caller then uses ce_localize).
    assert abc._candidate_files("some bug about Widgets", [], set(), tmp_path) == []
