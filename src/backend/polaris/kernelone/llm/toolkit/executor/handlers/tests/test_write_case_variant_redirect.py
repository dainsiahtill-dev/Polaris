"""RC-B (2026-06-16): write_file case-variant duplicate redirect.

Weak models conflate file casing and write ``App.jsx`` while ``app.jsx`` already
exists, forking a split-brain pair on case-sensitive Linux. ``_resolve_case_variant_rel``
detects the existing same-directory case-only sibling so the write handler can
redirect onto it instead of creating a duplicate.
"""

from __future__ import annotations

import os

from polaris.kernelone.llm.toolkit.executor.handlers.filesystem import _resolve_case_variant_rel


class TestResolveCaseVariantRel:
    def test_redirects_to_existing_case_variant(self, tmp_path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.jsx").write_text("x", encoding="utf-8")
        assert _resolve_case_variant_rel(str(tmp_path), "src/App.jsx") == "src/app.jsx"

    def test_root_level_variant(self, tmp_path) -> None:
        (tmp_path / "Readme.md").write_text("x", encoding="utf-8")
        assert _resolve_case_variant_rel(str(tmp_path), "README.MD") == "Readme.md"

    def test_exact_match_returns_none(self, tmp_path) -> None:
        # Exact path exists -> not a case-variant case; handled by the normal path.
        (tmp_path / "app.jsx").write_text("x", encoding="utf-8")
        assert _resolve_case_variant_rel(str(tmp_path), "app.jsx") is None

    def test_no_variant_returns_none(self, tmp_path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.jsx").write_text("x", encoding="utf-8")
        assert _resolve_case_variant_rel(str(tmp_path), "src/component.jsx") is None

    def test_missing_directory_returns_none(self, tmp_path) -> None:
        assert _resolve_case_variant_rel(str(tmp_path), "nope/missing.jsx") is None

    def test_directory_case_variant_is_not_matched_as_file(self, tmp_path) -> None:
        # A directory named like the target must not be treated as a file redirect.
        os.makedirs(tmp_path / "Components")
        assert _resolve_case_variant_rel(str(tmp_path), "components") is None

    def test_does_not_redirect_across_directories(self, tmp_path) -> None:
        # app.jsx in root must NOT satisfy a write to src/App.jsx.
        (tmp_path / "app.jsx").write_text("x", encoding="utf-8")
        (tmp_path / "src").mkdir()
        assert _resolve_case_variant_rel(str(tmp_path), "src/App.jsx") is None
