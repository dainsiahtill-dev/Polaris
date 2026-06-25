"""Tests for the Director deterministic-repair strategy catalog."""

from __future__ import annotations

import re
from pathlib import Path

from polaris.cells.director.runtime.public import (
    QueryDirectorRepairStrategyCatalogV1,
    query_director_repair_strategy_catalog,
)
from polaris.cells.roles.adapters.internal.director.deterministic_repairs.strategy_catalog import (
    KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS,
    describe_deterministic_repair_strategy,
    deterministic_repair_source_tool_known,
    deterministic_repair_strategy_catalog,
    summarize_deterministic_repair_source_tools,
)

_SOURCE_TOOL_RE = re.compile(r"[\"'](?P<tool>deterministic_[A-Za-z0-9_]+)[\"']")
_NON_STRATEGY_TOKENS = {"deterministic_repair_profiles"}


def _implementation_files() -> list[Path]:
    root = Path(__file__).resolve().parents[1] / "internal" / "director"
    repair_root = root / "deterministic_repairs"
    files = [path for path in repair_root.glob("*.py") if path.name not in {"strategy_catalog.py", "__init__.py"}]
    files.append(root / "execute_method.py")
    return files


def _deterministic_tokens_from_implementation() -> set[str]:
    tokens: set[str] = set()
    for path in _implementation_files():
        text = path.read_text(encoding="utf-8")
        tokens.update(
            match.group("tool")
            for match in _SOURCE_TOOL_RE.finditer(text)
            if match.group("tool") not in _NON_STRATEGY_TOKENS
        )
    return tokens


def test_catalog_registers_all_hardcoded_deterministic_tokens() -> None:
    implementation_tokens = _deterministic_tokens_from_implementation()

    assert implementation_tokens
    assert implementation_tokens <= KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS


def test_catalog_describes_language_phase_and_concern() -> None:
    profile = describe_deterministic_repair_strategy("deterministic_typescript_missing_export_repair")

    assert profile.source_tool == "deterministic_typescript_missing_export_repair"
    assert profile.language == "typescript"
    assert profile.phase == "quality_repair"
    assert profile.concern == "missing_symbol_or_file"
    assert profile.risk_level == "low"


def test_unknown_source_tool_is_fail_closed_high_risk() -> None:
    profile = describe_deterministic_repair_strategy("deterministic_future_repair")

    assert deterministic_repair_source_tool_known(profile.source_tool) is False
    assert profile.language == "unknown"
    assert profile.phase == "unknown"
    assert profile.concern == "unregistered"
    assert profile.risk_level == "high"


def test_summary_dedupes_profiles_and_marks_registration() -> None:
    profiles = summarize_deterministic_repair_source_tools(
        [
            "deterministic_patch_residue_cleanup",
            "deterministic_patch_residue_cleanup",
            "deterministic_future_repair",
        ]
    )

    assert profiles == [
        {
            "source_tool": "deterministic_patch_residue_cleanup",
            "language": "generic",
            "phase": "cleanup",
            "concern": "generated_residue",
            "risk_level": "low",
            "registered": True,
        },
        {
            "source_tool": "deterministic_future_repair",
            "language": "unknown",
            "phase": "unknown",
            "concern": "unregistered",
            "risk_level": "high",
            "registered": False,
        },
    ]


def test_catalog_is_stable_sorted_and_machine_readable() -> None:
    catalog = deterministic_repair_strategy_catalog()
    source_tools = [item["source_tool"] for item in catalog]

    assert source_tools == sorted(source_tools)
    assert len(source_tools) == len(set(source_tools))
    assert {"source_tool", "language", "phase", "concern", "risk_level"} <= set(catalog[0])


def test_director_runtime_public_catalog_mirrors_authoritative_catalog() -> None:
    catalog = deterministic_repair_strategy_catalog()
    result = query_director_repair_strategy_catalog(QueryDirectorRepairStrategyCatalogV1())
    payload = result.to_dict()

    assert payload["schema_version"] == "director.deterministic_repair_strategy_catalog.v1"
    assert payload["source"] == "director.runtime.repair_kernel.strategy_catalog"
    assert payload["access"] == "read_only"
    assert payload["agi_execution_authority"] is False
    assert payload["director_tool_execution_required"] is True
    assert payload["owner_cell"] == "director.runtime"
    assert payload["execution_boundary"] == "director_authorized_tools_only"
    assert payload["chain"] == "PM → Chief Engineer → Director"
    assert payload["unknown_source_tool_policy"] == "fail_closed_high_risk"
    assert payload["items"] == catalog
    assert payload["summary"]["total"] == len(catalog)
    assert payload["summary"]["returned"] == len(catalog)
    assert payload["summary"]["by_concern"]
