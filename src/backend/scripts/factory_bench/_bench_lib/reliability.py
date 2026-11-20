"""Descriptive measures on the frozen internal engineering batch selection."""

from __future__ import annotations

import math
from typing import Any


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def batch_reliability(selection: list[str], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Report A from the platform and independent checks of the catalog floor.

    A structural floor passing cannot establish end-to-end correctness (Y).
    Partial or dry selections have no fixed-sample qualification.
    """
    attempts = [record for record in records if record.get("harness_status") != "NOT_RUN"]
    released = reviewed = release_errors = known_decisions = 0
    floor_passed = floor_reviewed = 0
    elapsed: list[float] = []
    for record in attempts:
        observation = record.get("final_observation") or record.get("observation") or {}
        platform = observation.get("platform") or {}
        outcome = platform.get("project_outcome")
        terminal = platform.get("status") in {"completed", "failed", "cancelled", "interrupted"}
        known_decisions += terminal and outcome in {"accepted", "rejected", "not_accepted"}
        floor = record.get("catalog_quality")
        checked = floor in {"PASS", "FAIL"}
        floor_reviewed += checked
        floor_passed += floor == "PASS"
        if terminal and outcome == "accepted":
            released += 1
            reviewed += checked
            release_errors += floor == "FAIL"
        duration = observation.get("elapsed_seconds")
        if (
            terminal
            and isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and math.isfinite(duration)
            and duration >= 0
        ):
            elapsed.append(duration)

    elapsed.sort()
    unknown_decisions = len(selection) - known_decisions
    return {
        "planned": len(selection),
        "attempted": len(attempts),
        "fixed_selection_complete": bool(selection) and [r["project_id"] for r in attempts] == selection,
        "known_platform_decisions": known_decisions,
        "unknown_or_unobserved_decisions": unknown_decisions,
        "observed_releases": released,
        "observed_acceptance_coverage": _ratio(released, known_decisions),
        "planned_acceptance_coverage_bounds": [
            _ratio(released, len(selection)),
            _ratio(released + unknown_decisions, len(selection)),
        ],
        "floor_reviewed": floor_reviewed,
        "catalog_floor_pass_rate_among_reviewed": _ratio(floor_passed, floor_reviewed),
        "reviewed_releases": reviewed,
        "release_review_coverage": _ratio(reviewed, released),
        "catalog_floor_failures_among_releases": release_errors,
        "catalog_floor_error_among_reviewed_releases": _ratio(release_errors, reviewed),
        "observed_terminal_durations": len(elapsed),
        "p95_observed_elapsed_seconds": elapsed[math.ceil(0.95 * len(elapsed)) - 1] if elapsed else None,
        "full_contract_independent_success_rate": None,
        "unit_cost_per_independent_success": None,
        "qualification_status": "UNKNOWN",
        "qualification_reason": "full-contract independent labels and complete physical billing unavailable",
    }


def repeated_delivery(batches: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe observed bench-gate stability for repeated comparable cohorts in supplied order."""
    if len(batches) < 2 or not all(isinstance(batch, dict) for batch in batches):
        raise ValueError("repeatability requires at least two ordered batch summaries")
    projects = batches[0].get("selected_project_ids")
    if not isinstance(projects, list) or not projects or len(set(projects)) != len(projects):
        raise ValueError("expected a nonempty unique frozen project selection")
    trials: dict[str, list[str]] = {project: [] for project in projects}
    batch_ids: set[str] = set()
    run_ids: set[str] = set()
    first_conditions = None
    for batch in batches:
        identity = batch.get("comparison") or {}
        inputs = {key: value["sha256"] for key, value in batch["frozen_inputs"].items()}
        if not {"catalog", "llm_config", "host_config"} <= inputs.keys() or not all(
            identity.get(k) for k in ("source_fingerprint", "policy_sha256")
        ):
            raise ValueError("repeatability requires registered source, policy and frozen inputs")
        conditions = (
            identity["source_fingerprint"],
            identity["policy_sha256"],
            inputs,
            batch["workflow"],
            batch.get("permission_profile_id"),
            batch["project_budget_seconds"],
            batch["overall_budget_seconds"],
        )
        if batch.get("selected_project_ids") != projects or (
            first_conditions is not None and conditions != first_conditions
        ):
            raise ValueError("repetitions must use the same selection and conditions")
        first_conditions = conditions
        batch_id = batch.get("batch_id")
        if not batch_id or batch_id in batch_ids:
            raise ValueError("duplicate or missing batch")
        batch_ids.add(batch_id)
        seen = set()
        for record in batch["records"]:
            project = record["project_id"]
            if project in seen or project not in trials:
                raise ValueError("unexpected or repeated project in a batch")
            seen.add(project)
            observation = record.get("final_observation") or record.get("observation") or {}
            run_id = (observation.get("platform") or {}).get("run_id")
            gate = record.get("bench_gate")
            if run_id:
                if run_id in run_ids:
                    raise ValueError("a run cannot count as two independent attempts")
                run_ids.add(run_id)
            trials[project].append(gate if run_id and gate in {"PASS", "FAIL"} else "UNKNOWN")
        for project in set(projects) - seen:
            trials[project].append("UNKNOWN")

    def counts(verdicts: list[str]) -> dict[str, int]:
        return {status.lower(): verdicts.count(status) for status in ("PASS", "FAIL", "UNKNOWN")}

    return {
        "measure": "settled_internal_bench_gate",
        "projects": len(projects),
        "k": len(batches),
        "ordered_batches": [batch["batch_id"] for batch in batches],
        "project_trials": trials,
        "first_attempt": counts([results[0] for results in trials.values()]),
        "at_least_one_pass": counts(
            [
                "PASS" if "PASS" in results else "UNKNOWN" if "UNKNOWN" in results else "FAIL"
                for results in trials.values()
            ]
        ),
        "all_attempts_pass": counts(
            [
                "FAIL" if "FAIL" in results else "UNKNOWN" if "UNKNOWN" in results else "PASS"
                for results in trials.values()
            ]
        ),
    }
