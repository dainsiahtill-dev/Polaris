#!/usr/bin/env python3
"""Factory-bench runner — drive the FULL Polaris role chain per project.

For each project in ``projects_v1.json`` (L1→L8, sequential — the local vLLM
is a shared single GPU, so this runner IS the load mutex):

1. create a fresh workspace directory;
2. hand the project brief to the Polaris role chain (PM→Architect/CE→
   Director→QA) headlessly;
3. collect generated artifacts (plan/blueprint docs, QA verdicts, code);
4. run the project's deterministic checks (``factory_audit``) and append a
   schema-stamped audit record.

Benchmark discipline (memory: benchmark-run-discipline): one project at a
time, ``--max-failed`` early stop, audit + root-cause before continuing.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")

from polaris.kernelone.benchmark.factory_audit import (
    aggregate_factory_audits,
    build_factory_audit_record,
)

_FIXTURE = Path(__file__).resolve().parent / "projects_v1.json"
_BACKEND_ROOT = Path("/home/dains/Documents/polaris/src/backend")

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
    "verdict": ("runs/*/qa/integration_qa.result.json", "results/integration_qa.result.json"),
    "director_result": ("runs/*/results/director.result.json", "results/director.result.json"),
}
_WORKSPACE_ARTIFACT_GLOBS: dict[str, tuple[str, ...]] = {
    "plan": (".polaris/docs/product/plan.md", ".polaris/docs/product/requirements.md", ".polaris/docs/*.md"),
    "blueprint": (".polaris/blueprints/*",),
    "verdict": (),
    "director_result": (),
}


def load_projects() -> list[dict[str, Any]]:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    return list(data["projects"])


def resolve_runtime_dir_for_workspace(workspace: Path) -> Path | None:
    """Find this workspace's runtime dir by its deterministic name key."""
    key_prefix = workspace.name.lower() + "-"
    matches: list[Path] = []
    for base in _RUNTIME_PROJECT_BASES:
        try:
            matches.extend(e for e in base.iterdir() if e.is_dir() and e.name.startswith(key_prefix))
        except OSError:
            continue
    if not matches:
        return None
    newest = max(matches, key=lambda e: e.stat().st_mtime)
    runtime = newest / "runtime"
    return runtime if runtime.is_dir() else newest


def discover_artifacts(workspace: Path, runtime_dir: Path | None) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for kind, patterns in _WORKSPACE_ARTIFACT_GLOBS.items():
        hits: list[str] = []
        for pattern in patterns:
            hits.extend(f"ws:{p.relative_to(workspace)}" for p in workspace.glob(pattern) if p.is_file())
        found[kind] = sorted(set(hits))
    if runtime_dir is not None:
        for kind, patterns in _RUNTIME_ARTIFACT_GLOBS.items():
            hits = list(found.get(kind, []))
            for pattern in patterns:
                hits.extend(f"rt:{p.relative_to(runtime_dir)}" for p in runtime_dir.glob(pattern) if p.is_file())
            found[kind] = sorted(set(hits))
    return found


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


_EXIT_CLASS_BY_CODE = {0: "clean", 4: "director_partial", 5: "qa_failed"}


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


# Module-level state populated by main() so the emit helper can also
# forward each event to the Factory HTTP backend (Factory panel SSE stream).
# Empty values mean "no backend wiring": the helper degrades to local JSONL
# only and never makes a network call.
_BENCH_BACKEND: dict[str, str] = {"backend_url": "", "session_id": "", "token": ""}


def configure_bench_backend(backend_url: str, session_id: str, token: str = "") -> None:
    """Set the active backend URL + session id + token (called once by main())."""
    _BENCH_BACKEND["backend_url"] = backend_url
    _BENCH_BACKEND["session_id"] = session_id
    _BENCH_BACKEND["token"] = token


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

    Backend path: when main() registered a session, also POSTs the event to
    ``/v2/factory/bench/sessions/{id}/events`` so the Factory front-end
    panel can subscribe via SSE.

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

    # --- Factory HTTP backend push: canonical real-time path. Runs
    # independently of cache_root so the Factory panel sees every event
    # even when the bench is run against a plain parent work_dir.
    backend_ok = False
    backend_url = _BENCH_BACKEND.get("backend_url", "")
    backend_sid = _BENCH_BACKEND.get("session_id", "")
    if backend_url and backend_sid:
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


# --- Bench subprocess -> backend HTTP client ---
#
# The bench runs in a terminal and posts lifecycle events to the Factory
# HTTP backend so the Factory front-end panel can stream them in real time.
# All helpers are fail-soft: a missing/unreachable backend must NEVER crash
# the bench run. The bench only does local JSONL emission in that case
# (which the WS bridge can still pick up if connected to the active
# workspace's runtime dir).

import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

_DEFAULT_BACKEND_URL = "http://127.0.0.1:49977"
_BENCH_HTTP_TIMEOUT_S = 10.0  # bumped from 2.0: cold-start 49977 can exceed 2s


def _resolve_backend_url(explicit: str | None = None) -> str:
    """Pick a backend URL from arg > env > default; return "" to disable."""
    candidate = (
        (explicit or "").strip()
        or os.environ.get("KERNELONE_BACKEND_URL", "").strip()
        or os.environ.get("FACTORY_BENCH_BACKEND_URL", "").strip()
        or _DEFAULT_BACKEND_URL
    )
    return candidate.rstrip("/")


def _resolve_backend_token(explicit: str | None = None) -> str:
    """Pick a backend bearer token from arg > env.

    The Polaris factory router requires a Bearer token in the Authorization
    header (query tokens are intentionally rejected — see
    ``polaris.delivery.http.dependencies.require_auth``). The bench subprocess
    runs in a terminal and has no way to ask the Electron app for a token,
    so the user must set ``KERNELONE_TOKEN`` (or ``FACTORY_BENCH_BACKEND_TOKEN``)
    in the same env as the bench, matching the value the backend was started
    with. Returns "" when no token is configured (the bench then makes
    unauthenticated requests, which is fine for dev mode with auth disabled).
    """
    return (
        (explicit or "").strip()
        or os.environ.get("FACTORY_BENCH_BACKEND_TOKEN", "").strip()
        or os.environ.get("KERNELONE_TOKEN", "").strip()
    )


def _http_post_json(
    url: str,
    body: dict[str, Any],
    *,
    timeout_s: float = _BENCH_HTTP_TIMEOUT_S,
    token: str = "",
) -> dict[str, Any] | None:
    try:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8") or "{}"
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        print(f"[factory-bench] backend POST failed: {url}: {exc}", file=sys.stderr, flush=True)
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


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
    response = _http_post_json(f"{backend_url}/v2/factory/bench/sessions", payload, token=token)
    if not isinstance(response, dict):
        return None
    sid = str(response.get("session_id") or "").strip()
    return sid or None


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
        token=token,
    )
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
    payload: dict[str, Any] = {
        "success": bool(success),
        "summary": dict(summary or {}),
    }
    response = _http_post_json(
        f"{backend_url}/v2/factory/bench/sessions/{session_id}/complete",
        payload,
        token=token,
    )
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
    right counter and the SSE snapshot reflects it on the next tick.
    """
    payload: dict[str, Any] = {
        "completed": int(completed),
        "failed": int(failed),
    }
    response = _http_post_json(
        f"{backend_url}/v2/factory/bench/sessions/{session_id}/progress",
        payload,
        token=token,
    )
    return response is not None and bool(response.get("updated", False))


def _bench_gate(gate: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"gate": gate, "ok": bool(ok), "detail": detail}


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
    return [
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


def apply_factory_bench_gates(record: dict[str, Any], chain: dict[str, Any]) -> None:
    """Fold full-chain gates into ``all_checks_passed`` in-place."""

    static_checks_passed = bool(record.get("static_checks_passed", record.get("all_checks_passed")))
    gates = build_factory_bench_gates(record, chain)
    record["static_checks_passed"] = static_checks_passed
    record["factory_gates"] = gates
    record["all_checks_passed"] = static_checks_passed and all(gate["ok"] for gate in gates)


def build_requirements_doc(project: dict[str, Any]) -> str:
    """Frame the project brief as the requirements file the PM chain consumes."""
    return (
        f"# Product Requirements — {project['title']}\n\n"
        "## Goal\n"
        f"- {project['brief']}\n\n"
        "## Acceptance Criteria\n"
        "- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。\n"
        "- 附 README.md 说明如何运行。\n"
        f"- 关键验收维度: {project.get('test_focus', '')}。\n"
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
    # (PM/CE/QA) on cloud large-context models, the Director coding role on the
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
    ap.add_argument("--levels", default="1", help="comma-separated levels to run when no ids given")
    ap.add_argument("--work-dir", default=os.path.expanduser("~/Temp/factory-bench"))
    ap.add_argument("--timeout", type=int, default=5400, help="per-project chain timeout seconds")
    ap.add_argument("--max-failed", type=int, default=3, help="early stop after N audit failures")
    ap.add_argument(
        "--director-workflow-execution-mode",
        choices=("serial", "parallel"),
        default="serial",
        help="PM Director workflow mode; keep serial by default, use parallel with task-market role pools",
    )
    ap.add_argument(
        "--director-dispatch-driver",
        choices=("workflow", "task-market"),
        default="workflow",
        help="Director dispatch path: legacy PM workflow or task-market chain after PM planning",
    )
    args = ap.parse_args()

    projects = load_projects()
    if args.project_ids.strip():
        wanted_ids = {s.strip() for s in args.project_ids.split(",") if s.strip()}
        selected = [p for p in projects if p["id"] in wanted_ids]
    else:
        wanted_levels = {int(s) for s in args.levels.split(",") if s.strip()}
        selected = [p for p in projects if int(p["level"]) in wanted_levels]
    if not selected:
        print("[factory-bench] nothing selected", flush=True)
        return 1

    base = Path(args.work_dir)
    base.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    failed = 0
    bench_session_id = os.environ.get("FACTORY_BENCH_SESSION_ID") or ""
    backend_url = _resolve_backend_url()
    backend_token = _resolve_backend_token()
    if backend_url and not bench_session_id:
        bench_session_id = (
            _push_bench_session_to_backend(
                backend_url=backend_url,
                work_dir=str(base),
                project_ids=[str(p["id"]) for p in selected],
                total=len(selected),
                metadata={"levels": sorted({int(p.get("level") or 0) for p in selected})},
                token=backend_token,
            )
            or ""
        )
    configure_bench_backend(backend_url, bench_session_id, backend_token)
    _emit_bench_event(
        workspace=base,
        project_id="-",
        level=0,
        name="run.started",
        summary=f"factory-bench session {bench_session_id or 'local'}: {len(selected)} project(s)",
        meta={"session_id": bench_session_id, "total": len(selected), "backend_url": bool(backend_url)},
    )

    for project in selected:
        pid = project["id"]
        workspace = base / pid
        workspace.mkdir(parents=True, exist_ok=True)
        log_path = base / f"{pid}.chain.log"
        print(f"[factory-bench] === {pid} {project['title']} ===", flush=True)
        _emit_bench_event(
            workspace=base,
            project_id=pid,
            level=int(project.get("level") or 0),
            name="project.started",
            summary=f"{pid} {project['title']} starting",
            meta={"session_id": bench_session_id, "title": project.get("title") or ""},
        )
        try:
            chain = run_chain(
                project,
                workspace,
                timeout_s=args.timeout,
                log_path=log_path,
                director_workflow_execution_mode=args.director_workflow_execution_mode,
                director_dispatch_driver=args.director_dispatch_driver,
            )
        except subprocess.TimeoutExpired:
            chain = {"exit_code": -1, "duration_s": float(args.timeout), "timeout": True}
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
        runtime_dir = resolve_runtime_dir_for_workspace(workspace)
        record = build_factory_audit_record(
            project=project,
            workspace=str(workspace),
            artifact_globs=discover_artifacts(workspace, runtime_dir),
        )
        record["runtime_dir"] = str(runtime_dir) if runtime_dir else None
        record["chain"] = chain
        record["chain_results"] = read_chain_results(runtime_dir)
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
        apply_factory_bench_gates(record, chain)
        records.append(record)
        status = "PASS" if record["all_checks_passed"] else "FAIL"
        print(
            f"[factory-bench] {pid} {status}: chain={record['chain_state']} files={record['code_file_count']} "
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
        # Push live counters so the front-end sees ``X/Y 通过`` update
        # immediately, not only at run.completed.
        if backend_url and bench_session_id:
            _push_bench_progress_to_backend(
                backend_url=backend_url,
                session_id=bench_session_id,
                completed=sum(1 for r in records if r.get("all_checks_passed")),
                failed=sum(1 for r in records if not r.get("all_checks_passed")),
                token=backend_token,
            )

        out_path = base / "factory_audits.json"
        out_path.write_text(
            json.dumps(
                {"aggregate": aggregate_factory_audits(records), "records": records},
                ensure_ascii=False,
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
        if not record["all_checks_passed"]:
            failed += 1
            if failed >= args.max_failed:
                print(f"[factory-bench] early stop: {failed} failures (audit before continuing)", flush=True)
                break

    agg = aggregate_factory_audits(records)
    run_success = failed < args.max_failed and agg["all_checks_passed"] == agg["total"]
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
        },
    )
    if backend_url and bench_session_id:
        _push_bench_complete_to_backend(
            backend_url=backend_url,
            session_id=bench_session_id,
            success=run_success,
            summary={
                "total": agg["total"],
                "passed": agg["all_checks_passed"],
                "failed": agg["total"] - agg["all_checks_passed"],
                "by_level": agg["by_level"],
            },
            token=backend_token,
        )
    print(
        f"\n[factory-bench] ===== {agg['all_checks_passed']}/{agg['total']} passed | by_level={agg['by_level']} =====",
        flush=True,
    )

    print(f"[factory-bench] audits -> {base / 'factory_audits.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
