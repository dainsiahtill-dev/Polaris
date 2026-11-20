"""Opt-in Director binding selection from host-pinned independent holdout results."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, TypeGuard

Binding = tuple[str, str]


def load_routing_record(path: str | None, digest: str | None) -> tuple[str, dict[str, Any]] | None:
    if path is None and digest is None:
        return None
    if not path or not digest or not Path(path).is_absolute():
        raise ValueError("Engineering routing requires both the host record path and SHA-256 digest")
    content = Path(path).read_bytes()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("Engineering routing record digest mismatch")
    record = json.loads(content.decode("utf-8"))
    if not isinstance(record, dict):
        raise ValueError("Engineering routing requires a JSON object")
    return digest, record


def conditions_digest(conditions: dict[str, Any]) -> str:
    encoded = json.dumps(conditions, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _number(value: Any) -> TypeGuard[int | float]:
    return type(value) in {int, float} and math.isfinite(value)


def _qualified_cost(data: Any, projects: set[str], record: dict[str, Any], binding: Binding) -> float | None:
    if not isinstance(data, list) or len(data) != len(projects):
        return None
    seen: set[str] = set()
    total = 0.0
    runs: set[str] = set()
    for trial in data:
        if not isinstance(trial, dict):
            return None
        project = trial.get("project")
        review, bill = trial.get("review"), trial.get("bill")
        if not isinstance(review, dict) or not isinstance(bill, dict):
            return None
        cost = bill.get("amount")
        budget = trial.get("budget_seconds")
        duration = trial.get("elapsed_seconds")
        run = trial.get("run_id")
        identity = {"project_id": project, "run_id": run, "plan_sha256": record.get("plan_sha256")}
        if (
            not isinstance(project, str)
            or project not in projects
            or project in seen
            or not isinstance(run, str)
            or not run
            or run in runs
            or any(doc.get(key) != value for doc in (review, bill) for key, value in identity.items())
            or trial.get("project_outcome") != "accepted"
            or review.get("conditions_sha256") != record["conditions_sha256"]
            or not _number(budget)
            or not _number(duration)
            or not record["minimum_remaining_seconds"] <= budget <= record["maximum_remaining_seconds"]
            or not 0 <= duration <= budget
            or review.get("kind") != "external_full_contract_review"
            or review.get("verdict") != "PASS"
            or review.get("reviewer") != record["reviewer"]
            or review.get("protocol_sha256") != record["protocol_sha256"]
            or not re.fullmatch("[a-f0-9]{64}", str(trial.get("artifact_sha256") or ""))
            or review.get("artifact_sha256") != trial["artifact_sha256"]
            or bill.get("kind") != "settled_billing"
            or bill.get("currency") != "USD"
            or bill.get("all_attempts") is not True
            or (bill.get("provider_id"), bill.get("model")) != binding
            or not _number(cost)
            or cost < 0
        ):
            return None
        seen.add(project)
        runs.add(run)
        total += cost
    return total / len(projects) if math.isfinite(total) else None


def choose_qualified_bindings(
    record: dict[str, Any], allowed: tuple[Binding, ...], *, goal: str, conditions_sha256: str, seconds_left: float
) -> tuple[Binding, ...]:
    """First policy supports fixed independent all-success samples at one-sided 95% confidence."""
    threshold = record.get("minimum_success_rate")
    lower_time = record.get("minimum_remaining_seconds")
    upper_time = record.get("maximum_remaining_seconds")
    goals = record.get("goals")
    if (
        not record.get("reviewer")
        or not re.fullmatch("[a-f0-9]{64}", str(record.get("plan_sha256") or ""))
        or not re.fullmatch("[a-f0-9]{64}", str(record.get("protocol_sha256") or ""))
        or not record.get("task_class")
        or not isinstance(goals, list)
        or hashlib.sha256(goal.encode("utf-8")).hexdigest() not in goals
        or conditions_sha256 != record.get("conditions_sha256")
        or not _number(lower_time)
        or not _number(upper_time)
        or not 0 < lower_time <= seconds_left <= upper_time
        or not _number(threshold)
        or not 0 < threshold <= 1
        or record.get("terminal")
    ):
        return ()
    count = record.get("fixed_sample_size")
    projects = record.get("projects")
    rows = record.get("bindings")
    if (
        type(count) is not int
        or count <= 0
        or not isinstance(projects, list)
        or len(projects) != count
        or not all(isinstance(p, str) and p for p in projects)
        or len(set(projects)) != count
        or not isinstance(rows, list)
    ):
        return ()
    # Simultaneous Bonferroni lower bounds account for choosing among the
    # prespecified, authorized models on the same cohort after seeing costs.
    candidates = [(row.get("provider_id"), row.get("model")) for row in rows if isinstance(row, dict)]
    peers = [key for key in candidates if key in allowed]
    if len(set(peers)) != len(peers) or not peers or (0.05 / len(peers)) ** (1.0 / count) < threshold:
        return ()
    costs = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (row.get("provider_id"), row.get("model"))
        if key in allowed:
            cost = _qualified_cost(row.get("trials"), set(projects), record, key)
            if cost is not None:
                costs[key] = cost
    return tuple(sorted((key for key in allowed if key in costs), key=costs.__getitem__))
