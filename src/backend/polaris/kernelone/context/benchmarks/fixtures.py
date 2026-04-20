"""Benchmark Fixtures — case definitions loaded from JSON fixtures.

.. deprecated::
    This module is deprecated. Use ``polaris.kernelone.benchmark.unified_models``
    for new benchmark case definitions. The canonical benchmark framework is now
    ``polaris/kernelone/benchmark/`` (UnifiedBenchmarkCase, UnifiedBenchmarkRunner).

    This module is retained for backward compatibility with existing
    ContextOS benchmark validators and will be removed in a future release.

Provides:
1. BenchmarkCase dataclass      — structured representation of a fixture
2. load_fixture()               — load a single fixture by case_id
3. load_all_fixtures()          — load all fixtures from the fixtures/ directory
4. BUILTIN_FIXTURES             — registry of all available fixture case_ids
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Fixtures directory
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_DIR: Path = _FIXTURES_DIR


# ---------------------------------------------------------------------------
# BenchmarkCase dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BudgetConditions:
    """Budget constraints for a benchmark case."""

    max_tokens: int
    max_turns: int
    max_wall_time_seconds: float

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> BudgetConditions:
        return cls(
            max_tokens=int(data.get("max_tokens", 0)),
            max_turns=int(data.get("max_turns", 0)),
            max_wall_time_seconds=float(data.get("max_wall_time_seconds", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """Structured representation of a benchmark fixture.

    Attributes:
        case_id: Unique identifier for this benchmark case.
        description: Human-readable description of what the case tests.
        user_prompt: The prompt given to the agent for this case.
        workspace_fixture: Description of the workspace state expected.
        expected_evidence_path: List of file paths the agent is expected to
            touch or modify during this case.
        expected_answer_shape: Type of answer expected (e.g., "diagnosis",
            "summary", "edit", "refactor_plan").
        budget_conditions: Budget constraints for the case.
        canonical_profile: The canonical profile to use for this case.
        score_threshold: Minimum score (0.0–1.0) required to pass.
        fixture_path: Absolute path to the source fixture file (for tooling).
    """

    case_id: str
    description: str
    user_prompt: str
    workspace_fixture: str
    expected_evidence_path: tuple[str, ...]
    expected_answer_shape: str
    budget_conditions: BudgetConditions
    canonical_profile: str
    score_threshold: float
    fixture_path: str = ""

    @classmethod
    def from_mapping(cls, data: dict[str, Any], fixture_path: str = "") -> BenchmarkCase:
        return cls(
            case_id=str(data.get("case_id", "")),
            description=str(data.get("description", "")),
            user_prompt=str(data.get("user_prompt", "")),
            workspace_fixture=str(data.get("workspace_fixture", "")),
            expected_evidence_path=tuple(data.get("expected_evidence_path", [])),
            expected_answer_shape=str(data.get("expected_answer_shape", "")),
            budget_conditions=BudgetConditions.from_mapping(data.get("budget_conditions", {})),
            canonical_profile=str(data.get("canonical_profile", "")),
            score_threshold=float(data.get("score_threshold", 0.0)),
            fixture_path=fixture_path,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "user_prompt": self.user_prompt,
            "workspace_fixture": self.workspace_fixture,
            "expected_evidence_path": list(self.expected_evidence_path),
            "expected_answer_shape": self.expected_answer_shape,
            "budget_conditions": {
                "max_tokens": self.budget_conditions.max_tokens,
                "max_turns": self.budget_conditions.max_turns,
                "max_wall_time_seconds": self.budget_conditions.max_wall_time_seconds,
            },
            "canonical_profile": self.canonical_profile,
            "score_threshold": self.score_threshold,
            "fixture_path": self.fixture_path,
        }


# ---------------------------------------------------------------------------
# Loader functions
# ---------------------------------------------------------------------------


def load_fixture(case_id: str) -> BenchmarkCase:
    """Load a single fixture by case_id.

    Args:
        case_id: The case_id of the fixture to load (e.g., "summarize_project").

    Returns:
        BenchmarkCase for the requested fixture.

    Raises:
        FileNotFoundError: If no fixture with that case_id exists.
        ValueError: If the fixture file is malformed.
    """
    path = _DIR / f"{case_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Fixture not found: {case_id!r} at {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Fixture {case_id!r} must be a JSON object")
    # Validate case_id matches
    if data.get("case_id") != case_id:
        raise ValueError(f"Fixture case_id mismatch: expected {case_id!r}, got {data.get('case_id')!r}")
    return BenchmarkCase.from_mapping(data, fixture_path=str(path))


def load_all_fixtures() -> dict[str, BenchmarkCase]:
    """Load all fixtures from the fixtures/ directory.

    Returns:
        Dict mapping case_id -> BenchmarkCase for all discovered fixtures.
    """
    fixtures: dict[str, BenchmarkCase] = {}
    if not _DIR.is_dir():
        return fixtures
    for path in sorted(_DIR.iterdir()):
        if path.suffix == ".json" and path.is_file():
            try:
                case = load_fixture(path.stem)
                fixtures[case.case_id] = case
            except (FileNotFoundError, ValueError):
                # Skip malformed fixtures
                continue
    return fixtures


# ---------------------------------------------------------------------------
# Built-in fixture registry
# ---------------------------------------------------------------------------

BUILTIN_FIXTURES: frozenset[str] = frozenset(p.stem for p in _DIR.iterdir() if p.suffix == ".json" and p.is_file())

__all__ = [
    "BUILTIN_FIXTURES",
    "BenchmarkCase",
    "BudgetConditions",
    "load_all_fixtures",
    "load_fixture",
]
