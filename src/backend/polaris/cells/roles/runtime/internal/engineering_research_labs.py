"""Project versioned experiment dependencies for one laboratory in an engineering workspace."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_research_references import (
    validate_reference_specs,
    verify_references,
)

_ID = re.compile(r"[\w.-]{1,100}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
PLAN_LIMIT = 2 * 1024 * 1024


def _key(node: Mapping[str, Any]) -> str:
    for field in ("lab", "id"):
        if not isinstance(node.get(field), str) or not _ID.fullmatch(node[field]):
            raise ValueError(f"invalid experiment {field}")
    return f"{node['lab']}/{node['id']}"


def _catalog(plan: object) -> tuple[str, dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    if not isinstance(plan, dict) or plan.get("schema") != "engineering.research-plan.v1":
        raise ValueError("unsupported research plan schema")
    project = plan.get("project")
    if not isinstance(project, str) or not _ID.fullmatch(project):
        raise ValueError("invalid research project")
    nodes = plan.get("experiments")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 4096:
        raise ValueError("research plan requires 1–4096 experiments")
    catalog: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, str]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("expected an experiment object")
        key = _key(node)
        if key in catalog:
            raise ValueError(f"duplicate experiment {key}")
        if not isinstance(node.get("scope"), str) or not node["scope"].strip():
            raise ValueError(f"missing experiment scope for {key}")
        if node.get("report") is not None:
            validate_reference_specs([node["report"]])
        if not isinstance(node.get("next_question", ""), str):
            raise ValueError(f"invalid next_question for {key}")
        catalog[key] = node
        depends = node.get("depends_on", [])
        if not isinstance(depends, list):
            raise ValueError(f"invalid dependencies for {key}")
        edges[key] = {}
        for dep in depends:
            if not isinstance(dep, dict):
                raise ValueError(f"invalid dependency of {key}")
            pin = dep.get("sha256")
            if pin is not None and (not isinstance(pin, str) or not _DIGEST.fullmatch(pin)):
                raise ValueError(f"invalid dependency report SHA-256 for {key}")
            producer = _key(dep)
            if producer in edges[key]:
                raise ValueError(f"duplicate dependency {producer} in {key}")
            edges[key][producer] = pin or ""
    return project, catalog, edges


def _selected_order(catalog: dict[str, dict[str, Any]], edges: dict[str, dict[str, str]], lab: str) -> list[str]:
    pending = [k for k, node in catalog.items() if node["lab"] == lab]
    if not pending:
        raise ValueError(f"unknown laboratory {lab}")
    selected: set[str] = set()
    while pending:
        key = pending.pop()
        if key in selected:
            continue
        selected.add(key)
        pending.extend(dep for dep in edges[key] if dep in catalog)
    graph = {k: [d for d in edges[k] if d in selected] for k in sorted(selected)}
    try:
        return list(TopologicalSorter(graph).static_order())
    except CycleError as error:
        raise ValueError("research dependency cycle in the selected experiments") from error


def freeze_research_plan(plan: object) -> tuple[dict[str, Any], str]:
    """Detach a bounded JSON plan and compute its portable version."""
    encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    if len(encoded) > PLAN_LIMIT:
        raise ValueError("research plan exceeds 2 MiB")
    frozen = json.loads(encoded)
    if not isinstance(frozen, dict):
        raise ValueError("expected a research plan object")
    return frozen, hashlib.sha256(encoded).hexdigest()


def bind_research(selection: object) -> dict[str, Any] | None:
    if selection is None:
        return None
    if (
        not isinstance(selection, dict)
        or not isinstance(selection.get("lab"), str)
        or set(selection) - {"plan", "lab", "plan_sha256"}
    ):
        raise ValueError("research needs a plan and a selected laboratory")
    frozen, digest = freeze_research_plan(selection.get("plan"))
    _, catalog, edges = _catalog(frozen)
    _selected_order(catalog, edges, selection["lab"])
    if selection.get("plan_sha256", digest) != digest:
        raise ValueError("research plan version mismatch")
    return {"lab": selection["lab"], "plan_sha256": digest, "plan": frozen}


def research_identity(bound: dict[str, Any]) -> dict[str, str]:
    return {
        "project": str(bound["plan"]["project"]),
        "lab": str(bound["lab"]),
        "plan_sha256": str(bound["plan_sha256"]),
    }


def bounded_observation(view: dict[str, Any]) -> dict[str, Any]:
    """Keep scoped, unresolved experiments visible before spending space on matched dependencies."""
    ranked = sorted(
        view["experiments"],
        key=lambda n: (n["state"] == "matched", not n["key"].startswith(view["lab"] + "/"), n["key"]),
    )
    entries = []
    used = 0
    for row in ranked:
        entry = {k: row[k] for k in ("key", "scope", "state", "manifest", "report_sha256", "next_question")}
        entry["affected_by"] = row["affected_by"][:6]
        entry["other_affected_dependencies"] = max(0, len(row["affected_by"]) - 6)
        chars = len(json.dumps(entry, ensure_ascii=False))
        if used + chars > 4200:
            continue
        entries.append(entry)
        used += chars
    return {**view, "experiments": entries, "omitted_experiments": view["experiment_count"] - len(entries)}


def project_lab(
    root: Path,
    plan: object,
    lab: str,
    *,
    read_allowed: Callable[[str], bool] | None = None,
    total_bytes_limit: int | None = None,
    max_bytes: int = 64 * 1024 * 1024,
    deadline: float | None = None,
    max_references: int | None = None,
) -> dict[str, Any]:
    """Read only the selected lab and its prerequisites; expose independent local and inherited gaps."""
    project, catalog, edges = _catalog(plan)
    order = _selected_order(catalog, edges, lab)
    issued = [k for k in order if catalog[k].get("report") is not None]
    specs = [catalog[k]["report"] for k in issued]
    references = [s.get("references") for s in specs]
    if not all(isinstance(refs, list) for refs in references):
        raise ValueError("research report references must be an array")
    if max_references is not None and sum(len(refs) for refs in references if isinstance(refs, list)) > max_references:
        return {
            "project": project,
            "lab": lab,
            "state_counts": {"unproven": len(order)},
            "experiments": [],
            "reason": "reference_budget",
            "experiment_count": len(order),
        }
    result = verify_references(
        root,
        specs,
        max_bytes=max_bytes,
        read_allowed=read_allowed,
        total_bytes_limit=total_bytes_limit,
        deadline=deadline,
    )
    observations: dict[str, dict[str, Any]] = {}
    bound_reports = dict(zip(issued, result["reports"], strict=True))
    for key in order:
        report = bound_reports.get(key, {"status": "unpublished", "references": [], "sha256": None, "manifest": None})
        node = catalog[key]
        counts = Counter(row["status"] for row in report["references"])
        if report["status"] == "unpublished":
            local = "unpublished"
        elif report["status"] == "manifest_changed" or counts["changed"]:
            local = "changed"
        elif report["status"] != "checked" or not counts or counts["unproven"]:
            local = "unproven"
        else:
            local = "matched"
        affected = [
            dep
            for dep, pinned in edges[key].items()
            if dep not in observations
            or observations[dep]["state"] != "matched"
            or catalog[dep]["report"]["sha256"] != pinned
        ]
        recorded = report.get("published_dependencies")
        if "published_producer" in report:
            producer = report["published_producer"]
            expected = {"project": project, "lab": node["lab"], "experiment": node["id"]}
            if (
                not isinstance(producer, dict)
                or any(producer.get(k) != v for k, v in expected.items())
                or report["published_scope"] != node["scope"]
                or not isinstance(recorded, list)
            ):
                local = "unproven"
            else:
                published_inputs: dict[str, str] = {}
                try:
                    for edge in recorded:
                        if not isinstance(edge, dict):
                            raise ValueError("invalid published dependency")
                        target = _key(edge)
                        pin = edge.get("sha256")
                        if target in published_inputs or not isinstance(pin, str) or not _DIGEST.fullmatch(pin):
                            raise ValueError("missing or ambiguous published dependency pin")
                        published_inputs[target] = pin
                except ValueError:
                    local = "unproven"
                affected = sorted(
                    set(affected)
                    | {
                        target
                        for target in set(edges[key]) | set(published_inputs)
                        if edges[key].get(target) != published_inputs.get(target)
                    }
                )
        recheck = node.get("recheck")
        if (
            local == "unpublished"
            and isinstance(recheck, dict)
            and isinstance(recheck.get("changed_dependencies"), list)
        ):
            affected = sorted(
                set(affected) | {name for name in recheck["changed_dependencies"] if isinstance(name, str)}
            )
            local = "needs_recheck"
        observations[key] = {
            "key": key,
            "scope": node["scope"][:300],
            "report_sha256": report["sha256"],
            "manifest": report["manifest"],
            "reference_counts": dict(counts),
            "local": local,
            "state": "needs_recheck" if affected and local == "matched" else local,
            "affected_by": affected,
            "next_question": node.get("next_question", "")[:240],
        }
    return {
        "project": project,
        "lab": lab,
        "experiment_count": len(order),
        "state_counts": dict(Counter(n["state"] for n in observations.values())),
        "experiments": list(observations.values()),
    }


def observe_bound_lab(
    root: Path,
    selection: dict[str, Any],
    *,
    read_enabled: bool,
    read_allowed: Callable[[str], bool],
    authorize: Callable[[], None] | None,
    deadline: float | None,
) -> dict[str, Any]:
    """Bound one role-frame inspection. Ordinary decisions reuse this dated observation."""
    identity = research_identity(selection)
    if not read_enabled:
        return {**identity, "reason": "read_not_granted", "state_counts": {"unproven": 1}, "experiments": []}
    seconds = 5.0 if deadline is None else min(5.0, max(0.0, deadline - time.time()))
    try:
        if authorize is not None:
            authorize()
        view = project_lab(
            root,
            selection["plan"],
            selection["lab"],
            read_allowed=read_allowed,
            max_bytes=8 * 1024 * 1024,
            total_bytes_limit=64 * 1024 * 1024,
            deadline=time.monotonic() + seconds,
            max_references=384,
        )
        if authorize is not None:
            authorize()
        return {**identity, **bounded_observation(view)}
    except (ValueError, OSError) as error:
        return {**identity, "reason": type(error).__name__, "state_counts": {"unproven": 1}, "experiments": []}
