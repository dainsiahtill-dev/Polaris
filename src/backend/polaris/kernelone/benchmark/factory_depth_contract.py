"""Level-aware delivery-depth contracts for internal factory-bench runs."""

from __future__ import annotations

from typing import Any

FACTORY_BENCH_LEVEL_CONTRACT_SCHEMA_VERSION = "factory-bench.level_contract.v1"

_LEVEL_BASELINES: dict[int, dict[str, int]] = {
    1: {
        "min_prod_files": 3,
        "min_prod_lines": 120,
        "min_behavior_symbols": 6,
        "min_branch_count": 3,
        "min_test_files": 1,
        "min_test_assertions": 2,
        "min_behavior_rules": 3,
        "min_primary_entities": 3,
        "min_edge_cases": 2,
    },
    2: {
        "min_prod_files": 6,
        "min_prod_lines": 500,
        "min_behavior_symbols": 12,
        "min_branch_count": 6,
        "min_test_files": 1,
        "min_test_assertions": 8,
        "min_behavior_rules": 4,
        "min_primary_entities": 4,
        "min_edge_cases": 3,
    },
    3: {
        "min_prod_files": 7,
        "min_prod_lines": 650,
        "min_behavior_symbols": 16,
        "min_branch_count": 8,
        "min_test_files": 2,
        "min_test_assertions": 10,
        "min_behavior_rules": 5,
        "min_primary_entities": 4,
        "min_edge_cases": 4,
    },
}

_DEFAULT_KEYS = tuple(next(iter(_LEVEL_BASELINES.values())).keys())


def _level_to_int(level: Any) -> int:
    try:
        parsed = int(level or 1)
    except (TypeError, ValueError):
        return 1
    return max(parsed, 1)


def _baseline_for_level(level: int) -> dict[str, int]:
    if level in _LEVEL_BASELINES:
        return dict(_LEVEL_BASELINES[level])

    previous = dict(_LEVEL_BASELINES[max(_LEVEL_BASELINES)])
    for current in range(max(_LEVEL_BASELINES) + 1, min(level, 12) + 1):
        previous = {
            "min_prod_files": previous["min_prod_files"] + 1,
            "min_prod_lines": previous["min_prod_lines"] + 180,
            "min_behavior_symbols": previous["min_behavior_symbols"] + 4,
            "min_branch_count": previous["min_branch_count"] + 2,
            "min_test_files": previous["min_test_files"] + (1 if current in {4, 7, 10} else 0),
            "min_test_assertions": previous["min_test_assertions"] + 3,
            "min_behavior_rules": previous["min_behavior_rules"] + (1 if current % 2 == 0 else 0),
            "min_primary_entities": previous["min_primary_entities"] + (1 if current % 3 == 0 else 0),
            "min_edge_cases": previous["min_edge_cases"] + (1 if current % 2 == 1 else 0),
        }
    return previous


def build_factory_bench_level_contract(
    level: Any,
    *,
    project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the canonical depth contract for a factory-bench level."""

    normalized_level = _level_to_int(level)
    project_payload = project or {}
    minimums = _baseline_for_level(normalized_level)
    return {
        "schema_version": FACTORY_BENCH_LEVEL_CONTRACT_SCHEMA_VERSION,
        "source": "factory_bench.level_contract",
        "level": normalized_level,
        "project_id": str(project_payload.get("id") or "").strip(),
        "language": str(project_payload.get("primary_language") or "").strip(),
        "project_type": str(project_payload.get("project_type") or "").strip(),
        "minimums": minimums,
        "required_evidence": [
            "PM task contract carries product-specific behavior rules and sample data",
            "Chief Engineer blueprint names concrete modules, public contracts, and gate commands",
            "Director implementation separates core behavior from entrypoint/I/O",
            "Tests assert business results across normal, boundary, and invalid inputs",
            "Factory audit implementation_depth passes the level-specific thresholds",
        ],
        "anti_hollow_delivery": [
            "Do not satisfy content checks by keyword stuffing, comments, README text, or static placeholder output",
            "Do not pass tests that only check files exist, scripts exist, or keywords appear",
            "Do not let runtime hard gates override low QA score or major quality findings",
        ],
    }


def extract_level_contract_minimums(
    contract: dict[str, Any] | None,
    *,
    level: Any = 1,
) -> dict[str, int]:
    """Extract depth minimums from a contract, filling missing keys from level defaults."""

    baseline = _baseline_for_level(_level_to_int(level))
    if not isinstance(contract, dict):
        return baseline
    raw = contract.get("minimums")
    if not isinstance(raw, dict):
        return baseline

    result = dict(baseline)
    for key in _DEFAULT_KEYS:
        try:
            value = int(raw.get(key, result[key]))
        except (TypeError, ValueError):
            value = result[key]
        result[key] = max(value, 0)
    return result


def format_level_contract_for_requirements(contract: dict[str, Any]) -> str:
    """Render a depth contract block for the generated requirements document."""

    minimums = extract_level_contract_minimums(contract, level=contract.get("level"))
    evidence_lines = "\n".join(f"- {item}" for item in contract.get("required_evidence", []) if str(item).strip())
    anti_hollow_lines = "\n".join(f"- {item}" for item in contract.get("anti_hollow_delivery", []) if str(item).strip())
    metrics = "\n".join(f"- {key}: {value}" for key, value in sorted(minimums.items()))
    return (
        "## Bench Level Contract (Mandatory)\n"
        f"- schema_version: {contract.get('schema_version')}\n"
        f"- level: {contract.get('level')}\n"
        "\n"
        "Required minimums:\n"
        f"{metrics}\n"
        "\n"
        "Required evidence:\n"
        f"{evidence_lines or '- No additional evidence declared.'}\n"
        "\n"
        "Anti-hollow delivery rules:\n"
        f"{anti_hollow_lines or '- No hollow-delivery rules declared.'}\n"
        "\n"
    )
