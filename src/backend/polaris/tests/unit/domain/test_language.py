"""Tests for polaris.domain.language."""

from __future__ import annotations

import pytest
from polaris.domain.language import detect_language


class TestDetectLanguage:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("main.py", "python"),
            ("app.js", "javascript"),
            ("app.ts", "typescript"),
            ("component.tsx", "typescript"),
            ("Main.java", "java"),
            ("main.go", "go"),
            ("lib.rs", "rust"),
            ("main.c", "c"),
            ("main.cpp", "cpp"),
            ("main.h", "c"),
            ("App.cs", "csharp"),
            ("Gemfile.rb", "ruby"),
            ("index.php", "php"),
            ("App.swift", "swift"),
            ("build.kt", "kotlin"),
            ("config.scala", "scala"),
            ("run.sh", "shell"),
            ("deploy.bash", "shell"),
            ("setup.zsh", "shell"),
            ("query.sql", "sql"),
            ("data.json", "json"),
            ("config.yaml", "yaml"),
            ("config.yml", "yaml"),
            ("Cargo.toml", "toml"),
            ("README.md", "markdown"),
            ("index.html", "html"),
            ("style.css", "css"),
            ("unknown.xyz", "unknown"),
            ("", "unknown"),
        ],
    )
    def test_detect_language(self, path: str, expected: str) -> None:
        assert detect_language(path) == expected
