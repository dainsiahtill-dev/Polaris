"""Tests for the deterministic repo-identity card (Phase-1 B1)."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.kernel.internal.context_gateway.repo_identity import (
    _MAX_CARD_CHARS,
    build_repo_identity_card,
)


def _make_django_like(tmp_path: Path) -> Path:
    (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    (tmp_path / "setup.py").write_text("from setuptools import setup\n", encoding="utf-8")
    package = tmp_path / "django" / "db" / "models"
    package.mkdir(parents=True)
    for i in range(5):
        (package / f"mod{i}.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.txt").write_text("docs\n", encoding="utf-8")
    return tmp_path


class TestBuildRepoIdentityCard:
    def test_python_repo_positive_and_negative_assertions(self, tmp_path: Path) -> None:
        card = build_repo_identity_card(str(_make_django_like(tmp_path)))
        assert card is not None
        assert "Python 100%" in card
        assert "manage.py" in card.split("仓库根存在的项目标记")[1].splitlines()[0]
        absent_line = next(line for line in card.splitlines() if "仓库根不存在" in line)
        # The run20 adsorber set must be explicitly denied when truly absent.
        assert "package.json" in absent_line
        assert "app.py" in absent_line
        assert "main.py" in absent_line
        assert "repo_rg" in card

    def test_node_repo_flips_assertions(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text("export {}\n", encoding="utf-8")
        card = build_repo_identity_card(str(tmp_path))
        assert card is not None
        present_line = next(line for line in card.splitlines() if "仓库根存在" in line)
        assert "package.json" in present_line
        assert "TypeScript" in card
        absent_line = next(line for line in card.splitlines() if "仓库根不存在" in line)
        assert "pyproject.toml" in absent_line

    def test_deterministic(self, tmp_path: Path) -> None:
        ws = str(_make_django_like(tmp_path))
        assert build_repo_identity_card(ws) == build_repo_identity_card(ws)

    def test_top_level_entries_listed_with_dir_suffix(self, tmp_path: Path) -> None:
        card = build_repo_identity_card(str(_make_django_like(tmp_path)))
        assert card is not None
        top_line = next(line for line in card.splitlines() if "顶层条目" in line)
        assert "django/" in top_line
        assert "docs/" in top_line
        assert "manage.py" in top_line

    def test_missing_workspace_returns_none(self, tmp_path: Path) -> None:
        assert build_repo_identity_card(str(tmp_path / "nope")) is None
        assert build_repo_identity_card("") is None

    def test_empty_repo_still_produces_negative_assertions(self, tmp_path: Path) -> None:
        card = build_repo_identity_card(str(tmp_path))
        assert card is not None
        assert "仓库根不存在" in card

    def test_card_respects_size_cap(self, tmp_path: Path) -> None:
        for i in range(40):
            (tmp_path / f"some_rather_long_top_level_directory_name_{i:02d}").mkdir()
        card = build_repo_identity_card(str(tmp_path))
        assert card is not None
        assert len(card) <= _MAX_CARD_CHARS

    def test_skip_dirs_excluded_from_language_scan(self, tmp_path: Path) -> None:
        vendored = tmp_path / "node_modules" / "lib"
        vendored.mkdir(parents=True)
        for i in range(50):
            (vendored / f"v{i}.js").write_text("//\n", encoding="utf-8")
        (tmp_path / "core.py").write_text("x = 1\n", encoding="utf-8")
        card = build_repo_identity_card(str(tmp_path))
        assert card is not None
        assert "Python 100%" in card
