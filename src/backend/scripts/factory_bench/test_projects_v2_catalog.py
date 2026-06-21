#!/usr/bin/env python3
"""Validate projects_v2.json catalog integrity.

Tests:
- Schema completeness (all required fields present)
- Level range (L1-L12)
- Language validity (7 supported languages)
- ID format consistency (L{level}-{seq})
- Level-min_files mapping consistency
- Compile check matches primary_language
- content_any checks present
- source_target_coverage checks present
- Language distribution balance
- No duplicate project IDs
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

_FIXTURE = Path(__file__).resolve().parent / "projects_v2.json"

REQUIRED_FIELDS = [
    "id",
    "level",
    "domain",
    "project_type",
    "primary_language",
    "title",
    "creative_hook",
    "novelty_tags",
    "brief",
    "test_focus",
    "checks",
]

VALID_LEVELS = set(range(1, 13))
VALID_LANGUAGES = {"typescript", "javascript", "python", "go", "rust", "cpp", "java"}
VALID_DOMAINS = {"science_creative", "creative", "game", "music", "internet_platform"}

LEVEL_MIN_FILES = {
    1: 3,
    2: 4,
    3: 5,
    4: 7,
    5: 8,
    6: 10,
    7: 11,
    8: 12,
    9: 13,
    10: 14,
    11: 15,
    12: 16,
}

LANG_COMPILE_CHECK = {
    "typescript": "ts_syntax",
    "javascript": "js_syntax",
    "python": "py_compile",
    "go": "go_compile",
    "rust": "rust_compile",
    "cpp": "cpp_compile",
    "java": "java_compile",
}


@pytest.fixture(scope="module")
def catalog_data() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def projects(catalog_data: dict[str, Any]) -> list[dict[str, Any]]:
    return catalog_data.get("projects", [])


def test_catalog_has_120_projects(projects: list[dict[str, Any]]) -> None:
    assert len(projects) == 120, f"Expected 120 projects, got {len(projects)}"


def test_no_duplicate_ids(projects: list[dict[str, Any]]) -> None:
    ids = [p["id"] for p in projects]
    dupes = [x for x in ids if ids.count(x) > 1]
    assert not dupes, f"Duplicate IDs: {sorted(set(dupes))}"


def test_all_levels_covered(projects: list[dict[str, Any]]) -> None:
    levels = {int(p["level"]) for p in projects}
    missing = VALID_LEVELS - levels
    assert not missing, f"Missing levels: {sorted(missing)}"


def test_10_projects_per_level(projects: list[dict[str, Any]]) -> None:
    counts = Counter(int(p["level"]) for p in projects)
    for level in VALID_LEVELS:
        assert counts[level] == 10, f"L{level} has {counts[level]} projects, expected 10"


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_required_field_present(projects: list[dict[str, Any]], field: str) -> None:
    for p in projects:
        assert field in p, f"Project {p.get('id', '?')} missing required field: {field}"


def test_level_range(projects: list[dict[str, Any]]) -> None:
    for p in projects:
        level = int(p["level"])
        assert level in VALID_LEVELS, f"Project {p['id']} has invalid level: {level}"


def test_language_valid(projects: list[dict[str, Any]]) -> None:
    for p in projects:
        lang = p["primary_language"]
        assert lang in VALID_LANGUAGES, f"Project {p['id']} has invalid language: {lang}"


def test_id_format(projects: list[dict[str, Any]]) -> None:
    for p in projects:
        pid = str(p["id"])
        level = int(p["level"])
        assert pid.startswith(f"L{level}-"), f"ID {pid} doesn't match level {level}"


def test_min_files_matches_level(projects: list[dict[str, Any]]) -> None:
    for p in projects:
        level = int(p["level"])
        checks = p.get("checks", [])
        for check in checks:
            check_str = str(check)
            if check_str.startswith("min_files:"):
                min_files = int(check_str.split(":")[1])
                expected = LEVEL_MIN_FILES.get(level)
                assert min_files == expected, (
                    f"Project {p['id']} (L{level}): min_files={min_files}, expected={expected}"
                )


def test_compile_check_matches_language(projects: list[dict[str, Any]]) -> None:
    for p in projects:
        lang = p["primary_language"]
        expected = LANG_COMPILE_CHECK.get(lang)
        if not expected:
            continue
        checks = [str(c) for c in p.get("checks", [])]
        assert expected in checks, f"Project {p['id']} ({lang}): missing compile check {expected}"


def test_content_any_check_present(projects: list[dict[str, Any]]) -> None:
    for p in projects:
        checks = [str(c) for c in p.get("checks", [])]
        has_content = any(c.startswith("content_any:") for c in checks)
        assert has_content, f"Project {p['id']} missing content_any check"


def test_source_target_coverage_present(projects: list[dict[str, Any]]) -> None:
    for p in projects:
        checks = [str(c) for c in p.get("checks", [])]
        has_coverage = any(c.startswith("source_target_coverage:") for c in checks)
        assert has_coverage, f"Project {p['id']} missing source_target_coverage check"


def test_language_distribution_balanced(projects: list[dict[str, Any]]) -> None:
    counts = Counter(p["primary_language"] for p in projects)
    min_count = min(counts.values())
    max_count = max(counts.values())
    assert max_count - min_count <= 3, f"Language distribution too uneven: {dict(counts)}"


def test_novelty_tags_minimum(projects: list[dict[str, Any]]) -> None:
    for p in projects:
        tags = p.get("novelty_tags", [])
        assert len(tags) >= 3, f"Project {p['id']} has only {len(tags)} novelty_tags"


def test_brief_minimum_length(projects: list[dict[str, Any]]) -> None:
    for p in projects:
        brief = p.get("brief", "")
        assert len(brief) >= 50, f"Project {p['id']} brief too short: {len(brief)} chars"


def test_schema_version(catalog_data: dict[str, Any]) -> None:
    version = catalog_data.get("schema_version")
    assert version == "factory-bench/2", f"Unexpected schema_version: {version}"
