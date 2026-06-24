#!/usr/bin/env python3
"""Factory-bench runner — drive the FULL Polaris role chain per project.

For each project in ``projects_v2.json`` (L1→L12, sequential — the local vLLM
is a shared single GPU, so this runner IS the load mutex):

1. create a fresh workspace directory;
2. hand the project brief to the Polaris role chain (PM→Chief Engineer→
   Director→QA) headlessly;
3. collect generated artifacts (plan/blueprint docs, QA verdicts, code);
4. run the project's deterministic checks (``factory_audit``) and append a
   schema-stamped audit record.

Benchmark discipline (memory: benchmark-run-discipline): one project at a
time, ``--max-failed`` early stop, audit + root-cause before continuing.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid as _uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import urlparse

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")

from polaris.cells.factory.pipeline.internal.bench_gates import (
    aggregate_goal_audit,
    apply_factory_bench_failure_taxonomy,
    build_llm_route_audit,
    build_real_run_gate,
    collect_llm_events,
    resolve_expected_llm_bindings,
)
from polaris.cells.factory.pipeline.public.service import (
    load_run_ledger_projection,
    persist_real_run_gate_ledger,
    summarize_run_ledger_projection,
)
from polaris.kernelone.benchmark.factory_audit import (
    aggregate_factory_audits,
    build_factory_audit_record,
)
from polaris.kernelone.storage import resolve_runtime_path, resolve_storage_roots
from scripts.factory_bench.backend_fingerprint import (
    build_run_backend_metadata,
    check_backend_freshness,
)
from scripts.factory_bench.factory_http_client import (
    _http_post_json as _shared_http_post_json,
    cancel_factory_run,
    get_audit_bundle,
    start_factory_run,
    wait_run_until_terminal,
)

_logger = logging.getLogger(__name__)

_FIXTURE = Path(__file__).resolve().parent / "projects_v2.json"
_BACKEND_ROOT = Path("/home/dains/Documents/polaris/src/backend")
_REPO_ROOT = _BACKEND_ROOT.parent.parent
FACTORY_BENCH_REQUIRED_LLM_ROLES = ("pm", "chief_engineer", "director", "qa")
_LAUNCHER_INSTANCE_MODES = {"observed", "isolated"}
_BENCH_SESSION_REPORTING_MODES = {"auto", "shared", "off"}


def _sanitize_run_id(raw: str | None) -> str:
    """Return a filesystem-safe run_id.

    If *raw* is non-empty after stripping, replace any character outside
    ``[A-Za-z0-9._-]`` with ``-`` and collapse consecutive dashes.
    If *raw* is empty/None, generate a stable uuid4 hex.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw or "").strip()).strip("-")
    return cleaned if cleaned else _uuid.uuid4().hex[:12]


def _resolve_bench_work_dir(raw_work_dir: str) -> Path:
    """Resolve the bench output root before deriving project workspaces."""
    raw_value = str(raw_work_dir or "").strip()
    if not raw_value:
        raise ValueError("--work-dir must not be empty")
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    resolved = path.resolve()
    if resolved == _REPO_ROOT:
        raise ValueError("--work-dir must not resolve to the Polaris repository root")
    return resolved


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _director_resume_plan_tasks(workspace: Path) -> list[dict[str, Any]]:
    payload = _load_json_object(Path(resolve_runtime_path(str(workspace), "runtime/tasks/plan.json")))
    tasks = payload.get("tasks")
    return [item for item in tasks if isinstance(item, dict)] if isinstance(tasks, list) else []


def _director_resume_task_files(task_dir: Path) -> list[Path]:
    try:
        return sorted(
            path for path in task_dir.glob("task_*.json") if path.is_file() and not path.name.endswith(".session.json")
        )
    except OSError:
        return []


def _director_resume_has_taskboard(workspace: Path) -> bool:
    task_dir = Path(resolve_runtime_path(str(workspace), "runtime/tasks"))
    return bool(_director_resume_task_files(task_dir))


def _director_resume_workspace_slug(workspace_key: str) -> str:
    match = re.match(r"^(?P<slug>.+)-[0-9a-f]{12}$", workspace_key)
    return str(match.group("slug")) if match else workspace_key


def _director_resume_legacy_task_dirs(workspace: Path) -> list[Path]:
    roots = resolve_storage_roots(str(workspace))
    current_task_dir = Path(resolve_runtime_path(str(workspace), "runtime/tasks")).resolve()
    slug = _director_resume_workspace_slug(str(roots.workspace_key))
    runtime_project_bases = [Path(roots.runtime_projects_root)]
    runtime_project_bases.extend(Path(path) for path in globals().get("_RUNTIME_PROJECT_BASES", ()))
    candidates: list[Path] = []
    with contextlib.suppress(OSError):
        for runtime_projects_root in dict.fromkeys(runtime_project_bases):
            if not runtime_projects_root.exists():
                continue
            for project_root in runtime_projects_root.glob(f"{slug}-*"):
                task_dir = project_root / "runtime" / "tasks"
                if task_dir.resolve() == current_task_dir:
                    continue
                if (task_dir / "plan.json").is_file() and _director_resume_task_files(task_dir):
                    candidates.append(task_dir)
    return sorted(candidates, key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)


def _director_resume_taskboard_score(task_dir: Path) -> tuple[int, int, float]:
    task_files = _director_resume_task_files(task_dir)
    plan = _load_json_object(task_dir / "plan.json")
    tasks = plan.get("tasks")
    planned_count = len(tasks) if isinstance(tasks, list) else 0
    blueprint_dir = task_dir.parent / "blueprints"
    blueprint_count = 0
    with contextlib.suppress(OSError):
        blueprint_count = len([path for path in blueprint_dir.glob("ce_*.json") if path.is_file()])
    mtime = max((path.stat().st_mtime for path in [task_dir / "plan.json", *task_files] if path.exists()), default=0.0)
    return (blueprint_count, min(planned_count, len(task_files)), mtime)


def _director_resume_reset_task_payload(payload: dict[str, Any]) -> dict[str, Any]:
    reset = dict(payload)
    blocked_by = reset.get("blocked_by")
    if not isinstance(blocked_by, list):
        blocked_by = reset.get("blockedBy") if isinstance(reset.get("blockedBy"), list) else []
    reset["status"] = "blocked" if blocked_by else "pending"
    reset["claimed_by"] = None
    reset["assignee"] = ""
    reset["started_at"] = None
    reset["completed_at"] = None
    reset["claimed_at"] = None
    reset["result_summary"] = ""
    reset["error_message"] = None
    metadata = reset.get("metadata")
    if isinstance(metadata, dict):
        cleaned_metadata = dict(metadata)
        for key in (
            "adapter_phase",
            "claim_attempt",
            "claimed_at",
            "claimed_by",
            "director_claimable_task_ids",
            "factory_stage",
            "last_claimed_by",
            "last_context_summary",
            "last_execution_error",
            "last_execution_summary",
            "resume_available",
            "resume_count",
            "resume_state",
            "runtime_execution",
            "workflow_run_id",
        ):
            cleaned_metadata.pop(key, None)
        reset["metadata"] = cleaned_metadata
    return reset


def _rehydrate_director_resume_taskboard(workspace: Path) -> str:
    target_dir = Path(resolve_runtime_path(str(workspace), "runtime/tasks"))
    if _director_resume_plan_tasks(workspace) and _director_resume_has_taskboard(workspace):
        _reset_current_director_resume_taskboard(workspace, target_dir=target_dir)
        return ""
    candidates = sorted(
        _director_resume_legacy_task_dirs(workspace),
        key=_director_resume_taskboard_score,
        reverse=True,
    )
    for source_dir in candidates:
        plan_payload = _load_json_object(source_dir / "plan.json")
        if not isinstance(plan_payload.get("tasks"), list) or not _director_resume_task_files(source_dir):
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_dir / "plan.json", target_dir / "plan.json")
        copied: list[str] = ["plan.json"]
        for task_file in _director_resume_task_files(source_dir):
            payload = _load_json_object(task_file)
            if not payload:
                continue
            normalized_payload = _director_resume_reset_task_payload(payload)
            (target_dir / task_file.name).write_text(
                json.dumps(normalized_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            copied.append(task_file.name)
        max_id = source_dir / ".max_id"
        if max_id.is_file():
            shutil.copy2(max_id, target_dir / ".max_id")
            copied.append(".max_id")
        evidence = {
            "schema_version": "factory.director_resume_taskboard_rehydration.v1",
            "source": "factory_bench",
            "source_task_dir": str(source_dir),
            "target_task_dir": str(target_dir),
            "copied_files": copied,
            "reset_statuses": "all_task_records",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (target_dir / "director_resume_rehydration.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(source_dir)
    return ""


def _reset_current_director_resume_taskboard(workspace: Path, *, target_dir: Path | None = None) -> dict[str, Any]:
    """Reset existing Director task rows to a clean pre-Director claimable state."""
    task_dir = target_dir or Path(resolve_runtime_path(str(workspace), "runtime/tasks"))
    task_files = _director_resume_task_files(task_dir)
    if not task_files:
        return {}

    reset_files: list[str] = []
    skipped_files: list[str] = []
    deleted_session_files: list[str] = []
    for task_file in task_files:
        payload = _load_json_object(task_file)
        if not payload:
            skipped_files.append(task_file.name)
            continue
        normalized_payload = _director_resume_reset_task_payload(payload)
        task_file.write_text(
            json.dumps(normalized_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        reset_files.append(task_file.name)

    with contextlib.suppress(OSError):
        for session_file in sorted(task_dir.glob("task_*.session.json")):
            if not session_file.is_file():
                continue
            session_file.unlink()
            deleted_session_files.append(session_file.name)

    evidence = {
        "schema_version": "factory.director_resume_taskboard_reset.v1",
        "source": "factory_bench",
        "workspace": str(workspace),
        "target_task_dir": str(task_dir),
        "reset_files": reset_files,
        "skipped_files": skipped_files,
        "deleted_session_files": deleted_session_files,
        "reset_statuses": "all_task_records",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "director_resume_reset.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return evidence


def _director_resume_has_ce_blueprint(workspace: Path) -> bool:
    candidates = [workspace / ".polaris" / "blueprints" / "latest.review.json"]
    state_dir = Path(resolve_runtime_path(str(workspace), "runtime/state/blueprints"))
    with contextlib.suppress(OSError):
        candidates.extend(path for path in state_dir.glob("*.review.json") if path.is_file())
    for path in candidates:
        payload = _load_json_object(path)
        blueprints = payload.get("blueprints")
        try:
            generated_count = int(payload.get("generated_blueprints") or 0)
        except (TypeError, ValueError):
            generated_count = 0
        if generated_count > 0 or (isinstance(blueprints, list) and bool(blueprints)):
            return True
    return False


def _director_resume_snapshot_manifest(workspace: Path) -> Path:
    return workspace / ".polaris" / "factory_snapshots" / "pre_director" / "manifest.json"


def _director_resume_snapshot_ready(workspace: Path) -> bool:
    payload = _load_json_object(_director_resume_snapshot_manifest(workspace))
    return str(payload.get("snapshot_kind") or "") == "pre_director_workspace"


def _director_resume_declared_delivery_paths(tasks: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for task in tasks:
        for key in ("target_files", "scope_paths"):
            raw = task.get(key)
            if isinstance(raw, str):
                values = [raw]
            elif isinstance(raw, list):
                values = [str(item) for item in raw if str(item).strip()]
            else:
                values = []
            for value in values:
                normalized = value.replace("\\", "/").strip().strip("/")
                if normalized and normalized not in paths:
                    paths.append(normalized)
    return paths


def _director_resume_delivery_files(workspace: Path, tasks: list[dict[str, Any]]) -> list[str]:
    allowed_pre_director_inputs = {
        ".catalog_meta.json",
        "requirements.md",
    }
    candidates = {
        "package.json",
        "tsconfig.json",
        "index.html",
        "README.md",
        "src",
        "tests",
    }
    candidates.update(_director_resume_declared_delivery_paths(tasks))
    candidates.difference_update(allowed_pre_director_inputs)
    existing: list[str] = []
    root = workspace.resolve()
    for candidate in sorted(candidates):
        path = (root / candidate).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            existing.append(candidate)
            continue
        if path.exists():
            existing.append(candidate)
    return existing


def _prepare_director_resume_workspace(workspace: Path) -> None:
    if _director_resume_has_ce_blueprint(workspace):
        _rehydrate_director_resume_taskboard(workspace)
    tasks = _director_resume_plan_tasks(workspace)
    missing: list[str] = []
    if not tasks:
        missing.append("runtime/tasks/plan.json")
    if not _director_resume_has_taskboard(workspace):
        missing.append("runtime/tasks/task_*.json")
    if not _director_resume_has_ce_blueprint(workspace):
        missing.append(".polaris/blueprints/latest.review.json")
    if missing:
        raise ValueError("Director-only resume missing evidence: " + ", ".join(missing))
    if _director_resume_snapshot_ready(workspace):
        return
    delivery_files = _director_resume_delivery_files(workspace, tasks)
    if delivery_files:
        raise ValueError(
            "Director-only resume snapshot is missing and workspace already has delivery files: "
            + ", ".join(delivery_files[:12])
        )
    from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor

    OrchestrationStageExecutor(workspace)._create_pre_director_snapshot(run_id="bench_director_resume_seed")


def _next_immutable_json_path(path: Path) -> Path:
    """Return the first available immutable JSON path for *path*.

    If *path* does not exist, return *path*; otherwise try ``<stem>.2.json``,
    ``<stem>.3.json``, … until an unused slot is found.
    """
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}.{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> Path:
    """Write *payload* as UTF-8 JSON to *path*, never overwriting an existing file.

    If *path* already exists, write to ``<stem>.2.json``, ``<stem>.3.json``, …
    using the first available slot.  Returns the path actually written.
    """
    target = _next_immutable_json_path(path)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    return target


# Artifact layout (2026-06-11 recon): most chain artifacts live OUTSIDE the
# workspace, under <runtime_base>/.polaris/projects/<workspace_key>/runtime/.
# The runner snapshots that projects dir before/after the run and diffs to find
# this run's runtime dir. In-workspace artifacts live under <ws>/.polaris/.
# The chain's storage layout resolves runtime to ramdisk first
# (KERNELONE_STATE_TO_RAMDISK defaults on -> /dev/shm), then cache roots —
# and the project dir is DETERMINISTICALLY keyed "<workspace-name-lower>-<hash>".
# Expert audit 2026-06-12: guessing one base + falling back to "newest dir"
# matched a stale pytest leftover and produced false-positive plan artifacts.
# Scan all known bases but accept ONLY name-keyed matches for this workspace.
_RUNTIME_PROJECT_BASES = (
    Path("/dev/shm/.polaris/projects"),
    Path(os.path.expanduser("~/.cache/polaris")) / ".polaris" / "projects",
    Path(os.path.expanduser("~/.cache/kernelone")) / ".polaris" / "projects",
)

_RUNTIME_ARTIFACT_GLOBS: dict[str, tuple[str, ...]] = {
    "plan": ("contracts/pm_tasks.contract.json", "contracts/plan.md", "results/pm.report.md"),
    "blueprint": (
        "blueprints/*.json",
        "contracts/chief_engineer.blueprint.json",
        "runs/*/contracts/chief_engineer.blueprint.json",
    ),
    "verdict": (
        "runs/*/qa/integration_qa.result.json",
        "results/integration_qa.result.json",
        "qa/report.json",
        "workspace/qa/*.report.json",
        "workspace/roles/qa/*/report.json",
    ),
    "director_result": ("runs/*/results/director.result.json", "results/director.result.json"),
}
_WORKSPACE_ARTIFACT_GLOBS: dict[str, tuple[str, ...]] = {
    "plan": (".polaris/docs/product/plan.md", ".polaris/docs/product/requirements.md", ".polaris/docs/*.md"),
    "blueprint": (".polaris/blueprints/*",),
    "verdict": (
        ".polaris/qa/*.report.json",
        ".polaris/roles/qa/*/report.json",
        ".polaris/runtime/qa/report.json",
    ),
    "director_result": (),
}


def _resolve_catalog_path(path: str | Path, *, base_dir: Path | None = None) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (base_dir or Path(__file__).resolve().parent) / candidate
    return candidate.resolve()


def _load_project_catalog(path: Path, *, seen: set[Path] | None = None) -> list[dict[str, Any]]:
    seen = set(seen or set())
    if path in seen:
        raise ValueError(f"factory-bench catalog extends cycle: {path}")
    seen.add(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    projects: list[dict[str, Any]] = []
    extends = data.get("extends") or []
    if isinstance(extends, str):
        extends = [extends]
    if not isinstance(extends, list):
        raise ValueError(f"factory-bench catalog {path} has invalid extends")
    for parent in extends:
        if not isinstance(parent, str) or not parent.strip():
            raise ValueError(f"factory-bench catalog {path} has invalid extends entry")
        projects.extend(_load_project_catalog(_resolve_catalog_path(parent, base_dir=path.parent), seen=seen))
    raw_projects = data.get("projects")
    if not isinstance(raw_projects, list):
        raise ValueError(f"factory-bench catalog {path} missing projects[]")
    projects.extend(item for item in raw_projects if isinstance(item, dict))
    return projects


def load_projects(projects_file: str | Path | None = None) -> list[dict[str, Any]]:
    projects = _load_project_catalog(_resolve_catalog_path(projects_file or _FIXTURE))
    seen: set[str] = set()
    duplicates: list[str] = []
    for project in projects:
        project_id = str(project.get("id") or "").strip()
        if not project_id:
            raise ValueError("factory-bench catalog contains a project without id")
        if project_id in seen:
            duplicates.append(project_id)
        seen.add(project_id)
    if duplicates:
        raise ValueError(
            "factory-bench catalog contains duplicate project id(s): " + ", ".join(sorted(set(duplicates)))
        )
    return projects


def resolve_runtime_dir_for_workspace(workspace: Path) -> Path | None:
    """Find this workspace's runtime dir by its deterministic name key."""
    runtime_dirs = resolve_runtime_dirs_for_workspace(workspace)
    return runtime_dirs[0] if runtime_dirs else None


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


_RUNTIME_WORKSPACE_EVIDENCE_RELATIVE_PATHS = (
    "events/director.llm.events.jsonl",
    "events/pm.llm.events.jsonl",
    "events/task_runtime.execution.jsonl",
    "events/roles.kernel.events.jsonl",
    "results/director.result.json",
    "results/integration_qa.result.json",
)


def _file_mentions_workspace(path: Path, workspace_text: str) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            content = handle.read(2_000_000)
    except (OSError, RuntimeError, UnicodeDecodeError):
        return False
    return workspace_text in content


def _runtime_dir_matches_workspace(runtime_dir: Path, workspace: Path) -> bool:
    """Return true when a runtime dir contains evidence for this exact workspace."""

    try:
        workspace_text = str(workspace.resolve())
    except (OSError, RuntimeError, ValueError):
        workspace_text = str(workspace)
    candidates: list[Path] = []
    candidates.extend(runtime_dir / rel_path for rel_path in _RUNTIME_WORKSPACE_EVIDENCE_RELATIVE_PATHS)
    events_dir = runtime_dir / "events"
    if events_dir.is_dir():
        with contextlib.suppress(OSError):
            candidates.extend(sorted(events_dir.glob("*.jsonl"))[:24])
    return any(path.is_file() and _file_mentions_workspace(path, workspace_text) for path in candidates)


def resolve_runtime_dirs_for_workspace(workspace: Path) -> list[Path]:
    """Find all runtime dirs for this workspace across ramdisk/cache bases."""
    key_prefix = workspace.name.lower() + "-"
    matches: set[Path] = set()
    for base in _RUNTIME_PROJECT_BASES:
        try:
            matches.update(e for e in base.iterdir() if e.is_dir() and e.name.startswith(key_prefix))
        except OSError:
            continue
    if not matches:
        return []
    runtime_dirs: set[Path] = set()
    for match in matches:
        runtime = match / "runtime"
        if runtime.is_dir():
            runtime_dirs.add(runtime)
        elif match.is_dir():
            runtime_dirs.add(match)
    sorted_runtime_dirs = sorted(runtime_dirs, key=_safe_mtime, reverse=True)
    matching_runtime_dirs = [
        runtime_dir for runtime_dir in sorted_runtime_dirs if _runtime_dir_matches_workspace(runtime_dir, workspace)
    ]
    return matching_runtime_dirs or sorted_runtime_dirs


def discover_artifacts(
    workspace: Path,
    runtime_dirs: Path | list[Path] | tuple[Path, ...] | None,
) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for kind, patterns in _WORKSPACE_ARTIFACT_GLOBS.items():
        hits: list[str] = []
        for pattern in patterns:
            hits.extend(
                f"ws:{p.relative_to(workspace)}"
                for p in workspace.glob(pattern)
                if p.is_file() and _is_valid_artifact_match(kind, p)
            )
        found[kind] = sorted(set(hits))
    if runtime_dirs is None:
        runtime_dir_list: list[Path] = []
    elif isinstance(runtime_dirs, Path):
        runtime_dir_list = [runtime_dirs]
    else:
        runtime_dir_list = list(runtime_dirs)
    multi_runtime = len(runtime_dir_list) > 1
    for runtime_dir in runtime_dir_list:
        for kind, patterns in _RUNTIME_ARTIFACT_GLOBS.items():
            hits = list(found.get(kind, []))
            for pattern in patterns:
                hits.extend(
                    (
                        f"rt:{runtime_dir.parent.name}/{p.relative_to(runtime_dir)}"
                        if multi_runtime
                        else f"rt:{p.relative_to(runtime_dir)}"
                    )
                    for p in runtime_dir.glob(pattern)
                    if p.is_file() and _is_valid_artifact_match(kind, p)
                )
            found[kind] = sorted(set(hits))
    return found


def _is_valid_artifact_match(kind: str, path: Path) -> bool:
    if kind != "verdict":
        return True
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and ("verdict" in payload or "passed" in payload)


def brief_goal_overlap(brief: str, goal: str) -> float:
    """Char-bigram Jaccard between the project brief and the PM contract goal.

    Cross-project session contamination (2026-06-12) shipped a PERFECT
    calculator for a tic-tac-toe brief — QA green, exit 0. Requirement-output
    consistency is therefore a first-class audit dimension: a near-zero
    overlap means the chain built the wrong product, however well.
    """

    def bigrams(text: str) -> set[str]:
        compact = "".join(text.split()).lower()
        return {compact[i : i + 2] for i in range(len(compact) - 1)} if len(compact) > 1 else set()

    a, b = bigrams(brief), bigrams(goal)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def read_chain_results(runtime_dir: Path | None) -> dict[str, Any]:
    """Parse the chain's own result artifacts (QA verdict, director summary).

    Existence alone is misleading — integration QA writes ran=false when it is
    SKIPPED (director_failures_present), so the audit must surface content.
    """
    summary: dict[str, Any] = {
        "qa_ran": None,
        "qa_passed": None,
        "qa_reason": "",
        "director": {},
        "contract_goal": "",
        "exit_class": "",
    }
    if runtime_dir is None:
        return summary
    # Preferred source: the chain's own machine-readable terminal summary
    # (chain-summary/1, written by orchestration_engine after reconciliation).
    chain_summary_path = runtime_dir / "results" / "chain_summary.json"
    try:
        chain_summary = json.loads(chain_summary_path.read_text(encoding="utf-8"))
        summary["exit_class"] = str(chain_summary.get("exit_class") or "")
        qa_block = chain_summary.get("integration_qa")
        if isinstance(qa_block, dict):
            summary["qa_ran"] = bool(qa_block.get("ran"))
            summary["qa_passed"] = bool(qa_block.get("passed"))
            summary["qa_reason"] = str(qa_block.get("reason") or "")
        director_block = chain_summary.get("director")
        if isinstance(director_block, dict):
            summary["director"] = {
                key: director_block.get(key)
                for key in ("total", "successes", "failures", "blocked")
                if key in director_block
            }
    except (OSError, json.JSONDecodeError):
        pass
    qa_path = runtime_dir / "results" / "integration_qa.result.json"
    if summary["qa_ran"] is None:
        try:
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            summary["qa_ran"] = bool(qa.get("ran"))
            summary["qa_passed"] = bool(qa.get("passed"))
            summary["qa_reason"] = str(qa.get("reason") or qa.get("skip_reason") or "")
        except (OSError, json.JSONDecodeError):
            pass
    contract_path = runtime_dir / "contracts" / "pm_tasks.contract.json"
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        summary["contract_goal"] = str(contract.get("overall_goal") or "")[:160]
    except (OSError, json.JSONDecodeError):
        summary["contract_goal"] = ""
    director_path = runtime_dir / "results" / "director.result.json"
    if not summary["director"]:
        try:
            director = json.loads(director_path.read_text(encoding="utf-8"))
            summary["director"] = {
                key: director.get(key) for key in ("total", "successes", "failures", "blocked") if key in director
            }
        except (OSError, json.JSONDecodeError):
            pass
    return summary


def read_chain_results_from_runtime_dirs(runtime_dirs: list[Path]) -> dict[str, Any]:
    """Merge terminal chain facts from all runtime candidates, newest first."""
    merged = read_chain_results(None)
    for runtime_dir in runtime_dirs:
        current = read_chain_results(runtime_dir)
        if merged["qa_ran"] is None and current["qa_ran"] is not None:
            merged["qa_ran"] = current["qa_ran"]
            merged["qa_passed"] = current["qa_passed"]
            merged["qa_reason"] = current["qa_reason"]
        if not merged["director"] and current["director"]:
            merged["director"] = current["director"]
        if not merged["contract_goal"] and current["contract_goal"]:
            merged["contract_goal"] = current["contract_goal"]
        if not merged["exit_class"] and current["exit_class"]:
            merged["exit_class"] = current["exit_class"]
    return merged


_EXIT_CLASS_BY_CODE = {0: "clean", 4: "director_partial", 5: "qa_failed"}
_NON_TERMINAL_CHAIN_ERRORS = {"start_failed", "workspace_switch_failed", "event_wait_timeout"}


def grade_chain_state(chain_results: dict[str, Any], exit_code: Any) -> str:
    """Three-state chain verdict: clean / partial / fail.

    Prefers the chain's own exit_class (chain-summary/1); falls back to the
    graded exit code for pre-summary runs.
    """
    exit_class = str(chain_results.get("exit_class") or "")
    if not exit_class and isinstance(exit_code, int):
        exit_class = _EXIT_CLASS_BY_CODE.get(exit_code, "hard_failed")
    if exit_class == "clean":
        return "clean"
    if exit_class in {"director_partial", "qa_failed"}:
        return "partial"
    return "fail"


def _chain_reached_terminal(chain: dict[str, Any]) -> bool:
    """Return whether the runner has a definitive backend terminal state."""
    chain_error = str(chain.get("error") or "")
    if chain_error in _NON_TERMINAL_CHAIN_ERRORS:
        return False
    return not chain.get("_runner_exception")


def _build_non_terminal_real_run_gate(*, chain_phase: str, chain_status: str) -> dict[str, Any]:
    """Fail closed when the chain has not reached a stable audit point."""
    phase = chain_phase or chain_status or "unknown"
    detail = f"chain_terminal=false; phase={phase}; status={chain_status or 'unknown'}"
    return {
        "ok": False,
        "skipped": True,
        "summary": f"real run gate skipped: chain did not reach terminal state ({phase})",
        "requirements": {
            "chain_terminal": {
                "ok": False,
                "detail": detail,
            },
            "artifact_landed": {
                "ok": False,
                "detail": "not evaluated because the Polaris chain was non-terminal",
            },
            "environment_prepared": {
                "ok": False,
                "detail": "not evaluated because the Polaris chain was non-terminal",
            },
            "build_test_lint_ran": {
                "ok": False,
                "detail": "not evaluated because the Polaris chain was non-terminal",
            },
            "entrypoint_smoke": {
                "ok": False,
                "detail": "not evaluated because the Polaris chain was non-terminal",
                "kind": "",
            },
        },
        "commands": [],
        "entrypoint": {
            "ok": False,
            "kind": "",
            "detail": "not run because the Polaris chain did not reach terminal state",
        },
    }


def _resolve_bench_cache_root(workspace: Path) -> str:
    """Resolve the runtime cache_root for ``workspace`` using the same storage
    layout the chain subprocess writes to. Returns "" if storage roots cannot
    be resolved (e.g. workspace has no docs sentinel — bench then skips WS
    emit and degrades to file-only)."""
    try:
        from polaris.kernelone.storage.io_paths import build_cache_root
    except ImportError:
        return ""
    ramdisk = os.environ.get("KERNELONE_STATE_TO_RAMDISK") or "/dev/shm"
    try:
        return build_cache_root(ramdisk, str(workspace))
    except (OSError, ValueError) as exc:  # pragma: no cover - defensive
        print(f"[factory-bench] cache_root resolution failed: {exc}", file=sys.stderr, flush=True)
        return ""


# Module-level state populated by main() so the emit helper can optionally
# forward internal bench events to a shared Factory HTTP observation backend.
# Empty values mean "no shared observation wiring": the helper degrades to
# local JSONL only and never makes a network call. This is not the canonical
# runtime path for isolated project instances; each isolated backend owns its
# own runtime.v2 stream.
_BENCH_BACKEND: dict[str, str] = {"backend_url": "", "session_id": "", "token": ""}
_BENCH_OBSERVATION_CIRCUIT: dict[str, str] = {"disabled_reason": ""}


def configure_bench_backend(backend_url: str, session_id: str, token: str = "") -> None:
    """Set the active backend URL + session id + token (called once by main())."""
    _BENCH_BACKEND["backend_url"] = backend_url
    _BENCH_BACKEND["session_id"] = session_id
    _BENCH_BACKEND["token"] = token
    _BENCH_OBSERVATION_CIRCUIT["disabled_reason"] = ""


def _bench_observation_disabled() -> bool:
    return bool(str(_BENCH_OBSERVATION_CIRCUIT.get("disabled_reason") or "").strip())


def _disable_bench_observation(reason: str) -> None:
    if _bench_observation_disabled():
        return
    _BENCH_OBSERVATION_CIRCUIT["disabled_reason"] = str(reason or "shared observation failed").strip()
    print(
        f"[factory-bench] shared bench observation disabled: {_BENCH_OBSERVATION_CIRCUIT['disabled_reason']}",
        file=sys.stderr,
        flush=True,
    )


def _emit_bench_event(
    *,
    workspace: Path,
    project_id: str,
    level: int,
    name: str,
    summary: str = "",
    meta: dict[str, Any] | None = None,
    cache_root: str | None = None,
) -> bool:
    """Append a bench-level event to the workspace's runtime.events.jsonl
    AND forward it to the Factory HTTP backend (if wired by main()).

    Local path: writes to ``<cache_root>/runs/<run_id>/events/runtime.events.jsonl``
    (resolved via ``latest_run.json``) so the Polaris WS bridge at
    ``/v2/ws/runtime`` can stream it to the ContextOS real-time dashboard.

    Shared observation path: when main() explicitly wired a shared bench
    session, POSTs the event to ``/v2/factory/bench/sessions/{id}/events``.
    This bridge is internal-test-only and best-effort. It must never be
    treated as the isolated project's runtime source of truth.

    Returns True if at least one of the two paths succeeded; False only when
    neither produced a record (e.g. local path has no run_id and backend
    is not wired). All failures are non-fatal: the bench continues.
    """
    try:
        from polaris.kernelone.events import emit_event
    except ImportError:
        # If we cannot import the local emitter, we can still push to the
        # Factory HTTP backend below — do NOT bail out before that.
        emit_event = None  # type: ignore[assignment]

    payload_meta: dict[str, Any] = dict(meta or {})
    payload_meta.setdefault("project_id", str(project_id))
    payload_meta.setdefault("level", int(level))
    payload_meta.setdefault("source", "factory-bench")

    # --- Local JSONL (WS-bridge side channel): best-effort, requires
    # a Polaris cache_root + latest_run.json. The real bench runtime
    # uses a plain parent work_dir (no .polaris), so this path often
    # legitimately has nothing to write into. The Factory HTTP push
    # below is the canonical real-time path and must run regardless.
    local_ok = False
    if not cache_root:
        cache_root = _resolve_bench_cache_root(workspace)
    if cache_root:
        pointer = Path(cache_root) / "latest_run.json"
        if pointer.is_file():
            try:
                pointer_payload = json.loads(pointer.read_text(encoding="utf-8"))
                run_id = str(pointer_payload.get("run_id") or "").strip()
            except (OSError, ValueError):
                run_id = ""
            if run_id:
                events_path = Path(cache_root) / "runs" / run_id / "events" / "runtime.events.jsonl"
                if emit_event is not None:
                    try:
                        emit_event(
                            str(events_path),
                            kind="event",
                            actor="factory-bench",
                            name=f"factory_bench.{name}",
                            summary=summary,
                            meta=payload_meta,
                        )
                        local_ok = True
                    except (OSError, ValueError, TypeError) as exc:
                        print(
                            f"[factory-bench] WS emit failed: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )

    # --- Shared Factory HTTP observation push. Runs independently of cache_root
    # when explicitly enabled, but remains best-effort and circuit-broken so a
    # busy main backend cannot stall isolated project execution.
    backend_ok = False
    backend_url = _BENCH_BACKEND.get("backend_url", "")
    backend_sid = _BENCH_BACKEND.get("session_id", "")
    if backend_url and backend_sid and not _bench_observation_disabled():
        backend_ok = _push_bench_event_to_backend(
            backend_url=backend_url,
            session_id=backend_sid,
            event_type=f"factory_bench.{name}",
            name=f"factory_bench.{name}",
            actor="factory-bench",
            summary=summary,
            meta=payload_meta,
            token=_BENCH_BACKEND.get("token", ""),
        )

    return local_ok or backend_ok


def _factory_role_from_phase(phase: str) -> str:
    token = str(phase or "").strip().lower()
    if token in {"pending", "intake", "planning", "pm_planning", "docs_check"}:
        return "pm"
    if "chief" in token or "blueprint" in token or token in {"ce", "ce_review"}:
        return "chief_engineer"
    if "director" in token or token in {"implementation", "mutation", "execution", "handover"}:
        return "director"
    if "qa" in token or "verification" in token or "quality" in token:
        return "qa"
    return "unknown"


def _emit_factory_phase_event(
    *,
    bench_workspace: Path,
    project_workspace: Path,
    project_id: str,
    level: int,
    title: str,
    status: str,
    phase_payload: dict[str, Any],
    cache_root: str | None = None,
) -> bool:
    phase = str(phase_payload.get("phase") or "").strip()
    run_status = str(phase_payload.get("status") or status or "").strip()
    if not phase and not run_status:
        return False
    role = _factory_role_from_phase(phase)
    run_id = str(phase_payload.get("run_id") or "").strip()
    summary_parts = [project_id]
    if role != "unknown":
        summary_parts.append(role)
    if phase:
        summary_parts.append(f"phase={phase}")
    if run_status:
        summary_parts.append(f"status={run_status}")
    project_workspace_full = str(project_workspace.resolve())
    meta: dict[str, Any] = {
        "project_id": project_id,
        "level": int(level),
        "title": title,
        "workspace": project_workspace_full,
        "workspace_path": project_workspace_full,
        "project_workspace": project_workspace_full,
        "phase": phase,
        "status": run_status,
        "role": role,
    }
    if run_id:
        meta["run_id"] = run_id
    return _emit_bench_event(
        workspace=bench_workspace,
        project_id=project_id,
        level=level,
        name="project.phase",
        summary=" ".join(part for part in summary_parts if part),
        meta=meta,
        cache_root=cache_root,
    )


def _emit_factory_task_runtime_event(
    *,
    bench_workspace: Path,
    project_workspace: Path,
    project_id: str,
    level: int,
    title: str,
    phase_payload: dict[str, Any],
    event_payload: dict[str, Any],
    cache_root: str | None = None,
) -> bool:
    project_workspace_full = str(project_workspace.resolve())
    task_id = str(event_payload.get("task_id") or "").strip()
    task_status = str(event_payload.get("status") or "").strip()
    event_type = str(event_payload.get("event_type") or "").strip()
    director_run_id = str(event_payload.get("run_id") or "").strip()
    factory_run_id = str(phase_payload.get("run_id") or event_payload.get("factory_run_id") or "").strip()
    summary_parts = [project_id, "director"]
    if task_id:
        summary_parts.append(f"task={task_id}")
    if event_type:
        summary_parts.append(event_type)
    if task_status:
        summary_parts.append(f"status={task_status}")
    meta: dict[str, Any] = {
        "project_id": project_id,
        "level": int(level),
        "title": title,
        "workspace": project_workspace_full,
        "workspace_path": project_workspace_full,
        "project_workspace": project_workspace_full,
        "phase": "director_dispatch",
        "status": task_status or str(phase_payload.get("status") or "running"),
        "role": "director",
        "task_id": task_id,
        "task_status": task_status,
        "task_runtime_event_type": event_type,
        "director_run_id": director_run_id,
        "run_id": factory_run_id,
        "session_id": str(event_payload.get("session_id") or "").strip(),
        "details": event_payload.get("details") if isinstance(event_payload.get("details"), dict) else {},
    }
    return _emit_bench_event(
        workspace=bench_workspace,
        project_id=project_id,
        level=level,
        name="project.task_runtime",
        summary=" ".join(part for part in summary_parts if part),
        meta={k: v for k, v in meta.items() if v not in (None, "")},
        cache_root=cache_root,
    )


# --- Bench subprocess -> backend HTTP client ---
#
# The bench runs in a terminal and posts lifecycle events to the Factory
# HTTP backend so the Factory front-end panel can stream them in real time.
# All helpers are fail-soft: a missing/unreachable backend must NEVER crash
# the bench run. The bench only does local JSONL emission in that case
# (which the WS bridge can still pick up if connected to the active
# workspace's runtime dir).


_DEFAULT_BACKEND_URL = "http://127.0.0.1:49977"
_DEFAULT_LOCAL_BACKEND_TOKEN = "polaris-local-dev"
_BENCH_HTTP_TIMEOUT_S = 10.0  # bumped from 2.0: cold-start 49977 can exceed 2s
_BENCH_OBSERVATION_HTTP_TIMEOUT_S = 1.5


def _resolve_polaris_home(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the Polaris home directory (``~/.polaris``).

    Resolution order mirrors the Electron ``resolvepolarisHome`` helper in
    ``config-paths.cjs``:

    1. ``KERNELONE_HOME`` env var — if set and already named ``.polaris``,
       use it directly; otherwise append ``.polaris``.
    2. ``~/.polaris`` (platform home).
    """
    active_env = env or os.environ
    home_override = str(active_env.get("KERNELONE_HOME") or "").strip()
    if home_override:
        expanded = Path(home_override).expanduser().resolve()
        if expanded.name.lower() == ".polaris":
            return expanded
        return expanded / ".polaris"
    return Path.home() / ".polaris"


def _desktop_backend_info_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the path to ``desktop-backend.json`` written by Electron."""
    return _resolve_polaris_home(env) / "runtime" / "desktop-backend.json"


def _read_desktop_backend_info(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read and parse ``desktop-backend.json``.

    Returns an empty dict on any failure (missing file, malformed JSON,
    permission errors).  This is a *read-only* helper — it never creates
    or modifies the file.
    """
    path = _desktop_backend_info_path(env)
    try:
        if not path.exists():
            _logger.debug("desktop-backend.json not found at %s", path)
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _logger.debug("desktop-backend.json found at %s (token source found)", path)
            return data
        return {}
    except (ValueError, OSError):
        _logger.debug("desktop-backend.json unreadable at %s (token source missing)", path)
        return {}


def _resolve_backend_url(explicit: str | None = None) -> str:
    """Pick a backend URL from arg > env > desktop-backend info > default.

    Priority:
    1. *explicit* argument
    2. ``KERNELONE_BACKEND_URL`` env
    3. ``FACTORY_BENCH_BACKEND_URL`` env
    4. ``desktop-backend.json`` → ``backend.baseUrl``
    5. ``_DEFAULT_BACKEND_URL`` (127.0.0.1:49977)
    """
    candidate = (
        (explicit or "").strip()
        or os.environ.get("KERNELONE_BACKEND_URL", "").strip()
        or os.environ.get("FACTORY_BENCH_BACKEND_URL", "").strip()
        or _desktop_backend_url_from_info()
    )
    return candidate.rstrip("/") or _DEFAULT_BACKEND_URL


def _desktop_backend_url_from_info() -> str:
    """Extract baseUrl from desktop-backend.json, or "" if absent."""
    info = _read_desktop_backend_info()
    backend = info.get("backend")
    if isinstance(backend, dict):
        return str(backend.get("baseUrl") or "").strip()
    return ""


def _is_local_backend_url(url: str) -> bool:
    """Return True when *url* targets a loopback backend."""
    try:
        parsed = urlparse(str(url or ""))
    except (TypeError, ValueError):
        return False
    hostname = str(parsed.hostname or "").lower()
    return hostname in {"127.0.0.1", "localhost", "::1"}


def _resolve_backend_token(explicit: str | None = None) -> str:
    """Pick a backend bearer token from arg > env > desktop-backend info.

    The Polaris factory router requires a Bearer token in the Authorization
    header (query tokens are intentionally rejected — see
    ``polaris.delivery.http.dependencies.require_auth``). The bench subprocess
    runs in a terminal and has no way to ask the Electron app for a token
    directly, so it reads the token Electron already persisted to
    ``desktop-backend.json`` as a fallback.

    Priority:
    1. *explicit* argument
    2. ``FACTORY_BENCH_BACKEND_TOKEN`` env
    3. ``KERNELONE_TOKEN`` env
    4. ``KERNELONE_BACKEND_TOKEN`` env
    5. ``desktop-backend.json`` → ``backend.token``

    Returns "" when no token is configured (the bench then makes
    unauthenticated requests, which is fine for dev mode with auth disabled).
    """
    token = (
        (explicit or "").strip()
        or os.environ.get("FACTORY_BENCH_BACKEND_TOKEN", "").strip()
        or os.environ.get("KERNELONE_TOKEN", "").strip()
        or os.environ.get("KERNELONE_BACKEND_TOKEN", "").strip()
        or _desktop_backend_token_from_info()
    )
    if not token and _is_local_backend_url(_resolve_backend_url()):
        token = _DEFAULT_LOCAL_BACKEND_TOKEN
    if token:
        _logger.debug("backend token source found")
    else:
        _logger.debug("backend token source missing — using unauthenticated requests")
    return token


def _desktop_backend_token_from_info() -> str:
    """Extract token from desktop-backend.json, or "" if absent."""
    info = _read_desktop_backend_info()
    backend = info.get("backend")
    if isinstance(backend, dict):
        return str(backend.get("token") or "").strip()
    return ""


def _http_post_json(
    url: str,
    body: dict[str, Any],
    *,
    timeout_s: float = _BENCH_HTTP_TIMEOUT_S,
    token: str = "",
) -> dict[str, Any] | None:
    return _shared_http_post_json(url, body, timeout_s=timeout_s, token=token)


def _push_bench_session_to_backend(
    *,
    backend_url: str,
    work_dir: str,
    project_ids: list[str],
    total: int,
    metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
    token: str = "",
) -> str | None:
    """Register a bench session with the Factory backend.

    Returns the assigned ``session_id`` on success, ``None`` on any failure
    (no backend / network error / non-2xx / malformed body). The bench run
    must continue in all cases; the only side effect of failure is that
    the Factory panel cannot show this run.
    """
    payload: dict[str, Any] = {
        "work_dir": str(work_dir),
        "project_ids": list(project_ids),
        "total": int(total),
        "metadata": dict(metadata or {}),
    }
    if session_id:
        payload["session_id"] = session_id
    response = _http_post_json(
        f"{backend_url}/v2/factory/bench/sessions",
        payload,
        timeout_s=_BENCH_OBSERVATION_HTTP_TIMEOUT_S,
        token=token,
    )
    if not isinstance(response, dict):
        return None
    sid = str(response.get("session_id") or "").strip()
    return sid or None


def _ensure_bench_session(
    *,
    backend_url: str,
    work_dir: str,
    project_ids: list[str],
    total: int,
    metadata: dict[str, Any] | None = None,
    requested_session_id: str = "",
    token: str = "",
) -> str:
    """Register a bench session and return the usable session id.

    An explicit ``FACTORY_BENCH_SESSION_ID`` is still a real frontend contract:
    the Factory panel can subscribe to ``event.bench:<id>`` only after the
    backend has a durable session row for that id.
    """

    requested = str(requested_session_id or "").strip()
    if not backend_url:
        return requested
    registered = _push_bench_session_to_backend(
        backend_url=backend_url,
        work_dir=work_dir,
        project_ids=project_ids,
        total=total,
        metadata=metadata,
        session_id=requested or None,
        token=token,
    )
    return registered or requested


def _bench_record_counts(records: list[dict[str, Any]], *, total: int) -> dict[str, int]:
    passed = sum(1 for record in records if record.get("all_checks_passed"))
    failed = sum(1 for record in records if not record.get("all_checks_passed"))
    attempted = len(records)
    return {
        "total": int(total),
        "attempted": attempted,
        "passed": passed,
        "failed": failed,
        "pending": max(0, int(total) - attempted),
    }


def _push_bench_event_to_backend(
    *,
    backend_url: str,
    session_id: str,
    event_type: str,
    name: str | None = None,
    actor: str | None = None,
    summary: str | None = None,
    ok: bool | None = None,
    meta: dict[str, Any] | None = None,
    token: str = "",
) -> bool:
    """Append a bench event to the active session on the Factory backend."""
    if _bench_observation_disabled():
        return False
    payload: dict[str, Any] = {
        "type": str(event_type),
        "name": name,
        "actor": actor,
        "summary": summary,
        "ok": ok,
        "meta": dict(meta or {}),
    }
    response = _http_post_json(
        f"{backend_url}/v2/factory/bench/sessions/{session_id}/events",
        payload,
        timeout_s=_BENCH_OBSERVATION_HTTP_TIMEOUT_S,
        token=token,
    )
    if response is None:
        _disable_bench_observation(f"event POST failed: {event_type}")
        return False
    return response is not None and bool(response.get("appended", False))


def _push_bench_complete_to_backend(
    *,
    backend_url: str,
    session_id: str,
    success: bool = True,
    summary: dict[str, Any] | None = None,
    token: str = "",
) -> bool:
    """Mark a bench session complete (or failed) on the Factory backend."""
    if _bench_observation_disabled():
        return False
    payload: dict[str, Any] = {
        "success": bool(success),
        "summary": dict(summary or {}),
    }
    response = _http_post_json(
        f"{backend_url}/v2/factory/bench/sessions/{session_id}/complete",
        payload,
        timeout_s=_BENCH_OBSERVATION_HTTP_TIMEOUT_S,
        token=token,
    )
    if response is None:
        _disable_bench_observation("complete POST failed")
        return False
    return response is not None and bool(response.get("updated", False))


def _push_bench_progress_to_backend(
    *,
    backend_url: str,
    session_id: str,
    completed: int,
    failed: int,
    token: str = "",
) -> bool:
    """Push live per-project counters so the front-end sees real-time progress.

    Without this, ``session.completed`` / ``session.failed`` stay at the
    zero they had at registration time and the bench UI shows ``0/Y 通过``
    for the whole run. The bench subprocess must call this after every
    project so each project.finished (success or fail) increments the
    right counter and the Nats-JetStream/WebSocket snapshot reflects it on the next tick.
    """
    payload: dict[str, Any] = {
        "completed": int(completed),
        "failed": int(failed),
    }
    if _bench_observation_disabled():
        return False
    response = _http_post_json(
        f"{backend_url}/v2/factory/bench/sessions/{session_id}/progress",
        payload,
        timeout_s=_BENCH_OBSERVATION_HTTP_TIMEOUT_S,
        token=token,
    )
    if response is None:
        _disable_bench_observation("progress POST failed")
        return False
    return response is not None and bool(response.get("updated", False))


def _push_bench_workspace_to_backend(
    *,
    backend_url: str,
    workspace: str,
    token: str = "",
    attempts: int = 3,
    retry_delay_seconds: float = 0.25,
) -> bool:
    """Switch the desktop backend to the project workspace before observation starts."""
    if not backend_url or not workspace:
        return False
    workspace_path = Path(workspace).expanduser()
    try:
        workspace_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _logger.warning("factory bench workspace switch skipped; cannot prepare workspace %s: %s", workspace, exc)
        return False
    target_workspace = workspace_path.resolve()
    workspace_payload = str(target_workspace)
    max_attempts = max(1, int(attempts))
    for attempt in range(max_attempts):
        response = _http_post_json(
            f"{backend_url}/settings",
            {"workspace": workspace_payload},
            token=token,
        )
        if isinstance(response, dict):
            returned_workspace = str(response.get("workspace") or response.get("workspace_path") or "").strip()
            if returned_workspace:
                try:
                    returned_path = Path(returned_workspace).expanduser().resolve()
                except (OSError, RuntimeError, ValueError) as exc:
                    _logger.warning(
                        "factory bench workspace switch rejected malformed response workspace=%r: %s",
                        returned_workspace,
                        exc,
                    )
                else:
                    if returned_path == target_workspace:
                        return True
                    _logger.warning(
                        "factory bench workspace switch mismatch: requested=%s returned=%s",
                        target_workspace,
                        returned_path,
                    )
        if attempt < max_attempts - 1 and retry_delay_seconds > 0:
            time.sleep(retry_delay_seconds)
    return False


def _register_bench_project_instance(
    *,
    bench_session_id: str,
    project_id: str,
    project_title: str,
    level: int,
    bench_workspace: Path,
    project_workspace: str,
    backend_url: str,
    backend_token: str,
) -> None:
    """Register bench project activity in the platform instance registry.

    This is discovery metadata for the Launcher only. factory_bench remains an
    internal stress harness and must not become a production fact source.
    """
    try:
        from polaris.cells.instances.internal.service import (
            InstanceRecord,
            InstanceRegistry,
            default_polaris_root,
            sanitize_instance_id,
        )
    except (ImportError, RuntimeError):
        return

    parsed_backend = urlparse(backend_url or "")
    parsed_frontend = urlparse(os.environ.get("FACTORY_BENCH_FRONTEND_URL", ""))
    backend_port = int(parsed_backend.port or 0)
    frontend_port = int(parsed_frontend.port or 0)
    if backend_port <= 0:
        return

    instance_id = sanitize_instance_id(
        f"{bench_session_id}-{project_id}" if bench_session_id else f"factory-bench-{project_id}"
    )
    record = InstanceRecord(
        instance_id=instance_id,
        name=f"{project_id} {project_title}".strip(),
        kind="bench_project",
        polaris_root=str(default_polaris_root()),
        workspace=project_workspace,
        runtime_root=str((Path(project_workspace) / "runtime").resolve()),
        backend_port=backend_port,
        frontend_port=frontend_port,
        backend_url=backend_url,
        frontend_url=os.environ.get("FACTORY_BENCH_FRONTEND_URL", ""),
        token=backend_token,
        backend_reload=False,
        frontend_vite=bool(frontend_port),
        start_frontend=bool(frontend_port),
        status="observed",
        backend_pid=None,
        frontend_pid=None,
        bench={
            "session_id": bench_session_id,
            "project_id": project_id,
            "level": level,
            "bench_workspace": str(bench_workspace),
            "registration_mode": "factory_bench_runner",
        },
        metadata={
            "registered_by": "factory_bench",
            "internal_test_only": True,
            "backend_binding": "shared_backend_workspace_switch",
        },
    )
    try:
        InstanceRegistry().save(record)
    except (OSError, RuntimeError, ValueError):
        return


def _default_launcher_instance_mode() -> str:
    raw = str(os.environ.get("FACTORY_BENCH_LAUNCHER_INSTANCE_MODE") or "isolated").strip().lower()
    return raw if raw in _LAUNCHER_INSTANCE_MODES else "isolated"


def _default_bench_session_reporting_mode() -> str:
    raw = str(os.environ.get("FACTORY_BENCH_SESSION_REPORTING") or "auto").strip().lower()
    return raw if raw in _BENCH_SESSION_REPORTING_MODES else "auto"


def _bench_session_backend_url(
    *,
    launcher_instance_mode: str,
    bench_session_reporting: str,
    backend_url: str,
) -> str:
    reporting = str(bench_session_reporting or "auto").strip().lower()
    launcher_mode = str(launcher_instance_mode or "isolated").strip().lower()
    if reporting == "off":
        return ""
    if reporting == "shared":
        return str(backend_url or "").rstrip("/")
    if launcher_mode == "observed":
        return str(backend_url or "").rstrip("/")
    return ""


def _bench_project_instance_id(*, bench_session_id: str, project_id: str) -> str:
    raw = f"{bench_session_id}-{project_id}" if bench_session_id else f"factory-bench-{project_id}"
    try:
        from polaris.cells.instances.internal.service import sanitize_instance_id
    except (ImportError, RuntimeError):
        return re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-").lower()[:80] or "factory-bench-project"
    return sanitize_instance_id(raw)


def _wait_backend_health(backend_url: str, token: str, *, timeout_s: float = 45.0) -> bool:
    deadline = time.time() + max(1.0, float(timeout_s))
    health_url = f"{str(backend_url or '').rstrip('/')}/health"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    while time.time() < deadline:
        try:
            request = urllib.request.Request(health_url, headers=headers)
            with urllib.request.urlopen(request, timeout=2.0) as response:
                if 200 <= int(response.status) < 300:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.5)
    return False


def _start_isolated_bench_project_instance(
    *,
    bench_session_id: str,
    project_id: str,
    project_title: str,
    level: int,
    bench_workspace: Path,
    project_workspace: str,
    backend_token: str,
) -> dict[str, Any] | None:
    """Start a project-scoped Polaris instance for internal factory_bench runs."""
    try:
        from polaris.cells.instances.internal.service import InstanceSupervisor, default_polaris_root
    except (ImportError, RuntimeError):
        return None

    token = backend_token or _DEFAULT_LOCAL_BACKEND_TOKEN
    try:
        instance = InstanceSupervisor().start_instance(
            {
                "instance_id": _bench_project_instance_id(
                    bench_session_id=bench_session_id,
                    project_id=project_id,
                ),
                "name": f"{project_id} {project_title}".strip(),
                "kind": "bench_project",
                "polaris_root": str(default_polaris_root()),
                "workspace": project_workspace,
                "runtime_root": str((Path(project_workspace) / "runtime").resolve()),
                "backend_port": None,
                "frontend_port": None,
                "token": token,
                "backend_reload": False,
                "frontend_vite": True,
                "start_frontend": True,
                "bench": {
                    "session_id": bench_session_id,
                    "project_id": project_id,
                    "level": level,
                    "bench_workspace": str(bench_workspace),
                    "registration_mode": "factory_bench_runner",
                },
                "metadata": {
                    "registered_by": "factory_bench",
                    "internal_test_only": True,
                    "backend_binding": "isolated_backend_instance",
                    "launcher_instance_mode": "isolated",
                },
            }
        )
    except (OSError, RuntimeError, ValueError):
        _logger.debug("factory bench isolated instance start failed", exc_info=True)
        return None
    if not _wait_backend_health(str(instance.get("backend_url") or ""), str(instance.get("token") or token)):
        metadata = instance.get("metadata")
        if isinstance(metadata, dict):
            metadata["backend_health"] = "starting"
    return instance


def _bench_gate(gate: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"gate": gate, "ok": bool(ok), "detail": detail}


def map_factory_run_to_chain_results(
    run_status: dict[str, Any],
    audit_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Translate /v2/factory/runs status + audit-bundle into the dict shape
    previously produced by read_chain_results()."""
    summary_raw = audit_bundle.get("summary_json") or {}
    summary_json: dict[str, Any]
    if isinstance(summary_raw, str):
        try:
            parsed_summary = json.loads(summary_raw)
        except ValueError:
            summary_json = {}
        else:
            summary_json = parsed_summary if isinstance(parsed_summary, dict) else {}
    elif isinstance(summary_raw, dict):
        summary_json = cast(dict[str, Any], summary_raw)
    else:
        summary_json = {}

    gates_raw = audit_bundle.get("gates") or run_status.get("gates") or []
    gates = gates_raw if isinstance(gates_raw, list) else []
    qa_gate: dict[str, Any] = next(
        (cast(dict[str, Any], g) for g in gates if isinstance(g, dict) and g.get("gate_name") == "quality_gate"),
        {},
    )
    qa_passed = bool(qa_gate.get("passed"))
    qa_ran = bool(qa_gate)

    status = str(run_status.get("status") or "").lower()
    phase = str(run_status.get("phase") or "").lower()
    metadata_raw = run_status.get("metadata")
    metadata: dict[str, Any] = cast(dict[str, Any], metadata_raw) if isinstance(metadata_raw, dict) else {}
    current_stage = str(metadata.get("current_stage") or run_status.get("current_stage") or "").lower()
    failed_stage = str(metadata.get("last_failed_stage") or "").lower()
    stage_hint = failed_stage or current_stage or phase

    exit_class = "hard_failed"
    if status == "completed" and qa_passed:
        exit_class = "clean"
    elif (status == "completed" and qa_ran and not qa_passed) or (status == "failed" and phase == "qa_gate"):
        exit_class = "qa_failed"
    elif status == "failed":
        if "pm" in stage_hint:
            exit_class = "pm_failed"
        elif "chief" in stage_hint or "engineer" in stage_hint:
            exit_class = "chief_engineer_failed"
        elif "director" in stage_hint:
            exit_class = "director_partial"
        elif "qa" in stage_hint or "quality" in stage_hint:
            exit_class = "qa_failed"

    director = summary_json.get("director") or {}
    if not director:
        events_tail_raw = audit_bundle.get("events_tail") or []
        events_tail = events_tail_raw if isinstance(events_tail_raw, list) else []
        for evt in reversed(events_tail):
            if (
                isinstance(evt, dict)
                and evt.get("stage") == "director_dispatch"
                and isinstance(evt.get("result"), dict)
            ):
                director = cast(dict[str, Any], evt["result"])
                break

    return {
        "qa_ran": qa_ran,
        "qa_passed": qa_passed,
        "qa_reason": qa_gate.get("message") or "",
        "director": {
            "total": director.get("total") if isinstance(director, dict) else None,
            "successes": director.get("successes") if isinstance(director, dict) else None,
            "failures": director.get("failures") if isinstance(director, dict) else None,
            "blocked": director.get("blocked") if isinstance(director, dict) else None,
        },
        "contract_goal": "",
        "exit_class": exit_class,
        "factory_stage_hint": stage_hint,
    }


def required_llm_roles_for_factory_record(
    *,
    chain: dict[str, Any],
    record: dict[str, Any],
) -> tuple[str, ...]:
    chain_results_raw = chain.get("chain_results")
    chain_results: dict[str, Any] = (
        cast(dict[str, Any], chain_results_raw) if isinstance(chain_results_raw, dict) else {}
    )
    start_from = (
        str(
            record.get("factory_bench_start_from")
            or record.get("start_from")
            or chain.get("start_from")
            or chain_results.get("factory_bench_start_from")
            or ""
        )
        .strip()
        .lower()
    )
    stage_hint = str(chain_results.get("factory_stage_hint") or "").strip().lower()
    terminal_status = chain.get("factory_terminal_status")
    if isinstance(terminal_status, dict):
        terminal_status_map = cast(dict[str, Any], terminal_status)
        terminal_metadata_raw = terminal_status_map.get("metadata")
        metadata: dict[str, Any] = (
            cast(dict[str, Any], terminal_metadata_raw) if isinstance(terminal_metadata_raw, dict) else {}
        )
        stage_hint = (
            str(
                metadata.get("last_failed_stage")
                or metadata.get("current_stage")
                or terminal_status_map.get("current_stage")
                or terminal_status_map.get("phase")
                or stage_hint
            )
            .strip()
            .lower()
        )
    exit_class = str(chain_results.get("exit_class") or "").strip().lower()
    director_result = chain_results.get("director")
    director_evidence = False
    if isinstance(director_result, dict):
        director_evidence = any(value not in (None, "", 0) for value in director_result.values())
    if start_from == "director":
        resume_roles = []
        if "director" in stage_hint or exit_class in {"director_partial", "qa_failed", "clean"} or director_evidence:
            resume_roles.append("director")
        if (
            bool(chain_results.get("qa_ran"))
            or "qa" in stage_hint
            or "quality" in stage_hint
            or exit_class in {"qa_failed", "clean"}
        ):
            resume_roles.append("qa")
        return tuple(role for role in FACTORY_BENCH_REQUIRED_LLM_ROLES if role in set(resume_roles))

    roles: list[str] = ["pm"]
    pm_only_stage = "pm" in stage_hint and "chief" not in stage_hint and "director" not in stage_hint
    if exit_class == "pm_failed" or pm_only_stage:
        return tuple(roles)
    roles.append("chief_engineer")
    if exit_class == "chief_engineer_failed" or "chief" in stage_hint or "engineer" in stage_hint:
        return tuple(dict.fromkeys(roles))
    if "director" in stage_hint or exit_class in {"director_partial", "qa_failed", "clean"} or director_evidence:
        roles.append("director")
    if (
        bool(chain_results.get("qa_ran"))
        or "qa" in stage_hint
        or "quality" in stage_hint
        or exit_class in {"qa_failed", "clean"}
    ):
        roles.append("qa")
    return tuple(role for role in FACTORY_BENCH_REQUIRED_LLM_ROLES if role in set(roles))


def build_factory_bench_gates(record: dict[str, Any], chain: dict[str, Any]) -> list[dict[str, Any]]:
    """Build fail-closed full-chain gates for the factory-bench verdict.

    The per-project deterministic checks measure artifact shape/content only.
    A benchmark run must not pass if the Polaris chain failed, QA was skipped or
    failed, required governance artifacts are absent, or the product was likely
    for a different brief.
    """

    chain_results = record.get("chain_results")
    if not isinstance(chain_results, dict):
        chain_results = {}
    chain_state = str(record.get("chain_state") or "")
    chain_exit_code = chain.get("exit_code")
    gates = [
        _bench_gate(
            "plan_artifact_present",
            bool(record.get("has_plan_doc")),
            "plan artifact discovered" if record.get("has_plan_doc") else "plan artifact missing",
        ),
        _bench_gate(
            "blueprint_artifact_present",
            bool(record.get("has_blueprint_doc")),
            "blueprint artifact discovered" if record.get("has_blueprint_doc") else "blueprint artifact missing",
        ),
        _bench_gate(
            "qa_verdict_artifact_present",
            bool(record.get("has_qa_verdict")),
            "QA verdict artifact discovered" if record.get("has_qa_verdict") else "QA verdict artifact missing",
        ),
        _bench_gate(
            "chain_clean",
            chain_state == "clean" and chain_exit_code == 0,
            f"chain_state={chain_state or 'unknown'} exit_code={chain_exit_code}",
        ),
        _bench_gate(
            "integration_qa_passed",
            chain_results.get("qa_ran") is True and chain_results.get("qa_passed") is True,
            f"qa_ran={chain_results.get('qa_ran')} qa_passed={chain_results.get('qa_passed')}",
        ),
        _bench_gate(
            "wrong_product_guard",
            not bool(record.get("wrong_product_suspect")),
            (
                "no wrong-product signal"
                if not record.get("wrong_product_suspect")
                else f"wrong-product suspect match={record.get('wrong_product_match') or 'unknown'}"
            ),
        ),
    ]
    # Backend fingerprint freshness gate (fail-closed)
    backend_freshness = record.get("backend_freshness")
    if isinstance(backend_freshness, dict):
        gates.append(
            _bench_gate(
                "stale_backend_or_unknown",
                bool(backend_freshness.get("ok")),
                str(backend_freshness.get("detail") or "backend freshness check missing detail"),
            )
        )
    else:
        gates.append(
            _bench_gate(
                "stale_backend_or_unknown",
                False,
                "backend freshness gate missing; cannot verify backend is current",
            )
        )

    real_run_gate = record.get("real_run_gate")
    if isinstance(real_run_gate, dict):
        gates.append(
            _bench_gate(
                "real_run_gate",
                bool(real_run_gate.get("ok")),
                str(real_run_gate.get("summary") or "real run gate missing summary"),
            )
        )
    else:
        gates.append(_bench_gate("real_run_gate", False, "real run gate missing"))
    run_ledger_status = summarize_run_ledger_projection(record.get("run_ledger_projection"))
    gates.append(
        _bench_gate(
            "run_ledger_projection",
            bool(run_ledger_status.get("ok")),
            str(run_ledger_status.get("detail") or "run ledger status missing detail"),
        )
    )
    llm_route_audit = record.get("llm_route_audit")
    if isinstance(llm_route_audit, dict):
        gates.append(
            _bench_gate(
                "llm_route_audit",
                bool(llm_route_audit.get("ok")),
                str(llm_route_audit.get("summary") or "LLM route audit missing summary"),
            )
        )
    else:
        gates.append(_bench_gate("llm_route_audit", False, "LLM route audit missing"))
    return gates


def build_bench_backend_audit_context(
    backend_url: str,
    *,
    backend_token: str = "",
    workspace: str = "",
) -> dict[str, Any]:
    """Build backend freshness and trace metadata for every bench record."""
    freshness = check_backend_freshness(
        backend_url,
        token=backend_token,
        backend_root=_BACKEND_ROOT,
    )
    backend_info = freshness.get("backend_info")
    backend_info_dict: dict[str, Any] = backend_info if isinstance(backend_info, dict) else {}
    metadata = build_run_backend_metadata(
        backend_url,
        token_source="configured" if backend_token else "missing",
        workspace=workspace,
        expected_fingerprint=str(freshness.get("expected_fingerprint") or ""),
        actual_fingerprint=str(freshness.get("actual_fingerprint") or ""),
        backend_pid=backend_info_dict.get("pid") if isinstance(backend_info_dict.get("pid"), int) else None,
        backend_startup_time=str(backend_info_dict.get("startup_time") or ""),
        fingerprint_source=str(backend_info_dict.get("source") or ""),
    )
    return {
        "backend_freshness": freshness,
        "backend_metadata": metadata,
    }


def apply_factory_bench_gates(record: dict[str, Any], chain: dict[str, Any]) -> None:
    """Fold full-chain gates into ``all_checks_passed`` in-place."""

    static_checks_passed = bool(record.get("static_checks_passed", record.get("all_checks_passed")))
    record["run_ledger_projection_status"] = summarize_run_ledger_projection(record.get("run_ledger_projection"))
    gates = build_factory_bench_gates(record, chain)
    record["static_checks_passed"] = static_checks_passed
    record["factory_gates"] = gates
    record["all_checks_passed"] = static_checks_passed and all(gate["ok"] for gate in gates)


def _build_language_runnable_contract(primary_language: str) -> str:
    """Build a runnable-language contract specifying how the project must be executable."""
    lang = primary_language.lower().strip()
    if lang == "typescript":
        return (
            "## Language-Specific Runnable Contract (TypeScript)\n"
            "- 必须包含 `package.json` 且定义 `scripts.start` / `scripts.build` 脚本。\n"
            "- `npm install && npm run build` 必须成功。\n"
            "- 必须包含 `tsconfig.json`。\n"
            "- `tsc --noEmit` 必须通过。\n"
        )
    if lang == "python":
        return (
            "## Language-Specific Runnable Contract (Python)\n"
            "- 必须包含 `requirements.txt` 或 `pyproject.toml`。\n"
            "- `python -m pip install -r requirements.txt` 或等价命令必须成功。\n"
        )
    if lang == "rust":
        return "## Language-Specific Runnable Contract (Rust)\n- 必须包含 `Cargo.toml`。\n- `cargo build` 必须成功。\n"
    return ""


def _build_source_tree_contract(primary_language: str, project_type: str) -> str:
    """Build explicit source tree structure requirements for the given language/type.

    This ensures the PM -> Chief Engineer -> Director chain creates src/
    directories and core source files rather than only scaffolding files like
    package.json and tsconfig.json.
    """
    lang = primary_language.lower().strip()
    ptype = project_type.lower().strip()

    sections: list[str] = []
    sections.append("## Source Tree Structure Contract (MANDATORY)\n")
    sections.append(
        "PM -> Chief Engineer -> Director 必须按以下结构创建源代码文件, 仅生成 package.json / tsconfig.json 等配置文件"
        "不算完成, 必须包含核心业务逻辑源码:\n"
    )

    if lang == "typescript":
        sections.append(
            "- 必须包含 `src/` 目录, 核心业务逻辑在 `src/` 下的 `.ts` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/models/` — 数据模型/实体定义\n"
            "  - `src/engine/` 或 `src/core/` — 核心引擎/逻辑\n"
            "  - `src/index.ts` — 应用入口\n"
            "- 必须包含 `tests/` 目录下的至少一个 `.test.ts` 测试文件。\n"
            '- tsconfig.json 的 `include` 必须包含 `"src/**/*.ts"`。\n'
        )
    elif lang == "javascript":
        sections.append(
            "- 必须包含 `src/` 目录, 核心业务逻辑在 `src/` 下的 `.js` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/models/` — 数据模型/实体定义\n"
            "  - `src/engine/` 或 `src/core/` — 核心引擎/逻辑\n"
            "  - `src/index.js` — 应用入口\n"
            "- 必须包含 `tests/` 目录下的至少一个测试文件。\n"
        )
    elif lang == "python":
        sections.append(
            "- 必须包含 `src/` 目录(或项目级 Python 包), 核心业务逻辑在 `.py` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/models/` — 数据模型/实体定义\n"
            "  - `src/engine/` 或 `src/core/` — 核心引擎/逻辑\n"
            "  - `src/__init__.py` 或项目入口 `.py` 文件\n"
            "- 必须包含 `tests/` 目录下的至少一个 `test_*.py` 测试文件。\n"
        )
    elif lang == "go":
        sections.append(
            "- 必须包含 `src/` 或项目级 Go 包, 核心业务逻辑在 `.go` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/models/` 或 `models/` — 数据模型/实体定义\n"
            "  - `src/engine/` 或 `engine/` — 核心引擎/逻辑\n"
            "  - `main.go` 或 `cmd/` — 应用入口\n"
            "- 必须包含 `*_test.go` 测试文件。\n"
        )
    elif lang == "rust":
        sections.append(
            "- 必须包含 `src/` 目录, 核心业务逻辑在 `src/` 下的 `.rs` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/models/` 或 `src/model.rs` — 数据模型/实体定义\n"
            "  - `src/engine/` 或 `src/lib.rs` — 核心引擎/逻辑\n"
            "  - `src/main.rs` — 应用入口\n"
            "- 必须包含 `tests/` 目录下的集成测试或 `#[test]` 单元测试。\n"
        )
    elif lang == "cpp":
        sections.append(
            "- 必须包含 `src/` 目录, 核心业务逻辑在 `.cpp`/`.hpp` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/models/` 或 `include/models/` — 数据模型/实体定义\n"
            "  - `src/engine/` 或 `src/core/` — 核心引擎/逻辑\n"
            "  - `src/main.cpp` — 应用入口\n"
            "- 必须包含 `tests/` 目录下的测试文件。\n"
        )
    elif lang == "java":
        sections.append(
            "- 必须包含 `src/main/java/` 目录, 核心业务逻辑在 `.java` 文件中。\n"
            "- 至少包含以下类型的源文件:\n"
            "  - `src/main/java/**/models/` — 数据模型/实体定义\n"
            "  - `src/main/java/**/engine/` 或 `core/` — 核心引擎/逻辑\n"
            "  - `src/main/java/**/App.java` — 应用入口\n"
            "- 必须包含 `src/test/java/` 下的测试文件。\n"
        )
    else:
        sections.append(
            f"- primary_language={lang!r} — 请按该语言惯例创建 src/ 目录结构, "
            "包含核心业务逻辑源码、数据模型和测试文件。\n"
        )

    if "simulation" in ptype or "game" in ptype or "interactive" in ptype:
        sections.append(
            "- simulation/game/interactive 项目必须包含一个可渲染的场景/引擎核心文件 "
            "(如 `src/engine/renderer.ts`, `src/core/simulation.py` 等)。\n"
        )

    sections.append(
        "\n**重要**: Director 任务的 target_files 必须覆盖 src/ 下的源文件, "
        "不能只包含 package.json / tsconfig.json / index.html 等脚手架文件。\n"
    )
    return "".join(sections)


def _build_feature_keywords_contract(feature_keywords: list[str]) -> str:
    """Build a contract section requiring feature keywords in generated source code."""
    if not feature_keywords:
        return ""
    kw_list = ", ".join(feature_keywords)
    return (
        "\n## Feature Keywords Contract (MANDATORY)\n"
        f"以下关键词必须出现在生成的源代码文件中(变量名、类名、注释或字符串均可): "
        f"**{kw_list}**\n"
        "PM -> Chief Engineer -> Director 的任务目标和验收标准必须包含这些关键词。\n"
        "Director 的 target_files 中的源文件必须至少包含其中一个关键词的实际使用。\n"
    )


def build_requirements_doc(project: dict[str, Any]) -> str:
    """Frame the project brief as the requirements file the PM chain consumes."""
    checks = [str(item).strip() for item in project.get("checks", []) if str(item).strip()]
    checks_block = "\n".join(f"- {item}" for item in checks) if checks else "- 未声明额外 deterministic checks。"
    primary_language = str(project.get("primary_language") or "").strip()
    project_type = str(project.get("project_type") or "").strip()
    domain = str(project.get("domain") or "").strip()
    creative_hook = str(project.get("creative_hook") or "").strip()
    feature_keywords = _extract_feature_keywords(project)
    lang_contract = _build_language_runnable_contract(primary_language)
    source_tree_contract = _build_source_tree_contract(primary_language, project_type)
    feature_contract = _build_feature_keywords_contract(feature_keywords)

    domain_line = f"- 领域: {domain}\n" if domain else ""
    type_line = f"- 项目类型: {project_type}\n" if project_type else ""
    hook_line = f"- 创意钩子: {creative_hook}\n" if creative_hook else ""
    metadata_block = ""
    language_line = f"- 主语言: {primary_language}\n" if primary_language else ""
    if domain_line or type_line or hook_line or language_line:
        metadata_block = (
            "\n## Project Metadata\n"
            f"{language_line}{domain_line}{type_line}{hook_line}"
            "- PM -> Chief Engineer -> Director -> QA 必须在任务合同中保留这些元数据字段, "
            "确保目标语义不丢失。\n"
        )

    return (
        f"# Product Requirements — {project['title']}\n\n"
        "## Goal\n"
        f"- {project['brief']}\n\n"
        f"{metadata_block}\n"
        "## Acceptance Criteria\n"
        "- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。\n"
        "- 必须提供至少一种真实可执行入口, 且验收脚本可自动发现: Web/visual/simulation/game 项目提供含 <html> 的 index.html 或等价 HTML 入口; CLI 项目提供 package.json 脚本或可直接执行的 main 文件; API 项目提供可启动服务入口和健康检查说明。\n"
        "- package.json 脚本不得是只检查 manifest 的占位脚本; build/test/start 或等价脚本必须实际运行产品入口或核心规则验证。\n"
        "- 附 README.md 说明如何运行。\n"
        f"- 关键验收维度: {project.get('test_focus', '')}。\n"
        "\n## Deterministic Checks\n"
        "PM -> Chief Engineer -> Director -> QA 必须把以下检查转成任务目标和验收标准, 缺失任一项应视为未完成:\n"
        f"{checks_block}\n"
        "\n"
        f"{source_tree_contract}\n"
        f"{feature_contract}\n"
        f"{lang_contract}\n"
    )


def purge_project_runtime(workspace: Path) -> None:
    """Remove this project's keyed runtime dirs before a fresh run.

    Benchmark memory-isolation (adr-0092 principle): a prior run's runtime
    state ("上次 PM 任务"/plan.md/sessions) otherwise leaks into the next run's
    planning prompt — live 2026-06-12: a residual calculator contract steered
    a tic-tac-toe rerun straight back to calculator tasks.
    """
    import shutil as _shutil

    key_prefix = workspace.name.lower() + "-"
    for base in _RUNTIME_PROJECT_BASES:
        try:
            for entry in base.iterdir():
                if entry.is_dir() and entry.name.startswith(key_prefix):
                    _shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def _extract_feature_keywords(project: dict[str, Any]) -> list[str]:
    """Extract feature keywords from content_any checks in the project catalog.

    Returns a deduplicated list of keywords that the Director must embed in the
    generated source code to pass deterministic content checks.
    """
    keywords: list[str] = []
    seen: set[str] = set()
    for check in project.get("checks", []):
        check_str = str(check).strip()
        if check_str.startswith("content_any:"):
            raw = check_str[len("content_any:") :]
            for kw in raw.split("|"):
                kw = kw.strip()
                if kw and kw.lower() not in seen:
                    keywords.append(kw)
                    seen.add(kw.lower())
    return keywords


def _fallback_audit_bundle_from_workspace(workspace: Path) -> dict[str, Any]:
    """Build a partial audit bundle from workspace ``.polaris`` artifacts.

    Used as a fallback when the backend ``/audit-bundle`` endpoint times out or
    returns empty.  Reads dispatch logs, CE review, and plan artifacts that the
    Director writes directly into the workspace.
    """
    bundle: dict[str, Any] = {"gates": [], "events_tail": [], "artifacts": [], "summary_json": None}
    polaris_dir = workspace / ".polaris"
    if not polaris_dir.is_dir():
        return bundle

    events: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    # Collect dispatch logs
    dispatch_dir = polaris_dir / "dispatch"
    if dispatch_dir.is_dir():
        for log_file in sorted(dispatch_dir.glob("*.log.json")):
            try:
                payload = json.loads(log_file.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    events.append(
                        {
                            "type": "stage_completed",
                            "stage": "director_dispatch",
                            "message": f"dispatch log: {log_file.name}",
                            "result": payload,
                            "source": "workspace_fallback",
                        }
                    )
                    artifacts.append(
                        {
                            "name": log_file.name,
                            "path": str(log_file.relative_to(workspace)),
                            "size": log_file.stat().st_size,
                            "source": "workspace_fallback",
                        }
                    )
            except (OSError, ValueError):
                continue

    # Collect roles director logs
    roles_dir = polaris_dir / "roles" / "director"
    if roles_dir.is_dir():
        for log_file in sorted(roles_dir.rglob("*.log.json")):
            try:
                payload = json.loads(log_file.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    events.append(
                        {
                            "type": "stage_completed",
                            "stage": "director_dispatch",
                            "message": f"director log: {log_file.name}",
                            "result": payload,
                            "source": "workspace_fallback",
                        }
                    )
                    artifacts.append(
                        {
                            "name": log_file.name,
                            "path": str(log_file.relative_to(workspace)),
                            "size": log_file.stat().st_size,
                            "source": "workspace_fallback",
                        }
                    )
            except (OSError, ValueError):
                continue

    # Collect CE / blueprint review
    for ce_pattern in ("**/ce_*.json", "**/blueprint_*.json", "**/chief_engineer_*.json"):
        for review_file in sorted(polaris_dir.glob(ce_pattern)):
            if not review_file.is_file():
                continue
            try:
                payload = json.loads(review_file.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and payload.get("task_id"):
                    artifacts.append(
                        {
                            "name": review_file.name,
                            "path": str(review_file.relative_to(workspace)),
                            "size": review_file.stat().st_size,
                            "task_id": payload.get("task_id"),
                            "source": "workspace_fallback",
                        }
                    )
            except (OSError, ValueError):
                continue

    # Collect plan
    plan_path = polaris_dir / "docs" / "product" / "plan.json"
    if plan_path.is_file():
        try:
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
            if isinstance(plan_data, dict):
                bundle["summary_json"] = {"plan": plan_data}
                artifacts.append(
                    {
                        "name": "plan.json",
                        "path": str(plan_path.relative_to(workspace)),
                        "size": plan_path.stat().st_size,
                        "source": "workspace_fallback",
                    }
                )
        except (OSError, ValueError):
            pass

    bundle["events_tail"] = events
    bundle["artifacts"] = artifacts
    return bundle


def run_factory_chain(
    project: dict[str, Any],
    workspace: Path,
    *,
    backend_url: str,
    backend_token: str,
    timeout_s: int,
    log_path: Path,
    director_workflow_execution_mode: str = "parallel",
    director_dispatch_driver: str = "task-market",
    bench_session_id: str = "",
    start_from: str = "pm",
    on_stage_change: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Start a /v2/factory/runs for the project workspace and wait for completion."""
    normalized_start_from = str(start_from or "pm").strip().lower()
    if normalized_start_from not in {"pm", "director"}:
        raise ValueError(f"unsupported factory bench start_from: {start_from!r}")
    if normalized_start_from == "director":
        _prepare_director_resume_workspace(workspace)
    else:
        purge_project_runtime(workspace)
    workflow_mode = str(director_workflow_execution_mode or "parallel").strip().lower()
    if workflow_mode not in {"serial", "parallel"}:
        raise ValueError(f"unsupported director workflow execution mode: {director_workflow_execution_mode!r}")
    dispatch_driver = str(director_dispatch_driver or "task-market").strip().lower()
    if dispatch_driver != "task-market":
        raise ValueError("factory-bench only supports the PM→Chief Engineer→Director task-market chain")

    feature_keywords = _extract_feature_keywords(project)
    requirements_doc = build_requirements_doc(project)
    if normalized_start_from != "director":
        requirements_path = workspace / "requirements.md"
        requirements_path.write_text(requirements_doc, encoding="utf-8")
        ws_requirements = workspace / ".polaris" / "docs" / "product" / "requirements.md"
        ws_requirements.parent.mkdir(parents=True, exist_ok=True)
        ws_requirements.write_text(requirements_doc, encoding="utf-8")

        # Embed catalog metadata in the workspace so PM -> Chief Engineer -> Director can access it
        catalog_contract_path = workspace / ".polaris" / "catalog_contract.json"
        catalog_contract_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_contract = {
            "project_id": str(project.get("id") or "").strip(),
            "domain": str(project.get("domain") or "").strip(),
            "project_type": str(project.get("project_type") or "").strip(),
            "primary_language": str(project.get("primary_language") or "").strip(),
            "creative_hook": str(project.get("creative_hook") or "").strip(),
            "feature_keywords": feature_keywords,
            "checks": list(project.get("checks") or []),
            "test_focus": str(project.get("test_focus") or "").strip(),
            "source_tree_mandate": (
                "PM -> Chief Engineer -> Director must create src/ with core source files, not just scaffolding"
            ),
        }
        catalog_contract_path.write_text(
            json.dumps(catalog_contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if not (workspace / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=str(workspace), check=False)
        subprocess.run(["git", "add", "-A"], cwd=str(workspace), check=False)
        subprocess.run(
            ["git", "-c", "user.email=bench@polaris", "-c", "user.name=bench", "commit", "-qm", "bench: seed"],
            cwd=str(workspace),
            check=False,
        )

    payload = {
        "workspace": str(workspace),
        "start_from": normalized_start_from,
        "directive": requirements_doc,
        "run_director": True,
        "director_iterations": 0,
        "director_workflow_execution_mode": workflow_mode,
        "director_dispatch_driver": "task-market",
        "loop": False,
        "input_source": "directive",
        "persist_workspace": False,
        "metadata": {
            "factory_bench_session_id": str(bench_session_id or "").strip(),
            "factory_bench_project_id": str(project.get("id") or "").strip(),
            "factory_bench_level": int(project.get("level") or 0),
            "factory_bench_title": str(project.get("title") or "").strip(),
            "factory_bench_project_workspace": str(workspace.resolve()),
            "factory_bench_start_from": normalized_start_from,
        },
    }

    started = time.time()

    with open(log_path, "w", encoding="utf-8") as log_fh:

        def _on_status(status: dict[str, Any]) -> None:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            phase = status.get("phase", "")
            msg = f"[{ts}] status={status.get('status')} phase={phase}\n"
            log_fh.write(msg)
            log_fh.flush()
            if on_stage_change is not None:
                on_stage_change(str(status.get("status") or ""), status)

        start_response = start_factory_run(backend_url, payload, token=backend_token)
        if not isinstance(start_response, dict):
            return {"exit_code": -1, "duration_s": 0, "error": "start_failed"}
        if isinstance(start_response.get("_http_error"), dict):
            return {
                "exit_code": -1,
                "duration_s": round(time.time() - started, 1),
                "error": "start_failed",
                "start_error": start_response["_http_error"],
            }

        run_id = str(start_response.get("run_id") or "").strip()
        if not run_id:
            return {
                "exit_code": -1,
                "duration_s": round(time.time() - started, 1),
                "error": "start_failed",
                "start_response": start_response,
            }

        terminal_status = wait_run_until_terminal(
            backend_url,
            run_id,
            token=backend_token,
            workspace=str(workspace),
            timeout_s=float(timeout_s),
            on_status=_on_status,
            initial_status=start_response,
        )
        if terminal_status is None:
            cancel_factory_run(
                backend_url,
                run_id,
                reason=f"factory-bench event wait timeout after {timeout_s}s",
                token=backend_token,
                workspace=str(workspace),
            )
            return {
                "exit_code": -1,
                "duration_s": round(time.time() - started, 1),
                "run_id": run_id,
                "error": "event_wait_timeout",
            }

    audit_bundle = get_audit_bundle(backend_url, run_id, token=backend_token, workspace=str(workspace))
    if not audit_bundle:
        _logger.warning(
            "factory-bench: audit-bundle GET returned empty/None for run %s; "
            "falling back to workspace .polaris artifacts",
            run_id,
        )
        audit_bundle = _fallback_audit_bundle_from_workspace(workspace)
    chain_results = map_factory_run_to_chain_results(terminal_status, audit_bundle)
    chain_results["factory_bench_start_from"] = normalized_start_from

    # Read contract_goal from workspace tasks/plan.json if available
    plan_path = workspace / ".polaris" / "docs" / "product" / "plan.json"
    if plan_path.is_file():
        try:
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
            chain_results["contract_goal"] = str(plan_data.get("overall_goal") or "")[:160]
        except (OSError, ValueError):
            pass

    return {
        "exit_code": 0 if str(terminal_status.get("status") or "").lower() == "completed" else 1,
        "duration_s": round(time.time() - started, 1),
        "run_id": run_id,
        "start_from": normalized_start_from,
        "factory_terminal_status": terminal_status,
        "chain_results": chain_results,
        "audit_bundle": audit_bundle,
    }


def run_chain(
    project: dict[str, Any],
    workspace: Path,
    *,
    timeout_s: int,
    log_path: Path,
    director_workflow_execution_mode: str = "serial",
    director_dispatch_driver: str = "workflow",
) -> dict[str, Any]:
    """Invoke the full role chain headlessly on the workspace (subprocess).

    The exact invocation is centralized here; see factory-bench recon notes in
    the capability-amplification blueprint for the entrypoint decision.
    """
    purge_project_runtime(workspace)
    requirements_path = workspace.parent / f"{project['id']}.requirements.md"
    requirements_doc = build_requirements_doc(project)
    requirements_path.write_text(requirements_doc, encoding="utf-8")
    # Belt and braces: also seed the workspace-resident requirements file the
    # chain's docs auto-init would otherwise fill with a placeholder template.
    ws_requirements = workspace / ".polaris" / "docs" / "product" / "requirements.md"
    ws_requirements.parent.mkdir(parents=True, exist_ok=True)
    ws_requirements.write_text(requirements_doc, encoding="utf-8")
    # Embed catalog metadata in the workspace so PM -> Chief Engineer -> Director can access it
    catalog_contract_path = workspace / ".polaris" / "catalog_contract.json"
    catalog_contract_path.parent.mkdir(parents=True, exist_ok=True)
    feature_keywords = _extract_feature_keywords(project)
    catalog_contract = {
        "project_id": str(project.get("id") or "").strip(),
        "domain": str(project.get("domain") or "").strip(),
        "project_type": str(project.get("project_type") or "").strip(),
        "primary_language": str(project.get("primary_language") or "").strip(),
        "creative_hook": str(project.get("creative_hook") or "").strip(),
        "feature_keywords": feature_keywords,
        "checks": list(project.get("checks") or []),
        "test_focus": str(project.get("test_focus") or "").strip(),
        "source_tree_mandate": (
            "PM -> Chief Engineer -> Director must create src/ with core source files, not just scaffolding"
        ),
    }
    catalog_contract_path.write_text(
        json.dumps(catalog_contract, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # Director bundle machinery wants a git base sha; give the workspace a repo.
    if not (workspace / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=str(workspace), check=False)
        subprocess.run(["git", "add", "-A"], cwd=str(workspace), check=False)
        subprocess.run(
            ["git", "-c", "user.email=bench@polaris", "-c", "user.name=bench", "commit", "-qm", "bench: seed"],
            cwd=str(workspace),
            check=False,
        )
    dispatch_driver = str(director_dispatch_driver or "workflow").strip().lower()
    if dispatch_driver not in {"workflow", "task-market"}:
        raise ValueError(f"unsupported director dispatch driver: {director_dispatch_driver!r}")
    workflow_mode = str(director_workflow_execution_mode or "serial").strip().lower()
    if workflow_mode not in {"serial", "parallel"}:
        raise ValueError(f"unsupported director workflow execution mode: {director_workflow_execution_mode!r}")

    cmd = [
        sys.executable,
        "-m",
        "polaris.delivery.cli.pm.cli",
        "--workspace",
        str(workspace),
        "--iterations",
        "1",
        "--requirements-path",
        str(requirements_path.resolve()),
        # Local 27B decodes ~20 tok/s; the 360s default PM timeout is sized for
        # cloud latency and kills planning mid-JSON.
        "--timeout",
        "1800",
    ]
    if dispatch_driver == "workflow":
        cmd.extend(
            [
                "--run-director",
                "--director-workflow-execution-mode",
                workflow_mode,
            ]
        )
    env = dict(os.environ)
    env.setdefault("KERNELONE_WORKSPACE", str(workspace))
    if dispatch_driver == "task-market":
        env.setdefault("KERNELONE_TASK_MARKET_MODE", "mainline-full")
        env.setdefault("KERNELONE_TASK_MARKET_ROLE_POOLS", "director")
        env.setdefault("KERNELONE_TASK_MARKET_ENABLE_SAFE_PARALLEL_DIRECTOR", "1")
        # Live factory-bench L1-01 / L2-07 / L6-32 (2026-06-17): with
        # KERNELONE_CE_STEP_FISSION off (the migration default), CE
        # does not fanout parent tasks into leaf steps, so the market
        # only ever has the parent task. Workers serialize on it, the
        # second/third siblings stay in `pending_design` forever, and
        # integration_qa never gets called. The task-market
        # dispatch driver is a deliberate opt-in to a more parallel
        # path, so it must also opt in to step fission.
        env.setdefault("KERNELONE_CE_STEP_FISSION", "1")
    # Module imports come from PYTHONPATH, NOT cwd: parts of the chain key
    # role-session/storage roots off the CURRENT DIRECTORY's workspace
    # resolution (docs sentinel). Running with cwd=src/backend made every
    # project share one "backend-…" session space — live forensics 2026-06-12:
    # L1-06 (tic-tac-toe) planned and shipped L1-01's calculator because the
    # planning role session replayed cross-project state.
    env["PYTHONPATH"] = str(_BACKEND_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    # Role bindings follow the user's GLOBAL llm config: orchestration roles
    # (PM/Chief Engineer/QA) on cloud large-context models, the Director coding role on the
    # local model under test. (The all-local override used during early bring-up
    # lives on in ~/Temp/factory-bench/llm_config_all_qwen.json — set
    # KERNELONE_LLM_CONFIG yourself to reproduce those runs.)
    started = time.time()
    with open(log_path, "w", encoding="utf-8") as log_fh:
        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
        )
        if dispatch_driver != "task-market" or proc.returncode != 0:
            return {"exit_code": proc.returncode, "duration_s": round(time.time() - started, 1)}
        log_fh.write("\n[factory-bench] === task-market dispatch ===\n")
        log_fh.flush()
        market_cmd = [
            sys.executable,
            str(_BACKEND_ROOT / "scripts" / "factory_bench" / "run_market_chain.py"),
            "--workspace",
            str(workspace),
            "--fresh-market",
            "--archive-label",
            f"factory-bench-{project['id']}",
        ]
        market_proc = subprocess.run(
            market_cmd,
            cwd=str(workspace),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
        )
    return {
        "exit_code": market_proc.returncode,
        "duration_s": round(time.time() - started, 1),
        "planning_exit_code": proc.returncode,
        "task_market_exit_code": market_proc.returncode,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Polaris factory-bench full-chain runner")
    ap.add_argument("--project-ids", default="", help="comma-separated ids (e.g. L1-01,L1-02); empty = use --levels")
    ap.add_argument(
        "--levels",
        default="1,2,3,4,5,6,7,8,9,10,11,12",
        help="comma-separated levels to run when no ids given",
    )
    ap.add_argument(
        "--projects-file",
        default=str(_FIXTURE),
        help="factory-bench project catalog JSON; defaults to standalone creative projects_v2.json",
    )
    ap.add_argument("--work-dir", default=os.path.expanduser("~/Temp/factory-bench"))
    ap.add_argument("--timeout", type=int, default=5400, help="per-project chain timeout seconds")
    ap.add_argument(
        "--max-failed",
        type=int,
        default=0,
        help="early stop after N audit failures; 0 disables early stop",
    )
    ap.add_argument(
        "--director-workflow-execution-mode",
        choices=("serial", "parallel"),
        default="parallel",
        help="Director execution mode for the HTTP Factory PM→Chief Engineer→Director chain",
    )
    ap.add_argument(
        "--director-dispatch-driver",
        choices=("task-market",),
        default="task-market",
        help="Director dispatch path; only task-market mainline-full is supported",
    )
    ap.add_argument(
        "--start-from",
        choices=("pm", "director"),
        default="pm",
        help="Factory stage to start from; director reuses trusted PM/CE evidence and pre-Director snapshot",
    )
    ap.add_argument(
        "--use-legacy-chain",
        action="store_true",
        help="Retired; Factory Bench refuses legacy two-role subprocess runs",
    )
    ap.add_argument(
        "--real-run-timeout",
        type=int,
        default=60,
        help="seconds for each generated project's dependency/build/entrypoint real-run gate",
    )
    ap.add_argument(
        "--launcher-instance-mode",
        choices=tuple(sorted(_LAUNCHER_INSTANCE_MODES)),
        default=_default_launcher_instance_mode(),
        help=(
            "Launcher registration mode: isolated starts a project-scoped Polaris backend/frontend and runs the "
            "chain against it; observed registers shared-backend bench activity for explicit compatibility only"
        ),
    )
    ap.add_argument(
        "--bench-session-reporting",
        choices=tuple(sorted(_BENCH_SESSION_REPORTING_MODES)),
        default=_default_bench_session_reporting_mode(),
        help=(
            "Internal bench session reporting mode: auto reports only for observed shared-backend runs; "
            "shared also reports isolated runs to the main backend; off disables shared session POSTs"
        ),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="validate projects and generate audit structure without running the chain",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="limit number of projects to process; 0 disables limit",
    )
    args = ap.parse_args()
    if args.use_legacy_chain:
        print(
            "[factory-bench] --use-legacy-chain is retired; use the HTTP Factory "
            "PM→Chief Engineer→Director task-market chain",
            flush=True,
        )
        return 2

    projects = load_projects() if args.projects_file == str(_FIXTURE) else load_projects(args.projects_file)
    if args.project_ids.strip():
        wanted_ids = [s.strip() for s in args.project_ids.split(",") if s.strip()]
        available_ids = {str(p["id"]) for p in projects}
        missing_ids = [project_id for project_id in wanted_ids if project_id not in available_ids]
        if missing_ids:
            print(
                "[factory-bench] unknown project id(s): "
                + ", ".join(missing_ids)
                + "; refusing to run partial explicit selection",
                flush=True,
            )
            return 1
        wanted_id_set = set(wanted_ids)
        selected = [p for p in projects if p["id"] in wanted_id_set]
    else:
        wanted_levels = {int(s) for s in args.levels.split(",") if s.strip()}
        selected = [p for p in projects if int(p["level"]) in wanted_levels]
    if not selected:
        print("[factory-bench] nothing selected", flush=True)
        return 1

    # Apply --limit if specified
    if args.limit > 0:
        selected = selected[: args.limit]
        print(f"[factory-bench] limiting to {len(selected)} project(s) (--limit={args.limit})", flush=True)

    # Handle --dry-run: validate and generate audit structure without running chain
    if args.dry_run:
        print(f"[factory-bench] dry-run mode: validating {len(selected)} project(s)", flush=True)
        try:
            base = _resolve_bench_work_dir(args.work_dir)
        except ValueError as exc:
            print(f"[factory-bench] invalid --work-dir: {exc}", flush=True)
            return 2
        base.mkdir(parents=True, exist_ok=True)
        audit_dir = base / "audits" / "dry-run"
        audit_dir.mkdir(parents=True, exist_ok=True)

        catalog_hash = hashlib.sha256(
            json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]

        for project in selected:
            pid = str(project.get("id") or "")
            level = int(project.get("level") or 0)
            lang = str(project.get("primary_language") or "")

            audit_file = audit_dir / f"{pid}.audit.json"
            project_audit = {
                "catalog_schema_version": "factory-bench/2",
                "catalog_hash": catalog_hash,
                "run_id": "dry-run",
                "project_id": pid,
                "level": level,
                "primary_language": lang,
                "title": str(project.get("title") or ""),
                "domain": str(project.get("domain") or ""),
                "project_type": str(project.get("project_type") or ""),
                "record": {
                    "project_id": pid,
                    "level": level,
                    "primary_language": lang,
                    "dry_run": True,
                    "validation_passed": True,
                },
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _write_immutable_json(audit_file, project_audit)
            print(f"[factory-bench]   {pid} L{level} {lang}: audit package generated", flush=True)

        print(f"[factory-bench] dry-run complete: {len(selected)} audit package(s) -> {audit_dir}", flush=True)
        return 0

    try:
        base = _resolve_bench_work_dir(args.work_dir)
    except ValueError as exc:
        print(f"[factory-bench] invalid --work-dir: {exc}", flush=True)
        return 2
    base.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    run_errors: list[str] = []
    failed = 0
    expected_llm_bindings = resolve_expected_llm_bindings()
    bench_session_id = os.environ.get("FACTORY_BENCH_SESSION_ID") or ""
    launcher_instance_mode = str(args.launcher_instance_mode or "isolated").strip().lower()
    bench_session_reporting = str(args.bench_session_reporting or "auto").strip().lower()
    backend_url = _resolve_backend_url()
    backend_token = _resolve_backend_token()
    bench_session_backend_url = _bench_session_backend_url(
        launcher_instance_mode=launcher_instance_mode,
        bench_session_reporting=bench_session_reporting,
        backend_url=backend_url,
    )
    bench_session_id = _ensure_bench_session(
        backend_url=bench_session_backend_url,
        work_dir=str(base),
        project_ids=[str(p["id"]) for p in selected],
        total=len(selected),
        metadata={
            "levels": sorted({int(p.get("level") or 0) for p in selected}),
            "launcher_instance_mode": launcher_instance_mode,
            "bench_session_reporting": bench_session_reporting,
        },
        requested_session_id=bench_session_id,
        token=backend_token,
    )
    configure_bench_backend(bench_session_backend_url, bench_session_id, backend_token)
    backend_audit_context = build_bench_backend_audit_context(
        bench_session_backend_url,
        backend_token=backend_token,
        workspace=str(base),
    )
    _emit_bench_event(
        workspace=base,
        project_id="-",
        level=0,
        name="run.started",
        summary=f"factory-bench session {bench_session_id or 'local'}: {len(selected)} project(s)",
        meta={
            "session_id": bench_session_id,
            "total": len(selected),
            "launcher_instance_mode": launcher_instance_mode,
            "bench_session_reporting": bench_session_reporting,
            "shared_session_backend_url": bool(bench_session_backend_url),
        },
    )
    use_legacy_chain = False

    # Compute catalog hash for immutable audit trail
    catalog_hash = hashlib.sha256(json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[
        :16
    ]
    catalog_schema_version = "factory-bench/2"

    # Resolve a single run_id for the entire bench run.
    # FACTORY_BENCH_RUN_ID env takes precedence; otherwise generate once.
    run_id = _sanitize_run_id(os.environ.get("FACTORY_BENCH_RUN_ID"))
    audit_dir = base / "audits" / run_id
    audit_dir.mkdir(parents=True, exist_ok=True)

    for project in selected:
        pid = project["id"]
        workspace = base / pid
        # Purge project directory completely to prevent stale contamination
        import shutil as _shutil

        resume_director = str(args.start_from or "pm").strip().lower() == "director"
        if workspace.exists() and not resume_director:
            _shutil.rmtree(workspace, ignore_errors=True)
        workspace.mkdir(parents=True, exist_ok=True)
        log_path = base / f"{pid}.chain.log"
        # Write catalog metadata for audit traceability
        catalog_meta = {
            "catalog_schema_version": catalog_schema_version,
            "catalog_hash": catalog_hash,
            "run_id": run_id,
            "project_id": pid,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (workspace / ".catalog_meta.json").write_text(
            json.dumps(catalog_meta, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        print(f"[factory-bench] === {pid} {project['title']} ===", flush=True)
        project_level = int(project.get("level") or 0)
        project_title = str(project.get("title") or "")
        workspace.mkdir(parents=True, exist_ok=True)
        project_workspace = str(workspace.resolve())
        project_backend_url = backend_url
        project_backend_token = backend_token
        project_backend_audit_context = backend_audit_context
        launcher_instance_meta: dict[str, Any] = {"mode": launcher_instance_mode}
        if launcher_instance_mode == "isolated":
            isolated_instance = _start_isolated_bench_project_instance(
                bench_session_id=bench_session_id,
                project_id=str(pid),
                project_title=project_title,
                level=project_level,
                bench_workspace=base,
                project_workspace=project_workspace,
                backend_token=backend_token,
            )
            if isolated_instance:
                project_backend_url = str(isolated_instance.get("backend_url") or backend_url).rstrip("/")
                project_backend_token = str(isolated_instance.get("token") or backend_token)
                project_backend_audit_context = build_bench_backend_audit_context(
                    project_backend_url,
                    backend_token=project_backend_token,
                    workspace=project_workspace,
                )
                launcher_instance_meta.update(
                    {
                        "ok": True,
                        "instance_id": isolated_instance.get("instance_id"),
                        "backend_url": isolated_instance.get("backend_url"),
                        "frontend_url": isolated_instance.get("frontend_url"),
                    }
                )
                workspace_switch_ok = True
            else:
                workspace_switch_ok = False
                launcher_instance_meta.update({"ok": False, "error": "isolated_instance_start_failed"})
        else:
            workspace_switch_ok = _push_bench_workspace_to_backend(
                backend_url=backend_url,
                workspace=project_workspace,
                token=backend_token,
            )
            _register_bench_project_instance(
                bench_session_id=bench_session_id,
                project_id=str(pid),
                project_title=project_title,
                level=project_level,
                bench_workspace=base,
                project_workspace=project_workspace,
                backend_url=backend_url,
                backend_token=backend_token,
            )
            launcher_instance_meta.update({"ok": True, "backend_binding": "shared_backend_workspace_switch"})
        _emit_bench_event(
            workspace=base,
            project_id=pid,
            level=project_level,
            name="project.started",
            summary=f"{pid} {project_title} starting",
            meta={
                "session_id": bench_session_id,
                "title": project_title,
                "workspace": project_workspace,
                "workspace_path": project_workspace,
                "project_workspace": project_workspace,
                "launcher_instance": launcher_instance_meta,
                "workspace_switch": {
                    "attempted": bool(project_backend_url) and launcher_instance_mode != "isolated",
                    "ok": bool(workspace_switch_ok),
                    "endpoint": "/settings" if launcher_instance_mode != "isolated" else "instance_supervisor",
                },
            },
        )
        last_stage_event_key = ""

        def _on_factory_stage_change(
            stage_status: str,
            status_payload: dict[str, Any],
            *,
            _project_id: str = pid,
            _project_level: int = project_level,
            _project_title: str = project_title,
            _project_workspace: Path = workspace,
        ) -> None:
            nonlocal last_stage_event_key
            phase = str(status_payload.get("phase") or "").strip()
            run_status = str(status_payload.get("status") or stage_status or "").strip()
            run_ref = str(status_payload.get("run_id") or "").strip()
            event_payload_raw = status_payload.get("event_payload")
            event_payload: dict[str, Any] = event_payload_raw if isinstance(event_payload_raw, dict) else {}
            factory_event_type = str(event_payload.get("type") or status_payload.get("event_type") or "").strip()
            if factory_event_type == "task_runtime_execution":
                event_key = ":".join(
                    [
                        run_ref,
                        factory_event_type,
                        str(event_payload.get("session_id") or ""),
                        str(event_payload.get("task_id") or ""),
                        str(event_payload.get("event_type") or ""),
                        str(event_payload.get("timestamp") or ""),
                    ]
                )
                if event_key == last_stage_event_key:
                    return
                last_stage_event_key = event_key
                _emit_factory_task_runtime_event(
                    bench_workspace=base,
                    project_workspace=_project_workspace,
                    project_id=_project_id,
                    level=_project_level,
                    title=_project_title,
                    phase_payload=status_payload,
                    event_payload=event_payload,
                )
                return
            event_key = f"{run_ref}:{run_status}:{phase}"
            if not event_key.strip(":") or event_key == last_stage_event_key:
                return
            last_stage_event_key = event_key
            _emit_factory_phase_event(
                bench_workspace=base,
                project_workspace=_project_workspace,
                project_id=_project_id,
                level=_project_level,
                title=_project_title,
                status=stage_status,
                phase_payload=status_payload,
            )

        if project_backend_url and not workspace_switch_ok:
            error = (
                "isolated_instance_start_failed" if launcher_instance_mode == "isolated" else "workspace_switch_failed"
            )
            run_errors.append(error)
            chain = {
                "exit_code": -1,
                "duration_s": 0.0,
                "error": error,
                "failure_category": "runtime_environment",
                "root_cause_signature": f"runtime_environment:{error}",
                "launcher_instance": launcher_instance_meta,
                "workspace_switch": {
                    "attempted": launcher_instance_mode != "isolated",
                    "ok": False,
                    "endpoint": "/settings" if launcher_instance_mode != "isolated" else "instance_supervisor",
                    "workspace": project_workspace,
                },
            }
            _emit_bench_event(
                workspace=base,
                project_id=pid,
                level=project_level,
                name="project.failed",
                summary=f"{pid} workspace switch failed before observation",
                meta={
                    "session_id": bench_session_id,
                    "error": error,
                    "failure_category": "runtime_environment",
                    "root_cause_signature": "runtime_environment:workspace_switch_failed",
                    "workspace": project_workspace,
                    "workspace_path": project_workspace,
                    "project_workspace": project_workspace,
                    "launcher_instance": launcher_instance_meta,
                    "workspace_switch": chain["workspace_switch"],
                },
            )
        else:
            try:
                if use_legacy_chain:
                    chain = run_chain(
                        project,
                        workspace,
                        timeout_s=args.timeout,
                        log_path=log_path,
                        director_workflow_execution_mode=args.director_workflow_execution_mode,
                        director_dispatch_driver=args.director_dispatch_driver,
                    )
                else:
                    chain = run_factory_chain(
                        project,
                        workspace,
                        backend_url=project_backend_url,
                        backend_token=project_backend_token,
                        timeout_s=args.timeout,
                        log_path=log_path,
                        director_workflow_execution_mode=args.director_workflow_execution_mode,
                        director_dispatch_driver=args.director_dispatch_driver,
                        bench_session_id=bench_session_id,
                        start_from=args.start_from,
                        on_stage_change=_on_factory_stage_change,
                    )
            except subprocess.TimeoutExpired:
                chain = {"exit_code": -1, "duration_s": float(args.timeout), "timeout": True}
            except KeyboardInterrupt as exc:
                reason = "interrupted"
                interrupted_counts = _bench_record_counts(records, total=len(selected))
                _emit_bench_event(
                    workspace=base,
                    project_id="-",
                    level=0,
                    name="run.cancelled",
                    summary=f"factory-bench cancelled: {reason}",
                    meta={
                        "session_id": bench_session_id,
                        **interrupted_counts,
                        "error": reason,
                    },
                )
                if bench_session_backend_url and bench_session_id:
                    _push_bench_complete_to_backend(
                        backend_url=bench_session_backend_url,
                        session_id=bench_session_id,
                        success=False,
                        summary={
                            **interrupted_counts,
                            "error": reason,
                            "exception": type(exc).__name__,
                        },
                        token=backend_token,
                    )
                return 130
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                error = str(exc) or type(exc).__name__
                run_errors.append(error)
                chain = {
                    "exit_code": -1,
                    "duration_s": 0.0,
                    "error": error,
                    "exception": type(exc).__name__,
                    "_runner_exception": True,
                }
        _emit_bench_event(
            workspace=base,
            project_id=pid,
            level=int(project.get("level") or 0),
            name="project.completed",
            summary=f"{pid} exit={chain.get('exit_code')} dur={chain.get('duration_s')}s",
            meta={
                "session_id": bench_session_id,
                "exit_code": chain.get("exit_code"),
                "duration_s": chain.get("duration_s"),
                "timeout": bool(chain.get("timeout")),
            },
        )
        runtime_dirs = resolve_runtime_dirs_for_workspace(workspace)
        runtime_dir = runtime_dirs[0] if runtime_dirs else None
        # Determine whether the chain reached a genuine terminal state.
        # - start_failed/workspace_switch_failed: the pipeline never started.
        # - _runner_exception: the bench runner crashed before completion.
        # - event_wait_timeout: runtime.v2 did not deliver a terminal event;
        #   we send cancel, but the backend may still be mutating the
        #   workspace. Treat this as non-terminal and do not run final gates
        #   against a racing snapshot.
        # - Otherwise: wait_run_until_terminal returned a terminal status dict
        #   or a legacy subprocess reached an interrupted terminal state.
        chain_error = str(chain.get("error") or "")
        chain_is_terminal = _chain_reached_terminal(chain)
        chain_results_raw = chain.get("chain_results")
        chain_results_for_status: dict[str, Any] = chain_results_raw if isinstance(chain_results_raw, dict) else {}
        chain_status_raw = str(chain_results_for_status.get("exit_class", ""))
        chain_phase_raw = chain_error or ("timeout" if chain.get("timeout") else "")
        record = build_factory_audit_record(
            project=project,
            workspace=str(workspace),
            artifact_globs=discover_artifacts(workspace, runtime_dirs),
            chain_terminal=chain_is_terminal,
            chain_status=chain_status_raw,
            chain_phase=chain_phase_raw,
        )
        record["runtime_dir"] = str(runtime_dir) if runtime_dir else None
        record["runtime_dirs"] = [str(path) for path in runtime_dirs]
        record["chain"] = chain
        if use_legacy_chain:
            record["chain_results"] = read_chain_results_from_runtime_dirs(runtime_dirs)
        else:
            record["chain_results"] = (
                chain.get("chain_results")
                if "chain_results" in chain
                else read_chain_results_from_runtime_dirs(runtime_dirs)
            )
        contract_goal = record["chain_results"]["contract_goal"]
        own_overlap = brief_goal_overlap(str(project.get("brief") or ""), contract_goal)
        record["goal_brief_overlap"] = round(own_overlap, 3)
        # Language-robust contamination detection: an absolute threshold
        # false-positives when the planner answers a Chinese brief with an
        # English goal (zero char-bigram overlap, live 2026-06-12). The real
        # contamination signal is RELATIVE — the goal resembling ANOTHER
        # project's brief more than its own.
        best_other = 0.0
        best_other_id = ""
        for other in projects:
            if other["id"] == project["id"]:
                continue
            score = brief_goal_overlap(str(other.get("brief") or ""), contract_goal)
            if score > best_other:
                best_other, best_other_id = score, str(other["id"])
        # Absolute floor besides the relative margin: an English goal vs a
        # Chinese own-brief scores 0.0, and any latin-bearing OTHER brief
        # (e.g. "Docker/Cgroups") wins the relative test on noise alone —
        # live false positive: L2-12's correct brick-breaker goal flagged as
        # ~L8-45 (container engine) at best_other≈0.1.
        record["wrong_product_suspect"] = bool(contract_goal and best_other > max(0.18, own_overlap + 0.1))
        record["wrong_product_match"] = best_other_id if record["wrong_product_suspect"] else ""
        record["chain_state"] = grade_chain_state(record["chain_results"], chain.get("exit_code"))
        raw_audit_bundle = chain.get("audit_bundle")
        audit_bundle: dict[str, Any] = raw_audit_bundle if isinstance(raw_audit_bundle, dict) else {}
        record.update(project_backend_audit_context)
        record["run_id"] = run_id
        record["project_id"] = pid
        record["factory_run_id"] = str(chain.get("run_id") or run_id)
        if chain_is_terminal:
            record["real_run_gate"] = build_real_run_gate(
                workspace,
                record,
                timeout_s=int(args.real_run_timeout),
            )
            record["run_ledger"] = persist_real_run_gate_ledger(
                workspace,
                record,
                record["real_run_gate"],
                run_id=run_id,
                project_id=pid,
            )
        else:
            record["real_run_gate"] = _build_non_terminal_real_run_gate(
                chain_phase=chain_phase_raw,
                chain_status=chain_status_raw,
            )
            record["run_ledger"] = persist_real_run_gate_ledger(
                workspace,
                record,
                record["real_run_gate"],
                run_id=run_id,
                project_id=pid,
                stage=chain_phase_raw or chain_status_raw or "chain_non_terminal",
                gate_name="chain_non_terminal",
            )
        record["run_ledger_projection"] = load_run_ledger_projection(workspace, run_id=run_id)
        required_llm_roles = required_llm_roles_for_factory_record(chain=chain, record=record)
        record["required_llm_roles"] = list(required_llm_roles)
        record["llm_route_audit"] = build_llm_route_audit(
            collect_llm_events(workspace, runtime_dirs, audit_bundle),
            expected_bindings=expected_llm_bindings,
            required_roles=required_llm_roles,
            require_all_director_routes=False,
        )
        apply_factory_bench_gates(record, chain)
        apply_factory_bench_failure_taxonomy(record)
        convergence = audit_bundle.get("director_convergence")
        if isinstance(convergence, dict):
            record["director_convergence"] = convergence
        records.append(record)
        status = "PASS" if record["all_checks_passed"] else "FAIL"
        print(
            f"[factory-bench] {pid} {status}: chain={record['chain_state']} "
            f"files={record['code_file_count']} source={record.get('source_file_count', '?')} "
            f"plan={record['has_plan_doc']} blueprint={record['has_blueprint_doc']} "
            f"verdict={record['has_qa_verdict']} qa_ran={record['chain_results']['qa_ran']} "
            f"qa_passed={record['chain_results']['qa_passed']} director={record['chain_results']['director']} "
            f"goal_overlap={record['goal_brief_overlap']}"
            f"{' [WRONG-PRODUCT? ~' + record['wrong_product_match'] + ']' if record['wrong_product_suspect'] else ''} "
            f"chain_exit={chain.get('exit_code')} ({chain.get('duration_s')}s)",
            flush=True,
        )
        for check in record["checks"]:
            print(
                f"[factory-bench]   - {check['check']}: {'ok' if check['ok'] else 'FAIL'} ({check['detail']})",
                flush=True,
            )
        for gate in record["factory_gates"]:
            print(
                f"[factory-bench]   - gate:{gate['gate']}: {'ok' if gate['ok'] else 'FAIL'} ({gate['detail']})",
                flush=True,
            )
            _emit_bench_event(
                workspace=base,
                project_id=pid,
                level=int(project.get("level") or 0),
                name="gate.evaluated",
                summary=f"{pid} gate:{gate['gate']}={'ok' if gate['ok'] else 'FAIL'}",
                meta={
                    "session_id": bench_session_id,
                    "gate": gate["gate"],
                    "ok": bool(gate["ok"]),
                    "detail": gate.get("detail") or "",
                },
            )
        _emit_bench_event(
            workspace=base,
            project_id=pid,
            level=int(project.get("level") or 0),
            name="project.audit",
            summary=(
                f"{pid} audit={status} real_run={bool(record['real_run_gate'].get('ok'))} "
                f"llm_route={bool(record['llm_route_audit'].get('ok'))} "
                f"root={record['failure_taxonomy'].get('root_cause_signature')}"
            ),
            meta={
                "session_id": bench_session_id,
                "project_id": pid,
                "status": status.lower(),
                "real_run_gate": record["real_run_gate"],
                "llm_route_audit": record["llm_route_audit"],
                "failure_taxonomy": record["failure_taxonomy"],
            },
        )
        # Push live counters to the optional shared bench session. Isolated
        # project instances do not depend on this observation bridge.
        if bench_session_backend_url and bench_session_id:
            _push_bench_progress_to_backend(
                backend_url=bench_session_backend_url,
                session_id=bench_session_id,
                completed=sum(1 for r in records if r.get("all_checks_passed")),
                failed=sum(1 for r in records if not r.get("all_checks_passed")),
                token=backend_token,
            )

        out_path = base / "factory_audits.json"
        partial_agg = aggregate_factory_audits(records)
        partial_goal_audit = aggregate_goal_audit(records)
        out_path.write_text(
            json.dumps(
                {"aggregate": partial_agg, "goal_audit": partial_goal_audit, "records": records},
                ensure_ascii=False,
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
        # Write immutable per-run audit package
        audit_file = _next_immutable_json_path(audit_dir / f"{pid}.audit.json")
        project_audit = {
            "catalog_schema_version": catalog_schema_version,
            "catalog_hash": catalog_hash,
            "run_id": run_id,
            "project_id": pid,
            "record": record,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "audit_path": str(audit_file.relative_to(base)),
        }
        _write_immutable_json(audit_file, project_audit)
        if not record["all_checks_passed"]:
            failed += 1
            if args.max_failed > 0 and failed >= args.max_failed:
                print(f"[factory-bench] early stop: {failed} failures (audit before continuing)", flush=True)
                break

    agg = aggregate_factory_audits(records)
    goal_audit = aggregate_goal_audit(records)
    out_path = base / "factory_audits.json"
    out_path.write_text(
        json.dumps(
            {"aggregate": agg, "goal_audit": goal_audit, "records": records},
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    run_success = agg["all_checks_passed"] == agg["total"]
    _emit_bench_event(
        workspace=base,
        project_id="-",
        level=0,
        name="run.completed",
        summary=f"factory-bench {agg['all_checks_passed']}/{agg['total']} passed",
        meta={
            "session_id": bench_session_id,
            "total": agg["total"],
            "passed": agg["all_checks_passed"],
            "failed": agg["total"] - agg["all_checks_passed"],
            "by_level": agg["by_level"],
            "goal_audit": goal_audit,
        },
    )
    if bench_session_backend_url and bench_session_id:
        complete_summary = {
            "total": agg["total"],
            "passed": agg["all_checks_passed"],
            "failed": agg["total"] - agg["all_checks_passed"],
            "by_level": agg["by_level"],
            "goal_audit": goal_audit,
        }
        if run_errors:
            complete_summary["error"] = "; ".join(run_errors)
        _push_bench_complete_to_backend(
            backend_url=bench_session_backend_url,
            session_id=bench_session_id,
            success=run_success,
            summary=complete_summary,
            token=backend_token,
        )
    print(
        f"\n[factory-bench] ===== {agg['all_checks_passed']}/{agg['total']} passed | by_level={agg['by_level']} =====",
        flush=True,
    )

    print(f"[factory-bench] audits -> {base / 'factory_audits.json'}", flush=True)
    return 0 if run_success else 1


if __name__ == "__main__":
    sys.exit(main())
