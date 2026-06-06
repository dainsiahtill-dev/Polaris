"""Tests for the SWE-bench single-instance solver's localization fallback.

Focus: the lexical content-retrieval fallback (_content_ranked_candidates) that the solver
branches into when the symbol/PageRank ranker is degraded or low-confidence. This is the
feasible, offline substitute for dense-embedding retrieval (no embedding backend is
provisioned in this environment) and targets issues that name no symbol defined in the
target file.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def _load_solver() -> ModuleType:
    path = Path(__file__).resolve().parent / "polaris_solve_one.py"
    spec = importlib.util.spec_from_file_location("polaris_solve_one", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


class TestIsTestPath:
    def test_flags_test_dirs_and_files(self) -> None:
        solver = _load_solver()
        assert solver._is_test_path("tests/test_core.py") is True
        assert solver._is_test_path("pkg/tests/helpers.py") is True
        assert solver._is_test_path("pkg/test/helpers.py") is True
        assert solver._is_test_path("pkg/foo_test.py") is True
        assert solver._is_test_path("django/http/response.py") is False
        assert solver._is_test_path("pkg/contest.py") is False  # not a test


class TestContentRankedCandidates:
    def test_surfaces_content_match_and_filters_tests(self, tmp_path: Path) -> None:
        solver = _load_solver()
        repo = tmp_path / "repo"
        repo.mkdir()
        # core.py mentions the identifier in CONTENT (string/comment), not as a def name.
        (repo / "core.py").write_text(
            "def handler():\n    # references WidgetSerializer indirectly\n    return 'WidgetSerializer'\n",
            encoding="utf-8",
        )
        (repo / "other.py").write_text("def unrelated():\n    return 1\n", encoding="utf-8")
        tests_dir = repo / "tests"
        tests_dir.mkdir()
        # A test file mentions it MORE often — must still be filtered out.
        (tests_dir / "test_core.py").write_text(
            "WidgetSerializer = 1\nWidgetSerializer\nWidgetSerializer\nWidgetSerializer\n",
            encoding="utf-8",
        )
        _git(["init", "-q"], repo)
        _git(["add", "-A"], repo)
        _git(["commit", "-qm", "init"], repo)

        ranked = solver._content_ranked_candidates(str(repo), ["WidgetSerializer"])

        assert "core.py" in ranked
        assert ranked[0] == "core.py"  # source content match outranks the (filtered) test
        assert all(not solver._is_test_path(p) for p in ranked)
        assert "tests/test_core.py" not in ranked
        assert "other.py" not in ranked  # no co-occurrence -> not surfaced

    def test_no_idents_returns_empty(self, tmp_path: Path) -> None:
        solver = _load_solver()
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        _git(["init", "-q"], repo)
        _git(["add", "-A"], repo)
        _git(["commit", "-qm", "init"], repo)

        assert solver._content_ranked_candidates(str(repo), []) == []

    def test_non_git_workspace_degrades_to_empty(self, tmp_path: Path) -> None:
        """git grep fails outside a repo -> must degrade to [], never raise."""
        solver = _load_solver()
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "a.py").write_text("WidgetSerializer = 1\n", encoding="utf-8")
        assert solver._content_ranked_candidates(str(plain), ["WidgetSerializer"]) == []


class TestContextBudgetMaxTokens:
    """input + max_tokens must stay <= the model context window (local gemma 32768)."""

    def test_small_prompt_gets_full_cap(self) -> None:
        solver = _load_solver()
        assert solver._context_budget_max_tokens("hi") == solver.EDIT_MAX_OUT_CAP

    def test_huge_prompt_floors_at_512(self) -> None:
        solver = _load_solver()
        huge = "x" * 400_000  # ~100k est tokens -> budget goes negative -> floor 512
        assert solver._context_budget_max_tokens(huge) == 512

    def test_within_window_inputs_respect_ceiling(self) -> None:
        solver = _load_solver()
        # For inputs that fit the window, input + requested output must stay within ctx.
        for n in (0, 10_000, 60_000):  # est ~0 / 2.5k / 15k tokens, all < ctx
            prompt = "y" * n
            out = solver._context_budget_max_tokens(prompt)
            assert 512 <= out <= solver.EDIT_MAX_OUT_CAP
            assert solver._estimate_tokens(prompt) + out <= solver.MODEL_CTX_TOKENS

    def test_over_window_input_floors_output(self) -> None:
        solver = _load_solver()
        # Pathological over-window input floors output at 512 (the model rejects the input
        # itself; content is capped by MAX_CONTENT_CHARS in practice so this never fires).
        assert solver._context_budget_max_tokens("y" * 400_000) == 512


class TestDiagnoseBlocks:
    TARGET = "mod.py"
    CONTENT = "def foo():\n    x = 1\n    y = 2\n    return x + y\n"

    def _block(self, search: str, replace: str) -> str:
        return f"<<<< SEARCH:{self.TARGET}\n{search}\n====\n{replace}\n>>>> REPLACE\n"

    def test_truncated_missing_terminator(self) -> None:
        solver = _load_solver()
        draft = f"<<<< SEARCH:{self.TARGET}\n    x = 1\n    y = 2\n===="  # no >>>> REPLACE
        ok, reason = solver._diagnose_blocks(draft, self.TARGET, self.CONTENT)
        assert ok is False
        assert reason == "truncated"

    def test_clean_unique_block_ok(self) -> None:
        solver = _load_solver()
        draft = self._block("    x = 1\n    y = 2\n    return x + y", "    x = 1\n    y = 3\n    return x + y")
        ok, reason = solver._diagnose_blocks(draft, self.TARGET, self.CONTENT)
        assert ok is True, reason

    def test_ambiguous_anchor_rejected(self) -> None:
        solver = _load_solver()
        content = "def a():\n    z = go()\n    return z\n\ndef b():\n    z = go()\n    return z\n"
        draft = self._block("    z = go()\n    return z", "    z = go2()\n    return z")
        ok, reason = solver._diagnose_blocks(draft, self.TARGET, content)
        assert ok is False
        assert "ambiguous" in reason

    def test_noop_block_rejected(self) -> None:
        solver = _load_solver()
        draft = self._block("    x = 1\n    y = 2\n    return x + y", "    x = 1\n    y = 2\n    return x + y")
        ok, _reason = solver._diagnose_blocks(draft, self.TARGET, self.CONTENT)
        assert ok is False


class TestIndentTolerantReplace:
    def test_reindents_to_source_indentation(self) -> None:
        solver = _load_solver()
        content = "def f():\n        x = compute()\n        return x\n"  # 8-space body
        search = "    x = compute()\n    return x"  # 4-space (deviation)
        replace = "    x = compute2()\n    return x"
        new_content, ok = solver._indent_tolerant_replace(content, search, replace)
        assert ok is True
        assert "compute2()" in new_content
        assert "        x = compute2()" in new_content  # re-indented to 8 spaces

    def test_no_match_returns_false(self) -> None:
        solver = _load_solver()
        content = "def f():\n    return 1\n"
        new_content, ok = solver._indent_tolerant_replace(content, "    nope = 1", "    nope = 2")
        assert ok is False
        assert new_content == content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
