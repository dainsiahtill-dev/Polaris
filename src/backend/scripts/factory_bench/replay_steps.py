#!/usr/bin/env python3
"""Director-replay sandbox — velocity harness (2026-06-15).

Freeze a project's post-CE construction steps ONCE, then replay only the
Director+QA segment against them — skipping the slow PM-planning + CE-fission
cloud round-trips (MiniMax, 300s timeout, ~9.7k reasoning tokens per PM task).
Each Director/write-stage fix then validates in minutes, not the ~6-30min full
chain (``run_market_chain.py``).

USAGE
    # 1. capture (run AFTER a real full chain that fissioned the steps):
    python replay_steps.py capture --workspace <ws> --out <fixture.json>
    # 2. replay (iterate Director/write fixes fast):
    python replay_steps.py replay --workspace <ws> --from <fixture.json>

CAVEAT (loud by design): a fixture FREEZES depends_on / construction_step /
ledger serialization from its capture run. RE-CAPTURE after ANY change to CE
fission / step_splitter, otherwise replay faithfully reproduces the OLD fission.
Replay validates Director/write-stage fixes ONLY.

This is meta-tooling for the factory-bench self-test rig — not business code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Keys that carry control-plane / cognitive-runtime lifecycle state which, if
# re-seeded, triggers a receipt side-effect that fails closed (service.py:557).
_STRIP_KEY_SUFFIXES = ("_required",)
_STRIP_KEY_EXACT = frozenset(
    {
        "last_cognitive_runtime_lifecycle",
        "cognitive_runtime_receipt_ids",
    }
)
_STRIP_KEY_PREFIXES = ("cognitive_runtime_",)


def _bootstrap_env(workspace: str) -> tuple[str, str]:
    """Mirror run_market_chain.py's env bootstrap; return (workspace_full, cache_root_full)."""
    workspace_full = os.path.abspath(os.path.expanduser(workspace))
    os.environ.setdefault("KERNELONE_WORKSPACE", workspace_full)

    from polaris.bootstrap.config import get_settings

    os.environ.setdefault("KERNELONE_RUNTIME_CACHE_ROOT", str(get_settings().runtime_base))
    from polaris.kernelone.storage import resolve_ramdisk_root
    from polaris.kernelone.storage.io_paths import build_cache_root

    ramdisk_root = resolve_ramdisk_root(None)
    cache_root_full = build_cache_root(ramdisk_root, workspace_full) or ""
    return workspace_full, cache_root_full


def _strip_control_keys(mapping: Any) -> dict[str, Any]:
    """Drop cognitive-runtime / *_required keys so re-seeding does not fail closed."""
    if not isinstance(mapping, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in mapping.items():
        key_str = str(key)
        low = key_str.lower()
        if low in _STRIP_KEY_EXACT:
            continue
        if any(low.startswith(prefix) for prefix in _STRIP_KEY_PREFIXES):
            continue
        if any(low.endswith(suffix) for suffix in _STRIP_KEY_SUFFIXES):
            continue
        cleaned[key_str] = value
    return cleaned


def _normalize_seed(row: dict[str, Any]) -> dict[str, Any]:
    """Map a query_status item dict into a replay-seed dict (stage forced to pending_exec)."""
    payload = _strip_control_keys(row.get("payload"))
    # payload must not be empty (contracts.py:157) — inject a sentinel.
    if not payload:
        payload = {"replay_seed": True}
    metadata = _strip_control_keys(row.get("metadata"))
    depends_on = tuple(str(dep) for dep in (row.get("depends_on") or ()) if str(dep))
    return {
        "task_id": str(row.get("task_id") or ""),
        "trace_id": str(row.get("trace_id") or ""),
        "run_id": str(row.get("run_id") or ""),
        "source_role": str(row.get("source_role") or "chief_engineer"),
        "payload": payload,
        "metadata": metadata,
        "priority": str(row.get("priority") or "medium"),
        "plan_id": str(row.get("plan_id") or ""),
        "plan_revision_id": str(row.get("plan_revision_id") or ""),
        "root_task_id": str(row.get("root_task_id") or ""),
        "parent_task_id": str(row.get("parent_task_id") or ""),
        "is_leaf": bool(row.get("is_leaf")),
        "depends_on": depends_on,
        "requirement_digest": str(row.get("requirement_digest") or ""),
        "constraint_digest": str(row.get("constraint_digest") or ""),
        "change_policy": str(row.get("change_policy") or ""),
        "compensation_group_id": str(row.get("compensation_group_id") or ""),
        # Provenance only (NOT re-seeded): the original stage/status, so a human
        # can see what the capture run did with this step.
        "_captured_stage": str(row.get("stage") or ""),
        "_captured_status": str(row.get("status") or ""),
    }


def capture_steps(workspace_full: str, out_path: str) -> int:
    """Dump the construction steps (Director-stage lineage) to a replay fixture."""
    from polaris.cells.runtime.task_market.internal.service import get_task_market_service
    from polaris.cells.runtime.task_market.public.contracts import QueryTaskMarketStatusV1

    service = get_task_market_service()
    result = service.query_status(QueryTaskMarketStatusV1(workspace=workspace_full, include_payload=True, limit=10000))
    # Keep the construction-step lineage: leaf steps (Director executes these)
    # UNION their non-leaf parents (DAG grouping for QA aggregation).
    kept = [
        _normalize_seed(dict(row))
        for row in result.items
        if isinstance(row, dict) and (bool(row.get("is_leaf")) or str(row.get("parent_task_id") or ""))
    ]
    leaves = sum(1 for step in kept if step["is_leaf"])
    fixture = {
        "project": Path(workspace_full).name,
        "workspace": workspace_full,
        "step_count": len(kept),
        "leaf_count": leaves,
        "steps": kept,
    }
    out_full = os.path.abspath(os.path.expanduser(out_path))
    os.makedirs(os.path.dirname(out_full) or ".", exist_ok=True)
    with open(out_full, "w", encoding="utf-8") as fh:
        json.dump(fixture, fh, ensure_ascii=False, indent=2)
    print(
        f"[replay-capture] project={fixture['project']} steps={len(kept)} leaves={leaves} -> {out_full}",
        flush=True,
    )
    if not kept:
        print("[replay-capture] WARNING: no construction steps captured — run a full chain first.", flush=True)
        return 1
    return 0


def seed_steps(
    workspace_full: str,
    steps: list[dict[str, Any]],
    *,
    only: set[str] | None = None,
) -> tuple[str, ...]:
    """Re-publish the captured leaf steps at pending_exec; return seeded leaf task ids.

    ``only`` restricts seeding to a subset of task_ids — the key velocity lever:
    Director (not CE) is the wall-clock floor, so iterating a fix on ONE
    representative step (e.g. a from-scratch create leaf with no deps) costs
    ~minutes instead of re-running all N steps. A step whose ``depends_on`` is not
    in the seeded set is warned about (its chain prerequisite is absent).
    """
    from polaris.cells.runtime.task_market.internal.service import get_task_market_service
    from polaris.cells.runtime.task_market.public.contracts import PublishTaskWorkItemCommandV1

    service = get_task_market_service()
    seeded: list[str] = []
    selected = [step for step in steps if step.get("is_leaf") and (only is None or step["task_id"] in only)]
    selected_ids = {step["task_id"] for step in selected}
    for step in selected:
        missing_deps = [dep for dep in step["depends_on"] if dep not in selected_ids]
        if missing_deps:
            print(
                f"[replay] WARNING: seeding {step['task_id']} but its depends_on {missing_deps} "
                "are not in the seeded set — it will not be claimable until they exist.",
                flush=True,
            )
        command = PublishTaskWorkItemCommandV1(
            workspace=workspace_full,
            trace_id=step["trace_id"],
            run_id=step["run_id"],
            task_id=step["task_id"],
            stage="pending_exec",
            source_role=step["source_role"],
            payload=dict(step["payload"]),
            priority=step["priority"],
            metadata=dict(step["metadata"]),
            plan_id=step["plan_id"],
            plan_revision_id=step["plan_revision_id"],
            root_task_id=step["root_task_id"],
            parent_task_id=step["parent_task_id"],
            is_leaf=True,
            depends_on=tuple(step["depends_on"]),
            requirement_digest=step["requirement_digest"],
            constraint_digest=step["constraint_digest"],
            change_policy=step["change_policy"],
            compensation_group_id=step["compensation_group_id"],
        )
        service.publish_work_item(command)
        seeded.append(step["task_id"])
    return tuple(seeded)


def replay(
    workspace_full: str,
    cache_root_full: str,
    fixture_path: str,
    *,
    only: set[str] | None = None,
) -> int:
    """Fresh workspace + market, seed frozen steps, drive Director+QA only."""
    os.environ.setdefault("KERNELONE_TASK_MARKET_MODE", "mainline-full")
    os.environ.setdefault("KERNELONE_CE_STEP_FISSION", "1")
    # A frozen skeleton+fillN chain is a deep depends_on DAG; the default 2 drain
    # cycles under-drain it. Replay's whole job is to drain the Director DAG, so
    # default generously (still overridable from the environment).
    os.environ.setdefault("KERNELONE_TASK_MARKET_MAINLINE_FULL_MAX_CYCLES", "12")

    fixture_full = os.path.abspath(os.path.expanduser(fixture_path))
    with open(fixture_full, encoding="utf-8") as fh:
        fixture = json.load(fh)
    steps = list(fixture.get("steps") or [])

    from polaris.kernelone.storage.io_paths import resolve_artifact_path

    # Fresh workspace (remove prior product files) + fresh market (clean claims).
    for entry in sorted(Path(workspace_full).iterdir()):
        if entry.is_file() and entry.suffix.lower() in {".html", ".js", ".css", ".md", ".json", ".py"}:
            if entry.name == "AGENTS.md":
                continue
            entry.unlink()
    import shutil

    market_dir = resolve_artifact_path(workspace_full, cache_root_full, "runtime/task_market")
    shutil.rmtree(market_dir, ignore_errors=True)

    seeded = seed_steps(workspace_full, steps, only=only)
    print(
        f"[replay] project={fixture.get('project')} seeded {len(seeded)}/{fixture.get('leaf_count')} leaf steps "
        f"at pending_exec{' (subset: ' + ','.join(sorted(only)) + ')' if only else ''} — "
        "RE-CAPTURE after any CE/step_splitter change.",
        flush=True,
    )
    if not seeded:
        print("[replay] ERROR: no leaf steps to seed — re-capture the fixture.", flush=True)
        return 1

    run_id = steps[0].get("run_id") or "pm-replay-00001"
    from polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline import (
        _run_inline_task_market_consumers,
    )

    result = _run_inline_task_market_consumers(
        workspace_full=workspace_full,
        run_id=str(run_id),
        iteration=1,
        published_task_ids=seeded,
        analysis_runner=None,
    )
    print(f"[replay] director+qa drive result: {json.dumps(result, ensure_ascii=False)[:600]}", flush=True)
    return 0 if result.get("ok") is not False else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Director-replay sandbox (velocity harness)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="dump construction steps to a replay fixture")
    cap.add_argument("--workspace", required=True)
    cap.add_argument("--out", required=True)

    rep = sub.add_parser("replay", help="seed frozen steps and run Director+QA only")
    rep.add_argument("--workspace", required=True)
    rep.add_argument("--from", dest="fixture", required=True)
    rep.add_argument(
        "--steps",
        default="",
        help="comma-separated leaf task_ids to seed (default: all). Director is the wall-clock "
        "floor, so iterating a fix on ONE create leaf (e.g. a dep-free step) is the real speedup.",
    )

    args = parser.parse_args()
    workspace_full, cache_root_full = _bootstrap_env(args.workspace)
    os.chdir(workspace_full)

    if args.cmd == "capture":
        return capture_steps(workspace_full, args.out)
    if args.cmd == "replay":
        only = {s.strip() for s in str(args.steps).split(",") if s.strip()} or None
        return replay(workspace_full, cache_root_full, args.fixture, only=only)
    return 2


if __name__ == "__main__":
    sys.exit(main())
