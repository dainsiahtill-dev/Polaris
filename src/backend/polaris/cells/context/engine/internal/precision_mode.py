from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

COST_CLASSES: tuple[str, ...] = ("LOCAL", "FIXED", "METERED")


def normalize_cost_class(value: str | None) -> str:
    if not value:
        return "LOCAL"
    upper = str(value).strip().upper()
    if upper in COST_CLASSES:
        return upper
    return "LOCAL"


def resolve_cost_class(value: str | None = None) -> str:
    if value:
        return normalize_cost_class(value)
    env_value = os.environ.get("KERNELONE_COST_MODEL") or os.environ.get("KERNELONE_COST_CLASS") or ""
    return normalize_cost_class(env_value)


def merge_policy(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(base)
    if not isinstance(override, dict):
        return merged
    for key, value in override.items():
        if value is None:
            continue
        merged[key] = value
    return merged


@dataclass(frozen=True)
class CostStrategy:
    name: str
    cost_class: str
    budget: dict[str, int]
    policy: dict[str, Any]
    sources_enabled: list[str]


ROLE_POLICIES: dict[str, dict[str, Any]] = {
    "pm": {
        "max_items": 8,
        "required_providers": ["docs", "contract", "memory"],
        "forbidden_providers": ["repo_evidence"],
        "memory_limit": 3,
    },
    "director": {
        "max_items": 12,
        "required_providers": ["contract", "repo_evidence"],
        "memory_limit": 5,
    },
    "qa": {
        "max_items": 10,
        "required_providers": ["contract", "events", "repo_evidence"],
        "memory_limit": 4,
    },
}


COST_POLICIES: dict[str, dict[str, Any]] = {
    "LOCAL": {
        "docs_max_chars": 2400,
        "contract_max_chars": 5000,
        "memory_top_k": 6,
        "memory_max_chars": 600,
        "events_tail_lines": 160,
        "events_max_chars": 3000,
        "repo_evidence_max_chars": 1500,
        "repo_map_max_files": 200,
        "repo_map_max_lines": 200,
        "repo_map_per_file_lines": 12,
    },
    "FIXED": {
        "docs_max_chars": 2000,
        "contract_max_chars": 4000,
        "memory_top_k": 5,
        "memory_max_chars": 500,
        "events_tail_lines": 120,
        "events_max_chars": 2400,
        "repo_evidence_max_chars": 1200,
        "repo_map_max_files": 160,
        "repo_map_max_lines": 160,
        "repo_map_per_file_lines": 10,
    },
    "METERED": {
        "docs_max_chars": 1200,
        "contract_max_chars": 3000,
        "memory_top_k": 3,
        "memory_max_chars": 360,
        "memory_refs_required": True,
        "events_tail_lines": 80,
        "events_max_chars": 1600,
        "repo_evidence_max_chars": 800,
        "repo_map_max_files": 120,
        "repo_map_max_lines": 80,
        "repo_map_per_file_lines": 8,
        "max_items": 6,
    },
}


COST_BUDGETS: dict[str, dict[str, int]] = {
    "LOCAL": {"max_tokens": 12000, "max_chars": 48000},
    "FIXED": {"max_tokens": 8000, "max_chars": 32000},
    "METERED": {"max_tokens": 3000, "max_chars": 12000},
}


BASE_SOURCES: dict[str, list[str]] = {
    "pm": ["docs", "contract", "memory"],
    "director": ["docs", "contract", "memory", "events", "repo_evidence"],
    "qa": ["docs", "contract", "memory", "events", "repo_evidence"],
}


def resolve_sources(role: str, cost_class: str) -> list[str]:
    role_key = (role or "").strip().lower()
    sources = list(BASE_SOURCES.get(role_key, ["docs", "contract"]))
    cost_class = normalize_cost_class(cost_class)
    if cost_class == "METERED":
        if role_key in ("director", "qa"):
            sources = ["contract", "repo_evidence", "events"]
        else:
            sources = ["docs", "contract", "memory"]
    else:
        sources.append("repo_map")
    return _unique_preserve(sources)


def route_by_cost_model(cost_class: str, role: str) -> CostStrategy:
    cost_class = normalize_cost_class(cost_class)
    role_key = (role or "").strip().lower()
    role_policy = dict(ROLE_POLICIES.get(role_key, {}))
    cost_policy = dict(COST_POLICIES.get(cost_class, {}))
    policy = merge_policy(role_policy, cost_policy)
    sources = resolve_sources(role_key, cost_class)
    budget = dict(COST_BUDGETS.get(cost_class, COST_BUDGETS["LOCAL"]))
    name = {
        "LOCAL": "ContextWindowStrategy",
        "FIXED": "QuotaOptimizationStrategy",
        "METERED": "TokenSavingStrategy",
    }.get(cost_class, "ContextWindowStrategy")
    return CostStrategy(
        name=name,
        cost_class=cost_class,
        budget=budget,
        policy=policy,
        sources_enabled=sources,
    )


def _unique_preserve(values: list[str]) -> list[str]:
    from polaris.kernelone.runtime.shared_types import unique_preserve as _impl

    return _impl(values)
