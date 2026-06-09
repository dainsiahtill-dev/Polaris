"""Tests for the pure-Python repo_rg fallback (used when ripgrep is absent).

Regression guard: when the ``rg`` binary is unavailable, ``repo_rg`` must still
return REAL matches rather than an empty ``success=true`` payload that misleads
the agent into believing a symbol does not exist.
"""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.llm.toolkit.executor.handlers.search import (
    _fallback_pattern_matches,
    _python_search_fallback,
)


def _seed_repo(root: Path) -> None:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "response.py").write_text(
        "class HttpResponseBase:\n    pass\n\n\nclass HttpResponse(HttpResponseBase):\n    pass\n",
        encoding="utf-8",
    )
    (root / "pkg" / "other.py").write_text("x = 1\n", encoding="utf-8")
    (root / "README.md").write_text("class HttpResponse docs\n", encoding="utf-8")
    # Noise that must be skipped.
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("class HttpResponse\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.py").write_text("class HttpResponse\n", encoding="utf-8")


def test_fallback_finds_real_matches(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    payload = _python_search_fallback(
        workspace=tmp_path,
        query="class HttpResponse",
        file_patterns=["*.py"],
        safe_max_results=50,
        case_sensitive=False,
        search_path=".",
    )
    result = payload["result"]
    assert result["backend"] == "python_fallback"
    assert result["returned_count"] >= 2
    files = {hit["file"] for hit in result["results"]}
    assert "pkg/response.py" in files
    # *.py filter excludes the markdown hit.
    assert all(f.endswith(".py") for f in files)
    # Skip-dirs must be honored.
    assert not any(".git" in f or "__pycache__" in f for f in files)


def test_fallback_line_numbers_are_one_based(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    payload = _python_search_fallback(
        workspace=tmp_path,
        query="class HttpResponse",
        file_patterns=["*.py"],
        safe_max_results=50,
        case_sensitive=False,
        search_path=".",
    )
    hits = {(h["file"], h["line"]) for h in payload["result"]["results"]}
    # HttpResponseBase is on line 1, HttpResponse on line 5 of response.py.
    assert ("pkg/response.py", 1) in hits
    assert ("pkg/response.py", 5) in hits


def test_fallback_case_sensitive(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    payload = _python_search_fallback(
        workspace=tmp_path,
        query="httpresponse",
        file_patterns=["*.py"],
        safe_max_results=50,
        case_sensitive=True,
        search_path=".",
    )
    assert payload["result"]["returned_count"] == 0


def test_fallback_invalid_regex_treated_as_literal(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("value = compute(a\n", encoding="utf-8")
    payload = _python_search_fallback(
        workspace=tmp_path,
        query="compute(a",  # unbalanced paren -> invalid regex -> escaped to literal
        file_patterns=["*.py"],
        safe_max_results=10,
        case_sensitive=False,
        search_path=".",
    )
    assert payload["result"]["returned_count"] >= 1


def test_fallback_pattern_matches_basename_and_path() -> None:
    # No-slash patterns match the basename (so *.py works regardless of depth).
    assert _fallback_pattern_matches("a/b/c.py", ["*.py"]) is True
    assert _fallback_pattern_matches("a/b/c.txt", ["*.py"]) is False
    # Slash patterns match the relative path (fnmatch '*' spans '/').
    assert _fallback_pattern_matches("django/http/response.py", ["django/*"]) is True
    assert _fallback_pattern_matches("tests/http/response.py", ["django/*"]) is False
    # No patterns => match everything.
    assert _fallback_pattern_matches("response.py", None) is True
