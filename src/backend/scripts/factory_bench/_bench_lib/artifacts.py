"""Runtime discovery, chain-result reads, and chain-state grading.

Private helper module for run_factory_bench.
"""

from __future__ import annotations

# ruff: noqa: F821, E402
# mypy: ignore-errors


def _pull_namespace(module: object) -> None:
    """Copy non-dunder attributes into this module (private helpers + imports)."""
    g = globals()
    for key, value in vars(module).items():
        if key.startswith("__"):
            continue
        g[key] = value


from scripts.factory_bench._bench_lib import catalog as _catalog

_pull_namespace(_catalog)
del _catalog


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
    "events/chief_engineer.llm.events.jsonl",
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
        "source": LEGACY_BENCH_ARTIFACT_SOURCE,
        "authoritative": False,
        "degraded": True,
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


_NON_TERMINAL_CHAIN_ERRORS = {"start_failed", "workspace_switch_failed", "event_wait_timeout"}


def grade_chain_state(chain_results: dict[str, Any], exit_code: Any) -> str:
    """Project the display chain state from canonical execution only."""

    del exit_code
    if chain_results.get("source") != CANONICAL_BENCH_PROJECTION_SOURCE:
        return "fail"
    execution = chain_results.get("execution")
    execution_map = execution if isinstance(execution, Mapping) else {}
    return "clean" if bool(execution_map.get("ok")) else "fail"


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


def _non_terminal_chain_diagnostics(
    *,
    chain: dict[str, Any],
    backend_url: str,
    project_workspace: str,
    launcher_instance: dict[str, Any],
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "backend_url": backend_url,
        "workspace": project_workspace,
        "launcher_instance": dict(launcher_instance),
        "chain_non_terminal": True,
        "chain_non_terminal_target_files_truncated": True,
    }
    for key in ("event_wait_error", "last_observed_status", "cancel_response", "cancel_error"):
        value = chain.get(key)
        if value not in (None, "", {}):
            diagnostics[key] = value
    return diagnostics


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
